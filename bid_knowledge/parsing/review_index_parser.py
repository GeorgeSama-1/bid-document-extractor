from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from bid_knowledge.schemas.models import ParsedTable, PdfTextBlock

SCORE_MATERIAL_ROOT = "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料"


def _has_index_title(blocks: list[PdfTextBlock]) -> bool:
    return any("商务评审索引表" in (block.text or "") for block in blocks)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _clean_label(text: str) -> str:
    normalized = re.sub(r"\s+", "", (text or "").replace("\n", ""))
    normalized = re.sub(r"[（(]\s*[-\d]+(?:\s*[-~—至]\s*[-\d]+)*\s*[)）]?$", "", normalized)
    if re.fullmatch(r"[（(]?\s*[-\d]+(?:\s*[-~—至]\s*[-\d]+)*\s*[)）]?", normalized):
        return ""
    return normalized.strip("：:；;，,")


def _line_matches(text: str) -> list[tuple[int, str]]:
    matches: list[tuple[int, str]] = []
    normalized = (text or "").replace("\n", "")
    for page_no, title in re.findall(r"(?:详见)?第(\d+)页[:：]([^第]+?)(?=(?:详见)?第\d+页[:：]|$)", normalized):
        matches.append((int(page_no), title.strip(" ：:；;，,")))
    return matches


def _infer_element_from_row_text(text: str) -> str:
    normalized = _normalize_text(text)
    if "影响评标工作公正性行为" in normalized:
        return "影响评标工作公正性行为的凭证"
    if "报价文件完整性" in normalized:
        return "报价质量"
    return ""


def _section_parts(section_path: str) -> list[str]:
    return [part.strip() for part in str(section_path or "").split(" / ") if part.strip()]


def _normalize_key(text: str) -> str:
    normalized = re.sub(r"\s+", "", text or "")
    normalized = re.sub(r"^[\d.]+[、.．]\s*", "", normalized)
    normalized = normalized.replace("（无）", "").replace("(无)", "")
    normalized = re.sub(r"[“”\"'‘’、，,。:：；;（）()\\/－—_-]", "", normalized)
    return normalized.lower()


def _title_label(title: str) -> str:
    text = re.sub(r"\s+", "", title or "")
    text = re.sub(r"^[\d.]+[、.．]\s*", "", text)
    return text.strip(" ：:；;，,")


def _label_variants(label: str) -> list[str]:
    clean = _title_label(label)
    variants = [clean]
    for delimiter in ["-", "－", "—"]:
        if delimiter in clean:
            variants.append(clean.split(delimiter)[-1])
    return [item for item in variants if item]


def _match_score_section_path(label: str, score_paths: list[str]) -> str | None:
    candidates = [_normalize_key(item) for item in _label_variants(label)]
    if not candidates:
        return None
    for path in score_paths:
        tail = _section_parts(path)[-1] if _section_parts(path) else ""
        tail_key = _normalize_key(tail)
        if any(candidate == tail_key for candidate in candidates):
            return path
    for path in score_paths:
        tail = _section_parts(path)[-1] if _section_parts(path) else ""
        tail_key = _normalize_key(tail)
        if any(candidate and (candidate in tail_key or tail_key in candidate) for candidate in candidates):
            return path
    return None


def parse_business_review_index(blocks: list[PdfTextBlock], tables: list[ParsedTable]) -> list[dict[str, Any]]:
    if not _has_index_title(blocks):
        return []

    index_tables = [table for table in tables if 2 <= table.page_no <= 7]
    current_project = ""
    current_element = ""
    entries: list[dict[str, Any]] = []
    for table in sorted(index_tables, key=lambda item: item.page_no):
        for row in table.rows:
            if not row:
                continue
            col_0 = row[0] if len(row) > 0 else ""
            col_1 = row[1] if len(row) > 1 else ""
            row_text = "".join(str(cell or "") for cell in row)
            if "项目" in col_0 and "评审要素" in col_1:
                continue
            if _clean_label(col_0):
                current_project = _clean_label(col_0)
            if _clean_label(col_1):
                current_element = _clean_label(col_1)
            elif _infer_element_from_row_text(row_text):
                current_element = _infer_element_from_row_text(row_text)
            for cell in row[2:]:
                for page_no, title in _line_matches(cell):
                    if not current_project or not current_element:
                        continue
                    entries.append(
                        {
                            "section_path": f"商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / {current_project} / {current_element}",
                            "project_label": current_project,
                            "element_label": current_element,
                            "title": title,
                            "page_start": page_no,
                        }
                    )
    return entries


def align_business_review_index_entries(
    entries: list[dict[str, Any]],
    section_paths: list[str] | set[str],
) -> list[dict[str, Any]]:
    score_paths = [path for path in section_paths if str(path).startswith(f"{SCORE_MATERIAL_ROOT} / ")]
    aligned: list[dict[str, Any]] = []
    current_path: str | None = None
    for entry in entries:
        section_path = str(entry.get("section_path") or "")
        title = str(entry.get("title") or "")
        element_label = str(entry.get("element_label") or "")
        title_number = _title_number_key(title)
        title_path = _match_score_section_path(_title_label(title), score_paths)

        if title_path and title_number and len(title_number) <= 3:
            resolved_path = title_path
        elif current_path and title_number and len(title_number) > 3:
            resolved_path = current_path
        else:
            resolved_path = _match_score_section_path(element_label, score_paths)
            if title_path:
                resolved_path = title_path

        if resolved_path:
            current_path = resolved_path
        elif section_path in section_paths:
            resolved_path = section_path
        else:
            resolved_path = section_path

        project_label = entry.get("project_label")
        element = entry.get("element_label")
        parts = _section_parts(resolved_path)
        if resolved_path.startswith(f"{SCORE_MATERIAL_ROOT} / ") and len(parts) >= 5:
            project_label = parts[-2]
            element = parts[-1]

        aligned.append(
            {
                **entry,
                "section_path": resolved_path,
                "project_label": project_label,
                "element_label": element,
                "original_section_path": section_path if resolved_path != section_path else entry.get("original_section_path"),
            }
        )
    return aligned


def build_business_review_index_tree(entries: list[dict[str, Any]]) -> dict[str, Any]:
    tree: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for entry in entries:
        project = str(entry.get("project_label") or "未识别大项")
        element = str(entry.get("element_label") or "未识别评审要素")
        tree.setdefault(project, {}).setdefault(element, []).append(
            {
                "title": entry.get("title"),
                "page_start": entry.get("page_start"),
                "section_path": entry.get("section_path"),
                "original_section_path": entry.get("original_section_path"),
            }
        )
    return {
        "source": "商务评审索引表",
        "scope": SCORE_MATERIAL_ROOT,
        "entry_count": len(entries),
        "project_count": len(tree),
        "tree": tree,
    }


def _title_number_key(title: str) -> tuple[int, ...]:
    match = re.match(r"^(\d+(?:\.\d+)+)、", title or "")
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def _find_title_block(entry: dict[str, Any], blocks: list[PdfTextBlock]) -> PdfTextBlock | None:
    title_key = _normalize_key(str(entry.get("title") or entry.get("folder_title") or ""))
    if not title_key:
        return None
    page_no = int(entry.get("page_start") or 0)
    candidates = []
    for block in blocks:
        if block.page_no != page_no:
            continue
        block_key = _normalize_key(block.text)
        if title_key and (title_key in block_key or block_key in title_key):
            candidates.append(block)
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item.bbox[1] if item.bbox else 0.0, len(item.text)))[0]


def build_folder_ranges(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    for entry in entries:
        title = entry.get("title") or ""
        if not re.match(r"^\d+(?:\.\d+)+、", title):
            continue
        ordered.append(entry)
    ordered.sort(key=lambda item: (item["page_start"], len(_title_number_key(item["title"])), _title_number_key(item["title"])))

    ranges: list[dict[str, Any]] = []
    for index, item in enumerate(ordered):
        next_page = ordered[index + 1]["page_start"] if index + 1 < len(ordered) else None
        ranges.append(
            {
                "section_path": item["section_path"],
                "folder_title": item["title"],
                "page_start": item["page_start"],
                "page_end": max(item["page_start"], (next_page - 1) if next_page else item["page_start"]),
            }
        )
    return ranges


def build_precise_folder_ranges(entries: list[dict[str, Any]], blocks: list[PdfTextBlock]) -> list[dict[str, Any]]:
    ranges = build_folder_ranges(entries)
    enriched: list[dict[str, Any]] = []
    for item in ranges:
        block = _find_title_block(item, blocks)
        start_y = float(block.bbox[1]) if block and block.bbox else None
        enriched.append(
            {
                **item,
                "start_y": start_y,
                "start_block_id": block.block_id if block else None,
            }
        )

    for index, item in enumerate(enriched):
        next_item = enriched[index + 1] if index + 1 < len(enriched) else None
        if next_item and item.get("start_y") is not None and next_item.get("start_y") is not None:
            item["page_end"] = next_item["page_start"]
            item["end_y"] = next_item["start_y"]
            item["end_block_id"] = next_item.get("start_block_id")
        else:
            item["end_y"] = None
            item["end_block_id"] = None
    return enriched
