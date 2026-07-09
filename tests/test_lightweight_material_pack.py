import zipfile
from pathlib import Path

from bid_knowledge.export.lightweight_material_pack import export_lightweight_material_pack


def test_export_lightweight_material_pack_defaults_to_material_markdown_and_images(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs" / "structure_run"
    material_dir = output_dir / "modules" / "补充文件" / "材料A"
    image_dir = material_dir / "image_items"
    table_dir = material_dir / "table_items"
    text_dir = material_dir / "text_items"
    original_dir = material_dir / "original"
    image_dir.mkdir(parents=True)
    table_dir.mkdir()
    text_dir.mkdir()
    original_dir.mkdir()
    (material_dir / "material.md").write_text("| 字段 | 值 |\n| --- | --- |\n| 名称 | 材料A |\n\n![图1](image_items/图1.png)\n", encoding="utf-8")
    (material_dir / "material_meta.json").write_text("{}", encoding="utf-8")
    (image_dir / "图1.png").write_bytes(b"png")
    (image_dir / "图1.json").write_text("{}", encoding="utf-8")
    (table_dir / "表1.json").write_text('{"rows":[["字段","值"],["名称","材料A"]]}', encoding="utf-8")
    (text_dir / "材料A.md").write_text("text", encoding="utf-8")
    (original_dir / "source_pages.pdf").write_bytes(b"pdf")

    result = export_lightweight_material_pack(output_dir)

    package_dir = Path(result["package_dir"])
    zip_path = Path(result["zip_path"])
    assert result["material_count"] == 1
    assert result["image_count"] == 1
    assert result["table_count"] == 0
    assert result["image_json_count"] == 0
    assert result["ordered_material_count"] == 0
    assert (package_dir / "modules" / "补充文件" / "材料A" / "material.md").exists()
    assert (package_dir / "modules" / "补充文件" / "材料A" / "image_items" / "图1.png").exists()
    assert not (package_dir / "modules" / "补充文件" / "材料A" / "table_items" / "表1.json").exists()
    assert not (package_dir / "modules" / "补充文件" / "材料A" / "image_items" / "图1.json").exists()
    assert not (package_dir / "modules" / "补充文件" / "材料A" / "material_meta.json").exists()
    assert not (package_dir / "modules" / "补充文件" / "材料A" / "text_items").exists()
    assert not (package_dir / "modules" / "补充文件" / "材料A" / "original").exists()

    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert "modules/补充文件/材料A/material.md" in names
    assert "modules/补充文件/材料A/image_items/图1.png" in names
    assert "modules/补充文件/材料A/table_items/表1.json" not in names
    assert "modules/补充文件/材料A/image_items/图1.json" not in names


def test_export_lightweight_material_pack_optional_json_and_trace_files(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs" / "structure_run"
    material_dir = output_dir / "modules" / "补充文件" / "材料A"
    image_dir = material_dir / "image_items"
    table_dir = material_dir / "table_items"
    parsed_table_dir = output_dir / "parsed" / "table_regions"
    image_dir.mkdir(parents=True)
    table_dir.mkdir()
    parsed_table_dir.mkdir(parents=True)
    (material_dir / "material.md").write_text("# 材料A\n", encoding="utf-8")
    (material_dir / "ordered_material.json").write_text("{}", encoding="utf-8")
    (image_dir / "图1.png").write_bytes(b"png")
    (image_dir / "图1.json").write_text("{}", encoding="utf-8")
    (table_dir / "表1.json").write_text('{"rows":[["字段","值"]]}', encoding="utf-8")
    (output_dir / "pdf_toc_pipeline_manifest.json").write_text("{}", encoding="utf-8")
    (output_dir / "parsed" / "tables.json").write_text("[]", encoding="utf-8")
    (parsed_table_dir / "table_candidates.json").write_text("[]", encoding="utf-8")

    result = export_lightweight_material_pack(
        output_dir,
        include_table_json=True,
        include_image_json=True,
        include_ordered_material_json=True,
        include_manifest=True,
        include_parsed_tables=True,
        include_table_candidates=True,
    )

    package_dir = Path(result["package_dir"])
    assert result["table_count"] == 1
    assert result["image_json_count"] == 1
    assert result["ordered_material_count"] == 1
    assert result["manifest_count"] == 1
    assert result["parsed_tables_count"] == 1
    assert result["table_candidates_count"] == 1
    assert (package_dir / "modules" / "补充文件" / "材料A" / "table_items" / "表1.json").exists()
    assert (package_dir / "modules" / "补充文件" / "材料A" / "image_items" / "图1.json").exists()
    assert (package_dir / "modules" / "补充文件" / "材料A" / "ordered_material.json").exists()
    assert (package_dir / "pdf_toc_pipeline_manifest.json").exists()
    assert (package_dir / "parsed" / "tables.json").exists()
    assert (package_dir / "parsed" / "table_regions" / "table_candidates.json").exists()
