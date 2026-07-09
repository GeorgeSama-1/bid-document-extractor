#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bid_knowledge.export.lightweight_material_pack import export_lightweight_material_pack


def _parse_bool(value: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected boolean value, got: {value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a lightweight material package for bid material reuse.")
    parser.add_argument("--output-dir", required=True, help="Pipeline output directory, for example outputs/structure_run_v6")
    parser.add_argument("--package-dir", default=None, help="Optional temporary package directory. Defaults to <output-dir>/material_pack")
    parser.add_argument("--zip", dest="zip_path", default=None, help="Optional zip output path. Defaults to <output-dir>/material_pack.zip")
    parser.add_argument("--include-material-md", type=_parse_bool, default=True, help="Include modules/**/material.md. Default: true")
    parser.add_argument("--include-images", type=_parse_bool, default=True, help="Include image_items image files. Default: true")
    parser.add_argument("--include-table-json", type=_parse_bool, default=False, help="Include table_items/*.json. Default: false")
    parser.add_argument("--include-image-json", type=_parse_bool, default=False, help="Include image_items/*.json metadata. Default: false")
    parser.add_argument("--include-ordered-material-json", type=_parse_bool, default=False, help="Include ordered_material.json files. Default: false")
    parser.add_argument("--include-manifest", type=_parse_bool, default=False, help="Include pdf_toc_pipeline_manifest.json. Default: false")
    parser.add_argument("--include-parsed-tables", type=_parse_bool, default=False, help="Include parsed/tables.json. Default: false")
    parser.add_argument("--include-table-candidates", type=_parse_bool, default=False, help="Include parsed/table_regions/table_candidates.json. Default: false")
    args = parser.parse_args()

    result = export_lightweight_material_pack(
        args.output_dir,
        package_dir=args.package_dir,
        zip_path=args.zip_path,
        include_material_md=args.include_material_md,
        include_images=args.include_images,
        include_table_json=args.include_table_json,
        include_image_json=args.include_image_json,
        include_ordered_material_json=args.include_ordered_material_json,
        include_manifest=args.include_manifest,
        include_parsed_tables=args.include_parsed_tables,
        include_table_candidates=args.include_table_candidates,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
