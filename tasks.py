"""Celery tasks - the ETL and the ingestion both live here now.

The ETL used to run as an asyncio task inside the FastAPI process. Moving it
here is what makes the API stateless: a request enqueues a job and returns, and
progress reaches the browser over Redis rather than over a handle held in the
web server's memory.

Two queues, not one. ETL is network-bound and slow (minutes, holds a browser);
ingestion is CPU/GPU-bound (embeddings). Sharing a pool means a burst of
extractions starves embedding and vice versa, and they want very different
concurrency. The routing below is what lets them be scaled independently.
"""

import asyncio
import json
import os

from celery import Celery

import corpus
from job_bus import finish_job, publish, set_status
from rag_qe import CHUNK_SIZE, EMBED_MODEL, QueryEngine

celery_app = Celery(
    "tasks",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0",
)

celery_app.conf.task_routes = {
    "tasks.run_etl_task": {"queue": "etl"},
    "tasks.run_pipeline_task": {"queue": "ingest"},
}
# An extraction that wedges on a slow docs site must not hold a worker slot
# forever - the old workflow ran with timeout=None.
celery_app.conf.task_soft_time_limit = int(os.getenv("ETL_SOFT_TIME_LIMIT", "900"))
celery_app.conf.task_time_limit = int(os.getenv("ETL_TIME_LIMIT", "1020"))


@celery_app.task(bind=True, name="tasks.run_etl_task")
def run_etl_task(self, job_id: str, homepage_url: str, collection_name: str,
                 db_path: str = "./omnidoc_search.db"):
    """Runs ETLWorkflow to completion, streaming its status events to Redis.

    Imports are deferred into the task body: agentic_etl pulls in Playwright and
    the LLM client, and a worker that only serves the ingest queue should not pay
    that import cost (or need those packages) at module load.
    """
    from agentic_etl import StatusEmitterEvent, build_agents
    from llama_index.core.workflow import StopEvent
    from run_workflow import ETLWorkflow

    async def drive():
        agents, browser = await build_agents(use_playwright=True)
        result_payload = {"status": "failed", "dir_name": None}
        try:
            wf = ETLWorkflow(homepage_url=homepage_url, agents=agents, timeout=None)
            handler = wf.run()
            async for ev in handler.stream_events():
                if isinstance(ev, StatusEmitterEvent):
                    publish(job_id, {"type": "status", "data": ev.status})
                elif isinstance(ev, StopEvent):
                    result_payload = json.loads(ev.result)
            await handler
        finally:
            # Owned here so it's closed once the workflow is actually finished
            # with it, not when the request that started the run returns.
            if browser is not None:
                await browser.close()
        return result_payload

    set_status(job_id, "running")
    try:
        result = asyncio.run(drive())
    except Exception as exc:
        publish(job_id, {"type": "error", "data": str(exc)})
        corpus.record(
            url=homepage_url, collection=collection_name, fingerprint="",
            page_count=0, status="failed", embed_model=EMBED_MODEL,
            chunk_size=CHUNK_SIZE, job_id=job_id, detail={"error": str(exc)},
        )
        finish_job(job_id, "failed")
        raise

    status = result.get("status", "")
    dir_name = result.get("dir_name") if "failed" not in status else None

    if dir_name:
        ingest = run_pipeline_task.apply_async(
            kwargs={
                "collection_name": collection_name,
                "db_path": db_path,
                "input_dir": dir_name,
                "job_id": job_id,
                "homepage_url": homepage_url,
                "discovery": result.get("discovery"),
            },
            queue="ingest",
        )
        publish(job_id, {"type": "pipeline_started", "data": {"task_id": ingest.id}})

    # 'done' is emitted once extraction finishes and ingestion is queued - the
    # same point the old API-resident driver emitted it, so the frontend's
    # stage machine is unchanged. Ingestion reports separately via the corpus
    # record and /query/status.
    publish(job_id, {"type": "done", "data": status})
    finish_job(job_id, "done")
    return {"job_id": job_id, "status": status, "dir_name": dir_name}


@celery_app.task(bind=True, name="tasks.run_pipeline_task")
def run_pipeline_task(
    self,
    collection_name: str,
    db_path: str = "./omnidoc_search.db",
    input_dir: str = "save_dir",
    job_id: str = None,
    homepage_url: str = None,
    discovery: str = None,
):
    query_engine = QueryEngine(db_path=db_path)
    fingerprint = corpus.fingerprint_dir(input_dir)
    page_count = len([
        f for f in os.listdir(input_dir)
        if os.path.isfile(os.path.join(input_dir, f))
    ]) if os.path.isdir(input_dir) else 0

    ingested_nodes = query_engine.run_pipeline(
        collection_name=collection_name,
        db_path=db_path,
        input_dir=input_dir,
    )
    query_engine.mark_pipeline_complete(collection_name, auto_initialize=False)

    # Written only after the vectors are actually in Chroma - a record that
    # exists before the index does would make a half-built collection look
    # reusable to the next request.
    if homepage_url:
        corpus.record(
            url=homepage_url,
            collection=collection_name,
            fingerprint=fingerprint,
            page_count=page_count,
            node_count=ingested_nodes,
            status="indexed",
            embed_model=EMBED_MODEL,
            chunk_size=CHUNK_SIZE,
            job_id=job_id,
            discovery=discovery,
        )

    if job_id:
        publish(job_id, {
            "type": "status",
            "data": f"Indexed {ingested_nodes} chunks from {page_count} pages into {collection_name}",
        })

    return {
        "task_id": self.request.id,
        "status": "completed",
        "collection_name": collection_name,
        "db_path": db_path,
        "input_dir": input_dir,
        "ingested_nodes": ingested_nodes,
        "fingerprint": fingerprint,
    }
