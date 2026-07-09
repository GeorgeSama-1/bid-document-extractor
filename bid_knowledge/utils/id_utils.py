from __future__ import annotations

import hashlib


def make_stable_id(prefix: str, *parts: object) -> str:
    joined = "||".join("" if part is None else str(part) for part in parts)
    digest = hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"
