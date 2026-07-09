from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from bid_knowledge.utils.io_utils import ensure_dir, write_json


def _to_plain(value: Any) -> Any:
    if hasattr(value, "tolist") and callable(getattr(value, "tolist")):
        return _to_plain(value.tolist())
    if hasattr(value, "item") and callable(getattr(value, "item")):
        try:
            return _to_plain(value.item())
        except (TypeError, ValueError):
            pass
    if isinstance(value, list | tuple):
        return [_to_plain(item) for item in value]
    return value


def _float_list(value: Any) -> list[float]:
    plain = _to_plain(value)
    if not isinstance(plain, list):
        return []
    result: list[float] = []
    for item in plain:
        try:
            result.append(float(item))
        except (TypeError, ValueError):
            continue
    return result


def _float_matrix(value: Any) -> list[list[float]]:
    plain = _to_plain(value)
    if not isinstance(plain, list):
        return []
    rows: list[list[float]] = []
    for row in plain:
        values = _float_list(row)
        if values:
            rows.append(values)
    return rows


def _int_or_none(value: Any) -> int | None:
    try:
        return int(_to_plain(value))
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(_to_plain(value))
    except (TypeError, ValueError):
        return None


def _slim_parsing_res_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block in payload.get("parsing_res_list") or []:
        if not isinstance(block, dict):
            continue
        blocks.append(
            {
                "block_label": str(block.get("block_label") or ""),
                "block_content": str(block.get("block_content") or ""),
                "block_bbox": _float_list(block.get("block_bbox") or block.get("bbox")),
                "block_id": block.get("block_id"),
                "block_order": _int_or_none(block.get("block_order")),
            }
        )
    return blocks


def _slim_layout_det_res(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("layout_det_res")
    if not isinstance(raw, dict):
        return {"boxes": []}
    boxes: list[dict[str, Any]] = []
    for box in raw.get("boxes") or []:
        if not isinstance(box, dict):
            continue
        boxes.append(
            {
                "cls_id": _int_or_none(box.get("cls_id")),
                "label": str(box.get("label") or ""),
                "score": _float_or_none(box.get("score")),
                "coordinate": _float_list(box.get("coordinate")),
            }
        )
    return {"boxes": boxes}


def _slim_overall_ocr_res(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("overall_ocr_res")
    if not isinstance(raw, dict):
        return {"rec_texts": [], "rec_scores": [], "text_type": ""}
    return {
        "rec_texts": [str(item) for item in (_to_plain(raw.get("rec_texts")) or [])],
        "rec_scores": _float_list(raw.get("rec_scores")),
        "rec_boxes": _float_matrix(raw.get("rec_boxes")),
        "text_type": str(raw.get("text_type") or ""),
    }


def _slim_doc_preprocessor_res(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("doc_preprocessor_res")
    if not isinstance(raw, dict):
        return {}
    return {
        "angle": _int_or_none(raw.get("angle")),
        "model_settings": _to_plain(raw.get("model_settings") or {}),
    }


def _slim_pp_structure_payload(payload: dict[str, Any], page_index: int) -> dict[str, Any]:
    return {
        "input_path": str(payload.get("input_path") or ""),
        "page_index": _int_or_none(payload.get("page_index")) if payload.get("page_index") is not None else page_index,
        "page_count": _int_or_none(payload.get("page_count")),
        "width": _int_or_none(payload.get("width")),
        "height": _int_or_none(payload.get("height")),
        "model_settings": _to_plain(payload.get("model_settings") or {}),
        "parsing_res_list": _slim_parsing_res_list(payload),
        "layout_det_res": _slim_layout_det_res(payload),
        "doc_preprocessor_res": _slim_doc_preprocessor_res(payload),
        "overall_ocr_res": _slim_overall_ocr_res(payload),
    }


def _normalize_pp_structure_result(result: Any, page_index: int) -> dict[str, Any]:
    if isinstance(result, dict):
        payload = result.get("res") if isinstance(result.get("res"), dict) else dict(result)
    elif hasattr(result, "res"):
        payload = getattr(result, "res")
    elif hasattr(result, "json"):
        raw = getattr(result, "json")
        payload = raw if isinstance(raw, dict) else {}
    else:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {"res": _slim_pp_structure_payload(payload, page_index), "page_index": page_index}


def run_pp_structure(
    input_path: str | Path,
    *,
    out_path: str | Path | None = None,
    device: str = "gpu",
    use_doc_orientation_classify: bool = False,
    use_doc_unwarping: bool = False,
    use_textline_orientation: bool = False,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    try:
        from paddleocr import PPStructureV3
    except ImportError as exc:  # pragma: no cover - dependency-driven
        raise RuntimeError("需要先安装 paddleocr[all] 才能运行 PP-StructureV3。") from exc

    pipeline = PPStructureV3(
        device=device,
        use_doc_orientation_classify=use_doc_orientation_classify,
        use_doc_unwarping=use_doc_unwarping,
        use_textline_orientation=use_textline_orientation,
    )
    expected_total = 1
    input_suffix = str(input_path).lower()
    if input_suffix.endswith(".pdf"):
        try:
            import fitz

            doc = fitz.open(str(input_path))
            try:
                expected_total = max(1, int(doc.page_count))
            finally:
                doc.close()
        except Exception:  # pragma: no cover - dependency/environment driven
            expected_total = 1

    results: list[dict[str, Any]] = []
    for index, result in enumerate(pipeline.predict(input=str(input_path)), start=1):
        results.append(_normalize_pp_structure_result(result, page_index=index - 1))
        if progress_callback:
            progress_callback(index, expected_total)
    if out_path:
        write_json(out_path, results)
    return results


def ensure_pp_structure_output_dir(out_dir: str | Path) -> Path:
    return ensure_dir(out_dir)
