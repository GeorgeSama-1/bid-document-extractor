from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from bid_knowledge.service.jobs.models import JobRecord
from bid_knowledge.service.jobs.secrets import redact_secret


_PROGRESS_LINE = re.compile(r"^\[(\d+)/(\d+)\]\s*(.*)$")


@dataclass(frozen=True)
class RunCallbacks:
    on_started: Callable[[int, "RunningProcess"], None]
    on_output: Callable[[str], None]
    on_progress: Callable[[int, int, str], None]


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    cancelled: bool
    error: str | None


class RunningProcess:
    def __init__(self, process: Any) -> None:
        self._process = process
        self._terminate_lock = Lock()
        self._cancel_requested = False

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested

    def terminate(self, timeout_seconds: float = 30) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds cannot be negative")
        with self._terminate_lock:
            self._cancel_requested = True
            if self._process.poll() is not None:
                self._process.wait()
                return
            try:
                os.killpg(self._process.pid, signal.SIGTERM)
            except ProcessLookupError:
                self._process.wait()
                return
            try:
                self._process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self._process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self._process.wait()

    def wait(self) -> int:
        return self._process.wait()


class SubprocessJobRunner:
    def __init__(
        self,
        upload_root: str | Path = Path("service_data/uploads"),
        python_executable: str = sys.executable,
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self._upload_root = Path(upload_root)
        self._python_executable = python_executable
        self._popen = popen

    def run(
        self,
        job: JobRecord,
        api_key: str,
        callbacks: RunCallbacks,
    ) -> RunResult:
        process: Any | None = None
        handle: RunningProcess | None = None
        try:
            process = self._popen(
                self._build_argv(job),
                env=self._build_environment(job, api_key),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            handle = RunningProcess(process)
            callbacks.on_started(process.pid, handle)
            if process.stdout is not None:
                for raw_line in process.stdout:
                    line = redact_secret(raw_line.rstrip("\r\n"), api_key)
                    callbacks.on_output(line)
                    match = _PROGRESS_LINE.fullmatch(line)
                    if match is not None:
                        callbacks.on_progress(
                            int(match.group(1)),
                            int(match.group(2)),
                            match.group(3),
                        )
            exit_code = process.wait()
            cancelled = handle.cancel_requested
            error = None
            if exit_code != 0 and not cancelled:
                error = f"Process exited with code {exit_code}"
            return RunResult(
                exit_code=exit_code,
                cancelled=cancelled,
                error=error,
            )
        except Exception as exc:
            if handle is not None and process is not None and process.poll() is None:
                try:
                    handle.terminate()
                except Exception:
                    pass
            exit_code = -1
            if process is not None and process.poll() is not None:
                exit_code = int(process.returncode)
            return RunResult(
                exit_code=exit_code,
                cancelled=bool(handle and handle.cancel_requested),
                error=redact_secret(str(exc), api_key),
            )

    def _build_argv(self, job: JobRecord) -> list[str]:
        parameters = job.parameters
        argv = [
            self._python_executable,
            "-m",
            "bid_knowledge.cli",
            "pdf-toc-pipeline",
            "--pdf",
            str(self._upload_root / job.id / "input.pdf"),
            "--out-dir",
            job.output_dir,
            "--path-root",
            parameters.path_root,
            "--enable-pp-structure",
            self._boolean(parameters.enable_pp_structure),
            "--pp-structure-device",
            parameters.pp_structure_device,
            "--pp-structure-use-doc-orientation-classify",
            self._boolean(parameters.pp_structure_use_doc_orientation_classify),
            "--pp-structure-use-doc-unwarping",
            self._boolean(parameters.pp_structure_use_doc_unwarping),
            "--pp-structure-use-textline-orientation",
            self._boolean(parameters.pp_structure_use_textline_orientation),
            "--enable-vlm-table",
            self._boolean(parameters.enable_vlm_table),
        ]
        if parameters.enable_vlm_table:
            argv.extend(
                [
                    "--vlm-table-endpoint",
                    parameters.vlm_endpoint,
                    "--vlm-table-model",
                    parameters.vlm_model,
                    "--vlm-table-api-key-env",
                    "VLM_API_KEY",
                    "--vlm-table-timeout",
                    str(parameters.vlm_timeout),
                    "--vlm-table-max-tokens",
                    str(parameters.vlm_max_tokens),
                    "--vlm-table-workers",
                    str(parameters.vlm_workers),
                ]
            )
        argv.extend(["--progress", "true"])
        return argv

    @staticmethod
    def _build_environment(job: JobRecord, api_key: str) -> dict[str, str]:
        environment = os.environ.copy()
        environment["CUDA_VISIBLE_DEVICES"] = job.gpu_id
        environment["VLM_API_KEY"] = api_key
        return environment

    @staticmethod
    def _boolean(value: bool) -> str:
        return "true" if value else "false"
