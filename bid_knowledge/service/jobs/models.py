from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobParameters(BaseModel):
    """Non-secret command parameters safe to persist and return from the API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path_root: str = "PDF"
    enable_pp_structure: bool = True
    pp_structure_device: str = "gpu"
    pp_structure_use_doc_orientation_classify: bool = False
    pp_structure_use_doc_unwarping: bool = False
    pp_structure_use_textline_orientation: bool = False
    enable_vlm_table: bool = True
    vlm_endpoint: str = ""
    vlm_model: str = ""
    vlm_timeout: int = 1800
    vlm_max_tokens: int = 8192
    vlm_workers: int = 16


class JobRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: uuid4().hex)
    filename: str
    gpu_id: str
    run_name: str
    output_dir: str
    parameters: JobParameters
    status: JobStatus = JobStatus.QUEUED
    progress_step: int = 0
    progress_total: int = 0
    progress_stage: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    pid: int | None = None
    exit_code: int | None = None
    error: str | None = None
