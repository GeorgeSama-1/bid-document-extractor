from __future__ import annotations

import os
import re
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from threading import Lock

from pydantic import BaseModel, ConfigDict


_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class OutputFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    size_bytes: int


class JobFiles:
    def __init__(self) -> None:
        self._locks_guard = Lock()
        self._archive_locks: dict[tuple[Path, str], Lock] = {}

    def list(self, output_root: Path) -> list[OutputFile]:
        return [
            OutputFile(path=relative_path, size_bytes=file_path.stat().st_size)
            for relative_path, file_path in self._iter_safe_files(output_root)
        ]

    def resolve(self, output_root: Path, relative_path: str) -> Path:
        requested = self._validate_relative_path(relative_path)
        for safe_relative, file_path in self._iter_safe_files(output_root):
            if safe_relative == requested:
                return file_path
        raise FileNotFoundError(relative_path)

    def archive(
        self,
        job_id: str,
        output_root: Path,
        archive_root: Path,
    ) -> Path:
        if not _SAFE_JOB_ID.fullmatch(job_id):
            raise ValueError("unsafe job id")

        archive_directory = Path(archive_root)
        archive_directory.mkdir(parents=True, exist_ok=True)
        archive_directory = archive_directory.resolve(strict=True)
        target = archive_directory / f"{job_id}.zip"
        lock = self._archive_lock(archive_directory, job_id)

        with lock:
            if target.is_file() and not target.is_symlink():
                return target

            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{job_id}.", suffix=".tmp", dir=archive_directory
            )
            os.close(descriptor)
            temporary_path = Path(temporary_name)
            try:
                self._write_archive(output_root, temporary_path)
                os.replace(temporary_path, target)
            finally:
                temporary_path.unlink(missing_ok=True)
            return target

    def _archive_lock(self, archive_root: Path, job_id: str) -> Lock:
        key = (archive_root, job_id)
        with self._locks_guard:
            return self._archive_locks.setdefault(key, Lock())

    def _write_archive(self, output_root: Path, temporary_path: Path) -> None:
        with zipfile.ZipFile(
            temporary_path, mode="w", compression=zipfile.ZIP_DEFLATED
        ) as bundle:
            for relative_path, file_path in self._iter_safe_files(output_root):
                bundle.write(file_path, arcname=relative_path)

    @classmethod
    def _iter_safe_files(cls, output_root: Path) -> Iterator[tuple[str, Path]]:
        requested_root = Path(output_root)
        if requested_root.is_symlink():
            raise ValueError("output root cannot be a symlink")
        try:
            root = requested_root.resolve(strict=True)
        except FileNotFoundError as exc:
            raise FileNotFoundError(output_root) from exc
        if not root.is_dir():
            raise NotADirectoryError(output_root)

        safe_files: list[tuple[str, Path]] = []

        def visit(directory: Path) -> None:
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_path = Path(entry.path)
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        visit(entry_path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    if entry_path.is_symlink():
                        continue
                    try:
                        resolved = entry_path.resolve(strict=True)
                        relative = resolved.relative_to(root)
                    except (FileNotFoundError, ValueError):
                        continue
                    relative_text = relative.as_posix()
                    cls._validate_relative_path(relative_text)
                    safe_files.append((relative_text, resolved))

        visit(root)
        yield from sorted(safe_files, key=lambda item: item[0])

    @staticmethod
    def _validate_relative_path(relative_path: str) -> str:
        path = PurePosixPath(relative_path)
        if not relative_path or path.is_absolute() or ".." in path.parts:
            raise ValueError("unsafe relative path: absolute and parent paths are forbidden")
        if path.parts in ((), (".",)):
            raise ValueError("unsafe relative path: a file path is required")
        return path.as_posix()
