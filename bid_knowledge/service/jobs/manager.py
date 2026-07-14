from __future__ import annotations

import errno
import os
import shutil
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from bid_knowledge.service.jobs.files import JobFiles, OutputFile
from bid_knowledge.service.jobs.gpu import GpuInfo, GpuInventoryError, NvidiaSmiInventory
from bid_knowledge.service.jobs.models import JobParameters, JobRecord, JobStatus
from bid_knowledge.service.jobs.secrets import SecretStore, redact_secret
from bid_knowledge.service.jobs.store import JobStore


_DEFAULT_MAX_UPLOAD_BYTES = 500 * 1024 * 1024
_DEFAULT_MAX_VLM_WORKERS = 128
_UPLOAD_CHUNK_BYTES = 1024 * 1024
_LOG_READ_BLOCK_BYTES = 8192


class JobManagerError(RuntimeError):
    """Base class for errors suitable for translation at the HTTP boundary."""


class JobValidationError(JobManagerError, ValueError):
    """A submitted job field is invalid."""


class JobNotFoundError(JobManagerError, KeyError):
    """The requested job does not exist."""


class JobConflictError(JobManagerError):
    """The requested operation conflicts with the current job state."""


class JobProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step: int
    total: int
    stage: str


class JobView(BaseModel):
    """Response-safe job representation; it deliberately has no API-key field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    status: JobStatus
    queue_position: int | None
    gpu_id: str
    original_filename: str
    parameters: JobParameters
    progress: JobProgress
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    logs: list[str]
    run_name: str | None


class JobManager:
    def __init__(
        self,
        *,
        store: JobStore,
        inventory: NvidiaSmiInventory,
        scheduler: Any,
        files: JobFiles,
        secrets: SecretStore,
        upload_root: str | Path = Path("service_data/uploads"),
        output_root: str | Path = Path("outputs"),
        log_root: str | Path = Path("service_data/logs"),
        archive_root: str | Path = Path("service_data/archives"),
        max_upload_bytes: int | None = None,
        max_vlm_workers: int | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        environment = os.environ if environ is None else environ
        self._max_upload_bytes = self._configuration_integer(
            explicit=max_upload_bytes,
            environment=environment,
            name="BID_SERVICE_MAX_UPLOAD_BYTES",
            default=_DEFAULT_MAX_UPLOAD_BYTES,
            maximum=None,
        )
        self._max_vlm_workers = self._configuration_integer(
            explicit=max_vlm_workers,
            environment=environment,
            name="BID_SERVICE_MAX_VLM_WORKERS",
            default=_DEFAULT_MAX_VLM_WORKERS,
            maximum=128,
        )
        self._store = store
        self._inventory = inventory
        self._scheduler = scheduler
        self._files = files
        self._secrets = secrets
        self._upload_root = Path(upload_root)
        self._output_root = Path(output_root)
        self._log_root = Path(log_root)
        self._archive_root = Path(archive_root)

    @staticmethod
    def _configuration_integer(
        *,
        explicit: int | None,
        environment: Mapping[str, str],
        name: str,
        default: int,
        maximum: int | None,
    ) -> int:
        raw: object = explicit
        if raw is None:
            raw = environment.get(name, str(default))
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a positive integer") from exc
        if (
            isinstance(raw, (bool, float))
            or value <= 0
            or (maximum is not None and value > maximum)
        ):
            range_text = (
                f" between 1 and {maximum}" if maximum is not None else " positive"
            )
            raise ValueError(f"{name} must be{range_text}")
        return value

    def list_gpus(self) -> list[GpuInfo]:
        return self._inventory.list()

    def create_job(
        self,
        upload: BinaryIO,
        filename: str,
        form: Mapping[str, str],
        api_key: str,
    ) -> JobView:
        try:
            if not isinstance(filename, str) or not filename.lower().endswith(".pdf"):
                raise JobValidationError("The uploaded file must have a .pdf extension")
            gpu_id, parameters = self._validate_form(form)
            self._inventory.require(gpu_id)
        except JobValidationError as exc:
            raise JobValidationError(redact_secret(str(exc), api_key)) from None
        except GpuInventoryError as exc:
            raise JobValidationError(redact_secret(str(exc), api_key)) from None
        except Exception as exc:
            safe_error = redact_secret(str(exc), api_key)
            raise JobManagerError(f"Could not validate job: {safe_error}") from None

        job = JobRecord(
            filename=filename,
            gpu_id=gpu_id,
            run_name="placeholder",
            output_dir="placeholder",
            parameters=parameters,
        )
        run_name = f"job_{job.id}"
        output_dir = self._output_root / run_name
        job = job.model_copy(
            update={"run_name": run_name, "output_dir": str(output_dir)}
        )
        upload_dir = self._upload_root / job.id
        persisted = False
        upload_owned = False
        upload_root_owned = False
        output_owned = False
        output_root_owned = False
        try:
            upload_root_owned = self._ensure_directory(self._upload_root)
            upload_dir.mkdir(exist_ok=False)
            upload_owned = True
            self._save_upload(upload, upload_dir)
            output_root_owned = self._ensure_directory(self._output_root)
            output_dir.mkdir(exist_ok=False)
            output_owned = True
            self._store.create(job)
            persisted = True
            self._secrets.put(job.id, api_key)
            self._scheduler.submit(job)
        except JobValidationError as exc:
            rollback_errors = self._rollback_creation(
                job,
                upload_dir,
                output_dir,
                api_key,
                persisted,
                upload_owned,
                upload_root_owned,
                output_owned,
                output_root_owned,
                None,
            )
            safe_error = redact_secret(str(exc), api_key)
            if rollback_errors:
                safe_error = f"{safe_error}; rollback issues: {'; '.join(rollback_errors)}"
            raise JobValidationError(safe_error) from None
        except Exception as exc:
            safe_error = redact_secret(str(exc), api_key)
            if not persisted:
                try:
                    persisted = self._store.get(job.id) is not None
                except Exception:
                    pass
            rollback_errors = self._rollback_creation(
                job,
                upload_dir,
                output_dir,
                api_key,
                persisted,
                upload_owned,
                upload_root_owned,
                output_owned,
                output_root_owned,
                safe_error,
            )
            if rollback_errors:
                safe_error = f"{safe_error}; rollback issues: {'; '.join(rollback_errors)}"
            raise JobManagerError(f"Could not create job: {safe_error}") from None
        return self._to_view(job, dynamic=False)

    def _save_upload(self, upload: BinaryIO, upload_dir: Path) -> None:
        temporary_path = upload_dir / ".input.pdf.tmp"
        final_path = upload_dir / "input.pdf"
        total = 0
        header = bytearray()
        try:
            with temporary_path.open("xb") as destination:
                while True:
                    chunk = upload.read(_UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise TypeError("upload stream must return bytes")
                    total += len(chunk)
                    if total > self._max_upload_bytes:
                        raise JobValidationError(
                            f"Upload exceeds the maximum size of {self._max_upload_bytes} bytes"
                        )
                    if len(header) < 4:
                        header.extend(chunk[: 4 - len(header)])
                    destination.write(chunk)
            if bytes(header) != b"%PDF":
                raise JobValidationError("The uploaded file has an invalid PDF header")
            os.replace(temporary_path, final_path)
        except JobValidationError:
            raise
        except Exception as exc:
            raise JobValidationError(f"Upload failed: {exc}") from None

    def _rollback_creation(
        self,
        job: JobRecord,
        upload_dir: Path,
        output_dir: Path,
        api_key: str,
        persisted: bool,
        upload_owned: bool,
        upload_root_owned: bool,
        output_owned: bool,
        output_root_owned: bool,
        error: str | None,
    ) -> list[str]:
        rollback_errors: list[str] = []
        secret_error: Exception | None = None
        for _attempt in range(3):
            try:
                self._secrets.delete(job.id)
                secret_error = None
                break
            except Exception as exc:
                secret_error = exc
        if secret_error is not None:
            rollback_errors.append(
                redact_secret(
                    f"secret cleanup failed after 3 attempts: {secret_error}",
                    api_key,
                )
            )
        for path, owned, label in (
            (upload_dir, upload_owned, "upload cleanup"),
            (output_dir, output_owned, "output cleanup"),
        ):
            if not owned:
                continue
            try:
                self._remove_tree(path)
            except Exception as exc:
                rollback_errors.append(
                    redact_secret(f"{label} failed: {exc}", api_key)
                )
        for path, owned, label in (
            (self._upload_root, upload_root_owned, "upload root cleanup"),
            (self._output_root, output_root_owned, "output root cleanup"),
        ):
            if not owned:
                continue
            try:
                self._remove_empty_parent(path)
            except Exception as exc:
                rollback_errors.append(
                    redact_secret(f"{label} failed: {exc}", api_key)
                )
        if persisted:
            safe_error = redact_secret(error or "Job creation failed", api_key)
            status_error: Exception | None = None
            for _attempt in range(3):
                try:
                    current = self._store.get(job.id)
                    if current is None or current.status is not JobStatus.QUEUED:
                        status_error = None
                        break
                    self._store.update_status(
                        job.id, JobStatus.FAILED, error=safe_error
                    )
                    status_error = None
                    break
                except Exception as exc:
                    status_error = exc
            if status_error is not None:
                rollback_errors.append(
                    redact_secret(
                        f"status cleanup failed after 3 attempts: {status_error}",
                        api_key,
                    )
                )
        return rollback_errors

    @staticmethod
    def _remove_tree(path: Path) -> None:
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            pass

    @staticmethod
    def _ensure_directory(path: Path) -> bool:
        """Ensure a directory exists and report whether this call created it."""
        try:
            path.mkdir(parents=True, exist_ok=False)
            return True
        except FileExistsError:
            if not path.is_dir():
                raise
            return False

    @staticmethod
    def _remove_empty_parent(path: Path) -> None:
        try:
            path.rmdir()
        except FileNotFoundError:
            return
        except OSError as exc:
            if exc.errno in {errno.ENOTEMPTY, errno.EEXIST}:
                return
            raise

    def _validate_form(self, form: Mapping[str, str]) -> tuple[str, JobParameters]:
        gpu_id = form.get("gpu_id", "")
        if not gpu_id:
            raise JobValidationError("GPU is required")
        path_root = form.get("path_root", "PDF")
        self._validate_length("path_root", path_root, 1, 100)

        endpoint = form.get("vlm_endpoint", "")
        if not endpoint:
            raise JobValidationError("VLM endpoint is required")
        if any(
            ord(character) <= 32 or ord(character) == 127
            for character in endpoint
        ):
            raise JobValidationError(
                "VLM endpoint cannot contain whitespace or control characters"
            )
        try:
            parsed = urlsplit(endpoint)
            hostname = parsed.hostname
            username = parsed.username
            password = parsed.password
            parsed.port
        except ValueError:
            raise JobValidationError("VLM endpoint is not a valid absolute URL") from None
        if parsed.scheme not in {"http", "https"}:
            if not parsed.scheme:
                raise JobValidationError("VLM endpoint must be an absolute HTTP(S) URL")
            raise JobValidationError("VLM endpoint must use HTTP or HTTPS")
        if not parsed.netloc or hostname is None:
            raise JobValidationError("VLM endpoint must be an absolute HTTP(S) URL")
        if username is not None or password is not None:
            raise JobValidationError("VLM endpoint cannot contain credentials")

        model = form.get("vlm_model", "")
        self._validate_length("VLM model", model, 1, 200)
        parameters = JobParameters(
            path_root=path_root,
            pp_structure_use_doc_orientation_classify=self._parse_bool(
                form, "pp_structure_use_doc_orientation_classify"
            ),
            pp_structure_use_doc_unwarping=self._parse_bool(
                form, "pp_structure_use_doc_unwarping"
            ),
            pp_structure_use_textline_orientation=self._parse_bool(
                form, "pp_structure_use_textline_orientation"
            ),
            vlm_endpoint=endpoint,
            vlm_model=model,
            vlm_timeout=self._parse_bounded_int(form, "vlm_timeout", 1800, 1, 3600),
            vlm_max_tokens=self._parse_bounded_int(
                form, "vlm_max_tokens", 8192, 256, 32768
            ),
            vlm_workers=self._parse_bounded_int(
                form,
                "vlm_workers",
                min(16, self._max_vlm_workers),
                1,
                self._max_vlm_workers,
            ),
        )
        return gpu_id, parameters

    @staticmethod
    def _validate_length(name: str, value: object, minimum: int, maximum: int) -> None:
        if not isinstance(value, str) or not minimum <= len(value) <= maximum:
            raise JobValidationError(
                f"{name} length must be between {minimum} and {maximum} characters"
            )

    @staticmethod
    def _parse_bool(form: Mapping[str, str], name: str) -> bool:
        value = form.get(name, "false")
        if value == "true":
            return True
        if value == "false":
            return False
        raise JobValidationError(f"{name} must be exactly true or false")

    @staticmethod
    def _parse_bounded_int(
        form: Mapping[str, str], name: str, default: int, minimum: int, maximum: int
    ) -> int:
        raw = form.get(name, str(default))
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise JobValidationError(f"{name} must be an integer") from None
        if not minimum <= value <= maximum:
            raise JobValidationError(
                f"{name} must be between {minimum} and {maximum}"
            )
        return value

    def list_jobs(self) -> list[JobView]:
        return [
            self._to_view(record, include_logs=False) for record in self._store.list()
        ]

    def get_job(self, job_id: str) -> JobView:
        return self._to_view(self._get_record(job_id))

    def _get_record(self, job_id: str) -> JobRecord:
        record = self._store.get(job_id)
        if record is None:
            raise JobNotFoundError(job_id)
        return record

    def _to_view(
        self,
        record: JobRecord,
        *,
        dynamic: bool = True,
        include_logs: bool = True,
    ) -> JobView:
        queue_position = None
        if dynamic and record.status is JobStatus.QUEUED:
            queue_position = self._scheduler.queue_position(record.id)
        return JobView(
            id=record.id,
            status=record.status,
            queue_position=queue_position,
            gpu_id=record.gpu_id,
            original_filename=record.filename,
            parameters=record.parameters,
            progress=JobProgress(
                step=record.progress_step,
                total=record.progress_total,
                stage=record.progress_stage,
            ),
            created_at=record.created_at,
            updated_at=record.updated_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
            error=record.error,
            logs=self._read_log(record.id, limit=200)
            if dynamic and include_logs
            else [],
            run_name=record.run_name if record.status is JobStatus.SUCCEEDED else None,
        )

    def cancel(self, job_id: str) -> JobView:
        record = self._get_record(job_id)
        if record.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            raise JobConflictError("A terminal job cannot be cancelled")
        if not self._scheduler.cancel(job_id):
            raise JobConflictError("The job is no longer cancellable")
        return self.get_job(job_id)

    def tail_log(self, job_id: str, limit: int = 200) -> list[str]:
        self._get_record(job_id)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise JobValidationError("Log line limit must be a non-negative integer")
        return self._read_log(job_id, limit)

    def _read_log(self, job_id: str, limit: int) -> list[str]:
        if limit == 0:
            return []
        path = self._log_root / f"{job_id}.log"
        try:
            with path.open("rb") as log:
                log.seek(0, os.SEEK_END)
                remaining = log.tell()
                chunks: list[bytes] = []
                newline_count = 0
                while remaining > 0 and newline_count <= limit:
                    size = min(_LOG_READ_BLOCK_BYTES, remaining)
                    remaining -= size
                    log.seek(remaining)
                    chunk = log.read(size)
                    chunks.append(chunk)
                    newline_count += chunk.count(b"\n")
                text = b"".join(reversed(chunks)).decode(
                    "utf-8", errors="replace"
                )
                lines = text.splitlines()[-limit:]
                return [self._secrets.redact(job_id, line) for line in lines]
        except FileNotFoundError:
            return []

    def list_files(self, job_id: str) -> list[OutputFile]:
        record = self._get_record(job_id)
        return self._files.list(Path(record.output_dir))

    def open_file(self, job_id: str, relative_path: str) -> BinaryIO:
        record = self._get_record(job_id)
        return self._files.open_file(Path(record.output_dir), relative_path)

    def archive(self, job_id: str) -> Path:
        record = self._get_record(job_id)
        if record.status is not JobStatus.SUCCEEDED:
            raise JobConflictError("An archive is available only for a successful job")
        return self._files.archive(job_id, Path(record.output_dir), self._archive_root)

    def start(self) -> None:
        self._store.mark_interrupted()
        self._secrets.clear()

    def shutdown(self) -> None:
        self._scheduler.shutdown()


__all__ = [
    "JobConflictError",
    "JobManager",
    "JobManagerError",
    "JobNotFoundError",
    "JobProgress",
    "JobValidationError",
    "JobView",
]
