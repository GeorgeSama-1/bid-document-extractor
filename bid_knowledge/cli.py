from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import click
import typer

from bid_knowledge.config.manual_config_loader import load_manual_config
from bid_knowledge.config.processing_plan_builder import build_processing_plan
from bid_knowledge.config.rule_loader import load_rules_from_excel, load_rules_from_json
from bid_knowledge.extraction.candidate_extractor import extract_candidates
from bid_knowledge.extraction.chunk_builder import build_chunks
from bid_knowledge.matching.section_matcher import match_sections
from bid_knowledge.parsing.material_stream import build_combined_page_material_stream
from bid_knowledge.parsing.layout_mask import build_layout_masks
from bid_knowledge.parsing.ocr_client import run_ocr
from bid_knowledge.parsing.ocr_merger import merge_ocr_results
from bid_knowledge.parsing.module_packager import package_module_artifacts
from bid_knowledge.parsing.pdf_parser import parse_pdf, render_pdf_pages
from bid_knowledge.parsing.pp_structure import run_pp_structure
from bid_knowledge.parsing.pp_table_extractor import extract_pp_structure_tables, merge_pp_and_pdf_tables
from bid_knowledge.parsing.section_builder import build_sections
from bid_knowledge.parsing.table_extractor import extract_tables
from bid_knowledge.parsing.table_region_detector import detect_candidate_table_regions, group_candidate_table_regions, groups_to_parsed_tables
from bid_knowledge.parsing.text_block_merger import merge_multiline_heading_blocks
from bid_knowledge.parsing.toc_leaf_builder import (
    build_toc_leaf_candidates,
    toc_leaf_section_paths,
    top_level_modules_from_toc_candidates,
)
from bid_knowledge.parsing.vlm_table_extractor import enhance_tables_with_vlm
from bid_knowledge.retrieval.bm25_retriever import BM25Retriever, load_chunks
from bid_knowledge.retrieval.retrieval_eval import evaluate_retrieval
from bid_knowledge.retrieval.vector_retriever import VectorRetriever
from bid_knowledge.schemas.models import (
    OCRResult,
    ParsedTable,
    PdfTextBlock,
    ProcessingPlan,
    ReconstructedSection,
    ReusableCandidate,
    SectionMatchResult,
)
from bid_knowledge.utils.io_utils import ensure_dir, read_json, write_json


app = typer.Typer(help="Bid knowledge pre-ingestion parsing and retrieval validation MVP.")


def _pipeline_echo(step: int, total: int, message: str) -> None:
    typer.echo(f"[{step}/{total}] {message}")


def _format_elapsed_seconds(seconds: float) -> str:
    milliseconds = int(round(max(0.0, seconds) * 1000))
    total_seconds, millis = divmod(milliseconds, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _pipeline_summary_echo(*, pdf: str, root: Path, elapsed_seconds: float) -> None:
    typer.echo("")
    typer.echo("========== Pipeline Summary ==========")
    typer.echo(f"PDF: {pdf}")
    typer.echo(f"Output: {root}")
    typer.echo(f"Total elapsed: {_format_elapsed_seconds(elapsed_seconds)}")
    typer.echo("======================================")


def _make_progress_callback(enabled: bool, label: str):
    if not enabled:
        class _NullProgress:
            def __enter__(self):
                return None

            def __exit__(self, exc_type, exc, tb):
                return False

        return _NullProgress()

    class _ProgressWrapper:
        def __enter__(self):
            self._bar = None
            self._last = 0

            def _callback(current: int, total: int) -> None:
                if total <= 0:
                    return
                if self._bar is None:
                    self._bar = click.progressbar(length=total, label=label)
                    self._bar.__enter__()
                delta = max(0, int(current) - self._last)
                if delta:
                    self._bar.update(delta)
                    self._last = int(current)

            self._callback = _callback
            return self._callback

        def __exit__(self, exc_type, exc, tb):
            if self._bar is not None:
                self._bar.__exit__(exc_type, exc, tb)
            return False

    return _ProgressWrapper()


def _parse_bool_flag(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "是"}


def _load_plan(path: str | Path) -> ProcessingPlan:
    return ProcessingPlan(**read_json(path))


def _load_blocks(path: str | Path) -> list[PdfTextBlock]:
    return [PdfTextBlock(**item) for item in read_json(path)]


def _load_tables(path: str | Path) -> list[ParsedTable]:
    return [ParsedTable(**item) for item in read_json(path)]


def _load_ocr(path: str | Path) -> list[OCRResult]:
    return [OCRResult(**item) for item in read_json(path)]


def _load_sections(path: str | Path) -> list[ReconstructedSection]:
    return [ReconstructedSection(**item) for item in read_json(path)]


def _load_matches(path: str | Path) -> list[SectionMatchResult]:
    return [SectionMatchResult(**item) for item in read_json(path)]


def _load_candidates(path: str | Path) -> list[ReusableCandidate]:
    return [ReusableCandidate(**item) for item in read_json(path)]


def _load_images(path: str | Path | None) -> list[dict]:
    if not path:
        return []
    return list(read_json(path))


def _build_page_material_stream_payload(
    *,
    blocks: list[PdfTextBlock],
    tables: list[ParsedTable],
    images: list[dict],
    pp_structure_results: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    return [
        item.model_dump()
        for item in build_combined_page_material_stream(
            blocks=blocks,
            tables=tables,
            images=images,
            pp_structure_results=pp_structure_results,
        )
    ]


def _document_page_count(parsed: dict[str, Any]) -> int:
    meta = parsed.get("document_meta") if isinstance(parsed, dict) else {}
    try:
        return int((meta or {}).get("page_count") or 0)
    except (TypeError, ValueError):
        return 0


def _top_level_modules_from_plan(plan: ProcessingPlan) -> list[str]:
    modules: list[str] = []
    seen: set[str] = set()
    for item in plan.sections:
        parts = [part.strip() for part in item.section_path.split(" / ") if part.strip()]
        if len(parts) < 2:
            continue
        module = parts[1]
        if module in seen:
            continue
        seen.add(module)
        modules.append(module)
    return modules


def _history_section_paths_from_plan(plan: ProcessingPlan) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for item in plan.sections:
        if not item.from_history_bid:
            continue
        path = item.section_path.strip()
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def _collect_ocr_pages_from_plan(plan: ProcessingPlan) -> list[int]:
    pages: set[int] = set()
    for item in plan.sections:
        if item.use_ocr:
            pages.update(page for page in item.expected_pages if page > 0)
    return sorted(pages)


@app.command("load-rules")
def load_rules_command(
    rules_xlsx: str = typer.Option(..., "--rules-xlsx"),
    out: str = typer.Option(..., "--out"),
    report: str = typer.Option(..., "--report"),
) -> None:
    rules, final_report = load_rules_from_excel(rules_xlsx, out_path=out, report_path=report)
    typer.echo(f"Loaded {len(rules)} rules from {rules_xlsx}")
    typer.echo(json.dumps(final_report, ensure_ascii=False, indent=2))


@app.command("build-plan")
def build_plan_command(
    rules: str = typer.Option(..., "--rules"),
    manual_config: Optional[str] = typer.Option(None, "--manual-config"),
    out: str = typer.Option(..., "--out"),
) -> None:
    rule_models = load_rules_from_json(rules)
    manual = load_manual_config(manual_config)
    plan = build_processing_plan(rule_models, manual, out_path=out)
    typer.echo(f"Built processing plan with {len(plan.sections)} sections -> {out}")


@app.command("parse-pdf")
def parse_pdf_command(
    pdf: str = typer.Option(..., "--pdf"),
    plan: str = typer.Option(..., "--plan"),
    out_dir: str = typer.Option(..., "--out-dir"),
    progress: str = typer.Option("true", "--progress"),
) -> None:
    with _make_progress_callback(_parse_bool_flag(progress), "Parsing PDF pages") as progress_callback:
        parse_pdf(pdf, plan=_load_plan(plan), out_dir=out_dir, progress_callback=progress_callback)
    typer.echo(f"Parsed PDF -> {out_dir}")


@app.command("extract-tables")
def extract_tables_command(
    pdf: str = typer.Option(..., "--pdf"),
    plan: str = typer.Option(..., "--plan"),
    out: str = typer.Option(..., "--out"),
    progress: str = typer.Option("true", "--progress"),
) -> None:
    with _make_progress_callback(_parse_bool_flag(progress), "Extracting tables") as progress_callback:
        tables = extract_tables(pdf, plan=_load_plan(plan), out_path=out, progress_callback=progress_callback)
    typer.echo(f"Extracted {len(tables)} tables -> {out}")


@app.command("run-ocr")
def run_ocr_command(
    pdf: str = typer.Option(..., "--pdf"),
    plan: str = typer.Option(..., "--plan"),
    parsed_dir: str = typer.Option(..., "--parsed-dir"),
    ocr_endpoint: Optional[str] = typer.Option(None, "--ocr-endpoint"),
    ocr_model: Optional[str] = typer.Option(None, "--ocr-model"),
    ocr_api_key: Optional[str] = typer.Option(None, "--ocr-api-key"),
    out: str = typer.Option(..., "--out"),
    progress: str = typer.Option("true", "--progress"),
) -> None:
    processing_plan = _load_plan(plan)
    pages = _collect_ocr_pages_from_plan(processing_plan)
    if not pages:
        write_json(out, [])
        typer.echo("No explicit OCR pages were configured. Wrote empty OCR results.")
        return

    page_images_dir = ensure_dir(Path(parsed_dir) / "page_images")
    page_images = []
    for page_no in pages:
        image_path = page_images_dir / f"page_{page_no:04d}.png"
        if not image_path.exists():
            render_pdf_pages(pdf, [page_no], page_images_dir)
        if image_path.exists():
            page_images.append({"page_no": page_no, "image_path": str(image_path)})

    with _make_progress_callback(_parse_bool_flag(progress), "Running OCR") as progress_callback:
        results = run_ocr(
            page_images,
            endpoint=ocr_endpoint,
            model=ocr_model,
            api_key=ocr_api_key,
            out_path=out,
            progress_callback=progress_callback,
        )
    typer.echo(f"OCR finished for {len(results)} pages -> {out}")


@app.command("merge-ocr")
def merge_ocr_command(
    blocks: str = typer.Option(..., "--blocks"),
    ocr: str = typer.Option(..., "--ocr"),
    out: str = typer.Option(..., "--out"),
) -> None:
    merged = merge_ocr_results(_load_blocks(blocks), _load_ocr(ocr), out_path=out)
    typer.echo(f"Merged OCR into {len(merged)} blocks -> {out}")


@app.command("run-pp-structure")
def run_pp_structure_command(
    input_path: str = typer.Option(..., "--input"),
    out: str = typer.Option(..., "--out"),
    device: str = typer.Option("gpu", "--device"),
    use_doc_orientation_classify: str = typer.Option("false", "--use-doc-orientation-classify"),
    use_doc_unwarping: str = typer.Option("false", "--use-doc-unwarping"),
    use_textline_orientation: str = typer.Option("false", "--use-textline-orientation"),
    progress: str = typer.Option("true", "--progress"),
) -> None:
    with _make_progress_callback(_parse_bool_flag(progress), "Running PP-StructureV3") as progress_callback:
        results = run_pp_structure(
            input_path,
            out_path=out,
            device=device,
            use_doc_orientation_classify=_parse_bool_flag(use_doc_orientation_classify),
            use_doc_unwarping=_parse_bool_flag(use_doc_unwarping),
            use_textline_orientation=_parse_bool_flag(use_textline_orientation),
            progress_callback=progress_callback,
        )
    typer.echo(f"PP-StructureV3 finished for {len(results)} pages -> {out}")


@app.command("build-sections")
def build_sections_command(
    blocks: str = typer.Option(..., "--blocks"),
    toc: str = typer.Option(..., "--toc"),
    rules: str = typer.Option(..., "--rules"),
    out: str = typer.Option(..., "--out"),
) -> None:
    sections = build_sections(
        blocks=_load_blocks(blocks),
        toc=read_json(toc),
        rules=load_rules_from_json(rules),
        out_path=out,
    )
    typer.echo(f"Built {len(sections)} sections -> {out}")


@app.command("match-sections")
def match_sections_command(
    rules: str = typer.Option(..., "--rules"),
    sections: str = typer.Option(..., "--sections"),
    plan: str = typer.Option(..., "--plan"),
    blocks: Optional[str] = typer.Option(None, "--blocks"),
    out: str = typer.Option(..., "--out"),
) -> None:
    results = match_sections(
        rules=load_rules_from_json(rules),
        sections=_load_sections(sections),
        plan=_load_plan(plan),
        blocks=_load_blocks(blocks) if blocks else [],
        out_path=out,
    )
    typer.echo(f"Matched {len(results)} rules -> {out}")


@app.command("extract-candidates")
def extract_candidates_command(
    plan: str = typer.Option(..., "--plan"),
    matches: str = typer.Option(..., "--matches"),
    blocks: str = typer.Option(..., "--blocks"),
    tables: str = typer.Option(..., "--tables"),
    images: Optional[str] = typer.Option(None, "--images"),
    out_json: str = typer.Option(..., "--out-json"),
    out_csv: str = typer.Option(..., "--out-csv"),
) -> None:
    candidates = extract_candidates(
        plan=_load_plan(plan),
        matches=_load_matches(matches),
        blocks=_load_blocks(blocks),
        tables=_load_tables(tables),
        images=_load_images(images),
        out_json=out_json,
        out_csv=out_csv,
    )
    typer.echo(f"Extracted {len(candidates)} candidates -> {out_json}")


@app.command("package-materials")
def package_materials_command(
    candidates: str = typer.Option(..., "--candidates"),
    blocks: str = typer.Option(..., "--blocks"),
    tables: str = typer.Option(..., "--tables"),
    images: str = typer.Option(..., "--images"),
    out_dir: str = typer.Option(..., "--out-dir"),
    pdf: Optional[str] = typer.Option(None, "--pdf"),
    plan: Optional[str] = typer.Option(None, "--plan"),
    manual_config: Optional[str] = typer.Option(None, "--manual-config"),
) -> None:
    plan_model = _load_plan(plan) if plan else None
    manual = load_manual_config(manual_config) if manual_config else None
    manifest = package_module_artifacts(
        candidates=_load_candidates(candidates),
        blocks=_load_blocks(blocks),
        tables=_load_tables(tables),
        images=_load_images(images),
        out_dir=out_dir,
        pdf_path=pdf,
        top_level_modules=_top_level_modules_from_plan(plan_model) if plan_model else None,
        planned_section_paths=_history_section_paths_from_plan(plan_model) if plan_model else None,
        compound_material_rules=manual.compound_material_rules if manual and manual.compound_material_rules else None,
    )
    typer.echo(f"Packaged {len(manifest.get('sections', []))} sections -> {out_dir}")


@app.command("pdf-toc-pipeline")
def pdf_toc_pipeline_command(
    pdf: str = typer.Option(..., "--pdf"),
    out_dir: str = typer.Option(..., "--out-dir"),
    path_root: str = typer.Option("PDF", "--path-root"),
    enable_pp_structure: str = typer.Option("false", "--enable-pp-structure"),
    pp_structure_device: str = typer.Option("gpu", "--pp-structure-device"),
    pp_structure_use_doc_orientation_classify: str = typer.Option("false", "--pp-structure-use-doc-orientation-classify"),
    pp_structure_use_doc_unwarping: str = typer.Option("false", "--pp-structure-use-doc-unwarping"),
    pp_structure_use_textline_orientation: str = typer.Option("false", "--pp-structure-use-textline-orientation"),
    enable_vlm_table: str = typer.Option("false", "--enable-vlm-table"),
    vlm_table_endpoint: Optional[str] = typer.Option(None, "--vlm-table-endpoint"),
    vlm_table_model: Optional[str] = typer.Option(None, "--vlm-table-model"),
    vlm_table_api_key: Optional[str] = typer.Option(None, "--vlm-table-api-key"),
    vlm_table_api_key_env: Optional[str] = typer.Option(None, "--vlm-table-api-key-env"),
    vlm_table_timeout: int = typer.Option(180, "--vlm-table-timeout"),
    vlm_table_max_tokens: int = typer.Option(4096, "--vlm-table-max-tokens"),
    vlm_table_workers: int = typer.Option(1, "--vlm-table-workers"),
    progress: str = typer.Option("true", "--progress"),
) -> None:
    pipeline_started_at = time.perf_counter()
    pp_structure_enabled = _parse_bool_flag(enable_pp_structure)
    vlm_table_enabled = _parse_bool_flag(enable_vlm_table)
    show_progress = _parse_bool_flag(progress)
    total_steps = 7 if vlm_table_enabled else 6
    root = ensure_dir(out_dir)
    parsed_dir = ensure_dir(root / "parsed")
    candidates_dir = ensure_dir(root / "candidates")
    ensure_dir(root / "modules")

    _pipeline_echo(1, total_steps, "Parsing PDF text, images, and TOC")
    with _make_progress_callback(show_progress, "Parsing PDF pages") as progress_callback:
        parsed = parse_pdf(pdf, plan=None, out_dir=parsed_dir, progress_callback=progress_callback)

    toc = list(parsed.get("toc") or [])
    page_count = _document_page_count(parsed)
    if not toc:
        raise typer.BadParameter("当前 PDF 没有可用目录，无法按目录叶子章节展开。")

    pp_structure_results: list[dict[str, Any]] = []
    if pp_structure_enabled:
        _pipeline_echo(2, total_steps, "Running PP-StructureV3 for positioning")
        with _make_progress_callback(show_progress, "Running PP-StructureV3") as progress_callback:
            pp_structure_results = run_pp_structure(
                pdf,
                out_path=parsed_dir / "pp_structure_results.json",
                device=pp_structure_device,
                use_doc_orientation_classify=_parse_bool_flag(pp_structure_use_doc_orientation_classify),
                use_doc_unwarping=_parse_bool_flag(pp_structure_use_doc_unwarping),
                use_textline_orientation=_parse_bool_flag(pp_structure_use_textline_orientation),
                progress_callback=progress_callback,
            )
    else:
        _pipeline_echo(2, total_steps, "PP-StructureV3 disabled; using PDF-native positioning only")
        write_json(parsed_dir / "pp_structure_results.json", [])
    layout_masks = build_layout_masks(pp_structure_results)
    write_json(parsed_dir / "page_layout_masks.json", layout_masks)
    images = _load_images(parsed_dir / "images.json")

    if pp_structure_enabled:
        _pipeline_echo(3, total_steps, "Detecting table regions")
        with _make_progress_callback(show_progress, "Extracting PDF-native fallback tables") as progress_callback:
            pdf_tables = extract_tables(pdf, plan=None, progress_callback=progress_callback)
        pp_tables = extract_pp_structure_tables(pp_structure_results)
        source_tables = merge_pp_and_pdf_tables(pp_tables, pdf_tables)
        with _make_progress_callback(show_progress, "Detecting table regions") as progress_callback:
            table_regions = detect_candidate_table_regions(
                pdf_path=pdf,
                pdf_tables=source_tables,
                images=images,
                pp_structure_results=pp_structure_results,
                out_dir=parsed_dir / "table_regions",
                progress_callback=progress_callback,
            )
        table_groups = group_candidate_table_regions(table_regions, out_dir=parsed_dir / "table_regions")
        tables = groups_to_parsed_tables(table_groups, source_tables, pdf_path=pdf)
        write_json(parsed_dir / "tables.json", tables)
    else:
        _pipeline_echo(3, total_steps, "Detecting PDF-native table regions")
        with _make_progress_callback(show_progress, "Extracting tables") as progress_callback:
            source_tables = extract_tables(pdf, plan=None, progress_callback=progress_callback)
        with _make_progress_callback(show_progress, "Detecting table regions") as progress_callback:
            table_regions = detect_candidate_table_regions(
                pdf_path=pdf,
                pdf_tables=source_tables,
                images=images,
                pp_structure_results=[],
                out_dir=parsed_dir / "table_regions",
                progress_callback=progress_callback,
            )
        table_groups = group_candidate_table_regions(table_regions, out_dir=parsed_dir / "table_regions")
        tables = groups_to_parsed_tables(table_groups, source_tables, pdf_path=pdf)
        write_json(parsed_dir / "tables.json", tables)

    if vlm_table_enabled:
        _pipeline_echo(4, total_steps, "Enhancing tables with VLM")
        with _make_progress_callback(show_progress, "Enhancing tables with VLM") as progress_callback:
            tables = enhance_tables_with_vlm(
                pdf_path=pdf,
                tables=tables,
                images=images,
                out_dir=parsed_dir / "vlm_tables",
                endpoint=vlm_table_endpoint,
                model=vlm_table_model,
                api_key=vlm_table_api_key,
                api_key_env=vlm_table_api_key_env,
                request_timeout=vlm_table_timeout,
                max_tokens=vlm_table_max_tokens,
                incremental_out_path=parsed_dir / "tables.json",
                workers=vlm_table_workers,
                progress_callback=progress_callback,
            )
        write_json(parsed_dir / "tables.json", tables)

    _pipeline_echo(5 if vlm_table_enabled else 4, total_steps, "Building TOC leaf sections")
    blocks = merge_multiline_heading_blocks(_load_blocks(parsed_dir / "text_blocks.json"))
    write_json(parsed_dir / "text_blocks_merged.json", blocks)
    candidates = build_toc_leaf_candidates(
        toc=toc,
        page_count=page_count,
        path_root=path_root,
        company_id="pdf",
        document_id=Path(pdf).stem,
        blocks=blocks,
    )
    for candidate in candidates:
        candidate.source_file = str(pdf)
    write_json(candidates_dir / "toc_leaf_candidates.json", candidates)
    planned_paths = toc_leaf_section_paths(candidates)
    write_json(candidates_dir / "toc_leaf_section_paths.json", planned_paths)

    _pipeline_echo(6 if vlm_table_enabled else 5, total_steps, "Building page material stream")
    page_material_stream = _build_page_material_stream_payload(
        blocks=blocks,
        tables=tables,
        images=images,
        pp_structure_results=pp_structure_results,
    )
    write_json(parsed_dir / "page_material_stream.json", page_material_stream)

    _pipeline_echo(7 if vlm_table_enabled else 6, total_steps, "Packaging TOC leaf materials")
    manifest = package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=images,
        out_dir=root,
        pdf_path=pdf,
        top_level_modules=top_level_modules_from_toc_candidates(candidates),
        planned_section_paths=planned_paths,
        compound_material_rules=[],
        page_material_items=page_material_stream,
        layout_masks=layout_masks,
    )
    write_json(root / "pdf_toc_pipeline_manifest.json", manifest)
    elapsed_seconds = time.perf_counter() - pipeline_started_at
    typer.echo(f"PDF TOC pipeline completed -> {root}")
    _pipeline_summary_echo(pdf=pdf, root=root, elapsed_seconds=elapsed_seconds)


@app.command("build-chunks")
def build_chunks_command(
    candidates: str = typer.Option(..., "--candidates"),
    out: str = typer.Option(..., "--out"),
) -> None:
    chunks = build_chunks(_load_candidates(candidates), out_path=out)
    typer.echo(f"Built {len(chunks)} chunks -> {out}")


@app.command("search")
def search_command(
    chunks: str = typer.Option(..., "--chunks"),
    query: str = typer.Option(..., "--query"),
    top_k: int = typer.Option(5, "--top-k"),
    method: str = typer.Option("bm25", "--method"),
) -> None:
    chunk_models = load_chunks(chunks)
    retriever = BM25Retriever(chunk_models) if method == "bm25" else VectorRetriever(chunk_models)
    results = retriever.search(query, top_k=top_k)
    typer.echo(json.dumps(results, ensure_ascii=False, indent=2))


@app.command("eval-retrieval")
def eval_retrieval_command(
    chunks: str = typer.Option(..., "--chunks"),
    queries: str = typer.Option(..., "--queries"),
    out: str = typer.Option(..., "--out"),
    method: str = typer.Option("bm25", "--method"),
    top_k: int = typer.Option(5, "--top-k"),
) -> None:
    report = evaluate_retrieval(chunks, queries, method=method, top_k=top_k, out_path=out)
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))


@app.command("pipeline")
def pipeline_command(
    rules_xlsx: str = typer.Option(..., "--rules-xlsx"),
    pdf: str = typer.Option(..., "--pdf"),
    manual_config: Optional[str] = typer.Option(None, "--manual-config"),
    out_dir: str = typer.Option(..., "--out-dir"),
    enable_ocr: str = typer.Option("false", "--enable-ocr"),
    ocr_endpoint: Optional[str] = typer.Option(None, "--ocr-endpoint"),
    ocr_model: Optional[str] = typer.Option(None, "--ocr-model"),
    ocr_api_key: Optional[str] = typer.Option(None, "--ocr-api-key"),
    enable_pp_structure: str = typer.Option("false", "--enable-pp-structure"),
    pp_structure_device: str = typer.Option("gpu", "--pp-structure-device"),
    pp_structure_use_doc_orientation_classify: str = typer.Option("false", "--pp-structure-use-doc-orientation-classify"),
    pp_structure_use_doc_unwarping: str = typer.Option("false", "--pp-structure-use-doc-unwarping"),
    pp_structure_use_textline_orientation: str = typer.Option("false", "--pp-structure-use-textline-orientation"),
    progress: str = typer.Option("true", "--progress"),
) -> None:
    pp_structure_enabled = _parse_bool_flag(enable_pp_structure)
    show_progress = _parse_bool_flag(progress)
    total_steps = 14 if pp_structure_enabled else 13
    root = ensure_dir(out_dir)
    rules_dir = ensure_dir(root / "rules")
    plan_dir = ensure_dir(root / "plan")
    parsed_dir = ensure_dir(root / "parsed")
    structure_dir = ensure_dir(root / "structure")
    candidates_dir = ensure_dir(root / "candidates")
    retrieval_dir = ensure_dir(root / "retrieval")
    ensure_dir(root / "modules")

    rules_path = rules_dir / "section_rules.json"
    report_path = rules_dir / "rule_load_report.json"
    _pipeline_echo(1, total_steps, "Loading Excel rules")
    rules, _ = load_rules_from_excel(rules_xlsx, out_path=rules_path, report_path=report_path)

    _pipeline_echo(2, total_steps, "Building processing plan")
    manual = load_manual_config(manual_config)
    plan = build_processing_plan(rules, manual)
    plan.source_file = str(Path(pdf))
    plan_path = plan_dir / "processing_plan.json"
    write_json(plan_path, plan)

    _pipeline_echo(3, total_steps, "Parsing PDF text, images, and TOC")
    with _make_progress_callback(show_progress, "Parsing PDF pages") as progress_callback:
        parse_pdf(pdf, plan=plan, out_dir=parsed_dir, progress_callback=progress_callback)
    _pipeline_echo(4, total_steps, "Extracting tables")
    with _make_progress_callback(show_progress, "Extracting tables") as progress_callback:
        tables = extract_tables(pdf, plan=plan, out_path=parsed_dir / "tables.json", progress_callback=progress_callback)

    merged_blocks_path = parsed_dir / "text_blocks_merged.json"
    raw_blocks = merge_multiline_heading_blocks(_load_blocks(parsed_dir / "text_blocks.json"))
    ocr_enabled = _parse_bool_flag(enable_ocr)
    if ocr_enabled:
        pages = _collect_ocr_pages_from_plan(plan)
        if pages:
            _pipeline_echo(5, total_steps, f"Running OCR for {len(pages)} configured pages")
        else:
            _pipeline_echo(5, total_steps, "OCR enabled but no explicit OCR pages were configured; skipping OCR requests.")
        page_images_dir = ensure_dir(parsed_dir / "page_images")
        page_images = []
        if pages:
            render_pdf_pages(pdf, pages, page_images_dir)
            page_images = [{"page_no": page, "image_path": str(page_images_dir / f"page_{page:04d}.png")} for page in pages]
        with _make_progress_callback(show_progress, "Running OCR") as progress_callback:
            ocr_results = run_ocr(
                page_images,
                endpoint=ocr_endpoint or os.getenv("OCR_ENDPOINT"),
                model=ocr_model or os.getenv("OCR_MODEL"),
                api_key=ocr_api_key or os.getenv("OCR_API_KEY"),
                out_path=parsed_dir / "ocr_results.json",
                progress_callback=progress_callback,
            )
        merge_ocr_results(raw_blocks, ocr_results, out_path=merged_blocks_path)
    else:
        _pipeline_echo(5, total_steps, "OCR disabled; using PDF text only")
        write_json(parsed_dir / "ocr_results.json", [])
        write_json(merged_blocks_path, raw_blocks)

    pp_structure_results: list[dict[str, Any]] = []
    if pp_structure_enabled:
        _pipeline_echo(6, total_steps, "Running PP-StructureV3")
        with _make_progress_callback(show_progress, "Running PP-StructureV3") as progress_callback:
            pp_structure_results = run_pp_structure(
                pdf,
                out_path=parsed_dir / "pp_structure_results.json",
                device=pp_structure_device,
                use_doc_orientation_classify=_parse_bool_flag(pp_structure_use_doc_orientation_classify),
                use_doc_unwarping=_parse_bool_flag(pp_structure_use_doc_unwarping),
                use_textline_orientation=_parse_bool_flag(pp_structure_use_textline_orientation),
                progress_callback=progress_callback,
            )
    else:
        _pipeline_echo(6, total_steps, "PP-StructureV3 disabled; using PDF-native parsing only")
        write_json(parsed_dir / "pp_structure_results.json", [])
    layout_masks = build_layout_masks(pp_structure_results)
    write_json(parsed_dir / "page_layout_masks.json", layout_masks)

    _pipeline_echo(7, total_steps, "Building page material stream")
    merged_blocks = merge_multiline_heading_blocks(_load_blocks(merged_blocks_path))
    write_json(merged_blocks_path, merged_blocks)
    parsed_images = _load_images(parsed_dir / "images.json")
    page_material_stream = _build_page_material_stream_payload(
        blocks=merged_blocks,
        tables=tables,
        images=parsed_images,
        pp_structure_results=pp_structure_results,
    )
    write_json(parsed_dir / "page_material_stream.json", page_material_stream)

    _pipeline_echo(8, total_steps, "Building reconstructed sections")
    sections = build_sections(
        blocks=merged_blocks,
        toc=read_json(parsed_dir / "toc.json"),
        rules=rules,
        out_path=structure_dir / "reconstructed_sections.json",
    )
    _pipeline_echo(9, total_steps, "Matching rules to reconstructed sections")
    matches = match_sections(
        rules=rules,
        sections=sections,
        plan=plan,
        blocks=merged_blocks,
        out_path=structure_dir / "section_match_results.json",
    )
    _pipeline_echo(10, total_steps, "Extracting reusable candidates")
    candidates = extract_candidates(
        plan=plan,
        matches=matches,
        blocks=merged_blocks,
        tables=tables,
        images=parsed_images,
        out_json=candidates_dir / "reusable_candidates.json",
        out_csv=candidates_dir / "candidate_report.csv",
    )
    _pipeline_echo(11, total_steps, "Packaging module artifacts")
    package_module_artifacts(
        candidates=candidates,
        blocks=merged_blocks,
        tables=tables,
        images=parsed_images,
        out_dir=root,
        pdf_path=pdf,
        top_level_modules=_top_level_modules_from_plan(plan),
        planned_section_paths=_history_section_paths_from_plan(plan),
        compound_material_rules=manual.compound_material_rules or None,
        page_material_items=page_material_stream,
        layout_masks=layout_masks,
    )
    _pipeline_echo(12, total_steps, "Building retrieval chunks")
    build_chunks(candidates, out_path=retrieval_dir / "chunks.jsonl")

    _pipeline_echo(13, total_steps, "Evaluating retrieval")
    default_queries = Path("data/test_queries.json")
    if default_queries.exists():
        evaluate_retrieval(
            retrieval_dir / "chunks.jsonl",
            default_queries,
            method="bm25",
            top_k=5,
            out_path=retrieval_dir / "retrieval_eval_report.json",
        )
    else:
        write_json(retrieval_dir / "retrieval_eval_report.json", {"skipped": True, "reason": "test_queries_not_found"})

    _pipeline_echo(14 if pp_structure_enabled else 13, total_steps, "Pipeline finished")
    typer.echo(f"Pipeline completed -> {root}")


if __name__ == "__main__":
    app()
