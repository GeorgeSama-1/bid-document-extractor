from bid_knowledge.extraction.strategy_router import infer_content_type, infer_storage_profile, route_strategy
from bid_knowledge.schemas.models import ProcessingPlanItem, SectionRule


def make_rule(**overrides):
    data = {
        "rule_id": "rule-1",
        "file_type": "商务文件",
        "module_name": "资格审查",
        "sub_content_1": "投标人基本情况表",
        "sub_content_2": "",
        "sub_content_3": "",
        "section_path": "商务文件 / 资格审查 / 投标人基本情况表",
        "has_standard_template": False,
        "from_history_bid": False,
        "ai_generated": False,
        "user_upload_required": False,
        "reference_technical_spec": False,
        "raw_row": {},
    }
    data.update(overrides)
    return SectionRule(**data)


def make_plan_item(**overrides):
    data = {
        "rule_id": "rule-1",
        "section_path": "商务文件 / 资格审查 / 投标人基本情况表",
        "expected_pages": [],
        "parse_text": True,
        "parse_table": False,
        "use_ocr": False,
        "content_type": "structured_field",
        "reuse_method": "结构化字段填充",
        "enter_long_term_library": True,
        "review_required": True,
        "source_rule": {},
        "source_override": {},
        "notes": [],
    }
    data.update(overrides)
    return ProcessingPlanItem(**data)


def test_route_strategy_adds_historical_reuse() -> None:
    result = route_strategy(make_rule(from_history_bid=True), make_plan_item())
    assert "historical_reuse" in result.process_strategy


def test_route_strategy_adds_need_user_upload() -> None:
    result = route_strategy(make_rule(user_upload_required=True), make_plan_item())
    assert "need_user_upload" in result.process_strategy


def test_infer_content_type_for_project_specific_material() -> None:
    assert infer_content_type("商务文件 / 投标保证金") == "project_specific_material"


def test_infer_content_type_for_attachment() -> None:
    assert infer_content_type("商务文件 / 企业营业执照扫描件") == "attachment"


def test_infer_content_type_for_structured_field() -> None:
    assert infer_content_type("商务文件 / 投标人基本情况表") == "structured_field"


def test_infer_storage_profile_for_attachment() -> None:
    profile = infer_storage_profile("attachment")
    assert profile["storage_category"] == "attachment_asset"
    assert profile["capture_mode"] == "preserve_original_file"


def test_infer_storage_profile_for_structured_field() -> None:
    profile = infer_storage_profile("structured_field")
    assert profile["storage_category"] == "structured_reuse"
    assert profile["capture_mode"] == "extract_structured_fields"


def test_infer_storage_profile_for_unknown_goes_to_future_analysis() -> None:
    profile = infer_storage_profile("unknown")
    assert profile["storage_category"] == "needs_further_analysis"
    assert profile["analysis_status"] == "pending_deeper_analysis"
