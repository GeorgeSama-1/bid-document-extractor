from __future__ import annotations

from typing import Any

from bid_knowledge.schemas.models import PageMaterialItem, ParsedTable, PdfTextBlock


def _top_from_bbox(bbox: list[float] | None) -> float:
    if not bbox or len(bbox) < 2:
        return 0.0
    return float(bbox[1])


def _ocr_texts(pp_result: dict[str, Any]) -> list[str]:
    ocr = pp_result.get("overall_ocr_res") or {}
    if not isinstance(ocr, dict):
        return []
    return [str(text) for text in (ocr.get("rec_texts") or []) if str(text).strip()]


def _ocr_scores(pp_result: dict[str, Any]) -> list[float]:
    ocr = pp_result.get("overall_ocr_res") or {}
    if not isinstance(ocr, dict):
        return []
    scores: list[float] = []
    for score in ocr.get("rec_scores") or []:
        try:
            scores.append(float(score))
        except (TypeError, ValueError):
            continue
    return scores


def _ocr_boxes(pp_result: dict[str, Any]) -> list[list[float]]:
    ocr = pp_result.get("overall_ocr_res") or {}
    if not isinstance(ocr, dict):
        return []
    boxes: list[list[float]] = []
    for box in ocr.get("rec_boxes") or []:
        if not isinstance(box, list | tuple):
            continue
        values: list[float] = []
        for value in box:
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                continue
        if len(values) >= 4:
            boxes.append(values[:4])
    return boxes


def _box_center(box: list[float]) -> tuple[float, float]:
    return (float(box[0]) + float(box[2])) / 2.0, (float(box[1]) + float(box[3])) / 2.0


def _box_center_in_region(box: list[float], region: list[float]) -> bool:
    if len(box) < 4 or len(region) < 4:
        return False
    center_x, center_y = _box_center(box)
    tolerance = 4.0
    return (
        float(region[0]) - tolerance <= center_x <= float(region[2]) + tolerance
        and float(region[1]) - tolerance <= center_y <= float(region[3]) + tolerance
    )


def _is_margin_ocr_noise(text: str, box: list[float], page_height: int | None) -> bool:
    normalized = "".join(str(text or "").split())
    if not normalized:
        return True
    top = float(box[1]) if len(box) >= 2 else 0.0
    bottom = float(box[3]) if len(box) >= 4 else top
    near_top = top <= 90
    near_bottom = bool(page_height and bottom >= float(page_height) - 70)
    if near_top and any(keyword in normalized for keyword in ("NI.INF", "理工能科", "商务投标文件", "测控及在线监测系统")):
        return True
    if near_bottom and normalized.isdigit():
        return True
    return False


def _ocr_texts_in_region(
    *,
    texts: list[str],
    scores: list[float],
    boxes: list[list[float]],
    region: list[float],
    page_height: int | None,
) -> tuple[list[str], list[float]]:
    if not boxes:
        return [], []
    selected_texts: list[str] = []
    selected_scores: list[float] = []
    for index, text in enumerate(texts):
        if index >= len(boxes):
            continue
        box = boxes[index]
        if not _box_center_in_region(box, region):
            continue
        if _is_margin_ocr_noise(text, box, page_height):
            continue
        selected_texts.append(text)
        if index < len(scores):
            selected_scores.append(scores[index])
    return selected_texts, selected_scores


def build_page_material_stream(
    *,
    blocks: list[PdfTextBlock],
    tables: list[ParsedTable],
    images: list[dict[str, Any]],
) -> list[PageMaterialItem]:
    items: list[PageMaterialItem] = []

    for block in blocks:
        items.append(
            PageMaterialItem(
                item_id=block.block_id,
                item_type="text",
                source_type=block.source_type,
                page_no=block.page_no,
                top_y=_top_from_bbox(block.bbox),
                bbox=[float(value) for value in block.bbox],
                text=block.text,
                payload={
                    "block_id": block.block_id,
                    "block_no": block.block_no,
                    "font_size": block.font_size,
                    "confidence": block.confidence,
                },
            )
        )

    for table in tables:
        items.append(
            PageMaterialItem(
                item_id=table.table_id,
                item_type="table",
                source_type=table.source_type,
                page_no=table.page_no,
                top_y=_top_from_bbox(table.bbox),
                bbox=[float(value) for value in (table.bbox or [])],
                text="",
                payload={
                    "table_id": table.table_id,
                    "rows": table.rows,
                },
            )
        )

    for image in images:
        rect = [float(value) for value in (image.get("rect") or [])]
        items.append(
            PageMaterialItem(
                item_id=str(image.get("image_id") or ""),
                item_type="image",
                source_type="pdf_embedded_image",
                page_no=int(image.get("page_no") or 0),
                top_y=_top_from_bbox(rect),
                bbox=rect,
                text="",
                payload=dict(image),
            )
        )

    items = sorted(items, key=lambda item: (item.page_no, item.top_y, item.item_type))
    for index, item in enumerate(items, start=1):
        item.reading_order = index
    return items


def build_pp_structure_page_material_items(pp_result: dict[str, Any], *, page_no: int) -> list[PageMaterialItem]:
    items: list[PageMaterialItem] = []
    ocr_texts = _ocr_texts(pp_result)
    ocr_scores = _ocr_scores(pp_result)
    ocr_boxes = _ocr_boxes(pp_result)
    page_height = pp_result.get("height")
    page_width = pp_result.get("width")
    try:
        page_height_int = int(page_height) if page_height else None
    except (TypeError, ValueError):
        page_height_int = None

    for index, block in enumerate(pp_result.get("parsing_res_list") or [], start=1):
        label = str(block.get("block_label") or "")
        if label not in {"doc_title", "paragraph_title", "text"}:
            continue
        bbox = [float(value) for value in (block.get("block_bbox") or [])]
        items.append(
            PageMaterialItem(
                item_id=f"pp-text-{page_no}-{index}",
                item_type="text",
                source_type="pp_structure_text",
                page_no=page_no,
                reading_order=int(block.get("block_order") or index),
                top_y=_top_from_bbox(bbox),
                bbox=bbox,
                text=str(block.get("block_content") or ""),
                payload={
                    "layout_label": label,
                    "block_id": block.get("block_id"),
                },
            )
        )

    image_index = 0
    table_index = 0
    fallback_text_index = 0
    has_parsed_text = any(item.item_type == "text" for item in items)
    for box in (pp_result.get("layout_det_res") or {}).get("boxes") or []:
        label = str(box.get("label") or "")
        if label == "image":
            image_index += 1
            bbox = [float(value) for value in (box.get("coordinate") or [])]
            region_texts, region_scores = _ocr_texts_in_region(
                texts=ocr_texts,
                scores=ocr_scores,
                boxes=ocr_boxes,
                region=bbox,
                page_height=page_height_int,
            )
            items.append(
                PageMaterialItem(
                    item_id=f"pp-image-{page_no}-{image_index}",
                    item_type="image",
                    source_type="pp_structure_image_region",
                    page_no=page_no,
                    top_y=_top_from_bbox(bbox),
                    bbox=bbox,
                    text="",
                    payload={
                        "layout_label": "image",
                        "score": box.get("score"),
                        "ocr_texts": region_texts,
                        "ocr_scores": region_scores,
                        "page_width": page_width,
                        "page_height": page_height,
                    },
                )
            )
            continue

        if label == "table":
            table_index += 1
            bbox = [float(value) for value in (box.get("coordinate") or [])]
            items.append(
                PageMaterialItem(
                    item_id=f"pp-table-{page_no}-{table_index}",
                    item_type="table",
                    source_type="pp_structure_table_region",
                    page_no=page_no,
                    top_y=_top_from_bbox(bbox),
                    bbox=bbox,
                    text="",
                    payload={
                        "layout_label": "table",
                        "score": box.get("score"),
                        "page_width": page_width,
                        "page_height": page_height,
                    },
                )
            )
            continue

        if not has_parsed_text and label in {"doc_title", "paragraph_title", "text"}:
            fallback_text_index += 1
            bbox = [float(value) for value in (box.get("coordinate") or [])]
            region_texts, region_scores = _ocr_texts_in_region(
                texts=ocr_texts,
                scores=ocr_scores,
                boxes=ocr_boxes,
                region=bbox,
                page_height=page_height_int,
            )
            if not region_texts:
                continue
            items.append(
                PageMaterialItem(
                    item_id=f"pp-text-region-{page_no}-{fallback_text_index}",
                    item_type="text",
                    source_type="pp_structure_text_region",
                    page_no=page_no,
                    top_y=_top_from_bbox(bbox),
                    bbox=bbox,
                    text="\n".join(region_texts),
                    payload={
                        "layout_label": label,
                        "score": box.get("score"),
                        "ocr_texts": region_texts,
                        "ocr_scores": region_scores,
                        "page_width": page_width,
                        "page_height": page_height,
                    },
                )
            )

    items = sorted(items, key=lambda item: (item.page_no, item.top_y, item.item_type))
    for index, item in enumerate(items, start=1):
        item.reading_order = index
    return items


def build_combined_page_material_stream(
    *,
    blocks: list[PdfTextBlock],
    tables: list[ParsedTable],
    images: list[dict[str, Any]],
    pp_structure_results: list[dict[str, Any]] | None = None,
) -> list[PageMaterialItem]:
    items = build_page_material_stream(blocks=blocks, tables=tables, images=images)
    for result in pp_structure_results or []:
        payload = result.get("res") if isinstance(result, dict) and isinstance(result.get("res"), dict) else result
        page_index = int((result or {}).get("page_index") or payload.get("page_index") or 0)
        page_no = int(payload.get("page_no") or (page_index + 1))
        items.extend(build_pp_structure_page_material_items(payload, page_no=page_no))
    items = sorted(items, key=lambda item: (item.page_no, item.top_y, item.item_type, item.source_type))
    for index, item in enumerate(items, start=1):
        item.reading_order = index
    return items
