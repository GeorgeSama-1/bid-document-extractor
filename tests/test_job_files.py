from __future__ import annotations

import os
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


def test_open_file_reads_nested_regular_file_from_secure_descriptor(tmp_path) -> None:
    output_root = tmp_path / "output"
    nested = output_root / "nested" / "result.json"
    nested.parent.mkdir(parents=True)
    nested.write_text('{"safe": true}', encoding="utf-8")

    with JobFiles().open_file(output_root, "nested/result.json") as opened:
        assert opened.read() == b'{"safe": true}'


def test_open_file_rejects_file_replaced_by_symlink_after_resolve(tmp_path) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    victim = output_root / "result.txt"
    outside = tmp_path / "outside.txt"
    victim.write_text("safe", encoding="utf-8")
    outside.write_text("secret", encoding="utf-8")
    job_files = JobFiles()
    assert job_files.resolve(output_root, "result.txt") == victim.resolve()
    victim.unlink()
    victim.symlink_to(outside)

    with pytest.raises((FileNotFoundError, ValueError, OSError)):
        job_files.open_file(output_root, "result.txt")


def test_open_file_rejects_intermediate_directory_replaced_by_symlink(
    tmp_path,
) -> None:
    output_root = tmp_path / "output"
    nested = output_root / "nested"
    outside_directory = tmp_path / "outside"
    nested.mkdir(parents=True)
    outside_directory.mkdir()
    (nested / "result.txt").write_text("safe", encoding="utf-8")
    (outside_directory / "result.txt").write_text("secret", encoding="utf-8")
    job_files = JobFiles()
    job_files.resolve(output_root, "nested/result.txt")
    (nested / "result.txt").unlink()
    nested.rmdir()
    nested.symlink_to(outside_directory, target_is_directory=True)

    with pytest.raises((FileNotFoundError, OSError)):
        job_files.open_file(output_root, "nested/result.txt")


def test_open_file_never_follows_concurrent_symlink_swaps(tmp_path) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    victim = output_root / "result.txt"
    swap = output_root / "swap-entry"
    outside = tmp_path / "outside.txt"
    victim.write_bytes(b"safe")
    outside.write_bytes(b"secret")
    stop = threading.Event()
    started = threading.Event()

    def swap_repeatedly() -> None:
        while not stop.is_set():
            swap.unlink(missing_ok=True)
            swap.symlink_to(outside)
            os.replace(swap, victim)
            started.set()
            swap.write_bytes(b"safe")
            os.replace(swap, victim)

    attacker = threading.Thread(target=swap_repeatedly)
    attacker.start()
    try:
        assert started.wait(timeout=5)
        for _ in range(500):
            try:
                with JobFiles().open_file(output_root, "result.txt") as opened:
                    assert opened.read() == b"safe"
            except (FileNotFoundError, OSError):
                pass
    finally:
        stop.set()
        attacker.join(timeout=5)
    assert not attacker.is_alive()


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


def test_archive_skips_file_replaced_by_symlink_before_secure_open(tmp_path) -> None:
    output_root = tmp_path / "output"
    archive_root = tmp_path / "archives"
    output_root.mkdir()
    victim = output_root / "victim.txt"
    outside = tmp_path / "outside.txt"
    victim.write_text("safe", encoding="utf-8")
    outside.write_text("secret", encoding="utf-8")
    swapped = False

    class SwappingJobFiles(JobFiles):
        def open_file(self, output_root: Path, relative_path: str):
            nonlocal swapped
            if relative_path == "victim.txt" and not swapped:
                victim.unlink()
                victim.symlink_to(outside)
                swapped = True
            return super().open_file(output_root, relative_path)

    archive = SwappingJobFiles().archive("swap-test", output_root, archive_root)

    assert swapped
    with zipfile.ZipFile(archive) as bundle:
        assert "victim.txt" not in bundle.namelist()
        assert b"secret" not in b"".join(bundle.read(name) for name in bundle.namelist())


def test_archive_removes_temporary_file_when_writing_fails(tmp_path) -> None:
    output_root = tmp_path / "output"
    archive_root = tmp_path / "archives"
    output_root.mkdir()
    (output_root / "result.txt").write_text("result", encoding="utf-8")

    class FailingJobFiles(JobFiles):
        def _write_archive(self, output_root: Path, temporary_path: Path) -> None:
            temporary_path.write_bytes(b"partial")
            raise RuntimeError("write failed")

    with pytest.raises(RuntimeError, match="write failed"):
        FailingJobFiles().archive("failed-job", output_root, archive_root)

    assert not (archive_root / "failed-job.zip").exists()
    assert list(archive_root.iterdir()) == []


def test_portably_unsafe_names_are_excluded_and_cannot_be_opened(tmp_path) -> None:
    output_root = tmp_path / "output"
    archive_root = tmp_path / "archives"
    output_root.mkdir()
    unsafe_names = ["..\\outside.txt", "C:outside.txt", "CON", "aux.txt", "trail. "]
    for name in unsafe_names:
        (output_root / name).write_text("secret", encoding="utf-8")
    (output_root / "safe.txt").write_text("safe", encoding="utf-8")
    job_files = JobFiles()

    assert [item.path for item in job_files.list(output_root)] == ["safe.txt"]
    for name in unsafe_names:
        with pytest.raises(ValueError, match="unsafe"):
            job_files.resolve(output_root, name)
        with pytest.raises(ValueError, match="unsafe"):
            job_files.open_file(output_root, name)

    archive = job_files.archive("portable", output_root, archive_root)
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.namelist() == ["safe.txt"]


@pytest.mark.parametrize(
    "job_id", ["../escape", "nested/escape", "", ".", "..", "CON"]
)
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


def test_completed_archive_locks_are_released_from_registry(tmp_path) -> None:
    output_root = tmp_path / "output"
    archive_root = tmp_path / "archives"
    output_root.mkdir()
    (output_root / "result.txt").write_text("result", encoding="utf-8")
    job_files = JobFiles()

    for index in range(20):
        job_files.archive(f"job-{index}", output_root, archive_root)

    assert job_files._archive_locks == {}


def test_material_archive_matches_history_layout_and_filters_intermediates(
    tmp_path,
) -> None:
    output_root = tmp_path / "output"
    for chapter in ("1、 商务偏差表", "2、 投标保证金", "4、 法定代表人授权委托书"):
        chapter_dir = output_root / "modules" / chapter
        chapter_dir.mkdir(parents=True)
        (chapter_dir / "material.md").write_text(f"# {chapter}\n", encoding="utf-8")
    material = output_root / "modules" / "3、 补充文件" / "3.3、 投标人基本情况表"
    leaf = material / "3.3.2、 投标人基本情况表2"
    (leaf / "image_items").mkdir(parents=True)
    (leaf / "table_items").mkdir()
    image_path = leaf / "image_items" / "证书.jpeg"
    table_path = leaf / "table_items" / "表1.json"
    (leaf / "material.md").write_text(
        f"# 投标人基本情况表2\n\n![证书]({image_path})\n", encoding="utf-8"
    )
    image_path.write_bytes(b"image")
    (leaf / "image_items" / "证书.json").write_text("{\"meta\": true}")
    table_path.write_text('{"rows": []}', encoding="utf-8")
    (leaf / "ordered_material.json").write_text("{}")
    (output_root / "modules" / "material.md").write_text("root navigation")
    auxiliary = output_root / "modules" / "商务文件" / "1、 商务偏差表"
    auxiliary.mkdir(parents=True)
    (auxiliary / "module_meta.json").write_text("{}")
    (auxiliary / "material.md").write_text("auxiliary navigation")
    (output_root / "parsed").mkdir()
    (output_root / "parsed" / "tables.json").write_text("{}")

    archive = JobFiles().material_archive(
        "job-1",
        output_root,
        tmp_path / "archives",
        package_name="material.pdf",
    )

    assert archive.name == "job-1.materials-v3.zip"
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.namelist() == [
            "material/history/1、 商务偏差表/material.md",
            "material/history/2、 投标保证金/material.md",
            "material/history/3、 补充文件/3.3、 投标人基本情况表/3.3.2、 投标人基本情况表2/image_items/证书.jpeg",
            "material/history/3、 补充文件/3.3、 投标人基本情况表/3.3.2、 投标人基本情况表2/material.md",
            "material/history/4、 法定代表人授权委托书/material.md",
        ]
        markdown = bundle.read(bundle.namelist()[3]).decode()
        assert markdown == "# 投标人基本情况表2\n\n![证书](image_items/证书.jpeg)\n"
        assert not any("/table_items/" in name for name in bundle.namelist())


def test_material_archive_rejects_symlinked_modules_root(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "material.md").write_text("secret")
    safe_output = tmp_path / "safe-output"
    safe_output.mkdir()
    (safe_output / "modules").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        JobFiles().material_archive(
            "job-3",
            safe_output,
            tmp_path / "archives",
            package_name="material.pdf",
        )
