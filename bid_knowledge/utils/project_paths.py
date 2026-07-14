from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    env_root = os.environ.get("BID_SOURCE_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    candidates = [Path.cwd().resolve(), *Path(__file__).resolve().parents]
    for candidate in candidates:
        if (candidate / "bid-document-extractor").is_dir() and (
            (candidate / "data").is_dir() or (candidate / "outputs").is_dir()
        ):
            return candidate

    code_root = Path(__file__).resolve().parents[2]
    if code_root.name == "bid-document-extractor":
        return code_root.parent.resolve()
    return Path.cwd().resolve()


def data_dir() -> Path:
    return project_root() / "data"


def raw_data_dir() -> Path:
    return data_dir() / "raw"


def config_dir() -> Path:
    return data_dir() / "configs"


def outputs_dir() -> Path:
    return project_root() / "outputs"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _path_starts_with(path: Path, part: str) -> bool:
    return bool(path.parts) and path.parts[0] == part


def resolve_raw_input_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()

    cwd_candidate = (Path.cwd() / path).resolve()
    if cwd_candidate.exists():
        return cwd_candidate

    root = project_root()
    if _path_starts_with(path, "data"):
        return (root / path).resolve()

    raw_candidate = (raw_data_dir() / path).resolve()
    if raw_candidate.exists():
        return raw_candidate

    root_candidate = (root / path).resolve()
    if root_candidate.exists():
        return root_candidate

    return raw_candidate


def resolve_config_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()

    cwd_candidate = (Path.cwd() / path).resolve()
    if cwd_candidate.exists():
        return cwd_candidate

    root = project_root()
    if _path_starts_with(path, "data"):
        return (root / path).resolve()

    config_candidate = (config_dir() / path).resolve()
    if config_candidate.exists():
        return config_candidate

    root_candidate = (root / path).resolve()
    if root_candidate.exists():
        return root_candidate

    return config_candidate


def resolve_data_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    if _path_starts_with(path, "data"):
        return (project_root() / path).resolve()
    return (data_dir() / path).resolve()


def resolve_input_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()

    cwd_candidate = (Path.cwd() / path).resolve()
    if cwd_candidate.exists():
        return cwd_candidate

    root = project_root()
    if _path_starts_with(path, "outputs") or _path_starts_with(path, "data"):
        return (root / path).resolve()

    for candidate in (
        raw_data_dir() / path,
        config_dir() / path,
        data_dir() / path,
        outputs_dir() / path,
        root / path,
    ):
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    return (root / path).resolve()


def resolve_output_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()

    root_outputs = outputs_dir().resolve()
    root = project_root()
    if _path_starts_with(path, "outputs"):
        return (root / path).resolve()

    cwd_candidate = (Path.cwd() / path).resolve()
    if _is_relative_to(cwd_candidate, root_outputs):
        return cwd_candidate

    return (root_outputs / path).resolve()


def default_project_config_path() -> Path:
    return config_dir() / "material_projects.json"
