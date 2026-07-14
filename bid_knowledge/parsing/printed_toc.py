from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from bid_knowledge.schemas.models import PdfTextBlock


_TOC_MARKERS = {"目录", "目錄", "contents", "tableofcontents"}
_NUMBERED_TITLE = re.compile(
    r"^\s*(?P<number>\d+(?:\.\d+)*)(?P<delimiter>[、．.]?)\s*(?P<body>\S.*)$"
)
_PAGE_AT_END = re.compile(r"^(?P<label>.+?)\s+(?P<page>\d{1,5})\s*$")
_LEADER_AT_END = re.compile(r"(?:[.．·•]{2,}|…+)\s*$")


@dataclass(frozen=True)
class _ParsedEntry:
    item: dict[str, Any]
    has_leader: bool
    numbered: bool


def _compact(value: str) -> str:
    return "".join(str(value or "").split()).lower()


def _normalized(value: str) -> str:
    return " ".join(str(value or "").split())


def _is_marker(value: str) -> bool:
    return _compact(value) in _TOC_MARKERS


def _numbered_title(value: str) -> re.Match[str] | None:
    return _NUMBERED_TITLE.match(_normalized(value))


def _parse_entry(value: str, page_count: int) -> _ParsedEntry | None:
    text = _normalized(value)
    page_match = _PAGE_AT_END.match(text)
    if page_match is None:
        return None
    page = int(page_match.group("page"))
    if not 1 <= page <= page_count:
        return None

    raw_label = page_match.group("label").rstrip()
    leader_match = _LEADER_AT_END.search(raw_label)
    has_leader = leader_match is not None
    if leader_match is not None:
        raw_label = raw_label[: leader_match.start()].rstrip()
    title = _normalized(raw_label)
    number_match = _numbered_title(title)
    if not title or (number_match is None and not has_leader):
        return None

    level = 1
    if number_match is not None:
        level = number_match.group("number").count(".") + 1
    return _ParsedEntry(
        item={"level": level, "title": title, "page": page},
        has_leader=has_leader,
        numbered=number_match is not None,
    )


def _ordered_blocks(blocks: Iterable[PdfTextBlock]) -> dict[int, list[PdfTextBlock]]:
    grouped: dict[int, list[PdfTextBlock]] = defaultdict(list)
    for block in blocks:
        grouped[int(block.page_no)].append(block)
    for page_blocks in grouped.values():
        page_blocks.sort(
            key=lambda item: (
                float(item.bbox[1]) if len(item.bbox) >= 2 else 0.0,
                int(item.block_no),
            )
        )
    return dict(grouped)


def _parse_page(
    blocks: list[PdfTextBlock],
    *,
    page_count: int,
    start_after_marker: bool,
) -> tuple[list[dict[str, Any]], int]:
    entries: list[dict[str, Any]] = []
    leader_count = 0
    pending: str | None = None
    marker_seen = not start_after_marker

    for block in blocks:
        text = _normalized(block.text)
        if not text:
            continue
        if not marker_seen:
            if _is_marker(text):
                marker_seen = True
            continue

        parsed = _parse_entry(text, page_count)
        if parsed is not None and pending is not None and not parsed.numbered:
            combined = _parse_entry(f"{pending}{text}", page_count)
            if combined is not None:
                parsed = combined

        if parsed is not None:
            entries.append(parsed.item)
            leader_count += int(parsed.has_leader)
            pending = None
            continue

        if _numbered_title(text) is not None:
            pending = text
        elif pending is not None:
            pending = f"{pending}{text}"

    return entries, leader_count


def infer_printed_toc(
    blocks: Iterable[PdfTextBlock],
    *,
    page_count: int,
    search_pages: int = 30,
    max_toc_pages: int = 10,
) -> list[dict[str, Any]]:
    """Infer a bookmark-style TOC from text-based printed contents pages.

    The detector deliberately requires an explicit contents marker and at least
    two plausible entries, so ordinary numbered body text is not mistaken for
    a document outline.
    """
    if page_count <= 0 or search_pages <= 0 or max_toc_pages <= 0:
        return []
    by_page = _ordered_blocks(blocks)
    marker_page: int | None = None
    for page_no in range(1, min(page_count, search_pages) + 1):
        if any(_is_marker(block.text) for block in by_page.get(page_no, [])):
            marker_page = page_no
            break
    if marker_page is None:
        return []

    inferred: list[dict[str, Any]] = []
    for offset in range(max_toc_pages):
        page_no = marker_page + offset
        if page_no > page_count:
            break
        page_entries, leader_count = _parse_page(
            by_page.get(page_no, []),
            page_count=page_count,
            start_after_marker=offset == 0,
        )
        if offset == 0:
            if len(page_entries) < 2 or leader_count < 1:
                return []
        elif len(page_entries) < 2 or leader_count < 1:
            break
        inferred.extend(page_entries)

    return inferred if len(inferred) >= 2 else []


__all__ = ["infer_printed_toc"]
