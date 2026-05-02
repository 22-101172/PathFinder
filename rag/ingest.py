import os
import re
import pickle
import hashlib
import shutil
import uuid
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

MD_PATH = "CIS_Handbook.md"
DOC_ID = "CIS Student Handbook"
VERSION_DATE = "2026-03-05"
PERSIST_DIR = "./chroma_db"
CHUNKS_FILE = "chunks.pkl"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"

PARENT_CHUNK_SIZE = 800
PARENT_CHUNK_OVERLAP = 250
CHILD_CHUNK_SIZE = 200
CHILD_CHUNK_OVERLAP = 40

def load_md_as_pages(md_path: str) -> list[Document]:
      with open(md_path, encoding="utf-8") as f:
                raw = f.read()
            page_pattern = re.compile(r"---\s*PAGE\s+(\d+)\s*---", re.IGNORECASE)
    parts = page_pattern.split(raw)
    docs = []
    pre_content = parts[0].strip()
    if pre_content:
              docs.append(Document(page_content=pre_content, metadata={"doc_id": DOC_ID, "version_date": VERSION_DATE, "page": 0}))
          i = 1

    while i < len(parts) - 1:
              page_num = int(parts[i])
              content = parts[i + 1].strip()
              if content:
                            docs.append(Document(page_content=content, metadata={"doc_id": DOC_ID, "version_date": VERSION_DATE, "page": page_num}))
                        i += 2
    return docs

def ingest_document():
      if not os.path.exists(MD_PATH): return
            page_docs = load_md_as_pages(MD_PATH)
    parent_splitter = RecursiveCharacterTextSplitter(chunk_size=PARENT_CHUNK_SIZE, chunk_overlap=PARENT_CHUNK_OVERLAP)
    child_splitter = RecursiveCharacterTextSplitter(chunk_size=CHILD_CHUNK_SIZE, chunk_overlap=CHILD_CHUNK_OVERLAP)
    parent_dict = {}
    child_chunks = []
    for d in parent_splitter.split_documents(page_docs):
              parent_id = str(uuid.uuid4())
        d.metadata["parent_id"] = parent_id
        parent_dict[parent_id] = d
        children = child_splitter.split_documents([d])
        for c in children:
                      c.metadata["parent_id"] = parent_id
                      child_chunks.append(c)
              with open(CHUNKS_FILE, "wb") as f:
                        pickle.dump(parent_dict, f)
                    embeddings = HuggingFaceEmbeddings(model_name=EMBED_MODEL)
    if os.path.exists(PERSIST_DIR): shutil.rmtree(PERSIST_DIR)
          Chroma.from_documents(child_chunks, embeddings, persist_directory=PERSIST_DIR)

if __name__ == "__main__":
      ingest_document()
