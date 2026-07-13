from __future__ import annotations

import csv
import math
import subprocess
from collections.abc import Callable
from io import StringIO

from pydantic import BaseModel, ConfigDict


_NVIDIA_SMI_COMMAND = [
    "nvidia-smi",
    "--query-gpu=index,name,memory.total,memory.used",
    "--format=csv,noheader,nounits",
]


class GpuInventoryError(RuntimeError):
    """Raised when the server GPU inventory cannot be read safely."""


class GpuInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    total_mib: int
    used_mib: int


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class NvidiaSmiInventory:
    def __init__(
        self,
        run: CommandRunner = subprocess.run,
        timeout: float = 10.0,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and greater than zero")
        self._run = run
        self._timeout = float(timeout)

    def list(self) -> list[GpuInfo]:
        try:
            result = self._run(
                _NVIDIA_SMI_COMMAND,
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise GpuInventoryError(
                f"nvidia-smi timed out after {self._timeout:g} seconds"
            ) from exc
        except FileNotFoundError as exc:
            raise GpuInventoryError(
                "nvidia-smi was not found; NVIDIA drivers are unavailable"
            ) from exc
        except OSError as exc:
            raise GpuInventoryError(f"nvidia-smi could not be executed: {exc}") from exc

        if result.returncode != 0:
            detail = (result.stderr or "").strip() or f"exit code {result.returncode}"
            raise GpuInventoryError(f"nvidia-smi failed: {detail}")

        gpus: list[GpuInfo] = []
        for line_number, row in enumerate(csv.reader(StringIO(result.stdout)), start=1):
            if not row or all(not field.strip() for field in row):
                continue
            if len(row) != 4:
                raise GpuInventoryError(
                    f"malformed nvidia-smi row {line_number}: expected 4 fields"
                )
            gpu_id, name, total_text, used_text = (field.strip() for field in row)
            if not gpu_id or not name:
                raise GpuInventoryError(
                    f"malformed nvidia-smi row {line_number}: empty GPU id or name"
                )
            try:
                total_mib = int(total_text)
                used_mib = int(used_text)
            except ValueError as exc:
                raise GpuInventoryError(
                    f"malformed nvidia-smi row {line_number}: memory is not an integer"
                ) from exc
            if total_mib < 0 or used_mib < 0:
                raise GpuInventoryError(
                    f"malformed nvidia-smi row {line_number}: memory cannot be negative"
                )
            gpus.append(
                GpuInfo(
                    id=gpu_id,
                    name=name,
                    total_mib=total_mib,
                    used_mib=used_mib,
                )
            )

        if not gpus:
            raise GpuInventoryError("nvidia-smi reported no GPUs")
        return gpus

    def require(self, gpu_id: str) -> GpuInfo:
        for gpu in self.list():
            if gpu.id == gpu_id:
                return gpu
        raise GpuInventoryError(f"GPU '{gpu_id}' is not available")
