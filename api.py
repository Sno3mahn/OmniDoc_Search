import asyncio
import hashlib
import hmac
import json
import os
from uuid import uuid4
from typing import Dict, Any, Optional

from celery.result import AsyncResult
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from run_workflow import ETLWorkflow
from agentic_etl import build_agents, StatusEmitterEvent
from llama_index.core.workflow import StopEvent
from rag_qe import QueryEngine, collection_name_for_url
from tasks import celery_app, run_pipeline_task
from import_stuff import is_safe_url
from rate_limit import enforce_rate_limit

load_dotenv()
API_KEY = os.getenv("API_KEY")

active_wfs: Dict[str, Any] = {}
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


async def _sync_query_engine_state(task_id: str, collection_name: str):
    result = AsyncResult(task_id, app=celery_app)
    while not result.ready():
        await asyncio.sleep(1)

    query_engine = QueryEngine(db_path=RAG_DB_PATH)
    if result.successful():
        query_engine.mark_pipeline_complete(collection_name, auto_initialize=True)

@app.get('/health_check')
async def health_check():
    return {"message": "OK"}


def _emit(job_data: Dict[str, Any], msg: Optional[Dict[str, Any]]):
    """Appends to the job's event log and fans the message out to every attached
    WebSocket. A single shared queue was wrong: two connections to the same job
    (a page refresh, a second tab, or React StrictMode's double-mount in dev)
    both consume from it, so a disconnected-but-still-blocked handler can swallow
    events the live one needed. Each connection now gets its own queue, and the
    log lets a late joiner replay everything it missed."""
    if msg is not None:
        job_data['events'].append(msg)
    for q in list(job_data['subscribers']):
        q.put_nowait(msg)


async def _drive_workflow(job_id: str, handler, playwright_browser, collection_name: str):
    """Runs independently of any WebSocket connection, so a client disconnecting
    mid-run can't cause the workflow to be abandoned or the Celery hand-off to be
    skipped. Owns the playwright browser's lifetime - it's only closed here, once
    the workflow has actually finished using it."""
    job_data = active_wfs[job_id]
    try:
        async for ev in handler.stream_events():
            if isinstance(ev, StatusEmitterEvent):
                _emit(job_data, {"type": "status", "data": ev.status})
            elif isinstance(ev, StopEvent):
                res = json.loads(ev.result)
                status = res.get("status", "")
                dir_name = res.get("dir_name") if "failed" not in status else None

                if dir_name:
                    pipeline_task = run_pipeline_task.delay(
                        collection_name=collection_name,
                        db_path=RAG_DB_PATH,
                        input_dir=dir_name,
                    )
                    asyncio.create_task(
                        _sync_query_engine_state(pipeline_task.id, collection_name)
                    )
                    _emit(
                        job_data,
                        {"type": "pipeline_started", "data": {"task_id": pipeline_task.id}},
                    )

                job_data['final'] = {"type": "done", "data": status}
                _emit(job_data, job_data['final'])
        await handler
    except Exception as exc:
        job_data['final'] = {"type": "error", "data": str(exc)}
        _emit(job_data, job_data['final'])
    finally:
        if playwright_browser is not None:
            await playwright_browser.close()
        job_data['finished'] = True
        _emit(job_data, None)  # sentinel: tells attached WSs to stop reading


@app.post('/etl_workflow/')
async def run_etl_workflow(request: Request, api_key: str = Depends(require_api_key)):
    await enforce_rate_limit(api_key, bucket="etl_workflow", max_requests=5, window_seconds=60)

    req = await request.json()
    homepage_url = req.get('homepage_url', '')
    if not homepage_url:
        return {"status": "failed", "message": "homepage_url not provided"}
    if not is_safe_url(homepage_url):
        return {"status": "failed", "message": "homepage_url is not a permitted public address"}

    job_id = str(uuid4())
    collection_name = collection_name_for_url(homepage_url)
    agents, playwright_browser = await build_agents(use_playwright=True)
    try:
        wf = ETLWorkflow(
            homepage_url=homepage_url,
            agents=agents,
            timeout=None,
        )
        handler = wf.run()
    except Exception as ex:
        if playwright_browser is not None:
            await playwright_browser.close()
        return {"status": "failed", "message": str(ex)}

    active_wfs[job_id] = {
        'handler': handler,
        'events': [],
        'subscribers': set(),
        'finished': False,
        'final': None,
        'owner': api_key,
    }
    asyncio.create_task(_drive_workflow(job_id, handler, playwright_browser, collection_name))
    return {
        "status": "success",
        "job_id": job_id,
        "collection_name": collection_name,
        "message": "triggered workflow",
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

    try:
        if len(collections) == 1:
            engine = query_engine.get_query_engine(collections[0])
        else:
            engine = query_engine.build_multi_query_engine(collections)
        response = await engine.aquery(question)
    except Exception as ex:
        return {"status": "failed", "message": str(ex)}

    return {
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
    job_data = active_wfs.get(job_id)
    # Same "job not found" message for a missing job and for someone else's
    # job - distinguishing the two would let a caller enumerate other users'
    # job_ids by testing which ones return a different error.
    if job_data is None or not hmac.compare_digest(job_data.get('owner', ''), api_key):
        await ws.send_json({"type": "error", "message": "job not found"})
        await ws.close()
        return

    # Subscribe before snapshotting the log. There is no await between these
    # two lines, so no event can slip into the gap - the snapshot holds
    # everything emitted so far, and the queue gets everything after it, with
    # no overlap and nothing dropped. A client reconnecting mid-run therefore
    # replays the whole run rather than joining blind.
    queue: asyncio.Queue = asyncio.Queue()
    job_data['subscribers'].add(queue)
    history = list(job_data['events'])
    already_finished = job_data['finished']

    try:
        for msg in history:
            await ws.send_json(msg)
        if not already_finished:
            while True:
                msg = await queue.get()
                if msg is None:
                    break
                await ws.send_json(msg)
    except WebSocketDisconnect:
        pass
    finally:
        job_data['subscribers'].discard(queue)
