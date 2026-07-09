from bid_knowledge.parsing.layout_mask import build_layout_masks


def test_build_layout_masks_extracts_header_footer_and_number_regions() -> None:
    masks = build_layout_masks(
        [
            {
                "page_index": 21,
                "res": {
                    "width": 1684,
                    "height": 1191,
                    "parsing_res_list": [
                        {"block_label": "header", "block_bbox": [10, 0, 1600, 55]},
                        {"block_label": "text", "block_bbox": [10, 200, 1600, 260]},
                    ],
                    "layout_det_res": {
                        "boxes": [
                            {"label": "number", "coordinate": [830, 1130, 860, 1160]},
                            {"label": "image", "coordinate": [100, 200, 500, 500]},
                        ]
                    },
                },
            }
        ]
    )

    assert masks == [
        {"page_no": 22, "label": "header", "bbox": [10.0, 0.0, 1600.0, 55.0], "page_width": 1684, "page_height": 1191},
        {"page_no": 22, "label": "number", "bbox": [830.0, 1130.0, 860.0, 1160.0], "page_width": 1684, "page_height": 1191},
    ]
