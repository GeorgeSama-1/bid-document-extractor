from bid_knowledge.parsing.text_block_merger import merge_multiline_heading_blocks
from bid_knowledge.schemas.models import PdfTextBlock


def test_merge_multiline_numbered_heading_with_unclosed_parenthesis() -> None:
    blocks = [
        PdfTextBlock(
            block_id="b1",
            page_no=593,
            text="（3.1）、2025 年国网青海超高压公司柴达木换流站运行证明（性能指标优异、服务",
            bbox=[72.0, 73.8, 514.7, 85.8],
            block_no=1,
        ),
        PdfTextBlock(
            block_id="b2",
            page_no=593,
            text="质量良好）",
            bbox=[72.0, 90.0, 150.0, 102.0],
            block_no=2,
        ),
        PdfTextBlock(block_id="b3", page_no=593, text="正文内容", bbox=[72.0, 120.0, 180.0, 132.0], block_no=3),
    ]

    merged = merge_multiline_heading_blocks(blocks)

    assert [block.block_id for block in merged] == ["b1", "b3"]
    assert merged[0].text == "（3.1）、2025 年国网青海超高压公司柴达木换流站运行证明（性能指标优异、服务质量良好）"
    assert merged[0].bbox == [72.0, 73.8, 514.7, 102.0]


def test_merge_multiline_heading_does_not_swallow_next_numbered_heading() -> None:
    blocks = [
        PdfTextBlock(block_id="b1", page_no=1, text="（1）、第一项（说明", bbox=[72.0, 80.0, 300.0, 92.0], block_no=1),
        PdfTextBlock(block_id="b2", page_no=1, text="（2）、第二项", bbox=[72.0, 98.0, 180.0, 110.0], block_no=2),
    ]

    merged = merge_multiline_heading_blocks(blocks)

    assert [block.text for block in merged] == ["（1）、第一项（说明", "（2）、第二项"]


def test_merge_multiline_heading_does_not_merge_table_header_fragments() -> None:
    blocks = [
        PdfTextBlock(block_id="b1", page_no=1, text="序", bbox=[80.0, 80.0, 90.0, 92.0], block_no=1),
        PdfTextBlock(block_id="b2", page_no=1, text="号", bbox=[80.0, 96.0, 90.0, 108.0], block_no=2),
    ]

    merged = merge_multiline_heading_blocks(blocks)

    assert [block.text for block in merged] == ["序", "号"]


def test_merge_visually_wrapped_long_heading_without_terminal_punctuation() -> None:
    blocks = [
        PdfTextBlock(
            block_id="title",
            page_no=635,
            text="（1.3.9）、2024 年国网四川省电力公司月城II500 千伏开关站新建工程所需智能变",
            bbox=[72.024, 73.812, 515.05, 85.812],
            block_no=3,
            font_size=12.0,
        ),
        PdfTextBlock(
            block_id="continuation",
            page_no=635,
            text="电站变压器油中溶解气体在线监测装置",
            bbox=[72.024, 97.232, 282.89, 109.232],
            block_no=4,
            font_size=12.0,
        ),
    ]

    merged = merge_multiline_heading_blocks(blocks)

    assert len(merged) == 1
    assert merged[0].text == (
        "（1.3.9）、2024 年国网四川省电力公司月城II500 千伏开关站新建工程所需"
        "智能变电站变压器油中溶解气体在线监测装置"
    )


def test_long_heading_does_not_merge_similarly_aligned_body_with_different_font() -> None:
    blocks = [
        PdfTextBlock(
            block_id="title",
            page_no=1,
            text="（1.3.9）、这是一个已经完整并且长度足够长的编号章节标题用于验证正文不会被吞并",
            bbox=[72.0, 74.0, 520.0, 86.0],
            block_no=1,
            font_size=14.0,
        ),
        PdfTextBlock(
            block_id="body",
            page_no=1,
            text="这是该章节的正文说明",
            bbox=[72.0, 98.0, 250.0, 108.0],
            block_no=2,
            font_size=10.0,
        ),
    ]

    merged = merge_multiline_heading_blocks(blocks)

    assert [block.block_id for block in merged] == ["title", "body"]
