import json

from bid_knowledge.extraction.candidate_extractor import extract_candidates
from bid_knowledge.schemas.models import ParsedTable, PdfTextBlock, ProcessingPlan, ProcessingPlanItem, SectionMatchResult


def _make_plan() -> ProcessingPlan:
    return ProcessingPlan(
        company_id="demo_company",
        document_id="demo_doc",
        file_type="商务文件",
        source_file="demo.pdf",
        sections=[
            ProcessingPlanItem(
                rule_id="rule-1",
                section_path="商务文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 经营状况",
                from_history_bid=True,
                content_type="template_text",
                reuse_method="模板参考复用",
                enter_long_term_library=True,
                review_required=True,
                source_rule={
                    "rule_id": "rule-1",
                    "file_type": "商务文件",
                    "module_name": "“商务评分标准”涉及的支撑材料",
                    "sub_content_1": "一、履约能力评价",
                    "sub_content_2": "经营状况",
                    "sub_content_3": "",
                    "section_path": "商务文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 经营状况",
                    "from_history_bid": True,
                },
                source_override={},
                process_strategy=["historical_reuse"],
            )
        ],
    )


def test_extract_candidates_marks_mixed_material_types() -> None:
    plan = _make_plan()
    matches = [
        SectionMatchResult(
            rule_id="rule-1",
            rule_section_path=plan.sections[0].section_path,
            matched=True,
            matched_source_type="section",
            matched_section_id="sec-1",
            matched_title="3.8.1、经营状况",
            matched_page_no=574,
            matched_page_end=575,
            matched_container_section_id="sec-root",
            matched_container_title="3.8、招标文件第三章评标办法前附表之三“商务评分标准”涉及的支撑材料",
            matched_container_page_no=574,
            matched_container_page_end=590,
            confidence=1.0,
            match_reason="exact_normalized",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=574, text="3.8.1、经营状况", bbox=[0, 0, 1, 1], block_no=1),
        PdfTextBlock(block_id="b2", page_no=575, text="附：履约优秀证明", bbox=[0, 0, 1, 1], block_no=2),
    ]
    tables = [
        ParsedTable(table_id="t1", page_no=574, rows=[["项目", "评分"]], bbox=None),
    ]
    images = [
        {"page_no": 575, "xref": 2296, "width": 2559, "height": 1278, "rect": None},
    ]

    candidates = extract_candidates(plan, matches, blocks, tables, images=images)

    assert candidates[0].material_types == ["text", "table", "image"]
    assert candidates[0].dominant_material_type == "mixed"
    assert candidates[0].material_evidence["table_count"] == 1
    assert candidates[0].material_evidence["image_count"] == 1


def test_extract_candidates_marks_table_only_instance() -> None:
    plan = _make_plan()
    matches = [
        SectionMatchResult(
            rule_id="rule-1",
            rule_section_path=plan.sections[0].section_path,
            matched=True,
            matched_source_type="section",
            matched_section_id="sec-1",
            matched_title="3.8.1、经营状况",
            matched_page_no=574,
            matched_page_end=574,
            confidence=1.0,
            match_reason="exact_normalized",
        )
    ]
    blocks = [PdfTextBlock(block_id="b1", page_no=574, text="", bbox=[0, 0, 1, 1], block_no=1)]
    tables = [ParsedTable(table_id="t1", page_no=574, rows=[["项目", "评分"]], bbox=None)]

    candidates = extract_candidates(plan, matches, blocks, tables, images=[])

    assert candidates[0].material_types == ["table"]
    assert candidates[0].dominant_material_type == "table"


def test_extract_candidates_does_not_expand_page_span_with_other_container_related_match() -> None:
    plan = _make_plan()
    matches = [
        SectionMatchResult(
            rule_id="rule-1",
            rule_section_path=plan.sections[0].section_path,
            matched=True,
            matched_source_type="text_block",
            matched_section_id="block::b-current",
            matched_title="3.8.2、经营状况",
            matched_page_no=51,
            matched_page_end=51,
            matched_container_section_id="sec-current",
            matched_container_title="3.8.2、经营状况",
            matched_container_page_no=51,
            matched_container_page_end=51,
            confidence=0.95,
            match_reason="block_line_contains",
            related_matches=[
                {
                    "matched_source_type": "text_block",
                    "matched_section_id": "block::b-previous",
                    "matched_title": "3.8.1、上一章节",
                    "matched_page_no": 50,
                    "matched_page_end": 50,
                    "matched_container_section_id": "sec-previous",
                    "matched_container_title": "3.8.1、上一章节",
                    "matched_container_page_no": 50,
                    "matched_container_page_end": 50,
                    "confidence": 0.9,
                    "match_reason": "block_line_contains",
                }
            ],
        )
    ]
    blocks = [
        PdfTextBlock(block_id="b-previous", page_no=50, text="上一章节内容", bbox=[0, 0, 1, 1], block_no=1),
        PdfTextBlock(block_id="b-current", page_no=51, text="当前章节内容", bbox=[0, 0, 1, 1], block_no=1),
    ]

    candidates = extract_candidates(plan, matches, blocks, [], images=[])

    assert candidates[0].source_page == 51
    assert candidates[0].source_page_end == 51
    assert candidates[0].content == "当前章节内容"


def test_extract_candidates_only_keeps_from_history_bid_items() -> None:
    plan = ProcessingPlan(
        company_id="demo_company",
        document_id="demo_doc",
        file_type="商务文件",
        source_file="demo.pdf",
        sections=[
            ProcessingPlanItem(
                rule_id="rule-history",
                section_path="商务文件 / 模块A / 子内容A",
                from_history_bid=True,
                content_type="template_text",
                reuse_method="模板参考复用",
                enter_long_term_library=True,
                review_required=True,
                source_rule={
                    "rule_id": "rule-history",
                    "file_type": "商务文件",
                    "module_name": "模块A",
                    "sub_content_1": "子内容A",
                    "sub_content_2": "",
                    "sub_content_3": "",
                    "section_path": "商务文件 / 模块A / 子内容A",
                    "from_history_bid": True,
                    "has_standard_template": False,
                },
                source_override={},
                process_strategy=["historical_reuse"],
            ),
            ProcessingPlanItem(
                rule_id="rule-non-history",
                section_path="商务文件 / 模块B / 子内容B",
                from_history_bid=False,
                content_type="template_text",
                reuse_method="模板参考复用",
                enter_long_term_library=True,
                review_required=True,
                source_rule={
                    "rule_id": "rule-non-history",
                    "file_type": "商务文件",
                    "module_name": "模块B",
                    "sub_content_1": "子内容B",
                    "sub_content_2": "",
                    "sub_content_3": "",
                    "section_path": "商务文件 / 模块B / 子内容B",
                    "from_history_bid": False,
                    "has_standard_template": False,
                },
                source_override={},
                process_strategy=[],
            ),
        ],
    )
    matches = [
        SectionMatchResult(
            rule_id="rule-history",
            rule_section_path="商务文件 / 模块A / 子内容A",
            matched=True,
            matched_source_type="section",
            matched_section_id="sec-1",
            matched_title="模块A",
            matched_page_no=1,
            matched_page_end=1,
            confidence=1.0,
            match_reason="exact",
        ),
        SectionMatchResult(
            rule_id="rule-non-history",
            rule_section_path="商务文件 / 模块B / 子内容B",
            matched=True,
            matched_source_type="section",
            matched_section_id="sec-2",
            matched_title="模块B",
            matched_page_no=2,
            matched_page_end=2,
            confidence=1.0,
            match_reason="exact",
        ),
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=1, text="模块A内容", bbox=[0, 0, 1, 1], block_no=1),
        PdfTextBlock(block_id="b2", page_no=2, text="模块B内容", bbox=[0, 0, 1, 1], block_no=2),
    ]

    candidates = extract_candidates(plan, matches, blocks, [], images=[])

    assert len(candidates) == 1
    assert candidates[0].rule_id == "rule-history"
