from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelBase(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class SectionRule(ModelBase):
    rule_id: str
    file_type: str = ""
    module_name: str = ""
    sub_content_1: str = ""
    sub_content_2: str = ""
    sub_content_3: str = ""
    section_path: str
    has_standard_template: bool = False
    from_history_bid: bool = False
    ai_generated: bool = False
    user_upload_required: bool = False
    reference_technical_spec: bool = False
    raw_row: dict[str, Any] = Field(default_factory=dict)


class ManualSectionOverride(ModelBase):
    expected_pages: list[int] = Field(default_factory=list)
    parse_text: bool | None = None
    parse_table: bool | None = None
    use_ocr: bool | None = None
    content_type: str | None = None
    reuse_method: str | None = None
    enter_long_term_library: bool | None = None
    review_required: bool | None = None
    notes: list[str] = Field(default_factory=list)


class ManualConfig(ModelBase):
    company_id: str = "demo_company"
    document_id: str = "demo_document"
    file_type: str = ""
    ocr_pages: list[int] = Field(default_factory=list)
    ocr_sections: list[str] = Field(default_factory=list)
    table_sections: list[str] = Field(default_factory=list)
    skip_sections: list[str] = Field(default_factory=list)
    compound_material_rules: list[dict[str, Any]] = Field(default_factory=list)
    section_overrides: dict[str, ManualSectionOverride] = Field(default_factory=dict)


class ProcessingPlanItem(ModelBase):
    rule_id: str
    section_path: str
    from_history_bid: bool = False
    has_standard_template: bool = False
    expected_pages: list[int] = Field(default_factory=list)
    parse_text: bool = True
    parse_table: bool = False
    use_ocr: bool = False
    content_type: str = "template_text"
    reuse_method: str = "参考复用"
    enter_long_term_library: bool = True
    review_required: bool = True
    source_rule: dict[str, Any] = Field(default_factory=dict)
    source_override: dict[str, Any] = Field(default_factory=dict)
    process_strategy: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ProcessingPlan(ModelBase):
    company_id: str
    document_id: str
    file_type: str
    source_file: str = ""
    sections: list[ProcessingPlanItem] = Field(default_factory=list)


class PdfTextBlock(ModelBase):
    block_id: str
    page_no: int
    text: str
    bbox: list[float]
    block_no: int
    source_type: Literal["pdf_text", "ocr"] = "pdf_text"
    font_size: float | None = None
    confidence: float | None = None


class ParsedTable(ModelBase):
    table_id: str
    page_no: int
    rows: list[list[str]] = Field(default_factory=list)
    bbox: list[float] | None = None
    source_type: Literal["pdf_table", "pp_structure_table"] = "pdf_table"


class OCRBlock(ModelBase):
    text: str
    bbox: list[float] = Field(default_factory=list)
    confidence: float | None = None
    block_type: str = "ocr_text"


class OCRResult(ModelBase):
    page_no: int
    image_path: str
    blocks: list[OCRBlock] = Field(default_factory=list)
    raw_response: dict[str, Any] | list[Any] | str | None = None
    error: str | None = None


class TitleMapping(ModelBase):
    raw_context_title: str = ""
    normalized_context_title: str = ""
    material_title: str = ""
    rule_section_path: str = ""


class MaterialItemRef(ModelBase):
    type: Literal["text", "table", "image", "submaterial"]
    item_type: Literal["text", "table", "image", "submaterial"]
    item_id: str = ""
    page_no: int | None = None
    top_y: float = 0.0
    payload_ref: str | None = None
    nearest_heading: str = ""
    rule_section_path: str = ""
    material_path: str = ""
    order: int = 0
    block_id: str | None = None
    table_id: str | None = None
    image_id: str | None = None
    text: str | None = None
    table_title: str | None = None
    image_title: str | None = None
    bbox: list[float] | None = None
    rect: list[float] | None = None
    json_path: str | None = None
    file_path: str | None = None


class PageMaterialItem(ModelBase):
    item_id: str
    item_type: Literal["text", "table", "image"]
    source_type: str
    page_no: int
    reading_order: int = 0
    top_y: float = 0.0
    bbox: list[float] = Field(default_factory=list)
    text: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class OrderedMaterialPackage(ModelBase):
    material_title: str
    section_path: str
    material_path: str = ""
    rule_section_path: str = ""
    material_types: list[str] = Field(default_factory=list)
    dominant_material_type: str = "unknown"
    items: list[MaterialItemRef] = Field(default_factory=list)


class MaterialMeta(ModelBase):
    material_title: str
    section_path: str
    material_path: str = ""
    rule_section_path: str = ""
    rule_module_name: str = ""
    folder_parts: list[str] = Field(default_factory=list)
    source_file: str = ""
    source_page_start: int | None = None
    source_page_end: int | None = None
    source_start_y: float | None = None
    source_end_y: float | None = None
    source_start_block_id: str | None = None
    source_end_block_id: str | None = None
    original_capture: dict[str, Any] = Field(default_factory=dict)
    material_types: list[str] = Field(default_factory=list)
    dominant_material_type: str = "unknown"
    raw_context_title: str = ""
    title_mapping: TitleMapping = Field(default_factory=TitleMapping)
    text_item_count: int = 0
    table_item_count: int = 0
    image_item_count: int = 0
    ordered_item_count: int = 0
    review_status: str = "pending"


class CompoundInstanceMeta(ModelBase):
    material_type: str = "compound_instance"
    excel_anchor_path: str = ""
    rule_anchor_path: str = ""
    instance_title: str = ""
    instance_path: str = ""
    source_page_start: int | None = None
    source_page_end: int | None = None
    source_start_y: float | None = None
    source_end_y: float | None = None
    child_count: int = 0
    children: list[MaterialMeta] = Field(default_factory=list)
    review_status: str = "pending"


class ReconstructedSection(ModelBase):
    section_id: str
    title: str
    normalized_title: str
    level: int
    page_start: int
    page_end: int
    block_start_id: str | None = None
    block_end_id: str | None = None
    section_path: str | None = None
    source_type: str = "heuristic"


class SectionMatchResult(ModelBase):
    rule_id: str
    rule_section_path: str
    matched: bool
    matched_source_type: str = "section"
    matched_section_id: str | None = None
    matched_title: str | None = None
    matched_page_no: int | None = None
    matched_page_end: int | None = None
    matched_container_section_id: str | None = None
    matched_container_title: str | None = None
    matched_container_page_no: int | None = None
    matched_container_page_end: int | None = None
    matched_block_start_id: str | None = None
    matched_block_end_id: str | None = None
    confidence: float = 0.0
    match_reason: str = ""
    related_matches: list[dict[str, Any]] = Field(default_factory=list)
    process_strategy: list[str] = Field(default_factory=list)
    content_type: str = ""
    reuse_method: str = ""


class ReusableCandidate(ModelBase):
    candidate_id: str
    company_id: str
    document_id: str
    rule_id: str
    section_path: str
    from_history_bid: bool = False
    has_standard_template: bool = False
    title: str
    content: str
    fields: dict[str, Any] | None = None
    candidate_type: str
    storage_category: str = "needs_further_analysis"
    capture_mode: str = "pending_analysis"
    analysis_status: str = "pending_deeper_analysis"
    material_types: list[str] = Field(default_factory=list)
    dominant_material_type: str = "unknown"
    material_evidence: dict[str, Any] = Field(default_factory=dict)
    process_strategy: list[str] = Field(default_factory=list)
    reuse_method: str
    reuse_level: str
    enter_long_term_library: bool
    source_file: str
    source_page: int | None = None
    source_page_end: int | None = None
    source_container_title: str | None = None
    source_container_page: int | None = None
    source_container_page_end: int | None = None
    source_bbox: list[float] | None = None
    source_block_ids: list[str] = Field(default_factory=list)
    discovered_items: list[dict[str, Any]] = Field(default_factory=list)
    review_status: str = "pending"
    valid_status: str = "unknown"
    confidence: float = 0.0
    extraction_reason: str = ""


class RetrievalChunk(ModelBase):
    chunk_id: str
    company_id: str
    document_id: str
    candidate_id: str
    title: str
    content: str
    candidate_type: str
    section_path: str
    source_page: int | None = None
    reuse_method: str
    enter_long_term_library: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class StrategyDecision(ModelBase):
    process_strategy: list[str] = Field(default_factory=list)
    candidate_type: str
    storage_category: str
    capture_mode: str
    analysis_status: str
    reuse_method: str
    reuse_level: str
    reason: str
