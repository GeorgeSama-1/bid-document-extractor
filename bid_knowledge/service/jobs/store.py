from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from bid_knowledge.service.jobs.models import JobRecord, JobStatus, utc_now


_TERMINAL_STATUSES = {
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
}

_ALLOWED_TRANSITIONS = {
    JobStatus.QUEUED: {
        JobStatus.RUNNING,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.RUNNING: _TERMINAL_STATUSES,
    JobStatus.SUCCEEDED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
}

_UPDATE_FIELDS = {
    "pid",
    "exit_code",
    "error",
    "progress_step",
    "progress_total",
    "progress_stage",
}

_SCHEMA_VERSION = 1

_REQUIRED_COLUMNS = {
    "id": "TEXT",
    "filename": "TEXT",
    "gpu_id": "TEXT",
    "run_name": "TEXT",
    "output_dir": "TEXT",
    "parameters": "TEXT",
    "status": "TEXT",
    "progress_step": "INTEGER",
    "progress_total": "INTEGER",
    "progress_stage": "TEXT",
    "created_at": "TEXT",
    "updated_at": "TEXT",
    "started_at": "TEXT",
    "finished_at": "TEXT",
    "pid": "INTEGER",
    "exit_code": "INTEGER",
    "error": "TEXT",
}


class JobStore:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        with self._connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, _SCHEMA_VERSION):
                raise RuntimeError(
                    f"Unsupported jobs database schema version {version}; "
                    f"expected {_SCHEMA_VERSION}"
                )

            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
            ).fetchone()
            if table_exists is None:
                connection.execute(
                    """
                    CREATE TABLE jobs (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    gpu_id TEXT NOT NULL,
                    run_name TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    parameters TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress_step INTEGER NOT NULL,
                    progress_total INTEGER NOT NULL,
                    progress_stage TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    pid INTEGER,
                    exit_code INTEGER,
                    error TEXT
                )
                    """
                )

            self._validate_schema(connection)
            if version == 0:
                connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]: str(row["type"]).upper()
            for row in connection.execute("PRAGMA table_info(jobs)")
        }
        missing = _REQUIRED_COLUMNS.keys() - columns.keys()
        if missing:
            names = ", ".join(sorted(missing))
            raise RuntimeError(
                f"Incompatible jobs database schema: missing required columns: {names}"
            )

        wrong_types = {
            name: (columns[name], expected)
            for name, expected in _REQUIRED_COLUMNS.items()
            if columns[name] != expected
        }
        if wrong_types:
            descriptions = ", ".join(
                f"{name} is {actual or '<none>'}, expected {expected}"
                for name, (actual, expected) in sorted(wrong_types.items())
            )
            raise RuntimeError(
                f"Incompatible jobs database schema: column type mismatch: {descriptions}"
            )

    def create(self, job: JobRecord) -> JobRecord:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id, filename, gpu_id, run_name, output_dir, parameters,
                    status, progress_step, progress_total, progress_stage,
                    created_at, updated_at, started_at, finished_at, pid,
                    exit_code, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.filename,
                    job.gpu_id,
                    job.run_name,
                    job.output_dir,
                    json.dumps(job.parameters.model_dump(mode="json"), ensure_ascii=False),
                    job.status.value,
                    job.progress_step,
                    job.progress_total,
                    job.progress_stage,
                    self._serialize_datetime(job.created_at),
                    self._serialize_datetime(job.updated_at),
                    self._serialize_datetime(job.started_at),
                    self._serialize_datetime(job.finished_at),
                    job.pid,
                    job.exit_code,
                    job.error,
                ),
            )
        return job

    def get(self, job_id: str) -> JobRecord | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._to_record(row) if row is not None else None

    def list(self) -> list[JobRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [self._to_record(row) for row in rows]

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        **fields: Any,
    ) -> JobRecord:
        status = JobStatus(status)
        unknown_fields = fields.keys() - _UPDATE_FIELDS
        if unknown_fields:
            names = ", ".join(sorted(unknown_fields))
            raise ValueError(f"Unsupported job update fields: {names}")

        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            current = self._to_record(row)
            if status not in _ALLOWED_TRANSITIONS[current.status]:
                raise ValueError(
                    f"Invalid status transition: {current.status.value} -> {status.value}"
                )

            now = utc_now()
            updates: dict[str, Any] = {**fields, "status": status.value, "updated_at": now}
            if status is JobStatus.RUNNING and current.started_at is None:
                updates["started_at"] = now
            if status in _TERMINAL_STATUSES:
                updates["finished_at"] = now
            updated_count = self._execute_update(
                connection,
                job_id,
                expected_status=current.status,
                updates=updates,
            )
            if updated_count != 1:
                raise ValueError(
                    f"Job status transition lost a concurrent update: "
                    f"expected {current.status.value}"
                )
            updated_row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()

        return self._to_record(updated_row)

    def update_progress(self, job_id: str, step: int, total: int, stage: str) -> None:
        if total < 0 or step < 0 or step > total:
            raise ValueError(
                f"Invalid progress: require total >= 0 and 0 <= step <= total; "
                f"got step={step}, total={total}"
            )
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET progress_step = ?, progress_total = ?, progress_stage = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    step,
                    total,
                    stage,
                    utc_now().isoformat(),
                    job_id,
                    JobStatus.RUNNING.value,
                ),
            )
            if cursor.rowcount == 0:
                exists = connection.execute(
                    "SELECT 1 FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
                if exists is None:
                    raise KeyError(job_id)
                raise ValueError("Progress can only be updated while job is running")

    def mark_interrupted(self) -> int:
        now = utc_now().isoformat()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = ?, error = ?, finished_at = ?, updated_at = ?
                WHERE status IN (?, ?)
                """,
                (
                    JobStatus.FAILED.value,
                    "Service interrupted before job completion",
                    now,
                    now,
                    JobStatus.QUEUED.value,
                    JobStatus.RUNNING.value,
                ),
            )
            return cursor.rowcount

    @staticmethod
    def _execute_update(
        connection: sqlite3.Connection,
        job_id: str,
        expected_status: JobStatus,
        updates: dict[str, Any],
    ) -> int:
        serialized = {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in updates.items()
        }
        assignments = ", ".join(f"{key} = ?" for key in serialized)
        cursor = connection.execute(
            f"UPDATE jobs SET {assignments} WHERE id = ? AND status = ?",
            (*serialized.values(), job_id, expected_status.value),
        )
        return cursor.rowcount

    @staticmethod
    def _serialize_datetime(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _to_record(row: sqlite3.Row) -> JobRecord:
        values = dict(row)
        values["parameters"] = json.loads(values["parameters"])
        return JobRecord.model_validate(values)
