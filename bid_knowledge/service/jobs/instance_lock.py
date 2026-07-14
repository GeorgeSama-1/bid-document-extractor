from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import IO


class ServiceAlreadyRunningError(RuntimeError):
    """Raised when another service process owns the instance lock."""


class InstanceLock:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._file: IO[str] | None = None

    def acquire(self) -> None:
        if self._file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        opened = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(opened.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            opened.seek(0)
            opened.truncate()
            opened.write(f"{os.getpid()}\n")
            opened.flush()
        except BlockingIOError as exc:
            opened.close()
            raise ServiceAlreadyRunningError(
                f"Another service instance owns {self.path}"
            ) from exc
        except BaseException:
            opened.close()
            raise
        self._file = opened

    def release(self) -> None:
        opened, self._file = self._file, None
        if opened is None:
            return
        try:
            fcntl.flock(opened.fileno(), fcntl.LOCK_UN)
        finally:
            opened.close()

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


__all__ = ["InstanceLock", "ServiceAlreadyRunningError"]
