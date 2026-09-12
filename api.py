import asyncio
import hashlib
import hmac
import os
from uuid import uuid4
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

import corpus
import job_bus
import query_cache
from rag_qe import CHUNK_SIZE, EMBED_MODEL, SIMILARITY_TOP_K, QueryEngine, collection_name_for_url
from tasks import run_etl_task
from import_stuff import is_safe_url, sitemap_lastmod
from rate_limit import enforce_rate_limit

load_dotenv()
API_KEY = os.getenv("API_KEY")

# No module-level job registry any more. Job state lives in Redis (job_bus), so
# this process holds nothing a second replica wouldn't also see, and the ETL
# itself runs in a Celery worker rather than in this event loop.
RAG_DB_PATH = "./omnidoc_search.db"
app = FastAPI()

# The frontend dev server is a different origin (:5173 vs :8000), so the
# browser needs these headers to allow the fetch calls. Explicit origins
# rather than "*" - the API takes a credential in the X-API-Key header.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["x-api-key", "content-type"],
)


def _key_fingerprint(key: str) -> str:
    """Non-reversible identity derived from a validated API key. Everything
    downstream of the auth boundary (rate-limit bucket keys, job ownership
    tags) uses this instead of the raw key, so the actual secret never lands
    in a dict/log/response that might outlive the single comparison that
    needs it."""
    return hashlib.sha256(key.encode()).hexdigest()


async def require_api_key(request: Request) -> str:
    if not API_KEY:
        raise HTTPException(status_code=500, detail="Server misconfigured: API_KEY not set")
    provided = request.headers.get("x-api-key")
    # compare_digest, not `!=` - a plain string comparison short-circuits on
    # the first differing byte, which is a timing side-channel on a secret.
    if not provided or not hmac.compare_digest(provided, API_KEY):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    return _key_fingerprint(provided)


WS_SUBPROTOCOL = "omnidoc.v1"


def _check_ws_api_key(ws: WebSocket) -> Optional[str]:
    """Browsers can't set headers on a WebSocket, so a browser client passes the
    key as the second Sec-WebSocket-Protocol entry ("omnidoc.v1, <key>") rather
    than in a query string, which would end up in access logs. Non-browser
    clients can still use a plain x-api-key header."""
    if not API_KEY:
        return None

    provided = ws.headers.get("x-api-key")
    if not provided:
        offered = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",")]
        if len(offered) >= 2 and offered[0] == WS_SUBPROTOCOL:
            provided = offered[1]

    if not provided or not hmac.compare_digest(provided, API_KEY):
        return None
    return _key_fingerprint(provided)


@app.get('/health_check')
async def health_check():
    return {"message": "OK"}


@app.post('/etl_workflow/')
async def run_etl_workflow(request: Request, api_key: str = Depends(require_api_key)):
    await enforce_rate_limit(api_key, bucket="etl_workflow", max_requests=5, window_seconds=60)

    req = await request.json()
    homepage_url = req.get('homepage_url', '')
    force = bool(req.get('force'))
    if not homepage_url:
        return {"status": "failed", "message": "homepage_url not provided"}
    if not is_safe_url(homepage_url):
        return {"status": "failed", "message": "homepage_url is not a permitted public address"}

    collection_name = collection_name_for_url(homepage_url)

    # Re-indexing a site someone already indexed is the single most expensive
    # thing this system can do for no benefit. staleness() returns the reason
    # an index can't be reused, or None - so the cache hit is the default and
    # every re-run has to justify itself.
    existing = corpus.get(homepage_url)
    # One HTTP request to ask the site when it last changed, instead of
    # re-extracting every page to find out. Only worth paying when there's a
    # record that could be reused, and only when it carries a baseline to
    # compare against. Advisory: a newer lastmod triggers a re-index, a missing
    # or older one leaves the TTL as the fallback.
    observed = None
    if (existing is not None and existing.source_lastmod and not force
            and existing.age_seconds > corpus.RECHECK_AFTER_SECONDS):
        try:
            observed = await asyncio.to_thread(sitemap_lastmod, homepage_url)
        except Exception:
            observed = None
    reason = corpus.staleness(
        existing, embed_model=EMBED_MODEL, chunk_size=CHUNK_SIZE,
        observed_lastmod=observed,
    )
    if existing is not None and reason is None and not force:
        return {
            "status": "cached",
            "collection_name": collection_name,
            "indexed_at": existing.indexed_at,
            "page_count": existing.page_count,
            "node_count": existing.node_count,
            "message": "already indexed and current - pass force:true to re-index",
        }

    job_id = str(uuid4())
    job_bus.create_job(
        job_id, owner=api_key, homepage_url=homepage_url, collection=collection_name
    )
    run_etl_task.apply_async(
        kwargs={
            "job_id": job_id,
            "homepage_url": homepage_url,
            "collection_name": collection_name,
            "db_path": RAG_DB_PATH,
        },
        queue="etl",
    )
    return {
        "status": "success",
        "job_id": job_id,
        "collection_name": collection_name,
        "reindex_reason": reason,
        "message": "queued workflow",
    }


@app.get('/corpora')
async def list_corpora(api_key: str = Depends(require_api_key)):
    """What has been indexed, and whether each index is still good. The UI needs
    this to offer "open an existing index" without guessing at collection names."""
    records = corpus.list_all()
    return {
        "status": "success",
        "corpora": [
            {
                "url": rec.url,
                "collection": rec.collection,
                "page_count": rec.page_count,
                "node_count": rec.node_count,
                "indexed_at": rec.indexed_at,
                "discovery": rec.discovery,
                "stale_reason": corpus.staleness(
                    rec, embed_model=EMBED_MODEL, chunk_size=CHUNK_SIZE
                ),
            }
            for rec in records
        ],
    }


@app.get('/query/status')
async def query_status(homepage_url: str = '', api_key: str = Depends(require_api_key)):
    if not homepage_url:
        return {"status": "failed", "message": "homepage_url query param not provided"}

    collection_name = collection_name_for_url(homepage_url)
    query_engine = QueryEngine(db_path=RAG_DB_PATH)
    return {
        "ready": query_engine.is_ready(collection_name),
        "collection_name": collection_name,
    }


@app.post('/query')
async def query_docs(request: Request, api_key: str = Depends(require_api_key)):
    await enforce_rate_limit(api_key, bucket="query", max_requests=30, window_seconds=60)

    req = await request.json()
    question = req.get('query', '')
    # Accepts one URL or many. Many is the interesting case: asking a question
    # that spans two projects' docs is the thing no single vendor's built-in
    # docs search can answer, because each only owns its own corpus.
    urls = req.get('homepage_urls') or ([req['homepage_url']] if req.get('homepage_url') else [])
    if not question:
        return {"status": "failed", "message": "query not provided"}
    if not urls:
        return {"status": "failed", "message": "homepage_url(s) not provided"}

    query_engine = QueryEngine(db_path=RAG_DB_PATH)
    collections = [collection_name_for_url(u) for u in urls]
    missing = [u for u, c in zip(urls, collections) if not query_engine.is_ready(c)]
    if missing:
        return {
            "status": "failed",
            "message": f"no ingested documents yet for: {', '.join(missing)} - run /etl_workflow/ for each first",
        }

    # Retrieval is ~9ms; the ~3.7s is LLM synthesis, so the answer is the only
    # thing worth caching. Exact normalized question only - see query_cache's
    # module docstring for the measurement that ruled out semantic matching.
    key = query_cache.cache_key(question, collections, urls, SIMILARITY_TOP_K)
    cached = await query_cache.get(key)
    if cached is not None:
        return {**cached, "cached": True}

    try:
        if len(collections) == 1:
            # The fingerprint lets the engine cache notice a re-index that
            # happened in the ingest worker process, which this one never sees.
            rec = corpus.get(urls[0])
            engine = query_engine.get_query_engine(
                collections[0], fingerprint=rec.fingerprint if rec else None
            )
        else:
            engine = query_engine.build_multi_query_engine(collections)
        if engine is None:
            return {"status": "failed", "message": "index not available for this source"}
        response = await engine.aquery(question)
    except Exception as ex:
        return {"status": "failed", "message": str(ex)}

    payload = {
        "status": "success",
        "answer": str(response),
        "collections": collections,
        "sources": [
            {
                "text": node.node.get_content()[:500],
                "score": node.score,
                "source": node.node.metadata.get("source", ""),
                "collection": node.node.metadata.get("collection", collections[0]),
            }
            for node in getattr(response, "source_nodes", [])
        ],
    }
    await query_cache.put(key, payload)
    return {**payload, "cached": False}


@app.websocket("/ws/stream/{job_id}")
async def stream_events(ws: WebSocket, job_id: str):
    api_key = _check_ws_api_key(ws)
    if api_key is None:
        # Reject before accept() so this never looks like a successful
        # handshake to an unauthenticated client.
        await ws.close(code=4401, reason="Missing or invalid API key")
        return

    # A client that offered subprotocols needs one echoed back or the browser
    # rejects the handshake.
    offered_subprotocol = ws.headers.get("sec-websocket-protocol")
    await ws.accept(subprotocol=WS_SUBPROTOCOL if offered_subprotocol else None)

    meta = await job_bus.get_meta(job_id)
    # Same "job not found" message for a missing job and for someone else's
    # job - distinguishing the two would let a caller enumerate other users'
    # job_ids by testing which ones return a different error.
    if meta is None or not hmac.compare_digest(meta.get('owner', ''), api_key):
        await ws.send_json({"type": "error", "message": "job not found"})
        await ws.close()
        return

    # job_bus.stream handles replay-then-live with no gap and no duplicate, so
    # this handler is now just a pipe. It holds no job state of its own, which
    # is what lets a client reconnect to a different API replica mid-run.
    try:
        async for msg in job_bus.stream(job_id):
            await ws.send_json(msg)
    except WebSocketDisconnect:
        pass
