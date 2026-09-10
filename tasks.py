from celery import Celery
from rag_qe import QueryEngine

celery_app = Celery(
    "tasks",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0",
)


@celery_app.task(bind=True, name="tasks.run_pipeline_task")
def run_pipeline_task(
    self,
    collection_name: str,
    db_path: str = "./omnidoc_search.db",
    input_dir: str = "save_dir",
):
    query_engine = QueryEngine(db_path=db_path)
    ingested_nodes = query_engine.run_pipeline(
        collection_name=collection_name,
        db_path=db_path,
        input_dir=input_dir,
    )
    return {
        "task_id": self.request.id,
        "status": "completed",
        "collection_name": collection_name,
        "db_path": db_path,
        "input_dir": input_dir,
        "ingested_nodes": ingested_nodes,
    }
