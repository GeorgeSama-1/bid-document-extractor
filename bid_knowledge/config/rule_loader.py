from __future__ import annotations

from pathlib import Path
from typing import Any

from bid_knowledge.schemas.models import SectionRule
from bid_knowledge.utils.id_utils import make_stable_id
from bid_knowledge.utils.io_utils import write_json
from bid_knowledge.utils.text_utils import clean_text


COLUMN_ALIASES: dict[str, list[str]] = {
    "file_type": ["文件类型", "文件类别", "文件名称"],
    "module_name": ["组成模块", "模块", "章节", "一级目录"],
    "sub_content_1": ["子内容1", "子内容", "子内容一", "二级目录"],
    "sub_content_2": ["子内容2", "子内容二", "三级目录"],
    "sub_content_3": ["子内容3", "子内容三", "四级目录"],
    "sub_content_4": ["子内容4", "子内容四", "五级目录"],
    "has_standard_template": ["是否提供标准格式", "提供标准格式", "是否有标准格式"],
    "from_history_bid": ["是否从往期投标文件中摘取", "是否从往期投标文件摘取", "从往期投标文件中摘取"],
    "ai_generated": ["是否AI自主编制", "是否 AI 自主编制", "AI自主编制"],
    "user_upload_required": ["是否用户事先上传", "是否用户上传", "用户事先上传"],
    "reference_technical_spec": ["参考技术规范书模板", "是否参考技术规范书模板"],
}

REQUIRED_CANONICAL_COLUMNS = {"module_name"}
REPORTABLE_OPTIONAL_COLUMNS = {
    "sub_content_1",
    "sub_content_2",
    "sub_content_3",
    "has_standard_template",
    "from_history_bid",
    "ai_generated",
    "user_upload_required",
    "reference_technical_spec",
}


def normalize_header_name(name: str) -> str:
    return (
        clean_text(name)
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
        .replace("（", "(")
        .replace("）", ")")
        .lower()
    )


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = clean_text(str(value)).lower()
    if text in {"", "nan", "none", "-", "否", "n", "no", "false", "0"}:
        return False
    if text in {"是", "y", "yes", "true", "1"}:
        return True
    return False


def build_section_path(*parts: Any) -> str:
    cleaned = []
    for part in parts:
        value = clean_text(str(part)) if part is not None else ""
        if value and value != "-":
            cleaned.append(value)
    return " / ".join(cleaned)


def _map_row_to_canonical(raw_row: dict[str, Any], fallback_file_type: str = "") -> tuple[dict[str, Any], list[str], list[str]]:
    canonical: dict[str, Any] = {"file_type": fallback_file_type}
    recognized: list[str] = []
    normalized_map = {normalize_header_name(key): key for key in raw_row}
    for canonical_name, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            normalized_alias = normalize_header_name(alias)
            if normalized_alias in normalized_map:
                original_key = normalized_map[normalized_alias]
                canonical[canonical_name] = raw_row.get(original_key)
                recognized.append(canonical_name)
                break
    if not canonical.get("file_type"):
        canonical["file_type"] = raw_row.get("文件类型") or fallback_file_type
    expected = REQUIRED_CANONICAL_COLUMNS | REPORTABLE_OPTIONAL_COLUMNS
    missing = sorted(expected - set(recognized))
    return canonical, sorted(set(recognized)), missing


def rows_to_rules(rows: list[dict[str, Any]], file_type: str = "") -> tuple[list[SectionRule], dict[str, Any]]:
    rules: list[SectionRule] = []
    recognized_columns: set[str] = set()
    missing_columns: set[str] = set()
    skipped_rows: list[dict[str, Any]] = []
    hierarchy_state = {
        "module_name": "",
        "sub_content_1": "",
        "sub_content_2": "",
        "sub_content_3": "",
        "sub_content_4": "",
    }

    for index, raw_row in enumerate(rows, start=1):
        canonical, recognized, missing = _map_row_to_canonical(raw_row, fallback_file_type=file_type)
        recognized_columns.update(recognized)
        missing_columns.update(missing)

        file_type_value = clean_text(canonical.get("file_type", file_type))
        module_name = clean_text(canonical.get("module_name"))
        sub_content_1 = clean_text(canonical.get("sub_content_1"))
        sub_content_2 = clean_text(canonical.get("sub_content_2"))
        sub_content_3 = clean_text(canonical.get("sub_content_3"))
        sub_content_4 = clean_text(canonical.get("sub_content_4"))

        if module_name:
            hierarchy_state["module_name"] = module_name
            hierarchy_state["sub_content_1"] = ""
            hierarchy_state["sub_content_2"] = ""
            hierarchy_state["sub_content_3"] = ""
            hierarchy_state["sub_content_4"] = ""
        if sub_content_1:
            hierarchy_state["sub_content_1"] = sub_content_1
            hierarchy_state["sub_content_2"] = ""
            hierarchy_state["sub_content_3"] = ""
            hierarchy_state["sub_content_4"] = ""
        if sub_content_2:
            hierarchy_state["sub_content_2"] = sub_content_2
            hierarchy_state["sub_content_3"] = ""
            hierarchy_state["sub_content_4"] = ""
        if sub_content_3:
            hierarchy_state["sub_content_3"] = sub_content_3
            hierarchy_state["sub_content_4"] = ""
        if sub_content_4:
            hierarchy_state["sub_content_4"] = sub_content_4

        section_path = build_section_path(
            file_type_value,
            hierarchy_state["module_name"],
            hierarchy_state["sub_content_1"],
            hierarchy_state["sub_content_2"],
            hierarchy_state["sub_content_3"],
        )
        if not section_path or section_path == file_type_value:
            skipped_rows.append({"row_index": index, "reason": "empty_section_path", "raw_row": raw_row})
            continue

        rule = SectionRule(
            rule_id=make_stable_id("rule", section_path, index),
            file_type=file_type_value,
            module_name=hierarchy_state["module_name"],
            sub_content_1=hierarchy_state["sub_content_1"],
            sub_content_2=hierarchy_state["sub_content_2"],
            sub_content_3=hierarchy_state["sub_content_3"],
            section_path=section_path,
            has_standard_template=normalize_bool(canonical.get("has_standard_template")),
            from_history_bid=normalize_bool(canonical.get("from_history_bid")),
            ai_generated=normalize_bool(canonical.get("ai_generated")),
            user_upload_required=normalize_bool(canonical.get("user_upload_required")),
            reference_technical_spec=normalize_bool(canonical.get("reference_technical_spec")),
            raw_row={key: value for key, value in raw_row.items()},
        )
        rules.append(rule)

    report = {
        "recognized_columns": sorted(recognized_columns),
        "missing_columns": sorted(missing_columns),
        "skipped_rows": skipped_rows,
        "loaded_rule_count": len(rules),
    }
    return rules, report


def _pick_header_row(rows: list[list[Any]]) -> int | None:
    best_index = None
    best_score = 0
    normalized_aliases = {
        canonical: {normalize_header_name(alias) for alias in aliases}
        for canonical, aliases in COLUMN_ALIASES.items()
    }
    for index, row in enumerate(rows[:8]):
        score = 0
        normalized_row = [normalize_header_name(str(value)) for value in row if clean_text(str(value))]
        row_set = set(normalized_row)
        for aliases in normalized_aliases.values():
            if row_set & aliases:
                score += 1
        if score > best_score:
            best_score = score
            best_index = index
    return best_index if best_score >= 2 else None


def load_rules_from_excel(
    rules_xlsx: str | Path,
    out_path: str | Path | None = None,
    report_path: str | Path | None = None,
) -> tuple[list[SectionRule], dict[str, Any]]:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - dependency-driven
        raise RuntimeError("需要先安装 pandas/openpyxl 才能读取 Excel 规则表。") from exc

    xlsx_path = Path(rules_xlsx)
    if not xlsx_path.exists():
        raise FileNotFoundError(f"规则表不存在: {xlsx_path}")

    workbook = pd.ExcelFile(xlsx_path)
    all_rules: list[SectionRule] = []
    sheet_reports: list[dict[str, Any]] = []

    for sheet_name in workbook.sheet_names:
        raw_df = workbook.parse(sheet_name=sheet_name, header=None)
        rows = raw_df.fillna("").values.tolist()
        header_row = _pick_header_row(rows)
        if header_row is None:
            sheet_reports.append(
                {
                    "sheet_name": sheet_name,
                    "skipped": True,
                    "reason": "header_not_found",
                }
            )
            continue

        headers = [clean_text(value) for value in rows[header_row]]
        data_rows = []
        for row in rows[header_row + 1 :]:
            mapped = {headers[idx]: row[idx] for idx in range(min(len(headers), len(row))) if clean_text(headers[idx])}
            if any(clean_text(str(value)) for value in mapped.values()):
                data_rows.append(mapped)

        file_type = clean_text(rows[0][0]) or clean_text(sheet_name)
        if clean_text(file_type) in {"序号", ""}:
            file_type = clean_text(sheet_name)

        rules, report = rows_to_rules(data_rows, file_type=file_type)
        report["sheet_name"] = sheet_name
        report["header_row"] = header_row
        sheet_reports.append(report)
        all_rules.extend(rules)

    final_report = {
        "source_file": str(xlsx_path),
        "sheet_reports": sheet_reports,
        "total_rules": len(all_rules),
    }
    if out_path:
        write_json(out_path, all_rules)
    if report_path:
        write_json(report_path, final_report)
    return all_rules, final_report


def load_rules_from_json(path: str | Path) -> list[SectionRule]:
    import json

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [SectionRule(**item) for item in data]
