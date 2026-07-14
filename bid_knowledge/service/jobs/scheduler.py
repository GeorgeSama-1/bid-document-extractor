from __future__ import annotations

import queue
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Condition, Thread
from typing import Any, Literal

from bid_knowledge.service.jobs.models import JobRecord, JobStatus
from bid_knowledge.service.jobs.runner import RunCallbacks, RunResult
from bid_knowledge.service.jobs.secrets import SecretStore, redact_secret
from bid_knowledge.service.jobs.store import JobStore


_STOP = object()
_EntryState = Literal["queued", "running", "terminal"]


@dataclass
class _JobEntry:
    job: JobRecord
    state: _EntryState = "queued"
    handle: Any | None = None
    cancel_requested: bool = False
    cancel_attempt_done: bool = False
    exit_confirmed: bool = False
    cancel_error: str | None = None


@dataclass
class _GpuWorker:
    gpu_id: str
    queue: queue.Queue[object] = field(default_factory=queue.Queue)
    pending: list[str] = field(default_factory=list)
    thread: Thread | None = None


class GpuJobScheduler:
    """Run at most one extraction process per physical GPU in FIFO order."""

    def __init__(
        self,
        store: JobStore,
        secrets: SecretStore,
        runner: Any,
        log_callback: Callable[[str, str], None] | None = None,
        log_root: str | Path | None = None,
        thread_factory: Callable[..., Thread] | None = None,
    ) -> None:
        self._store = store
        self._secrets = secrets
        self._runner = runner
        self._thread_factory = Thread if thread_factory is None else thread_factory
        self._log_callback = log_callback
        self._log_root = Path(log_root) if log_root is not None else None
        if self._log_root is not None:
            self._log_root.mkdir(parents=True, exist_ok=True)
        self._condition = Condition()
        self._entries: dict[str, _JobEntry] = {}
        self._workers: dict[str, _GpuWorker] = {}
        self._health_errors: dict[str, str] = {}
        self._accepting = True
        self._shutdown_complete = False

    def submit(self, job: JobRecord) -> None:
        with self._condition:
            if not self._accepting:
                raise RuntimeError("job scheduler is shut down and is not accepting jobs")
            if job.id in self._entries:
                raise ValueError(f"job '{job.id}' was already submitted")
            persisted = self._store.get(job.id)
            if persisted is None or persisted.status is not JobStatus.QUEUED:
                raise ValueError("submitted job must already be persisted as queued")

            worker = self._workers.get(job.gpu_id)
            if worker is None:
                worker = _GpuWorker(gpu_id=job.gpu_id)
                worker.thread = self._thread_factory(
                    target=self._worker_main,
                    args=(worker,),
                    name=f"bid-gpu-{job.gpu_id}",
                    daemon=True,
                )
                try:
                    worker.thread.start()
                except Exception:
                    # A worker is deliberately started while it is still empty and
                    # private to this call.  If Thread.start() partially succeeds,
                    # stop and join it before preserving submit's atomic contract.
                    try:
                        started = worker.thread.is_alive()
                    except (AttributeError, RuntimeError):
                        started = False
                    if started:
                        worker.queue.put(_STOP)
                        worker.thread.join()
                    raise
                self._workers[job.gpu_id] = worker

            self._entries[job.id] = _JobEntry(job=job)
            worker.pending.append(job.id)
            worker.queue.put(job.id)
            self._condition.notify_all()

    def cancel(self, job_id: str) -> bool:
        with self._condition:
            entry = self._entries.get(job_id)
            if entry is None or entry.state == "terminal":
                return False
            if entry.state == "queued":
                self._cancel_queued_locked(entry)
                return True

            entry.cancel_requested = True
            while entry.handle is None and entry.state != "terminal":
                self._condition.wait()
            if entry.state == "terminal":
                return True
            handle = entry.handle
            api_key = self._secrets.get(job_id) or ""

        cancellation_error: str | None = None
        exit_confirmed = False
        try:
            handle.terminate(timeout_seconds=30)
            handle.wait()
            exit_confirmed = True
        except Exception as exc:
            cancellation_error = self._format_error(
                "Cancellation failed", exc, api_key
            )
        finally:
            with self._condition:
                entry.cancel_attempt_done = True
                entry.exit_confirmed = exit_confirmed
                entry.cancel_error = cancellation_error
                self._condition.notify_all()

        if cancellation_error is not None:
            raise RuntimeError(cancellation_error) from None

        with self._condition:
            while entry.state != "terminal":
                self._condition.wait()
        return True

    def health_errors(self) -> dict[str, str]:
        """Return a snapshot of scheduler errors that could not be persisted."""
        with self._condition:
            return dict(self._health_errors)

    def queue_position(self, job_id: str) -> int | None:
        with self._condition:
            entry = self._entries.get(job_id)
            if entry is None or entry.state != "queued":
                return None
            worker = self._workers[entry.job.gpu_id]
            position = 0
            for pending_id in worker.pending:
                pending = self._entries.get(pending_id)
                if pending is None or pending.state != "queued":
                    continue
                position += 1
                if pending_id == job_id:
                    return position
            return None

    def shutdown(self, timeout_seconds: float = 30) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds cannot be negative")
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            if self._shutdown_complete:
                return
            self._accepting = False
            for entry in list(self._entries.values()):
                if entry.state == "queued":
                    self._cancel_queued_locked(entry)
                elif entry.state == "running":
                    entry.cancel_requested = True

            running_entries = [
                entry for entry in self._entries.values() if entry.state == "running"
            ]
            workers = list(self._workers.values())
            self._condition.notify_all()

        for entry in running_entries:
            with self._condition:
                while entry.handle is None and entry.state != "terminal":
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("timed out waiting for a job process to start")
                    self._condition.wait(timeout=remaining)
                if entry.state == "terminal":
                    continue
                handle = entry.handle
                api_key = self._secrets.get(entry.job.id) or ""
            remaining = max(0.0, deadline - time.monotonic())
            cancellation_error: str | None = None
            exit_confirmed = False
            try:
                handle.terminate(timeout_seconds=remaining)
                handle.wait()
                exit_confirmed = True
            except Exception as exc:
                cancellation_error = self._format_error(
                    "Cancellation failed during shutdown", exc, api_key
                )
            finally:
                with self._condition:
                    entry.cancel_attempt_done = True
                    entry.exit_confirmed = exit_confirmed
                    entry.cancel_error = cancellation_error
                    self._condition.notify_all()

        for worker in workers:
            worker.queue.put(_STOP)
        for worker in workers:
            assert worker.thread is not None
            remaining = max(0.0, deadline - time.monotonic())
            worker.thread.join(timeout=remaining)
            if worker.thread.is_alive():
                raise TimeoutError(
                    f"timed out waiting for GPU {worker.gpu_id} worker shutdown"
                )

        self._secrets.clear()
        with self._condition:
            self._shutdown_complete = True
            self._condition.notify_all()

    def _cancel_queued_locked(self, entry: _JobEntry) -> None:
        worker = self._workers[entry.job.gpu_id]
        try:
            worker.pending.remove(entry.job.id)
        except ValueError:
            pass
        api_key = self._secrets.get(entry.job.id) or ""
        try:
            self._persist_terminal_status_locked(
                entry,
                JobStatus.CANCELLED,
                {},
                api_key,
            )
        finally:
            self._finish_entry_locked(entry, api_key)

    def _worker_main(self, worker: _GpuWorker) -> None:
        while True:
            queued_item = worker.queue.get()
            entry: _JobEntry | None = None
            try:
                if queued_item is _STOP:
                    return
                job_id = str(queued_item)
                with self._condition:
                    entry = self._entries.get(job_id)
                    if entry is None or entry.state != "queued":
                        continue
                    try:
                        worker.pending.remove(job_id)
                    except ValueError:
                        pass
                    entry.state = "running"
                    self._condition.notify_all()
                self._run_entry(entry)
            except Exception as exc:
                self._recover_worker_failure(worker, queued_item, entry, exc)
            finally:
                worker.queue.task_done()

    def _run_entry(self, entry: _JobEntry) -> None:
        job = entry.job
        api_key = self._secrets.get(job.id)

        def redact(text: str) -> str:
            return redact_secret(text, api_key or "")

        def on_started(pid: int, handle: Any) -> None:
            with self._condition:
                entry.handle = handle
                try:
                    self._store.update_status(job.id, JobStatus.RUNNING, pid=pid)
                finally:
                    self._condition.notify_all()

        def on_output(line: str) -> None:
            self._write_log(job.id, redact(line))

        def on_progress(step: int, total: int, stage: str) -> None:
            self._store.update_progress(job.id, step, total, redact(stage))

        if api_key is None:
            result = RunResult(
                exit_code=-1,
                cancelled=False,
                error="API key is unavailable because the service was interrupted",
            )
        else:
            try:
                result = self._runner.run(
                    job,
                    api_key,
                    RunCallbacks(
                        on_started=on_started,
                        on_output=on_output,
                        on_progress=on_progress,
                    ),
                )
            except Exception as exc:
                result = RunResult(
                    exit_code=-1,
                    cancelled=False,
                    error=redact(str(exc)),
                )

        with self._condition:
            try:
                if entry.cancel_requested and entry.handle is None:
                    entry.cancel_attempt_done = True
                    entry.exit_confirmed = True
                    self._condition.notify_all()
                while entry.cancel_requested and not entry.cancel_attempt_done:
                    self._condition.wait()
                if entry.cancel_error is not None:
                    status = JobStatus.FAILED
                    fields = {
                        "exit_code": result.exit_code,
                        "error": redact(entry.cancel_error),
                    }
                elif entry.cancel_requested or result.cancelled:
                    status = JobStatus.CANCELLED
                    fields = {"exit_code": result.exit_code}
                elif result.exit_code == 0 and result.error is None:
                    status = JobStatus.SUCCEEDED
                    fields = {"exit_code": result.exit_code}
                else:
                    status = JobStatus.FAILED
                    error = redact(
                        result.error
                        or f"Process exited with code {result.exit_code}"
                    )
                    fields = {"exit_code": result.exit_code, "error": error}
                self._persist_terminal_status_locked(
                    entry, status, fields, api_key or ""
                )
            finally:
                self._finish_entry_locked(entry, api_key or "")

    def _persist_terminal_status_locked(
        self,
        entry: _JobEntry,
        status: JobStatus,
        fields: dict[str, Any],
        api_key: str,
    ) -> bool:
        last_error: Exception | None = None
        for _attempt in range(3):
            try:
                self._store.update_status(entry.job.id, status, **fields)
                return True
            except Exception as exc:
                last_error = exc
                try:
                    record = self._store.get(entry.job.id)
                except Exception as read_exc:
                    last_error = read_exc
                else:
                    if record is not None and record.status is status:
                        return True

        assert last_error is not None
        message = self._format_error(
            f"Failed to persist terminal status {status.value} after 3 attempts",
            last_error,
            api_key,
        )
        self._health_errors[self._health_key(entry)] = message
        return False

    def _finish_entry_locked(self, entry: _JobEntry, api_key: str) -> None:
        try:
            self._secrets.delete(entry.job.id)
        except Exception as exc:
            self._health_errors[self._health_key(entry)] = self._format_error(
                "Failed to clear job secret", exc, api_key
            )
        finally:
            entry.state = "terminal"
            self._condition.notify_all()

    def _recover_worker_failure(
        self,
        worker: _GpuWorker,
        queued_item: object,
        entry: _JobEntry | None,
        exc: Exception,
    ) -> None:
        if queued_item is _STOP:
            return
        job_id = str(queued_item)
        api_key = self._secrets.get(job_id) or ""
        with self._condition:
            if entry is None:
                entry = self._entries.get(job_id)
            if entry is None:
                self._health_errors[f"{worker.gpu_id}/{job_id}"] = self._format_error(
                    "GPU worker item failed", exc, api_key
                )
                self._secrets.delete(job_id)
                self._condition.notify_all()
                return
            if entry.state == "terminal":
                return
            try:
                self._persist_terminal_status_locked(
                    entry,
                    JobStatus.FAILED,
                    {
                        "exit_code": -1,
                        "error": self._format_error(
                            "GPU worker item failed", exc, api_key
                        ),
                    },
                    api_key,
                )
            finally:
                self._finish_entry_locked(entry, api_key)

    @staticmethod
    def _health_key(entry: _JobEntry) -> str:
        return f"{entry.job.gpu_id}/{entry.job.id}"

    @staticmethod
    def _format_error(prefix: str, exc: Exception, api_key: str) -> str:
        detail = f"{type(exc).__name__}: {exc}"
        return redact_secret(f"{prefix}: {detail}", api_key)

    def _write_log(self, job_id: str, line: str) -> None:
        if self._log_callback is not None:
            self._log_callback(job_id, line)
        if self._log_root is not None:
            log_path = self._log_root / f"{job_id}.log"
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(line)
                log_file.write("\n")
