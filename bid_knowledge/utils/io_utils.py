from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel


def ensure_parent_dir(path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def ensure_dir(path: str | Path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _serialize(data: Any) -> Any:
    if data is None or isinstance(data, (str, int, float, bool)):
        return data
    if isinstance(data, BaseModel):
        return data.model_dump()
    if isinstance(data, Path):
        return str(data)
    if hasattr(data, "tolist") and callable(getattr(data, "tolist")):
        return _serialize(data.tolist())
    if hasattr(data, "item") and callable(getattr(data, "item")):
        try:
            return _serialize(data.item())
        except (TypeError, ValueError):
            pass
    if isinstance(data, list):
        return [_serialize(item) for item in data]
    if isinstance(data, tuple):
        return [_serialize(item) for item in data]
    if isinstance(data, dict):
        return {key: _serialize(value) for key, value in data.items()}
    return str(data)


def write_json(path: str | Path, data: Any) -> Path:
    target = ensure_parent_dir(path)
    target.write_text(
        json.dumps(_serialize(data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_jsonl(path: str | Path, rows: Iterable[Any]) -> Path:
    target = ensure_parent_dir(path)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(_serialize(row), ensure_ascii=False) + "\n")
    return target


def read_jsonl(path: str | Path) -> list[Any]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def write_csv(path: str | Path, rows: list[dict[str, Any]]) -> Path:
    target = ensure_parent_dir(path)
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _serialize(value) for key, value in row.items()})
    return target
