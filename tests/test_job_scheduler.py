from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from bid_knowledge.service.jobs.models import JobParameters, JobRecord, JobStatus
from bid_knowledge.service.jobs.runner import RunCallbacks, RunResult
from bid_knowledge.service.jobs.scheduler import GpuJobScheduler
from bid_knowledge.service.jobs.secrets import SecretStore
from bid_knowledge.service.jobs.store import JobStore


def make_job(tmp_path: Path, job_id: str, gpu_id: str = "6") -> JobRecord:
    return JobRecord(
        id=job_id,
        filename=f"{job_id}.pdf",
        gpu_id=gpu_id,
        run_name=f"job_{job_id}",
        output_dir=str(tmp_path / "outputs" / f"job_{job_id}"),
        parameters=JobParameters(
            path_root="商务文件",
            vlm_endpoint="https://vlm.internal/v1",
            vlm_model="table-model",
        ),
    )


def wait_until(predicate: Callable[[], bool], timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached before timeout")


class FakeRunningProcess:
    def __init__(self, release: threading.Event) -> None:
        self._release = release
        self.terminated = threading.Event()
        self.waited = threading.Event()

    def terminate(self, timeout_seconds: float = 30) -> None:
        self.terminated.set()
        self._release.set()

    def wait(self) -> int:
        assert self._release.wait(timeout=5)
        self.waited.set()
        return -15 if self.terminated.is_set() else 0


class ControlledRunner:
    def __init__(self, held_jobs: set[str] | None = None) -> None:
        self.held_jobs = held_jobs or set()
        self.started: dict[str, threading.Event] = {}
        self.releases: dict[str, threading.Event] = {}
        self.handles: dict[str, FakeRunningProcess] = {}
        self.results: dict[str, RunResult] = {}
        self.raise_for: dict[str, Exception] = {}
        self.call_order: list[str] = []
        self.finished_order: list[str] = []
        self._active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def event_for(self, mapping: dict[str, threading.Event], job_id: str):
        with self._lock:
            return mapping.setdefault(job_id, threading.Event())

    def run(
        self,
        job: JobRecord,
        api_key: str,
        callbacks: RunCallbacks,
    ) -> RunResult:
        started = self.event_for(self.started, job.id)
        release = self.event_for(self.releases, job.id)
        handle = FakeRunningProcess(release)
        with self._lock:
            self.handles[job.id] = handle
            self.call_order.append(job.id)
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        callbacks.on_started(1000 + len(self.call_order), handle)
        callbacks.on_output(f"output contains {api_key}")
        callbacks.on_progress(2, 5, f"stage contains {api_key}")
        started.set()
        try:
            if job.id in self.held_jobs:
                assert release.wait(timeout=5)
            if job.id in self.raise_for:
                raise self.raise_for[job.id]
            if handle.terminated.is_set():
                return RunResult(exit_code=-15, cancelled=True, error=None)
            return self.results.get(
                job.id,
                RunResult(exit_code=0, cancelled=False, error=None),
            )
        finally:
            with self._lock:
                self._active -= 1
                self.finished_order.append(job.id)

    def wait_started(self, job_id: str) -> None:
        assert self.event_for(self.started, job_id).wait(timeout=5)

    def release(self, job_id: str) -> None:
        self.event_for(self.releases, job_id).set()


def make_scheduler(
    tmp_path, runner, *, secrets=None, log_callback=None, thread_factory=None
):
    store = JobStore(tmp_path / "jobs.sqlite3")
    secrets = secrets or SecretStore()
    scheduler_kwargs = {}
    if thread_factory is not None:
        scheduler_kwargs["thread_factory"] = thread_factory
    scheduler = GpuJobScheduler(
        store=store,
        secrets=secrets,
        runner=runner,
        log_callback=log_callback,
        **scheduler_kwargs,
    )
    return store, secrets, scheduler


def persist_and_submit(store, secrets, scheduler, job, key=None) -> None:
    store.create(job)
    secrets.put(job.id, key if key is not None else f"key-{job.id}")
    scheduler.submit(job)


def test_new_worker_start_failure_is_atomic_and_retryable(tmp_path) -> None:
    runner = ControlledRunner()
    failed_workers = []
    factory_calls = 0

    class StartFailureThread:
        def start(self) -> None:
            raise RuntimeError("thread start failed")

    def thread_factory(**kwargs):
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            failed_workers.append(kwargs["args"][0])
            return StartFailureThread()
        return threading.Thread(**kwargs)

    store, secrets, scheduler = make_scheduler(
        tmp_path, runner, thread_factory=thread_factory
    )
    job = make_job(tmp_path, "retry")
    store.create(job)
    secrets.put(job.id, "key-retry")

    with pytest.raises(RuntimeError, match="thread start failed"):
        scheduler.submit(job)

    failed_worker = failed_workers[0]
    assert scheduler.queue_position(job.id) is None
    assert scheduler.cancel(job.id) is False
    assert scheduler._entries == {}
    assert scheduler._workers == {}
    assert failed_worker.pending == []
    assert failed_worker.queue.empty()
    assert not runner.event_for(runner.started, job.id).is_set()

    scheduler.submit(job)
    runner.wait_started(job.id)
    wait_until(lambda: store.get(job.id).status is JobStatus.SUCCEEDED)
    scheduler.shutdown()


def test_same_gpu_runs_fifo_without_overlap(tmp_path) -> None:
    runner = ControlledRunner(held_jobs={"first", "second"})
    store, secrets, scheduler = make_scheduler(tmp_path, runner)
    first = make_job(tmp_path, "first")
    second = make_job(tmp_path, "second")
    try:
        persist_and_submit(store, secrets, scheduler, first)
        persist_and_submit(store, secrets, scheduler, second)
        runner.wait_started("first")

        assert not runner.event_for(runner.started, "second").is_set()
        runner.release("first")
        runner.wait_started("second")
        runner.release("second")
        wait_until(lambda: store.get("second").status is JobStatus.SUCCEEDED)

        assert runner.call_order == ["first", "second"]
        assert runner.finished_order == ["first", "second"]
        assert runner.max_active == 1
    finally:
        scheduler.shutdown()


def test_different_gpus_run_in_parallel(tmp_path) -> None:
    runner = ControlledRunner(held_jobs={"gpu6", "gpu7"})
    store, secrets, scheduler = make_scheduler(tmp_path, runner)
    try:
        persist_and_submit(store, secrets, scheduler, make_job(tmp_path, "gpu6", "6"))
        persist_and_submit(store, secrets, scheduler, make_job(tmp_path, "gpu7", "7"))

        runner.wait_started("gpu6")
        runner.wait_started("gpu7")
        assert runner.max_active == 2
        runner.release("gpu6")
        runner.release("gpu7")
        wait_until(lambda: store.get("gpu6").status is JobStatus.SUCCEEDED)
        wait_until(lambda: store.get("gpu7").status is JobStatus.SUCCEEDED)
    finally:
        scheduler.shutdown()


def test_queue_position_counts_only_queued_jobs_ahead_on_same_gpu(tmp_path) -> None:
    runner = ControlledRunner(held_jobs={"running", "second", "third", "other"})
    store, secrets, scheduler = make_scheduler(tmp_path, runner)
    try:
        persist_and_submit(store, secrets, scheduler, make_job(tmp_path, "running"))
        runner.wait_started("running")
        persist_and_submit(store, secrets, scheduler, make_job(tmp_path, "second"))
        persist_and_submit(store, secrets, scheduler, make_job(tmp_path, "third"))
        persist_and_submit(
            store, secrets, scheduler, make_job(tmp_path, "other", gpu_id="7")
        )
        runner.wait_started("other")

        assert scheduler.queue_position("running") is None
        assert scheduler.queue_position("second") == 1
        assert scheduler.queue_position("third") == 2
        assert scheduler.queue_position("other") is None
        assert scheduler.queue_position("missing") is None

        runner.release("running")
        runner.release("other")
        runner.wait_started("second")
        assert scheduler.queue_position("second") is None
        assert scheduler.queue_position("third") == 1
        runner.release("second")
        runner.wait_started("third")
        runner.release("third")
    finally:
        scheduler.shutdown()


def test_cancel_queued_job_is_immediate_and_clears_secret(tmp_path) -> None:
    runner = ControlledRunner(held_jobs={"running"})
    store, secrets, scheduler = make_scheduler(tmp_path, runner)
    try:
        persist_and_submit(store, secrets, scheduler, make_job(tmp_path, "running"))
        runner.wait_started("running")
        persist_and_submit(store, secrets, scheduler, make_job(tmp_path, "queued"))

        assert scheduler.cancel("queued") is True

        cancelled = store.get("queued")
        assert cancelled is not None
        assert cancelled.status is JobStatus.CANCELLED
        assert cancelled.finished_at is not None
        assert secrets.get("queued") is None
        assert not runner.event_for(runner.started, "queued").is_set()
        assert scheduler.cancel("queued") is False
        runner.release("running")
    finally:
        scheduler.shutdown()


def test_cancel_running_waits_for_process_exit_then_updates_store_before_key_delete(
    tmp_path,
) -> None:
    runner = ControlledRunner(held_jobs={"running"})
    store = JobStore(tmp_path / "jobs.sqlite3")

    class OrderingSecretStore(SecretStore):
        def __init__(self) -> None:
            super().__init__()
            self.status_at_delete: JobStatus | None = None

        def delete(self, job_id: str) -> str | None:
            record = store.get(job_id)
            self.status_at_delete = record.status if record is not None else None
            return super().delete(job_id)

    secrets = OrderingSecretStore()
    scheduler = GpuJobScheduler(store=store, secrets=secrets, runner=runner)
    job = make_job(tmp_path, "running")
    try:
        persist_and_submit(store, secrets, scheduler, job, key="sentinel-key")
        runner.wait_started(job.id)

        assert scheduler.cancel(job.id) is True

        handle = runner.handles[job.id]
        assert handle.terminated.is_set()
        assert handle.waited.is_set()
        assert store.get(job.id).status is JobStatus.CANCELLED
        assert secrets.status_at_delete is JobStatus.CANCELLED
        assert secrets.get(job.id) is None
    finally:
        scheduler.shutdown()


def test_cancel_does_not_persist_terminal_state_until_handle_wait_confirms_exit(
    tmp_path,
) -> None:
    started = threading.Event()
    runner_can_return = threading.Event()
    wait_entered = threading.Event()
    confirm_exit = threading.Event()

    class DelayedWaitHandle:
        def terminate(self, timeout_seconds: float = 30) -> None:
            runner_can_return.set()

        def wait(self) -> int:
            wait_entered.set()
            assert confirm_exit.wait(timeout=5)
            return -15

    class RacingRunner:
        def run(self, job, api_key, callbacks):
            callbacks.on_started(4321, DelayedWaitHandle())
            started.set()
            assert runner_can_return.wait(timeout=5)
            return RunResult(exit_code=-15, cancelled=True, error=None)

    store, secrets, scheduler = make_scheduler(tmp_path, RacingRunner())
    job = make_job(tmp_path, "racing")
    persist_and_submit(store, secrets, scheduler, job, key="sentinel-key")
    assert started.wait(timeout=5)
    cancellation = threading.Thread(target=scheduler.cancel, args=(job.id,))
    cancellation.start()
    try:
        assert wait_entered.wait(timeout=5)
        time.sleep(0.05)

        assert store.get(job.id).status is JobStatus.RUNNING
        assert secrets.get(job.id) == "sentinel-key"

        confirm_exit.set()
        cancellation.join(timeout=5)
        assert not cancellation.is_alive()
        assert store.get(job.id).status is JobStatus.CANCELLED
        assert secrets.get(job.id) is None
    finally:
        confirm_exit.set()
        cancellation.join(timeout=5)
        scheduler.shutdown()


def test_cancel_during_spawn_failure_does_not_deadlock_without_process_handle(
    tmp_path,
) -> None:
    run_entered = threading.Event()
    let_spawn_fail = threading.Event()

    class SpawnFailureRunner:
        def run(self, job, api_key, callbacks):
            run_entered.set()
            assert let_spawn_fail.wait(timeout=5)
            return RunResult(exit_code=-1, cancelled=False, error="spawn failed")

    store, secrets, scheduler = make_scheduler(tmp_path, SpawnFailureRunner())
    job = make_job(tmp_path, "spawn-race")
    persist_and_submit(store, secrets, scheduler, job)
    assert run_entered.wait(timeout=5)
    cancellation = threading.Thread(target=scheduler.cancel, args=(job.id,), daemon=True)
    cancellation.start()
    let_spawn_fail.set()
    cancellation.join(timeout=2)

    assert not cancellation.is_alive()
    assert store.get(job.id).status is JobStatus.CANCELLED
    assert secrets.get(job.id) is None
    scheduler.shutdown()


def test_output_errors_and_progress_are_redacted_and_terminal_keys_are_cleared(
    tmp_path,
) -> None:
    runner = ControlledRunner()
    runner.results["failed"] = RunResult(
        exit_code=9,
        cancelled=False,
        error="failure leaked key-failed",
    )
    runner.raise_for["raised"] = RuntimeError("exception leaked key-raised")
    logs: list[tuple[str, str]] = []
    store, secrets, scheduler = make_scheduler(
        tmp_path,
        runner,
        log_callback=lambda job_id, line: logs.append((job_id, line)),
    )
    try:
        for job_id in ("success", "failed", "raised"):
            persist_and_submit(
                store,
                secrets,
                scheduler,
                make_job(tmp_path, job_id, gpu_id=job_id),
            )

        wait_until(lambda: store.get("success").status is JobStatus.SUCCEEDED)
        wait_until(lambda: store.get("failed").status is JobStatus.FAILED)
        wait_until(lambda: store.get("raised").status is JobStatus.FAILED)

        assert all(secrets.get(job_id) is None for job_id in ("success", "failed", "raised"))
        assert "key-failed" not in (store.get("failed").error or "")
        assert "[REDACTED]" in (store.get("failed").error or "")
        assert "key-raised" not in (store.get("raised").error or "")
        assert "[REDACTED]" in (store.get("raised").error or "")
        combined_logs = "\n".join(line for _job_id, line in logs)
        assert "key-success" not in combined_logs
        assert "key-failed" not in combined_logs
        assert "key-raised" not in combined_logs
        assert "[REDACTED]" in combined_logs
        for job_id in ("success", "failed", "raised"):
            record = store.get(job_id)
            assert record.progress_stage == "stage contains [REDACTED]"
    finally:
        scheduler.shutdown()


def test_shutdown_rejects_submission_cancels_queue_and_waits_for_running_exit(
    tmp_path,
) -> None:
    runner = ControlledRunner(held_jobs={"running"})
    store, secrets, scheduler = make_scheduler(tmp_path, runner)
    running = make_job(tmp_path, "running")
    queued = make_job(tmp_path, "queued")
    persist_and_submit(store, secrets, scheduler, running)
    runner.wait_started("running")
    persist_and_submit(store, secrets, scheduler, queued)

    scheduler.shutdown(timeout_seconds=5)

    assert runner.handles["running"].terminated.is_set()
    assert runner.handles["running"].waited.is_set()
    assert store.get("running").status is JobStatus.CANCELLED
    assert store.get("queued").status is JobStatus.CANCELLED
    assert secrets.get("running") is None
    assert secrets.get("queued") is None
    assert not runner.event_for(runner.started, "queued").is_set()
    with pytest.raises(RuntimeError, match="shut|accept"):
        scheduler.submit(make_job(tmp_path, "late"))
    assert scheduler.cancel("missing") is False
    scheduler.shutdown(timeout_seconds=5)


@pytest.mark.parametrize("failure_point", ["terminate", "wait"])
def test_cancel_failure_unblocks_worker_redacts_error_and_runs_next_job(
    tmp_path, failure_point
) -> None:
    first_started = threading.Event()
    first_release = threading.Event()
    second_started = threading.Event()

    class FaultyHandle:
        def __init__(self) -> None:
            self.cancel_requested = False

        def terminate(self, timeout_seconds: float = 30) -> None:
            self.cancel_requested = True
            if failure_point == "terminate":
                raise RuntimeError("terminate exposed sentinel-key")
            first_release.set()

        def wait(self) -> int:
            if failure_point == "wait":
                raise RuntimeError("wait exposed sentinel-key")
            return -15

    class FaultyCancellationRunner:
        def run(self, job, api_key, callbacks):
            if job.id == "first":
                handle = FaultyHandle()
                callbacks.on_started(4321, handle)
                first_started.set()
                assert first_release.wait(timeout=5)
                return RunResult(
                    exit_code=-15 if handle.cancel_requested else 0,
                    cancelled=handle.cancel_requested,
                    error=None,
                )
            callbacks.on_started(4322, FakeRunningProcess(threading.Event()))
            second_started.set()
            return RunResult(exit_code=0, cancelled=False, error=None)

    store, secrets, scheduler = make_scheduler(tmp_path, FaultyCancellationRunner())
    first = make_job(tmp_path, "first")
    second = make_job(tmp_path, "second")
    try:
        persist_and_submit(store, secrets, scheduler, first, key="sentinel-key")
        persist_and_submit(store, secrets, scheduler, second, key="second-key")
        assert first_started.wait(timeout=5)

        with pytest.raises(RuntimeError, match="Cancellation failed") as raised:
            scheduler.cancel(first.id)
        assert "sentinel-key" not in str(raised.value)

        first_release.set()
        assert second_started.wait(timeout=5)
        wait_until(lambda: store.get(second.id).status is JobStatus.SUCCEEDED)
        wait_until(lambda: secrets.get(first.id) is None)

        first_record = store.get(first.id)
        assert first_record.status is JobStatus.FAILED
        assert "sentinel-key" not in (first_record.error or "")
        assert "[REDACTED]" in (first_record.error or "")
        assert secrets.get(second.id) is None
    finally:
        first_release.set()
        scheduler.shutdown(timeout_seconds=5)


class FaultingTerminalStore:
    def __init__(self, store: JobStore, failures: int | None) -> None:
        self._store = store
        self._remaining = failures

    def __getattr__(self, name):
        return getattr(self._store, name)

    def update_status(self, job_id, status, **fields):
        if status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
            if self._remaining is None or self._remaining > 0:
                if self._remaining is not None:
                    self._remaining -= 1
                raise RuntimeError(f"terminal write exposed key-{job_id}")
        return self._store.update_status(job_id, status, **fields)


def test_terminal_update_is_retried_without_stopping_gpu_worker(tmp_path) -> None:
    base_store = JobStore(tmp_path / "jobs.sqlite3")
    store = FaultingTerminalStore(base_store, failures=1)
    secrets = SecretStore()
    runner = ControlledRunner()
    scheduler = GpuJobScheduler(store=store, secrets=secrets, runner=runner)
    try:
        for job_id in ("first", "second"):
            persist_and_submit(store, secrets, scheduler, make_job(tmp_path, job_id))

        wait_until(lambda: base_store.get("second").status is JobStatus.SUCCEEDED)

        assert runner.call_order == ["first", "second"]
        assert base_store.get("first").status is JobStatus.SUCCEEDED
        assert secrets.get("first") is None
        assert secrets.get("second") is None
    finally:
        scheduler.shutdown(timeout_seconds=5)


def test_persistent_terminal_update_records_health_error_and_worker_continues(
    tmp_path,
) -> None:
    base_store = JobStore(tmp_path / "jobs.sqlite3")
    store = FaultingTerminalStore(base_store, failures=None)
    secrets = SecretStore()
    runner = ControlledRunner()
    scheduler = GpuJobScheduler(store=store, secrets=secrets, runner=runner)
    try:
        for job_id in ("first", "second"):
            persist_and_submit(store, secrets, scheduler, make_job(tmp_path, job_id))

        runner.wait_started("second")
        wait_until(lambda: secrets.get("second") is None)

        assert runner.call_order == ["first", "second"]
        assert secrets.get("first") is None
        errors = scheduler.health_errors()
        assert "6/first" in errors
        assert "key-first" not in errors["6/first"]
        assert "[REDACTED]" in errors["6/first"]
    finally:
        scheduler.shutdown(timeout_seconds=5)


def test_log_callback_failure_does_not_kill_worker_or_leak_secret(tmp_path) -> None:
    runner = ControlledRunner()

    def broken_log_callback(_job_id: str, _line: str) -> None:
        raise RuntimeError("log callback exposed key-first")

    store, secrets, scheduler = make_scheduler(
        tmp_path, runner, log_callback=broken_log_callback
    )
    try:
        for job_id in ("first", "second"):
            persist_and_submit(store, secrets, scheduler, make_job(tmp_path, job_id))

        wait_until(lambda: runner.call_order == ["first", "second"])
        wait_until(lambda: store.get("second").status is JobStatus.FAILED)

        assert runner.call_order == ["first", "second"]
        assert secrets.get("first") is None
        assert secrets.get("second") is None
        assert "key-first" not in (store.get("first").error or "")
        assert "[REDACTED]" in (store.get("first").error or "")
    finally:
        scheduler.shutdown(timeout_seconds=5)


def test_shutdown_times_out_deterministically_when_cancel_cannot_stop_job(
    tmp_path,
) -> None:
    started = threading.Event()
    release = threading.Event()

    class UnstoppableHandle:
        def terminate(self, timeout_seconds: float = 30) -> None:
            raise RuntimeError("terminate exposed sentinel-key")

        def wait(self) -> int:
            raise AssertionError("wait must not run after terminate fails")

    class UnstoppableRunner:
        def run(self, job, api_key, callbacks):
            callbacks.on_started(9876, UnstoppableHandle())
            started.set()
            assert release.wait(timeout=5)
            return RunResult(exit_code=0, cancelled=False, error=None)

    store, secrets, scheduler = make_scheduler(tmp_path, UnstoppableRunner())
    job = make_job(tmp_path, "stuck")
    persist_and_submit(store, secrets, scheduler, job, key="sentinel-key")
    assert started.wait(timeout=5)
    try:
        before = time.monotonic()
        with pytest.raises(TimeoutError, match="GPU 6 worker shutdown"):
            scheduler.shutdown(timeout_seconds=0.05)
        assert time.monotonic() - before < 1

        release.set()
        wait_until(lambda: secrets.get(job.id) is None)
        record = store.get(job.id)
        assert record.status is JobStatus.FAILED
        assert "sentinel-key" not in (record.error or "")
    finally:
        release.set()
        scheduler.shutdown(timeout_seconds=5)
