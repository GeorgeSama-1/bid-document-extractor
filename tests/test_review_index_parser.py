from bid_knowledge.parsing.review_index_parser import (
    align_business_review_index_entries,
    build_business_review_index_tree,
    build_folder_ranges,
    parse_business_review_index,
)
from bid_knowledge.schemas.models import ParsedTable, PdfTextBlock


def test_parse_business_review_index_extracts_section_entries() -> None:
    blocks = [
        PdfTextBlock(block_id="b1", page_no=2, text="商务评审索引表", bbox=[0, 0, 10, 10], block_no=1),
    ]
    tables = [
        ParsedTable(
            table_id="t1",
            page_no=2,
            rows=[
                ["项目", "评审要素", "评审细则", "", ""],
                ["一、履约能力评价", "经营状况", "", "详见第574页：3.8.1.1、企业履约能力强", ""],
                ["", "", "", "第627页：3.8.1.2、企业整体经营状况优良", ""],
                ["", "售后服务", "", "详见第641页：3.8.2、售后服务", ""],
            ],
        )
    ]

    entries = parse_business_review_index(blocks, tables)

    assert len(entries) == 3
    assert entries[0]["section_path"] == "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 经营状况"
    assert entries[0]["title"] == "3.8.1.1、企业履约能力强"
    assert entries[0]["page_start"] == 574
    assert entries[1]["title"] == "3.8.1.2、企业整体经营状况优良"
    assert entries[2]["section_path"] == "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 售后服务"


def test_parse_business_review_index_strips_score_ranges_from_labels() -> None:
    blocks = [
        PdfTextBlock(block_id="b1", page_no=2, text="商务评审索引表", bbox=[0, 0, 10, 10], block_no=1),
    ]
    tables = [
        ParsedTable(
            table_id="t1",
            page_no=2,
            rows=[
                ["项目", "评审要素", "评审细则", "", ""],
                ["一、履约\n能力评价\n（17-27", "经营状况\n（1-5）", "", "详见第574页：3.8.1.1、企业履约能力强", ""],
                ["", "", "", "第627页：3.8.1.2、企业整体经营状况优良", ""],
                ["", "售后服务\n（4-6）", "", "详见第641页：3.8.2、售后服务", ""],
                ["", "（-20-0）", "", "", ""],
            ],
        )
    ]

    entries = parse_business_review_index(blocks, tables)

    assert len(entries) == 3
    assert entries[0]["section_path"] == "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 经营状况"
    assert entries[2]["section_path"] == "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 售后服务"


def test_parse_business_review_index_recovers_element_when_merged_cell_splits_across_pages() -> None:
    blocks = [
        PdfTextBlock(block_id="b1", page_no=2, text="商务评审索引表", bbox=[0, 0, 10, 10], block_no=1),
    ]
    tables = [
        ParsedTable(
            table_id="t1",
            page_no=6,
            rows=[
                ["项目", "评审要素", "评审细则", "", ""],
                ["四、诚信评价", "公共信用信息报告、企业信用信息公示报告", "", "第170页：3.6.2、查询报告及截图", ""],
                ["", "", "投标截止日近一年内，经查证核实，投标人存在可能影响评标工作公正性行为【扣20分】。", "", ""],
                ["", "（-20-0）", "", "", ""],
                ["", "", "", "详见第253页：3.6.2.3、“中国裁判文书网”网站查询", ""],
            ],
        )
    ]

    entries = parse_business_review_index(blocks, tables)

    assert entries[0]["element_label"] == "公共信用信息报告、企业信用信息公示报告"
    assert entries[1]["element_label"] == "影响评标工作公正性行为的凭证"


def test_build_folder_ranges_uses_numeric_entries_only() -> None:
    entries = [
        {
            "section_path": "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 经营状况",
            "title": "3.8.1.1、企业履约能力强",
            "page_start": 574,
        },
        {
            "section_path": "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 经营状况",
            "title": "（1）、绩效评价结果查询",
            "page_start": 574,
        },
        {
            "section_path": "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 经营状况",
            "title": "3.8.1.2、企业整体经营状况优良",
            "page_start": 627,
        },
        {
            "section_path": "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 售后服务",
            "title": "3.8.2、售后服务",
            "page_start": 641,
        },
    ]

    ranges = build_folder_ranges(entries)

    assert len(ranges) == 3
    assert ranges[0]["folder_title"] == "3.8.1.1、企业履约能力强"
    assert ranges[0]["page_start"] == 574
    assert ranges[0]["page_end"] == 626
    assert ranges[1]["folder_title"] == "3.8.1.2、企业整体经营状况优良"
    assert ranges[1]["page_start"] == 627
    assert ranges[1]["page_end"] == 640


def test_build_folder_ranges_orders_parent_before_same_page_children() -> None:
    entries = [
        {
            "section_path": "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 售后服务",
            "title": "3.8.2.1、售后服务响应时间承诺",
            "page_start": 641,
        },
        {
            "section_path": "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 售后服务",
            "title": "3.8.2、售后服务",
            "page_start": 641,
        },
        {
            "section_path": "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 售后服务",
            "title": "3.8.2.2、售后服务团队",
            "page_start": 643,
        },
    ]

    ranges = build_folder_ranges(entries)

    assert [item["folder_title"] for item in ranges] == [
        "3.8.2、售后服务",
        "3.8.2.1、售后服务响应时间承诺",
        "3.8.2.2、售后服务团队",
    ]
    assert ranges[0]["page_start"] == 641
    assert ranges[0]["page_end"] == 641
    assert ranges[1]["page_start"] == 641
    assert ranges[1]["page_end"] == 642


def test_align_business_review_index_entries_uses_excel_paths_when_project_cells_are_blank() -> None:
    entries = [
        {
            "section_path": "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 研发团队规模",
            "project_label": "一、履约能力评价",
            "element_label": "研发团队规模",
            "title": "3.8.10、研发团队规模",
            "page_start": 769,
        },
        {
            "section_path": "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 研发团队规模",
            "project_label": "一、履约能力评价",
            "element_label": "研发团队规模",
            "title": "3.8.10.2、职称证书37人",
            "page_start": 771,
        },
    ]
    section_paths = [
        "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 研发团队规模",
    ]

    aligned = align_business_review_index_entries(entries, section_paths)

    assert {entry["project_label"] for entry in aligned} == {"二、高质量发展评价"}
    assert {entry["section_path"] for entry in aligned} == set(section_paths)
    assert aligned[1]["original_section_path"] == "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 研发团队规模"


def test_build_business_review_index_tree_groups_aligned_entries_by_excel_project() -> None:
    entries = [
        {
            "section_path": "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 研发团队规模",
            "project_label": "二、高质量发展评价",
            "element_label": "研发团队规模",
            "title": "3.8.10、研发团队规模",
            "page_start": 769,
        },
        {
            "section_path": "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 三、投标响应 / 报价质量",
            "project_label": "三、投标响应",
            "element_label": "报价质量",
            "title": "3.8.19、投标响应-报价质量",
            "page_start": 818,
        },
    ]

    tree = build_business_review_index_tree(entries)

    assert tree["project_count"] == 2
    assert tree["tree"]["二、高质量发展评价"]["研发团队规模"][0]["page_start"] == 769
    assert tree["tree"]["三、投标响应"]["报价质量"][0]["title"] == "3.8.19、投标响应-报价质量"


def test_align_business_review_index_entries_keeps_children_under_matched_parent_title() -> None:
    entries = [
        {
            "section_path": "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 应急保供",
            "project_label": "一、履约能力评价",
            "element_label": "应急保供",
            "title": "3.8.4、绿色发展规划",
            "page_start": 706,
        },
        {
            "section_path": "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 应急保供",
            "project_label": "一、履约能力评价",
            "element_label": "应急保供",
            "title": "3.8.4.1、绿色发展顶层规划",
            "page_start": 706,
        },
    ]
    section_paths = [
        "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 应急保供",
        "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 绿色发展规划",
    ]

    aligned = align_business_review_index_entries(entries, section_paths)

    assert [entry["section_path"] for entry in aligned] == [
        "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 绿色发展规划",
        "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 绿色发展规划",
    ]


def test_align_business_review_index_entries_handles_multi_digit_numbered_children() -> None:
    entries = [
        {
            "section_path": "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 实体清单应对举措",
            "project_label": "二、高质量发展评价",
            "element_label": "实体清单应对举措",
            "title": "3.8.19、投标响应-报价质量",
            "page_start": 818,
        },
        {
            "section_path": "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 实体清单应对举措",
            "project_label": "二、高质量发展评价",
            "element_label": "实体清单应对举措",
            "title": "3.8.19.2、特定关系公司投标合法性承诺函",
            "page_start": 819,
        },
    ]
    section_paths = [
        "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 实体清单应对举措",
        "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 三、投标响应 / 报价质量",
    ]

    aligned = align_business_review_index_entries(entries, section_paths)

    assert [entry["section_path"] for entry in aligned] == [
        "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 三、投标响应 / 报价质量",
        "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 三、投标响应 / 报价质量",
    ]
