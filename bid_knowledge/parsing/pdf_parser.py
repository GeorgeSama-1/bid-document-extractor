from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from bid_knowledge.schemas.models import PdfTextBlock, ProcessingPlan
from bid_knowledge.utils.id_utils import make_stable_id
from bid_knowledge.utils.io_utils import ensure_dir, write_json
from bid_knowledge.utils.text_utils import clean_text


def _collect_ocr_pages(plan: ProcessingPlan | None) -> list[int]:
    if plan is None:
        return []
    pages: set[int] = set()
    for item in plan.sections:
        if item.use_ocr:
            pages.update(page for page in item.expected_pages if page > 0)
    return sorted(pages)


def render_pdf_pages(pdf_path: str | Path, page_numbers: list[int], out_dir: str | Path) -> list[dict[str, Any]]:
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - dependency-driven
        raise RuntimeError("需要先安装 PyMuPDF 才能渲染 PDF 页面。") from exc

    output_dir = ensure_dir(out_dir)
    rendered: list[dict[str, Any]] = []
    doc = fitz.open(pdf_path)
    try:
        for page_no in sorted(set(page_numbers)):
            if page_no < 1 or page_no > doc.page_count:
                continue
            page = doc.load_page(page_no - 1)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            target = output_dir / f"page_{page_no:04d}.png"
            pix.save(target)
            rendered.append({"page_no": page_no, "image_path": str(target)})
    finally:
        doc.close()
    return rendered


def parse_pdf(
    pdf_path: str | Path,
    plan: ProcessingPlan | None = None,
    out_dir: str | Path | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - dependency-driven
        raise RuntimeError("需要先安装 PyMuPDF 才能解析 PDF。") from exc

    source_path = Path(pdf_path)
    if not source_path.exists():
        raise FileNotFoundError(f"PDF 文件不存在: {source_path}")

    doc = fitz.open(source_path)
    text_blocks: list[PdfTextBlock] = []
    images: list[dict[str, Any]] = []
    try:
        metadata = dict(doc.metadata or {})
        meta = {
            "source_file": str(source_path),
            "page_count": doc.page_count,
            "metadata": metadata,
        }
        toc_entries = [
            {"level": level, "title": title, "page": page}
            for level, title, page in (doc.get_toc() or [])
        ]

        total_pages = doc.page_count
        for page_index in range(total_pages):
            page = doc.load_page(page_index)
            page_no = page_index + 1
            page_dict = page.get_text("dict")
            for block_no, block in enumerate(page_dict.get("blocks", [])):
                if block.get("type") != 0:
                    continue
                lines = []
                font_sizes: list[float] = []
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    line_text = "".join(span.get("text", "") for span in spans).strip()
                    if line_text:
                        lines.append(line_text)
                    font_sizes.extend(float(span.get("size", 0)) for span in spans if span.get("size"))
                text = "\n".join(lines).strip()
                if not text:
                    continue
                text_blocks.append(
                    PdfTextBlock(
                        block_id=make_stable_id("block", page_no, block_no, text[:80]),
                        page_no=page_no,
                        text=text,
                        bbox=[float(value) for value in block.get("bbox", [])[:4]],
                        block_no=block_no,
                        source_type="pdf_text",
                        font_size=max(font_sizes) if font_sizes else None,
                    )
                )

            for image_index, image_info in enumerate(page.get_images(full=True)):
                xref = image_info[0]
                rects = page.get_image_rects(xref)
                normalized_rects = [
                    [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]
                    for rect in rects
                ]
                images.append(
                    {
                        "image_id": make_stable_id("image", page_no, image_index, xref),
                        "page_no": page_no,
                        "xref": xref,
                        "width": image_info[2],
                        "height": image_info[3],
                        "bpc": image_info[4],
                        "colorspace": image_info[5],
                        "ext": image_info[7],
                        "rect": normalized_rects[0] if normalized_rects else None,
                        "rects": normalized_rects,
                    }
                )
            if progress_callback:
                progress_callback(page_index + 1, total_pages)
    finally:
        doc.close()

    rendered_pages: list[dict[str, Any]] = []
    if out_dir:
        target_dir = Path(out_dir)
        ensure_dir(target_dir)
        write_json(target_dir / "document_meta.json", meta)
        write_json(target_dir / "toc.json", toc_entries)
        write_json(target_dir / "text_blocks.json", text_blocks)
        write_json(target_dir / "images.json", images)
        ocr_pages = _collect_ocr_pages(plan)
        if ocr_pages:
            rendered_pages = render_pdf_pages(source_path, ocr_pages, target_dir / "page_images")

    return {
        "document_meta": meta,
        "toc": toc_entries,
        "text_blocks": text_blocks,
        "images": images,
        "rendered_pages": rendered_pages,
    }
