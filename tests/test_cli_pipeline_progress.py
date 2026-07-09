from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from bid_knowledge import cli
from bid_knowledge.parsing.table_region_detector import CandidateTableGroup, CandidateTableRegion
from bid_knowledge.schemas.models import (
    ManualConfig,
    PageMaterialItem,
    ParsedTable,
    PdfTextBlock,
    ProcessingPlan,
    ProcessingPlanItem,
    ReconstructedSection,
    SectionMatchResult,
    SectionRule,
)
from bid_knowledge.utils.io_utils import write_json


def test_pipeline_prints_stage_progress_and_empty_ocr_notice(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    rule = SectionRule(rule_id="rule-1", section_path="商务文件 / 证明材料")
    plan = ProcessingPlan(
        company_id="demo_company",
        document_id="demo_document",
        file_type="商务文件",
        sections=[
            ProcessingPlanItem(
                rule_id="rule-1",
                section_path="商务文件 / 证明材料",
                use_ocr=True,
                expected_pages=[],
            )
        ],
    )
    block = PdfTextBlock(
        block_id="block-1",
        page_no=1,
        text="证明材料",
        bbox=[],
        block_no=0,
    )
    section = ReconstructedSection(
        section_id="section-1",
        title="证明材料",
        normalized_title="证明材料",
        level=1,
        page_start=1,
        page_end=1,
    )
    match = SectionMatchResult(
        rule_id="rule-1",
        rule_section_path="商务文件 / 证明材料",
        matched=True,
    )

    def fake_load_rules_from_excel(*args, out_path=None, report_path=None, **kwargs):
        write_json(out_path, [rule])
        write_json(report_path, {"loaded": 1})
        return [rule], {"loaded": 1}

    def fake_parse_pdf(*args, out_dir=None, **kwargs):
        out = Path(out_dir)
        write_json(out / "document_meta.json", {"page_count": 1})
        write_json(out / "toc.json", [])
        write_json(out / "text_blocks.json", [block])
        write_json(out / "images.json", [])

    monkeypatch.setattr(cli, "load_rules_from_excel", fake_load_rules_from_excel)
    monkeypatch.setattr(cli, "load_manual_config", lambda *_: ManualConfig(file_type="商务文件"))
    monkeypatch.setattr(cli, "build_processing_plan", lambda *_: plan)
    monkeypatch.setattr(cli, "parse_pdf", fake_parse_pdf)
    monkeypatch.setattr(cli, "extract_tables", lambda *_, **__: [])
    monkeypatch.setattr(cli, "merge_ocr_results", lambda blocks, _ocr, out_path=None: write_json(out_path, blocks) or blocks)
    monkeypatch.setattr(cli, "build_sections", lambda *_, out_path=None, **__: write_json(out_path, [section]) or [section])
    monkeypatch.setattr(cli, "match_sections", lambda *_, out_path=None, **__: write_json(out_path, [match]) or [match])
    monkeypatch.setattr(cli, "extract_candidates", lambda *_, out_json=None, out_csv=None, **__: write_json(out_json, []) or Path(out_csv).write_text("") or [])
    monkeypatch.setattr(cli, "package_module_artifacts", lambda **_: {"sections": []})
    monkeypatch.setattr(cli, "build_chunks", lambda _candidates, out_path=None: Path(out_path).write_text("") or [])
    monkeypatch.setattr(cli, "evaluate_retrieval", lambda *_, **__: {"skipped": True})
    monkeypatch.setattr(
        cli,
        "build_combined_page_material_stream",
        lambda **_: [PageMaterialItem(item_id="block-1", item_type="text", source_type="pdf_text", page_no=1, reading_order=1, top_y=0.0, bbox=[], text="证明材料")],
    )

    result = runner.invoke(
        cli.app,
        [
            "pipeline",
            "--rules-xlsx",
            "rules.xlsx",
            "--pdf",
            "document.pdf",
            "--out-dir",
            str(tmp_path / "out"),
            "--enable-ocr",
            "true",
        ],
    )

    assert result.exit_code == 0
    assert "[1/13] Loading Excel rules" in result.output
    assert "[5/13] OCR enabled but no explicit OCR pages were configured; skipping OCR requests." in result.output
    assert "[6/13] PP-StructureV3 disabled; using PDF-native parsing only" in result.output
    assert "[7/13] Building page material stream" in result.output
    assert "Pipeline completed ->" in result.output


def test_run_pp_structure_command_reports_page_count(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    out_path = tmp_path / "pp_structure_results.json"
    fake_results = [{"res": {"parsing_res_list": []}, "page_index": 0}]

    def fake_run_pp_structure(*_, out_path=None, **__):
        write_json(out_path, fake_results)
        return fake_results

    monkeypatch.setattr(
        cli,
        "run_pp_structure",
        fake_run_pp_structure,
    )

    result = runner.invoke(
        cli.app,
        [
            "run-pp-structure",
            "--input",
            "document.pdf",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0
    assert "PP-StructureV3 finished for 1 pages" in result.output


def test_pdf_toc_pipeline_merges_pdf_tables_when_pp_structure_misses_them(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    block = PdfTextBlock(
        block_id="block-1",
        page_no=1,
        text="1、测试章节",
        bbox=[10, 20, 200, 40],
        block_no=1,
    )
    candidate = cli.ReusableCandidate(
        candidate_id="cand-1",
        company_id="pdf",
        document_id="demo",
        rule_id="toc-1",
        section_path="商务文件 / 1、测试章节",
        title="1、测试章节",
        content="",
        candidate_type="attachment",
        reuse_method="附件召回",
        reuse_level="long_term",
        enter_long_term_library=True,
        source_file="demo.pdf",
        source_page=1,
        source_page_end=1,
        source_container_title="1、测试章节",
    )
    pp_table = ParsedTable(
        table_id="pp-table-1",
        page_no=1,
        rows=[["字段", "内容"]],
        bbox=[20, 80, 400, 200],
        source_type="pp_structure_table",
    )
    pdf_table = ParsedTable(
        table_id="pdf-table-1",
        page_no=1,
        rows=[["补充", "表格"]],
        bbox=[20, 240, 400, 320],
        source_type="pdf_table",
    )
    captured: dict[str, object] = {}

    def fake_parse_pdf(*_, out_dir=None, **__):
        out = Path(out_dir)
        write_json(out / "text_blocks.json", [block])
        write_json(out / "images.json", [])
        return {"toc": [{"title": "1、测试章节", "page": 1, "level": 1}], "document_meta": {"page_count": 1}}

    monkeypatch.setattr(cli, "parse_pdf", fake_parse_pdf)
    monkeypatch.setattr(cli, "extract_tables", lambda *_, **__: [pdf_table])
    monkeypatch.setattr(cli, "run_pp_structure", lambda *_, **__: [{"res": {"page_index": 0}, "page_index": 0}])
    monkeypatch.setattr(cli, "extract_pp_structure_tables", lambda *_, **__: [pp_table])
    monkeypatch.setattr(
        cli,
        "detect_candidate_table_regions",
        lambda **_: [
            CandidateTableRegion(
                region_id="region-pp",
                page_no=1,
                bbox=pp_table.bbox,
                detectors=["pp_structure"],
                confidence=0.7,
            ),
            CandidateTableRegion(
                region_id="region-pdf",
                page_no=1,
                bbox=pdf_table.bbox,
                detectors=["pdfplumber"],
                confidence=0.7,
                source_table_ids=[pdf_table.table_id],
            ),
        ],
    )
    monkeypatch.setattr(cli, "group_candidate_table_regions", lambda regions, out_dir=None: [CandidateTableGroup(group_id="group-1", start_page=1, end_page=1, regions=regions)])
    monkeypatch.setattr(cli, "groups_to_parsed_tables", lambda _groups, _source_tables, **_: [pp_table, pdf_table])
    monkeypatch.setattr(cli, "build_layout_masks", lambda *_: [])
    monkeypatch.setattr(cli, "build_toc_leaf_candidates", lambda **_: [candidate])
    monkeypatch.setattr(cli, "toc_leaf_section_paths", lambda _: [candidate.section_path])
    monkeypatch.setattr(cli, "top_level_modules_from_toc_candidates", lambda _: ["1、测试章节"])
    monkeypatch.setattr(cli, "build_combined_page_material_stream", lambda **_: [])

    def fake_package_module_artifacts(**kwargs):
        captured["tables"] = kwargs["tables"]
        return {"sections": []}

    monkeypatch.setattr(cli, "package_module_artifacts", fake_package_module_artifacts)

    result = runner.invoke(
        cli.app,
        [
            "pdf-toc-pipeline",
            "--pdf",
            "demo.pdf",
            "--out-dir",
            str(tmp_path / "out"),
            "--enable-pp-structure",
            "true",
        ],
    )

    assert result.exit_code == 0
    assert captured["tables"] == [pp_table, pdf_table]


def test_pdf_toc_pipeline_enhances_tables_with_vlm_when_enabled(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    block = PdfTextBlock(block_id="block-1", page_no=1, text="1、测试章节", bbox=[10, 20, 200, 40], block_no=1)
    table = ParsedTable(table_id="table-1", page_no=1, rows=[], bbox=[20, 80, 400, 200])
    enhanced_table = ParsedTable(
        table_id="table-1",
        page_no=1,
        rows=[["272608"]],
        bbox=[20, 80, 400, 200],
        table_model_source="vlm",
    )
    candidate = cli.ReusableCandidate(
        candidate_id="cand-1",
        company_id="pdf",
        document_id="demo",
        rule_id="toc-1",
        section_path="商务文件 / 1、测试章节",
        title="1、测试章节",
        content="",
        candidate_type="attachment",
        reuse_method="附件召回",
        reuse_level="long_term",
        enter_long_term_library=True,
        source_file="demo.pdf",
        source_page=1,
        source_page_end=1,
        source_container_title="1、测试章节",
    )
    captured: dict[str, object] = {}

    def fake_parse_pdf(*_, out_dir=None, **__):
        out = Path(out_dir)
        write_json(out / "text_blocks.json", [block])
        write_json(out / "images.json", [])
        return {"toc": [{"title": "1、测试章节", "page": 1, "level": 1}], "document_meta": {"page_count": 1}}

    monkeypatch.setattr(cli, "parse_pdf", fake_parse_pdf)
    monkeypatch.setattr(cli, "run_pp_structure", lambda *_, **__: [])
    monkeypatch.setattr(cli, "build_layout_masks", lambda *_: [])
    monkeypatch.setattr(cli, "extract_tables", lambda *_, **__: [table])
    monkeypatch.setattr(cli, "extract_pp_structure_tables", lambda *_, **__: [])
    monkeypatch.setattr(cli, "merge_pp_and_pdf_tables", lambda _pp, pdf: pdf)
    monkeypatch.setattr(
        cli,
        "detect_candidate_table_regions",
        lambda **_: [
            CandidateTableRegion(
                region_id="region-table-1",
                page_no=1,
                bbox=table.bbox,
                detectors=["pdfplumber"],
                confidence=0.7,
                source_table_ids=[table.table_id],
            )
        ],
    )
    monkeypatch.setattr(cli, "group_candidate_table_regions", lambda regions, out_dir=None: [CandidateTableGroup(group_id="group-1", start_page=1, end_page=1, regions=regions)])
    monkeypatch.setattr(cli, "groups_to_parsed_tables", lambda _groups, _source_tables, **_: [table])
    monkeypatch.setattr(cli, "build_toc_leaf_candidates", lambda **_: [candidate])
    monkeypatch.setattr(cli, "toc_leaf_section_paths", lambda _: [candidate.section_path])
    monkeypatch.setattr(cli, "top_level_modules_from_toc_candidates", lambda _: ["1、测试章节"])
    monkeypatch.setattr(cli, "build_combined_page_material_stream", lambda **_: [])

    def fake_enhance_tables_with_vlm(**kwargs):
        captured["vlm_endpoint"] = kwargs["endpoint"]
        captured["vlm_model"] = kwargs["model"]
        captured["vlm_api_key"] = kwargs["api_key"]
        captured["vlm_api_key_env"] = kwargs["api_key_env"]
        captured["vlm_timeout"] = kwargs["request_timeout"]
        captured["vlm_max_tokens"] = kwargs["max_tokens"]
        captured["vlm_workers"] = kwargs["workers"]
        captured["vlm_incremental_out_path"] = kwargs["incremental_out_path"]
        return [enhanced_table]

    def fake_package_module_artifacts(**kwargs):
        captured["tables"] = kwargs["tables"]
        return {"sections": []}

    monkeypatch.setattr(cli, "enhance_tables_with_vlm", fake_enhance_tables_with_vlm)
    monkeypatch.setattr(cli, "package_module_artifacts", fake_package_module_artifacts)

    result = runner.invoke(
        cli.app,
        [
            "pdf-toc-pipeline",
            "--pdf",
            "demo.pdf",
            "--out-dir",
            str(tmp_path / "out"),
            "--enable-pp-structure",
            "true",
            "--enable-vlm-table",
            "true",
            "--vlm-table-endpoint",
            "http://172.20.0.160:8118/v1/chat/completions",
            "--vlm-table-model",
            "PaddleOCR-VL-1.5",
            "--vlm-table-api-key-env",
            "QWEN_VL_API_KEY",
            "--vlm-table-timeout",
            "300",
            "--vlm-table-max-tokens",
            "4096",
            "--vlm-table-workers",
            "2",
        ],
    )

    assert result.exit_code == 0
    assert captured["vlm_endpoint"] == "http://172.20.0.160:8118/v1/chat/completions"
    assert captured["vlm_model"] == "PaddleOCR-VL-1.5"
    assert captured["vlm_api_key"] is None
    assert captured["vlm_api_key_env"] == "QWEN_VL_API_KEY"
    assert captured["vlm_timeout"] == 300
    assert captured["vlm_max_tokens"] == 4096
    assert captured["vlm_workers"] == 2
    assert Path(captured["vlm_incremental_out_path"]).name == "tables.json"
    assert captured["tables"] == [enhanced_table]


def test_pdf_toc_pipeline_writes_table_candidate_trace_before_packaging(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    block = PdfTextBlock(block_id="block-1", page_no=1, text="1、测试章节", bbox=[10, 20, 200, 40], block_no=1)
    table = ParsedTable(table_id="pdf-table-1", page_no=1, rows=[["字段", "内容"]], bbox=[20, 80, 400, 200])
    region = CandidateTableRegion(
        region_id="region-1",
        page_no=1,
        bbox=[20, 80, 400, 200],
        expanded_bbox=[10, 70, 410, 210],
        detectors=["pdfplumber", "pymupdf_lines"],
        confidence=0.85,
        crop_image_path=str(tmp_path / "out" / "parsed" / "table_regions" / "debug_table_regions" / "region-1.png"),
        source_table_ids=["pdf-table-1"],
    )
    region_table = ParsedTable(
        table_id="region-1",
        page_no=1,
        rows=[["字段", "内容"]],
        bbox=[10, 70, 410, 210],
        table_image_path=region.crop_image_path,
    )
    candidate = cli.ReusableCandidate(
        candidate_id="cand-1",
        company_id="pdf",
        document_id="demo",
        rule_id="toc-1",
        section_path="商务文件 / 1、测试章节",
        title="1、测试章节",
        content="",
        candidate_type="attachment",
        reuse_method="附件召回",
        reuse_level="long_term",
        enter_long_term_library=True,
        source_file="demo.pdf",
        source_page=1,
        source_page_end=1,
        source_container_title="1、测试章节",
    )
    captured: dict[str, object] = {}
    elapsed_ticks = iter([100.0, 3825.25])

    def fake_parse_pdf(*_, out_dir=None, **__):
        out = Path(out_dir)
        write_json(out / "text_blocks.json", [block])
        write_json(out / "images.json", [])
        return {"toc": [{"title": "1、测试章节", "page": 1, "level": 1}], "document_meta": {"page_count": 1}}

    def fake_detect_candidate_table_regions(**kwargs):
        captured["table_region_out_dir"] = kwargs["out_dir"]
        captured["pdf_tables"] = kwargs["pdf_tables"]
        captured["images"] = kwargs["images"]
        return [region]

    monkeypatch.setattr(cli, "parse_pdf", fake_parse_pdf)
    monkeypatch.setattr(cli, "extract_tables", lambda *_, **__: [table])
    monkeypatch.setattr(cli, "run_pp_structure", lambda *_, **__: [])
    monkeypatch.setattr(cli, "extract_pp_structure_tables", lambda *_, **__: [])
    monkeypatch.setattr(cli, "detect_candidate_table_regions", fake_detect_candidate_table_regions)
    monkeypatch.setattr(cli, "group_candidate_table_regions", lambda regions, out_dir=None: [CandidateTableGroup(group_id="group-1", start_page=1, end_page=1, regions=regions)])
    monkeypatch.setattr(cli, "groups_to_parsed_tables", lambda groups, source_tables, **_: [region_table])
    monkeypatch.setattr(cli, "build_layout_masks", lambda *_: [])
    monkeypatch.setattr(cli, "build_toc_leaf_candidates", lambda **_: [candidate])
    monkeypatch.setattr(cli, "toc_leaf_section_paths", lambda _: [candidate.section_path])
    monkeypatch.setattr(cli, "top_level_modules_from_toc_candidates", lambda _: ["1、测试章节"])
    monkeypatch.setattr(cli, "build_combined_page_material_stream", lambda **_: [])
    monkeypatch.setattr(cli, "time", SimpleNamespace(perf_counter=lambda: next(elapsed_ticks)), raising=False)

    def fake_package_module_artifacts(**kwargs):
        captured["tables"] = kwargs["tables"]
        return {"sections": []}

    monkeypatch.setattr(cli, "package_module_artifacts", fake_package_module_artifacts)

    result = runner.invoke(
        cli.app,
        [
            "pdf-toc-pipeline",
            "--pdf",
            "demo.pdf",
            "--out-dir",
            str(tmp_path / "out"),
            "--enable-pp-structure",
            "true",
        ],
    )

    assert result.exit_code == 0
    assert captured["pdf_tables"] == [table]
    assert captured["images"] == []
    assert Path(captured["table_region_out_dir"]).name == "table_regions"
    assert captured["tables"] == [region_table]
    assert "========== Pipeline Summary ==========" in result.output
    assert "PDF: demo.pdf" in result.output
    assert f"Output: {tmp_path / 'out'}" in result.output
    assert "Total elapsed: 01:02:05.250" in result.output
