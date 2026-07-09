from __future__ import annotations

import json
from pathlib import Path

from bid_knowledge.schemas.models import ManualConfig


def default_manual_config() -> ManualConfig:
    return ManualConfig(
        company_id="demo_company",
        document_id="demo_document",
        file_type="",
        ocr_pages=[],
        ocr_sections=[],
        table_sections=[],
        skip_sections=[],
        compound_material_rules=[],
        section_overrides={},
    )


def load_manual_config(path: str | Path | None = None) -> ManualConfig:
    if path is None:
        return default_manual_config()

    config_path = Path(path)
    if not config_path.exists():
        return default_manual_config()

    data = json.loads(config_path.read_text(encoding="utf-8"))
    return ManualConfig(**data)
