from bid_knowledge.parsing.toc_leaf_builder import (
    build_toc_leaf_candidates,
    toc_leaf_section_paths,
    top_level_modules_from_toc_candidates,
)
from bid_knowledge.schemas.models import PdfTextBlock


def test_build_toc_leaf_candidates_uses_only_lowest_level_sections() -> None:
    candidates = build_toc_leaf_candidates(
        toc=[
            {"level": 1, "title": "补充文件", "page": 10},
            {"level": 2, "title": "投标保证金", "page": 11},
            {"level": 3, "title": "汇款凭证", "page": 12},
            {"level": 1, "title": "法定代表人授权委托书", "page": 14},
        ],
        page_count=20,
        path_root="PDF",
    )

    paths = toc_leaf_section_paths(candidates)

    assert paths == [
        "PDF / 补充文件 / 投标保证金 / 汇款凭证",
        "PDF / 法定代表人授权委托书",
    ]
    assert candidates[0].title == "汇款凭证"
    assert candidates[0].source_container_title == "投标保证金"
    assert candidates[0].source_page == 12
    assert candidates[0].source_page_end == 13
    assert candidates[1].title == "法定代表人授权委托书"
    assert candidates[1].source_page == 14
    assert candidates[1].source_page_end == 20


def test_top_level_modules_from_toc_candidates_uses_first_toc_level_below_root() -> None:
    candidates = build_toc_leaf_candidates(
        toc=[
            {"level": 1, "title": "补充文件", "page": 10},
            {"level": 2, "title": "投标保证金", "page": 11},
            {"level": 1, "title": "商务文件正文", "page": 20},
        ],
        page_count=30,
        path_root="PDF",
    )

    assert top_level_modules_from_toc_candidates(candidates) == ["补充文件", "商务文件正文"]


def test_build_toc_leaf_candidates_records_same_page_y_boundaries() -> None:
    candidates = build_toc_leaf_candidates(
        toc=[
            {"level": 1, "title": "3、 补充文件", "page": 1},
            {"level": 2, "title": "3.1、 投标保证金", "page": 1},
            {"level": 3, "title": "3.1.1、 汇款凭证", "page": 1},
            {"level": 3, "title": "3.1.2、 投标保证金银行保函（无、本项目采用电汇）", "page": 1},
            {"level": 3, "title": "3.1.3、 银行基本账户证明扫描件", "page": 1},
        ],
        page_count=2,
        path_root="PDF",
        blocks=[
            PdfTextBlock(block_id="h311", page_no=1, text="3.1.1、汇款凭证", bbox=[0, 100, 300, 120], block_no=1),
            PdfTextBlock(block_id="h312", page_no=1, text="3.1.2、投标保证金银行保函（无、本项目采用电汇）", bbox=[0, 300, 500, 320], block_no=2),
            PdfTextBlock(block_id="h313", page_no=1, text="3.1.3、银行基本账户证明扫描件", bbox=[0, 500, 400, 520], block_no=3),
        ],
    )

    assert candidates[0].title == "3.1.1、 汇款凭证"
    assert candidates[0].source_page == 1
    assert candidates[0].source_page_end == 1
    assert candidates[0].material_evidence["start_y"] == 100.0
    assert candidates[0].material_evidence["end_y"] == 300.0
    assert candidates[1].material_evidence["start_block_id"] == "h312"
    assert candidates[1].material_evidence["end_block_id"] == "h313"


def test_build_toc_leaf_candidates_stops_section_before_later_toc_pages() -> None:
    candidates = build_toc_leaf_candidates(
        toc=[
            {"level": 1, "title": "商务评审索引表", "page": 2},
            {"level": 1, "title": "商务偏差表", "page": 10},
        ],
        page_count=20,
        path_root="商务文件",
        blocks=[
            PdfTextBlock(block_id="idx-title", page_no=2, text="商务评审索引表", bbox=[0, 70, 200, 90], block_no=1),
            PdfTextBlock(block_id="toc-title", page_no=8, text="目  录", bbox=[0, 70, 200, 90], block_no=2),
            PdfTextBlock(block_id="toc-line", page_no=8, text="商务评审索引表.................................................................... 2", bbox=[0, 95, 500, 110], block_no=3),
        ],
    )

    assert candidates[0].section_path == "商务文件 / 商务评审索引表"
    assert candidates[0].source_page == 2
    assert candidates[0].source_page_end == 7
