from __future__ import annotations

from typing import Any

from bid_knowledge.schemas.models import PdfTextBlock
from bid_knowledge.schemas.models import ReusableCandidate
from bid_knowledge.utils.id_utils import make_stable_id


def _clean_toc_title(title: str) -> str:
    return " ".join(str(title or "").strip().split())


def _toc_path_prefix(path_root: str) -> list[str]:
    return [part.strip() for part in str(path_root or "PDF").split(" / ") if part.strip()] or ["PDF"]


def _toc_leaf_indexes(toc: list[dict[str, Any]]) -> set[int]:
    leaf_indexes: set[int] = set()
    for index, item in enumerate(toc):
        level = int(item.get("level") or 1)
        next_level = int(toc[index + 1].get("level") or 1) if index + 1 < len(toc) else 0
        if index + 1 == len(toc) or next_level <= level:
            leaf_indexes.add(index)
    return leaf_indexes


def _compact(text: str) -> str:
    return "".join(str(text or "").split())


def _block_top_y(block: PdfTextBlock | None) -> float | None:
    return float(block.bbox[1]) if block and block.bbox and len(block.bbox) >= 2 else None


def _blocks_by_page(blocks: list[PdfTextBlock] | None) -> dict[int, list[PdfTextBlock]]:
    grouped: dict[int, list[PdfTextBlock]] = {}
    for block in sorted(blocks or [], key=lambda item: (item.page_no, _block_top_y(item) or 0.0, item.block_no)):
        grouped.setdefault(block.page_no, []).append(block)
    return grouped


def _is_toc_page(blocks: list[PdfTextBlock]) -> bool:
    compact_texts = [_compact(block.text) for block in blocks]
    has_toc_title = any(text in {"目录", "目錄"} for text in compact_texts)
    has_dotted_entries = sum(1 for text in compact_texts if "..." in text or "。。" in text or "·" * 3 in text)
    return has_toc_title and has_dotted_entries >= 1


def _first_toc_page_between(blocks_by_page: dict[int, list[PdfTextBlock]], start_page: int, end_page: int) -> int | None:
    for page_no in range(int(start_page), int(end_page) + 1):
        if _is_toc_page(blocks_by_page.get(page_no, [])):
            return page_no
    return None


def _find_toc_title_block(title: str, page_no: int, blocks_by_page: dict[int, list[PdfTextBlock]]) -> PdfTextBlock | None:
    target = _compact(title)
    if not target:
        return None
    candidates = []
    for block in blocks_by_page.get(page_no, []):
        text = _compact(block.text)
        if not text:
            continue
        if text == target or target in text or text in target:
            candidates.append(block)
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (len(_compact(item.text)), _block_top_y(item) or 0.0, item.block_no))[0]


def build_toc_leaf_candidates(
    *,
    toc: list[dict[str, Any]],
    page_count: int,
    path_root: str = "PDF",
    company_id: str = "pdf",
    document_id: str = "pdf_document",
    blocks: list[PdfTextBlock] | None = None,
) -> list[ReusableCandidate]:
    root_parts = _toc_path_prefix(path_root)
    stack: list[str] = []
    leaf_indexes = _toc_leaf_indexes(toc)
    blocks_by_page = _blocks_by_page(blocks)
    leaf_entries: list[dict[str, Any]] = []

    for index, item in enumerate(toc):
        level = max(1, int(item.get("level") or 1))
        title = _clean_toc_title(str(item.get("title") or ""))
        if not title:
            continue
        while len(stack) >= level:
            stack.pop()
        stack.append(title)
        if index not in leaf_indexes:
            continue

        next_page = int(toc[index + 1].get("page") or page_count + 1) if index + 1 < len(toc) else page_count + 1
        page_start = max(1, int(item.get("page") or 1))
        page_end = max(page_start, min(page_count, next_page - 1))
        later_toc_page = _first_toc_page_between(blocks_by_page, page_start + 1, page_end)
        if later_toc_page is not None:
            page_end = max(page_start, later_toc_page - 1)
        section_parts = [*root_parts, *stack]
        parent_title = stack[-2] if len(stack) >= 2 else ""
        section_path = " / ".join(section_parts)
        start_block = _find_toc_title_block(title, page_start, blocks_by_page)
        leaf_entries.append(
            {
                "index": index,
                "title": title,
                "section_path": section_path,
                "parent_title": parent_title,
                "page_start": page_start,
                "page_end": page_end,
                "start_block": start_block,
            }
        )

    candidates: list[ReusableCandidate] = []
    for index, entry in enumerate(leaf_entries):
        next_entry = leaf_entries[index + 1] if index + 1 < len(leaf_entries) else None
        start_block = entry.get("start_block")
        next_block = next_entry.get("start_block") if next_entry else None
        start_y = _block_top_y(start_block)
        end_y = _block_top_y(next_block) if next_block and int(next_entry["page_start"]) == int(entry["page_end"]) else None
        page_end = int(entry["page_end"])
        if next_entry and int(next_entry["page_start"]) == int(entry["page_start"]):
            page_end = int(entry["page_start"])
        evidence = {
            "source": "pdf_toc_leaf",
            "start_y": start_y,
            "end_y": end_y,
            "start_block_id": start_block.block_id if start_block else None,
            "end_block_id": next_block.block_id if next_block and end_y is not None else None,
        }
        candidates.append(
            ReusableCandidate(
                candidate_id=make_stable_id("toc-leaf", entry["index"], entry["section_path"], entry["page_start"], page_end),
                company_id=company_id,
                document_id=document_id,
                rule_id=make_stable_id("toc-rule", entry["section_path"]),
                section_path=entry["section_path"],
                from_history_bid=True,
                has_standard_template=True,
                title=entry["title"],
                content="",
                candidate_type="pdf_toc_leaf",
                reuse_method="PDF目录叶子章节",
                reuse_level="document",
                enter_long_term_library=True,
                source_file="",
                source_page=int(entry["page_start"]),
                source_page_end=page_end,
                source_container_title=entry["parent_title"],
                source_bbox=start_block.bbox if start_block else None,
                source_block_ids=[start_block.block_id] if start_block else [],
                material_evidence=evidence,
            )
        )

    return candidates


def toc_leaf_section_paths(candidates: list[ReusableCandidate]) -> list[str]:
    seen: set[str] = set()
    paths: list[str] = []
    for candidate in candidates:
        if candidate.section_path in seen:
            continue
        seen.add(candidate.section_path)
        paths.append(candidate.section_path)
    return paths


def top_level_modules_from_toc_candidates(candidates: list[ReusableCandidate]) -> list[str]:
    modules: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        parts = [part.strip() for part in candidate.section_path.split(" / ") if part.strip()]
        if len(parts) < 2:
            continue
        module = parts[1]
        if module in seen:
            continue
        seen.add(module)
        modules.append(module)
    return modules
