from bid_knowledge.parsing.pp_structure import _normalize_pp_structure_result


class _ArrayLike:
    def __init__(self, values):
        self._values = values

    def tolist(self):
        return list(self._values)


class _Font:
    def __str__(self):
        return "demo-font"


def test_normalize_pp_structure_result_drops_heavy_runtime_payloads() -> None:
    result = {
        "res": {
            "input_path": "/tmp/page22.png",
            "width": 1684,
            "height": 1191,
            "input_img": _ArrayLike(range(1000)),
            "parsing_res_list": [
                {
                    "block_label": "text",
                    "block_content": "企业营业执照副本",
                    "block_bbox": _ArrayLike([753, 140, 950, 163]),
                    "block_order": 2,
                    "font": _Font(),
                }
            ],
            "layout_det_res": {
                "input_img": _ArrayLike(range(1000)),
                "boxes": [
                    {
                        "cls_id": 1,
                        "label": "image",
                        "score": 0.56,
                        "coordinate": _ArrayLike([164.3, 183.9, 1502.2, 1023.9]),
                        "font": _Font(),
                    }
                ],
            },
            "overall_ocr_res": {
                "dt_polys": _ArrayLike(range(1000)),
                "rec_polys": _ArrayLike(range(1000)),
                "rec_texts": ["营业执照", "名称"],
                "rec_scores": _ArrayLike([0.98, 0.97]),
                "rec_boxes": _ArrayLike([[10, 20, 110, 40], [10, 50, 80, 70]]),
            },
        }
    }

    normalized = _normalize_pp_structure_result(result, page_index=21)

    payload = normalized["res"]
    assert normalized["page_index"] == 21
    assert "input_img" not in payload
    assert payload["parsing_res_list"] == [
        {
            "block_label": "text",
            "block_content": "企业营业执照副本",
            "block_bbox": [753.0, 140.0, 950.0, 163.0],
            "block_id": None,
            "block_order": 2,
        }
    ]
    assert payload["layout_det_res"]["boxes"] == [
        {
            "cls_id": 1,
            "label": "image",
            "score": 0.56,
            "coordinate": [164.3, 183.9, 1502.2, 1023.9],
        }
    ]
    assert payload["overall_ocr_res"] == {
        "rec_texts": ["营业执照", "名称"],
        "rec_scores": [0.98, 0.97],
        "rec_boxes": [[10.0, 20.0, 110.0, 40.0], [10.0, 50.0, 80.0, 70.0]],
        "text_type": "",
    }
