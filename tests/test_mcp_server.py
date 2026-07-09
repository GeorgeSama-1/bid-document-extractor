import json
from pathlib import Path

from bid_knowledge.service.mcp_server import get_material_context, get_project_material_context, list_materials


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def make_material(
    outputs: Path,
    run_name: str,
    material_path: str,
    *,
    title: str,
    section_path: str,
    markdown: str,
    page_no: int = 1,
) -> None:
    material_dir = outputs / run_name / "modules" / material_path
    material_dir.mkdir(parents=True, exist_ok=True)
    (material_dir / "material.md").write_text(markdown, encoding="utf-8")
    write_json(
        material_dir / "material_meta.json",
        {
            "material_title": title,
            "section_path": section_path,
            "material_path": section_path,
        },
    )
    write_json(
        material_dir / "ordered_material.json",
        {
            "material_title": title,
            "section_path": section_path,
            "items": [{"type": "table", "item_type": "table", "table_title": f"{title}_表1", "page_no": page_no}],
        },
    )


def test_mcp_get_material_context_returns_markdown(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    make_material(
        outputs,
        "tech_run",
        "技术文件/技术偏差表",
        title="技术偏差表",
        section_path="技术文件 / 技术偏差表",
        markdown="# 技术偏差表\n\n| 序号 | 偏差事项 |\n| --- | --- |\n",
        page_no=8,
    )

    result = get_material_context(
        run_name="tech_run",
        section_path="技术文件 / 技术偏差表",
        outputs_dir=outputs,
    )

    assert result["status"] == "matched"
    assert result["match_type"] == "exact_path"
    assert result["source_pages"] == [8]
    assert "| 序号 | 偏差事项 |" in result["content_markdown"]


def test_mcp_list_materials_returns_run_index(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    make_material(
        outputs,
        "tech_run",
        "技术文件/技术偏差表",
        title="技术偏差表",
        section_path="技术文件 / 技术偏差表",
        markdown="# 技术偏差表\n",
    )

    result = list_materials(run_name="tech_run", outputs_dir=outputs)

    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["materials"][0]["title"] == "技术偏差表"


def test_mcp_get_project_material_context_routes_to_configured_run(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    config_path = tmp_path / "material_projects.json"
    write_json(
        config_path,
        {
            "gansu_2026": {
                "runs": {
                    "商务文件": "business_run",
                    "技术文件": "tech_run",
                }
            }
        },
    )
    make_material(
        outputs,
        "tech_run",
        "技术文件/技术偏差表",
        title="技术偏差表",
        section_path="技术文件 / 技术偏差表",
        markdown="# 技术偏差表\n\n技术内容",
    )

    result = get_project_material_context(
        project_id="gansu_2026",
        section_path="技术文件 / 技术偏差表",
        outputs_dir=outputs,
        projects_config_path=config_path,
    )

    assert result["status"] == "matched"
    assert result["file_type"] == "技术文件"
    assert result["run_name"] == "tech_run"
    assert "技术内容" in result["content_markdown"]
