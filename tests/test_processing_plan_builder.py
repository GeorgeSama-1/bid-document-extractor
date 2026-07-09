from bid_knowledge.config.processing_plan_builder import build_processing_plan
from bid_knowledge.schemas.models import ManualConfig, SectionRule


def test_build_processing_plan_preserves_from_history_bid_flag() -> None:
    rule = SectionRule(
        rule_id="rule-1",
        file_type="商务文件",
        module_name="投标人基本情况表",
        sub_content_1="基本信息",
        sub_content_2="",
        sub_content_3="",
        section_path="商务文件 / 投标人基本情况表 / 基本信息",
        has_standard_template=False,
        from_history_bid=True,
        ai_generated=False,
        user_upload_required=False,
        reference_technical_spec=False,
        raw_row={"是否从往期投标文件中摘取": "是"},
    )

    plan = build_processing_plan([rule], ManualConfig(file_type="商务文件"))

    assert len(plan.sections) == 1
    assert plan.sections[0].from_history_bid is True
