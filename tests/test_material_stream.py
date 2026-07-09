from bid_knowledge.parsing.material_stream import (
    build_combined_page_material_stream,
    build_page_material_stream,
    build_pp_structure_page_material_items,
)
from bid_knowledge.schemas.models import ParsedTable, PdfTextBlock


def test_build_page_material_stream_merges_text_table_and_image_items() -> None:
    blocks = [
        PdfTextBlock(
            block_id="block-1",
            page_no=3,
            text="3.8.13.2、供应链保障措施",
            bbox=[0, 100, 100, 120],
            block_no=1,
        ),
        PdfTextBlock(
            block_id="block-2",
            page_no=3,
            text="供应链保障正文",
            bbox=[0, 130, 200, 180],
            block_no=2,
        ),
    ]
    tables = [
        ParsedTable(
            table_id="table-1",
            page_no=3,
            rows=[["措施", "说明"]],
            bbox=[10, 190, 200, 240],
        )
    ]
    images = [
        {
            "image_id": "img-1",
            "page_no": 3,
            "xref": 10,
            "width": 500,
            "height": 400,
            "rect": [10, 260, 150, 360],
            "ext": "png",
        }
    ]

    stream = build_page_material_stream(blocks=blocks, tables=tables, images=images)

    assert [item.item_type for item in stream] == ["text", "text", "table", "image"]
    assert [item.reading_order for item in stream] == [1, 2, 3, 4]
    assert stream[0].source_type == "pdf_text"
    assert stream[2].source_type == "pdf_table"
    assert stream[3].source_type == "pdf_embedded_image"


def test_build_pp_structure_page_material_items_normalizes_layout_blocks() -> None:
    pp_result = {
        "parsing_res_list": [
            {
                "block_label": "doc_title",
                "block_content": "3.4、企业营业执照（扫描件）",
                "block_bbox": [21, 90, 952, 117],
                "block_order": 1,
            },
            {
                "block_label": "text",
                "block_content": "企业营业执照副本",
                "block_bbox": [753, 140, 950, 163],
                "block_order": 2,
            },
        ],
        "layout_det_res": {
            "boxes": [
                {
                    "label": "image",
                    "coordinate": [164.3, 183.9, 1502.2, 1023.9],
                    "score": 0.56,
                }
            ]
        },
    }

    items = build_pp_structure_page_material_items(pp_result, page_no=22)

    assert [item.item_type for item in items] == ["text", "text", "image"]
    assert items[0].source_type == "pp_structure_text"
    assert items[2].source_type == "pp_structure_image_region"
    assert items[2].payload["layout_label"] == "image"


def test_build_pp_structure_page_material_items_falls_back_to_spatial_ocr_texts() -> None:
    pp_result = {
        "parsing_res_list": [],
        "layout_det_res": {
            "boxes": [
                {
                    "label": "paragraph_title",
                    "coordinate": [138.8, 144.6, 929.2, 214.1],
                    "score": 0.36,
                },
                {
                    "label": "text",
                    "coordinate": [839.2, 973.0, 1087.7, 1001.8],
                    "score": 0.55,
                },
                {
                    "label": "table",
                    "coordinate": [120.0, 500.0, 900.0, 700.0],
                    "score": 0.81,
                },
                {
                    "label": "image",
                    "coordinate": [332.6, 241.2, 1351.2, 994.5],
                    "score": 0.53,
                },
                {
                    "label": "header",
                    "coordinate": [10, 10, 100, 30],
                    "score": 0.9,
                },
            ]
        },
        "overall_ocr_res": {
            "rec_texts": [
                "NI.INF 理工能科",
                "国网甘肃省电力公司【测控及在线监测系统】包05、包06、包07、包08—一商务投标文件",
                "3.4、企业营业执照（扫描件）",
                "统一社会信用代码",
                "913302007251641924",
            ],
            "rec_scores": [0.99, 0.98, 0.95],
            "rec_boxes": [
                [69, 3, 180, 28],
                [824, 23, 1666, 48],
                [140, 150, 900, 180],
                [850, 980, 1050, 995],
                [850, 996, 1080, 1010],
            ],
            "text_type": "general",
        },
    }

    items = build_pp_structure_page_material_items(pp_result, page_no=22)

    assert [item.item_type for item in items] == ["text", "image", "table", "text"]
    assert items[0].source_type == "pp_structure_text_region"
    assert items[0].text == "3.4、企业营业执照（扫描件）"
    assert items[0].payload["layout_label"] == "paragraph_title"
    assert items[0].payload["ocr_texts"] == ["3.4、企业营业执照（扫描件）"]
    assert items[1].source_type == "pp_structure_image_region"
    assert items[2].source_type == "pp_structure_table_region"
    assert items[3].source_type == "pp_structure_text_region"
    assert items[3].text == "统一社会信用代码\n913302007251641924"
    assert all("商务投标文件" not in item.text for item in items)


def test_build_pp_structure_page_material_items_attaches_spatial_ocr_to_image_regions() -> None:
    pp_result = {
        "height": 1684,
        "width": 1191,
        "parsing_res_list": [],
        "layout_det_res": {
            "boxes": [
                {
                    "label": "image",
                    "coordinate": [140, 150, 260, 178],
                    "score": 0.72,
                }
            ]
        },
        "overall_ocr_res": {
            "rec_texts": ["3.6.1.1、", "表格正文"],
            "rec_scores": [0.98, 0.93],
            "rec_boxes": [
                [145, 154, 245, 174],
                [140, 230, 520, 260],
            ],
            "text_type": "general",
        },
    }

    items = build_pp_structure_page_material_items(pp_result, page_no=1)

    assert len(items) == 1
    assert items[0].item_type == "image"
    assert items[0].payload["ocr_texts"] == ["3.6.1.1、"]
    assert items[0].payload["ocr_scores"] == [0.98]


def test_build_pp_structure_page_material_items_skips_unspatial_fallback_text() -> None:
    pp_result = {
        "parsing_res_list": [],
        "layout_det_res": {
            "boxes": [
                {"label": "text", "coordinate": [100, 100, 400, 160], "score": 0.8},
                {"label": "image", "coordinate": [100, 180, 500, 500], "score": 0.9},
            ]
        },
        "overall_ocr_res": {
            "rec_texts": ["NI.INF 理工能科", "国网甘肃省电力公司 商务投标文件", "法定代表人身份证明"],
            "rec_scores": [0.98, 0.98, 0.98],
            "text_type": "general",
        },
    }

    items = build_pp_structure_page_material_items(pp_result, page_no=1)

    assert [item.item_type for item in items] == ["image"]


def test_build_combined_page_material_stream_includes_pp_structure_items() -> None:
    blocks = [
        PdfTextBlock(
            block_id="block-1",
            page_no=22,
            text="3.4、企业营业执照（扫描件）",
            bbox=[0, 100, 100, 120],
            block_no=1,
        )
    ]
    pp_results = [
        {
            "res": {
                "parsing_res_list": [
                    {
                        "block_label": "text",
                        "block_content": "企业营业执照副本",
                        "block_bbox": [753, 140, 950, 163],
                        "block_order": 1,
                    }
                ],
                "layout_det_res": {
                    "boxes": [
                        {"label": "image", "coordinate": [164.3, 183.9, 1502.2, 1023.9], "score": 0.56}
                    ]
                },
            },
            "page_index": 21,
        }
    ]

    items = build_combined_page_material_stream(blocks=blocks, tables=[], images=[], pp_structure_results=pp_results)

    assert any(item.source_type == "pdf_text" for item in items)
    assert any(item.source_type == "pp_structure_text" for item in items)
    assert any(item.source_type == "pp_structure_image_region" for item in items)
