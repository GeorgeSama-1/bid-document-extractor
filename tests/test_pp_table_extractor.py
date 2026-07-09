from bid_knowledge.parsing.pp_table_extractor import extract_pp_structure_tables, merge_pp_and_pdf_tables
from bid_knowledge.schemas.models import ParsedTable


def test_extract_pp_structure_tables_keeps_table_content_and_skips_duplicate_layout_box() -> None:
    results = [
        {
            "res": {
                "page_index": 0,
                "width": 1684,
                "height": 1191,
                "parsing_res_list": [
                    {
                        "block_label": "table",
                        "block_content": "<table><tr><td>姓名</td><td>岗位</td></tr></table>",
                        "block_bbox": [10, 100, 500, 260],
                        "block_id": 7,
                        "block_order": 3,
                    }
                ],
                "layout_det_res": {
                    "boxes": [
                        {
                            "label": "table",
                            "score": 0.91,
                            "coordinate": [11, 101, 501, 261],
                        }
                    ]
                },
            },
            "page_index": 0,
        }
    ]

    tables = extract_pp_structure_tables(results)

    assert len(tables) == 1
    assert tables[0].page_no == 1
    assert tables[0].bbox == [10.0, 100.0, 500.0, 260.0]
    assert tables[0].source_type == "pp_structure_table"
    assert tables[0].table_content == "<table><tr><td>姓名</td><td>岗位</td></tr></table>"
    assert tables[0].source_detail == "parsing_res_list"
    assert tables[0].page_width == 1684
    assert tables[0].page_height == 1191
    assert tables[0].table_model["row_count"] == 1
    assert tables[0].table_model["col_count"] == 2
    assert tables[0].table_model["cells"][0]["text"] == "姓名"


def test_extract_pp_structure_tables_preserves_html_rowspan_and_colspan_in_table_model() -> None:
    html = (
        "<table>"
        "<tr><th rowspan='2'>招标编号</th><th colspan='2'>包信息</th></tr>"
        "<tr><td>包号</td><td>包名称</td></tr>"
        "<tr><td>272608</td><td>包05</td><td>测控及在线监测系统包05</td></tr>"
        "</table>"
    )
    results = [
        {
            "res": {
                "page_index": 0,
                "parsing_res_list": [
                    {
                        "block_label": "table",
                        "block_content": html,
                        "block_bbox": [10, 100, 500, 260],
                    }
                ],
            },
            "page_index": 0,
        }
    ]

    table = extract_pp_structure_tables(results)[0]

    assert table.rows == [
        ["招标编号", "包信息", ""],
        ["", "包号", "包名称"],
        ["272608", "包05", "测控及在线监测系统包05"],
    ]
    assert table.table_html == html
    assert table.table_model["preserves_spans"] is True
    assert table.table_model["row_count"] == 3
    assert table.table_model["col_count"] == 3
    assert table.table_model["merged_cells"] == [
        {"row": 0, "col": 0, "rowspan": 2, "colspan": 1},
        {"row": 0, "col": 1, "rowspan": 1, "colspan": 2},
    ]
    assert table.table_model["cells"][0] == {
        "row": 0,
        "col": 0,
        "rowspan": 2,
        "colspan": 1,
        "text": "招标编号",
        "is_header": True,
        "bbox": None,
    }


def test_extract_pp_structure_tables_uses_layout_boxes_when_structured_content_is_missing() -> None:
    results = [
        {
            "res": {
                "page_index": 2,
                "width": 1684,
                "height": 1191,
                "parsing_res_list": [],
                "layout_det_res": {
                    "boxes": [
                        {
                            "label": "table",
                            "score": 0.76,
                            "coordinate": [20, 80, 520, 300],
                        }
                    ]
                },
            },
            "page_index": 2,
        }
    ]

    tables = extract_pp_structure_tables(results)

    assert len(tables) == 1
    assert tables[0].page_no == 3
    assert tables[0].bbox == [20.0, 80.0, 520.0, 300.0]
    assert tables[0].rows == []
    assert tables[0].source_type == "pp_structure_table"
    assert tables[0].source_detail == "layout_det_res"
    assert tables[0].page_width == 1684
    assert tables[0].page_height == 1191
    assert tables[0].table_model["source"] == "pp_structure_layout"
    assert tables[0].table_model["row_count"] == 0


def test_merge_pp_and_pdf_tables_keeps_pdf_tables_when_pp_misses_them() -> None:
    pp_table = ParsedTable(
        table_id="pp-1",
        page_no=1,
        rows=[["PP"]],
        bbox=[10, 100, 300, 200],
        source_type="pp_structure_table",
    )
    overlapping_pdf_table = ParsedTable(
        table_id="pdf-overlap",
        page_no=1,
        rows=[["PDF overlap"]],
        bbox=[12, 102, 302, 202],
        source_type="pdf_table",
    )
    missed_pdf_table = ParsedTable(
        table_id="pdf-missed",
        page_no=1,
        rows=[["PDF missed"]],
        bbox=[10, 250, 300, 360],
        source_type="pdf_table",
    )

    merged = merge_pp_and_pdf_tables([pp_table], [overlapping_pdf_table, missed_pdf_table])

    assert [table.table_id for table in merged] == ["pp-1", "pdf-missed"]


def test_merge_pp_and_pdf_tables_prefers_pdf_rows_over_empty_pp_region() -> None:
    pp_region = ParsedTable(
        table_id="pp-empty",
        page_no=1,
        rows=[],
        bbox=[10, 100, 300, 200],
        source_type="pp_structure_table",
    )
    pdf_table = ParsedTable(
        table_id="pdf-with-rows",
        page_no=1,
        rows=[["招标编号", "包号"], ["272608", "包05"]],
        bbox=[12, 102, 302, 202],
        source_type="pdf_table",
    )

    merged = merge_pp_and_pdf_tables([pp_region], [pdf_table])

    assert [table.table_id for table in merged] == ["pdf-with-rows"]
    assert merged[0].rows == [["招标编号", "包号"], ["272608", "包05"]]
