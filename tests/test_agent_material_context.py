import json
from pathlib import Path

from bid_knowledge.service.agent_material_context import AgentMaterialContextService, ProjectMaterialContextService


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
            "items": [
                {
                    "type": "table",
                    "item_type": "table",
                    "table_title": f"{title}_表1",
                    "page_no": page_no,
                },
                {
                    "type": "text",
                    "item_type": "text",
                    "text": "表格之后说明。",
                    "page_no": page_no,
                },
            ],
        },
    )


def test_get_context_matches_exact_section_path(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    make_material(
        outputs,
        "tech_run",
        "技术文件/技术偏差表",
        title="技术偏差表",
        section_path="技术文件 / 技术偏差表",
        markdown="# 技术偏差表\n\n| 序号 | 偏差事项 |\n| --- | --- |\n",
    )

    result = AgentMaterialContextService(outputs).get_context(
        "tech_run",
        section_path="技术文件 / 技术偏差表",
    )

    assert result["status"] == "matched"
    assert result["match_type"] == "exact_path"
    assert result["selected"]["title"] == "技术偏差表"
    assert "| 序号 | 偏差事项 |" in result["content_markdown"]
    assert result["source_pages"] == [1]
    assert result["evidence"][0]["type"] == "table"


def test_get_context_matches_normalized_path_spacing_and_punctuation(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    make_material(
        outputs,
        "tech_run",
        "技术文件/3.8.1、 经营状况",
        title="3.8.1、 经营状况",
        section_path="技术文件 / 3.8.1、 经营状况",
        markdown="# 经营状况\n\n内容",
    )

    result = AgentMaterialContextService(outputs).get_context(
        "tech_run",
        section_path="技术文件/3.8.1经营状况",
    )

    assert result["status"] == "matched"
    assert result["match_type"] == "normalized_path"
    assert result["selected"]["section_path"] == "技术文件 / 3.8.1、 经营状况"


def test_get_context_matches_leaf_title_when_path_differs(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    make_material(
        outputs,
        "tech_run",
        "技术文件/1、技术偏差表",
        title="1、技术偏差表",
        section_path="技术文件 / 1、技术偏差表",
        markdown="# 技术偏差表\n\n内容",
    )

    result = AgentMaterialContextService(outputs).get_context(
        "tech_run",
        section_path="技术文件 / 技术偏差表",
        title="技术偏差表",
    )

    assert result["status"] == "matched"
    assert result["match_type"] == "title"
    assert result["selected"]["title"] == "1、技术偏差表"


def test_get_context_matches_similar_title(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    make_material(
        outputs,
        "tech_run",
        "技术文件/技术参数偏差表",
        title="技术参数偏差表",
        section_path="技术文件 / 技术参数偏差表",
        markdown="# 技术参数偏差表\n\n内容",
    )

    result = AgentMaterialContextService(outputs).get_context(
        "tech_run",
        title="技术偏差表",
    )

    assert result["status"] == "matched"
    assert result["match_type"] == "fuzzy"
    assert result["selected"]["title"] == "技术参数偏差表"


def test_get_context_returns_ambiguous_candidates_for_close_titles(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    make_material(
        outputs,
        "tech_run",
        "技术文件/技术偏差表",
        title="技术偏差表",
        section_path="技术文件 / 技术偏差表",
        markdown="# 技术偏差表\n\n内容",
    )
    make_material(
        outputs,
        "tech_run",
        "技术文件/技术参数偏差表",
        title="技术参数偏差表",
        section_path="技术文件 / 技术参数偏差表",
        markdown="# 技术参数偏差表\n\n内容",
    )

    result = AgentMaterialContextService(outputs).get_context(
        "tech_run",
        title="技术偏差",
    )

    assert result["status"] == "ambiguous"
    assert result["match_type"] == "fuzzy"
    assert [candidate["title"] for candidate in result["candidates"]] == ["技术偏差表", "技术参数偏差表"]


def test_project_context_routes_by_section_path_file_type(tmp_path: Path) -> None:
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
        "business_run",
        "商务文件/商务偏差表",
        title="商务偏差表",
        section_path="商务文件 / 商务偏差表",
        markdown="# 商务偏差表\n",
    )
    make_material(
        outputs,
        "tech_run",
        "技术文件/技术偏差表",
        title="技术偏差表",
        section_path="技术文件 / 技术偏差表",
        markdown="# 技术偏差表\n\n技术内容",
        page_no=5,
    )

    result = ProjectMaterialContextService(outputs, config_path).get_project_context(
        "gansu_2026",
        section_path="技术文件 / 技术偏差表",
    )

    assert result["status"] == "matched"
    assert result["project_id"] == "gansu_2026"
    assert result["file_type"] == "技术文件"
    assert result["run_name"] == "tech_run"
    assert result["source_pages"] == [5]
    assert "技术内容" in result["content_markdown"]


def test_project_context_searches_all_runs_when_only_title_is_available(tmp_path: Path) -> None:
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
        "business_run",
        "商务文件/商务偏差表",
        title="商务偏差表",
        section_path="商务文件 / 商务偏差表",
        markdown="# 商务偏差表\n",
    )
    make_material(
        outputs,
        "tech_run",
        "技术文件/技术偏差表",
        title="技术偏差表",
        section_path="技术文件 / 技术偏差表",
        markdown="# 技术偏差表\n",
    )

    result = ProjectMaterialContextService(outputs, config_path).get_project_context(
        "gansu_2026",
        title="技术偏差表",
    )

    assert result["status"] == "matched"
    assert result["file_type"] == "技术文件"
    assert result["run_name"] == "tech_run"
