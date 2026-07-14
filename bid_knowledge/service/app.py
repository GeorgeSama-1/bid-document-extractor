from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.datastructures import UploadFile

from bid_knowledge.service.agent_material_context import (
    AgentMaterialContextService,
    ProjectMaterialContextService,
)
from bid_knowledge.service.jobs.files import JobFiles
from bid_knowledge.service.jobs.gpu import GpuInventoryError, NvidiaSmiInventory
from bid_knowledge.service.jobs.instance_lock import InstanceLock
from bid_knowledge.service.jobs.manager import (
    JobConflictError,
    JobManager,
    JobManagerError,
    JobNotFoundError,
    JobValidationError,
)
from bid_knowledge.service.jobs.runner import SubprocessJobRunner
from bid_knowledge.service.jobs.scheduler import GpuJobScheduler
from bid_knowledge.service.jobs.secrets import SecretStore, redact_secret
from bid_knowledge.service.jobs.store import JobStore
from bid_knowledge.service.result_browser import ResultBrowser, ResultNotFoundError
from bid_knowledge.utils.project_paths import (
    default_project_config_path,
    outputs_dir,
    project_root,
)


BASE_DIR = project_root()
STATIC_DIR = Path(__file__).resolve().parent / "static"
OUTPUTS_DIR = outputs_dir()
PROJECTS_CONFIG = default_project_config_path()
SERVICE_DATA_DIR = BASE_DIR / "service_data"

browser = ResultBrowser(OUTPUTS_DIR)
context_service = AgentMaterialContextService(OUTPUTS_DIR)
project_context_service = ProjectMaterialContextService(OUTPUTS_DIR, PROJECTS_CONFIG)


class MaterialContextRequest(BaseModel):
    section_path: str | None = None
    title: str | None = None
    top_k: int = 5


def _production_manager() -> JobManager:
    store = JobStore(SERVICE_DATA_DIR / "jobs.sqlite3")
    secrets = SecretStore()
    upload_root = SERVICE_DATA_DIR / "uploads"
    log_root = SERVICE_DATA_DIR / "logs"
    scheduler = GpuJobScheduler(
        store,
        secrets,
        SubprocessJobRunner(upload_root=upload_root),
        log_root=log_root,
    )
    return JobManager(
        store=store,
        inventory=NvidiaSmiInventory(),
        scheduler=scheduler,
        files=JobFiles(),
        secrets=secrets,
        upload_root=upload_root,
        output_root=OUTPUTS_DIR,
        log_root=log_root,
        archive_root=SERVICE_DATA_DIR / "archives",
    )


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _job_payload(value: Any) -> dict[str, Any]:
    payload = _jsonable(value)
    return {
        key: payload.get(key)
        for key in (
            "id",
            "status",
            "queue_position",
            "gpu_id",
            "original_filename",
            "parameters",
            "progress",
            "created_at",
            "updated_at",
            "started_at",
            "finished_at",
            "error",
            "logs",
            "run_name",
        )
    }


def _stream_open_file(opened: BinaryIO, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    try:
        while chunk := opened.read(chunk_size):
            yield chunk
    finally:
        opened.close()


def create_app(job_manager: JobManager | Any | None = None) -> FastAPI:
    production = job_manager is None
    manager = _production_manager() if production else job_manager
    lock = InstanceLock(SERVICE_DATA_DIR / "service.lock") if production else None

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        if lock is not None:
            lock.acquire()
        try:
            manager.start()
            yield
        finally:
            try:
                manager.shutdown()
            finally:
                if lock is not None:
                    lock.release()

    application = FastAPI(title="Bid Result Browser", lifespan=lifespan)
    application.state.job_manager = manager

    def manager_error(exc: Exception, *, api_key: str = "") -> HTTPException:
        safe = redact_secret(str(exc), api_key)
        if isinstance(exc, JobNotFoundError):
            return HTTPException(status_code=404, detail=safe)
        if isinstance(exc, JobConflictError):
            return HTTPException(status_code=409, detail=safe)
        if isinstance(exc, (JobValidationError, GpuInventoryError, ValueError)):
            return HTTPException(status_code=400, detail=safe)
        if isinstance(exc, FileNotFoundError):
            return HTTPException(status_code=404, detail="Output file not found")
        if isinstance(exc, JobManagerError):
            return HTTPException(status_code=400, detail=safe)
        return HTTPException(
            status_code=500, detail=f"Internal service error ({type(exc).__name__})"
        )

    @application.get("/api/system/gpus")
    def list_gpus() -> dict[str, object]:
        try:
            return {"gpus": [_jsonable(item) for item in manager.list_gpus()]}
        except Exception as exc:
            raise manager_error(exc) from None

    @application.post("/api/jobs", status_code=201)
    async def create_job(request: Request) -> JSONResponse:
        api_key = ""
        upload: UploadFile | None = None
        try:
            parsed = await request.form()
            api_key_value = parsed.get("api_key", "")
            api_key = api_key_value if isinstance(api_key_value, str) else ""
            upload_value = parsed.get("pdf")
            if not isinstance(upload_value, UploadFile):
                raise JobValidationError("A PDF upload is required")
            upload = upload_value
            sanitized_form = {
                key: value
                for key, value in parsed.multi_items()
                if key not in {"api_key", "pdf"} and isinstance(value, str)
            }
            view = manager.create_job(
                upload.file,
                upload.filename or "",
                sanitized_form,
                api_key,
            )
            return JSONResponse(_job_payload(view), status_code=201)
        except HTTPException:
            raise
        except Exception as exc:
            raise manager_error(exc, api_key=api_key) from None
        finally:
            if upload is not None:
                await upload.close()

    @application.get("/api/jobs")
    def list_jobs() -> dict[str, object]:
        try:
            return {"jobs": [_job_payload(item) for item in manager.list_jobs()]}
        except Exception as exc:
            raise manager_error(exc) from None

    @application.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        try:
            return _job_payload(manager.get_job(job_id))
        except Exception as exc:
            raise manager_error(exc) from None

    @application.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        try:
            return _job_payload(manager.cancel(job_id))
        except Exception as exc:
            raise manager_error(exc) from None

    @application.get("/api/jobs/{job_id}/files")
    def list_job_files(job_id: str) -> dict[str, object]:
        try:
            return {"files": [_jsonable(item) for item in manager.list_files(job_id)]}
        except Exception as exc:
            raise manager_error(exc) from None

    @application.get("/api/jobs/{job_id}/files/{file_path:path}")
    def download_job_file(job_id: str, file_path: str) -> StreamingResponse:
        try:
            opened = manager.open_file(job_id, file_path)
        except Exception as exc:
            raise manager_error(exc) from None
        filename = Path(file_path).name
        disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        return StreamingResponse(
            _stream_open_file(opened),
            media_type="application/octet-stream",
            headers={"Content-Disposition": disposition},
        )

    @application.get("/api/jobs/{job_id}/archive")
    def download_job_archive(job_id: str) -> FileResponse:
        try:
            archive = manager.archive(job_id)
        except Exception as exc:
            raise manager_error(exc) from None
        return FileResponse(
            archive,
            media_type="application/zip",
            filename=f"job_{job_id}.zip",
        )

    @application.get("/api/runs")
    def list_runs() -> dict[str, object]:
        return {"runs": browser.list_runs()}

    @application.get("/api/runs/{run_name}/modules/tree")
    def get_module_tree(run_name: str) -> dict[str, object]:
        try:
            return browser.get_module_tree(run_name)
        except ResultNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.get("/api/runs/{run_name}/materials/meta")
    def get_material_meta(run_name: str, path: str = Query(...)) -> dict[str, object]:
        try:
            return browser.get_material_meta(run_name, path)
        except ResultNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.get("/api/runs/{run_name}/materials/ordered")
    def get_ordered_material(run_name: str, path: str = Query(...)) -> dict[str, object]:
        try:
            return browser.get_ordered_material(run_name, path)
        except ResultNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.post("/api/runs/{run_name}/materials/context")
    def get_material_context(
        run_name: str, request: MaterialContextRequest
    ) -> dict[str, object]:
        try:
            return context_service.get_context(
                run_name,
                section_path=request.section_path,
                title=request.title,
                top_k=request.top_k,
            )
        except ResultNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.post("/api/projects/{project_id}/materials/context")
    def get_project_material_context(
        project_id: str, request: MaterialContextRequest
    ) -> dict[str, object]:
        try:
            return project_context_service.get_project_context(
                project_id,
                section_path=request.section_path,
                title=request.title,
                top_k=request.top_k,
            )
        except ResultNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.get("/api/runs/{run_name}/items/detail")
    def get_item_detail(
        run_name: str,
        material_path: str = Query(...),
        item_type: str = Query(...),
        item_name: str = Query(...),
    ) -> object:
        try:
            return browser.get_item_detail(run_name, material_path, item_type, item_name)
        except ResultNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.get("/api/runs/{run_name}/images")
    def get_image(run_name: str, path: str = Query(...)) -> FileResponse:
        try:
            file_path, media_type = browser.get_image_file(run_name, path)
        except ResultNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(file_path, media_type=media_type)

    application.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return application


app = create_app()


__all__ = ["app", "create_app"]
