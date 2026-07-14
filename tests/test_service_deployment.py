from pathlib import Path


def test_launcher_uses_environment_and_exactly_one_worker(monkeypatch) -> None:
    from scripts import run_service

    captured = {}
    monkeypatch.setenv("BID_SERVICE_HOST", "127.0.0.2")
    monkeypatch.setenv("BID_SERVICE_PORT", "8123")
    monkeypatch.setattr(run_service.uvicorn, "run", lambda *a, **kw: captured.update(kw))
    run_service.main()
    assert captured == {"host": "127.0.0.2", "port": 8123, "workers": 1}


def test_deployment_examples_are_single_instance_and_secret_free() -> None:
    env = Path("deploy/bid-document-extractor.env.example").read_text()
    assert "BID_SERVICE_HOST=0.0.0.0" in env
    assert "BID_SERVICE_PORT=8000" in env
    assert "BID_SERVICE_MAX_UPLOAD_BYTES=524288000" in env
    assert "BID_SERVICE_MAX_VLM_WORKERS=128" in env
    unit = Path("deploy/bid-document-extractor.service.example").read_text()
    for value in (
        "EnvironmentFile=/etc/bid-document-extractor.env",
        "WorkingDirectory=/ABSOLUTE/PATH/TO/bid_source/bid-document-extractor",
        "ExecStart=/ABSOLUTE/PATH/TO/GPU_ENV/bin/python -m scripts.run_service",
        "KillMode=control-group",
        "TimeoutStopSec=45",
        "Restart=on-failure",
    ):
        assert value in unit
    assert "workers" not in unit.lower()
    assert "api_key" not in (env + unit).lower()


def test_server_install_documents_activation_and_trusted_network() -> None:
    guide = Path("deploy/SERVER_INSTALL.md").read_text(encoding="utf-8")
    assert "http://172.20.0.160:8000" in guide
    assert "可信内网" in guide
    assert "systemctl enable --now bid-document-extractor.service" in guide
    assert "journalctl -u bid-document-extractor.service -f" in guide
