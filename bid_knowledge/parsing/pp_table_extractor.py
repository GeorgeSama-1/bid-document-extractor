from __future__ import annotations

from pathlib import Path
from typing import Any

from bid_knowledge.parsing.table_model import build_table_model_from_html, build_table_model_from_rows
from bid_knowledge.schemas.models import ParsedTable
from bid_knowledge.utils.id_utils import make_stable_id
from bid_knowledge.utils.io_utils import write_json


def _float_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list | tuple) or len(value) < 4:
        return None
    bbox: list[float] = []
    for item in value[:4]:
        try:
            bbox.append(float(item))
        except (TypeError, ValueError):
            return None
    return bbox


def _page_no(result: dict[str, Any], payload: dict[str, Any]) -> int:
    page_index = result.get("page_index", payload.get("page_index", 0))
    try:
        return int(payload.get("page_no") or int(page_index) + 1)
    except (TypeError, ValueError):
        return 1


def _bbox_overlap_ratio(bbox: list[float], other: list[float]) -> float:
    x0, y0, x1, y1 = bbox[:4]
    ox0, oy0, ox1, oy1 = other[:4]
    ix0 = max(x0, ox0)
    iy0 = max(y0, oy0)
    ix1 = min(x1, ox1)
    iy1 = min(y1, oy1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    intersection = (ix1 - ix0) * (iy1 - iy0)
    area = max((x1 - x0) * (y1 - y0), 1.0)
    other_area = max((ox1 - ox0) * (oy1 - oy0), 1.0)
    return intersection / min(area, other_area)


def _duplicates_existing(bbox: list[float], existing: list[ParsedTable]) -> bool:
    return any(table.bbox and _bbox_overlap_ratio(bbox, [float(value) for value in table.bbox[:4]]) >= 0.8 for table in existing)


def merge_pp_and_pdf_tables(pp_tables: list[ParsedTable], pdf_tables: list[ParsedTable]) -> list[ParsedTable]:
    merged = list(pp_tables)
    for table in pdf_tables:
        if table.bbox:
            pdf_bbox = [float(value) for value in table.bbox[:4]]
            overlapping_index = next(
                (
                    index
                    for index, existing in enumerate(merged)
                    if existing.bbox and _bbox_overlap_ratio(pdf_bbox, [float(value) for value in existing.bbox[:4]]) >= 0.8
                ),
                None,
            )
            if overlapping_index is not None:
                existing = merged[overlapping_index]
                if not existing.rows and table.rows:
                    merged[overlapping_index] = table
                continue
        merged.append(table)
    return sorted(merged, key=lambda item: (item.page_no, float((item.bbox or [0, 0, 0, 0])[1]) if item.bbox else 0.0))


def extract_pp_structure_tables(
    pp_structure_results: list[dict[str, Any]],
    *,
    out_path: str | Path | None = None,
) -> list[ParsedTable]:
    tables: list[ParsedTable] = []
    for result in pp_structure_results:
        payload = result.get("res") if isinstance(result.get("res"), dict) else result
        if not isinstance(payload, dict):
            continue
        page_no = _page_no(result, payload)
        page_width = payload.get("width")
        page_height = payload.get("height")

        for block_index, block in enumerate(payload.get("parsing_res_list") or [], start=1):
            if not isinstance(block, dict) or str(block.get("block_label") or "") != "table":
                continue
            bbox = _float_bbox(block.get("block_bbox") or block.get("bbox"))
            if not bbox:
                continue
            content = str(block.get("block_content") or "")
            table_model = build_table_model_from_html(content, bbox=bbox)
            tables.append(
                ParsedTable(
                    table_id=make_stable_id("pp-table", page_no, block_index, bbox, content[:120]),
                    page_no=page_no,
                    rows=table_model["rows"],
                    bbox=bbox,
                    source_type="pp_structure_table",
                    table_content=content,
                    table_html=content,
                    table_model=table_model,
                    source_detail="parsing_res_list",
                    pp_block_id=block.get("block_id"),
                    pp_block_order=block.get("block_order"),
                    page_width=page_width,
                    page_height=page_height,
                )
            )

        layout_index = 0
        for box in (payload.get("layout_det_res") or {}).get("boxes") or []:
            if not isinstance(box, dict) or str(box.get("label") or "") != "table":
                continue
            bbox = _float_bbox(box.get("coordinate"))
            if not bbox or _duplicates_existing(bbox, tables):
                continue
            layout_index += 1
            tables.append(
                ParsedTable(
                    table_id=make_stable_id("pp-table-region", page_no, layout_index, bbox),
                    page_no=page_no,
                    rows=[],
                    bbox=bbox,
                    source_type="pp_structure_table",
                    table_content="",
                    table_html="",
                    table_model=build_table_model_from_rows([], source="pp_structure_layout", bbox=bbox),
                    source_detail="layout_det_res",
                    pp_score=box.get("score"),
                    page_width=page_width,
                    page_height=page_height,
                )
            )

    if out_path:
        write_json(out_path, tables)
    return tables
