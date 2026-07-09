from __future__ import annotations

from pathlib import Path

from bid_knowledge.schemas.models import OCRResult, PdfTextBlock
from bid_knowledge.utils.id_utils import make_stable_id
from bid_knowledge.utils.io_utils import write_json


def merge_ocr_results(
    text_blocks: list[PdfTextBlock],
    ocr_results: list[OCRResult],
    out_path: str | Path | None = None,
) -> list[PdfTextBlock]:
    merged = list(text_blocks)
    max_block_no_by_page: dict[int, int] = {}
    for block in text_blocks:
        max_block_no_by_page[block.page_no] = max(max_block_no_by_page.get(block.page_no, -1), block.block_no)

    for result in ocr_results:
        next_block_no = max_block_no_by_page.get(result.page_no, -1) + 1
        for index, ocr_block in enumerate(result.blocks):
            merged.append(
                PdfTextBlock(
                    block_id=make_stable_id("ocrblock", result.page_no, index, ocr_block.text[:80]),
                    page_no=result.page_no,
                    text=ocr_block.text,
                    bbox=ocr_block.bbox or [],
                    block_no=next_block_no + index,
                    source_type="ocr",
                    confidence=ocr_block.confidence,
                )
            )

    merged.sort(key=lambda item: (item.page_no, item.block_no))
    if out_path:
        write_json(out_path, merged)
    return merged
