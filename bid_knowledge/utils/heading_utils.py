from __future__ import annotations

import re
from typing import Any


HEADING_PATTERNS = [
    re.compile(r"^\d+(?:\.\d+)+(?:[、.．]|\s)+"),
    re.compile(r"^\d+[、.．]\s*"),
    re.compile(r"^[（(]\d+(?:\.\d+)*[）)](?:[、.．]|\s)*"),
    re.compile(r"^[（(][一二三四五六七八九十]+[）)](?:[、.．]|\s)*"),
    re.compile(r"^[一二三四五六七八九十]+[、.．]\s*"),
    re.compile(r"^附[:：]\s*"),
]

ATTACHMENT_HEADING_PATTERN = re.compile(r"^附[:：]\s*")


def is_heading_text(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    return any(pattern.match(stripped) for pattern in HEADING_PATTERNS)


def is_attachment_heading(text: str) -> bool:
    stripped = (text or "").strip()
    return bool(ATTACHMENT_HEADING_PATTERN.match(stripped))


def strip_heading_marker(text: str) -> str:
    stripped = (text or "").strip()
    for pattern in HEADING_PATTERNS:
        if pattern.match(stripped):
            stripped = pattern.sub("", stripped, count=1).strip()
            break
    return stripped or (text or "").strip()


def heading_level(text: str) -> int:
    stripped = (text or "").strip()
    dotted = re.match(r"^(\d+(?:\.\d+)+)(?:[、.．]|\s)+", stripped)
    if dotted:
        return len(dotted.group(1).split("."))
    simple = re.match(r"^\d+[、.．]\s*", stripped)
    if simple:
        return 1
    paren = re.match(r"^[（(](\d+(?:\.\d+)*)[）)]", stripped)
    if paren:
        return 10 + len(paren.group(1).split("."))
    chinese = re.match(r"^[（(]?([一二三四五六七八九十]+)[）)]?[、.．]", stripped)
    if chinese:
        return 1
    if stripped.startswith("附"):
        return 99
    return 50


def sanitize_display_title(text: str) -> str:
    return re.sub(r"\s+", " ", strip_heading_marker(text)).strip()


def attachment_heading_title(text: str) -> str:
    stripped = (text or "").strip()
    return ATTACHMENT_HEADING_PATTERN.sub("", stripped, count=1).strip() or sanitize_display_title(text)


def build_heading_candidates(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for block in blocks:
        text = str(block.get("text") or "").strip()
        if not is_heading_text(text):
            continue
        bbox = block.get("bbox") or [0, 0, 0, 0]
        candidates.append(
            {
                "page_no": int(block.get("page_no") or 0),
                "y": float(bbox[1]) if len(bbox) >= 2 else 0.0,
                "raw_title": text,
                "title": sanitize_display_title(text),
                "level": heading_level(text),
                "block_id": block.get("block_id"),
            }
        )
    return sorted(candidates, key=lambda item: (item["page_no"], item["y"]))


def find_nearest_heading(
    heading_candidates: list[dict[str, Any]],
    page_no: int,
    top_y: float | None,
) -> dict[str, Any] | None:
    filtered = []
    for candidate in heading_candidates:
        candidate_page = int(candidate.get("page_no") or 0)
        if candidate_page > page_no:
            continue
        if candidate_page == page_no and top_y is not None and float(candidate.get("y") or 0.0) >= float(top_y):
            continue
        filtered.append(candidate)
    if not filtered:
        return None
    return sorted(filtered, key=lambda item: (item["page_no"], item["y"]), reverse=True)[0]
