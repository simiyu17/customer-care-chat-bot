import os
import json
import shutil
from llama_index.core import SimpleDirectoryReader, VectorStoreIndex, StorageContext, load_index_from_storage
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.core import Settings

PERSIST_DIR = "./storage/vector_store"
DOCS_DIR = "./data/policies"
DOC_EXTS = (".pdf", ".md", ".txt")
FINGERPRINT_FILE = os.path.join(PERSIST_DIR, "docs_fingerprint.json")

def _docs_fingerprint() -> dict:
    """Name, size and mtime of each source document, used to detect when the index is stale."""
    fingerprint = {}
    for root, _, files in os.walk(DOCS_DIR):
        for name in files:
            if name.lower().endswith(DOC_EXTS):
                path = os.path.join(root, name)
                stat = os.stat(path)
                fingerprint[os.path.relpath(path, DOCS_DIR)] = [stat.st_size, int(stat.st_mtime)]
    return fingerprint

def _stored_fingerprint():
    try:
        with open(FINGERPRINT_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None

def initialize_rag_engine():
    os.makedirs(PERSIST_DIR, exist_ok=True)
    os.makedirs(DOCS_DIR, exist_ok=True)

    # Only embeddings are needed: the agent's own LLM writes the final answer from retrieved chunks
    Settings.embed_model = OpenAIEmbedding(model="text-embedding-3-small")

    fingerprint = _docs_fingerprint()

    if os.listdir(PERSIST_DIR) and _stored_fingerprint() == fingerprint:
        print("-> Loading existing vector database storage index...")
        storage_context = StorageContext.from_defaults(persist_dir=PERSIST_DIR)
        index = load_index_from_storage(storage_context)
    else:
        print("-> Constructing new Vector Index from source documents...")
        shutil.rmtree(PERSIST_DIR)
        os.makedirs(PERSIST_DIR)

        if fingerprint:
            documents = SimpleDirectoryReader(DOCS_DIR, required_exts=list(DOC_EXTS), recursive=True).load_data()
        else:
            documents = []
        
        parser = SentenceSplitter(chunk_size=512, chunk_overlap=50)
        nodes = parser.get_nodes_from_documents(documents)
        
        index = VectorStoreIndex(nodes)
        index.storage_context.persist(persist_dir=PERSIST_DIR)
        with open(FINGERPRINT_FILE, "w") as f:
            json.dump(fingerprint, f)
        
    return index.as_retriever(similarity_top_k=3)

# Initialize retriever instance
retriever = initialize_rag_engine()

def search_sales_policies(query: str) -> str:
    """Invoked inside your LangGraph query_sales_policy_rag tool."""
    try:
        results = retriever.retrieve(query)
    except Exception as e:
        return f"Error querying policy vector store: {str(e)}"

    if not results:
        return "No matching policy content was found."

    # Return raw excerpts with their source so the agent can answer and cite them
    excerpts = []
    for i, result in enumerate(results, start=1):
        source = result.node.metadata.get("file_name", "unknown source")
        page = result.node.metadata.get("page_label")
        location = f"{source}, page {page}" if page else source
        excerpts.append(f"[Excerpt {i} — {location}]\n{result.node.get_content().strip()}")
    return "\n\n".join(excerpts)
