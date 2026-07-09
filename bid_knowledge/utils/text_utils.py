from __future__ import annotations

import re
import unicodedata
from typing import Iterable


PUNCT_PATTERN = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def to_half_width(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def clean_text(text: str | None) -> str:
    if text is None:
        return ""
    return str(text).replace("\u3000", " ").strip()


def safe_preview(text: str, limit: int = 120) -> str:
    normalized = re.sub(r"\s+", " ", clean_text(text))
    return normalized[:limit]


def flatten_table_rows(rows: list[list[str]]) -> str:
    lines = []
    for row in rows:
        items = [clean_text(item) for item in row if clean_text(item)]
        if items:
            lines.append(" | ".join(items))
    return "\n".join(lines)


def tokenize_for_search(text: str) -> list[str]:
    normalized = to_half_width(clean_text(text)).lower()
    parts = re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", normalized)
    tokens: list[str] = []
    for part in parts:
        tokens.append(part)
        if re.fullmatch(r"[\u4e00-\u9fff]+", part) and len(part) > 1:
            tokens.extend(part[idx : idx + 2] for idx in range(len(part) - 1))
    return tokens


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", to_half_width(clean_text(text))).lower()


def strip_punctuation(text: str) -> str:
    return PUNCT_PATTERN.sub("", text)


def contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword for keyword in keywords if keyword and keyword in text)
