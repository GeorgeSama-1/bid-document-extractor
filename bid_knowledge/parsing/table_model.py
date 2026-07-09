from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

from bid_knowledge.utils.text_utils import clean_text


class _HTMLTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.raw_rows: list[list[dict[str, Any]]] = []
        self._current_row: list[dict[str, Any]] | None = None
        self._current_cell: dict[str, Any] | None = None
        self._cell_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._current_row = []
            return
        if tag not in {"td", "th"} or self._current_row is None:
            return
        attr_map = {name.lower(): value for name, value in attrs}
        self._current_cell = {
            "rowspan": _positive_int(attr_map.get("rowspan"), 1),
            "colspan": _positive_int(attr_map.get("colspan"), 1),
            "is_header": tag == "th",
        }
        self._cell_text = []

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._current_row is not None and self._current_cell is not None:
            self._current_cell["text"] = clean_text("".join(self._cell_text))
            self._current_row.append(self._current_cell)
            self._current_cell = None
            self._cell_text = []
            return
        if tag == "tr" and self._current_row is not None:
            if any(clean_text(cell.get("text") or "") for cell in self._current_row):
                self.raw_rows.append(self._current_row)
            self._current_row = None


def _positive_int(value: Any, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def _normalize_bbox(bbox: list[float] | None) -> list[float] | None:
    if not bbox or len(bbox) < 4:
        return None
    return [float(value) for value in bbox[:4]]


def _empty_model(*, source: str, bbox: list[float] | None = None, raw_html: str = "") -> dict[str, Any]:
    return {
        "schema_version": "table_model_v1",
        "source": source,
        "row_count": 0,
        "col_count": 0,
        "rows": [],
        "cells": [],
        "merged_cells": [],
        "bbox": _normalize_bbox(bbox),
        "raw_html": raw_html,
        "preserves_spans": False,
    }


def build_table_model_from_rows(
    rows: list[list[Any]],
    *,
    source: str,
    bbox: list[float] | None = None,
) -> dict[str, Any]:
    normalized_rows = [[clean_text(cell) for cell in row] for row in rows if isinstance(row, list)]
    col_count = max((len(row) for row in normalized_rows), default=0)
    padded_rows = [row + [""] * (col_count - len(row)) for row in normalized_rows]
    cells: list[dict[str, Any]] = []
    for row_index, row in enumerate(padded_rows):
        for col_index, text in enumerate(row):
            cells.append(
                {
                    "row": row_index,
                    "col": col_index,
                    "rowspan": 1,
                    "colspan": 1,
                    "text": text,
                    "is_header": row_index == 0,
                    "bbox": None,
                }
            )
    return {
        "schema_version": "table_model_v1",
        "source": source,
        "row_count": len(padded_rows),
        "col_count": col_count,
        "rows": padded_rows,
        "cells": cells,
        "merged_cells": [],
        "bbox": _normalize_bbox(bbox),
        "raw_html": "",
        "preserves_spans": False,
    }


def looks_like_sparse_fragmented_table(rows: list[list[Any]]) -> bool:
    normalized_rows = [[clean_text(cell) for cell in row] for row in rows if isinstance(row, list)]
    width = max((len(row) for row in normalized_rows), default=0)
    if width < 6:
        return False
    total_cells = sum(len(row) for row in normalized_rows)
    if total_cells <= 0:
        return False
    non_empty = [cell for row in normalized_rows for cell in row if cell]
    empty_ratio = (total_cells - len(non_empty)) / total_cells
    short_ratio = sum(1 for cell in non_empty if len(cell) <= 1) / max(len(non_empty), 1)
    return empty_ratio >= 0.55 or short_ratio >= 0.65


def _cell_bbox(cell: Any) -> list[float] | None:
    bbox = getattr(cell, "bbox", cell)
    if bbox is None or not isinstance(bbox, list | tuple) or len(bbox) < 4:
        return None
    try:
        x0, y0, x1, y1 = [float(value) for value in bbox[:4]]
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _coord_index(coords: list[float], value: float, *, tolerance: float = 1.0) -> int:
    for index, coord in enumerate(coords):
        if abs(coord - value) <= tolerance:
            return index
    return min(range(len(coords)), key=lambda index: abs(coords[index] - value))


def build_table_model_from_pdfplumber_table(
    table: Any,
    rows: list[list[Any]],
    *,
    bbox: list[float] | None = None,
) -> dict[str, Any]:
    row_groups = list(getattr(table, "rows", []) or [])
    geometry_rows: list[list[list[float]]] = []
    for row_group in row_groups:
        bboxes = [_cell_bbox(cell) for cell in (getattr(row_group, "cells", []) or [])]
        bboxes = [cell_bbox for cell_bbox in bboxes if cell_bbox]
        if bboxes:
            geometry_rows.append(bboxes)
    if not geometry_rows:
        return build_table_model_from_rows(rows, source="pdfplumber", bbox=bbox)

    x_edges = sorted({coord for row in geometry_rows for cell in row for coord in (cell[0], cell[2])})
    y_edges = sorted({coord for row in geometry_rows for cell in row for coord in (cell[1], cell[3])})
    if len(x_edges) < 2 or len(y_edges) < 2:
        return build_table_model_from_rows(rows, source="pdfplumber", bbox=bbox)

    normalized_rows = [[clean_text(cell) for cell in row] for row in rows if isinstance(row, list)]
    cells: list[dict[str, Any]] = []
    for visual_row_index, geometry_row in enumerate(geometry_rows):
        row_texts = normalized_rows[visual_row_index] if visual_row_index < len(normalized_rows) else []
        for visual_col_index, cell_bbox in enumerate(geometry_row):
            row_index = _coord_index(y_edges, cell_bbox[1])
            row_end_index = _coord_index(y_edges, cell_bbox[3])
            col_index = _coord_index(x_edges, cell_bbox[0])
            col_end_index = _coord_index(x_edges, cell_bbox[2])
            text = row_texts[visual_col_index] if visual_col_index < len(row_texts) else ""
            cells.append(
                {
                    "row": row_index,
                    "col": col_index,
                    "rowspan": max(1, row_end_index - row_index),
                    "colspan": max(1, col_end_index - col_index),
                    "text": text,
                    "is_header": row_index == 0,
                    "bbox": cell_bbox,
                }
            )

    row_count = len(y_edges) - 1
    col_count = len(x_edges) - 1
    grid = [["" for _ in range(col_count)] for _ in range(row_count)]
    for cell in cells:
        row_index = int(cell["row"])
        col_index = int(cell["col"])
        if 0 <= row_index < row_count and 0 <= col_index < col_count:
            grid[row_index][col_index] = str(cell.get("text") or "")

    merged_cells = [
        {
            "row": cell["row"],
            "col": cell["col"],
            "rowspan": cell["rowspan"],
            "colspan": cell["colspan"],
        }
        for cell in cells
        if int(cell.get("rowspan") or 1) > 1 or int(cell.get("colspan") or 1) > 1
    ]
    return {
        "schema_version": "table_model_v1",
        "source": "pdfplumber_geometry",
        "row_count": row_count,
        "col_count": col_count,
        "rows": grid,
        "cells": cells,
        "merged_cells": merged_cells,
        "bbox": _normalize_bbox(bbox),
        "raw_html": "",
        "preserves_spans": bool(merged_cells),
    }


def build_table_model_from_html(
    html: str,
    *,
    source: str = "pp_structure_html",
    bbox: list[float] | None = None,
) -> dict[str, Any]:
    if "<table" not in str(html or "").lower():
        return _empty_model(source=source, bbox=bbox, raw_html=str(html or ""))

    parser = _HTMLTableParser()
    parser.feed(str(html or ""))
    if not parser.raw_rows:
        return _empty_model(source=source, bbox=bbox, raw_html=str(html or ""))

    occupied: set[tuple[int, int]] = set()
    cells: list[dict[str, Any]] = []
    row_count = 0
    col_count = 0
    for row_index, raw_row in enumerate(parser.raw_rows):
        col_index = 0
        for raw_cell in raw_row:
            while (row_index, col_index) in occupied:
                col_index += 1
            rowspan = _positive_int(raw_cell.get("rowspan"), 1)
            colspan = _positive_int(raw_cell.get("colspan"), 1)
            cell = {
                "row": row_index,
                "col": col_index,
                "rowspan": rowspan,
                "colspan": colspan,
                "text": clean_text(raw_cell.get("text") or ""),
                "is_header": bool(raw_cell.get("is_header")),
                "bbox": None,
            }
            cells.append(cell)
            for row_offset in range(rowspan):
                for col_offset in range(colspan):
                    occupied.add((row_index + row_offset, col_index + col_offset))
            row_count = max(row_count, row_index + rowspan)
            col_count = max(col_count, col_index + colspan)
            col_index += colspan

    rows = [["" for _ in range(col_count)] for _ in range(row_count)]
    for cell in cells:
        rows[int(cell["row"])][int(cell["col"])] = str(cell.get("text") or "")

    merged_cells = [
        {
            "row": cell["row"],
            "col": cell["col"],
            "rowspan": cell["rowspan"],
            "colspan": cell["colspan"],
        }
        for cell in cells
        if int(cell.get("rowspan") or 1) > 1 or int(cell.get("colspan") or 1) > 1
    ]
    return {
        "schema_version": "table_model_v1",
        "source": source,
        "row_count": row_count,
        "col_count": col_count,
        "rows": rows,
        "cells": cells,
        "merged_cells": merged_cells,
        "bbox": _normalize_bbox(bbox),
        "raw_html": str(html or ""),
        "preserves_spans": bool(merged_cells),
    }
