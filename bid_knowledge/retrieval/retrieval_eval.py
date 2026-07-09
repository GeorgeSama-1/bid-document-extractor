from __future__ import annotations

from pathlib import Path
from typing import Any

from bid_knowledge.retrieval.bm25_retriever import BM25Retriever, load_chunks
from bid_knowledge.retrieval.vector_retriever import VectorRetriever
from bid_knowledge.utils.io_utils import read_json, write_json


def evaluate_retrieval(
    chunks_path: str | Path,
    queries_path: str | Path,
    method: str = "bm25",
    top_k: int = 5,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    chunks = load_chunks(chunks_path)
    retriever = BM25Retriever(chunks) if method == "bm25" else VectorRetriever(chunks)
    queries = read_json(queries_path)

    reports = []
    hit_count = 0
    for item in queries:
        query = item["query"]
        expected_keywords = item.get("expected_keywords", [])
        results = retriever.search(query, top_k=top_k)
        hit_position = None
        for index, result in enumerate(results, start=1):
            haystack = f"{result['title']} {result['content_preview']}"
            if all(keyword in haystack for keyword in expected_keywords):
                hit_position = index
                hit_count += 1
                break
        reports.append(
            {
                "query": query,
                "expected_keywords": expected_keywords,
                "top_k_results": results,
                "hit": hit_position is not None,
                "hit_position": hit_position,
            }
        )

    report = {
        "method": method,
        "top_k": top_k,
        "query_count": len(queries),
        "hit_count": hit_count,
        "hit_rate": (hit_count / len(queries)) if queries else 0.0,
        "results": reports,
    }
    if out_path:
        write_json(out_path, report)
    return report
