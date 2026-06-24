import os
import re
import pickle
import logging
from pathlib import Path
from langchain_community.vectorstores import Chroma  # carry-forward; migrate to langchain_chroma once confirmed installed
from langchain_huggingface import HuggingFaceEmbeddings
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

logger = logging.getLogger(__name__)

_RAG_DIR = Path(__file__).resolve().parent

# ── config ──────────────────────────────────────────────────────────────────
# HF_TOKEN is picked up automatically by Hugging Face libraries if set in .env.
# First startup may be slow while models are downloaded and cached locally.
PERSIST_DIR    = os.environ.get("RAG_CHROMA_DIR",  str(_RAG_DIR / "chroma_db"))
CHUNKS_FILE    = os.environ.get("RAG_CHUNKS_FILE", str(_RAG_DIR / "chunks.pkl"))
EMBED_MODEL    = "BAAI/bge-small-en-v1.5"
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class HybridRetriever:
    """
    4-step parent-child retrieval pipeline:

    1. Vector search   — searches CHILD chunks (small, 200 tokens) for semantic matches
    2. BM25 search     — searches PARENT chunks (large, 800 tokens) for keyword matches
    3. RRF merge       — maps children back to parents, merges both result lists fairly
    4. Cross-encoder   — reranks merged parent list against the query
    """

    def __init__(self):
        logger.info("Loading embedding model (%s)...", EMBED_MODEL)
        self.embeddings = HuggingFaceEmbeddings(model_name=EMBED_MODEL)

        if not os.path.exists(PERSIST_DIR):
            raise FileNotFoundError(
                f"ChromaDB directory not found: {PERSIST_DIR!r}. "
                "Run engines/rag/ingest.py first."
            )
        logger.info("Loading ChromaDB (child chunks) from %s...", PERSIST_DIR)
        self.child_db = Chroma(
            persist_directory=PERSIST_DIR,
            embedding_function=self.embeddings,
        )

        if not os.path.exists(CHUNKS_FILE):
            raise FileNotFoundError(
                f"Parent chunks file not found: {CHUNKS_FILE!r}. "
                "Run engines/rag/ingest.py first."
            )
        logger.info("Loading parent chunks + building BM25 index from %s...", CHUNKS_FILE)
        with open(CHUNKS_FILE, "rb") as f:
            self.parent_dict = pickle.load(f)

        self.parent_docs_list = list(self.parent_dict.values())
        bm25_corpus = [self._tokenize(d.page_content) for d in self.parent_docs_list]
        self.bm25   = BM25Okapi(bm25_corpus)

        logger.info("Loading cross-encoder reranker (%s)...", RERANKER_MODEL)
        self.reranker = CrossEncoder(RERANKER_MODEL)
        logger.info("Retriever ready.")

    # ── private helpers ──────────────────────────────────────────────────────

    def _tokenize(self, text: str) -> list[str]:
        text = text.lower()
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        return [t for t in text.split() if t]

    def _rrf_merge(self, vector_parents: list, bm25_parents: list, k: int = 60) -> list:
        scores: dict = {}
        for rank, doc in enumerate(vector_parents):
            pid = doc.metadata.get("parent_id")
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank + 1)
        for rank, doc in enumerate(bm25_parents):
            pid = doc.metadata.get("parent_id")
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank + 1)
        sorted_ids = sorted(scores, key=lambda x: scores[x], reverse=True)
        return [self.parent_dict[pid] for pid in sorted_ids if pid in self.parent_dict]

    # ── public interface ─────────────────────────────────────────────────────

    def retrieve(self, query: str, k_vec: int = 20, k_bm25: int = 15,
                 k_final: int = 6, filter: dict = None) -> list:

        if not query or not query.strip():
            return []

        # 1. Vector search on children
        chroma_filter = None
        if filter:
            chroma_filter = (
                {"$and": [{k: v} for k, v in filter.items()]}
                if len(filter) > 1 else filter
            )

        child_hits = self.child_db.similarity_search(query, k=k_vec, filter=chroma_filter)

        vector_parents = []
        seen_pids = set()
        for child in child_hits:
            pid = child.metadata.get("parent_id")
            if pid and pid not in seen_pids and pid in self.parent_dict:
                seen_pids.add(pid)
                vector_parents.append(self.parent_dict[pid])

        # 2. BM25 on parents
        bm25_scores = self.bm25.get_scores(self._tokenize(query))
        top_idx     = sorted(range(len(bm25_scores)),
                             key=lambda i: bm25_scores[i], reverse=True)
        bm25_parents = []
        for i in top_idx:
            doc = self.parent_docs_list[i]
            if filter:
                if not all(doc.metadata.get(k) == v for k, v in filter.items()):
                    continue
            bm25_parents.append(doc)
            if len(bm25_parents) >= k_bm25:
                break

        # 3. RRF merge
        candidates = self._rrf_merge(vector_parents, bm25_parents)[:15]
        if not candidates:
            return []

        # 4. Cross-encoder rerank
        pairs         = [(query, doc.page_content) for doc in candidates]
        rerank_scores = self.reranker.predict(pairs)
        ranked        = sorted(zip(rerank_scores, candidates),
                               key=lambda x: x[0], reverse=True)
        return [doc for _, doc in ranked[:k_final]]


# ── singleton ────────────────────────────────────────────────────────────────
_retriever_instance = None

def get_retriever() -> HybridRetriever:
    global _retriever_instance
    if _retriever_instance is None:
        _retriever_instance = HybridRetriever()
    return _retriever_instance
