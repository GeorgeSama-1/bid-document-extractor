from fastapi.testclient import TestClient

from bid_knowledge.service.app import create_app


class FakeJobManager:
    def start(self):
        pass

    def shutdown(self):
        pass

    def list_gpus(self):
        return []

    def list_jobs(self):
        return []


def test_static_jobs_and_existing_runs_smoke_without_gpu() -> None:
    with TestClient(create_app(job_manager=FakeJobManager())) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/jobs").status_code == 200
        assert client.get("/api/runs").status_code == 200
