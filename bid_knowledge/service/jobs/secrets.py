from __future__ import annotations

from collections.abc import Callable
from threading import Lock


REDACTED = "[REDACTED]"


def redact_secret(text: str, secret: str) -> str:
    """Replace exact occurrences of a non-empty secret in text."""
    if not secret:
        return text
    return text.replace(secret, REDACTED)


class SecretStore:
    """A process-local, lock-protected API key store."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._secrets: dict[str, str] = {}

    def put(self, job_id: str, api_key: str) -> None:
        with self._lock:
            self._secrets[job_id] = api_key

    def get(self, job_id: str) -> str | None:
        with self._lock:
            return self._secrets.get(job_id)

    def delete(self, job_id: str) -> str | None:
        with self._lock:
            return self._secrets.pop(job_id, None)

    def clear(self) -> None:
        with self._lock:
            self._secrets.clear()

    def redact(self, job_id: str, text: str) -> str:
        with self._lock:
            secret = self._secrets.get(job_id, "")
        return redact_secret(text, secret)

    def redactor(self, job_id: str) -> Callable[[str], str]:
        with self._lock:
            secret = self._secrets.get(job_id, "")
        return lambda text: redact_secret(text, secret)
