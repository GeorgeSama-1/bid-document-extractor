from __future__ import annotations

import io
from pathlib import Path

import pytest

from bid_knowledge.service.jobs.files import JobFiles
from bid_knowledge.service.jobs.gpu import GpuInfo, GpuInventoryError
from bid_knowledge.service.jobs.manager import (
    JobConflictError,
    JobManager,
    JobManagerError,
    JobNotFoundError,
    JobValidationError,
)
from bid_knowledge.service.jobs.models import JobParameters, JobRecord, JobStatus
from bid_knowledge.service.jobs.secrets import SecretStore
from bid_knowledge.service.jobs.store import JobStore


class FakeInventory:
    def __init__(self) -> None:
        self.gpus = [GpuInfo(id="6", name="A100", total_mib=80, used_mib=4)]

    def list(self):
        return self.gpus

    def require(self, gpu_id: str):
        for gpu in self.gpus:
            if gpu.id == gpu_id:
                return gpu
        raise GpuInventoryError(f"GPU '{gpu_id}' is not available")


class FakeScheduler:
    def __init__(self) -> None:
        self.submitted: list[JobRecord] = []
        self.positions: dict[str, int | None] = {}
        self.cancelled: list[str] = []
        self.shutdown_calls = 0
        self.submit_error: Exception | None = None
        self.raise_queue_position = False

    def submit(self, job: JobRecord) -> None:
        if self.submit_error is not None:
            raise self.submit_error
        self.submitted.append(job)

    def queue_position(self, job_id: str) -> int | None:
        if self.raise_queue_position:
            raise RuntimeError("queue view unavailable")
        return self.positions.get(job_id)

    def cancel(self, job_id: str) -> bool:
        self.cancelled.append(job_id)
        return True

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def valid_form(**overrides: str) -> dict[str, str]:
    result = {
        "gpu_id": "6",
        "vlm_endpoint": "https://vlm.internal/v1",
        "vlm_model": "table-model",
    }
    result.update(overrides)
    return result


@pytest.fixture
def manager_parts(tmp_path: Path):
    store = JobStore(tmp_path / "service_data/jobs.sqlite3")
    inventory = FakeInventory()
    scheduler = FakeScheduler()
    secrets = SecretStore()
    files = JobFiles()
    manager = JobManager(
        store=store,
        inventory=inventory,
        scheduler=scheduler,
        files=files,
        secrets=secrets,
        upload_root=tmp_path / "service_data/uploads",
        output_root=tmp_path / "outputs",
        log_root=tmp_path / "service_data/logs",
        archive_root=tmp_path / "service_data/archives",
        max_upload_bytes=64,
        max_vlm_workers=32,
    )
    return manager, store, inventory, scheduler, secrets, files, tmp_path


def create(manager: JobManager, *, data: bytes = b"%PDF-1.7\nbody", **form: str):
    return manager.create_job(io.BytesIO(data), "bid.PDF", valid_form(**form), "key")


def test_create_job_validates_pdf_extension_and_header(manager_parts) -> None:
    manager = manager_parts[0]
    with pytest.raises(JobValidationError, match="extension"):
        manager.create_job(io.BytesIO(b"%PDF"), "bid.txt", valid_form(), "key")
    with pytest.raises(JobValidationError, match="header"):
        manager.create_job(io.BytesIO(b"not-pdf"), "bid.pdf", valid_form(), "key")


def test_upload_limit_and_interrupted_upload_leave_no_staging(manager_parts) -> None:
    manager, store, _, _, _, _, tmp_path = manager_parts
    with pytest.raises(JobValidationError, match="maximum"):
        create(manager, data=b"%PDF" + b"x" * 61)

    class BrokenUpload:
        calls = 0

        def read(self, size: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"%PDF"
            raise OSError("connection key broke")

    with pytest.raises(JobValidationError, match="Upload failed") as caught:
        manager.create_job(BrokenUpload(), "bid.pdf", valid_form(), "key")
    assert "key" not in str(caught.value)
    assert store.list() == []
    assert not (tmp_path / "service_data/uploads").exists()
    assert not (tmp_path / "outputs").exists()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("gpu_id", "", "GPU"),
        ("vlm_endpoint", "", "endpoint"),
        ("vlm_endpoint", "vlm.local/v1", "absolute"),
        ("vlm_endpoint", "ftp://vlm.local/v1", "HTTP"),
        ("vlm_endpoint", "http://[broken", "endpoint"),
        ("vlm_endpoint", "https://vlm.local:not-a-port/v1", "endpoint"),
        ("vlm_endpoint", "https://vlm.local/line\nbreak", "endpoint"),
        ("vlm_endpoint", "https://user:pass@vlm.local/v1", "credentials"),
        ("vlm_model", "", "model"),
        ("vlm_model", "x" * 201, "model"),
        ("path_root", "", "path_root"),
        ("path_root", "x" * 101, "path_root"),
        ("vlm_timeout", "0", "vlm_timeout"),
        ("vlm_timeout", "3601", "vlm_timeout"),
        ("vlm_max_tokens", "255", "vlm_max_tokens"),
        ("vlm_max_tokens", "32769", "vlm_max_tokens"),
        ("vlm_workers", "0", "vlm_workers"),
        ("vlm_workers", "33", "vlm_workers"),
        ("vlm_workers", "one", "vlm_workers"),
    ],
)
def test_form_validation_rejects_invalid_values(
    manager_parts, field: str, value: str, message: str
) -> None:
    with pytest.raises(JobValidationError, match=message):
        create(manager_parts[0], **{field: value})


def test_gpu_must_exist(manager_parts) -> None:
    with pytest.raises(JobValidationError, match="not available"):
        create(manager_parts[0], gpu_id="9")


def test_inventory_validation_error_redacts_api_key(manager_parts) -> None:
    manager, _, inventory, *_ = manager_parts

    def fail_require(gpu_id: str):
        raise GpuInventoryError("inventory failed for private-key")

    inventory.require = fail_require
    with pytest.raises(JobValidationError) as caught:
        manager.create_job(
            io.BytesIO(b"%PDF"), "bid.pdf", valid_form(), "private-key"
        )
    assert "private-key" not in str(caught.value)


@pytest.mark.parametrize("value", ["1", "yes", "FALSE ", "True"])
def test_boolean_flags_are_strict(manager_parts, value: str) -> None:
    with pytest.raises(JobValidationError, match="true or false"):
        create(
            manager_parts[0],
            pp_structure_use_doc_orientation_classify=value,
        )


def test_defaults_and_exact_boundaries_are_persisted(manager_parts) -> None:
    manager, store, _, _, _, _, _ = manager_parts
    view = manager.create_job(
        io.BytesIO(b"%PDF"),
        "商务文件.pdf",
        valid_form(
            vlm_timeout="1",
            vlm_max_tokens="32768",
            vlm_workers="32",
            pp_structure_use_doc_unwarping="true",
        ),
        "",
    )
    record = store.get(view.id)
    assert record is not None
    assert record.parameters == JobParameters(
        path_root="PDF",
        pp_structure_use_doc_orientation_classify=False,
        pp_structure_use_doc_unwarping=True,
        pp_structure_use_textline_orientation=False,
        vlm_endpoint="https://vlm.internal/v1",
        vlm_model="table-model",
        vlm_timeout=1,
        vlm_max_tokens=32768,
        vlm_workers=32,
    )


def test_create_uses_private_layout_and_safe_view(manager_parts) -> None:
    manager, store, _, scheduler, secrets, _, tmp_path = manager_parts
    view = manager.create_job(
        io.BytesIO(b"%PDF-private-key"),
        "客户原名.pdf",
        valid_form(path_root="商务文件"),
        "private-key",
    )
    record = store.get(view.id)
    assert record is not None
    assert record.run_name == f"job_{view.id}"
    assert record.output_dir == str(tmp_path / "outputs" / f"job_{view.id}")
    assert (
        tmp_path / "service_data/uploads" / view.id / "input.pdf"
    ).read_bytes() == b"%PDF-private-key"
    assert Path(record.output_dir).is_dir()
    assert scheduler.submitted == [record]
    assert secrets.get(view.id) == "private-key"
    dumped = view.model_dump_json()
    assert "private-key" not in dumped
    assert view.original_filename == "客户原名.pdf"
    assert view.run_name is None


def test_queue_position_and_success_only_run_name(manager_parts) -> None:
    manager, store, _, scheduler, _, _, _ = manager_parts
    view = create(manager)
    scheduler.positions[view.id] = 2
    assert manager.get_job(view.id).queue_position == 2
    store.update_status(view.id, JobStatus.RUNNING)
    store.update_status(view.id, JobStatus.SUCCEEDED)
    succeeded = manager.get_job(view.id)
    assert succeeded.queue_position is None
    assert succeeded.run_name == f"job_{view.id}"


def test_create_returns_job_id_even_if_dynamic_response_dependencies_fail(
    manager_parts,
) -> None:
    manager, store, _, scheduler, *_ = manager_parts
    scheduler.raise_queue_position = True

    view = create(manager)

    assert store.get(view.id) is not None
    assert view.id == scheduler.submitted[0].id
    assert view.queue_position is None


def test_list_get_and_not_found(manager_parts) -> None:
    manager = manager_parts[0]
    first = create(manager)
    assert [item.id for item in manager.list_jobs()] == [first.id]
    with pytest.raises(JobNotFoundError):
        manager.get_job("missing")


def test_list_gpus_delegates_to_inventory(manager_parts) -> None:
    manager, _, inventory, *_ = manager_parts
    assert manager.list_gpus() == inventory.gpus


def test_cancel_delegates_and_rejects_terminal_job(manager_parts) -> None:
    manager, store, _, scheduler, *_ = manager_parts
    view = create(manager)
    assert manager.cancel(view.id).id == view.id
    assert scheduler.cancelled == [view.id]
    store.update_status(view.id, JobStatus.FAILED, error="failed")
    with pytest.raises(JobConflictError, match="terminal"):
        manager.cancel(view.id)


def test_tail_log_is_limited_and_view_includes_log_tail(manager_parts) -> None:
    manager, _, _, _, secrets, _, tmp_path = manager_parts
    view = create(manager)
    log = tmp_path / "service_data/logs" / f"{view.id}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("one\nkey\nthree\n", encoding="utf-8")
    assert secrets.get(view.id) == "key"
    assert manager.tail_log(view.id, limit=2) == ["[REDACTED]", "three"]
    assert manager.get_job(view.id).logs == ["one", "[REDACTED]", "three"]
    with pytest.raises(JobValidationError):
        manager.tail_log(view.id, limit=-1)


def test_tail_log_reads_a_bounded_suffix_and_list_jobs_skips_logs(
    manager_parts, monkeypatch
) -> None:
    manager, _, _, _, _, _, tmp_path = manager_parts
    view = create(manager)
    log = tmp_path / "service_data/logs" / f"{view.id}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(b"ignored line\n" * 100_000 + b"last one\nlast two\n")
    log_size = log.stat().st_size
    original_open = Path.open
    bytes_read = 0

    class ReadSpy:
        def __init__(self, wrapped) -> None:
            self._wrapped = wrapped

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self._wrapped.__exit__(*args)

        def seek(self, *args):
            return self._wrapped.seek(*args)

        def tell(self):
            return self._wrapped.tell()

        def read(self, size=-1):
            nonlocal bytes_read
            data = self._wrapped.read(size)
            bytes_read += len(data)
            return data

    def tracking_open(path, *args, **kwargs):
        opened = original_open(path, *args, **kwargs)
        if path == log:
            return ReadSpy(opened)
        return opened

    monkeypatch.setattr(Path, "open", tracking_open)

    assert manager.tail_log(view.id, limit=2) == ["last one", "last two"]
    assert bytes_read < log_size // 10

    def unexpected_log_read(*args, **kwargs):
        raise AssertionError("list_jobs must not read per-job logs")

    monkeypatch.setattr(manager, "_read_log", unexpected_log_read)
    assert [item.id for item in manager.list_jobs()] == [view.id]
    with pytest.raises(AssertionError, match="per-job logs"):
        manager.get_job(view.id)


def test_tail_log_caps_bytes_for_a_multi_megabyte_single_line(
    manager_parts, monkeypatch
) -> None:
    manager, _, _, _, secrets, _, tmp_path = manager_parts
    view = create(manager)
    log = tmp_path / "service_data/logs" / f"{view.id}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(b"x" * (3 * 1024 * 1024) + b"key")
    original_open = Path.open
    bytes_read = 0

    class ReadSpy:
        def __init__(self, wrapped) -> None:
            self._wrapped = wrapped

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self._wrapped.__exit__(*args)

        def seek(self, *args):
            return self._wrapped.seek(*args)

        def tell(self):
            return self._wrapped.tell()

        def read(self, size=-1):
            nonlocal bytes_read
            data = self._wrapped.read(size)
            bytes_read += len(data)
            return data

    def tracking_open(path, *args, **kwargs):
        opened = original_open(path, *args, **kwargs)
        if path == log:
            return ReadSpy(opened)
        return opened

    monkeypatch.setattr(Path, "open", tracking_open)

    lines = manager.tail_log(view.id)

    assert bytes_read <= 1024 * 1024
    assert lines[0] == "[log truncated]"
    assert lines[-1].endswith("[REDACTED]")
    assert "key" not in "\n".join(lines)


def test_files_open_is_delegated_and_archive_requires_success(manager_parts) -> None:
    manager, store, _, _, _, files, tmp_path = manager_parts
    view = create(manager)
    output = tmp_path / "outputs" / f"job_{view.id}"
    (output / "nested").mkdir()
    (output / "nested/result.docx").write_bytes(b"document")
    listed = manager.list_files(view.id)
    assert [item.path for item in listed] == ["nested/result.docx"]

    opened = manager.open_file(view.id, "nested/result.docx")
    with opened:
        assert opened.read() == b"document"
    with pytest.raises(ValueError):
        manager.open_file(view.id, "../secret")
    with pytest.raises(JobConflictError, match="successful"):
        manager.archive(view.id)
    store.update_status(view.id, JobStatus.RUNNING)
    store.update_status(view.id, JobStatus.SUCCEEDED)
    archive = manager.archive(view.id)
    assert archive == tmp_path / "service_data/archives" / f"{view.id}.zip"
    assert archive.is_file()


def test_constructor_rejects_invalid_environment_overrides(tmp_path, monkeypatch) -> None:
    dependencies = dict(
        store=JobStore(tmp_path / "jobs.sqlite3"),
        inventory=FakeInventory(),
        scheduler=FakeScheduler(),
        files=JobFiles(),
        secrets=SecretStore(),
        upload_root=tmp_path / "uploads",
        output_root=tmp_path / "outputs",
        log_root=tmp_path / "logs",
        archive_root=tmp_path / "archives",
    )
    for name, value in [
        ("BID_SERVICE_MAX_UPLOAD_BYTES", "bad"),
        ("BID_SERVICE_MAX_UPLOAD_BYTES", "0"),
        ("BID_SERVICE_MAX_VLM_WORKERS", "0"),
        ("BID_SERVICE_MAX_VLM_WORKERS", "129"),
    ]:
        monkeypatch.delenv("BID_SERVICE_MAX_UPLOAD_BYTES", raising=False)
        monkeypatch.delenv("BID_SERVICE_MAX_VLM_WORKERS", raising=False)
        monkeypatch.setenv(name, value)
        with pytest.raises(ValueError, match=name):
            JobManager(**dependencies)


@pytest.mark.parametrize(
    "override", [{"max_upload_bytes": True}, {"max_vlm_workers": False}]
)
def test_constructor_rejects_boolean_integer_overrides(tmp_path, override) -> None:
    dependencies = dict(
        store=JobStore(tmp_path / "jobs.sqlite3"),
        inventory=FakeInventory(),
        scheduler=FakeScheduler(),
        files=JobFiles(),
        secrets=SecretStore(),
        upload_root=tmp_path / "uploads",
        output_root=tmp_path / "outputs",
        log_root=tmp_path / "logs",
        archive_root=tmp_path / "archives",
    )

    with pytest.raises(ValueError, match="positive|between"):
        JobManager(**dependencies, **override)


def test_worker_default_is_lowered_by_configured_cap(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    manager = JobManager(
        store=store,
        inventory=FakeInventory(),
        scheduler=FakeScheduler(),
        files=JobFiles(),
        secrets=SecretStore(),
        upload_root=tmp_path / "uploads",
        output_root=tmp_path / "outputs",
        log_root=tmp_path / "logs",
        archive_root=tmp_path / "archives",
        max_upload_bytes=100,
        max_vlm_workers=8,
    )

    view = manager.create_job(
        io.BytesIO(b"%PDF"), "bid.pdf", valid_form(), ""
    )

    assert view.parameters.vlm_workers == 8


class FailingSecrets(SecretStore):
    def put(self, job_id: str, api_key: str) -> None:
        super().put(job_id, api_key)
        raise RuntimeError(f"secret backend saw {api_key}")


class TransientDeleteSecrets(SecretStore):
    def __init__(self) -> None:
        super().__init__()
        self.delete_calls = 0

    def delete(self, job_id: str) -> str | None:
        self.delete_calls += 1
        if self.delete_calls == 1:
            raise RuntimeError("temporary secret cleanup failure")
        return super().delete(job_id)


@pytest.mark.parametrize("failure", ["store", "secret", "scheduler"])
def test_create_failure_cleans_staging_secret_and_queued_state(
    tmp_path: Path, failure: str
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    scheduler = FakeScheduler()
    secrets: SecretStore = SecretStore()
    if failure == "store":
        original_create = store.create

        def fail_create(job):
            original_create(job)
            raise RuntimeError("store backend saw private-key")

        store.create = fail_create  # type: ignore[method-assign]
    elif failure == "secret":
        secrets = FailingSecrets()
    else:
        scheduler.submit_error = RuntimeError("scheduler saw private-key")
    manager = JobManager(
        store=store,
        inventory=FakeInventory(),
        scheduler=scheduler,
        files=JobFiles(),
        secrets=secrets,
        upload_root=tmp_path / "uploads",
        output_root=tmp_path / "outputs",
        log_root=tmp_path / "logs",
        archive_root=tmp_path / "archives",
        max_upload_bytes=100,
    )

    with pytest.raises(RuntimeError) as caught:
        manager.create_job(io.BytesIO(b"%PDF"), "bid.pdf", valid_form(), "private-key")
    assert "private-key" not in str(caught.value)
    assert not any((tmp_path / "uploads").glob("**/*"))
    assert not any((tmp_path / "outputs").glob("**/*"))
    records = store.list()
    assert len(records) == 1
    assert records[0].status is JobStatus.FAILED
    assert "private-key" not in (records[0].error or "")
    assert secrets.get(records[0].id) is None


def test_rollback_retries_transient_secret_and_status_cleanup_failures(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    original_update = store.update_status
    update_calls = 0

    def transient_update(job_id, status, **fields):
        nonlocal update_calls
        update_calls += 1
        if update_calls == 1:
            raise RuntimeError("temporary status cleanup failure")
        return original_update(job_id, status, **fields)

    store.update_status = transient_update  # type: ignore[method-assign]
    secrets = TransientDeleteSecrets()
    scheduler = FakeScheduler()
    scheduler.submit_error = RuntimeError("submit failed")
    manager = JobManager(
        store=store,
        inventory=FakeInventory(),
        scheduler=scheduler,
        files=JobFiles(),
        secrets=secrets,
        upload_root=tmp_path / "uploads",
        output_root=tmp_path / "outputs",
        log_root=tmp_path / "logs",
        archive_root=tmp_path / "archives",
        max_upload_bytes=100,
    )

    with pytest.raises(RuntimeError):
        manager.create_job(io.BytesIO(b"%PDF"), "bid.pdf", valid_form(), "key")

    record = store.list()[0]
    assert record.status is JobStatus.FAILED
    assert secrets.get(record.id) is None
    assert secrets.delete_calls >= 2
    assert update_calls >= 2


def test_atomic_submit_failure_does_not_attempt_scheduler_cancellation(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    scheduler = FakeScheduler()
    scheduler.submit_error = RuntimeError("thread start failed")
    manager = JobManager(
        store=store,
        inventory=FakeInventory(),
        scheduler=scheduler,
        files=JobFiles(),
        secrets=SecretStore(),
        upload_root=tmp_path / "uploads",
        output_root=tmp_path / "outputs",
        log_root=tmp_path / "logs",
        archive_root=tmp_path / "archives",
        max_upload_bytes=100,
    )

    with pytest.raises(RuntimeError):
        manager.create_job(io.BytesIO(b"%PDF"), "bid.pdf", valid_form(), "key")

    record = store.list()[0]
    assert scheduler.submitted == []
    assert scheduler.cancelled == []
    assert record.status is JobStatus.FAILED


def test_preexisting_upload_directory_is_never_removed(
    tmp_path: Path, monkeypatch
) -> None:
    manager = JobManager(
        store=JobStore(tmp_path / "jobs.sqlite3"),
        inventory=FakeInventory(),
        scheduler=FakeScheduler(),
        files=JobFiles(),
        secrets=SecretStore(),
        upload_root=tmp_path / "uploads",
        output_root=tmp_path / "outputs",
        log_root=tmp_path / "logs",
        archive_root=tmp_path / "archives",
        max_upload_bytes=100,
    )
    original_mkdir = Path.mkdir
    marker = tmp_path / "marker"

    def collide_with_upload(path, *args, **kwargs):
        if path.parent == tmp_path / "uploads" and path.name != "uploads":
            original_mkdir(path, parents=True, exist_ok=True)
            (path / "owned-by-someone-else").write_text("keep", encoding="utf-8")
            marker.write_text(str(path), encoding="utf-8")
        return original_mkdir(path, *args, **kwargs)

    # Force a collision without depending on the randomly generated job id.
    monkeypatch.setattr(Path, "mkdir", collide_with_upload)
    with pytest.raises(JobManagerError, match="Could not create job"):
        manager.create_job(io.BytesIO(b"%PDF"), "bid.pdf", valid_form(), "key")

    collided = Path(marker.read_text(encoding="utf-8"))
    assert (collided / "owned-by-someone-else").read_text(encoding="utf-8") == "keep"


def test_upload_cleanup_failure_is_visible_and_redacted(
    tmp_path: Path, monkeypatch
) -> None:
    manager = JobManager(
        store=JobStore(tmp_path / "jobs.sqlite3"),
        inventory=FakeInventory(),
        scheduler=FakeScheduler(),
        files=JobFiles(),
        secrets=SecretStore(),
        upload_root=tmp_path / "uploads",
        output_root=tmp_path / "outputs",
        log_root=tmp_path / "logs",
        archive_root=tmp_path / "archives",
        max_upload_bytes=100,
    )

    def fail_cleanup(path):
        raise PermissionError("cleanup exposed private-key")

    monkeypatch.setattr("bid_knowledge.service.jobs.manager.shutil.rmtree", fail_cleanup)

    with pytest.raises(JobValidationError) as caught:
        manager.create_job(
            io.BytesIO(b"not-pdf"), "bid.pdf", valid_form(), "private-key"
        )
    assert "cleanup" in str(caught.value)
    assert "private-key" not in str(caught.value)


def test_start_marks_interrupted_and_shutdown_delegates(manager_parts) -> None:
    manager, store, _, scheduler, secrets, _, tmp_path = manager_parts
    queued = JobRecord(
        id="old",
        filename="old.pdf",
        gpu_id="6",
        run_name="job_old",
        output_dir=str(tmp_path / "outputs/job_old"),
        parameters=JobParameters(
            vlm_endpoint="https://vlm.internal/v1", vlm_model="model"
        ),
    )
    store.create(queued)
    secrets.put("old", "old-key")
    manager.start()
    assert store.get("old").status is JobStatus.FAILED
    assert secrets.get("old") is None
    manager.shutdown()
    assert scheduler.shutdown_calls == 1
