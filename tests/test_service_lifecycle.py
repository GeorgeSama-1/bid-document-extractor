from pathlib import Path

import pytest

from bid_knowledge.service.jobs.instance_lock import (
    InstanceLock,
    ServiceAlreadyRunningError,
)


def test_instance_lock_is_exclusive_and_reusable(tmp_path: Path) -> None:
    path = tmp_path / "service.lock"
    first = InstanceLock(path)
    second = InstanceLock(path)
    first.acquire()
    with pytest.raises(ServiceAlreadyRunningError):
        second.acquire()
    first.release()
    second.acquire()
    second.release()


def test_instance_lock_context_releases_after_error(tmp_path: Path) -> None:
    path = tmp_path / "service.lock"
    with pytest.raises(RuntimeError):
        with InstanceLock(path):
            raise RuntimeError("startup failed")
    with InstanceLock(path):
        pass
