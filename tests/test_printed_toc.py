from __future__ import annotations

import json
from pathlib import Path

import fitz

from bid_knowledge.parsing.pdf_parser import parse_pdf
from bid_knowledge.parsing.printed_toc import infer_printed_toc
from bid_knowledge.schemas.models import PdfTextBlock


def block(page: int, number: int, text: str) -> PdfTextBlock:
    return PdfTextBlock(
        block_id=f"b-{page}-{number}",
        page_no=page,
        text=text,
        bbox=[10, number * 10, 500, number * 10 + 8],
        block_no=number,
    )


def test_infers_multilevel_toc_and_joins_wrapped_entries() -> None:
    blocks = [
        block(6, 1, "页眉 6"),
        block(6, 2, "目  录"),
        block(6, 3, "商务评审索引表........................ 2"),
        block(6, 4, "1、 商务偏差表........................ 8"),
        block(6, 5, "3、 补充文件.......................... 9"),
        block(6, 6, "3.1、 投标保证金....................... 9"),
        block(6, 7, "3.1.1、 汇款凭证....................... 9"),
        block(7, 1, "重复页眉 7"),
        block(7, 2, "3.8.2.12、 实体清单应对举措............ 1116"),
        block(7, 3, "3.9、 投标人自述的企业名称变更原因说明、市场监督管理部门出具的"),
        block(7, 4, "证明材料、有权机构出具的资格资质变更证明等相关证明材料...... 1119"),
        block(7, 5, "3.9.1、 企业名称变更原因说明............ 1119"),
        block(8, 1, "1、商务偏差表"),
        block(8, 2, "正文内容 2026"),
    ]

    toc = infer_printed_toc(blocks, page_count=1129)

    assert toc[:5] == [
        {"level": 1, "title": "商务评审索引表", "page": 2},
        {"level": 1, "title": "1、 商务偏差表", "page": 8},
        {"level": 1, "title": "3、 补充文件", "page": 9},
        {"level": 2, "title": "3.1、 投标保证金", "page": 9},
        {"level": 3, "title": "3.1.1、 汇款凭证", "page": 9},
    ]
    assert toc[-2:] == [
        {
            "level": 2,
            "title": "3.9、 投标人自述的企业名称变更原因说明、市场监督管理部门出具的证明材料、有权机构出具的资格资质变更证明等相关证明材料",
            "page": 1119,
        },
        {"level": 3, "title": "3.9.1、 企业名称变更原因说明", "page": 1119},
    ]


def test_requires_toc_marker_and_rejects_out_of_range_pages() -> None:
    without_marker = [block(1, 1, "1、普通正文................ 2")]
    assert infer_printed_toc(without_marker, page_count=5) == []

    invalid = [
        block(1, 1, "目录"),
        block(1, 2, "1、第一章................ 2"),
        block(1, 3, "2、第二章................ 999"),
    ]
    assert infer_printed_toc(invalid, page_count=5) == []


def test_parse_pdf_falls_back_to_printed_toc_and_persists_source(tmp_path: Path) -> None:
    pdf = tmp_path / "printed.pdf"
    document = fitz.open()
    toc_page = document.new_page()
    toc_page.insert_text((72, 72), "CONTENTS")
    toc_page.insert_text((72, 100), "1. First chapter................ 2")
    toc_page.insert_text((72, 120), "1.1 Details..................... 3")
    document.new_page().insert_text((72, 72), "1. First chapter")
    document.new_page().insert_text((72, 72), "1.1 Details")
    document.save(pdf)
    document.close()

    output = tmp_path / "parsed"
    parsed = parse_pdf(pdf, out_dir=output)

    assert parsed["document_meta"]["toc_source"] == "printed"
    assert parsed["toc"] == [
        {"level": 1, "title": "1. First chapter", "page": 2},
        {"level": 2, "title": "1.1 Details", "page": 3},
    ]
    assert json.loads((output / "toc.json").read_text()) == parsed["toc"]


def test_parse_pdf_keeps_embedded_toc_preferred(tmp_path: Path) -> None:
    pdf = tmp_path / "embedded.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "目录")
    page.insert_text((72, 100), "1、伪目录................ 1")
    document.set_toc([[1, "真实书签", 1]])
    document.save(pdf)
    document.close()

    parsed = parse_pdf(pdf)

    assert parsed["document_meta"]["toc_source"] == "embedded"
    assert parsed["toc"] == [{"level": 1, "title": "真实书签", "page": 1}]
