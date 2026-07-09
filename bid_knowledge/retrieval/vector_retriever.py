from __future__ import annotations

from typing import Any

from bid_knowledge.schemas.models import RetrievalChunk
from bid_knowledge.utils.text_utils import safe_preview


class VectorRetriever:
    def __init__(self, chunks: list[RetrievalChunk]):
        self.chunks = chunks
        self.available = False
        self._index = None
        self._embeddings = None
        self._model = None
        try:
            import faiss  # noqa: F401
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
            vectors = self._model.encode([f"{chunk.title} {chunk.content}" for chunk in chunks], normalize_embeddings=True)
            import faiss

            self._index = faiss.IndexFlatIP(vectors.shape[1])
            self._index.add(vectors)
            self._embeddings = vectors
            self.available = True
        except Exception:
            self.available = False

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if not self.available or self._index is None or self._model is None:
            return []
        query_vector = self._model.encode([query], normalize_embeddings=True)
        scores, indices = self._index.search(query_vector, top_k)
        results: list[dict[str, Any]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self.chunks):
                continue
            chunk = self.chunks[idx]
            results.append(
                {
                    "score": float(score),
                    "chunk_id": chunk.chunk_id,
                    "candidate_id": chunk.candidate_id,
                    "title": chunk.title,
                    "content_preview": safe_preview(chunk.content, limit=180),
                    "source_page": chunk.source_page,
                    "section_path": chunk.section_path,
                    "candidate_type": chunk.candidate_type,
                }
            )
        return results
