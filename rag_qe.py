import os
import re
import sys
from threading import Lock
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import chromadb
from dotenv import load_dotenv

from llama_index.core import SimpleDirectoryReader, VectorStoreIndex
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.deepseek import DeepSeek
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.vector_stores.chroma import ChromaVectorStore

__import__('pysqlite3')
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')

load_dotenv()
DEEPSEEK_KEY = os.getenv("API_DEEPSEEK")


def collection_name_for_url(homepage_url: str) -> str:
    """Derives a stable, Chroma-legal collection name from a docs homepage URL,
    so each site gets its own namespace instead of every ingestion sharing one
    fixed collection. Chroma collection names must be 3-63 chars, start/end
    alphanumeric, and contain only [a-zA-Z0-9._-].
    """
    parsed = urlparse(homepage_url if "://" in homepage_url else f"https://{homepage_url}")
    host = (parsed.netloc or parsed.path).lower()
    host = re.sub(r"^www\.", "", host)
    slug = re.sub(r"[^a-z0-9]+", "-", host).strip("-")
    if not slug:
        slug = "site"
    name = f"doc-{slug}"[:63].strip("-")
    if len(name) < 3:
        name = (name + "-col")[:63]
    return name


class QueryEngine:
    _instance = None
    _instance_lock = Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super(QueryEngine, cls).__new__(cls)
                    cls._instance._bootstrapped = False
        return cls._instance

    def __init__(self, db_path: str = "./omnidoc_search.db"):
        if self._bootstrapped:
            self.configure(db_path=db_path)
            return

        self.db_path = db_path
        # Keyed by collection_name - one Chroma db can (and now does) hold a
        # separate collection per ingested site, so readiness/engine state
        # can't be a single flat attribute anymore.
        self._ready: Dict[str, bool] = {}
        self._query_engines: Dict[str, Any] = {}
        self._bootstrapped = True

    def configure(self, db_path: Optional[str] = None):
        if db_path:
            self.db_path = db_path

    def _collection_has_rows(self, collection_name: str) -> bool:
        """Read-only existence check. Deliberately not _define_db, which creates
        the collection as a side effect."""
        try:
            db = chromadb.PersistentClient(path=self.db_path)
            if collection_name not in [c.name for c in db.list_collections()]:
                return False
            return db.get_collection(collection_name).count() > 0
        except Exception:
            return False

    def is_ready(self, collection_name: str) -> bool:
        if self._ready.get(collection_name) and collection_name in self._query_engines:
            return True
        # The Chroma collection outlives this process, so readiness shouldn't
        # depend solely on in-memory state: an API restart (or an ingestion
        # that ran in a different process) would otherwise make an index that
        # is sitting on disk look permanently unqueryable.
        return self._collection_has_rows(collection_name)

    def get_query_engine(self, collection_name: str):
        engine = self._query_engines.get(collection_name)
        if engine is None and self._collection_has_rows(collection_name):
            self._ready[collection_name] = True
            engine = self.initialize_query_engine(collection_name)
        return engine

    def _load_docs(self, input_dir: str = "save_dir"):
        reader = SimpleDirectoryReader(input_dir=input_dir)
        docs = reader.load_data()
        docs = [doc for doc in docs if doc.metadata.get("file_size")]
        return docs

    def _define_llms(
        self,
        llm_model: str = "deepseek-v4-flash",
        embedding_model: str = "BAAI/bge-small-en-v1.5",
        batch_size: int = 8,
    ):
        llm = DeepSeek(model=llm_model, api_key=DEEPSEEK_KEY)
        emb_model = HuggingFaceEmbedding(embedding_model, embed_batch_size=batch_size)
        return llm, emb_model

    def _define_db(self, collection_name: str, db_path: str):
        db = chromadb.PersistentClient(path=db_path)
        collections = [col.name for col in db.list_collections()]
        if collection_name in collections:
            collection = db.get_collection(collection_name)
            if not collection.count():
                db.delete_collection(collection_name)
                collection = db.create_collection(collection_name)
        else:
            collection = db.create_collection(collection_name)
        return ChromaVectorStore(chroma_collection=collection)

    def run_pipeline(
        self,
        collection_name: str,
        db_path: Optional[str] = None,
        input_dir: str = "save_dir",
    ) -> int:
        self.configure(db_path=db_path)
        docs = self._load_docs(input_dir=input_dir)
        _, emb_model = self._define_llms()
        vector_store = self._define_db(collection_name=collection_name, db_path=self.db_path)

        pipeline = IngestionPipeline(
            transformations=[MarkdownNodeParser(), emb_model],
            vector_store=vector_store,
        )

        nodes = pipeline.run(documents=docs)
        self._ready[collection_name] = True
        return len(nodes)

    def initialize_query_engine(
        self,
        collection_name: str,
        db_path: Optional[str] = None,
    ):
        self.configure(db_path=db_path)
        if not self._ready.get(collection_name):
            raise RuntimeError(
                f"run_pipeline must finish for collection {collection_name!r} "
                "before initialize_query_engine is called."
            )
        llm, emb_model = self._define_llms()
        vector_store = self._define_db(collection_name=collection_name, db_path=self.db_path)
        db_index = VectorStoreIndex.from_vector_store(vector_store, embed_model=emb_model)
        query_engine = db_index.as_query_engine(
            llm=llm,
            response_mode="tree_summarize",
        )
        self._query_engines[collection_name] = query_engine
        return query_engine

    def mark_pipeline_complete(self, collection_name: str, auto_initialize: bool = True):
        self._ready[collection_name] = True
        if auto_initialize:
            return self.initialize_query_engine(collection_name)
        return None
