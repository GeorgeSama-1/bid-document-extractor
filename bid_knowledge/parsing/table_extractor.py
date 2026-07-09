from __future__ import annotations

from pathlib import Path
from typing import Callable

from bid_knowledge.parsing.table_model import build_table_model_from_pdfplumber_table
from bid_knowledge.schemas.models import ParsedTable, ProcessingPlan
from bid_knowledge.utils.id_utils import make_stable_id
from bid_knowledge.utils.io_utils import write_json
from bid_knowledge.utils.text_utils import clean_text


def _collect_target_pages(plan: ProcessingPlan | None) -> list[int]:
    if plan is None:
        return []
    pages: set[int] = set()
    for item in plan.sections:
        if item.parse_table and item.expected_pages:
            pages.update(page for page in item.expected_pages if page > 0)
    return sorted(pages)


def extract_tables(
    pdf_path: str | Path,
    plan: ProcessingPlan | None = None,
    out_path: str | Path | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[ParsedTable]:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency-driven
        raise RuntimeError("需要先安装 pdfplumber 才能抽取表格。") from exc

    source_path = Path(pdf_path)
    if not source_path.exists():
        raise FileNotFoundError(f"PDF 文件不存在: {source_path}")

    target_pages = _collect_target_pages(plan)
    tables: list[ParsedTable] = []
    with pdfplumber.open(source_path) as pdf:
        selected = target_pages or list(range(1, len(pdf.pages) + 1))
        total_pages = len(selected)
        for index, page_no in enumerate(selected, start=1):
            if page_no < 1 or page_no > len(pdf.pages):
                continue
            page = pdf.pages[page_no - 1]
            try:
                found_tables = page.find_tables()
            except Exception:
                found_tables = []
            for table_index, found in enumerate(found_tables):
                rows = found.extract() or []
                normalized_rows = [
                    [clean_text(cell) for cell in row]
                    for row in rows
                    if any(clean_text(cell) for cell in row)
                ]
                if not normalized_rows:
                    continue
                bbox = list(found.bbox) if getattr(found, "bbox", None) else None
                table_model = build_table_model_from_pdfplumber_table(found, normalized_rows, bbox=bbox)
                tables.append(
                    ParsedTable(
                        table_id=make_stable_id("table", page_no, table_index),
                        page_no=page_no,
                        rows=table_model.get("rows") or normalized_rows,
                        bbox=bbox,
                        table_model=table_model,
                    )
                )
            if progress_callback:
                progress_callback(index, total_pages)

    if out_path:
        write_json(out_path, tables)
    return tables
