from __future__ import annotations

import re

from bid_knowledge.schemas.models import PdfTextBlock
from bid_knowledge.utils.heading_utils import is_heading_text


_NUMBERED_HEADING_RE = re.compile(r"^\s*(?:[（(]\d+(?:\.\d+)*[）)]|(?:\d+(?:\.\d+)+|\d+))[、.．、]?\s*")


def merge_multiline_heading_blocks(blocks: list[PdfTextBlock]) -> list[PdfTextBlock]:
    """Join safe, adjacent continuation lines for numbered headings.

    PDF text extraction can split long numbered headings into two blocks. We only
    merge when the first line is a heading-like block and has a strong
    continuation signal, so ordinary body text and table cell fragments stay
    untouched.
    """
    ordered = sorted(blocks, key=lambda item: (item.page_no, _top(item), item.block_no))
    merged: list[PdfTextBlock] = []
    index = 0
    while index < len(ordered):
        current = ordered[index]
        if index + 1 >= len(ordered):
            merged.append(current)
            break
        following = ordered[index + 1]
        if _should_merge_heading_line(current, following):
            merged.append(_merge_blocks(current, following))
            index += 2
            continue
        merged.append(current)
        index += 1
    return merged


def _should_merge_heading_line(current: PdfTextBlock, following: PdfTextBlock) -> bool:
    current_text = _clean_text(current.text)
    following_text = _clean_text(following.text)
    if not current_text or not following_text:
        return False
    if current.page_no != following.page_no:
        return False
    if not _looks_like_numbered_heading(current_text):
        return False
    if is_heading_text(following_text):
        return False
    if _vertical_gap(current, following) > max(18.0, _height(current) * 1.8):
        return False
    if not _horizontally_aligned(current, following):
        return False
    return _has_strong_continuation_signal(current_text, following_text)


def _looks_like_numbered_heading(text: str) -> bool:
    return bool(_NUMBERED_HEADING_RE.match(text)) and is_heading_text(text)


def _has_strong_continuation_signal(current: str, following: str) -> bool:
    if _has_unclosed_bracket(current):
        return True
    if current.endswith(("、", "，", ",", "；", ";", "：", ":")):
        return True
    return len(current) >= 35 and following.endswith(("）", ")", "。"))


def _has_unclosed_bracket(text: str) -> bool:
    pairs = [("（", "）"), ("(", ")"), ("《", "》"), ("“", "”")]
    return any(text.count(opening) > text.count(closing) for opening, closing in pairs)


def _merge_blocks(first: PdfTextBlock, second: PdfTextBlock) -> PdfTextBlock:
    first_text = _clean_text(first.text)
    second_text = _clean_text(second.text)
    joiner = "" if _prefer_direct_join(first_text, second_text) else " "
    bbox = _union_bbox(first.bbox, second.bbox)
    return first.model_copy(update={"text": f"{first_text}{joiner}{second_text}", "bbox": bbox})


def _prefer_direct_join(first: str, second: str) -> bool:
    if not first or not second:
        return True
    if first[-1].isascii() and second[0].isascii():
        return False
    return True


def _union_bbox(first: list[float], second: list[float]) -> list[float]:
    if len(first) < 4:
        return second[:4]
    if len(second) < 4:
        return first[:4]
    return [
        min(float(first[0]), float(second[0])),
        min(float(first[1]), float(second[1])),
        max(float(first[2]), float(second[2])),
        max(float(first[3]), float(second[3])),
    ]


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _top(block: PdfTextBlock) -> float:
    return float(block.bbox[1]) if len(block.bbox) >= 2 else 0.0


def _bottom(block: PdfTextBlock) -> float:
    return float(block.bbox[3]) if len(block.bbox) >= 4 else _top(block)


def _height(block: PdfTextBlock) -> float:
    return max(1.0, _bottom(block) - _top(block))


def _vertical_gap(first: PdfTextBlock, second: PdfTextBlock) -> float:
    return _top(second) - _bottom(first)


def _horizontally_aligned(first: PdfTextBlock, second: PdfTextBlock) -> bool:
    if len(first.bbox) < 4 or len(second.bbox) < 4:
        return False
    first_x0 = float(first.bbox[0])
    first_x1 = float(first.bbox[2])
    second_x0 = float(second.bbox[0])
    second_x1 = float(second.bbox[2])
    overlap = max(0.0, min(first_x1, second_x1) - max(first_x0, second_x0))
    second_width = max(1.0, second_x1 - second_x0)
    return abs(second_x0 - first_x0) <= 40.0 or overlap / second_width >= 0.5
