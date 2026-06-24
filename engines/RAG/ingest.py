import os
import re
import pickle
import shutil
import uuid
from pathlib import Path
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
# langchain_community Chroma kept for compatibility; migrate to langchain_chroma once confirmed installed
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_RAG_DIR = Path(__file__).resolve().parent

# ── config ──────────────────────────────────────────────────────────────────
MD_PATH              = os.environ.get("RAG_HANDBOOK_PATH", str(_RAG_DIR / "CIS_Handbook.md"))
DOC_ID               = "CIS Student Handbook"
VERSION_DATE         = "2026-03-05"
PERSIST_DIR          = os.environ.get("RAG_CHROMA_DIR",    str(_RAG_DIR / "chroma_db"))
CHUNKS_FILE          = os.environ.get("RAG_CHUNKS_FILE",   str(_RAG_DIR / "chunks.pkl"))
EMBED_MODEL          = "BAAI/bge-small-en-v1.5"

PARENT_CHUNK_SIZE    = 800
PARENT_CHUNK_OVERLAP = 250
CHILD_CHUNK_SIZE     = 200
CHILD_CHUNK_OVERLAP  = 40


def load_md_as_pages(md_path: str) -> list[Document]:
    with open(md_path, encoding="utf-8") as f:
        raw = f.read()

    page_pattern = re.compile(r"---\s*PAGE\s+(\d+)\s*---", re.IGNORECASE)
    parts = page_pattern.split(raw)

    docs = []
    pre_content = parts[0].strip()
    if pre_content:
        docs.append(Document(
            page_content=pre_content,
            metadata={"doc_id": DOC_ID, "version_date": VERSION_DATE,
                      "page": 0, "major": "CIS", "handbook_type": "Undergraduate"}
        ))

    i = 1
    while i < len(parts) - 1:
        page_num = int(parts[i])
        content  = parts[i + 1].strip()
        if content:
            docs.append(Document(
                page_content=content,
                metadata={"doc_id": DOC_ID, "version_date": VERSION_DATE,
                          "page": page_num, "major": "CIS", "handbook_type": "Undergraduate"}
            ))
        i += 2

    return docs


def ingest_document():
    if not os.path.exists(MD_PATH):
        raise FileNotFoundError(f"Handbook not found: {MD_PATH}")

    print(f"📄 Reading: {MD_PATH}")
    page_docs = load_md_as_pages(MD_PATH)
    print(f"✅ Extracted {len(page_docs)} pages.")

    print(f"✂️  Chunking (Parent={PARENT_CHUNK_SIZE}, Child={CHILD_CHUNK_SIZE})...")

    parent_splitter = RecursiveCharacterTextSplitter(
        chunk_size=PARENT_CHUNK_SIZE,
        chunk_overlap=PARENT_CHUNK_OVERLAP,
        separators=["\n\n", "\n", ".", " ", ""],
    )
    child_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHILD_CHUNK_SIZE,
        chunk_overlap=CHILD_CHUNK_OVERLAP,
        separators=["\n\n", "\n", ".", " ", ""],
    )

    parent_dict  = {}
    child_chunks = []

    for d in parent_splitter.split_documents(page_docs):
        parent_id = str(uuid.uuid4())
        d.metadata["parent_id"]   = parent_id
        d.metadata["chunk_type"]  = "parent"
        parent_dict[parent_id]    = d

        for c in child_splitter.split_documents([d]):
            c.metadata["parent_id"]  = parent_id
            c.metadata["chunk_type"] = "child"
            c.metadata["page"]       = str(d.metadata.get("page", "Unknown"))
            child_chunks.append(c)

    print(f"✅ {len(parent_dict)} parent chunks, {len(child_chunks)} child chunks.")

    with open(CHUNKS_FILE, "wb") as f:
        pickle.dump(parent_dict, f)
    print(f"💾 Saved parent chunks → {CHUNKS_FILE}")

    print(f"🧠 Loading embedding model ({EMBED_MODEL})...")
    embeddings = HuggingFaceEmbeddings(model_name=EMBED_MODEL)

    if os.path.exists(PERSIST_DIR):
        shutil.rmtree(PERSIST_DIR)
        print("🗑️  Cleared old ChromaDB.")

    print(f"💾 Building ChromaDB (child chunks) → {PERSIST_DIR}...")
    Chroma.from_documents(child_chunks, embeddings, persist_directory=PERSIST_DIR)

    print("🎉 Ingestion complete. See README for how to start the PathFinder backend and UI.")


if __name__ == "__main__":
    ingest_document()
