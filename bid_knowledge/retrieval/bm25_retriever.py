from __future__ import annotations

from pathlib import Path
from typing import Any

from bid_knowledge.schemas.models import RetrievalChunk
from bid_knowledge.utils.io_utils import read_jsonl
from bid_knowledge.utils.text_utils import safe_preview, tokenize_for_search


class BM25Retriever:
    def __init__(self, chunks: list[RetrievalChunk]):
        self.chunks = chunks
        self.tokenized_corpus = [tokenize_for_search(f"{chunk.title} {chunk.content}") for chunk in chunks]
        self._bm25 = None
        try:
            from rank_bm25 import BM25Okapi

            self._bm25 = BM25Okapi(self.tokenized_corpus)
        except ImportError:
            self._bm25 = None

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        query_tokens = tokenize_for_search(query)
        if self._bm25 is not None:
            scores = self._bm25.get_scores(query_tokens)
        else:
            scores = []
            query_set = set(query_tokens)
            for tokens in self.tokenized_corpus:
                overlap = len(query_set & set(tokens))
                scores.append(float(overlap))

        ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)[:top_k]
        results = []
        for index, score in ranked:
            chunk = self.chunks[index]
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


def load_chunks(path: str | Path) -> list[RetrievalChunk]:
    return [RetrievalChunk(**item) for item in read_jsonl(path)]
