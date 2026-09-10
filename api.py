import asyncio
import json
from uuid import uuid4
from typing import Dict, Any
from celery.result import AsyncResult
from fastapi import Request, FastAPI, WebSocket, WebSocketDisconnect

from run_workflow import ETLWorkflow
from agentic_etl import build_agents, StatusEmitterEvent
from llama_index.core.workflow import StopEvent
from rag_qe import QueryEngine, collection_name_for_url
from tasks import celery_app, run_pipeline_task
from import_stuff import is_safe_url

active_wfs: Dict[str, Any] = {}
RAG_DB_PATH = "./omnidoc_search.db"
app = FastAPI()


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


async def _drive_workflow(job_id: str, handler, playwright_browser, collection_name: str):
    """Runs independently of any WebSocket connection, so a client disconnecting
    mid-run can't cause the workflow to be abandoned or the Celery hand-off to be
    skipped. Owns the playwright browser's lifetime - it's only closed here, once
    the workflow has actually finished using it."""
    job_data = active_wfs[job_id]
    queue: asyncio.Queue = job_data['queue']
    try:
        async for ev in handler.stream_events():
            if isinstance(ev, StatusEmitterEvent):
                await queue.put({"type": "status", "data": ev.status})
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
                    await queue.put(
                        {"type": "pipeline_started", "data": {"task_id": pipeline_task.id}}
                    )

                job_data['final'] = {"type": "done", "data": status}
                await queue.put(job_data['final'])
        await handler
    except Exception as exc:
        job_data['final'] = {"type": "error", "data": str(exc)}
        await queue.put(job_data['final'])
    finally:
        if playwright_browser is not None:
            await playwright_browser.close()
        job_data['finished'] = True
        await queue.put(None)  # sentinel: tells any connected WS to stop reading


@app.post('/etl_workflow/')
async def run_etl_workflow(request: Request):
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
        'queue': asyncio.Queue(),
        'finished': False,
        'final': None,
    }
    asyncio.create_task(_drive_workflow(job_id, handler, playwright_browser, collection_name))
    return {
        "status": "success",
        "job_id": job_id,
        "collection_name": collection_name,
        "message": "triggered workflow",
    }
    
@app.get('/query/status')
async def query_status(homepage_url: str = ''):
    if not homepage_url:
        return {"status": "failed", "message": "homepage_url query param not provided"}

    collection_name = collection_name_for_url(homepage_url)
    query_engine = QueryEngine(db_path=RAG_DB_PATH)
    return {
        "ready": query_engine.is_ready(collection_name),
        "collection_name": collection_name,
    }


@app.post('/query')
async def query_docs(request: Request):
    req = await request.json()
    question = req.get('query', '')
    homepage_url = req.get('homepage_url', '')
    if not question:
        return {"status": "failed", "message": "query not provided"}
    if not homepage_url:
        return {"status": "failed", "message": "homepage_url not provided"}

    collection_name = collection_name_for_url(homepage_url)
    query_engine = QueryEngine(db_path=RAG_DB_PATH)
    if not query_engine.is_ready(collection_name):
        return {
            "status": "failed",
            "message": "no ingested documents yet for this site - run /etl_workflow/ and wait for the pipeline to finish before querying",
        }

    try:
        response = await query_engine.get_query_engine(collection_name).aquery(question)
    except Exception as ex:
        return {"status": "failed", "message": str(ex)}

    return {
        "status": "success",
        "answer": str(response),
        "sources": [
            {"text": node.node.get_content()[:500], "score": node.score}
            for node in getattr(response, "source_nodes", [])
        ],
    }


@app.websocket("/ws/stream/{job_id}")
async def stream_events(ws: WebSocket, job_id: str):
    await ws.accept()
    job_data = active_wfs.get(job_id)
    if job_data is None:
        await ws.send_json({"type": "error", "message": "job not found"})
        await ws.close()
        return

    # Job already finished before this client connected (e.g. reconnect after
    # a drop) - replay the final message instead of blocking on an empty queue.
    if job_data['finished']:
        if job_data['final'] is not None:
            await ws.send_json(job_data['final'])
        await ws.close()
        return

    queue: asyncio.Queue = job_data['queue']
    try:
        while True:
            msg = await queue.get()
            if msg is None:
                break
            await ws.send_json(msg)
    except WebSocketDisconnect:
        pass
