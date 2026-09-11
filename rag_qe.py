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
from llama_index.core.prompts import PromptTemplate
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.schema import TextNode
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.vector_stores.chroma import ChromaVectorStore

# LlamaIndex defaults similarity_top_k to 2, and as_query_engine() never
# overrode it - so every answer this system has ever produced was synthesised
# from exactly 2 chunks. For docs QA 5-10 is the normal range.
SIMILARITY_TOP_K = int(os.getenv("RAG_TOP_K", "8"))
HYBRID_RETRIEVAL = os.getenv("RAG_HYBRID", "1") != "0"

__import__('pysqlite3')
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')

load_dotenv()
DEEPSEEK_KEY = os.getenv("API_DEEPSEEK")


class _QuotaRetriever(BaseRetriever):
    """Fans a query out to one retriever per doc source and tags each hit with
    the source it came from, so both the synthesiser and the UI can attribute."""

    def __init__(self, retrievers: Dict[str, Any]):
        self._retrievers = retrievers
        super().__init__()

    def _retrieve(self, query_bundle):
        results = []
        for name, retriever in self._retrievers.items():
            for node in retriever.retrieve(query_bundle):
                node.node.metadata["collection"] = name
                results.append(node)
        return results


# RISK MITIGATION (hallucinated integrations): when neither doc set actually
# documents how two projects fit together, an unconstrained model will invent a
# plausible-looking bridge - worse than admitting it isn't documented, because
# it reads authoritative and cites real-looking chunks.
MULTI_SOURCE_QA = PromptTemplate(
    "You are answering from the documentation of MULTIPLE separate projects.\n"
    "Each context chunk is labelled with the project it came from.\n"
    "---------------------\n"
    "{context_str}\n"
    "---------------------\n"
    "Rules:\n"
    "- Use only the context. Attribute each concrete claim to the project it\n"
    "  came from, by name.\n"
    "- If the question asks how these projects work TOGETHER and the context\n"
    "  does not actually document that integration, say so plainly, then state\n"
    "  what each project's docs do cover that is relevant. Never invent an API,\n"
    "  import path, config key, or integration that is not in the context.\n"
    "- Quote identifiers exactly rather than paraphrasing them.\n\n"
    "Question: {query_str}\n"
    "Answer: "
)


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

    # SimpleDirectoryReader embeds file_path by default and excludes file_name,
    # which is backwards here: file_path put the absolute local path into every
    # single chunk's embedding (an identical ~60-char prefix across the whole
    # corpus - it blunts discrimination and leaks the local directory layout
    # into the vector store), while the page identity that actually helps
    # ranking was left out.
    _NOISE_METADATA = [
        "file_path",
        "file_name",
        "file_type",
        "file_size",
        "creation_date",
        "last_modified_date",
        "last_accessed_date",
    ]

    def _load_docs(self, input_dir: str = "save_dir"):
        reader = SimpleDirectoryReader(input_dir=input_dir)
        docs = reader.load_data()
        docs = [doc for doc in docs if doc.metadata.get("file_size")]

        for doc in docs:
            name = doc.metadata.get("file_name", "")
            if name.endswith(".md"):
                name = name[: -len(".md")]
            # Anchors every chunk to its page, so a chunk from the middle of a
            # page competes on equal footing with one that happens to contain
            # the title. header_path (already embedded) gives the section.
            doc.metadata["source"] = name.replace("-", " / ")
            doc.excluded_embed_metadata_keys = list(self._NOISE_METADATA)
            doc.excluded_llm_metadata_keys = list(self._NOISE_METADATA)

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
        llm, _ = self._define_llms()
        retriever = self.build_retriever(
            collection_name, similarity_top_k=SIMILARITY_TOP_K, hybrid=HYBRID_RETRIEVAL
        )
        query_engine = RetrieverQueryEngine.from_args(
            retriever=retriever,
            llm=llm,
            response_mode="tree_summarize",
        )
        self._query_engines[collection_name] = query_engine
        return query_engine

    def build_index(self, collection_name: str):
        _, emb_model = self._define_llms()
        vector_store = self._define_db(collection_name=collection_name, db_path=self.db_path)
        return VectorStoreIndex.from_vector_store(vector_store, embed_model=emb_model)

    def build_retriever(
        self,
        collection_name: str,
        similarity_top_k: int = SIMILARITY_TOP_K,
        hybrid: bool = HYBRID_RETRIEVAL,
    ):
        """Dense retrieval, optionally fused with BM25.

        Dense embeddings are weak on rare exact tokens, which docs are full of
        (`typer.Argument`, `--install-completion`). BM25 matches those exactly,
        so fusing the two covers both the paraphrase and the literal-identifier
        case. Fusion is reciprocal-rank based and needs no LLM.
        """
        index = self.build_index(collection_name)
        dense = index.as_retriever(similarity_top_k=similarity_top_k)
        if not hybrid:
            return dense

        nodes = self._all_nodes(collection_name)
        if not nodes:
            return dense

        bm25 = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=similarity_top_k)
        llm, _ = self._define_llms()
        return QueryFusionRetriever(
            [dense, bm25],
            similarity_top_k=similarity_top_k,
            num_queries=1,          # no LLM query expansion - keeps this free
            mode="reciprocal_rerank",
            use_async=False,
            # Never actually called at num_queries=1, but the constructor
            # resolves Settings.llm eagerly and that defaults to OpenAI.
            llm=llm,
        )

    def _all_nodes(self, collection_name: str):
        """BM25 is lexical and needs the corpus in memory; Chroma stores the
        text, so pull it back rather than re-reading from disk."""
        try:
            db = chromadb.PersistentClient(path=self.db_path)
            collection = db.get_collection(collection_name)
            payload = collection.get(include=["documents", "metadatas"])
        except Exception:
            return []

        nodes = []
        for text, meta in zip(payload.get("documents") or [], payload.get("metadatas") or []):
            if text:
                nodes.append(TextNode(text=text, metadata=meta or {}))
        return nodes

    def build_multi_retriever(
        self,
        collection_names: list,
        per_source_k: int = 5,
        hybrid: bool = HYBRID_RETRIEVAL,
    ):
        """Retrieval across several doc sets, with a per-source quota.

        RISK MITIGATION (retrieval dilution): a single global top_k across two
        collections routinely returns all k chunks from the more verbose corpus,
        which silently turns a cross-source question back into a single-source
        one. Giving each source its own retriever with its own k makes the quota
        structural - every source is guaranteed representation.
        """
        retrievers = {
            name: self.build_retriever(name, similarity_top_k=per_source_k, hybrid=hybrid)
            for name in collection_names
        }
        return _QuotaRetriever(retrievers)

    def build_multi_query_engine(self, collection_names: list, per_source_k: int = 5):
        llm, _ = self._define_llms()
        retriever = self.build_multi_retriever(collection_names, per_source_k=per_source_k)
        return RetrieverQueryEngine.from_args(
            retriever=retriever,
            llm=llm,
            response_mode="tree_summarize",
            # tree_summarize synthesises with summary_template, NOT
            # text_qa_template - passing only the latter silently left the
            # grounding rules unapplied and the model happily invented an
            # integration. Both are set so the constraint survives a change of
            # response_mode.
            text_qa_template=MULTI_SOURCE_QA,
            summary_template=MULTI_SOURCE_QA,
        )

    def mark_pipeline_complete(self, collection_name: str, auto_initialize: bool = True):
        self._ready[collection_name] = True
        if auto_initialize:
            return self.initialize_query_engine(collection_name)
        return None
