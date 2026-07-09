from __future__ import annotations

from typing import Any


LAYOUT_MASK_LABELS = {"header", "footer", "number", "header_image", "footer_image", "footnote"}


def _result_page_no(result: dict[str, Any], payload: dict[str, Any]) -> int:
    page_index = result.get("page_index", payload.get("page_index"))
    if page_index is not None:
        try:
            return int(page_index) + 1
        except (TypeError, ValueError):
            pass
    return 1


def _bbox(values: Any) -> list[float]:
    if not isinstance(values, list | tuple):
        return []
    result: list[float] = []
    for value in values[:4]:
        try:
            result.append(float(value))
        except (TypeError, ValueError):
            return []
    return result if len(result) == 4 else []


def build_layout_masks(pp_structure_results: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    masks: list[dict[str, Any]] = []
    seen: set[tuple[int, str, tuple[float, ...]]] = set()
    for result in pp_structure_results or []:
        payload = result.get("res") if isinstance(result.get("res"), dict) else result
        if not isinstance(payload, dict):
            continue
        page_no = _result_page_no(result, payload)
        page_width = payload.get("width")
        page_height = payload.get("height")

        for block in payload.get("parsing_res_list") or []:
            label = str(block.get("block_label") or "")
            if label not in LAYOUT_MASK_LABELS:
                continue
            bbox = _bbox(block.get("block_bbox"))
            if not bbox:
                continue
            key = (page_no, label, tuple(round(value, 2) for value in bbox))
            if key in seen:
                continue
            seen.add(key)
            masks.append({"page_no": page_no, "label": label, "bbox": bbox, "page_width": page_width, "page_height": page_height})

        for box in (payload.get("layout_det_res") or {}).get("boxes") or []:
            label = str(box.get("label") or "")
            if label not in LAYOUT_MASK_LABELS:
                continue
            bbox = _bbox(box.get("coordinate"))
            if not bbox:
                continue
            key = (page_no, label, tuple(round(value, 2) for value in bbox))
            if key in seen:
                continue
            seen.add(key)
            masks.append({"page_no": page_no, "label": label, "bbox": bbox, "page_width": page_width, "page_height": page_height})
    return masks
