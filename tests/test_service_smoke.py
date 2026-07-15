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
        index = client.get("/")
        styles = client.get("/jobs.css?v=test")
        script = client.get("/jobs.js?v=test")
        assert index.status_code == 200
        assert index.headers["content-type"].startswith("text/html")
        assert styles.status_code == 200
        assert styles.headers["content-type"].startswith("text/css")
        assert script.status_code == 200
        assert script.headers["content-type"].startswith("text/javascript")
        for response in (index, styles, script):
            assert response.headers["cache-control"] == (
                "no-cache, max-age=0, must-revalidate"
            )
        assert client.get("/api/jobs").status_code == 200
        assert client.get("/api/runs").status_code == 200
