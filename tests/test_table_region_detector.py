from pathlib import Path
import sys
import types

from bid_knowledge.parsing.table_region_detector import (
    CandidateTableGroup,
    CandidateTableRegion,
    _filter_regions_overlapping_images,
    _line_regions_from_segments,
    _merge_candidate_regions,
    _normalize_regions_to_pdf_coords,
    _rect_has_table_text_evidence,
    group_candidate_table_regions,
    groups_to_parsed_tables,
    regions_to_parsed_tables,
)
from bid_knowledge.schemas.models import ParsedTable


def test_merge_candidate_regions_deduplicates_overlapping_regions_and_combines_evidence() -> None:
    regions = [
        CandidateTableRegion(
            region_id="pdf-1",
            page_no=1,
            bbox=[10, 100, 300, 220],
            detectors=["pdfplumber"],
            confidence=0.7,
            evidence={"source": "pdfplumber"},
        ),
        CandidateTableRegion(
            region_id="pp-1",
            page_no=1,
            bbox=[12, 98, 302, 224],
            detectors=["pp_structure"],
            confidence=0.6,
            evidence={"pp_score": 0.91},
        ),
        CandidateTableRegion(
            region_id="lines-1",
            page_no=1,
            bbox=[400, 100, 550, 180],
            detectors=["pymupdf_lines"],
            confidence=0.8,
        ),
    ]

    merged = _merge_candidate_regions(regions)

    assert len(merged) == 2
    first = merged[0]
    assert first.detectors == ["pdfplumber", "pp_structure"]
    assert first.bbox == [10.0, 98.0, 302.0, 224.0]
    assert first.confidence == 0.75
    assert first.evidence["source"] == "pdfplumber"
    assert first.evidence["pp_score"] == 0.91


def test_regions_to_parsed_tables_reuses_source_rows_and_records_debug_crop_path(tmp_path: Path) -> None:
    region = CandidateTableRegion(
        region_id="region-1",
        page_no=3,
        bbox=[10, 100, 300, 220],
        expanded_bbox=[5, 90, 310, 235],
        detectors=["pdfplumber", "pymupdf_lines"],
        confidence=0.9,
        crop_image_path=str(tmp_path / "debug" / "region-1.png"),
        source_table_ids=["pdf-table-1"],
    )
    source_table = ParsedTable(
        table_id="pdf-table-1",
        page_no=3,
        rows=[["招标编号", "包号"], ["272608", "包05"]],
        bbox=[12, 102, 298, 218],
        table_model={"source": "pdfplumber"},
    )

    tables = regions_to_parsed_tables([region], [source_table])

    assert len(tables) == 1
    assert tables[0].table_id == "region-1"
    assert tables[0].bbox == [5.0, 90.0, 310.0, 235.0]
    assert tables[0].rows == [["招标编号", "包号"], ["272608", "包05"]]
    assert tables[0].candidate_detectors == ["pdfplumber", "pymupdf_lines"]
    assert tables[0].table_image_path.endswith("region-1.png")
    assert tables[0].table_region_confidence == 0.9


def test_line_regions_from_segments_keeps_separate_tables_on_same_page() -> None:
    horizontal = [
        (10, 100, 200, 100),
        (10, 140, 200, 140),
        (10, 180, 200, 180),
        (300, 100, 500, 100),
        (300, 140, 500, 140),
        (300, 180, 500, 180),
    ]
    vertical = [
        (10, 100, 10, 180),
        (100, 100, 100, 180),
        (200, 100, 200, 180),
        (300, 100, 300, 180),
        (400, 100, 400, 180),
        (500, 100, 500, 180),
    ]

    regions = _line_regions_from_segments(1, horizontal, vertical)

    assert len(regions) == 2
    assert regions[0].bbox == [10.0, 100.0, 200.0, 180.0]
    assert regions[1].bbox == [300.0, 100.0, 500.0, 180.0]


def test_normalize_regions_to_pdf_coords_converts_pp_pixel_bbox_before_merge() -> None:
    regions = [
        CandidateTableRegion(
            region_id="pp-1",
            page_no=1,
            bbox=[168.4, 119.1, 842.0, 595.5],
            detectors=["pp_structure"],
            confidence=0.7,
            page_width=1684,
            page_height=1191,
        )
    ]

    normalized = _normalize_regions_to_pdf_coords(regions, {1: (842.0, 595.5)})

    assert normalized[0].bbox == [84.2, 59.55, 421.0, 297.75]
    assert normalized[0].page_width is None
    assert normalized[0].page_height is None
    assert normalized[0].evidence["source_page_width"] == 1684


def test_group_candidate_table_regions_merges_adjacent_same_page_fragments() -> None:
    regions = [
        CandidateTableRegion(region_id="r1", page_no=1, bbox=[10, 100, 300, 180], detectors=["pymupdf_lines"], confidence=0.8),
        CandidateTableRegion(region_id="r2", page_no=1, bbox=[12, 190, 302, 260], detectors=["pymupdf_lines"], confidence=0.75),
        CandidateTableRegion(region_id="r3", page_no=1, bbox=[420, 100, 560, 180], detectors=["pymupdf_lines"], confidence=0.8),
    ]

    groups = group_candidate_table_regions(regions)

    assert len(groups) == 2
    assert groups[0].region_ids == ["r1", "r2"]
    assert groups[0].bbox_by_page == {1: [10.0, 100.0, 302.0, 260.0]}
    assert groups[0].is_cross_page is False
    assert groups[1].region_ids == ["r3"]


def test_group_candidate_table_regions_links_likely_cross_page_table() -> None:
    regions = [
        CandidateTableRegion(region_id="page1", page_no=1, bbox=[50, 500, 550, 790], detectors=["pymupdf_lines"], confidence=0.8),
        CandidateTableRegion(region_id="page2", page_no=2, bbox=[52, 40, 548, 260], detectors=["pymupdf_lines"], confidence=0.8),
    ]

    groups = group_candidate_table_regions(regions)

    assert len(groups) == 1
    assert groups[0].region_ids == ["page1", "page2"]
    assert groups[0].start_page == 1
    assert groups[0].end_page == 2
    assert groups[0].is_cross_page is True
    assert groups[0].bbox_by_page[1] == [50.0, 500.0, 550.0, 790.0]
    assert groups[0].bbox_by_page[2] == [52.0, 40.0, 548.0, 260.0]


def test_groups_to_parsed_tables_keeps_cross_page_parts_under_one_group() -> None:
    group = CandidateTableGroup(
        group_id="group-1",
        start_page=1,
        end_page=2,
        region_ids=["page1", "page2"],
        bbox_by_page={1: [50, 500, 550, 790], 2: [52, 40, 548, 260]},
        regions=[
            CandidateTableRegion(region_id="page1", page_no=1, bbox=[50, 500, 550, 790], crop_image_path="page1.png"),
            CandidateTableRegion(region_id="page2", page_no=2, bbox=[52, 40, 548, 260], crop_image_path="page2.png"),
        ],
        is_cross_page=True,
    )

    tables = groups_to_parsed_tables([group], [])

    assert [table.table_id for table in tables] == ["group-1-p1", "group-1-p2"]
    assert all(table.table_group_id == "group-1" for table in tables)
    assert tables[0].table_group_part_index == 1
    assert tables[1].table_group_part_index == 2
    assert tables[0].table_image_path == "page1.png"


def test_groups_to_parsed_tables_uses_pdfplumber_crop_for_bbox_only_region(tmp_path: Path, monkeypatch) -> None:
    pdf_path = tmp_path / "demo.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    region = CandidateTableRegion(
        region_id="pp-table",
        page_no=1,
        bbox=[10, 100, 140, 130],
        expanded_bbox=[8, 98, 142, 132],
        detectors=["pp_structure"],
        confidence=0.72,
    )
    group = CandidateTableGroup(
        group_id="table-group-1",
        start_page=1,
        end_page=1,
        regions=[region],
        detectors=["pp_structure"],
    )

    class FakeRow:
        def __init__(self, cells):
            self.cells = cells

    class FakeFoundTable:
        bbox = (10, 100, 140, 130)
        rows = [
            FakeRow([(10, 100, 70, 110), (70, 100, 140, 110)]),
            FakeRow([(10, 110, 20, 120), (20, 110, 30, 120), (30, 110, 40, 120), (40, 110, 50, 120), (50, 110, 60, 120), (60, 110, 70, 120), (70, 110, 80, 120), (80, 110, 90, 120), (90, 110, 100, 120), (100, 110, 110, 120), (110, 110, 120, 120), (120, 110, 130, 120), (130, 110, 140, 120)]),
            FakeRow([(10, 120, 20, 130), (20, 120, 30, 130), (30, 120, 40, 130), (40, 120, 50, 130), (50, 120, 60, 130), (60, 120, 70, 130), (70, 120, 80, 130), (80, 120, 90, 130), (90, 120, 100, 130), (100, 120, 110, 130), (110, 120, 120, 130), (120, 120, 130, 130), (130, 120, 140, 130)]),
        ]

        def extract(self):
            return [
                ["本企业人员基本信息", "国家电网公司系统人员基本信息"],
                ["人员姓名", "性别", "身份证号", "职务", "任职时间", "与国网公司系统人员关系", "人员姓名", "性别", "身份证号", "（曾）任职单位名称", "职务", "任职状态（在职/非在职）", "离职/退休时间"],
                ["无", "—", "—", "—", "—", "—", "—", "—", "—", "—", "—", "—", "—"],
            ]

    class FakePage:
        width = 595
        height = 842

        def __init__(self):
            self.crop_bbox = None

        def crop(self, bbox):
            self.crop_bbox = bbox
            return self

        def find_tables(self):
            return [FakeFoundTable()]

    fake_page = FakePage()

    class FakePdf:
        pages = [fake_page]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setitem(sys.modules, "pdfplumber", types.SimpleNamespace(open=lambda _path: FakePdf()))

    tables = groups_to_parsed_tables([group], [], pdf_path=pdf_path)

    assert fake_page.crop_bbox == (8.0, 98.0, 142.0, 132.0)
    assert tables[0].rows[2][12] == "—"
    assert tables[0].source_type == "pdf_table"
    assert tables[0].candidate_detectors == ["pp_structure"]
    assert tables[0].table_model["source"] == "pdfplumber_geometry"
    assert tables[0].table_model["col_count"] == 13
    assert tables[0].table_model["cells"][1]["colspan"] == 7


def test_filter_regions_overlapping_images_drops_image_backed_tables() -> None:
    regions = [
        CandidateTableRegion(region_id="image-table", page_no=1, bbox=[10, 100, 300, 260], detectors=["pp_structure"], confidence=0.8),
        CandidateTableRegion(region_id="native-table", page_no=1, bbox=[10, 300, 300, 460], detectors=["pymupdf_lines"], confidence=0.8),
    ]
    images = [
        {"image_id": "img-1", "page_no": 1, "rect": [8, 95, 302, 265]},
    ]

    filtered = _filter_regions_overlapping_images(regions, images)

    assert [region.region_id for region in filtered] == ["native-table"]
    assert regions[0].evidence["filtered_reason"] == "overlaps_image"


def test_filter_regions_overlapping_pp_structure_image_boxes() -> None:
    regions = [
        CandidateTableRegion(region_id="image-table", page_no=2, bbox=[20, 120, 420, 360], detectors=["pymupdf_lines"], confidence=0.8),
    ]
    pp_results = [
        {
            "res": {
                "page_index": 1,
                "width": 842,
                "height": 595,
                "layout_det_res": {
                    "boxes": [
                        {"label": "image", "coordinate": [20, 120, 420, 360], "score": 0.9},
                    ]
                },
            },
            "page_index": 1,
        }
    ]

    filtered = _filter_regions_overlapping_images(regions, [], pp_results)

    assert filtered == []
    assert regions[0].evidence["filtered_image_source"] == "pp_structure"


def test_filter_regions_overlapping_images_keeps_tables_with_embedded_images() -> None:
    regions = [
        CandidateTableRegion(region_id="table-with-image-cell", page_no=1, bbox=[20, 100, 500, 360], detectors=["pymupdf_lines"], confidence=0.8),
    ]
    images = [
        {"image_id": "img-in-cell", "page_no": 1, "rect": [260, 220, 470, 330]},
    ]

    filtered = _filter_regions_overlapping_images(regions, images)

    assert [region.region_id for region in filtered] == ["table-with-image-cell"]
    assert "filtered_reason" not in regions[0].evidence


def test_filter_regions_overlapping_images_drops_table_when_center_falls_inside_image() -> None:
    regions = [
        CandidateTableRegion(region_id="image-backed-table", page_no=1, bbox=[0, 0, 100, 100], detectors=["pymupdf_lines"], confidence=0.8),
    ]
    images = [
        {"image_id": "img-1", "page_no": 1, "rect": [40, 40, 200, 200]},
    ]

    filtered = _filter_regions_overlapping_images(regions, images)

    assert filtered == []
    assert regions[0].evidence["filtered_reason"] == "overlaps_image"
    assert regions[0].evidence["filtered_image_id"] == "img-1"


def test_filter_regions_overlapping_images_drops_table_with_meaningful_partial_overlap() -> None:
    regions = [
        CandidateTableRegion(region_id="partial-image-table", page_no=1, bbox=[0, 0, 100, 100], detectors=["pp_structure"], confidence=0.8),
    ]
    images = [
        {"image_id": "img-1", "page_no": 1, "rect": [50, 0, 150, 100]},
    ]

    filtered = _filter_regions_overlapping_images(regions, images)

    assert filtered == []
    assert regions[0].evidence["filtered_reason"] == "overlaps_image"


def test_rect_has_table_text_evidence_requires_enough_text_blocks() -> None:
    class FakePage:
        def __init__(self, blocks):
            self.blocks = blocks

        def get_text(self, _kind, clip=None):
            return self.blocks

    valid_page = FakePage(
        [
            (10, 10, 50, 20, "招标编号", 0, 0),
            (60, 10, 100, 20, "投标保证金金额", 0, 0),
            (10, 30, 50, 40, "272608", 0, 0),
            (60, 30, 100, 40, "测控及在线监测系统包05", 0, 0),
        ]
    )
    weak_page = FakePage([(10, 10, 50, 20, "表头", 0, 0)])

    assert _rect_has_table_text_evidence(valid_page, object()) is True
    assert _rect_has_table_text_evidence(weak_page, object()) is False
