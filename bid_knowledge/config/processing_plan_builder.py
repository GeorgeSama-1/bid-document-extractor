from __future__ import annotations

from pathlib import Path

from bid_knowledge.extraction.strategy_router import build_process_strategy, infer_content_type, infer_reuse_method
from bid_knowledge.schemas.models import ManualConfig, ProcessingPlan, ProcessingPlanItem, SectionRule
from bid_knowledge.utils.io_utils import write_json
from bid_knowledge.utils.text_utils import clean_text


def _matches_section(section_path: str, targets: list[str]) -> bool:
    if section_path in targets:
        return True
    leaf = section_path.split(" / ")[-1]
    return leaf in targets


def build_processing_plan(
    rules: list[SectionRule],
    manual_config: ManualConfig,
    out_path: str | Path | None = None,
) -> ProcessingPlan:
    sections: list[ProcessingPlanItem] = []
    for rule in rules:
        if manual_config.file_type and rule.file_type and clean_text(manual_config.file_type) != clean_text(rule.file_type):
            continue
        if _matches_section(rule.section_path, manual_config.skip_sections):
            continue

        override = manual_config.section_overrides.get(rule.section_path)
        content_type = infer_content_type(rule.section_path)
        reuse_method = infer_reuse_method(content_type)
        expected_pages = list(override.expected_pages) if override else []
        parse_table = content_type == "structured_field" or _matches_section(rule.section_path, manual_config.table_sections)
        parse_text = content_type != "attachment"
        use_ocr = _matches_section(rule.section_path, manual_config.ocr_sections)
        if manual_config.ocr_pages and expected_pages:
            use_ocr = use_ocr or any(page in manual_config.ocr_pages for page in expected_pages)
        enter_long_term_library = content_type != "project_specific_material"
        review_required = True
        notes: list[str] = []

        if override:
            if override.parse_text is not None:
                parse_text = override.parse_text
            if override.parse_table is not None:
                parse_table = override.parse_table
            if override.use_ocr is not None:
                use_ocr = override.use_ocr
            if override.content_type:
                content_type = override.content_type
            if override.reuse_method:
                reuse_method = override.reuse_method
            if override.enter_long_term_library is not None:
                enter_long_term_library = override.enter_long_term_library
            if override.review_required is not None:
                review_required = override.review_required
            notes.extend(override.notes)

        if content_type == "project_specific_material" and not (override and override.enter_long_term_library is not None):
            enter_long_term_library = False

        process_strategy = build_process_strategy(rule)
        if use_ocr and "use_ocr" not in process_strategy:
            notes.append("OCR enabled by manual configuration")

        item = ProcessingPlanItem(
            rule_id=rule.rule_id,
            section_path=rule.section_path,
            from_history_bid=rule.from_history_bid,
            has_standard_template=rule.has_standard_template,
            expected_pages=expected_pages,
            parse_text=parse_text,
            parse_table=parse_table,
            use_ocr=use_ocr,
            content_type=content_type,
            reuse_method=reuse_method,
            enter_long_term_library=enter_long_term_library,
            review_required=review_required,
            source_rule=rule.model_dump(),
            source_override=override.model_dump() if override else {},
            process_strategy=process_strategy,
            notes=notes,
        )
        sections.append(item)

    plan = ProcessingPlan(
        company_id=manual_config.company_id,
        document_id=manual_config.document_id,
        file_type=manual_config.file_type or (rules[0].file_type if rules else ""),
        sections=sections,
    )
    if out_path:
        write_json(out_path, plan)
    return plan
