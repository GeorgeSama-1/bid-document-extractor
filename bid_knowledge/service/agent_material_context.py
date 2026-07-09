from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from bid_knowledge.service.result_browser import ResultNotFoundError


@dataclass(frozen=True)
class MaterialRecord:
    material_id: str
    material_path: str
    section_path: str
    title: str
    content_markdown: str
    source_pages: list[int]
    evidence: list[dict[str, Any]]
    normalized_path: str
    normalized_title: str


class AgentMaterialContextService:
    def __init__(self, outputs_dir: Path | str = "outputs") -> None:
        self.outputs_dir = Path(outputs_dir).resolve()

    def get_context(
        self,
        run_name: str,
        *,
        section_path: str | None = None,
        title: str | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        records = self.build_index(run_name)
        candidates = self._resolve(records, section_path=section_path or "", title=title or "", top_k=top_k)
        if not candidates:
            return {"status": "not_found", "match_type": "not_found", "candidates": []}

        top = candidates[0]
        if top["match_type"] == "fuzzy" and len(candidates) > 1 and self._is_ambiguous_fuzzy(candidates):
            return {
                "status": "ambiguous",
                "match_type": "fuzzy",
                "candidates": [self._candidate_payload(candidate) for candidate in candidates],
            }

        record = top["record"]
        return {
            "status": "matched",
            "match_type": top["match_type"],
            "selected": self._candidate_payload(top),
            "content_markdown": record.content_markdown,
            "source_pages": record.source_pages,
            "evidence": record.evidence,
            "candidates": [],
        }

    def build_index(self, run_name: str) -> list[MaterialRecord]:
        modules_dir = self._modules_dir(run_name)
        records: list[MaterialRecord] = []
        for material_dir in sorted(_iter_material_dirs(modules_dir), key=lambda path: path.relative_to(modules_dir).as_posix()):
            record = self._record_from_material_dir(modules_dir, material_dir)
            if record:
                records.append(record)
        return records

    def _resolve(
        self,
        records: list[MaterialRecord],
        *,
        section_path: str,
        title: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        limit = max(1, int(top_k or 5))
        stripped_path = str(section_path or "").strip()
        stripped_title = str(title or "").strip()

        if stripped_path:
            exact = [
                _match(record, "exact_path", 1.0)
                for record in records
                if stripped_path in {record.section_path, record.material_path}
            ]
            if exact:
                return exact[:limit]

            normalized_path = _normalize_key(stripped_path)
            normalized = [
                _match(record, "normalized_path", 0.98)
                for record in records
                if normalized_path in {_normalize_key(record.section_path), _normalize_key(record.material_path)}
            ]
            if normalized:
                return normalized[:limit]

        query_title = stripped_title or _leaf_title(stripped_path)
        normalized_title = _normalize_title(query_title)
        if normalized_title:
            title_matches = [
                _match(record, "title", 0.95)
                for record in records
                if normalized_title == record.normalized_title
            ]
            if title_matches:
                return title_matches[:limit]

            fuzzy = sorted(
                (
                    _match(record, "fuzzy", _title_similarity(normalized_title, record.normalized_title))
                    for record in records
                ),
                key=lambda item: (-float(item["score"]), len(item["record"].normalized_title), item["record"].section_path),
            )
            fuzzy = [item for item in fuzzy if float(item["score"]) >= 0.55]
            return fuzzy[:limit]
        return []

    def _record_from_material_dir(self, modules_dir: Path, material_dir: Path) -> MaterialRecord | None:
        rel_path = material_dir.relative_to(modules_dir).as_posix()
        meta = _read_json_if_exists(material_dir / "material_meta.json")
        ordered = _read_json_if_exists(material_dir / "ordered_material.json")
        markdown_path = material_dir / "material.md"
        content_markdown = markdown_path.read_text(encoding="utf-8") if markdown_path.is_file() else ""

        title = str(meta.get("material_title") or ordered.get("material_title") or material_dir.name).strip()
        section_path = str(meta.get("section_path") or ordered.get("section_path") or rel_path.replace("/", " / ")).strip()
        source_pages, evidence = _extract_evidence(ordered.get("items") if isinstance(ordered.get("items"), list) else [])
        return MaterialRecord(
            material_id=rel_path,
            material_path=rel_path,
            section_path=section_path,
            title=title,
            content_markdown=content_markdown,
            source_pages=source_pages,
            evidence=evidence,
            normalized_path=_normalize_key(section_path),
            normalized_title=_normalize_title(title),
        )

    def _modules_dir(self, run_name: str) -> Path:
        run_dir = _safe_join(self.outputs_dir, run_name)
        modules_dir = run_dir / "modules"
        if not modules_dir.is_dir():
            raise ResultNotFoundError(f"Run has no modules directory: {run_name}")
        return modules_dir.resolve()

    def _candidate_payload(self, candidate: dict[str, Any]) -> dict[str, Any]:
        record = candidate["record"]
        return {
            "material_id": record.material_id,
            "section_path": record.section_path,
            "material_path": record.material_path,
            "title": record.title,
            "score": round(float(candidate["score"]), 4),
        }

    def _is_ambiguous_fuzzy(self, candidates: list[dict[str, Any]]) -> bool:
        if len(candidates) < 2:
            return False
        first = float(candidates[0]["score"])
        second = float(candidates[1]["score"])
        return second >= 0.55 and (first - second) <= 0.15


class ProjectMaterialContextService:
    def __init__(
        self,
        outputs_dir: Path | str = "outputs",
        projects_config_path: Path | str = "configs/material_projects.json",
    ) -> None:
        self.outputs_dir = Path(outputs_dir).resolve()
        self.projects_config_path = Path(projects_config_path).resolve()
        self.run_service = AgentMaterialContextService(self.outputs_dir)

    def get_project_context(
        self,
        project_id: str,
        *,
        section_path: str | None = None,
        title: str | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        project = self._project(project_id)
        runs = project.get("runs") if isinstance(project.get("runs"), dict) else {}
        if not runs:
            return {
                "status": "error",
                "project_id": project_id,
                "message": f"Project has no runs configured: {project_id}",
                "candidates": [],
            }

        selected_file_type = _section_path_root(section_path or "")
        run_scope = _runs_for_file_type(runs, selected_file_type) if selected_file_type else list(runs.items())
        if not run_scope:
            run_scope = list(runs.items())

        contexts: list[dict[str, Any]] = []
        for file_type, run_name in run_scope:
            context = self.run_service.get_context(
                str(run_name),
                section_path=section_path,
                title=title,
                top_k=top_k,
            )
            contexts.append(_annotate_project_context(context, project_id, str(file_type), str(run_name)))

        matched = [context for context in contexts if context.get("status") == "matched"]
        if matched:
            matched = sorted(matched, key=lambda context: _match_type_priority(str(context.get("match_type") or "")))
            best_priority = _match_type_priority(str(matched[0].get("match_type") or ""))
            best = [context for context in matched if _match_type_priority(str(context.get("match_type") or "")) == best_priority]
            if len(best) == 1:
                return best[0]
            return {
                "status": "ambiguous",
                "project_id": project_id,
                "match_type": "multi_run",
                "candidates": [context.get("selected") for context in best if context.get("selected")],
            }

        candidates = []
        for context in contexts:
            candidates.extend(context.get("candidates") or [])
        if candidates:
            return {
                "status": "ambiguous",
                "project_id": project_id,
                "match_type": "multi_run",
                "candidates": candidates[: max(1, int(top_k or 5))],
            }
        return {
            "status": "not_found",
            "project_id": project_id,
            "match_type": "not_found",
            "candidates": [],
        }

    def _project(self, project_id: str) -> dict[str, Any]:
        projects = _read_json_if_exists(self.projects_config_path)
        project = projects.get(project_id)
        if not isinstance(project, dict):
            raise ResultNotFoundError(f"Project not found: {project_id}")
        return project


def _iter_material_dirs(modules_dir: Path) -> list[Path]:
    dirs: list[Path] = []
    for path in modules_dir.rglob("*"):
        if not path.is_dir():
            continue
        if path.name in {"text_items", "table_items", "image_items", "original"}:
            continue
        if (path / "material.md").is_file() or (path / "ordered_material.json").is_file() or (path / "material_meta.json").is_file():
            dirs.append(path)
    return dirs


def _section_path_root(section_path: str) -> str:
    parts = [part.strip() for part in re.split(r"\s*/\s*", str(section_path or "")) if part.strip()]
    return parts[0] if parts else ""


def _runs_for_file_type(runs: dict[str, Any], file_type: str) -> list[tuple[str, Any]]:
    normalized = _normalize_key(file_type)
    return [(key, value) for key, value in runs.items() if _normalize_key(str(key)) == normalized]


def _annotate_project_context(context: dict[str, Any], project_id: str, file_type: str, run_name: str) -> dict[str, Any]:
    annotated = dict(context)
    annotated["project_id"] = project_id
    if context.get("status") == "matched":
        annotated["file_type"] = file_type
        annotated["run_name"] = run_name
        selected = dict(annotated.get("selected") or {})
        selected["file_type"] = file_type
        selected["run_name"] = run_name
        annotated["selected"] = selected
    if context.get("status") == "ambiguous":
        annotated["candidates"] = [
            {**candidate, "file_type": file_type, "run_name": run_name}
            for candidate in context.get("candidates") or []
            if isinstance(candidate, dict)
        ]
    return annotated


def _match_type_priority(match_type: str) -> int:
    return {
        "exact_path": 0,
        "normalized_path": 1,
        "title": 2,
        "fuzzy": 3,
    }.get(match_type, 9)


def _extract_evidence(items: list[Any]) -> tuple[list[int], list[dict[str, Any]]]:
    pages: set[int] = set()
    evidence: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        page_no = item.get("page_no")
        if isinstance(page_no, int):
            pages.add(page_no)
        item_type = str(item.get("item_type") or item.get("type") or "")
        if item_type == "text" and item.get("material_role") == "table_text":
            continue
        evidence.append(
            {
                "type": item_type,
                "title": item.get("table_title") or item.get("image_title") or item.get("table_id") or item.get("image_id") or "",
                "page_no": page_no,
                "item_id": item.get("item_id") or item.get("block_id") or item.get("table_id") or item.get("image_id") or "",
            }
        )
    return sorted(pages), evidence


def _match(record: MaterialRecord, match_type: str, score: float) -> dict[str, Any]:
    return {"record": record, "match_type": match_type, "score": score}


def _title_similarity(query: str, title: str) -> float:
    if not query or not title:
        return 0.0
    if query == title:
        return 1.0
    if query in title or title in query:
        return 1.0
    ordered_overlap = _ordered_char_overlap(query, title)
    sequence = SequenceMatcher(None, query, title).ratio()
    return max(sequence, ordered_overlap)


def _ordered_char_overlap(query: str, title: str) -> float:
    if not query:
        return 0.0
    pos = 0
    matched = 0
    for char in query:
        found = title.find(char, pos)
        if found < 0:
            continue
        matched += 1
        pos = found + 1
    return matched / max(len(query), 1)


def _leaf_title(path: str) -> str:
    parts = [part.strip() for part in re.split(r"\s*/\s*", str(path or "")) if part.strip()]
    return parts[-1] if parts else ""


def _normalize_title(value: str) -> str:
    normalized = _normalize_key(_leaf_title(value) or value)
    return re.sub(r"^(?:\(?\d+(?:\.\d+)*\)?|（?\d+(?:\.\d+)*）?)+", "", normalized)


def _normalize_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[\s/\\，,。；;：:、（）()\[\]【】<>《》\-—_·.．]+", "", normalized)


def _safe_join(root: Path, relative_path: str | Path) -> Path:
    root = root.resolve()
    candidate = (root / Path(str(relative_path))).resolve()
    if candidate != root and root not in candidate.parents:
        raise ResultNotFoundError(f"Path is outside allowed directory: {relative_path}")
    return candidate


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
