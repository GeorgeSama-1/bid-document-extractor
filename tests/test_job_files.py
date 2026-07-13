from __future__ import annotations

import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from bid_knowledge.service.jobs.files import JobFiles


def test_list_enumerates_nested_files_in_stable_order(tmp_path) -> None:
    output_root = tmp_path / "output"
    (output_root / "tables").mkdir(parents=True)
    (output_root / "summary.md").write_text("summary", encoding="utf-8")
    (output_root / "tables" / "table-1.json").write_text("{}", encoding="utf-8")

    files = JobFiles().list(output_root)

    assert [(item.path, item.size_bytes) for item in files] == [
        ("summary.md", 7),
        ("tables/table-1.json", 2),
    ]


def test_symlinks_are_excluded_from_listing_resolution_and_archive(tmp_path) -> None:
    output_root = tmp_path / "output"
    archive_root = tmp_path / "archives"
    outside = tmp_path / "outside.txt"
    output_root.mkdir()
    outside.write_text("secret", encoding="utf-8")
    (output_root / "safe.txt").write_text("safe", encoding="utf-8")
    (output_root / "outside-link.txt").symlink_to(outside)
    (output_root / "safe-link.txt").symlink_to(output_root / "safe.txt")
    linked_dir = output_root / "linked-dir"
    linked_dir.symlink_to(tmp_path, target_is_directory=True)

    job_files = JobFiles()

    assert [item.path for item in job_files.list(output_root)] == ["safe.txt"]
    for relative_path in ("outside-link.txt", "safe-link.txt", "linked-dir/outside.txt"):
        with pytest.raises((FileNotFoundError, ValueError)):
            job_files.resolve(output_root, relative_path)

    archive = job_files.archive("job-1", output_root, archive_root)
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.namelist() == ["safe.txt"]
        assert bundle.read("safe.txt") == b"safe"


@pytest.mark.parametrize(
    "relative_path",
    [
        "/etc/passwd",
        "../outside.txt",
        "nested/../../outside.txt",
        "nested/../safe.txt",
    ],
)
def test_resolve_rejects_absolute_paths_and_parent_segments(
    tmp_path, relative_path
) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    (output_root / "safe.txt").write_text("safe", encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe|relative|parent"):
        JobFiles().resolve(output_root, relative_path)


def test_resolve_returns_nested_regular_file(tmp_path) -> None:
    output_root = tmp_path / "output"
    nested = output_root / "nested" / "result.json"
    nested.parent.mkdir(parents=True)
    nested.write_text("{}", encoding="utf-8")

    resolved = JobFiles().resolve(output_root, "nested/result.json")

    assert resolved == nested.resolve()


def test_resolve_rejects_missing_and_directory_paths(tmp_path) -> None:
    output_root = tmp_path / "output"
    (output_root / "nested").mkdir(parents=True)

    with pytest.raises(FileNotFoundError):
        JobFiles().resolve(output_root, "missing.txt")
    with pytest.raises(FileNotFoundError):
        JobFiles().resolve(output_root, "nested")


def test_archive_contains_only_nested_regular_output_files(tmp_path) -> None:
    output_root = tmp_path / "output"
    archive_root = tmp_path / "archives"
    (output_root / "nested").mkdir(parents=True)
    (output_root / "root.txt").write_text("root", encoding="utf-8")
    (output_root / "nested" / "data.json").write_text(
        '{"ok": true}', encoding="utf-8"
    )

    archive = JobFiles().archive("abc123", output_root, archive_root)

    assert archive == archive_root.resolve() / "abc123.zip"
    assert not list(archive_root.glob("*.tmp"))
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.namelist() == ["nested/data.json", "root.txt"]
        assert bundle.read("nested/data.json") == b'{"ok": true}'


@pytest.mark.parametrize("job_id", ["../escape", "nested/escape", "", ".", ".."])
def test_archive_rejects_unsafe_job_ids(tmp_path, job_id) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    (output_root / "result.txt").write_text("result", encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe job id"):
        JobFiles().archive(job_id, output_root, tmp_path / "archives")


def test_concurrent_archive_requests_build_once_under_per_job_lock(tmp_path) -> None:
    output_root = tmp_path / "output"
    archive_root = tmp_path / "archives"
    output_root.mkdir()
    (output_root / "result.txt").write_text("result", encoding="utf-8")
    calls = 0
    calls_lock = threading.Lock()
    start = threading.Barrier(2)

    class CountingJobFiles(JobFiles):
        def _write_archive(self, output_root: Path, temporary_path: Path) -> None:
            nonlocal calls
            with calls_lock:
                calls += 1
            super()._write_archive(output_root, temporary_path)

    job_files = CountingJobFiles()

    def create_archive(_index: int) -> Path:
        start.wait(timeout=5)
        return job_files.archive("shared-job", output_root, archive_root)

    with ThreadPoolExecutor(max_workers=2) as executor:
        archives = list(executor.map(create_archive, range(2)))

    assert archives[0] == archives[1]
    assert calls == 1
    with zipfile.ZipFile(archives[0]) as bundle:
        assert bundle.read("result.txt") == b"result"


def test_archives_for_different_jobs_use_independent_locks(tmp_path) -> None:
    output_root = tmp_path / "output"
    archive_root = tmp_path / "archives"
    output_root.mkdir()
    (output_root / "result.txt").write_text("result", encoding="utf-8")
    both_building = threading.Barrier(2)

    class SynchronizingJobFiles(JobFiles):
        def _write_archive(self, output_root: Path, temporary_path: Path) -> None:
            both_building.wait(timeout=5)
            super()._write_archive(output_root, temporary_path)

    job_files = SynchronizingJobFiles()

    with ThreadPoolExecutor(max_workers=2) as executor:
        archives = list(
            executor.map(
                lambda job_id: job_files.archive(job_id, output_root, archive_root),
                ("job-a", "job-b"),
            )
        )

    assert {archive.name for archive in archives} == {"job-a.zip", "job-b.zip"}
