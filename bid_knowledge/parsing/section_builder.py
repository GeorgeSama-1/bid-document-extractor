from __future__ import annotations

import statistics
from pathlib import Path

from bid_knowledge.matching.normalizer import normalize_section_title
from bid_knowledge.schemas.models import PdfTextBlock, ReconstructedSection, SectionRule
from bid_knowledge.utils.id_utils import make_stable_id
from bid_knowledge.utils.io_utils import write_json


def _group_blocks_by_page(blocks: list[PdfTextBlock]) -> dict[int, list[PdfTextBlock]]:
    grouped: dict[int, list[PdfTextBlock]] = {}
    for block in sorted(blocks, key=lambda item: (item.page_no, item.block_no)):
        grouped.setdefault(block.page_no, []).append(block)
    return grouped


def _level_from_title(title: str) -> int:
    if title.startswith("（") or title.startswith("("):
        return 2
    if "." in title[:6]:
        return min(title.count(".") + 1, 4)
    return 1


def _sections_from_toc(toc: list[dict], blocks: list[PdfTextBlock]) -> list[ReconstructedSection]:
    grouped = _group_blocks_by_page(blocks)
    max_page = max(grouped) if grouped else 1
    sections: list[ReconstructedSection] = []
    for index, item in enumerate(toc):
        page_start = int(item["page"])
        next_page = int(toc[index + 1]["page"]) if index + 1 < len(toc) else max_page + 1
        page_end = max(page_start, next_page - 1)
        start_blocks = grouped.get(page_start, [])
        end_blocks = grouped.get(page_end, [])
        title = str(item["title"]).strip()
        sections.append(
            ReconstructedSection(
                section_id=make_stable_id("section", "toc", index, title),
                title=title,
                normalized_title=normalize_section_title(title),
                level=int(item["level"]),
                page_start=page_start,
                page_end=page_end,
                block_start_id=start_blocks[0].block_id if start_blocks else None,
                block_end_id=end_blocks[-1].block_id if end_blocks else None,
                source_type="toc",
            )
        )
    return sections


def _looks_like_heading(block: PdfTextBlock, font_threshold: float, rule_keywords: list[str]) -> bool:
    text = block.text.strip()
    if not text or len(text) > 50:
        return False
    if block.font_size and block.font_size >= font_threshold:
        return True
    if any(text.startswith(prefix) for prefix in ("一、", "二、", "三、", "四、", "五、", "（一）", "(一)", "1.", "1.1")):
        return True
    normalized = normalize_section_title(text)
    return any(keyword and (keyword in normalized or normalized in keyword) for keyword in rule_keywords)


def _sections_from_blocks(blocks: list[PdfTextBlock], rules: list[SectionRule]) -> list[ReconstructedSection]:
    sorted_blocks = sorted(blocks, key=lambda item: (item.page_no, item.block_no))
    font_sizes = [block.font_size for block in sorted_blocks if block.font_size]
    font_threshold = (statistics.median(font_sizes) + 1.0) if font_sizes else 12.0
    rule_keywords = [normalize_section_title(rule.section_path.split(" / ")[-1]) for rule in rules]
    heading_blocks = [block for block in sorted_blocks if _looks_like_heading(block, font_threshold, rule_keywords)]

    if not heading_blocks and sorted_blocks:
        first = sorted_blocks[0]
        return [
            ReconstructedSection(
                section_id=make_stable_id("section", "fallback", first.page_no),
                title="全文",
                normalized_title="全文",
                level=1,
                page_start=first.page_no,
                page_end=sorted_blocks[-1].page_no,
                block_start_id=first.block_id,
                block_end_id=sorted_blocks[-1].block_id,
                source_type="fallback",
            )
        ]

    sections: list[ReconstructedSection] = []
    for index, block in enumerate(heading_blocks):
        next_block = heading_blocks[index + 1] if index + 1 < len(heading_blocks) else None
        page_end = next_block.page_no if next_block else sorted_blocks[-1].page_no
        if next_block and next_block.page_no == block.page_no:
            page_end = block.page_no
        elif next_block:
            page_end = max(block.page_no, next_block.page_no - 1)
        sections.append(
            ReconstructedSection(
                section_id=make_stable_id("section", "block", block.page_no, block.block_no, block.text[:40]),
                title=block.text.strip(),
                normalized_title=normalize_section_title(block.text),
                level=_level_from_title(block.text.strip()),
                page_start=block.page_no,
                page_end=page_end,
                block_start_id=block.block_id,
                block_end_id=next_block.block_id if next_block else sorted_blocks[-1].block_id,
                source_type="heuristic",
            )
        )
    return sections


def build_sections(
    blocks: list[PdfTextBlock],
    toc: list[dict] | None = None,
    rules: list[SectionRule] | None = None,
    out_path: str | Path | None = None,
) -> list[ReconstructedSection]:
    rules = rules or []
    sections = _sections_from_toc(toc or [], blocks) if toc else _sections_from_blocks(blocks, rules)
    if out_path:
        write_json(out_path, sections)
    return sections
