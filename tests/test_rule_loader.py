from bid_knowledge.config.rule_loader import build_section_path, normalize_bool, rows_to_rules


def test_normalize_bool_supports_chinese_yes_no() -> None:
    assert normalize_bool("是") is True
    assert normalize_bool("否") is False
    assert normalize_bool("Y") is True
    assert normalize_bool("0") is False


def test_build_section_path_joins_non_empty_parts() -> None:
    path = build_section_path("商务文件", "投标人资格", "投标人基本情况表", "", None)
    assert path == "商务文件 / 投标人资格 / 投标人基本情况表"


def test_rows_to_rules_tolerates_missing_optional_columns() -> None:
    rows = [
        {
            "文件类型": "商务文件",
            "组成模块": "投标人资格",
            "子内容1": "投标人基本情况表",
            "是否从往期投标文件中摘取": "是",
        }
    ]

    rules, report = rows_to_rules(rows)

    assert len(rules) == 1
    assert rules[0].from_history_bid is True
    assert rules[0].section_path == "商务文件 / 投标人资格 / 投标人基本情况表"
    assert report["missing_columns"]


def test_rows_to_rules_forward_fills_hierarchy_columns() -> None:
    rows = [
        {
            "文件类型": "商务文件",
            "组成模块": "补充文件",
            "子内容1": "投标保证金",
            "子内容2": "银行汇款",
            "是否从往期投标文件中摘取": "否",
        },
        {
            "文件类型": "商务文件",
            "组成模块": "",
            "子内容1": "",
            "子内容2": "银行保函",
            "是否从往期投标文件中摘取": "否",
        },
        {
            "文件类型": "商务文件",
            "组成模块": "",
            "子内容1": "投标人基本情况表",
            "子内容2": "基本信息",
            "是否从往期投标文件中摘取": "是",
        },
        {
            "文件类型": "商务文件",
            "组成模块": "",
            "子内容1": "",
            "子内容2": "股权信息",
            "是否从往期投标文件中摘取": "是",
        },
    ]

    rules, _ = rows_to_rules(rows)

    assert rules[0].section_path == "商务文件 / 补充文件 / 投标保证金 / 银行汇款"
    assert rules[1].section_path == "商务文件 / 补充文件 / 投标保证金 / 银行保函"
    assert rules[2].section_path == "商务文件 / 补充文件 / 投标人基本情况表 / 基本信息"
    assert rules[3].section_path == "商务文件 / 补充文件 / 投标人基本情况表 / 股权信息"
