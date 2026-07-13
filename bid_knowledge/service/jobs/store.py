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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    gpu_id INTEGER NOT NULL,
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
            self._execute_update(connection, job_id, updates)
            updated_row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()

        return self._to_record(updated_row)

    def update_progress(self, job_id: str, step: int, total: int, stage: str) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET progress_step = ?, progress_total = ?, progress_stage = ?, updated_at = ?
                WHERE id = ?
                """,
                (step, total, stage, utc_now().isoformat(), job_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(job_id)

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
        updates: dict[str, Any],
    ) -> None:
        serialized = {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in updates.items()
        }
        assignments = ", ".join(f"{key} = ?" for key in serialized)
        connection.execute(
            f"UPDATE jobs SET {assignments} WHERE id = ?",
            (*serialized.values(), job_id),
        )

    @staticmethod
    def _serialize_datetime(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _to_record(row: sqlite3.Row) -> JobRecord:
        values = dict(row)
        values["parameters"] = json.loads(values["parameters"])
        return JobRecord.model_validate(values)
