import sys
import os
from typing import List, Tuple, Any
from dotenv import load_dotenv
load_dotenv()

OPENAI_KEY=os.getenv('API_OAI')
# sys.path.append('/home/kmodi/.venv/lib/python3.12/site-packages/')
from llama_index.core import SimpleDirectoryReader, VectorStoreIndex
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.openai import OpenAI
from llama_index.core.tools import QueryEngineTool
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import MarkdownNodeParser
import chromadb
from llama_index.vector_stores.chroma import ChromaVectorStore


__import__('pysqlite3')
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')

def _load_md_files(docs_dir: str='save_dir') -> List[str]:

    reader = SimpleDirectoryReader(input_dir=docs_dir)
    docs = reader.load_data()
    return [doc for doc in docs if doc.metadata['file_size']]


def _define_models(llm_name: str, emb_model_name: str, embed_batch_size: int) -> Tuple[Any, Any]:

    llm = OpenAI(model=llm_name, api_key=OPENAI_KEY)
    emb_model = HuggingFaceEmbedding(emb_model_name, embed_batch_size=embed_batch_size)
    return llm, emb_model


def _db_and_ingestion_pipeline(collection_name: str, docs: List[str], emb_model, db_path: str):
    db = chromadb.PersistentClient(path=db_path)
    try:
        db.get_collection(collection_name)
        db.delete_collection(collection_name)
    except:
        pass

    collection = db.create_collection(collection_name)
    vector_store = ChromaVectorStore(chroma_collection=collection)

    pipeline = IngestionPipeline(transformations=[
        # SentenceSplitter(chunk_size=80, chunk_overlap=25),
        MarkdownNodeParser(),
        emb_model
    ], vector_store=vector_store)

    nodes = pipeline.run(documents=docs)
    db_index = VectorStoreIndex.from_vector_store(vector_store, embed_model=emb_model)
    return db_index



def qe_tool(collection_name: str, docs_dir: str='save_dir', llm_name: str='gpt-5-nano', emb_model_name: str='BAAI/bge-small-en-v1.5', embed_batch_size: int=8, db_path: str='./docu_rag.db'):
    docs = _load_md_files(docs_dir=docs_dir)
    llm, emb_model = _define_models(llm_name=llm_name, emb_model_name=emb_model_name, embed_batch_size=embed_batch_size)
    db_index = _db_and_ingestion_pipeline(collection_name=collection_name, docs=docs, emb_model=emb_model, db_path=db_path)

    query_engine = db_index.as_query_engine(
        llm=llm,
        response_mode="tree_summarize",
    )
    return QueryEngineTool.from_defaults(query_engine,
                                         name="doc_retriever",
                                         description="retrieves chunks of docs from the datatailr documentation to add additional context to respond to user queries"
                                         )
    
