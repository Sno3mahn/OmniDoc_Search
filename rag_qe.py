import os
import sys
from threading import Lock
from typing import Optional

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

    def __init__(
        self,
        collection_name: str = "dt_doc_collection",
        db_path: str = "./omnidoc_search.db",
    ):
        if self._bootstrapped:
            self.configure(collection_name=collection_name, db_path=db_path)
            return

        self.collection_name = collection_name
        self.db_path = db_path
        self.pipeline_run = False
        self.query_engine = None
        self._bootstrapped = True

    def configure(
        self,
        collection_name: Optional[str] = None,
        db_path: Optional[str] = None,
    ):
        if collection_name:
            self.collection_name = collection_name
        if db_path:
            self.db_path = db_path

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
        collection_name: Optional[str] = None,
        db_path: Optional[str] = None,
        input_dir: str = "save_dir",
    ) -> int:
        self.configure(collection_name=collection_name, db_path=db_path)
        docs = self._load_docs(input_dir=input_dir)
        _, emb_model = self._define_llms()
        vector_store = self._define_db(
            collection_name=self.collection_name,
            db_path=self.db_path,
        )

        pipeline = IngestionPipeline(
            transformations=[MarkdownNodeParser(), emb_model],
            vector_store=vector_store,
        )

        nodes = pipeline.run(documents=docs)
        self.pipeline_run = True
        return len(nodes)

    def initialize_query_engine(
        self,
        collection_name: Optional[str] = None,
        db_path: Optional[str] = None,
    ):
        self.configure(collection_name=collection_name, db_path=db_path)
        if not self.pipeline_run:
            raise RuntimeError(
                "run_pipeline must finish before initialize_query_engine is called."
            )
        llm, emb_model = self._define_llms()
        vector_store = self._define_db(
            collection_name=self.collection_name,
            db_path=self.db_path,
        )
        db_index = VectorStoreIndex.from_vector_store(vector_store, embed_model=emb_model)
        self.query_engine = db_index.as_query_engine(
            llm=llm,
            response_mode="tree_summarize",
        )
        return self.query_engine

    def mark_pipeline_complete(self, auto_initialize: bool = True):
        self.pipeline_run = True
        if auto_initialize:
            return self.initialize_query_engine()
        return None
