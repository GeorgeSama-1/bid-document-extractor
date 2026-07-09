from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bid_knowledge.extraction.strategy_router import route_strategy
from bid_knowledge.schemas.models import (
    ParsedTable,
    PdfTextBlock,
    ProcessingPlan,
    ReusableCandidate,
    SectionMatchResult,
    SectionRule,
)
from bid_knowledge.utils.id_utils import make_stable_id
from bid_knowledge.utils.io_utils import write_csv, write_json
from bid_knowledge.utils.text_utils import flatten_table_rows, safe_preview


def _group_blocks_by_page(blocks: list[PdfTextBlock]) -> dict[int, list[PdfTextBlock]]:
    grouped: dict[int, list[PdfTextBlock]] = {}
    for block in sorted(blocks, key=lambda item: (item.page_no, item.block_no)):
        grouped.setdefault(block.page_no, []).append(block)
    return grouped


def _group_tables_by_page(tables: list[ParsedTable]) -> dict[int, list[ParsedTable]]:
    grouped: dict[int, list[ParsedTable]] = {}
    for table in tables:
        grouped.setdefault(table.page_no, []).append(table)
    return grouped


def _group_images_by_page(images: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for image in images:
        page_no = image.get("page_no")
        if not page_no:
            continue
        grouped.setdefault(int(page_no), []).append(image)
    return grouped


def _meaningful_images(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in images:
        width = float(item.get("width", 0) or 0)
        height = float(item.get("height", 0) or 0)
        if width < 200 or height < 120:
            continue
        selected.append(item)
    return selected


def _collect_blocks(match: SectionMatchResult, blocks_by_page: dict[int, list[PdfTextBlock]]) -> list[PdfTextBlock]:
    page_start, page_end = _match_page_span(match)
    if not page_start:
        return []
    collected: list[PdfTextBlock] = []
    for page_no in range(page_start, page_end + 1):
        collected.extend(blocks_by_page.get(page_no, []))
    return collected


def _collect_tables(match: SectionMatchResult, tables: list[ParsedTable]) -> list[ParsedTable]:
    page_start, page_end = _match_page_span(match)
    if not page_start:
        return []
    return [table for table in tables if page_start <= table.page_no <= page_end]


def _match_page_span(match: SectionMatchResult) -> tuple[int | None, int | None]:
    start = match.matched_page_no
    end = match.matched_page_end or start
    for item in match.related_matches:
        if not _same_match_container(match, item):
            continue
        page_no = item.get("matched_page_no")
        page_end = item.get("matched_page_end") or page_no
        if not page_no:
            continue
        start = min(start, int(page_no)) if start else int(page_no)
        end = max(end, int(page_end)) if end else int(page_end)
    return start, end


def _same_match_container(match: SectionMatchResult, item: dict[str, Any]) -> bool:
    primary_container_id = match.matched_container_section_id or match.matched_section_id
    related_container_id = item.get("matched_container_section_id") or item.get("matched_section_id")
    if primary_container_id and related_container_id:
        return primary_container_id == related_container_id

    primary_title = match.matched_container_title or match.matched_title
    related_title = item.get("matched_container_title") or item.get("matched_title")
    if primary_title and related_title:
        return str(primary_title).strip() == str(related_title).strip()
    return True


def _build_discovered_items(match: SectionMatchResult) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seeds = [
        {
            "matched_source_type": match.matched_source_type,
            "matched_section_id": match.matched_section_id,
            "matched_title": match.matched_title,
            "matched_page_no": match.matched_page_no,
            "matched_page_end": match.matched_page_end,
            "matched_container_section_id": match.matched_container_section_id,
            "matched_container_title": match.matched_container_title,
            "matched_container_page_no": match.matched_container_page_no,
            "matched_container_page_end": match.matched_container_page_end,
            "confidence": match.confidence,
            "match_reason": match.match_reason,
        },
        *[item for item in match.related_matches if _same_match_container(match, item)],
    ]
    seen = set()
    for item in seeds:
        title = item.get("matched_title")
        page_no = item.get("matched_page_no")
        key = (title, page_no, item.get("matched_source_type"))
        if not title or key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "title": title,
                "page_no": page_no,
                "page_end": item.get("matched_page_end") or page_no,
                "source_type": item.get("matched_source_type"),
                "section_id": item.get("matched_section_id"),
                "container_section_id": item.get("matched_container_section_id"),
                "container_title": item.get("matched_container_title"),
                "container_page_no": item.get("matched_container_page_no"),
                "container_page_end": item.get("matched_container_page_end"),
                "confidence": item.get("confidence"),
                "reason": item.get("match_reason"),
            }
        )
    return sorted(items, key=lambda row: (row.get("page_no") or 0, row.get("title") or ""))


def _page_range_material_profile(
    page_start: int | None,
    page_end: int | None,
    blocks_by_page: dict[int, list[PdfTextBlock]],
    tables_by_page: dict[int, list[ParsedTable]],
    images_by_page: dict[int, list[dict[str, Any]]],
) -> tuple[list[str], dict[str, Any]]:
    if not page_start:
        return [], {"text_block_count": 0, "table_count": 0, "image_count": 0}

    text_block_count = 0
    table_count = 0
    image_count = 0
    pages = range(page_start, (page_end or page_start) + 1)
    for page_no in pages:
        text_block_count += len([block for block in blocks_by_page.get(page_no, []) if (block.text or "").strip()])
        table_count += len(tables_by_page.get(page_no, []))
        image_count += len(_meaningful_images(images_by_page.get(page_no, [])))

    material_types: list[str] = []
    if text_block_count:
        material_types.append("text")
    if table_count:
        material_types.append("table")
    if image_count:
        material_types.append("image")
    return material_types, {
        "text_block_count": text_block_count,
        "table_count": table_count,
        "image_count": image_count,
    }


def _dominant_material_type(material_types: list[str], material_evidence: dict[str, Any]) -> str:
    if not material_types:
        return "unknown"
    if len(material_types) > 1:
        return "mixed"
    return material_types[0]


def _extract_fields_from_tables(tables: list[ParsedTable]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for table in tables:
        for row in table.rows:
            if len(row) >= 2 and row[0]:
                fields[row[0]] = " | ".join(cell for cell in row[1:] if cell)
    return fields


def _extract_fields_from_blocks(blocks: list[PdfTextBlock]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for block in blocks:
        for line in block.text.splitlines():
            if "：" in line:
                key, value = line.split("：", 1)
            elif ":" in line:
                key, value = line.split(":", 1)
            else:
                continue
            key = key.strip()
            value = value.strip()
            if key and value:
                fields[key] = value
    return fields


def extract_candidates(
    plan: ProcessingPlan,
    matches: list[SectionMatchResult],
    blocks: list[PdfTextBlock],
    tables: list[ParsedTable],
    images: list[dict[str, Any]] | None = None,
    out_json: str | Path | None = None,
    out_csv: str | Path | None = None,
) -> list[ReusableCandidate]:
    blocks_by_page = _group_blocks_by_page(blocks)
    tables_by_page = _group_tables_by_page(tables)
    images_by_page = _group_images_by_page(images or [])
    plan_map = {item.rule_id: item for item in plan.sections}
    rule_map = {item.rule_id: SectionRule(**item.source_rule) for item in plan.sections}
    candidates: list[ReusableCandidate] = []
    csv_rows: list[dict[str, Any]] = []

    for match in matches:
        plan_item = plan_map.get(match.rule_id)
        if not plan_item:
            continue
        if not plan_item.from_history_bid:
            continue
        rule = rule_map.get(match.rule_id)
        decision = route_strategy(rule, plan_item) if rule else None
        title = plan_item.section_path.split(" / ")[-1]
        discovered_items = _build_discovered_items(match) if match.matched else []
        source_page, source_page_end = _match_page_span(match)
        material_types, material_evidence = _page_range_material_profile(
            source_page,
            source_page_end,
            blocks_by_page,
            tables_by_page,
            images_by_page,
        )
        for item in discovered_items:
            item_types, item_evidence = _page_range_material_profile(
                item.get("page_no"),
                item.get("page_end"),
                blocks_by_page,
                tables_by_page,
                images_by_page,
            )
            item["material_types"] = item_types
            item["dominant_material_type"] = _dominant_material_type(item_types, item_evidence)
            item["material_evidence"] = item_evidence
        section_blocks = _collect_blocks(match, blocks_by_page) if match.matched else []
        section_tables = _collect_tables(match, tables) if match.matched else []
        source_block_ids = [block.block_id for block in section_blocks]
        source_bbox = section_blocks[0].bbox if section_blocks else None
        extraction_reason = match.match_reason if match.matched else "section_not_matched"

        fields = None
        content = ""
        candidate_type = plan_item.content_type
        if match.matched:
            if candidate_type == "structured_field":
                fields = _extract_fields_from_tables(section_tables) or _extract_fields_from_blocks(section_blocks)
                content = json.dumps(fields, ensure_ascii=False, indent=2) if fields else flatten_table_rows([row for table in section_tables for row in table.rows])
            elif candidate_type == "attachment":
                content = "\n".join(block.text for block in section_blocks[:8]).strip()
                extraction_reason = f"{extraction_reason}; attachment_preview"
            else:
                content = "\n".join(block.text for block in section_blocks).strip()
        elif "need_user_upload" in plan_item.process_strategy:
            content = ""
            fields = {"missing_material": True}
            extraction_reason = "need_user_upload_without_extracted_content"
        else:
            fields = {"matched": False}

        if candidate_type == "project_specific_material":
            plan_item.enter_long_term_library = False

        candidate = ReusableCandidate(
            candidate_id=make_stable_id("candidate", plan.document_id, plan_item.rule_id, title),
            company_id=plan.company_id,
            document_id=plan.document_id,
            rule_id=plan_item.rule_id,
            section_path=plan_item.section_path,
            from_history_bid=rule.from_history_bid if rule else False,
            has_standard_template=plan_item.has_standard_template,
            title=title,
            content=content,
            fields=fields,
            candidate_type=candidate_type,
            storage_category=decision.storage_category if decision else "needs_further_analysis",
            capture_mode=decision.capture_mode if decision else "pending_analysis",
            analysis_status=decision.analysis_status if decision else "pending_deeper_analysis",
            material_types=material_types,
            dominant_material_type=_dominant_material_type(material_types, material_evidence),
            material_evidence=material_evidence,
            process_strategy=plan_item.process_strategy,
            reuse_method=decision.reuse_method if decision else plan_item.reuse_method,
            reuse_level=decision.reuse_level if decision else ("long_term" if plan_item.enter_long_term_library else "project_only"),
            enter_long_term_library=plan_item.enter_long_term_library,
            source_file=plan.source_file or plan.document_id,
            source_page=source_page,
            source_page_end=source_page_end,
            source_container_title=match.matched_container_title,
            source_container_page=match.matched_container_page_no,
            source_container_page_end=match.matched_container_page_end,
            source_bbox=source_bbox,
            source_block_ids=source_block_ids,
            discovered_items=discovered_items,
            confidence=match.confidence if match.matched else 0.0,
            extraction_reason=extraction_reason,
        )
        candidates.append(candidate)
        csv_rows.append(
            {
                "candidate_id": candidate.candidate_id,
                "title": title,
                "candidate_type": candidate_type,
                "from_history_bid": candidate.from_history_bid,
                "storage_category": candidate.storage_category,
                "capture_mode": candidate.capture_mode,
                "analysis_status": candidate.analysis_status,
                "material_types": ",".join(candidate.material_types),
                "dominant_material_type": candidate.dominant_material_type,
                "material_evidence": json.dumps(candidate.material_evidence, ensure_ascii=False),
                "source_page": candidate.source_page,
                "source_page_end": candidate.source_page_end,
                "source_container_title": candidate.source_container_title,
                "source_container_page": candidate.source_container_page,
                "source_container_page_end": candidate.source_container_page_end,
                "discovered_items": json.dumps(candidate.discovered_items, ensure_ascii=False),
                "enter_long_term_library": candidate.enter_long_term_library,
                "reuse_method": candidate.reuse_method,
                "confidence": candidate.confidence,
                "content_preview": safe_preview(candidate.content),
                "extraction_reason": candidate.extraction_reason,
            }
        )

    if out_json:
        write_json(out_json, candidates)
    if out_csv:
        write_csv(out_csv, csv_rows)
    return candidates
