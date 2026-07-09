from __future__ import annotations

import re

from bid_knowledge.utils.text_utils import clean_text, strip_punctuation, to_half_width


NUMBERING_PATTERNS = [
    re.compile(r"^\s*[一二三四五六七八九十百千万]+[、.]?\s*"),
    re.compile(r"^\s*[（(][一二三四五六七八九十0-9]+[)）]\s*"),
    re.compile(r"^\s*\d+(?:\.\d+)*[、.]?\s*"),
]


def strip_section_numbering(title: str) -> str:
    normalized = to_half_width(clean_text(title))
    changed = True
    while changed and normalized:
        changed = False
        for pattern in NUMBERING_PATTERNS:
            updated = pattern.sub("", normalized, count=1)
            if updated != normalized:
                normalized = updated.strip()
                changed = True
    return normalized.strip()


def normalize_section_title(title: str) -> str:
    stripped = strip_section_numbering(title)
    stripped = strip_punctuation(stripped)
    stripped = re.sub(r"\s+", "", stripped)
    return stripped.lower()
