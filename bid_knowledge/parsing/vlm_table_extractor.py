from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import re
from threading import Lock
from pathlib import Path
from typing import Any, Callable

from bid_knowledge.parsing.table_model import looks_like_sparse_fragmented_table
from bid_knowledge.schemas.models import ParsedTable
from bid_knowledge.utils.io_utils import ensure_dir, write_json


TABLE_TO_JSON_PROMPT = """你是表格结构识别引擎。请识别图片中的表格，并返回严格 JSON。

任务目标：
从图片中恢复可复用的二维表格结构，而不是只提取文字。

必须遵守：
1. 只能返回一个 JSON object，不能返回 Markdown、解释、列表或普通文本。
2. 第一个字符必须是 {，最后一个字符必须是 }。
3. 必须返回 row_count、col_count、cells、merged_cells。
4. cells 中每个单元格必须包含 row、col、text、rowspan、colspan。
5. row 和 col 从 0 开始。
6. 必须保留空单元格的位置。
7. 如果存在合并单元格，必须用 rowspan / colspan 表达，并同步写入 merged_cells。
8. 不要只提取文字；不要把同一列里的文字拆成多行普通文本。
9. 不要因为表格线不明显就退化成纯文字识别。
10. 表头、多级表头、单位、备注都要尽量保留在对应单元格中。
11. 如果某个单元格中包含图片、截图、证照、印章或照片，请只把该单元格 text 写为 "[图片]"。
12. 不要识别、转写、总结图片内部文字；图片只用于判断单元格位置和表格结构。
13. 如果图片与表格线、表头或其他单元格同处一个表格区域，请仍然按一个完整表格恢复，不要把图片周围的表格文字拆成普通文本。

返回格式：
{
  "row_count": 0,
  "col_count": 0,
  "cells": [
    {"row": 0, "col": 0, "text": "", "rowspan": 1, "colspan": 1}
  ],
  "merged_cells": []
}
"""

TABLE_TO_JSON_RETRY_PROMPT = TABLE_TO_JSON_PROMPT + """

重新识别：上一次输出没有返回可用的表格结构。
请不要只提取文字内容。必须把表格恢复为二维结构：
- 每个单元格必须放入 cells。
- 每个 cells 元素必须包含 row、col、text、rowspan、colspan。
- row_count 和 col_count 必须大于 0。
- 如果某个单元格为空，也要保留它在表格中的位置。
- 如果单元格中是图片，text 写为 "[图片]"，不要读取图片内部文字。
- 图片只用于判断单元格位置和表格结构，不要识别、转写、总结图片内部文字。
- 只返回一个 JSON object，不能返回 Markdown 表格、解释、列表或普通文本。"""


def _extract_response_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if content is None:
        content = message.get("reasoning_content")
    if isinstance(content, list):
        return "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict) and item.get("type") == "text")
    return str(content or "")


def _strip_json_fence(text: str) -> str:
    stripped = str(text or "").strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    return stripped


def _table_model_from_rows(rows: list[list[Any]], *, source: str) -> dict[str, Any]:
    normalized_rows = [[str(cell or "").strip() for cell in row] for row in rows if isinstance(row, list)]
    row_count = len(normalized_rows)
    col_count = max((len(row) for row in normalized_rows), default=0)
    padded_rows = [row + [""] * (col_count - len(row)) for row in normalized_rows]
    cells: list[dict[str, Any]] = []
    for row_index, row in enumerate(padded_rows):
        for col_index, text in enumerate(row):
            cells.append(
                {
                    "row": row_index,
                    "col": col_index,
                    "text": text,
                    "rowspan": 1,
                    "colspan": 1,
                    "bbox": None,
                }
            )
    return {
        "schema_version": "table_model_v1",
        "source": source,
        "row_count": row_count,
        "col_count": col_count,
        "rows": padded_rows,
        "cells": cells,
        "merged_cells": [],
        "bbox": None,
        "raw_html": "",
        "preserves_spans": False,
    }


def _parse_table_model_text(text: str) -> dict[str, Any]:
    payload = json.loads(_strip_json_fence(text))
    if isinstance(payload, list):
        return _table_model_from_rows(payload, source="vlm_rows_json")
    if isinstance(payload, dict) and isinstance(payload.get("table_model"), dict):
        payload = payload["table_model"]
    if not isinstance(payload, dict):
        raise ValueError("VLM table response is not a JSON object.")
    cells = payload.get("cells")
    if not isinstance(cells, list):
        raise ValueError("VLM table response must contain cells list.")
    row_count = int(payload.get("row_count") or 0)
    col_count = int(payload.get("col_count") or 0)
    normalized_cells: list[dict[str, Any]] = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        text = str(cell.get("text") or "")
        image_ref = str(cell.get("image_ref") or "").strip()
        if image_ref and not text:
            text = "[图片]"
        normalized_cell = {
            "row": int(cell.get("row") or 0),
            "col": int(cell.get("col") or 0),
            "text": text,
            "rowspan": max(1, int(cell.get("rowspan") or 1)),
            "colspan": max(1, int(cell.get("colspan") or 1)),
            "bbox": cell.get("bbox"),
        }
        if image_ref:
            normalized_cell["image_ref"] = image_ref
        normalized_cells.append(normalized_cell)
    if normalized_cells:
        row_count = max(
            0,
            max(int(cell["row"]) + int(cell["rowspan"]) for cell in normalized_cells),
        )
        col_count = max(
            0,
            max(int(cell["col"]) + int(cell["colspan"]) for cell in normalized_cells),
        )
    rows = [["" for _ in range(col_count)] for _ in range(row_count)]
    for cell in normalized_cells:
        row = int(cell["row"])
        col = int(cell["col"])
        if 0 <= row < row_count and 0 <= col < col_count:
            rows[row][col] = str(cell.get("text") or "")
    merged_cells = payload.get("merged_cells")
    if not isinstance(merged_cells, list):
        merged_cells = [
            {
                "row": cell["row"],
                "col": cell["col"],
                "rowspan": cell["rowspan"],
                "colspan": cell["colspan"],
            }
            for cell in normalized_cells
            if int(cell["rowspan"]) > 1 or int(cell["colspan"]) > 1
        ]
    return {
        "schema_version": "table_model_v1",
        "source": "vlm",
        "row_count": row_count,
        "col_count": col_count,
        "rows": rows,
        "cells": normalized_cells,
        "merged_cells": merged_cells,
        "bbox": payload.get("bbox"),
        "raw_html": str(payload.get("raw_html") or payload.get("html") or ""),
        "preserves_spans": bool(merged_cells),
    }


def _table_model_rows(model: dict[str, Any] | None) -> list[list[Any]]:
    if not isinstance(model, dict):
        return []
    rows = model.get("rows")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, list)]
    row_count = int(model.get("row_count") or 0)
    col_count = int(model.get("col_count") or 0)
    cells = model.get("cells")
    if row_count <= 0 or col_count <= 0 or not isinstance(cells, list):
        return []
    grid = [["" for _ in range(col_count)] for _ in range(row_count)]
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        try:
            row = int(cell.get("row") or 0)
            col = int(cell.get("col") or 0)
        except (TypeError, ValueError):
            continue
        if 0 <= row < row_count and 0 <= col < col_count:
            grid[row][col] = str(cell.get("text") or "")
    return grid


def _non_empty_text_count(model: dict[str, Any] | None) -> int:
    if not isinstance(model, dict):
        return 0
    row_text_count = sum(1 for row in _table_model_rows(model) for cell in row if str(cell or "").strip())
    cells = model.get("cells")
    if isinstance(cells, list):
        cell_text_count = sum(1 for cell in cells if isinstance(cell, dict) and str(cell.get("text") or "").strip())
        return max(row_text_count, cell_text_count)
    return row_text_count


def _table_model_quality_score(model: dict[str, Any] | None) -> int:
    if not isinstance(model, dict):
        return -1000
    rows = _table_model_rows(model)
    row_count = int(model.get("row_count") or len(rows) or 0)
    col_count = int(model.get("col_count") or max((len(row) for row in rows), default=0) or 0)
    non_empty_count = _non_empty_text_count(model)
    if row_count <= 0 or col_count <= 0 or non_empty_count <= 0:
        return -1000

    score = row_count * 3 + col_count * 4 + non_empty_count
    if col_count <= 1:
        score -= 80
    if row_count <= 1:
        score -= 40
    if bool(model.get("preserves_spans")):
        score += 45
    merged_cells = model.get("merged_cells")
    if isinstance(merged_cells, list) and merged_cells:
        score += min(30, len(merged_cells) * 5)
    if looks_like_sparse_fragmented_table(rows):
        score -= 55
    return score


def _has_non_empty_vlm_table_model(model: dict[str, Any] | None) -> bool:
    if not isinstance(model, dict):
        return False
    rows = _table_model_rows(model)
    row_count = int(model.get("row_count") or len(rows) or 0)
    col_count = int(model.get("col_count") or max((len(row) for row in rows), default=0) or 0)
    non_empty_count = _non_empty_text_count(model)
    return row_count > 0 and col_count > 0 and non_empty_count > 0


def _select_table_model(
    *,
    original_model: dict[str, Any] | None,
    vlm_model: dict[str, Any],
) -> tuple[dict[str, Any], bool, int, int]:
    original_score = _table_model_quality_score(original_model)
    vlm_score = _table_model_quality_score(vlm_model)
    if _has_non_empty_vlm_table_model(vlm_model) or not isinstance(original_model, dict) or original_score <= -1000:
        return vlm_model, True, original_score, vlm_score
    return original_model, False, original_score, vlm_score


def _image_to_data_uri(image_path: str | Path) -> str:
    raw = Path(image_path).read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _coerce_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) < 4:
        return None
    try:
        x0, y0, x1, y1 = [float(item) for item in value[:4]]
    except (TypeError, ValueError):
        return None
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


def _bbox_overlap_area(a: list[float], b: list[float]) -> float:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _embedded_images_for_table(table: ParsedTable, images: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    table_bbox = _coerce_bbox(getattr(table, "bbox", None))
    if not table_bbox:
        return []
    matches: list[dict[str, Any]] = []
    for image in images or []:
        if int(image.get("page_no") or 0) != int(table.page_no):
            continue
        image_bbox = _coerce_bbox(image.get("rect") or image.get("bbox"))
        if not image_bbox:
            continue
        image_area = max(1.0, (image_bbox[2] - image_bbox[0]) * (image_bbox[3] - image_bbox[1]))
        if _bbox_overlap_area(table_bbox, image_bbox) / image_area >= 0.8:
            matches.append(image)
    return matches


def _embedded_image_ref(image: dict[str, Any], fallback_index: int) -> str:
    image_id = str(image.get("image_id") or f"image-{fallback_index}").strip() or f"image-{fallback_index}"
    safe_id = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", image_id).strip("._") or f"image-{fallback_index}"
    ext = str(image.get("ext") or "png").lower().strip(".") or "png"
    if ext not in {"png", "jpg", "jpeg", "webp"}:
        ext = "png"
    return f"embedded_images/{safe_id}.{ext}"


def _attach_embedded_image_refs(table_model: dict[str, Any], embedded_images: list[dict[str, Any]]) -> dict[str, Any]:
    if not embedded_images or not isinstance(table_model, dict):
        return table_model
    refs = [_embedded_image_ref(image, index) for index, image in enumerate(embedded_images, start=1)]
    cells = table_model.get("cells")
    if not isinstance(cells, list):
        return table_model
    ref_index = 0
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        text = str(cell.get("text") or "").strip()
        if text == "[图片]" and not cell.get("image_ref") and ref_index < len(refs):
            cell["image_ref"] = refs[ref_index]
            ref_index += 1
    return table_model


def _draw_embedded_image_placeholders(page: Any, fitz_module: Any, images: list[dict[str, Any]]) -> None:
    for image in images:
        bbox = _coerce_bbox(image.get("rect") or image.get("bbox"))
        if not bbox:
            continue
        rect = fitz_module.Rect(*bbox)
        page.draw_rect(rect, color=(0, 0, 0), fill=(1, 1, 1), width=0.8, overlay=True)
        page.insert_textbox(
            rect,
            "[图片]",
            fontsize=max(8, min(18, rect.height * 0.35)),
            align=1,
            color=(0, 0, 0),
            overlay=True,
        )


def _render_table_crop(
    *,
    pdf_path: str | Path,
    table: ParsedTable,
    out_dir: str | Path,
    zoom: float = 2.0,
    embedded_images: list[dict[str, Any]] | None = None,
) -> Path:
    if not table.bbox or len(table.bbox) < 4:
        raise ValueError(f"table {table.table_id} has no bbox.")
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - dependency-driven
        raise RuntimeError("需要安装 PyMuPDF 才能裁剪表格区域。") from exc

    target_dir = ensure_dir(out_dir)
    image_path = target_dir / f"{table.table_id}.png"
    doc = fitz.open(str(pdf_path))
    try:
        page = doc.load_page(int(table.page_no) - 1)
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        bbox = [float(value) for value in table.bbox[:4]]
        source_width = getattr(table, "page_width", None)
        source_height = getattr(table, "page_height", None)
        try:
            source_width_value = float(source_width) if source_width else None
            source_height_value = float(source_height) if source_height else None
        except (TypeError, ValueError):
            source_width_value = None
            source_height_value = None
        if source_width_value and source_height_value:
            scale_x = page_width / source_width_value
            scale_y = page_height / source_height_value
            bbox = [bbox[0] * scale_x, bbox[1] * scale_y, bbox[2] * scale_x, bbox[3] * scale_y]
        clip = fitz.Rect(*bbox)
        if embedded_images:
            _draw_embedded_image_placeholders(page, fitz, embedded_images)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
        pix.save(image_path)
    finally:
        doc.close()
    return image_path


def _call_vlm_table_model(
    *,
    image_path: str | Path,
    endpoint: str,
    model: str,
    api_key: str | None = None,
    request_timeout: int = 180,
    max_tokens: int = 4096,
) -> tuple[dict[str, Any], dict[str, Any]]:
    import requests

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    image_data_uri = _image_to_data_uri(image_path)
    last_error: Exception | None = None
    last_raw_response: dict[str, Any] | None = None

    for attempt, prompt in enumerate([TABLE_TO_JSON_PROMPT, TABLE_TO_JSON_RETRY_PROMPT], start=1):
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a table recognition engine. Return strict JSON only."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data_uri}},
                    ],
                },
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "enable_thinking": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        response = requests.post(endpoint, headers=headers, json=payload, timeout=request_timeout)
        response.raise_for_status()
        raw_response = response.json()
        last_raw_response = raw_response
        try:
            table_model = _parse_table_model_text(_extract_response_text(raw_response))
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            if attempt == 1:
                continue
            raise
        if _has_non_empty_vlm_table_model(table_model):
            if attempt > 1:
                table_model["vlm_retry_count"] = attempt - 1
                raw_response["vlm_retry_count"] = attempt - 1
            return table_model, raw_response
        last_error = ValueError("VLM table response did not contain non-empty table structure.")
        if attempt == 1:
            continue

    if last_error:
        if last_raw_response is not None:
            raise ValueError(f"{last_error}; last_response={_extract_response_text(last_raw_response)[:500]}") from last_error
        raise last_error
    raise ValueError("VLM table response did not contain non-empty table structure.")


def enhance_tables_with_vlm(
    *,
    pdf_path: str | Path,
    tables: list[ParsedTable],
    images: list[dict[str, Any]] | None = None,
    out_dir: str | Path,
    endpoint: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    api_key_env: str | None = None,
    request_timeout: int = 180,
    max_tokens: int = 4096,
    incremental_out_path: str | Path | None = None,
    workers: int = 1,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[ParsedTable]:
    endpoint = endpoint or os.getenv("VLM_TABLE_ENDPOINT")
    model = model or os.getenv("VLM_TABLE_MODEL")
    api_key = api_key or (os.getenv(api_key_env) if api_key_env else None) or os.getenv("VLM_TABLE_API_KEY")
    if not endpoint or not model:
        return tables

    crop_dir = ensure_dir(Path(out_dir) / "table_crops")
    enhanced: list[ParsedTable] = list(tables)
    total = len(tables)
    write_lock = Lock()

    def process_one(index_and_table: tuple[int, ParsedTable]) -> tuple[int, ParsedTable]:
        _index, table = index_and_table
        try:
            embedded_images = _embedded_images_for_table(table, images)
            existing_image_path = getattr(table, "table_image_path", "")
            if existing_image_path and Path(existing_image_path).exists() and not embedded_images:
                image_path = Path(existing_image_path)
            else:
                render_kwargs = {
                    "pdf_path": pdf_path,
                    "table": table,
                    "out_dir": crop_dir,
                    "zoom": 2.0,
                }
                if embedded_images:
                    render_kwargs["embedded_images"] = embedded_images
                image_path = _render_table_crop(**render_kwargs)
            table_model, raw_response = _call_vlm_table_model(
                image_path=image_path,
                endpoint=endpoint,
                model=model,
                api_key=api_key,
                request_timeout=request_timeout,
                max_tokens=max_tokens,
            )
            table_model = _attach_embedded_image_refs(table_model, embedded_images)
            data = table.model_dump()
            original_model = data.get("table_model")
            selected_model, vlm_selected, original_score, vlm_score = _select_table_model(
                original_model=original_model if isinstance(original_model, dict) else None,
                vlm_model=table_model,
            )
            selected_source = "vlm" if vlm_selected else str(selected_model.get("source") or data.get("table_model_source") or data.get("source_type") or "")
            data.update(
                {
                    "rows": selected_model.get("rows") or table.rows,
                    "table_model": selected_model,
                    "vlm_table_model": table_model,
                    "vlm_raw_response": raw_response,
                    "vlm_error": None,
                    "vlm_selected": vlm_selected,
                    "vlm_quality_score": vlm_score,
                    "original_table_quality_score": original_score,
                    "table_model_source": selected_source,
                    "table_image_path": str(image_path),
                    "embedded_image_refs": [
                        {**image, "image_ref": _embedded_image_ref(image, index)}
                        for index, image in enumerate(embedded_images, start=1)
                    ],
                }
            )
            return _index, ParsedTable(**data)
        except Exception as exc:
            data = table.model_dump()
            data.update({"vlm_error": str(exc), "table_model_source": data.get("table_model_source") or data.get("source_type")})
            return _index, ParsedTable(**data)

    completed = 0
    max_workers = max(1, int(workers or 1))
    if max_workers == 1:
        for index, table in enumerate(tables):
            result_index, result = process_one((index, table))
            enhanced[result_index] = result
            completed += 1
            if incremental_out_path:
                with write_lock:
                    write_json(incremental_out_path, enhanced)
            if progress_callback:
                progress_callback(completed, total)
        return enhanced

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_one, item) for item in enumerate(tables)]
        for future in as_completed(futures):
            result_index, result = future.result()
            enhanced[result_index] = result
            completed += 1
            if incremental_out_path:
                with write_lock:
                    write_json(incremental_out_path, enhanced)
            if progress_callback:
                progress_callback(completed, total)
    return enhanced
