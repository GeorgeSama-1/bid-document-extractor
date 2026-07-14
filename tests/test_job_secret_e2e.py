from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from bid_knowledge.service.app import create_app
from bid_knowledge.service.jobs.files import JobFiles
from bid_knowledge.service.jobs.gpu import GpuInfo, GpuInventoryError
from bid_knowledge.service.jobs.manager import JobManager
from bid_knowledge.service.jobs.runner import RunResult, SubprocessJobRunner
from bid_knowledge.service.jobs.scheduler import GpuJobScheduler
from bid_knowledge.service.jobs.secrets import SecretStore
from bid_knowledge.service.jobs.store import JobStore


class Inventory:
    gpu = GpuInfo(id="6", name="Fake A100", total_mib=80_000, used_mib=0)

    def list(self):
        return [self.gpu]

    def require(self, gpu_id):
        if gpu_id != "6":
            raise GpuInventoryError("not available")
        return self.gpu


class Handle:
    def terminate(self, timeout_seconds=30):
        return None

    def wait(self):
        return 0


class CapturingRunner:
    def __init__(self, upload_root: Path) -> None:
        self.argv = None
        self.child_environment = None
        self.builder = SubprocessJobRunner(upload_root=upload_root)

    def run(self, job, api_key, callbacks):
        self.argv = self.builder._build_argv(job)
        self.child_environment = {"VLM_API_KEY": api_key, "CUDA_VISIBLE_DEVICES": job.gpu_id}
        callbacks.on_started(12345, Handle())
        callbacks.on_output(f"authenticated with {api_key}")
        callbacks.on_progress(1, 1, f"complete {api_key}")
        output = Path(job.output_dir)
        material = output / "modules" / "PDF" / "结果"
        material.mkdir(parents=True, exist_ok=True)
        (material / "material.md").write_text(
            "safe extracted result", encoding="utf-8"
        )
        return RunResult(exit_code=0, cancelled=False, error=None)


def test_api_key_exists_only_in_captured_child_environment(tmp_path: Path) -> None:
    sentinel = "UNIQUE_SENTINEL_API_KEY"
    service_data = tmp_path / "service_data"
    upload_root = service_data / "uploads"
    store = JobStore(service_data / "jobs.sqlite3")
    secrets = SecretStore()
    runner = CapturingRunner(upload_root)
    scheduler = GpuJobScheduler(store, secrets, runner, log_root=service_data / "logs")
    manager = JobManager(
        store=store,
        inventory=Inventory(),
        scheduler=scheduler,
        files=JobFiles(),
        secrets=secrets,
        upload_root=upload_root,
        output_root=tmp_path / "outputs",
        log_root=service_data / "logs",
        archive_root=service_data / "archives",
        max_upload_bytes=1024,
    )
    response_bodies: list[bytes] = []

    with TestClient(create_app(job_manager=manager)) as client:
        created = client.post(
            "/api/jobs",
            data={
                "gpu_id": "6",
                "vlm_endpoint": "https://vlm.test/v1",
                "vlm_model": "fake-model",
                "api_key": sentinel,
            },
            files={"pdf": ("bid.pdf", b"%PDF-1.7\nbody")},
        )
        response_bodies.append(created.content)
        assert created.status_code == 201
        job_id = created.json()["id"]
        deadline = time.monotonic() + 5
        while True:
            detail = client.get(f"/api/jobs/{job_id}")
            response_bodies.append(detail.content)
            if detail.json()["status"] == "succeeded":
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        for url in ("/api/jobs", f"/api/jobs/{job_id}/files"):
            response = client.get(url)
            response_bodies.append(response.content)
            assert response.status_code == 200
        archive_response = client.get(f"/api/jobs/{job_id}/archive")
        response_bodies.append(archive_response.content)
        assert archive_response.status_code == 200

    encoded = sentinel.encode()
    assert encoded not in store.database.read_bytes()
    assert encoded not in (service_data / "logs" / f"{job_id}.log").read_bytes()
    assert all(encoded not in body for body in response_bodies)
    assert runner.argv is not None
    assert sentinel not in "\n".join(runner.argv)
    assert runner.child_environment == {
        "VLM_API_KEY": sentinel,
        "CUDA_VISIBLE_DEVICES": "6",
    }
    with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
        for info in archive.infolist():
            assert sentinel not in info.filename
            assert encoded not in info.extra
            assert encoded not in info.comment
            assert encoded not in archive.read(info)
