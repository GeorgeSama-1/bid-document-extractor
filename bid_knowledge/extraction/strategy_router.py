from __future__ import annotations

from bid_knowledge.schemas.models import ProcessingPlanItem, SectionRule, StrategyDecision


PROJECT_SPECIFIC_KEYWORDS = ("投标保证金", "保证金凭证", "本项目", "本次")
ATTACHMENT_KEYWORDS = ("营业执照", "扫描件", "证书", "合同", "发票", "保函", "保险", "证明", "附件")
STRUCTURED_FIELD_KEYWORDS = ("基本情况表", "基本信息表", "股权信息", "人员表", "财务表", "参数表")
TEMPLATE_TEXT_KEYWORDS = ("说明", "声明", "承诺", "偏差表", "格式")
REUSABLE_TEXT_KEYWORDS = ("方案", "措施", "保障", "服务", "计划", "理解")


def infer_content_type(section_path: str) -> str:
    if any(keyword in section_path for keyword in PROJECT_SPECIFIC_KEYWORDS):
        return "project_specific_material"
    if any(keyword in section_path for keyword in ATTACHMENT_KEYWORDS):
        return "attachment"
    if any(keyword in section_path for keyword in STRUCTURED_FIELD_KEYWORDS):
        return "structured_field"
    if any(keyword in section_path for keyword in TEMPLATE_TEXT_KEYWORDS):
        return "template_text"
    if any(keyword in section_path for keyword in REUSABLE_TEXT_KEYWORDS):
        return "reusable_text"
    return "template_text"


def infer_reuse_method(content_type: str) -> str:
    mapping = {
        "attachment": "附件召回",
        "structured_field": "结构化字段填充",
        "template_text": "模板参考复用",
        "reusable_text": "章节文本复用",
        "project_specific_material": "仅本次归档",
    }
    return mapping.get(content_type, "参考复用")


def infer_storage_profile(candidate_type: str) -> dict[str, str]:
    mapping = {
        "structured_field": {
            "storage_category": "structured_reuse",
            "capture_mode": "extract_structured_fields",
            "analysis_status": "rule_based_initial",
        },
        "attachment": {
            "storage_category": "attachment_asset",
            "capture_mode": "preserve_original_file",
            "analysis_status": "rule_based_initial",
        },
        "template_text": {
            "storage_category": "text_reuse",
            "capture_mode": "extract_reusable_text",
            "analysis_status": "rule_based_initial",
        },
        "reusable_text": {
            "storage_category": "text_reuse",
            "capture_mode": "extract_reusable_text",
            "analysis_status": "rule_based_initial",
        },
        "project_specific_material": {
            "storage_category": "project_archive_only",
            "capture_mode": "archive_for_project_only",
            "analysis_status": "rule_based_initial",
        },
    }
    return mapping.get(
        candidate_type,
        {
            "storage_category": "needs_further_analysis",
            "capture_mode": "pending_analysis",
            "analysis_status": "pending_deeper_analysis",
        },
    )


def build_process_strategy(rule: SectionRule) -> list[str]:
    strategies: list[str] = []
    if rule.user_upload_required:
        strategies.append("need_user_upload")
    if rule.from_history_bid:
        strategies.append("historical_reuse")
    if rule.has_standard_template:
        strategies.append("template_fill")
    if rule.ai_generated:
        strategies.append("ai_generate")
    if rule.reference_technical_spec:
        strategies.append("spec_reference")
    return strategies


def route_strategy(rule: SectionRule, plan_item: ProcessingPlanItem) -> StrategyDecision:
    process_strategy = list(dict.fromkeys(plan_item.process_strategy or build_process_strategy(rule)))
    if not process_strategy:
        process_strategy = build_process_strategy(rule)

    candidate_type = plan_item.content_type or infer_content_type(rule.section_path)
    reuse_method = plan_item.reuse_method or infer_reuse_method(candidate_type)
    reuse_level = "long_term" if plan_item.enter_long_term_library else "project_only"
    storage_profile = infer_storage_profile(candidate_type)

    reasons = [
        f"content_type={candidate_type}",
        f"storage_category={storage_profile['storage_category']}",
        f"capture_mode={storage_profile['capture_mode']}",
        f"reuse_method={reuse_method}",
        f"enter_long_term_library={plan_item.enter_long_term_library}",
    ]
    if process_strategy:
        reasons.append(f"process_strategy={','.join(process_strategy)}")

    return StrategyDecision(
        process_strategy=process_strategy,
        candidate_type=candidate_type,
        storage_category=storage_profile["storage_category"],
        capture_mode=storage_profile["capture_mode"],
        analysis_status=storage_profile["analysis_status"],
        reuse_method=reuse_method,
        reuse_level=reuse_level,
        reason="; ".join(reasons),
    )
