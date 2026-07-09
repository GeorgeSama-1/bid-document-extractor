from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
import re
from typing import Any

from bid_knowledge.matching.normalizer import normalize_section_title
from bid_knowledge.schemas.models import PdfTextBlock, ProcessingPlan, ReconstructedSection, SectionMatchResult, SectionRule
from bid_knowledge.utils.io_utils import write_json


STABLE_CONTAINER_TITLES = {"法定代表人授权委托书"}


def _extract_rule_titles(rule: SectionRule) -> list[str]:
    titles: list[str] = []
    for candidate in (rule.sub_content_3, rule.sub_content_2, rule.sub_content_1):
        if candidate and candidate not in titles:
            titles.append(candidate)
    if not titles:
        for candidate in (rule.module_name, rule.file_type):
            if candidate and candidate not in titles:
                titles.append(candidate)
    if not titles:
        titles.append(rule.section_path.split(" / ")[-1])
    return titles


def _fuzzy_ratio(left: str, right: str) -> float:
    try:
        from rapidfuzz import fuzz

        return float(fuzz.ratio(left, right)) / 100.0
    except ImportError:
        return SequenceMatcher(None, left, right).ratio()


def _score_match(rule_title: str, section: ReconstructedSection) -> tuple[float, str]:
    normalized_rule = normalize_section_title(rule_title)
    normalized_title = section.normalized_title or normalize_section_title(section.title)
    if normalized_rule == normalized_title:
        return 1.0, "exact_normalized"
    if normalized_rule and (normalized_rule in normalized_title or normalized_title in normalized_rule):
        return 0.92, "contains_match"
    fuzzy = _fuzzy_ratio(normalized_rule, normalized_title)
    if fuzzy >= 0.75:
        return fuzzy, "fuzzy_match"
    rule_tokens = {token for token in normalized_rule if token.strip()}
    title_tokens = {token for token in normalized_title if token.strip()}
    overlap = len(rule_tokens & title_tokens)
    if rule_tokens and overlap >= max(2, len(rule_tokens) // 2):
        return min(0.8, overlap / max(len(rule_tokens), 1)), "keyword_overlap"
    return 0.0, "no_match"


def _extract_search_keywords(rule_title: str) -> list[str]:
    base = re.sub(r"[（()）/、，,：:\-\s]+", " ", rule_title)
    fragments = [fragment.strip() for fragment in re.split(r"[或及与和]", base) if fragment.strip()]
    generic_terms = ("扫描件", "附件", "正反面", "有效", "证明文件", "证明材料", "图片", "照片", "单位负责人", "等")
    keywords: list[str] = []
    for fragment in fragments:
        normalized = fragment
        for term in generic_terms:
            normalized = normalized.replace(term, "")
        normalized = normalized.strip()
        if len(normalized) >= 2 and normalized not in keywords:
            keywords.append(normalized)
    title = rule_title
    for hard_keyword in ("被授权人", "法定代表人", "身份证", "营业执照", "税务登记", "银行保函", "基本账户证明"):
        if hard_keyword in title and hard_keyword not in keywords:
            keywords.append(hard_keyword)
    return [normalize_section_title(keyword) for keyword in keywords if keyword]


def _extract_anchor_keywords(rule_title: str) -> list[str]:
    anchors = []
    for keyword in ("身份证", "营业执照", "税务登记", "保函", "基本账户证明", "合同", "发票", "证书", "证明文件", "证明材料"):
        if keyword in rule_title:
            anchors.append(normalize_section_title(keyword))
    return anchors


def _is_attachment_like_title(rule_title: str) -> bool:
    return any(keyword in rule_title for keyword in ("扫描件", "附件", "身份证", "营业执照", "保函", "证明"))


def _score_block_line(rule_title: str, line: str) -> tuple[float, str]:
    normalized_rule = normalize_section_title(rule_title)
    normalized_line = normalize_section_title(line)
    if not normalized_rule or not normalized_line:
        return 0.0, "no_match"
    keywords = _extract_search_keywords(rule_title)
    anchors = _extract_anchor_keywords(rule_title)
    if normalized_rule == normalized_line:
        return 1.0, "block_line_exact"
    if _is_attachment_like_title(rule_title) and ("附" not in line and "扫描件" not in line):
        return 0.0, "no_match"
    if keywords:
        keyword_hits = [keyword for keyword in keywords if keyword and keyword in normalized_line]
        strong_hit = any(len(keyword) >= 4 for keyword in keyword_hits)
        if not keyword_hits:
            return 0.0, "no_match"
        if len(keywords) >= 2 and not strong_hit and len(keyword_hits) < 2:
            return 0.0, "no_match"
    if anchors and not any(anchor in normalized_line for anchor in anchors):
        return 0.0, "no_match"
    if normalized_rule in normalized_line or normalized_line in normalized_rule:
        return 0.94, "block_line_contains"
    rule_bigrams = {normalized_rule[idx : idx + 2] for idx in range(len(normalized_rule) - 1)}
    line_bigrams = {normalized_line[idx : idx + 2] for idx in range(len(normalized_line) - 1)}
    if rule_bigrams:
        overlap_ratio = len(rule_bigrams & line_bigrams) / len(rule_bigrams)
        if overlap_ratio >= 0.45:
            return min(0.9, 0.7 + overlap_ratio * 0.2), "block_bigram_overlap"
    return 0.0, "no_match"


def _find_block_match(rule_title: str, blocks: list[PdfTextBlock]) -> tuple[float, str, PdfTextBlock | None, str | None]:
    best_score = 0.0
    best_reason = "no_match"
    best_block: PdfTextBlock | None = None
    best_line: str | None = None
    for block in blocks:
        for line in (part.strip() for part in block.text.splitlines() if part.strip()):
            score, reason = _score_block_line(rule_title, line)
            if score > best_score:
                best_score = score
                best_reason = reason
                best_block = block
                best_line = line
    return best_score, best_reason, best_block, best_line


def _directory_like_pages(blocks: list[PdfTextBlock]) -> set[int]:
    pages: set[int] = set()
    by_page: dict[int, list[PdfTextBlock]] = {}
    for block in blocks:
        by_page.setdefault(block.page_no, []).append(block)
    for page_no, page_blocks in by_page.items():
        if page_no > 20:
            continue
        normalized_lines = [re.sub(r"\s+", "", block.text or "") for block in page_blocks]
        has_directory_title = any(line in {"目录", "目次", "目录页"} for line in normalized_lines)
        has_directory_rows = sum(1 for line in normalized_lines if re.search(r"\.{2,}|…{2,}|第?\d+页?$", line))
        if has_directory_title or has_directory_rows >= 3:
            pages.add(page_no)
    return pages


def _section_in_scope(section: ReconstructedSection, scopes: list[ReconstructedSection]) -> bool:
    return any(scope.page_start <= section.page_start <= scope.page_end and scope.page_start <= section.page_end <= scope.page_end for scope in scopes)


def _block_in_scope(block: PdfTextBlock, scopes: list[ReconstructedSection]) -> bool:
    return any(scope.page_start <= block.page_no <= scope.page_end for scope in scopes)


def _stable_container_scope_sections(rule: SectionRule, sections: list[ReconstructedSection]) -> list[ReconstructedSection]:
    parts = [part.strip() for part in str(rule.section_path or "").split(" / ") if part.strip()]
    parent_title = (rule.module_name or "").strip()
    if parent_title not in STABLE_CONTAINER_TITLES:
        return []
    if len(parts) < 3 or parent_title not in parts[:-1]:
        return []
    child_title = parts[-1]
    if normalize_section_title(child_title) == normalize_section_title(parent_title):
        return []
    scopes: list[ReconstructedSection] = []
    for section in sections:
        score, _reason = _score_match(parent_title, section)
        if score >= 0.8:
            scopes.append(section)
    return sorted(scopes, key=lambda item: (item.page_start, item.page_end))


def _collect_block_matches(
    rule_title: str,
    blocks: list[PdfTextBlock],
    sections: list[ReconstructedSection],
    scope_sections: list[ReconstructedSection] | None = None,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    directory_pages = _directory_like_pages(blocks)
    scopes = scope_sections or []
    for block in blocks:
        if block.page_no in directory_pages:
            continue
        if scopes and not _block_in_scope(block, scopes):
            continue
        for line in (part.strip() for part in block.text.splitlines() if part.strip()):
            score, reason = _score_block_line(rule_title, line)
            if score < 0.6:
                continue
            direct_section = _find_container_section(block, sections)
            container = _resolve_container_for_section(direct_section, sections)
            matches.append(
                {
                    "matched_source_type": "text_block",
                    "matched_section_id": f"block::{block.block_id}",
                    "matched_title": line,
                    "matched_page_no": block.page_no,
                    "matched_page_end": block.page_no,
                    "matched_container_section_id": container.section_id if container else None,
                    "matched_container_title": container.title if container else None,
                    "matched_container_page_no": container.page_start if container else None,
                    "matched_container_page_end": container.page_end if container else None,
                    "matched_block_start_id": block.block_id,
                    "matched_block_end_id": block.block_id,
                    "confidence": round(score, 4),
                    "match_reason": reason,
                }
            )
    return matches


def _deduplicate_matches(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated: list[dict[str, Any]] = []
    seen = set()
    for item in sorted(
        candidates,
        key=lambda row: (
            row.get("matched_page_no") or 0,
            row.get("matched_page_end") or 0,
            row.get("matched_title") or "",
            -(row.get("confidence") or 0.0),
        ),
    ):
        key = (
            item.get("matched_source_type"),
            item.get("matched_title"),
            item.get("matched_page_no"),
            item.get("matched_page_end"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(item)
    return deduplicated


def _prefer_body_matches(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidates:
        return []
    max_score = max(float(item.get("confidence") or 0.0) for item in candidates)
    has_strong_late_match = any(
        (item.get("matched_page_no") or 0) >= 50 and float(item.get("confidence") or 0.0) >= max_score - 0.05
        for item in candidates
    )
    if not has_strong_late_match:
        return candidates
    filtered = [item for item in candidates if (item.get("matched_page_no") or 0) > 20]
    return filtered or candidates


def _choose_primary_match(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    preferred = _prefer_body_matches(candidates)
    return sorted(
        preferred,
        key=lambda item: (
            float(item.get("confidence") or 0.0),
            1 if item.get("matched_source_type") == "section" else 0,
            -(int(item.get("rule_title_rank") or 0)),
            int(item.get("matched_page_no") or 0),
            int(item.get("matched_page_end") or 0),
        ),
        reverse=True,
    )[0]


def _related_matches(primary: dict[str, Any] | None, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not primary:
        return []
    related: list[dict[str, Any]] = []
    for item in _deduplicate_matches(_prefer_body_matches(candidates)):
        if item == primary:
            continue
        if float(item.get("confidence") or 0.0) < 0.75:
            continue
        related.append(item)
    return related


def _resolve_primary_container(
    primary: dict[str, Any] | None,
    related: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not primary:
        return None
    candidates = [primary, *related]
    preferred = [
        item
        for item in candidates
        if item.get("matched_container_title")
        and normalize_section_title(str(item.get("matched_container_title") or ""))
        != normalize_section_title(str(item.get("matched_title") or ""))
    ]
    if not preferred:
        return primary
    best = sorted(
        preferred,
        key=lambda item: (
            -(int(item.get("matched_container_page_end") or item.get("matched_container_page_no") or 0) - int(item.get("matched_container_page_no") or 0)),
            int(item.get("matched_container_page_no") or 0),
            float(item.get("confidence") or 0.0),
        ),
        reverse=True,
    )[0]
    primary["matched_container_section_id"] = best.get("matched_container_section_id")
    primary["matched_container_title"] = best.get("matched_container_title")
    primary["matched_container_page_no"] = best.get("matched_container_page_no")
    primary["matched_container_page_end"] = best.get("matched_container_page_end")
    return primary


def _find_container_section(block: PdfTextBlock, sections: list[ReconstructedSection]) -> ReconstructedSection | None:
    containing = [
        section
        for section in sections
        if section.page_start <= block.page_no <= section.page_end
    ]
    if not containing:
        return None
    return sorted(containing, key=lambda item: (item.page_start, item.level), reverse=True)[0]


def _find_parent_section(section: ReconstructedSection | None, sections: list[ReconstructedSection]) -> ReconstructedSection | None:
    if section is None:
        return None
    candidates = [
        item
        for item in sections
        if item.section_id != section.section_id
        and item.level < section.level
        and item.page_start <= section.page_start <= item.page_end
        and item.page_start <= section.page_end <= item.page_end
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item.level, item.page_start), reverse=True)[0]


def _resolve_container_for_section(section: ReconstructedSection | None, sections: list[ReconstructedSection]) -> ReconstructedSection | None:
    if section is None:
        return None
    return _find_parent_section(section, sections) or section


def match_sections(
    rules: list[SectionRule],
    sections: list[ReconstructedSection],
    plan: ProcessingPlan | None = None,
    blocks: list[PdfTextBlock] | None = None,
    out_path: str | Path | None = None,
) -> list[SectionMatchResult]:
    plan_map = {item.rule_id: item for item in (plan.sections if plan else [])}
    blocks = blocks or []
    results: list[SectionMatchResult] = []
    for rule in rules:
        rule_titles = _extract_rule_titles(rule)
        primary_title = rule_titles[0]
        scope_sections = _stable_container_scope_sections(rule, sections)
        match_candidates: list[dict[str, Any]] = []
        for title_rank, rule_title in enumerate(rule_titles):
            for section in sections:
                if scope_sections and not _section_in_scope(section, scope_sections):
                    continue
                score, reason = _score_match(rule_title, section)
                if score < 0.6:
                    continue
                container = _resolve_container_for_section(section, sections)
                match_candidates.append(
                    {
                        "matched_source_type": "section",
                        "matched_section_id": section.section_id,
                        "matched_title": section.title,
                        "matched_page_no": section.page_start,
                        "matched_page_end": section.page_end,
                        "matched_container_section_id": container.section_id if container else None,
                        "matched_container_title": container.title if container else None,
                        "matched_container_page_no": container.page_start if container else None,
                        "matched_container_page_end": container.page_end if container else None,
                        "matched_block_start_id": section.block_start_id,
                        "matched_block_end_id": section.block_end_id,
                        "confidence": round(score, 4),
                        "match_reason": reason,
                        "rule_title_rank": title_rank,
                    }
                )

        if blocks:
            for item in _collect_block_matches(primary_title, blocks, sections, scope_sections=scope_sections):
                item["rule_title_rank"] = 0
                match_candidates.append(item)

        best_match = _choose_primary_match(match_candidates)
        related_matches = _related_matches(best_match, match_candidates)
        best_match = _resolve_primary_container(best_match, related_matches)

        plan_item = plan_map.get(rule.rule_id)
        matched = best_match is not None
        results.append(
            SectionMatchResult(
                rule_id=rule.rule_id,
                rule_section_path=rule.section_path,
                matched=matched,
                matched_source_type=best_match.get("matched_source_type", "unmatched") if matched else "unmatched",
                matched_section_id=best_match.get("matched_section_id") if matched else None,
                matched_title=best_match.get("matched_title") if matched else None,
                matched_page_no=best_match.get("matched_page_no") if matched else None,
                matched_page_end=best_match.get("matched_page_end") if matched else None,
                matched_container_section_id=best_match.get("matched_container_section_id") if matched else None,
                matched_container_title=best_match.get("matched_container_title") if matched else None,
                matched_container_page_no=best_match.get("matched_container_page_no") if matched else None,
                matched_container_page_end=best_match.get("matched_container_page_end") if matched else None,
                matched_block_start_id=best_match.get("matched_block_start_id") if matched else None,
                matched_block_end_id=best_match.get("matched_block_end_id") if matched else None,
                confidence=float(best_match.get("confidence") or 0.0) if matched else 0.0,
                match_reason=str(best_match.get("match_reason") or "no_confident_match") if matched else "no_confident_match",
                related_matches=related_matches,
                process_strategy=plan_item.process_strategy if plan_item else [],
                content_type=plan_item.content_type if plan_item else "",
                reuse_method=plan_item.reuse_method if plan_item else "",
            )
        )
    if out_path:
        write_json(out_path, results)
    return results
