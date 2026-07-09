from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from pydantic import Field

from bid_knowledge.parsing.table_model import build_table_model_from_pdfplumber_table, build_table_model_from_rows
from bid_knowledge.schemas.models import ModelBase, ParsedTable
from bid_knowledge.utils.id_utils import make_stable_id
from bid_knowledge.utils.io_utils import ensure_dir, write_json, write_jsonl
from bid_knowledge.utils.text_utils import clean_text


class CandidateTableRegion(ModelBase):
    region_id: str
    page_no: int
    bbox: list[float]
    detectors: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    evidence: dict[str, Any] = Field(default_factory=dict)
    expanded_bbox: list[float] | None = None
    crop_image_path: str = ""
    source_table_ids: list[str] = Field(default_factory=list)
    page_width: float | None = None
    page_height: float | None = None


class CandidateTableGroup(ModelBase):
    group_id: str
    start_page: int
    end_page: int
    region_ids: list[str] = Field(default_factory=list)
    bbox_by_page: dict[int, list[float]] = Field(default_factory=dict)
    regions: list[CandidateTableRegion] = Field(default_factory=list)
    is_cross_page: bool = False
    confidence: float = 0.0
    detectors: list[str] = Field(default_factory=list)
    crop_image_paths: list[str] = Field(default_factory=list)
    group_kind: str = "structured_table"
    should_parse_table: bool = True
    evidence: dict[str, Any] = Field(default_factory=dict)


def _float_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list | tuple) or len(value) < 4:
        return None
    try:
        x0, y0, x1, y1 = [float(item) for item in value[:4]]
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


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


def _bbox_overlap_area_ratio(bbox: list[float], other: list[float]) -> float:
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
    return intersection / area


def _bbox_center_inside(bbox: list[float], other: list[float]) -> bool:
    center_x = (float(bbox[0]) + float(bbox[2])) / 2.0
    center_y = (float(bbox[1]) + float(bbox[3])) / 2.0
    return float(other[0]) <= center_x <= float(other[2]) and float(other[1]) <= center_y <= float(other[3])


def _union_bbox(bbox: list[float], other: list[float]) -> list[float]:
    return [
        min(float(bbox[0]), float(other[0])),
        min(float(bbox[1]), float(other[1])),
        max(float(bbox[2]), float(other[2])),
        max(float(bbox[3]), float(other[3])),
    ]


def _x_overlap_ratio(bbox: list[float], other: list[float]) -> float:
    x0, _y0, x1, _y1 = bbox[:4]
    ox0, _oy0, ox1, _oy1 = other[:4]
    intersection = max(0.0, min(x1, ox1) - max(x0, ox0))
    width = max(min(x1 - x0, ox1 - ox0), 1.0)
    return intersection / width


def _vertical_gap(previous: list[float], current: list[float]) -> float:
    return float(current[1]) - float(previous[3])


def _expand_bbox(
    bbox: list[float],
    *,
    page_width: float | None = None,
    page_height: float | None = None,
    ratio: float = 0.08,
    min_padding: float = 8.0,
) -> list[float]:
    x0, y0, x1, y1 = [float(value) for value in bbox[:4]]
    padding_x = max((x1 - x0) * ratio, min_padding)
    padding_y = max((y1 - y0) * ratio, min_padding)
    expanded = [x0 - padding_x, y0 - padding_y, x1 + padding_x, y1 + padding_y]
    if page_width:
        expanded[0] = max(0.0, expanded[0])
        expanded[2] = min(float(page_width), expanded[2])
    if page_height:
        expanded[1] = max(0.0, expanded[1])
        expanded[3] = min(float(page_height), expanded[3])
    return expanded


def _scale_bbox(bbox: list[float], scale_x: float, scale_y: float) -> list[float]:
    return [
        round(float(bbox[0]) * scale_x, 6),
        round(float(bbox[1]) * scale_y, 6),
        round(float(bbox[2]) * scale_x, 6),
        round(float(bbox[3]) * scale_y, 6),
    ]


def _normalize_regions_to_pdf_coords(
    regions: list[CandidateTableRegion],
    page_sizes: dict[int, tuple[float, float]],
) -> list[CandidateTableRegion]:
    normalized: list[CandidateTableRegion] = []
    for region in regions:
        page_size = page_sizes.get(region.page_no)
        if not page_size or not region.page_width or not region.page_height:
            normalized.append(region)
            continue
        pdf_width, pdf_height = page_size
        try:
            source_width = float(region.page_width)
            source_height = float(region.page_height)
        except (TypeError, ValueError):
            normalized.append(region)
            continue
        if source_width <= 0 or source_height <= 0:
            normalized.append(region)
            continue
        scale_x = float(pdf_width) / source_width
        scale_y = float(pdf_height) / source_height
        evidence = {
            **region.evidence,
            "source_page_width": region.page_width,
            "source_page_height": region.page_height,
            "normalized_to_pdf_coords": True,
        }
        normalized.append(
            CandidateTableRegion(
                region_id=region.region_id,
                page_no=region.page_no,
                bbox=_scale_bbox(region.bbox, scale_x, scale_y),
                detectors=region.detectors,
                confidence=region.confidence,
                evidence=evidence,
                expanded_bbox=_scale_bbox(region.expanded_bbox, scale_x, scale_y) if region.expanded_bbox else None,
                crop_image_path=region.crop_image_path,
                source_table_ids=region.source_table_ids,
                page_width=None,
                page_height=None,
            )
        )
    return normalized


def _image_masks_from_images(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    masks: list[dict[str, Any]] = []
    for image in images:
        try:
            page_no = int(image.get("page_no") or 0)
        except (TypeError, ValueError):
            continue
        for rect in [image.get("rect"), *(image.get("rects") or [])]:
            bbox = _float_bbox(rect)
            if bbox:
                masks.append({"page_no": page_no, "bbox": bbox, "source": "pdf_image", "image_id": image.get("image_id")})
    return masks


def _image_masks_from_pp_structure(pp_structure_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    masks: list[dict[str, Any]] = []
    for result in pp_structure_results:
        payload = result.get("res") if isinstance(result.get("res"), dict) else result
        if not isinstance(payload, dict):
            continue
        page_index = result.get("page_index", payload.get("page_index", 0))
        try:
            page_no = int(payload.get("page_no") or int(page_index) + 1)
        except (TypeError, ValueError):
            page_no = 1
        page_width = payload.get("width")
        page_height = payload.get("height")
        for block in payload.get("parsing_res_list") or []:
            if not isinstance(block, dict) or str(block.get("block_label") or "") not in {"image", "header_image", "footer_image"}:
                continue
            bbox = _float_bbox(block.get("block_bbox") or block.get("bbox"))
            if bbox:
                masks.append({"page_no": page_no, "bbox": bbox, "source": "pp_structure", "page_width": page_width, "page_height": page_height})
        for box in (payload.get("layout_det_res") or {}).get("boxes") or []:
            if not isinstance(box, dict) or str(box.get("label") or "") not in {"image", "header_image", "footer_image"}:
                continue
            bbox = _float_bbox(box.get("coordinate"))
            if bbox:
                masks.append({"page_no": page_no, "bbox": bbox, "source": "pp_structure", "page_width": page_width, "page_height": page_height})
    return masks


def _normalize_image_masks_to_pdf_coords(
    masks: list[dict[str, Any]],
    page_sizes: dict[int, tuple[float, float]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for mask in masks:
        page_no = int(mask.get("page_no") or 0)
        bbox = _float_bbox(mask.get("bbox"))
        if not bbox:
            continue
        page_size = page_sizes.get(page_no)
        source_width = mask.get("page_width")
        source_height = mask.get("page_height")
        if page_size and source_width and source_height:
            try:
                scale_x = float(page_size[0]) / float(source_width)
                scale_y = float(page_size[1]) / float(source_height)
                bbox = _scale_bbox(bbox, scale_x, scale_y)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        normalized.append({**mask, "bbox": bbox})
    return normalized


def _filter_regions_overlapping_images(
    regions: list[CandidateTableRegion],
    images: list[dict[str, Any]],
    pp_structure_results: list[dict[str, Any]] | None = None,
    *,
    page_sizes: dict[int, tuple[float, float]] | None = None,
    overlap_threshold: float = 0.65,
    table_area_overlap_threshold: float = 0.3,
) -> list[CandidateTableRegion]:
    page_sizes = page_sizes or {}
    masks = _image_masks_from_images(images)
    masks.extend(_image_masks_from_pp_structure(pp_structure_results or []))
    masks = _normalize_image_masks_to_pdf_coords(masks, page_sizes)
    filtered: list[CandidateTableRegion] = []
    for region in regions:
        mask = next(
            (
                item
                for item in masks
                if int(item.get("page_no") or 0) == region.page_no
                and not _image_mask_embedded_in_table_region(region.bbox, item["bbox"])
                and (
                    _bbox_overlap_ratio(region.bbox, item["bbox"]) >= overlap_threshold
                    or _bbox_center_inside(region.bbox, item["bbox"])
                    or _bbox_overlap_area_ratio(region.bbox, item["bbox"]) >= table_area_overlap_threshold
                )
            ),
            None,
        )
        if mask:
            region.evidence["filtered_reason"] = "overlaps_image"
            region.evidence["filtered_image_source"] = mask.get("source")
            region.evidence["filtered_image_id"] = mask.get("image_id")
            continue
        filtered.append(region)
    return filtered


def _image_mask_embedded_in_table_region(region_bbox: list[float], image_bbox: list[float]) -> bool:
    image_covered = _bbox_overlap_ratio(region_bbox, image_bbox)
    table_covered = _bbox_overlap_area_ratio(region_bbox, image_bbox)
    if image_covered < 0.9 or table_covered >= 0.85:
        return False
    rx0, ry0, rx1, ry1 = [float(value) for value in region_bbox[:4]]
    ix0, iy0, ix1, iy1 = [float(value) for value in image_bbox[:4]]
    table_width = max(rx1 - rx0, 1.0)
    table_height = max(ry1 - ry0, 1.0)
    horizontal_margin = max(0.0, ix0 - rx0) + max(0.0, rx1 - ix1)
    vertical_margin = max(0.0, iy0 - ry0) + max(0.0, ry1 - iy1)
    return horizontal_margin >= table_width * 0.08 or vertical_margin >= table_height * 0.08


def _merge_candidate_regions(
    regions: list[CandidateTableRegion],
    *,
    overlap_threshold: float = 0.65,
) -> list[CandidateTableRegion]:
    detector_order = {"pdfplumber": 0, "pymupdf_lines": 1, "pp_structure": 2}
    merged: list[CandidateTableRegion] = []
    for region in sorted(regions, key=lambda item: (item.page_no, item.bbox[1], item.bbox[0])):
        match_index = next(
            (
                index
                for index, existing in enumerate(merged)
                if existing.page_no == region.page_no
                and _bbox_overlap_ratio(existing.bbox, region.bbox) >= overlap_threshold
            ),
            None,
        )
        if match_index is None:
            merged.append(region)
            continue
        existing = merged[match_index]
        detectors = sorted(
            dict.fromkeys([*existing.detectors, *region.detectors]),
            key=lambda value: detector_order.get(value, 99),
        )
        source_table_ids = list(dict.fromkeys([*existing.source_table_ids, *region.source_table_ids]))
        evidence = {**existing.evidence, **region.evidence}
        confidence = min(0.99, max(existing.confidence, region.confidence) + 0.05 * (len(detectors) - 1))
        merged[match_index] = CandidateTableRegion(
            region_id=existing.region_id,
            page_no=existing.page_no,
            bbox=_union_bbox(existing.bbox, region.bbox),
            detectors=detectors,
            confidence=round(confidence, 4),
            evidence=evidence,
            expanded_bbox=existing.expanded_bbox or region.expanded_bbox,
            crop_image_path=existing.crop_image_path or region.crop_image_path,
            source_table_ids=source_table_ids,
            page_width=existing.page_width or region.page_width,
            page_height=existing.page_height or region.page_height,
        )
    return sorted(merged, key=lambda item: (item.page_no, item.bbox[1], item.bbox[0]))


def _detectors_for_regions(regions: list[CandidateTableRegion]) -> list[str]:
    detector_order = {"pdfplumber": 0, "pymupdf_lines": 1, "pp_structure": 2}
    return sorted(
        dict.fromkeys(detector for region in regions for detector in region.detectors),
        key=lambda value: detector_order.get(value, 99),
    )


def _group_from_regions(group_id: str, regions: list[CandidateTableRegion]) -> CandidateTableGroup:
    sorted_regions = sorted(regions, key=lambda item: (item.page_no, item.bbox[1], item.bbox[0]))
    bbox_by_page: dict[int, list[float]] = {}
    for region in sorted_regions:
        bbox = region.expanded_bbox or region.bbox
        bbox_by_page[region.page_no] = _union_bbox(bbox_by_page[region.page_no], bbox) if region.page_no in bbox_by_page else bbox
    confidence = max((region.confidence for region in sorted_regions), default=0.0)
    evidence: dict[str, Any] = {
        "region_count": len(sorted_regions),
        "same_page_merged": len({region.page_no for region in sorted_regions}) == 1 and len(sorted_regions) > 1,
    }
    if len({region.page_no for region in sorted_regions}) > 1:
        evidence["cross_page_linked"] = True
    return CandidateTableGroup(
        group_id=group_id,
        start_page=min(region.page_no for region in sorted_regions),
        end_page=max(region.page_no for region in sorted_regions),
        region_ids=[region.region_id for region in sorted_regions],
        bbox_by_page=bbox_by_page,
        regions=sorted_regions,
        is_cross_page=len({region.page_no for region in sorted_regions}) > 1,
        confidence=confidence,
        detectors=_detectors_for_regions(sorted_regions),
        crop_image_paths=[region.crop_image_path for region in sorted_regions if region.crop_image_path],
        evidence=evidence,
    )


def _should_merge_same_page(previous: CandidateTableRegion, current: CandidateTableRegion) -> bool:
    if previous.page_no != current.page_no:
        return False
    gap = _vertical_gap(previous.expanded_bbox or previous.bbox, current.expanded_bbox or current.bbox)
    return _x_overlap_ratio(previous.bbox, current.bbox) >= 0.72 and -8.0 <= gap <= 40.0


def _should_link_cross_page(previous: CandidateTableGroup, current: CandidateTableGroup) -> bool:
    if previous.end_page + 1 != current.start_page:
        return False
    previous_bbox = previous.bbox_by_page.get(previous.end_page)
    current_bbox = current.bbox_by_page.get(current.start_page)
    if not previous_bbox or not current_bbox:
        return False
    if _x_overlap_ratio(previous_bbox, current_bbox) < 0.72:
        return False
    previous_near_bottom = previous_bbox[3] >= 500.0
    current_near_top = current_bbox[1] <= 180.0
    return previous_near_bottom and current_near_top


def group_candidate_table_regions(
    regions: list[CandidateTableRegion],
    *,
    out_dir: str | Path | None = None,
) -> list[CandidateTableGroup]:
    buckets: list[list[CandidateTableRegion]] = []
    for region in sorted(regions, key=lambda item: (item.page_no, item.bbox[1], item.bbox[0])):
        match = next((bucket for bucket in buckets if _should_merge_same_page(bucket[-1], region)), None)
        if match is None:
            buckets.append([region])
        else:
            match.append(region)
    same_page_groups = [
        _group_from_regions(make_stable_id("table-group", index, bucket[0].page_no, bucket[0].bbox), bucket)
        for index, bucket in enumerate(buckets, start=1)
    ]
    same_page_groups = sorted(same_page_groups, key=lambda item: (item.start_page, item.bbox_by_page[item.start_page][1], item.bbox_by_page[item.start_page][0]))

    linked_groups: list[CandidateTableGroup] = []
    for group in same_page_groups:
        if linked_groups and _should_link_cross_page(linked_groups[-1], group):
            combined_regions = [*linked_groups[-1].regions, *group.regions]
            linked_groups[-1] = _group_from_regions(linked_groups[-1].group_id, combined_regions)
        else:
            linked_groups.append(group)
    for index, group in enumerate(linked_groups, start=1):
        group.group_id = make_stable_id("table-group", index, group.start_page, group.end_page, group.region_ids)
    if out_dir:
        output_dir = ensure_dir(out_dir)
        write_json(output_dir / "table_groups.json", linked_groups)
        write_jsonl(output_dir / "table_groups.jsonl", linked_groups)
    return linked_groups


def _regions_from_pdf_tables(tables: list[ParsedTable]) -> list[CandidateTableRegion]:
    regions: list[CandidateTableRegion] = []
    for table in tables:
        bbox = _float_bbox(table.bbox)
        if not bbox:
            continue
        detector = "pp_structure" if table.source_type == "pp_structure_table" else "pdfplumber"
        confidence = 0.68 if detector == "pp_structure" else 0.72
        regions.append(
            CandidateTableRegion(
                region_id=make_stable_id("table-region-source", table.page_no, table.table_id, bbox),
                page_no=table.page_no,
                bbox=bbox,
                detectors=[detector],
                confidence=confidence,
                evidence={"source_table_id": table.table_id, "source_type": table.source_type, "row_count": len(table.rows)},
                source_table_ids=[table.table_id],
                page_width=getattr(table, "page_width", None),
                page_height=getattr(table, "page_height", None),
            )
        )
    return regions


def _regions_from_pp_structure(pp_structure_results: list[dict[str, Any]]) -> list[CandidateTableRegion]:
    regions: list[CandidateTableRegion] = []
    for result in pp_structure_results:
        payload = result.get("res") if isinstance(result.get("res"), dict) else result
        if not isinstance(payload, dict):
            continue
        page_index = result.get("page_index", payload.get("page_index", 0))
        try:
            page_no = int(payload.get("page_no") or int(page_index) + 1)
        except (TypeError, ValueError):
            page_no = 1
        page_width = payload.get("width")
        page_height = payload.get("height")
        for block_index, block in enumerate(payload.get("parsing_res_list") or [], start=1):
            if not isinstance(block, dict) or str(block.get("block_label") or "") != "table":
                continue
            bbox = _float_bbox(block.get("block_bbox") or block.get("bbox"))
            if not bbox:
                continue
            regions.append(
                CandidateTableRegion(
                    region_id=make_stable_id("table-region-pp-block", page_no, block_index, bbox),
                    page_no=page_no,
                    bbox=bbox,
                    detectors=["pp_structure"],
                    confidence=0.68,
                    evidence={"source": "parsing_res_list", "pp_block_id": block.get("block_id")},
                    page_width=page_width,
                    page_height=page_height,
                )
            )
        for layout_index, box in enumerate((payload.get("layout_det_res") or {}).get("boxes") or [], start=1):
            if not isinstance(box, dict) or str(box.get("label") or "") != "table":
                continue
            bbox = _float_bbox(box.get("coordinate"))
            if not bbox:
                continue
            score = float(box.get("score") or 0.0)
            regions.append(
                CandidateTableRegion(
                    region_id=make_stable_id("table-region-pp-layout", page_no, layout_index, bbox),
                    page_no=page_no,
                    bbox=bbox,
                    detectors=["pp_structure"],
                    confidence=max(0.45, min(0.72, score)),
                    evidence={"source": "layout_det_res", "pp_score": box.get("score")},
                    page_width=page_width,
                    page_height=page_height,
                )
            )
    return regions


def _line_regions_from_segments(
    page_no: int,
    horizontal: list[tuple[float, float, float, float]],
    vertical: list[tuple[float, float, float, float]],
) -> list[CandidateTableRegion]:
    nodes: list[dict[str, Any]] = [
        {"kind": "h", "line": (float(x0), float(y0), float(x1), float(y1))}
        for x0, y0, x1, y1 in horizontal
    ] + [
        {"kind": "v", "line": (float(x0), float(y0), float(x1), float(y1))}
        for x0, y0, x1, y1 in vertical
    ]
    if not nodes:
        return []
    graph: list[set[int]] = [set() for _ in nodes]
    tolerance = 3.0
    for h_index, h_node in enumerate(nodes):
        if h_node["kind"] != "h":
            continue
        hx0, hy0, hx1, _hy1 = h_node["line"]
        for v_index, v_node in enumerate(nodes):
            if v_node["kind"] != "v":
                continue
            vx0, vy0, _vx1, vy1 = v_node["line"]
            if hx0 - tolerance <= vx0 <= hx1 + tolerance and vy0 - tolerance <= hy0 <= vy1 + tolerance:
                graph[h_index].add(v_index)
                graph[v_index].add(h_index)

    regions: list[CandidateTableRegion] = []
    seen: set[int] = set()
    for start in range(len(nodes)):
        if start in seen:
            continue
        stack = [start]
        component: list[int] = []
        seen.add(start)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in graph[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        component_nodes = [nodes[index] for index in component]
        component_horizontal = [node["line"] for node in component_nodes if node["kind"] == "h"]
        component_vertical = [node["line"] for node in component_nodes if node["kind"] == "v"]
        if len(component_horizontal) < 2 or len(component_vertical) < 2:
            continue
        x0 = min(min(line[0], line[2]) for line in [*component_horizontal, *component_vertical])
        y0 = min(min(line[1], line[3]) for line in [*component_horizontal, *component_vertical])
        x1 = max(max(line[0], line[2]) for line in [*component_horizontal, *component_vertical])
        y1 = max(max(line[1], line[3]) for line in [*component_horizontal, *component_vertical])
        if x1 - x0 < 80 or y1 - y0 < 24:
            continue
        bbox = [x0, y0, x1, y1]
        regions.append(
            CandidateTableRegion(
                region_id=make_stable_id("table-region-lines", page_no, bbox),
                page_no=page_no,
                bbox=bbox,
                detectors=["pymupdf_lines"],
                confidence=0.8,
                evidence={
                    "horizontal_lines": len(component_horizontal),
                    "vertical_lines": len(component_vertical),
                },
            )
        )
    return sorted(regions, key=lambda item: (item.bbox[1], item.bbox[0]))


def _regions_from_pymupdf_lines(pdf_path: str | Path) -> list[CandidateTableRegion]:
    try:
        import fitz
    except ImportError:
        return []

    regions: list[CandidateTableRegion] = []
    doc = fitz.open(str(pdf_path))
    try:
        for page_index, page in enumerate(doc, start=1):
            horizontal: list[tuple[float, float, float, float]] = []
            vertical: list[tuple[float, float, float, float]] = []
            for drawing in page.get_drawings():
                rect = drawing.get("rect")
                if rect is not None:
                    width = float(rect.x1 - rect.x0)
                    height = float(rect.y1 - rect.y0)
                    if width >= 250 and height >= 80 and _rect_has_table_text_evidence(page, rect):
                        regions.append(
                            CandidateTableRegion(
                                region_id=make_stable_id("table-region-rect", page_index, [rect.x0, rect.y0, rect.x1, rect.y1]),
                                page_no=page_index,
                                bbox=[float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)],
                                detectors=["pymupdf_lines"],
                                confidence=0.62,
                                evidence={"source": "rect", "rect_text_verified": True},
                            )
                        )
                for item in drawing.get("items") or []:
                    if not item or item[0] != "l" or len(item) < 3:
                        continue
                    p0, p1 = item[1], item[2]
                    x0, y0, x1, y1 = float(p0.x), float(p0.y), float(p1.x), float(p1.y)
                    if abs(y1 - y0) <= 1.5 and abs(x1 - x0) >= 40:
                        horizontal.append((min(x0, x1), y0, max(x0, x1), y1))
                    elif abs(x1 - x0) <= 1.5 and abs(y1 - y0) >= 16:
                        vertical.append((x0, min(y0, y1), x1, max(y0, y1)))
            regions.extend(_line_regions_from_segments(page_index, horizontal, vertical))
    finally:
        doc.close()
    return regions


def _rect_has_table_text_evidence(
    page: Any,
    rect: Any,
    *,
    min_blocks: int = 4,
    min_text_chars: int = 20,
) -> bool:
    try:
        blocks = page.get_text("blocks", clip=rect) or []
    except Exception:
        return False
    texts: list[str] = []
    for block in blocks:
        if not isinstance(block, list | tuple) or len(block) < 5:
            continue
        text = str(block[4] or "").strip()
        if text:
            texts.append(text)
    return len(texts) >= min_blocks and sum(len(text) for text in texts) >= min_text_chars


def _pdf_page_sizes(pdf_path: str | Path) -> dict[int, tuple[float, float]]:
    try:
        import fitz
    except ImportError:
        return {}
    sizes: dict[int, tuple[float, float]] = {}
    doc = fitz.open(str(pdf_path))
    try:
        for page_index, page in enumerate(doc, start=1):
            sizes[page_index] = (float(page.rect.width), float(page.rect.height))
    finally:
        doc.close()
    return sizes


def _render_region_crop(
    *,
    pdf_path: str | Path,
    region: CandidateTableRegion,
    out_dir: str | Path,
    zoom: float = 2.0,
) -> str:
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - dependency-driven
        raise RuntimeError("需要安装 PyMuPDF 才能裁剪表格候选区域。") from exc

    target_dir = ensure_dir(out_dir)
    image_path = target_dir / f"{region.region_id}.png"
    doc = fitz.open(str(pdf_path))
    try:
        page = doc.load_page(region.page_no - 1)
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        bbox = region.expanded_bbox or region.bbox
        source_width = float(region.page_width) if region.page_width else None
        source_height = float(region.page_height) if region.page_height else None
        if source_width and source_height:
            bbox = [
                bbox[0] * page_width / source_width,
                bbox[1] * page_height / source_height,
                bbox[2] * page_width / source_width,
                bbox[3] * page_height / source_height,
            ]
        clip = fitz.Rect(*bbox)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
        pix.save(image_path)
    finally:
        doc.close()
    return str(image_path)


def detect_candidate_table_regions(
    *,
    pdf_path: str | Path,
    pdf_tables: list[ParsedTable] | None = None,
    images: list[dict[str, Any]] | None = None,
    pp_structure_results: list[dict[str, Any]] | None = None,
    out_dir: str | Path | None = None,
    expansion_ratio: float = 0.08,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[CandidateTableRegion]:
    raw_regions: list[CandidateTableRegion] = []
    raw_regions.extend(_regions_from_pdf_tables(pdf_tables or []))
    if progress_callback:
        progress_callback(1, 3)
    raw_regions.extend(_regions_from_pymupdf_lines(pdf_path))
    if progress_callback:
        progress_callback(2, 3)
    raw_regions.extend(_regions_from_pp_structure(pp_structure_results or []))
    page_sizes = _pdf_page_sizes(pdf_path)
    raw_regions = _normalize_regions_to_pdf_coords(raw_regions, page_sizes)
    raw_regions = _filter_regions_overlapping_images(
        raw_regions,
        images or [],
        pp_structure_results or [],
        page_sizes=page_sizes,
    )
    regions = _merge_candidate_regions(raw_regions)
    for region in regions:
        region.expanded_bbox = _expand_bbox(
            region.bbox,
            page_width=region.page_width,
            page_height=region.page_height,
            ratio=expansion_ratio,
        )
    if out_dir:
        output_dir = ensure_dir(out_dir)
        crop_dir = ensure_dir(output_dir / "debug_table_regions")
        for region in regions:
            try:
                region.crop_image_path = _render_region_crop(pdf_path=pdf_path, region=region, out_dir=crop_dir)
            except Exception as exc:
                region.evidence["crop_error"] = str(exc)
        write_json(output_dir / "table_candidates.json", regions)
        write_jsonl(output_dir / "results.jsonl", regions)
    if progress_callback:
        progress_callback(3, 3)
    return regions


def _is_pdfplumber_table_model(table_model: Any) -> bool:
    return isinstance(table_model, dict) and str(table_model.get("source") or "").startswith("pdfplumber")


def _clip_bbox_to_page(bbox: list[float], page: Any) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = [float(value) for value in bbox[:4]]
    width = getattr(page, "width", None)
    height = getattr(page, "height", None)
    if width:
        x0 = max(0.0, min(x0, float(width)))
        x1 = max(0.0, min(x1, float(width)))
    if height:
        y0 = max(0.0, min(y0, float(height)))
        y1 = max(0.0, min(y1, float(height)))
    return (x0, y0, x1, y1)


def _extract_pdfplumber_table_from_bbox(
    *,
    pdf_path: str | Path | None,
    page_no: int,
    bbox: list[float] | None,
) -> tuple[list[list[str]], dict[str, Any]] | None:
    if not pdf_path or not bbox or len(bbox) < 4 or page_no < 1:
        return None
    try:
        import pdfplumber
    except ImportError:
        return None
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            if page_no > len(pdf.pages):
                return None
            page = pdf.pages[page_no - 1]
            cropped = page.crop(_clip_bbox_to_page([float(value) for value in bbox[:4]], page))
            found_tables = cropped.find_tables() or []
            for found in found_tables:
                raw_rows = found.extract() or []
                rows = [
                    [clean_text(cell) for cell in row]
                    for row in raw_rows
                    if isinstance(row, list) and any(clean_text(cell) for cell in row)
                ]
                if not rows:
                    continue
                found_bbox = list(found.bbox) if getattr(found, "bbox", None) else list(bbox[:4])
                table_model = build_table_model_from_pdfplumber_table(found, rows, bbox=found_bbox)
                if table_model.get("row_count") and table_model.get("col_count"):
                    return table_model.get("rows") or rows, table_model
    except Exception:
        return None
    return None


def regions_to_parsed_tables(
    regions: list[CandidateTableRegion],
    source_tables: list[ParsedTable] | None = None,
    pdf_path: str | Path | None = None,
) -> list[ParsedTable]:
    source_by_id = {table.table_id: table for table in source_tables or []}
    tables: list[ParsedTable] = []
    for index, region in enumerate(regions, start=1):
        source = next((source_by_id[table_id] for table_id in region.source_table_ids if table_id in source_by_id), None)
        rows = source.rows if source else []
        table_model = getattr(source, "table_model", None) if source else None
        if not source:
            pdfplumber_result = _extract_pdfplumber_table_from_bbox(
                pdf_path=pdf_path,
                page_no=region.page_no,
                bbox=region.expanded_bbox or region.bbox,
            )
            if pdfplumber_result:
                rows, table_model = pdfplumber_result
        if not table_model:
            table_model = build_table_model_from_rows(rows, source="candidate_region", bbox=region.expanded_bbox or region.bbox)
        source_type = "pp_structure_table" if "pp_structure" in region.detectors and not source and not _is_pdfplumber_table_model(table_model) else "pdf_table"
        tables.append(
            ParsedTable(
                table_id=region.region_id or make_stable_id("table-region", region.page_no, index, region.bbox),
                page_no=region.page_no,
                rows=rows,
                bbox=region.expanded_bbox or region.bbox,
                source_type=source_type,
                table_model=table_model,
                table_region_bbox=region.bbox,
                table_region_expanded_bbox=region.expanded_bbox,
                table_region_confidence=region.confidence,
                candidate_detectors=region.detectors,
                candidate_evidence=region.evidence,
                table_image_path=region.crop_image_path,
                page_width=region.page_width,
                page_height=region.page_height,
            )
        )
    return tables


def groups_to_parsed_tables(
    groups: list[CandidateTableGroup],
    source_tables: list[ParsedTable] | None = None,
    pdf_path: str | Path | None = None,
) -> list[ParsedTable]:
    source_by_id = {table.table_id: table for table in source_tables or []}
    tables: list[ParsedTable] = []
    for group in groups:
        for part_index, region in enumerate(group.regions, start=1):
            source = next((source_by_id[table_id] for table_id in region.source_table_ids if table_id in source_by_id), None)
            rows = source.rows if source else []
            table_model = getattr(source, "table_model", None) if source else None
            if not source:
                pdfplumber_result = _extract_pdfplumber_table_from_bbox(
                    pdf_path=pdf_path,
                    page_no=region.page_no,
                    bbox=region.expanded_bbox or region.bbox,
                )
                if pdfplumber_result:
                    rows, table_model = pdfplumber_result
            if not table_model:
                table_model = build_table_model_from_rows(rows, source="candidate_table_group", bbox=region.expanded_bbox or region.bbox)
            source_type = "pp_structure_table" if "pp_structure" in region.detectors and not source and not _is_pdfplumber_table_model(table_model) else "pdf_table"
            tables.append(
                ParsedTable(
                    table_id=f"{group.group_id}-p{part_index}" if len(group.regions) > 1 else group.group_id,
                    page_no=region.page_no,
                    rows=rows,
                    bbox=region.expanded_bbox or region.bbox,
                    source_type=source_type,
                    table_model=table_model,
                    table_group_id=group.group_id,
                    table_group_start_page=group.start_page,
                    table_group_end_page=group.end_page,
                    table_group_part_index=part_index,
                    table_group_part_count=len(group.regions),
                    table_group_is_cross_page=group.is_cross_page,
                    table_group_bbox_by_page=group.bbox_by_page,
                    table_region_bbox=region.bbox,
                    table_region_expanded_bbox=region.expanded_bbox,
                    table_region_confidence=region.confidence,
                    candidate_detectors=region.detectors,
                    candidate_evidence=region.evidence,
                    table_image_path=region.crop_image_path,
                    should_parse_table=group.should_parse_table,
                    table_group_kind=group.group_kind,
                )
            )
    return tables
