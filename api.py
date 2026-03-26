import asyncio
import json
from uuid import uuid4
from typing import Dict, Any
from celery.result import AsyncResult
from fastapi import Request, FastAPI, WebSocket, WebSocketDisconnect
# import asyncio
# import socketio

from run_workflow import ETLWorkflow
from agentic_etl import build_agents, StatusEmitterEvent
from llama_index.core.workflow import StopEvent
from rag_qe import QueryEngine
from tasks import celery_app, run_pipeline_task

active_wfs: Dict[str, Any] = {}
dir_name: str = None
homepage_url: str = None
RAG_COLLECTION_NAME = "dt_doc_collection"
RAG_DB_PATH = "./omnidoc_search.db"
app = FastAPI()
# sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
# socket_app = socketio.ASGIApp(sio, other_asgi_app=app)


async def _sync_query_engine_state(task_id: str):
    result = AsyncResult(task_id, app=celery_app)
    while not result.ready():
        await asyncio.sleep(1)

    query_engine = QueryEngine(
        collection_name=RAG_COLLECTION_NAME,
        db_path=RAG_DB_PATH,
    )
    if result.successful():
        query_engine.mark_pipeline_complete(auto_initialize=True)
    else:
        query_engine.pipeline_run = False

@app.get('/health_check')
async def health_check():
    return {"message": "OK"}


@app.post('/etl_workflow/')
async def run_etl_workflow(request: Request):
    req = await request.json()
    homepage_url = req.get('homepage_url', '')
    if homepage_url:
        # asyncio.sleep(10)
        job_id = str(uuid4())
        agents, playwright_browser = await build_agents(use_playwright=True)
        try:
            wf = ETLWorkflow(
                homepage_url=homepage_url,
                agents=agents,
                timeout=None,
            )
            handler = wf.run()
            active_wfs[job_id] = {'handler': handler}
            return {"status": "success", "job_id": job_id, "message": "triggered workflow"}
        except Exception as ex:
            return {"status": "failed", "message": str(ex)}
        finally:
            if playwright_browser is not None:
                await playwright_browser.close()
        # background_tasks.add_task(heavy_processing_task, home_page_url, True, sid, sio)
        # await run_workflow_main(homepage_url=homepage_url, use_playwright_tools=True)
    else:
        return {"status": "failed", "message": "homepage_url not provided"}
    
@app.websocket("/ws/stream/{job_id}")
async def stream_events(ws: WebSocket, job_id: str):
    await ws.accept()
    job_data = active_wfs.get(job_id)
    if job_data is None:
        await ws.send_json({"type": "error", "message": "job not found"})
        await ws.close()
        return
    handler = job_data.get('handler')
    try:
        async for ev in handler.stream_events():
            if isinstance(ev, StatusEmitterEvent):
                await ws.send_json({"type": "status", "data": ev.status})
            elif isinstance(ev, StopEvent):
                res = json.loads(ev.result)
                status = res.get("status", "")
                dir_name = res.get("dir_name") if "failed" not in status else None

                if dir_name:
                    pipeline_task = run_pipeline_task.delay(
                        collection_name=RAG_COLLECTION_NAME,
                        db_path=RAG_DB_PATH,
                        input_dir=dir_name,
                    )
                    asyncio.create_task(_sync_query_engine_state(pipeline_task.id))
                    await ws.send_json(
                        {
                            "type": "pipeline_started",
                            "data": {"task_id": pipeline_task.id},
                        }
                    )

                await ws.send_json({"type": "done", "data": status})

        await handler
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await ws.send_json({"type": "error", "data": str(exc)})
        finally:
            handler.cancel()
    finally:
        del active_wfs[job_id]
