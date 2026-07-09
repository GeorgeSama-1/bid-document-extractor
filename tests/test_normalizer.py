from bid_knowledge.matching.normalizer import normalize_section_title, strip_section_numbering


def test_strip_section_numbering_handles_common_chinese_patterns() -> None:
    assert strip_section_numbering("一、投标人基本情况表") == "投标人基本情况表"
    assert strip_section_numbering("（一）投标人基本情况") == "投标人基本情况"
    assert strip_section_numbering("1.1 投标人基本情况") == "投标人基本情况"


def test_normalize_section_title_removes_spaces_and_punctuation() -> None:
    normalized = normalize_section_title(" 一、投标人 基本情况表： ")
    assert normalized == "投标人基本情况表"
