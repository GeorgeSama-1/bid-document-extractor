from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from bid_knowledge.service.jobs.models import JobParameters, JobRecord, JobStatus
from bid_knowledge.service.jobs.store import JobStore


def make_job(**overrides: object) -> JobRecord:
    values: dict[str, object] = {
        "filename": "商务文件.pdf",
        "gpu_id": 6,
        "run_name": "business-v1",
        "output_dir": "/srv/bid/outputs/business-v1",
        "parameters": JobParameters(
            path_root="商务文件",
            vlm_endpoint="https://vlm.internal/v1",
            vlm_model="table-model",
        ),
    }
    values.update(overrides)
    return JobRecord(**values)


def test_create_and_get_round_trip(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = make_job()

    created = store.create(job)

    assert created == job
    assert store.get(job.id) == job
    assert store.get("missing") is None


def test_list_returns_newest_jobs_first(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    older_time = datetime(2026, 7, 13, 10, tzinfo=UTC)
    newer_time = older_time + timedelta(minutes=1)
    older = make_job(id="older", created_at=older_time, updated_at=older_time)
    newer = make_job(id="newer", created_at=newer_time, updated_at=newer_time)
    store.create(older)
    store.create(newer)

    assert [job.id for job in store.list()] == ["newer", "older"]


def test_legal_status_transitions_set_lifecycle_fields(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create(make_job())

    running = store.update_status(job.id, JobStatus.RUNNING, pid=4321)
    succeeded = store.update_status(job.id, JobStatus.SUCCEEDED, exit_code=0)

    assert running.status is JobStatus.RUNNING
    assert running.pid == 4321
    assert running.started_at is not None
    assert succeeded.status is JobStatus.SUCCEEDED
    assert succeeded.exit_code == 0
    assert succeeded.finished_at is not None
    assert succeeded.updated_at >= running.updated_at


@pytest.mark.parametrize(
    ("initial", "target"),
    [
        (JobStatus.QUEUED, JobStatus.SUCCEEDED),
        (JobStatus.RUNNING, JobStatus.QUEUED),
        (JobStatus.SUCCEEDED, JobStatus.RUNNING),
        (JobStatus.FAILED, JobStatus.RUNNING),
        (JobStatus.CANCELLED, JobStatus.RUNNING),
    ],
)
def test_illegal_status_transitions_are_rejected(tmp_path, initial, target) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = make_job(status=initial)
    store.create(job)

    with pytest.raises(ValueError, match="status transition"):
        store.update_status(job.id, target)


def test_update_progress_persists_step_total_and_stage(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create(make_job())

    assert store.update_progress(job.id, step=3, total=7, stage="Detecting tables") is None

    updated = store.get(job.id)
    assert updated is not None
    assert (updated.progress_step, updated.progress_total, updated.progress_stage) == (
        3,
        7,
        "Detecting tables",
    )
    assert updated.updated_at >= job.updated_at


def test_mark_interrupted_fails_queued_and_running_jobs(tmp_path) -> None:
    database = tmp_path / "jobs.sqlite3"
    first_store = JobStore(database)
    queued = first_store.create(make_job(id="queued"))
    running = first_store.create(make_job(id="running"))
    first_store.update_status(running.id, JobStatus.RUNNING, pid=999)
    first_store.create(make_job(id="done", status=JobStatus.SUCCEEDED))

    restarted_store = JobStore(database)
    assert restarted_store.mark_interrupted() == 2

    for job_id in (queued.id, running.id):
        interrupted = restarted_store.get(job_id)
        assert interrupted is not None
        assert interrupted.status is JobStatus.FAILED
        assert interrupted.finished_at is not None
        assert "interrupted" in (interrupted.error or "").lower()
    assert restarted_store.get("done").status is JobStatus.SUCCEEDED


def test_api_key_is_rejected_and_never_persisted(tmp_path) -> None:
    secret = "sentinel-secret"
    payload = {
        "path_root": "商务文件",
        "vlm_endpoint": "https://vlm.internal/v1",
        "vlm_model": "table-model",
        "api_key": secret,
    }

    with pytest.raises(ValidationError):
        JobParameters.model_validate(payload)

    schema = JobParameters.model_json_schema()
    assert "api_key" not in schema.get("properties", {})
    assert "api_key_env" not in schema.get("properties", {})

    database = tmp_path / "jobs.sqlite3"
    store = JobStore(database)
    store.create(make_job())
    with sqlite3.connect(database) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(jobs)")]
        stored = connection.execute("SELECT * FROM jobs").fetchone()

    assert "api_key" not in columns
    assert "api_key_env" not in columns
    assert secret not in database.read_bytes().decode("utf-8", errors="ignore")
    assert secret not in repr(stored)
