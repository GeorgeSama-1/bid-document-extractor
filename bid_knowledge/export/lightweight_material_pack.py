from __future__ import annotations

import posixpath
import shutil
import zipfile
from pathlib import Path


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
TABLE_EXTENSIONS = {".json"}


def _is_included_file(
    source_file: Path,
    source_root: Path,
    excluded_roots: frozenset[str],
) -> bool:
    if source_file.is_symlink() or not source_file.is_file():
        return False
    try:
        relative = source_file.relative_to(source_root)
        source_file.resolve(strict=True).relative_to(source_root.resolve(strict=True))
    except (FileNotFoundError, ValueError):
        return False
    return not relative.parts or relative.parts[0] not in excluded_roots


def _copy_material_files(
    source_root: Path,
    package_root: Path,
    excluded_roots: frozenset[str],
    include_root_material_md: bool,
) -> int:
    copied_count = 0
    for material_md in source_root.rglob("material.md"):
        if not _is_included_file(material_md, source_root, excluded_roots):
            continue
        relative_path = material_md.relative_to(source_root)
        if not include_root_material_md and relative_path.parts == ("material.md",):
            continue
        target_path = package_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(material_md, target_path)
        copied_count += 1
    return copied_count


def _copy_named_files(
    source_root: Path,
    package_root: Path,
    filename: str,
    excluded_roots: frozenset[str],
) -> int:
    copied_count = 0
    for source_file in source_root.rglob(filename):
        if not _is_included_file(source_file, source_root, excluded_roots):
            continue
        relative_path = source_file.relative_to(source_root)
        target_path = package_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_path)
        copied_count += 1
    return copied_count


def _copy_image_items(
    source_root: Path,
    package_root: Path,
    excluded_roots: frozenset[str],
) -> int:
    copied_count = 0
    for image_dir in source_root.rglob("image_items"):
        if not image_dir.is_dir():
            continue
        for image_file in image_dir.iterdir():
            if (
                not _is_included_file(image_file, source_root, excluded_roots)
                or image_file.suffix.lower() not in IMAGE_EXTENSIONS
            ):
                continue
            relative_path = image_file.relative_to(source_root)
            target_path = package_root / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(image_file, target_path)
            copied_count += 1
    return copied_count


def _copy_image_item_json(
    source_root: Path,
    package_root: Path,
    excluded_roots: frozenset[str],
) -> int:
    copied_count = 0
    for image_dir in source_root.rglob("image_items"):
        if not image_dir.is_dir():
            continue
        for image_file in image_dir.iterdir():
            if (
                not _is_included_file(image_file, source_root, excluded_roots)
                or image_file.suffix.lower() != ".json"
            ):
                continue
            relative_path = image_file.relative_to(source_root)
            target_path = package_root / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(image_file, target_path)
            copied_count += 1
    return copied_count


def _copy_table_items(
    source_root: Path,
    package_root: Path,
    excluded_roots: frozenset[str],
) -> int:
    copied_count = 0
    for table_dir in source_root.rglob("table_items"):
        if not table_dir.is_dir():
            continue
        for table_file in table_dir.iterdir():
            if (
                not _is_included_file(table_file, source_root, excluded_roots)
                or table_file.suffix.lower() not in TABLE_EXTENSIONS
            ):
                continue
            relative_path = table_file.relative_to(source_root)
            target_path = package_root / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(table_file, target_path)
            copied_count += 1
    return copied_count


def _copy_root_file(source_root: Path, package_root: Path, relative_path: str) -> int:
    source_file = source_root / relative_path
    if not source_file.is_file():
        return 0
    target_path = package_root / relative_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_file, target_path)
    return 1


def _write_zip(package_root: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(package_root))


def _auxiliary_module_roots(modules_dir: Path) -> frozenset[str]:
    roots: set[str] = set()
    for metadata in modules_dir.rglob("module_meta.json"):
        if not _is_included_file(metadata, modules_dir, frozenset()):
            continue
        relative = metadata.relative_to(modules_dir)
        if len(relative.parts) >= 2:
            roots.add(relative.parts[0])
    return frozenset(roots)


def _rewrite_material_links(source_root: Path, package_content_root: Path) -> None:
    copied_paths = [
        path.relative_to(package_content_root)
        for path in package_content_root.rglob("*")
        if path.is_file() and not path.is_symlink()
    ]
    for material_md in package_content_root.rglob("material.md"):
        material_relative = material_md.relative_to(package_content_root)
        material_parent = material_relative.parent.as_posix()
        replacements: list[tuple[str, str]] = []
        for copied_relative in copied_paths:
            copied_posix = copied_relative.as_posix()
            target = posixpath.relpath(copied_posix, material_parent)
            source_absolute = source_root.joinpath(*copied_relative.parts).resolve()
            replacements.extend(
                (
                    (str(source_absolute), target),
                    (source_absolute.as_posix(), target),
                    (copied_posix, target),
                    (f"modules/{copied_posix}", target),
                )
            )
        markdown = material_md.read_text(encoding="utf-8", errors="replace")
        for source, target in sorted(
            replacements, key=lambda item: len(item[0]), reverse=True
        ):
            markdown = markdown.replace(source, target)
            markdown = markdown.replace(source.replace("/", "\\"), target)
        material_md.write_text(markdown, encoding="utf-8")


def export_lightweight_material_pack(
    output_dir: str | Path,
    *,
    package_dir: str | Path | None = None,
    zip_path: str | Path | None = None,
    include_material_md: bool = True,
    include_images: bool = True,
    include_table_json: bool = False,
    include_image_json: bool = False,
    include_ordered_material_json: bool = False,
    include_manifest: bool = False,
    include_parsed_tables: bool = False,
    include_table_candidates: bool = False,
    package_subdir: str | Path = "modules",
    rewrite_material_links: bool = False,
    exclude_module_indexes: bool = False,
    include_root_material_md: bool = True,
) -> dict[str, str | int]:
    source_root = Path(output_dir).resolve()
    modules_dir = source_root / "modules"
    if not modules_dir.exists():
        raise FileNotFoundError(f"Cannot find modules directory: {modules_dir}")
    if modules_dir.is_symlink() or not modules_dir.is_dir():
        raise ValueError(f"Unsafe modules directory: {modules_dir}")

    target_package_dir = Path(package_dir).resolve() if package_dir else source_root / "material_pack"
    target_zip_path = Path(zip_path).resolve() if zip_path else source_root / "material_pack.zip"

    if target_package_dir.exists():
        shutil.rmtree(target_package_dir)
    target_package_dir.mkdir(parents=True, exist_ok=True)

    relative_subdir = Path(package_subdir)
    if relative_subdir.is_absolute() or ".." in relative_subdir.parts:
        raise ValueError("package_subdir must be a safe relative path")
    package_modules_dir = target_package_dir / relative_subdir
    excluded_roots = (
        _auxiliary_module_roots(modules_dir)
        if exclude_module_indexes
        else frozenset()
    )
    material_count = (
        _copy_material_files(
            modules_dir,
            package_modules_dir,
            excluded_roots,
            include_root_material_md,
        )
        if include_material_md
        else 0
    )
    image_count = (
        _copy_image_items(modules_dir, package_modules_dir, excluded_roots)
        if include_images
        else 0
    )
    table_count = (
        _copy_table_items(modules_dir, package_modules_dir, excluded_roots)
        if include_table_json
        else 0
    )
    image_json_count = (
        _copy_image_item_json(modules_dir, package_modules_dir, excluded_roots)
        if include_image_json
        else 0
    )
    ordered_material_count = (
        _copy_named_files(
            modules_dir,
            package_modules_dir,
            "ordered_material.json",
            excluded_roots,
        )
        if include_ordered_material_json
        else 0
    )
    manifest_count = _copy_root_file(source_root, target_package_dir, "pdf_toc_pipeline_manifest.json") if include_manifest else 0
    parsed_tables_count = _copy_root_file(source_root, target_package_dir, "parsed/tables.json") if include_parsed_tables else 0
    table_candidates_count = _copy_root_file(source_root, target_package_dir, "parsed/table_regions/table_candidates.json") if include_table_candidates else 0
    if rewrite_material_links and include_material_md:
        _rewrite_material_links(modules_dir, package_modules_dir)
    _write_zip(target_package_dir, target_zip_path)

    return {
        "source_dir": str(source_root),
        "package_dir": str(target_package_dir),
        "zip_path": str(target_zip_path),
        "material_count": material_count,
        "image_count": image_count,
        "table_count": table_count,
        "image_json_count": image_json_count,
        "ordered_material_count": ordered_material_count,
        "manifest_count": manifest_count,
        "parsed_tables_count": parsed_tables_count,
        "table_candidates_count": table_candidates_count,
    }
