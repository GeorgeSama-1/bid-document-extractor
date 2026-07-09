from bid_knowledge.matching.section_matcher import match_sections
from bid_knowledge.schemas.models import PdfTextBlock, ProcessingPlan, ProcessingPlanItem, ReconstructedSection, SectionRule


def make_rule(**overrides):
    data = {
        "rule_id": "rule-1",
        "file_type": "商务文件",
        "module_name": "模块A",
        "sub_content_1": "标题1",
        "sub_content_2": "标题2",
        "sub_content_3": "标题3",
        "section_path": "商务文件 / 模块A / 标题1 / 标题2 / 标题3",
        "has_standard_template": False,
        "from_history_bid": True,
        "ai_generated": False,
        "user_upload_required": False,
        "reference_technical_spec": False,
        "raw_row": {},
    }
    data.update(overrides)
    return SectionRule(**data)


def make_plan(rule_id: str) -> ProcessingPlan:
    return ProcessingPlan(
        company_id="demo_company",
        document_id="doc_001",
        file_type="商务文件",
        sections=[
            ProcessingPlanItem(
                rule_id=rule_id,
                section_path="商务文件 / 模块A / 标题1 / 标题2 / 标题3",
                from_history_bid=True,
                content_type="template_text",
                reuse_method="模板参考复用",
                enter_long_term_library=True,
                review_required=True,
                source_rule={},
                source_override={},
                process_strategy=["historical_reuse"],
            )
        ],
    )


def test_match_sections_prefers_deepest_sub_content_title() -> None:
    rule = make_rule()
    sections = [
        ReconstructedSection(
            section_id="sec-1",
            title="标题2",
            normalized_title="标题2",
            level=2,
            page_start=10,
            page_end=10,
            block_start_id="b1",
            block_end_id="b2",
            source_type="toc",
        ),
        ReconstructedSection(
            section_id="sec-2",
            title="标题3",
            normalized_title="标题3",
            level=3,
            page_start=11,
            page_end=11,
            block_start_id="b3",
            block_end_id="b4",
            source_type="toc",
        ),
    ]

    results = match_sections([rule], sections, plan=make_plan(rule.rule_id), blocks=[])

    assert results[0].matched is True
    assert results[0].matched_title == "标题3"


def test_match_sections_falls_back_to_block_search_when_title_not_in_toc() -> None:
    rule = make_rule(
        rule_id="rule-2",
        module_name="法定代表人授权委托书",
        sub_content_1="法定代表人（单位负责人）身份证（扫描件）",
        sub_content_2="",
        sub_content_3="",
        section_path="商务文件 / 法定代表人（单位负责人）身份证（扫描件）",
    )
    sections = [
        ReconstructedSection(
            section_id="sec-auth",
            title="法定代表人授权委托书",
            normalized_title="法定代表人授权委托书",
            level=1,
            page_start=834,
            page_end=835,
            block_start_id="b-auth-1",
            block_end_id="b-auth-9",
            source_type="toc",
        )
    ]
    blocks = [
        PdfTextBlock(
            block_id="blk-1",
            page_no=834,
            text="法定代表人（单位负责人）授权委托书",
            bbox=[0, 0, 10, 10],
            block_no=1,
        ),
        PdfTextBlock(
            block_id="blk-2",
            page_no=834,
            text="附：法定代表人（单位负责人）身份证（扫描件）",
            bbox=[0, 20, 10, 30],
            block_no=2,
        ),
    ]

    results = match_sections([rule], sections, plan=make_plan(rule.rule_id), blocks=blocks)

    assert results[0].matched is True
    assert results[0].matched_page_no == 834
    assert results[0].matched_page_end == 834
    assert results[0].matched_container_page_end == 835
    assert "法定代表人" in (results[0].matched_title or "")
    assert "block" in results[0].match_reason


def test_match_sections_prefers_stronger_block_hit_over_weak_section_hit() -> None:
    rule = make_rule(
        rule_id="rule-3",
        module_name="",
        sub_content_1="法定代表人（单位负责人）身份证（扫描件）",
        sub_content_2="",
        sub_content_3="",
        section_path="商务文件 / 法定代表人（单位负责人）身份证（扫描件）",
    )
    sections = [
        ReconstructedSection(
            section_id="sec-weak",
            title="（2）、 公司法定代表人（单位负责人）【周方洁】",
            normalized_title="公司法定代表人单位负责人周方洁",
            level=5,
            page_start=254,
            page_end=254,
            block_start_id="b-weak-1",
            block_end_id="b-weak-2",
            source_type="toc",
        ),
        ReconstructedSection(
            section_id="sec-auth",
            title="法定代表人授权委托书",
            normalized_title="法定代表人授权委托书",
            level=1,
            page_start=834,
            page_end=835,
            block_start_id="b-auth-1",
            block_end_id="b-auth-9",
            source_type="toc",
        ),
    ]
    blocks = [
        PdfTextBlock(
            block_id="blk-attach",
            page_no=834,
            text="附：法定代表人（单位负责人）身份证（扫描件）",
            bbox=[0, 20, 10, 30],
            block_no=2,
        ),
    ]

    results = match_sections([rule], sections, plan=make_plan(rule.rule_id), blocks=blocks)

    assert results[0].matched is True
    assert results[0].matched_source_type == "text_block"
    assert results[0].matched_page_no == 834
    assert results[0].matched_page_end == 834
    assert results[0].matched_container_page_end == 835


def test_match_sections_attachment_ignores_non_scan_identity_text() -> None:
    rule = make_rule(
        rule_id="rule-4",
        module_name="",
        sub_content_1="法定代表人（单位负责人）身份证（扫描件）",
        sub_content_2="",
        sub_content_3="",
        section_path="商务文件 / 法定代表人（单位负责人）身份证（扫描件）",
    )
    sections = [
        ReconstructedSection(
            section_id="sec-auth",
            title="法定代表人授权委托书",
            normalized_title="法定代表人授权委托书",
            level=1,
            page_start=834,
            page_end=835,
            block_start_id="b-auth-1",
            block_end_id="b-auth-9",
            source_type="toc",
        )
    ]
    blocks = [
        PdfTextBlock(
            block_id="blk-false",
            page_no=14,
            text="法定代表人（单位负责人） 姓名 周方洁",
            bbox=[0, 0, 10, 10],
            block_no=1,
        ),
        PdfTextBlock(
            block_id="blk-true",
            page_no=834,
            text="附：法定代表人（单位负责人）身份证（扫描件）",
            bbox=[0, 20, 10, 30],
            block_no=2,
        ),
    ]

    results = match_sections([rule], sections, plan=make_plan(rule.rule_id), blocks=blocks)

    assert results[0].matched is True
    assert results[0].matched_page_no == 834
    assert results[0].matched_source_type == "text_block"


def test_match_sections_prefers_body_match_and_keeps_multiple_instances() -> None:
    rule = make_rule(
        rule_id="rule-5",
        module_name="",
        sub_content_1="企业名称变更",
        sub_content_2="",
        sub_content_3="",
        section_path="商务文件 / 企业名称变更",
    )
    sections = [
        ReconstructedSection(
            section_id="sec-parent",
            title="3.9、投标人自述的企业名称变更原因说明、市场监督管理部门出具的‘名称变更证明’相关证明材料",
            normalized_title="39投标人自述的企业名称变更原因说明市场监督管理部门出具的名称变更证明相关证明材料",
            level=2,
            page_start=830,
            page_end=831,
            block_start_id="b-parent-1",
            block_end_id="b-parent-9",
            source_type="heuristic",
        ),
        ReconstructedSection(
            section_id="sec-2007",
            title="3.9.2、2007年企业名称变更证明材料",
            normalized_title="3922007年企业名称变更证明材料",
            level=3,
            page_start=830,
            page_end=830,
            block_start_id="b-2007-1",
            block_end_id="b-2007-2",
            source_type="heuristic",
        ),
        ReconstructedSection(
            section_id="sec-2015",
            title="3.9.3、2015年企业名称变更证明材料",
            normalized_title="3932015年企业名称变更证明材料",
            level=3,
            page_start=831,
            page_end=831,
            block_start_id="b-2015-1",
            block_end_id="b-2015-2",
            source_type="heuristic",
        ),
    ]
    blocks = [
        PdfTextBlock(
            block_id="blk-toc",
            page_no=9,
            text="3.9、 投标人自述的企业名称变更原因说明、市场监督管理部门出具的‘名称变更证明’相关证",
            bbox=[0, 0, 10, 10],
            block_no=1,
        ),
        PdfTextBlock(
            block_id="blk-2007",
            page_no=830,
            text="3.9.2、2007年企业名称变更证明材料",
            bbox=[0, 20, 10, 30],
            block_no=2,
        ),
        PdfTextBlock(
            block_id="blk-2015",
            page_no=831,
            text="3.9.3、2015年企业名称变更证明材料",
            bbox=[0, 40, 10, 50],
            block_no=3,
        ),
    ]

    results = match_sections([rule], sections, plan=make_plan(rule.rule_id), blocks=blocks)

    assert results[0].matched is True
    assert results[0].matched_page_no in (830, 831)
    assert results[0].matched_page_no != 9
    assert "企业名称变更原因说明" in str(results[0].matched_container_title)
    assert any("2007年企业名称变更证明材料" in str(item.get("matched_title", "")) for item in results[0].related_matches + [{"matched_title": results[0].matched_title}])
    assert any("2015年企业名称变更证明材料" in str(item.get("matched_title", "")) for item in results[0].related_matches + [{"matched_title": results[0].matched_title}])


def test_match_sections_ignores_directory_page_text_block_hits() -> None:
    rule = make_rule(
        rule_id="rule-directory",
        module_name="投标人与国家电网公司系统人员关系说明",
        sub_content_1="",
        sub_content_2="",
        sub_content_3="",
        section_path="商务文件 / 投标人与国家电网公司系统人员关系说明",
    )
    sections = [
        ReconstructedSection(
            section_id="sec-real",
            title="投标人与国家电网公司系统人员关系说明",
            normalized_title="投标人与国家电网公司系统人员关系说明",
            level=1,
            page_start=45,
            page_end=46,
            block_start_id="b-real-1",
            block_end_id="b-real-9",
            source_type="heuristic",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="toc-title", page_no=3, text="目录", bbox=[0, 10, 100, 30], block_no=1),
        PdfTextBlock(
            block_id="toc-hit",
            page_no=3,
            text="投标人与国家电网公司系统人员关系说明 ........ 45",
            bbox=[0, 50, 300, 70],
            block_no=2,
        ),
        PdfTextBlock(
            block_id="body-hit",
            page_no=45,
            text="投标人与国家电网公司系统人员关系说明",
            bbox=[0, 50, 300, 70],
            block_no=3,
        ),
    ]

    results = match_sections([rule], sections, plan=make_plan(rule.rule_id), blocks=blocks)

    assert results[0].matched is True
    assert results[0].matched_page_no == 45
    assert results[0].matched_source_type == "section"
    assert all(item.get("matched_page_no") != 3 for item in results[0].related_matches)


def test_match_sections_prefers_authorization_title_over_business_license_role_text() -> None:
    rule = make_rule(
        rule_id="rule-auth",
        module_name="法定代表人授权委托书",
        sub_content_1="",
        sub_content_2="",
        sub_content_3="",
        section_path="商务文件 / 法定代表人授权委托书",
    )
    sections = [
        ReconstructedSection(
            section_id="sec-business-license",
            title="企业营业执照扫描件",
            normalized_title="企业营业执照扫描件",
            level=1,
            page_start=22,
            page_end=23,
            block_start_id="b-license-1",
            block_end_id="b-license-9",
            source_type="toc",
        ),
        ReconstructedSection(
            section_id="sec-auth",
            title="法定代表人授权委托书",
            normalized_title="法定代表人授权委托书",
            level=1,
            page_start=834,
            page_end=835,
            block_start_id="b-auth-1",
            block_end_id="b-auth-9",
            source_type="toc",
        ),
    ]
    blocks = [
        PdfTextBlock(
            block_id="license-role",
            page_no=22,
            text="法定代表人 周方洁\n国家企业信用信息公示系统网址：http://www.gsxl.gov.cn",
            bbox=[0, 300, 500, 340],
            block_no=1,
        ),
        PdfTextBlock(
            block_id="auth-title",
            page_no=834,
            text="4、法定代表人授权委托书",
            bbox=[0, 50, 500, 80],
            block_no=2,
        ),
    ]

    results = match_sections([rule], sections, plan=make_plan(rule.rule_id), blocks=blocks)

    assert results[0].matched is True
    assert results[0].matched_page_no == 834
    assert results[0].matched_title == "法定代表人授权委托书"


def test_match_sections_scopes_authorization_child_to_parent_section() -> None:
    rule = make_rule(
        rule_id="rule-auth-child",
        module_name="法定代表人授权委托书",
        sub_content_1="被授权人身份证等有效身份证件（扫描件）",
        sub_content_2="",
        sub_content_3="",
        section_path="商务文件 / 法定代表人授权委托书 / 被授权人身份证等有效身份证件（扫描件）",
    )
    sections = [
        ReconstructedSection(
            section_id="sec-other",
            title="国家企业信用信息公示系统网站自动生成的PDF文件",
            normalized_title="国家企业信用信息公示系统网站自动生成的pdf文件",
            level=1,
            page_start=22,
            page_end=23,
            block_start_id="b-other-1",
            block_end_id="b-other-9",
            source_type="toc",
        ),
        ReconstructedSection(
            section_id="sec-auth",
            title="法定代表人授权委托书",
            normalized_title="法定代表人授权委托书",
            level=1,
            page_start=834,
            page_end=836,
            block_start_id="b-auth-1",
            block_end_id="b-auth-9",
            source_type="toc",
        ),
    ]
    blocks = [
        PdfTextBlock(
            block_id="wrong-image-title",
            page_no=22,
            text="被授权人身份证等有效身份证件（扫描件）",
            bbox=[0, 20, 10, 30],
            block_no=1,
        ),
        PdfTextBlock(
            block_id="right-attachment-title",
            page_no=835,
            text="附：被授权人身份证等有效身份证件（扫描件）",
            bbox=[0, 20, 10, 30],
            block_no=2,
        ),
    ]

    results = match_sections([rule], sections, plan=make_plan(rule.rule_id), blocks=blocks)

    assert results[0].matched is True
    assert results[0].matched_source_type == "text_block"
    assert results[0].matched_page_no == 835
    assert results[0].matched_container_title == "法定代表人授权委托书"
    assert all(item.get("matched_page_no") != 22 for item in results[0].related_matches)


def test_text_block_match_does_not_inherit_large_container_page_end() -> None:
    rule = make_rule(
        rule_id="rule-block-anchor",
        module_name="补充文件",
        sub_content_1="中国质量奖",
        sub_content_2="",
        sub_content_3="",
        section_path="商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 中国质量奖",
    )
    sections = [
        ReconstructedSection(
            section_id="sec-large-parent",
            title="二、高质量发展评价",
            normalized_title="二高质量发展评价",
            level=2,
            page_start=574,
            page_end=835,
            block_start_id="b-parent-1",
            block_end_id="b-parent-9",
            source_type="toc",
        )
    ]
    blocks = [
        PdfTextBlock(
            block_id="b-award",
            page_no=810,
            text="3.8.15、中国质量奖（无）",
            bbox=[0, 120, 300, 140],
            block_no=1,
        )
    ]

    results = match_sections([rule], sections, plan=make_plan(rule.rule_id), blocks=blocks)

    assert results[0].matched is True
    assert results[0].matched_source_type == "text_block"
    assert results[0].matched_page_no == 810
    assert results[0].matched_page_end == 810
    assert results[0].matched_container_page_end == 835
