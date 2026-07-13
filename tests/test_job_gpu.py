from __future__ import annotations

import subprocess

import pytest

from bid_knowledge.service.jobs.gpu import (
    GpuInventoryError,
    NvidiaSmiInventory,
)


EXPECTED_COMMAND = [
    "nvidia-smi",
    "--query-gpu=index,name,memory.total,memory.used",
    "--format=csv,noheader,nounits",
]


def completed(stdout: str, *, returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        EXPECTED_COMMAND,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_inventory_executes_exact_query_and_parses_all_gpu_fields() -> None:
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return completed(
            "0, NVIDIA A100-SXM4-80GB, 81920, 1024\n"
            "\n"
            " 6 , NVIDIA L40S , 46068 , 2048 \n"
        )

    inventory = NvidiaSmiInventory(run=run)

    gpus = inventory.list()

    assert calls == [
        (
            EXPECTED_COMMAND,
            {
                "capture_output": True,
                "text": True,
                "check": False,
                "timeout": 10.0,
            },
        )
    ]
    assert [gpu.model_dump() for gpu in gpus] == [
        {
            "id": "0",
            "name": "NVIDIA A100-SXM4-80GB",
            "total_mib": 81920,
            "used_mib": 1024,
        },
        {
            "id": "6",
            "name": "NVIDIA L40S",
            "total_mib": 46068,
            "used_mib": 2048,
        },
    ]


@pytest.mark.parametrize(
    "stdout",
    [
        "0, NVIDIA A100, not-a-number, 100",
        "0, NVIDIA A100, 81920",
        "0, NVIDIA A100, 81920, 100, unexpected",
        ", NVIDIA A100, 81920, 100",
        "0, , 81920, 100",
        "0, NVIDIA A100, -1, 100",
        "0, NVIDIA A100, 81920, -1",
        "0, NVIDIA A100, 81920, 100\nbroken",
    ],
)
def test_inventory_rejects_any_malformed_row_without_partial_results(stdout) -> None:
    inventory = NvidiaSmiInventory(run=lambda *_args, **_kwargs: completed(stdout))

    with pytest.raises(GpuInventoryError, match="malformed"):
        inventory.list()


def test_inventory_reports_missing_nvidia_smi_clearly() -> None:
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("nvidia-smi")

    inventory = NvidiaSmiInventory(run=missing)

    with pytest.raises(GpuInventoryError, match="nvidia-smi.*not found"):
        inventory.list()


def test_inventory_reports_nonzero_exit_clearly() -> None:
    inventory = NvidiaSmiInventory(
        run=lambda *_args, **_kwargs: completed(
            "", returncode=9, stderr="driver unavailable"
        )
    )

    with pytest.raises(GpuInventoryError, match="driver unavailable"):
        inventory.list()


def test_inventory_uses_configured_finite_timeout() -> None:
    calls = []

    def run(*_args, **kwargs):
        calls.append(kwargs)
        return completed("6, NVIDIA L40S, 46068, 200")

    NvidiaSmiInventory(run=run, timeout=2.5).list()

    assert calls[0]["timeout"] == 2.5


def test_inventory_reports_command_timeout_clearly() -> None:
    def timed_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(EXPECTED_COMMAND, timeout=10.0)

    inventory = NvidiaSmiInventory(run=timed_out)

    with pytest.raises(GpuInventoryError, match="timed out.*10"):
        inventory.list()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_inventory_rejects_non_finite_or_non_positive_timeout(timeout) -> None:
    with pytest.raises(ValueError, match="timeout"):
        NvidiaSmiInventory(timeout=timeout)


@pytest.mark.parametrize("stdout", ["", "\n \n"])
def test_inventory_rejects_empty_gpu_inventory(stdout) -> None:
    inventory = NvidiaSmiInventory(run=lambda *_args, **_kwargs: completed(stdout))

    with pytest.raises(GpuInventoryError, match="no GPUs"):
        inventory.list()


def test_require_returns_selected_physical_gpu() -> None:
    inventory = NvidiaSmiInventory(
        run=lambda *_args, **_kwargs: completed(
            "0, NVIDIA A100, 81920, 100\n6, NVIDIA L40S, 46068, 200"
        )
    )

    selected = inventory.require("6")

    assert selected.id == "6"
    assert selected.name == "NVIDIA L40S"


def test_require_rejects_unknown_physical_gpu() -> None:
    inventory = NvidiaSmiInventory(
        run=lambda *_args, **_kwargs: completed("6, NVIDIA L40S, 46068, 200")
    )

    with pytest.raises(GpuInventoryError, match="GPU '0'.*not available"):
        inventory.require("0")
