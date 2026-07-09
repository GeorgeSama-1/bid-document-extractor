from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import quote


class ResultBrowserError(Exception):
    """Base error for result browser lookups."""


class ResultNotFoundError(ResultBrowserError):
    """Raised when a requested run, material, or item does not exist."""


class ResultBrowser:
    def __init__(self, outputs_dir: Path | str = "outputs") -> None:
        self.outputs_dir = Path(outputs_dir).resolve()

    def list_runs(self) -> list[dict[str, str]]:
        if not self.outputs_dir.exists():
            return []
        runs = []
        for path in sorted(self.outputs_dir.iterdir(), key=lambda item: item.name):
            if path.is_dir() and (path / "modules").is_dir():
                runs.append({"name": path.name, "path": str(path)})
        return runs

    def get_module_tree(self, run_name: str) -> dict[str, Any]:
        modules_dir = self._modules_dir(run_name)
        return self._build_tree(modules_dir, modules_dir)

    def get_material_meta(self, run_name: str, material_path: str) -> dict[str, Any]:
        return self._read_optional_json(self._material_dir(run_name, material_path) / "material_meta.json")

    def get_ordered_material(self, run_name: str, material_path: str) -> dict[str, Any]:
        material_dir = self._material_dir(run_name, material_path)
        data = self._read_json(material_dir / "ordered_material.json")
        items = data.get("items")
        if isinstance(items, list):
            data["items"] = [
                self._enrich_ordered_item(run_name, material_dir, item)
                if isinstance(item, dict)
                else item
                for item in items
            ]
        return data

    def get_item_detail(
        self,
        run_name: str,
        material_path: str,
        item_type: str,
        item_name: str,
    ) -> dict[str, Any] | str:
        material_dir = self._material_dir(run_name, material_path)
        directory_name = {
            "text": "text_items",
            "table": "table_items",
            "image": "image_items",
            "submaterial": "submaterials",
        }.get(item_type)
        if directory_name is None:
            raise ResultNotFoundError(f"Unsupported item type: {item_type}")

        item_path = self._safe_join(material_dir / directory_name, item_name)
        if item_path.is_dir():
            ordered = item_path / "ordered_material.json"
            if ordered.exists():
                return self._read_json(ordered)
            raise ResultNotFoundError(f"Submaterial has no ordered_material.json: {item_name}")
        if item_path.suffix.lower() == ".json":
            return self._read_json(item_path)
        if item_path.suffix.lower() in {".md", ".txt"}:
            if not item_path.is_file():
                raise ResultNotFoundError(f"Item not found: {item_name}")
            return item_path.read_text(encoding="utf-8")
        raise ResultNotFoundError(f"Unsupported item detail file: {item_name}")

    def get_image_file(self, run_name: str, image_path: str) -> tuple[Path, str]:
        modules_dir = self._modules_dir(run_name)
        file_path = self._safe_join(modules_dir, image_path)
        if not file_path.is_file():
            raise ResultNotFoundError(f"Image not found: {image_path}")
        media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if not media_type.startswith("image/"):
            raise ResultNotFoundError(f"Not an image file: {image_path}")
        return file_path, media_type

    def _enrich_ordered_item(
        self,
        run_name: str,
        material_dir: Path,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        enriched = dict(item)
        item_type = enriched.get("type")
        if item_type == "image":
            file_path = enriched.get("file_path")
            relative_image = self._relative_to_modules(run_name, file_path)
            if relative_image:
                enriched["image_path"] = relative_image
                enriched["preview_url"] = (
                    f"/api/runs/{quote(run_name, safe='')}/images?path="
                    f"{quote(relative_image, safe='')}"
                )
        if item_type == "table":
            json_path = enriched.get("json_path")
            relative_json = self._relative_to_material(material_dir, json_path)
            if relative_json:
                enriched["detail_item_name"] = relative_json
        if item_type == "submaterial":
            target = enriched.get("path") or enriched.get("material_path") or enriched.get("folder_path")
            relative_target = self._relative_to_modules(run_name, target)
            if relative_target:
                enriched["target_material_path"] = relative_target
        return enriched

    def _build_tree(self, path: Path, modules_dir: Path) -> dict[str, Any]:
        rel_path = "" if path == modules_dir else path.relative_to(modules_dir).as_posix()
        node = {
            "name": path.name,
            "path": rel_path,
            "is_material": (path / "ordered_material.json").is_file()
            or (path / "material_meta.json").is_file()
            or (path / "section_meta.json").is_file(),
            "children": [],
        }
        child_dirs = [
            child
            for child in path.iterdir()
            if child.is_dir() and child.name not in {"text_items", "table_items", "image_items", "original"}
        ]
        for child in sorted(child_dirs, key=lambda item: item.name):
            node["children"].append(self._build_tree(child, modules_dir))
        return node

    def _read_optional_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return self._read_json(path)

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise ResultNotFoundError(f"JSON not found: {path.name}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _modules_dir(self, run_name: str) -> Path:
        run_dir = self._safe_join(self.outputs_dir, run_name)
        modules_dir = run_dir / "modules"
        if not modules_dir.is_dir():
            raise ResultNotFoundError(f"Run has no modules directory: {run_name}")
        return modules_dir.resolve()

    def _material_dir(self, run_name: str, material_path: str) -> Path:
        material_dir = self._safe_join(self._modules_dir(run_name), material_path)
        if not material_dir.is_dir():
            raise ResultNotFoundError(f"Material not found: {material_path}")
        return material_dir

    def _safe_join(self, root: Path, relative_path: str | Path) -> Path:
        root = root.resolve()
        candidate = (root / Path(str(relative_path))).resolve()
        if candidate != root and root not in candidate.parents:
            raise ResultNotFoundError(f"Path is outside allowed directory: {relative_path}")
        return candidate

    def _relative_to_modules(self, run_name: str, maybe_path: Any) -> str | None:
        if not isinstance(maybe_path, str) or not maybe_path:
            return None
        path = Path(maybe_path)
        if not path.is_absolute():
            return path.as_posix()
        try:
            return path.resolve().relative_to(self._modules_dir(run_name)).as_posix()
        except ValueError:
            return None

    def _relative_to_material(self, material_dir: Path, maybe_path: Any) -> str | None:
        if not isinstance(maybe_path, str) or not maybe_path:
            return None
        path = Path(maybe_path)
        if not path.is_absolute():
            return path.as_posix()
        try:
            return path.resolve().relative_to(material_dir.resolve()).as_posix()
        except ValueError:
            return None
