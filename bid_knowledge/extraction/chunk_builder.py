from __future__ import annotations

from pathlib import Path

from bid_knowledge.schemas.models import RetrievalChunk, ReusableCandidate
from bid_knowledge.utils.id_utils import make_stable_id
from bid_knowledge.utils.io_utils import write_jsonl


def _split_text(text: str, chunk_size: int = 500, overlap: int = 80) -> list[str]:
    if len(text) <= chunk_size:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def build_chunks(
    candidates: list[ReusableCandidate],
    out_path: str | Path | None = None,
) -> list[RetrievalChunk]:
    chunks: list[RetrievalChunk] = []
    for candidate in candidates:
        payloads = _split_text(candidate.content) or [candidate.title]
        for index, content in enumerate(payloads):
            chunks.append(
                RetrievalChunk(
                    chunk_id=make_stable_id("chunk", candidate.candidate_id, index),
                    company_id=candidate.company_id,
                    document_id=candidate.document_id,
                    candidate_id=candidate.candidate_id,
                    title=candidate.title,
                    content=content,
                    candidate_type=candidate.candidate_type,
                    section_path=candidate.section_path,
                    source_page=candidate.source_page,
                    reuse_method=candidate.reuse_method,
                    enter_long_term_library=candidate.enter_long_term_library,
                    metadata={
                        "candidate_type": candidate.candidate_type,
                        "reuse_level": candidate.reuse_level,
                        "source_block_ids": candidate.source_block_ids,
                    },
                )
            )
    if out_path:
        write_jsonl(out_path, chunks)
    return chunks
