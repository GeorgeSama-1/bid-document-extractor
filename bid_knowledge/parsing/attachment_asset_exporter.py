from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bid_knowledge.utils.io_utils import ensure_dir, write_json


def sanitize_asset_name(title: str) -> str:
    sanitized = re.sub(r"[\\/:*?\"<>|]+", "_", (title or "").strip())
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized or "未命名附件"


def group_asset_name(candidate: dict[str, Any], anchor_text: str | None = None) -> str:
    text = (anchor_text or "").strip()
    if text.startswith("附："):
        text = text[2:].strip()
    base = text or str(candidate.get("title") or candidate.get("section_path") or "未命名附件")
    return sanitize_asset_name(base)


def candidate_page_numbers(candidate: dict[str, Any]) -> list[int]:
    start = candidate.get("source_page")
    end = candidate.get("source_page_end") or start
    if not start:
        return []
    start = int(start)
    end = int(end) if end else start
    if end < start:
        end = start
    return list(range(start, end + 1))


def _normalize_anchor_text(text: str) -> str:
    normalized = re.sub(r"[（()）/、，,：:\-\s'‘’“”]+", "", text or "")
    return normalized.lower()


def _bigram_overlap(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return 1.0
    if len(left) < 2 or len(right) < 2:
        return 0.0
    left_bigrams = {left[idx : idx + 2] for idx in range(len(left) - 1)}
    right_bigrams = {right[idx : idx + 2] for idx in range(len(right) - 1)}
    if not left_bigrams:
        return 0.0
    return len(left_bigrams & right_bigrams) / len(left_bigrams)


def anchor_matches_candidate(candidate_title: str, anchor_text: str) -> bool:
    title = candidate_title or ""
    text = anchor_text or ""
    role_terms = []
    for role in ("法定代表人", "被授权人", "制造商", "投标人"):
        if role in title:
            role_terms.append(role)
    if role_terms and not all(role in text for role in role_terms):
        return False
    for required in ("身份证", "营业执照", "保函", "基本账户证明", "税务登记"):
        if required in title and required not in text:
            return False
    normalized_title = _normalize_anchor_text(title)
    normalized_text = _normalize_anchor_text(text)
    if normalized_title and normalized_text and _bigram_overlap(normalized_title, normalized_text) < 0.35:
        return False
    return True


def _is_heading_like(text: str) -> bool:
    stripped = (text or "").strip()
    return bool(re.match(r"^\d+(?:\.\d+)*[、.．]", stripped)) or len(stripped) <= 40


def select_meaningful_images(page_images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = []
    for item in page_images:
        width = float(item.get("width", 0) or 0)
        height = float(item.get("height", 0) or 0)
        rect = item.get("rect") or []
        rect_width = max(float(rect[2]) - float(rect[0]), 0.0) if len(rect) >= 4 else 0.0
        rect_height = max(float(rect[3]) - float(rect[1]), 0.0) if len(rect) >= 4 else 0.0
        if width < 200 or height < 120:
            continue
        if rect_width * rect_height < 20000:
            continue
        selected.append(item)
    return sorted(selected, key=lambda item: (item["rect"][1], item["rect"][0], -item["width"] * item["height"]))


def assign_images_to_anchors(
    anchors: list[dict[str, Any]],
    images: list[dict[str, Any]],
    boundary_anchors: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not anchors:
        return []
    sorted_anchors = sorted(anchors, key=lambda item: (item["page_no"], item["y"]))
    sorted_boundaries = sorted(boundary_anchors or anchors, key=lambda item: (item["page_no"], item["y"]))
    sorted_images = sorted(images, key=lambda item: (item["page_no"], item["rect"][1], item["rect"][0]))
    groups: list[dict[str, Any]] = []
    for anchor in sorted_anchors:
        anchor_page = int(anchor["page_no"])
        anchor_y = float(anchor["y"])
        next_anchor = next(
            (
                candidate
                for candidate in sorted_boundaries
                if int(candidate["page_no"]) == anchor_page and float(candidate["y"]) > anchor_y
            ),
            None,
        )
        grouped_images = []
        for image in sorted_images:
            page_no = int(image["page_no"])
            top_y = float(image["rect"][1])
            if page_no != anchor_page:
                continue
            if top_y <= anchor_y:
                continue
            if next_anchor and int(next_anchor["page_no"]) == anchor_page and top_y >= float(next_anchor["y"]):
                continue
            grouped_images.append(image)
        grouped_images = sorted(
            grouped_images,
            key=lambda item: (round(float(item["rect"][1]) / 40.0), item["rect"][0], item["rect"][1]),
        )
        groups.append({**anchor, "images": grouped_images})
    return groups


def assign_images_to_candidates(
    candidates: list[dict[str, Any]],
    selected_images: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not candidates or not selected_images:
        return []
    ordered_images = sorted(selected_images, key=lambda item: (item["rect"][1], item["rect"][0]))
    if len(selected_images) == 1:
        return [{**candidates[0], **ordered_images[0]}]

    assigned = []
    for index, candidate in enumerate(candidates):
        if index >= len(ordered_images):
            break
        assigned.append({**candidate, **ordered_images[index]})
    return assigned


def _page_image_descriptors(page) -> list[dict[str, Any]]:
    descriptors = []
    for image_index, image_info in enumerate(page.get_images(full=True)):
        xref = image_info[0]
        rects = page.get_image_rects(xref)
        if not rects:
            continue
        rect = rects[0]
        descriptors.append(
            {
                "image_index": image_index,
                "xref": xref,
                "width": image_info[2],
                "height": image_info[3],
                "rect": [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)],
            }
        )
    return descriptors


def _page_text_anchors(page_no: int, page, candidate_title: str | None = None) -> list[dict[str, Any]]:
    data = page.get_text("dict")
    anchors: list[dict[str, Any]] = []
    title_text = candidate_title or ""
    core_terms = []
    for term in ("法定代表人", "被授权人", "身份证", "营业执照", "保函", "基本账户证明", "税务登记"):
        if term in title_text:
            core_terms.append(term)
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if not text:
                continue
            is_attachment_anchor = "附" in text or "扫描件" in text
            is_candidate_heading = bool(title_text) and _is_heading_like(text)
            if not is_attachment_anchor and not is_candidate_heading:
                continue
            if title_text and not anchor_matches_candidate(title_text, text):
                continue
            anchors.append(
                {
                    "page_no": page_no,
                    "text": text,
                    "y": float(line.get("bbox", [0, 0, 0, 0])[1]),
                }
            )
    return anchors


def export_attachment_images(
    pdf_path: str | Path,
    candidates: list[dict[str, Any]],
    out_dir: str | Path,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - dependency-driven
        raise RuntimeError("需要先安装 PyMuPDF 才能导出附件图片。") from exc

    source_pdf = Path(pdf_path)
    output_dir = ensure_dir(out_dir)
    exported_items: list[dict[str, Any]] = []
    doc = fitz.open(source_pdf)
    try:
        page_claims: dict[int, list[dict[str, Any]]] = {}
        ranged_candidates: list[dict[str, Any]] = []
        single_page_candidates: list[dict[str, Any]] = []
        for candidate in candidates:
            pages = candidate_page_numbers(candidate)
            if not pages:
                continue
            if len(pages) > 1:
                ranged_candidates.append(candidate)
            else:
                single_page_candidates.append(candidate)
                page_claims.setdefault(pages[0], []).append(candidate)

        # Single-page candidates share page images by top-to-bottom assignment.
        for page_no, page_candidates in sorted(page_claims.items()):
            page = doc.load_page(page_no - 1)
            selected_images = select_meaningful_images(_page_image_descriptors(page))
            assigned = assign_images_to_candidates(page_candidates, selected_images)

            for item in page_candidates:
                matched = next((row for row in assigned if row.get("candidate_id") == item.get("candidate_id")), None)
                record = dict(item)
                if matched is None:
                    record["export_status"] = "no_meaningful_embedded_image_found"
                    exported_items.append(record)
                    continue

                image_bytes = doc.extract_image(int(matched["xref"]))
                ext = image_bytes.get("ext", "png")
                asset_name = group_asset_name(item)
                target = output_dir / f"{asset_name}.{ext}"
                suffix = 2
                while target.exists():
                    target = output_dir / f"{asset_name}_{suffix}.{ext}"
                    suffix += 1
                target.write_bytes(image_bytes["image"])

                record["export_status"] = "exported_embedded_image"
                record["exported_image"] = str(target)
                record["image_xref"] = matched["xref"]
                record["image_rect"] = matched["rect"]
                exported_items.append(record)

        # Ranged candidates can legitimately own multiple attachment images across pages.
        for candidate in ranged_candidates:
            pages = candidate_page_numbers(candidate)
            meaningful_images: list[dict[str, Any]] = []
            anchors: list[dict[str, Any]] = []
            boundary_anchors: list[dict[str, Any]] = []
            discovered_items = candidate.get("discovered_items") or []
            for page_no in pages:
                page = doc.load_page(page_no - 1)
                boundary_anchors.extend(_page_text_anchors(page_no, page))
                candidate_titles = []
                for item in discovered_items:
                    if item.get("page_no") == page_no and item.get("title"):
                        candidate_titles.append(str(item["title"]))
                if not candidate_titles:
                    candidate_titles.append(str(candidate.get("title") or candidate.get("section_path") or ""))
                for candidate_title in candidate_titles:
                    anchors.extend(_page_text_anchors(page_no, page, candidate_title))
                for image in select_meaningful_images(_page_image_descriptors(page)):
                    meaningful_images.append({**image, "page_no": page_no})
            if not meaningful_images:
                exported_items.append({**candidate, "export_status": "no_meaningful_embedded_image_found"})
                continue
            grouped = assign_images_to_anchors(anchors, meaningful_images, boundary_anchors) if anchors else []
            if grouped and any(group["images"] for group in grouped):
                group_counter = 0
                for group in grouped:
                    if not group["images"]:
                        continue
                    group_counter += 1
                    asset_name = group_asset_name(candidate, str(group.get("text") or ""))
                    for image_index, image in enumerate(group["images"], start=1):
                        image_bytes = doc.extract_image(int(image["xref"]))
                        ext = image_bytes.get("ext", "png")
                        target = output_dir / f"{asset_name}_{image_index}.{ext}"
                        suffix = 2
                        while target.exists():
                            target = output_dir / f"{asset_name}_{image_index}_{suffix}.{ext}"
                            suffix += 1
                        target.write_bytes(image_bytes["image"])
                        exported_items.append(
                            {
                                **candidate,
                                "export_status": "exported_embedded_image",
                                "exported_image": str(target),
                                "image_xref": image["xref"],
                                "image_rect": image["rect"],
                                "image_page_no": image["page_no"],
                                "image_index": image_index,
                                "group_index": group_counter,
                                "group_anchor_text": group["text"],
                                "group_title": asset_name,
                            }
                        )
            else:
                asset_name = group_asset_name(candidate)
                for index, image in enumerate(meaningful_images, start=1):
                    image_bytes = doc.extract_image(int(image["xref"]))
                    ext = image_bytes.get("ext", "png")
                    target = output_dir / f"{asset_name}_{index}.{ext}"
                    target.write_bytes(image_bytes["image"])
                    exported_items.append(
                        {
                            **candidate,
                            "export_status": "exported_embedded_image",
                            "exported_image": str(target),
                            "image_xref": image["xref"],
                            "image_rect": image["rect"],
                            "image_page_no": image["page_no"],
                            "image_index": index,
                        }
                    )
    finally:
        doc.close()

    # Preserve entries that still have no page number at all.
    for candidate in candidates:
        if not candidate.get("source_page"):
            exported_items.append({**candidate, "export_status": "source_page_not_found"})

    manifest = {
        "source_pdf": str(source_pdf),
        "export_count": sum(1 for item in exported_items if item.get("export_status") == "exported_embedded_image"),
        "items": exported_items,
    }
    if manifest_path:
        write_json(manifest_path, manifest)
    return manifest
