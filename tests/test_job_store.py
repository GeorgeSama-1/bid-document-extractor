from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from pydantic import ValidationError

from bid_knowledge.service.jobs.models import JobParameters, JobRecord, JobStatus
from bid_knowledge.service.jobs.store import JobStore


def make_job(**overrides: object) -> JobRecord:
    values: dict[str, object] = {
        "filename": "商务文件.pdf",
        "gpu_id": "6",
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
    assert created.gpu_id == "6"
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


def test_competing_terminal_status_updates_use_compare_and_swap(
    tmp_path, monkeypatch
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    running = store.create(make_job())
    store.update_status(running.id, JobStatus.RUNNING)
    barrier = Barrier(2)
    original_to_record = JobStore._to_record

    def synchronize_after_read(row):
        record = original_to_record(row)
        if record.status is JobStatus.RUNNING:
            barrier.wait(timeout=5)
        return record

    monkeypatch.setattr(JobStore, "_to_record", staticmethod(synchronize_after_read))

    def finish(status: JobStatus):
        try:
            return store.update_status(running.id, status)
        except ValueError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(finish, (JobStatus.SUCCEEDED, JobStatus.FAILED))
        )

    successes = [result for result in outcomes if isinstance(result, JobRecord)]
    failures = [result for result in outcomes if isinstance(result, ValueError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert store.get(running.id).status is successes[0].status


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
    store.update_status(job.id, JobStatus.RUNNING)

    assert store.update_progress(job.id, step=3, total=7, stage="Detecting tables") is None

    updated = store.get(job.id)
    assert updated is not None
    assert (updated.progress_step, updated.progress_total, updated.progress_stage) == (
        3,
        7,
        "Detecting tables",
    )
    assert updated.updated_at >= job.updated_at


def test_update_progress_requires_running_job(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create(make_job())

    with pytest.raises(ValueError, match="running"):
        store.update_progress(job.id, step=1, total=2, stage="Parsing")


@pytest.mark.parametrize(("step", "total"), [(-1, 7), (8, 7), (0, -1)])
def test_update_progress_rejects_invalid_bounds(tmp_path, step, total) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create(make_job())
    store.update_status(job.id, JobStatus.RUNNING)

    with pytest.raises(ValueError, match="progress"):
        store.update_progress(job.id, step=step, total=total, stage="Parsing")


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


def test_job_parameters_and_record_are_immutable() -> None:
    job = make_job()

    with pytest.raises(ValidationError, match="frozen"):
        job.parameters.path_root = "changed"
    with pytest.raises(ValidationError, match="frozen"):
        job.parameters = JobParameters(
            path_root="replacement",
            vlm_endpoint="https://other.internal/v1",
            vlm_model="other-model",
        )

    assert job.parameters.path_root == "商务文件"
    assert job.parameters.vlm_model == "table-model"


def test_schema_has_version_and_text_gpu_column(tmp_path) -> None:
    database = tmp_path / "jobs.sqlite3"
    JobStore(database)

    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        columns = {
            row[1]: row[2] for row in connection.execute("PRAGMA table_info(jobs)")
        }

    assert version == 1
    assert columns["gpu_id"] == "TEXT"


def test_incompatible_existing_schema_is_rejected_clearly(tmp_path) -> None:
    database = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="Incompatible jobs database schema.*missing"):
        JobStore(database)
