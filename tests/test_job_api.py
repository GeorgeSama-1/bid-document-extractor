from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from bid_knowledge.service.app import create_app
from bid_knowledge.service.jobs.gpu import GpuInfo
from bid_knowledge.service.jobs.manager import (
    JobConflictError,
    JobNotFoundError,
    JobProgress,
    JobValidationError,
    JobView,
)
from bid_knowledge.service.jobs.models import JobParameters, JobStatus


def job_view(status: JobStatus = JobStatus.QUEUED) -> JobView:
    now = datetime.now(timezone.utc)
    return JobView(
        id="job-1",
        status=status,
        queue_position=1 if status is JobStatus.QUEUED else None,
        gpu_id="6",
        original_filename="bid.pdf",
        parameters=JobParameters(
            vlm_endpoint="https://vlm.test/v1",
            vlm_model="model",
        ),
        progress=JobProgress(step=0, total=0, stage=""),
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        error=None,
        logs=[],
        run_name="job_job-1" if status is JobStatus.SUCCEEDED else None,
    )


class FakeManager:
    def __init__(self, tmp_path: Path) -> None:
        self.view = job_view()
        self.created = None
        self.file = tmp_path / "result.txt"
        self.file.write_text("safe result", encoding="utf-8")
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def shutdown(self):
        self.stopped += 1

    def list_gpus(self):
        return [GpuInfo(id="6", name="A100", total_mib=80, used_mib=4)]

    def create_job(self, upload, filename, form, api_key):
        body = upload.read()
        if not body.startswith(b"%PDF"):
            raise JobValidationError(f"invalid PDF {api_key}")
        self.created = (filename, dict(form), api_key)
        return self.view

    def list_jobs(self):
        return [self.view]

    def get_job(self, job_id):
        if job_id != self.view.id:
            raise JobNotFoundError(job_id)
        return self.view

    def cancel(self, job_id):
        self.get_job(job_id)
        if self.view.status is JobStatus.SUCCEEDED:
            raise JobConflictError("terminal")
        return self.view

    def delete(self, job_id):
        self.get_job(job_id)
        return job_id

    def clear_history(self):
        return {"deleted": [self.view.id], "active": []}

    def list_files(self, job_id):
        self.get_job(job_id)
        return [{"path": "nested/result.txt", "size_bytes": 11}]

    def open_file(self, job_id, relative_path):
        self.get_job(job_id)
        if relative_path != "nested/result.txt":
            raise FileNotFoundError(relative_path)
        return self.file.open("rb")

    def archive(self, job_id):
        self.get_job(job_id)
        if self.view.status is not JobStatus.SUCCEEDED:
            raise JobConflictError("not successful")
        return self.file


def test_job_routes_and_manual_secret_separation(tmp_path: Path) -> None:
    manager = FakeManager(tmp_path)
    with TestClient(create_app(job_manager=manager)) as client:
        assert client.get("/api/system/gpus").json()["gpus"][0]["id"] == "6"
        created = client.post(
            "/api/jobs",
            data={
                "gpu_id": "6",
                "vlm_endpoint": "https://vlm.test/v1",
                "vlm_model": "model",
                "api_key": "sentinel-key",
            },
            files={"pdf": ("bid.pdf", b"%PDF-1.7", "application/pdf")},
        )
        assert created.status_code == 201, created.text
        assert "sentinel-key" not in created.text
        assert manager.created is not None
        assert manager.created[1].get("api_key") is None
        assert manager.created[2] == "sentinel-key"
        assert client.get("/api/jobs").json()["jobs"][0]["id"] == "job-1"
        assert client.get("/api/jobs/job-1").status_code == 200
        assert client.post("/api/jobs/job-1/cancel").status_code == 200
        assert client.delete("/api/jobs/job-1").json() == {"deleted": "job-1"}
        assert client.delete("/api/jobs").json() == {"deleted": ["job-1"], "active": []}
        assert client.get("/api/jobs/job-1/files").status_code == 200
        download = client.get("/api/jobs/job-1/files/nested/result.txt")
        assert download.content == b"safe result"

    assert manager.started == 1
    assert manager.stopped == 1


def test_create_errors_never_echo_key_and_status_mapping(tmp_path: Path) -> None:
    manager = FakeManager(tmp_path)
    with TestClient(create_app(job_manager=manager)) as client:
        response = client.post(
            "/api/jobs",
            data={"api_key": "sentinel-key"},
            files={"pdf": ("x.pdf", b"bad")},
        )
        assert response.status_code == 400, response.text
        assert "sentinel-key" not in response.text
        assert client.get("/api/jobs/missing").status_code == 404
        assert client.get("/api/jobs/job-1/files/../secret").status_code in {400, 404}
        assert client.get("/api/jobs/job-1/archive").status_code == 409


def test_file_download_uses_open_handle_even_if_path_is_swapped(tmp_path: Path) -> None:
    manager = FakeManager(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("outside secret", encoding="utf-8")
    original_open = manager.open_file

    def open_then_swap(job_id, relative_path):
        opened = original_open(job_id, relative_path)
        manager.file.unlink()
        manager.file.symlink_to(outside)
        return opened

    manager.open_file = open_then_swap
    with TestClient(create_app(job_manager=manager)) as client:
        response = client.get("/api/jobs/job-1/files/nested/result.txt")
    assert response.content == b"safe result"
    assert b"outside secret" not in response.content
