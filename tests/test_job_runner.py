from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from bid_knowledge.service.jobs.models import JobParameters, JobRecord
from bid_knowledge.service.jobs.runner import (
    RunCallbacks,
    RunningProcess,
    SubprocessJobRunner,
)
from bid_knowledge.service.jobs.secrets import SecretStore, redact_secret


def make_job(tmp_path: Path, **overrides: object) -> JobRecord:
    values: dict[str, object] = {
        "id": "job-123",
        "filename": "商务文件.pdf",
        "gpu_id": "6",
        "run_name": "job_job-123",
        "output_dir": str(tmp_path / "outputs" / "job_job-123"),
        "parameters": JobParameters(
            path_root="商务文件",
            pp_structure_use_doc_orientation_classify=False,
            pp_structure_use_doc_unwarping=True,
            pp_structure_use_textline_orientation=False,
            vlm_endpoint="https://vlm.internal/v1",
            vlm_model="table-model",
            vlm_timeout=1700,
            vlm_max_tokens=4096,
            vlm_workers=24,
        ),
    }
    values.update(overrides)
    return JobRecord(**values)


class FakePopen:
    next_pid = 4321

    def __init__(self, argv, **kwargs) -> None:
        self.argv = argv
        self.kwargs = kwargs
        self.pid = self.next_pid
        self.stdout = iter(
            [
                "ordinary output\n",
                "[3/7] Detecting tables\n",
                "credential=sentinel-key\n",
            ]
        )
        self.returncode = 0
        self.wait_calls: list[float | None] = []

    def wait(self, timeout=None) -> int:
        self.wait_calls.append(timeout)
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode


def test_secret_store_is_locked_mapping_with_exact_nonempty_redaction() -> None:
    secrets = SecretStore()

    secrets.put("job", "abc.123")

    assert secrets.get("job") == "abc.123"
    assert secrets.redact("job", "x abc.123 abcX123 abc.123") == (
        "x [REDACTED] abcX123 [REDACTED]"
    )
    assert redact_secret("unchanged", "") == "unchanged"
    assert secrets.delete("job") == "abc.123"
    assert secrets.get("job") is None
    secrets.put("a", "one")
    secrets.put("b", "two")
    secrets.clear()
    assert secrets.get("a") is None
    assert secrets.get("b") is None


def test_runner_builds_complete_cli_and_keeps_key_only_in_child_environment(
    tmp_path, monkeypatch
) -> None:
    calls: list[FakePopen] = []

    def popen(argv, **kwargs):
        process = FakePopen(argv, **kwargs)
        calls.append(process)
        return process

    inherited_environment = {"PATH": os.environ.get("PATH", "")}
    monkeypatch.setattr(os, "environ", inherited_environment)
    runner = SubprocessJobRunner(
        upload_root=tmp_path / "uploads",
        python_executable="/venv/bin/python",
        popen=popen,
    )
    started = []
    output: list[str] = []
    progress = []

    result = runner.run(
        make_job(tmp_path),
        api_key="sentinel-key",
        callbacks=RunCallbacks(
            on_started=lambda pid, handle: started.append((pid, handle)),
            on_output=output.append,
            on_progress=lambda step, total, stage: progress.append(
                (step, total, stage)
            ),
        ),
    )

    assert result.exit_code == 0
    assert result.cancelled is False
    assert result.error is None
    assert len(calls) == 1
    process = calls[0]
    assert process.argv == [
        "/venv/bin/python",
        "-m",
        "bid_knowledge.cli",
        "pdf-toc-pipeline",
        "--pdf",
        str(tmp_path / "uploads" / "job-123" / "input.pdf"),
        "--out-dir",
        str(tmp_path / "outputs" / "job_job-123"),
        "--path-root",
        "商务文件",
        "--enable-pp-structure",
        "true",
        "--pp-structure-device",
        "gpu",
        "--pp-structure-use-doc-orientation-classify",
        "false",
        "--pp-structure-use-doc-unwarping",
        "true",
        "--pp-structure-use-textline-orientation",
        "false",
        "--enable-vlm-table",
        "true",
        "--vlm-table-endpoint",
        "https://vlm.internal/v1",
        "--vlm-table-model",
        "table-model",
        "--vlm-table-api-key-env",
        "VLM_API_KEY",
        "--vlm-table-timeout",
        "1700",
        "--vlm-table-max-tokens",
        "4096",
        "--vlm-table-workers",
        "24",
        "--progress",
        "true",
    ]
    assert "sentinel-key" not in " ".join(process.argv)
    assert process.kwargs["env"] == {
        "PATH": inherited_environment["PATH"],
        "CUDA_VISIBLE_DEVICES": "6",
        "VLM_API_KEY": "sentinel-key",
    }
    assert process.kwargs["stdout"] is subprocess.PIPE
    assert process.kwargs["stderr"] is subprocess.STDOUT
    assert process.kwargs["text"] is True
    assert process.kwargs["start_new_session"] is True
    assert process.kwargs["bufsize"] == 1
    assert started[0][0] == 4321
    assert isinstance(started[0][1], RunningProcess)
    assert output == [
        "ordinary output",
        "[3/7] Detecting tables",
        "credential=[REDACTED]",
    ]
    assert progress == [(3, 7, "Detecting tables")]


def test_runner_can_disable_optional_engines_and_select_cpu(tmp_path: Path) -> None:
    job = make_job(
        tmp_path,
        parameters=JobParameters(
            path_root="PDF",
            enable_pp_structure=False,
            pp_structure_device="cpu",
            enable_vlm_table=False,
        ),
    )

    argv = SubprocessJobRunner(python_executable="python")._build_argv(job)

    assert argv[argv.index("--enable-pp-structure") + 1] == "false"
    assert argv[argv.index("--pp-structure-device") + 1] == "cpu"
    assert argv[argv.index("--enable-vlm-table") + 1] == "false"
    assert not any(value.startswith("--vlm-table-") for value in argv)
    assert argv[-2:] == ["--progress", "true"]


def test_runner_redacts_spawn_exception_and_reports_failure(tmp_path) -> None:
    def fail_to_spawn(*_args, **_kwargs):
        raise OSError("could not use sentinel-key")

    output: list[str] = []
    result = SubprocessJobRunner(popen=fail_to_spawn).run(
        make_job(tmp_path),
        api_key="sentinel-key",
        callbacks=RunCallbacks(
            on_started=lambda _pid, _handle: None,
            on_output=output.append,
            on_progress=lambda _step, _total, _stage: None,
        ),
    )

    assert result.exit_code == -1
    assert result.cancelled is False
    assert result.error == "could not use [REDACTED]"
    assert output == []


def test_runner_reports_nonzero_exit_without_inventing_cancellation(tmp_path) -> None:
    class FailedPopen(FakePopen):
        def __init__(self, argv, **kwargs) -> None:
            super().__init__(argv, **kwargs)
            self.stdout = iter(["pipeline failed\n"])
            self.returncode = 9

    result = SubprocessJobRunner(popen=FailedPopen).run(
        make_job(tmp_path),
        api_key="",
        callbacks=RunCallbacks(
            on_started=lambda _pid, _handle: None,
            on_output=lambda _line: None,
            on_progress=lambda _step, _total, _stage: None,
        ),
    )

    assert result.exit_code == 9
    assert result.cancelled is False
    assert result.error == "Process exited with code 9"


def test_running_process_terminates_process_group_then_escalates(
    monkeypatch,
) -> None:
    signals: list[tuple[int, signal.Signals]] = []

    class StubbornProcess:
        pid = 9876
        returncode = None

        def __init__(self) -> None:
            self.killed = False
            self.wait_calls: list[float | None] = []

        def poll(self):
            return None if not self.killed else -signal.SIGKILL

        def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            if not self.killed:
                raise subprocess.TimeoutExpired("worker", timeout)
            self.returncode = -signal.SIGKILL
            return self.returncode

    process = StubbornProcess()

    def killpg(pid, sent_signal):
        signals.append((pid, sent_signal))
        if sent_signal == signal.SIGKILL:
            process.killed = True

    monkeypatch.setattr(os, "killpg", killpg)
    handle = RunningProcess(process)

    handle.terminate(timeout_seconds=0.01)

    assert signals == [
        (9876, signal.SIGTERM),
        (9876, signal.SIGKILL),
    ]
    assert process.wait_calls == [0.01, None]
    assert handle.cancel_requested is True
    assert handle.wait() == -signal.SIGKILL


def test_running_process_terminates_real_short_process_group() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    handle = RunningProcess(process)
    try:
        handle.terminate(timeout_seconds=5)
        assert handle.wait() == -signal.SIGTERM
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
