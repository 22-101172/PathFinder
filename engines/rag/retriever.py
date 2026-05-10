from __future__ import annotations

import pickle
import re
from pathlib import Path

from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder

BASE_DIR = Path(__file__).resolve().parent
PERSIST_DIR = BASE_DIR / "chroma_db"
CHUNKS_FILE = BASE_DIR / "chunks.pkl"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class HybridRetriever:
    def __init__(self):
        self.embeddings = HuggingFaceEmbeddings(model_name=EMBED_MODEL)
        self.child_db = Chroma(
            persist_directory=str(PERSIST_DIR),
            embedding_function=self.embeddings,
        )
        with open(CHUNKS_FILE, "rb") as f:
            self.parent_dict = pickle.load(f)
        self.parent_docs_list = list(self.parent_dict.values())
        bm25_corpus = [self._tokenize(d.page_content) for d in self.parent_docs_list]
        self.bm25 = BM25Okapi(bm25_corpus)
        self.reranker = CrossEncoder(RERANKER_MODEL)

    def _tokenize(self, text: str) -> list[str]:
        text = text.lower()
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        return [t for t in text.split() if t]

    def _rrf_merge(self, vector_parents: list, bm25_parents: list, k: int = 60) -> list:
        scores = {}
        for rank, parent_doc in enumerate(vector_parents):
            p_id = parent_doc.metadata.get("parent_id")
            scores[p_id] = scores.get(p_id, 0.0) + 1.0 / (k + rank + 1)
        for rank, parent_doc in enumerate(bm25_parents):
            p_id = parent_doc.metadata.get("parent_id")
            scores[p_id] = scores.get(p_id, 0.0) + 1.0 / (k + rank + 1)

        sorted_ids = sorted(scores, key=lambda x: scores[x], reverse=True)
        return [self.parent_dict[pid] for pid in sorted_ids if pid in self.parent_dict]

    def retrieve(self, query: str, k_vec: int = 20, k_bm25: int = 15, k_final: int = 6) -> list:
        child_hits = self.child_db.similarity_search(query, k=k_vec)
        vector_parents = []
        seen_pids = set()

        for child_doc in child_hits:
            pid = child_doc.metadata.get("parent_id")
            if pid and pid not in seen_pids and pid in self.parent_dict:
                seen_pids.add(pid)
                vector_parents.append(self.parent_dict[pid])

        bm25_scores = self.bm25.get_scores(self._tokenize(query))
        top_idx = sorted(
            range(len(bm25_scores)),
            key=lambda i: bm25_scores[i],
            reverse=True,
        )[:k_bm25]
        bm25_parents = [self.parent_docs_list[i] for i in top_idx]
        candidates = self._rrf_merge(vector_parents, bm25_parents)[:15]
        if not candidates:
            return []

        pairs = [(query, doc.page_content) for doc in candidates]
        rerank_scores = self.reranker.predict(pairs)
        ranked = sorted(zip(rerank_scores, candidates), key=lambda x: x[0], reverse=True)
        return [doc for _, doc in ranked[:k_final]]


_retriever_instance = None


def get_retriever() -> HybridRetriever:
    global _retriever_instance
    if _retriever_instance is None:
        _retriever_instance = HybridRetriever()
    return _retriever_instance
