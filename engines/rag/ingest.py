from __future__ import annotations

import os
import pickle
import re
import shutil
import uuid
from pathlib import Path

from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent.parent
HANDBOOK_CANDIDATES = [
    REPO_ROOT / "data" / "handbook" / "CIS_Handbook.md",
    BASE_DIR / "CIS_Handbook.md",
]
DOC_ID = "CIS Student Handbook"
VERSION_DATE = "2026-03-05"
PERSIST_DIR = BASE_DIR / "chroma_db"
CHUNKS_FILE = BASE_DIR / "chunks.pkl"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"

PARENT_CHUNK_SIZE = 800
PARENT_CHUNK_OVERLAP = 250
CHILD_CHUNK_SIZE = 200
CHILD_CHUNK_OVERLAP = 40


def resolve_handbook_path() -> Path:
    for candidate in HANDBOOK_CANDIDATES:
        if candidate.exists():
            return candidate
    searched = ", ".join(str(path) for path in HANDBOOK_CANDIDATES)
    raise FileNotFoundError(f"Handbook markdown not found. Checked: {searched}")


def load_md_as_pages(md_path: Path) -> list[Document]:
    with open(md_path, encoding="utf-8") as f:
        raw = f.read()

    page_pattern = re.compile(r"---\s*PAGE\s+(\d+)\s*---", re.IGNORECASE)
    parts = page_pattern.split(raw)
    docs: list[Document] = []

    pre_content = parts[0].strip()
    if pre_content:
        docs.append(Document(
            page_content=pre_content,
            metadata={"doc_id": DOC_ID, "version_date": VERSION_DATE, "page": 0},
        ))

    i = 1
    while i < len(parts) - 1:
        page_num = int(parts[i])
        content = parts[i + 1].strip()
        if content:
            docs.append(Document(
                page_content=content,
                metadata={"doc_id": DOC_ID, "version_date": VERSION_DATE, "page": page_num},
            ))
        i += 2

    return docs


def ingest_document() -> None:
    md_path = resolve_handbook_path()
    page_docs = load_md_as_pages(md_path)
    parent_splitter = RecursiveCharacterTextSplitter(
        chunk_size=PARENT_CHUNK_SIZE, chunk_overlap=PARENT_CHUNK_OVERLAP,
    )
    child_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHILD_CHUNK_SIZE, chunk_overlap=CHILD_CHUNK_OVERLAP,
    )

    parent_dict: dict[str, Document] = {}
    child_chunks: list[Document] = []

    for document in parent_splitter.split_documents(page_docs):
        parent_id = str(uuid.uuid4())
        document.metadata["parent_id"] = parent_id
        parent_dict[parent_id] = document
        children = child_splitter.split_documents([document])
        for child in children:
            child.metadata["parent_id"] = parent_id
            child_chunks.append(child)

    with open(CHUNKS_FILE, "wb") as f:
        pickle.dump(parent_dict, f)

    embeddings = HuggingFaceEmbeddings(model_name=EMBED_MODEL)
    if os.path.exists(PERSIST_DIR):
        shutil.rmtree(PERSIST_DIR)

    Chroma.from_documents(child_chunks, embeddings, persist_directory=str(PERSIST_DIR))
    print(f"Ingestion complete. {len(parent_dict)} parent chunks, {len(child_chunks)} child chunks.")


if __name__ == "__main__":
    ingest_document()
