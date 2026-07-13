from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import BinaryIO

from pydantic import BaseModel, ConfigDict


_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_INVALID_PORTABLE_CHARACTERS = frozenset('<>:"\\|?*')
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


class OutputFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    size_bytes: int


@dataclass
class _ArchiveLockState:
    lock: Lock = field(default_factory=Lock)
    users: int = 0


class JobFiles:
    def __init__(self) -> None:
        self._locks_guard = Lock()
        self._archive_locks: dict[tuple[Path, str], _ArchiveLockState] = {}

    def list(self, output_root: Path) -> list[OutputFile]:
        files: list[OutputFile] = []
        for relative_path, _file_path in self._iter_safe_files(output_root):
            try:
                with self.open_file(output_root, relative_path) as opened:
                    size_bytes = os.fstat(opened.fileno()).st_size
            except OSError:
                continue
            files.append(OutputFile(path=relative_path, size_bytes=size_bytes))
        return files

    def resolve(self, output_root: Path, relative_path: str) -> Path:
        """Resolve a display path; security-sensitive readers must use open_file()."""
        requested = self._validate_relative_path(relative_path)
        for safe_relative, file_path in self._iter_safe_files(output_root):
            if safe_relative == requested:
                return file_path
        raise FileNotFoundError(relative_path)

    def open_file(self, output_root: Path, relative_path: str) -> BinaryIO:
        """Open a regular output file without following symlinks at any level."""
        requested = self._validate_relative_path(relative_path)
        components = PurePosixPath(requested).parts
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        directory_fd = os.open(Path(output_root), directory_flags)
        file_fd: int | None = None
        try:
            for component in components[:-1]:
                next_directory_fd = os.open(
                    component,
                    directory_flags,
                    dir_fd=directory_fd,
                )
                os.close(directory_fd)
                directory_fd = next_directory_fd

            file_fd = os.open(
                components[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise FileNotFoundError(relative_path)
            opened = os.fdopen(file_fd, mode="rb", closefd=True)
            file_fd = None
            return opened
        finally:
            if file_fd is not None:
                os.close(file_fd)
            os.close(directory_fd)

    def archive(
        self,
        job_id: str,
        output_root: Path,
        archive_root: Path,
    ) -> Path:
        if not _SAFE_JOB_ID.fullmatch(job_id) or self._is_windows_device_name(job_id):
            raise ValueError("unsafe job id")

        archive_directory = Path(archive_root)
        archive_directory.mkdir(parents=True, exist_ok=True)
        archive_directory = archive_directory.resolve(strict=True)
        target = archive_directory / f"{job_id}.zip"
        with self._archive_lock(archive_directory, job_id):
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

    @contextmanager
    def _archive_lock(self, archive_root: Path, job_id: str) -> Iterator[None]:
        key = (archive_root, job_id)
        with self._locks_guard:
            state = self._archive_locks.setdefault(key, _ArchiveLockState())
            state.users += 1
        try:
            state.lock.acquire()
        except BaseException:
            self._release_archive_lock_reference(key, state)
            raise
        try:
            yield
        finally:
            state.lock.release()
            self._release_archive_lock_reference(key, state)

    def _release_archive_lock_reference(
        self,
        key: tuple[Path, str],
        state: _ArchiveLockState,
    ) -> None:
        with self._locks_guard:
            state.users -= 1
            if state.users == 0 and self._archive_locks.get(key) is state:
                del self._archive_locks[key]

    def _write_archive(self, output_root: Path, temporary_path: Path) -> None:
        with zipfile.ZipFile(
            temporary_path, mode="w", compression=zipfile.ZIP_DEFLATED
        ) as bundle:
            for relative_path, _file_path in self._iter_safe_files(output_root):
                try:
                    source = self.open_file(output_root, relative_path)
                except OSError:
                    continue
                with source, bundle.open(relative_path, mode="w") as destination:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)

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
                    try:
                        cls._validate_relative_path(relative_text)
                    except ValueError:
                        continue
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
        for component in path.parts:
            if (
                component.endswith((".", " "))
                or any(
                    character in _INVALID_PORTABLE_CHARACTERS or ord(character) < 32
                    for character in component
                )
                or JobFiles._is_windows_device_name(component)
            ):
                raise ValueError("unsafe relative path: non-portable file name")
        return path.as_posix()

    @staticmethod
    def _is_windows_device_name(component: str) -> bool:
        return component.split(".", maxsplit=1)[0].upper() in _WINDOWS_DEVICE_NAMES
