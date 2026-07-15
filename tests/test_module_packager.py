import json
import re
import sys
from io import BytesIO
from types import SimpleNamespace
from pathlib import Path

from bid_knowledge.parsing.module_packager import _backfill_missing_material_indexes, _write_material_markdown, package_module_artifacts
from bid_knowledge.schemas.models import PageMaterialItem, ParsedTable, PdfTextBlock, ReusableCandidate


def _candidate(
    section_path: str,
    source_page: int,
    source_page_end: int,
    source_container_title: str,
    *,
    from_history_bid: bool = True,
    has_standard_template: bool = False,
) -> ReusableCandidate:
    return ReusableCandidate(
        candidate_id=f"cand-{source_page}",
        company_id="demo_company",
        document_id="demo_doc",
        rule_id=f"rule-{source_page}",
        section_path=section_path,
        from_history_bid=from_history_bid,
        has_standard_template=has_standard_template,
        title=section_path.split(" / ")[-1],
        content="",
        candidate_type="attachment",
        reuse_method="附件召回",
        reuse_level="long_term",
        enter_long_term_library=True,
        source_file="demo.pdf",
        source_page=source_page,
        source_page_end=source_page_end,
        source_container_title=source_container_title,
    )


def _write_demo_pdf(path: Path, page_count: int) -> Path:
    import fitz

    doc = fitz.open()
    try:
        for index in range(page_count):
            page = doc.new_page()
            page.insert_text((72, 72), f"demo page {index + 1}")
        doc.save(path)
    finally:
        doc.close()
    return path


def test_package_module_artifacts_exports_named_items_under_module(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 商务评分标准涉及的支撑材料 / 一、履约能力评价 / 经营状况",
            100,
            101,
            "3.8.1.2 企业整体经营状况优良",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=100, text="3.8.1.2 企业整体经营状况优良", bbox=[0, 10, 100, 20], block_no=1),
        PdfTextBlock(block_id="b2", page_no=100, text="（1）、企业发展稳健", bbox=[0, 30, 100, 40], block_no=2),
        PdfTextBlock(block_id="b3", page_no=101, text="（2）、具备优秀的团队", bbox=[0, 20, 100, 30], block_no=3),
    ]
    tables = [
        ParsedTable(table_id="table-1", page_no=101, rows=[["姓名", "岗位"]], bbox=[10, 40, 200, 180]),
    ]
    images = [
        {"image_id": "img-1", "page_no": 100, "xref": 10, "width": 500, "height": 400, "rect": [10, 50, 150, 120], "ext": "jpeg"},
        {"image_id": "img-2", "page_no": 100, "xref": 11, "width": 500, "height": 400, "rect": [10, 130, 150, 200], "ext": "jpeg"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "jpeg")),
    )

    module_dir = tmp_path / "modules" / "商务评分标准涉及的支撑材料" / "一、履约能力评价" / "经营状况"
    assert module_dir.exists()
    assert (module_dir / "section_meta.json").exists()
    assert (module_dir / "tables.json").exists()
    assert (module_dir / "images.json").exists()

    table_item = module_dir / "table_items" / "具备优秀的团队_表1.json"
    image_item_1 = module_dir / "image_items" / "企业发展稳健_图1.json"
    image_item_2 = module_dir / "image_items" / "企业发展稳健_图2.json"
    image_file_1 = module_dir / "image_items" / "企业发展稳健_图1.jpeg"
    image_file_2 = module_dir / "image_items" / "企业发展稳健_图2.jpeg"

    assert table_item.exists()
    assert image_item_1.exists()
    assert image_item_2.exists()
    assert image_file_1.exists()
    assert image_file_2.exists()


def test_pdf_toc_leaf_splits_numbered_subsections_and_keeps_parent_preface(
    tmp_path: Path,
) -> None:
    section_path = (
        "PDF / 3、 补充文件 / 3.8、 商务评分支撑材料 / "
        "3.8.2、 高质量发展评价 / 3.8.2.1、 绿色发展规划及管理体系认证"
    )
    candidate = _candidate(
        section_path,
        1,
        4,
        "3.8.2、高质量发展评价",
    ).model_copy(
        update={
            "candidate_type": "pdf_toc_leaf",
            "material_evidence": {
                "source": "pdf_toc_leaf",
                "start_y": 10.0,
                "end_y": None,
            },
        }
    )
    blocks = [
        PdfTextBlock(block_id="root", page_no=1, text="3.8.2.1、绿色发展规划及管理体系认证", bbox=[0, 10, 400, 30], block_no=1),
        PdfTextBlock(block_id="root-preface", page_no=1, text="父章节在首个子章节前的说明。", bbox=[0, 40, 400, 60], block_no=2),
        PdfTextBlock(block_id="n1", page_no=1, text="（1）、建立绿色发展规划制度", bbox=[0, 100, 400, 120], block_no=3),
        PdfTextBlock(block_id="n1-body", page_no=1, text="绿色发展规划正文。", bbox=[0, 130, 400, 150], block_no=4),
        PdfTextBlock(block_id="n2", page_no=2, text="（2）、能源管理体系证书（无）", bbox=[0, 20, 400, 40], block_no=5),
        PdfTextBlock(block_id="n2-body", page_no=2, text="无。", bbox=[0, 50, 400, 70], block_no=6),
        PdfTextBlock(block_id="n3", page_no=2, text="（3）、质量管理体系、职业健康安全管理体系及环境管理体系证书", bbox=[0, 100, 500, 120], block_no=7),
        PdfTextBlock(block_id="n3-preface", page_no=2, text="三项管理体系证书总体说明。", bbox=[0, 130, 400, 150], block_no=8),
        PdfTextBlock(block_id="n31", page_no=2, text="（3.1）、质量管理体系认证证书", bbox=[0, 200, 400, 220], block_no=9),
        PdfTextBlock(block_id="n31-body", page_no=2, text="质量管理体系证书正文。", bbox=[0, 230, 400, 250], block_no=10),
        PdfTextBlock(block_id="n32", page_no=3, text="（3.2）、职业健康安全管理体系认证证书", bbox=[0, 20, 400, 40], block_no=11),
        PdfTextBlock(block_id="n32-body", page_no=3, text="职业健康安全证书正文。", bbox=[0, 50, 400, 70], block_no=12),
        PdfTextBlock(block_id="n33", page_no=4, text="（3.3）、环境管理体系认证证书", bbox=[0, 20, 400, 40], block_no=13),
        PdfTextBlock(block_id="n33-body", page_no=4, text="环境管理体系证书正文。", bbox=[0, 50, 400, 70], block_no=14),
    ]
    images = [
        {"image_id": "root-image", "page_no": 1, "xref": 1, "width": 501, "height": 401, "rect": [10, 65, 180, 95], "ext": "png"},
        {"image_id": "n1-image", "page_no": 1, "xref": 2, "width": 502, "height": 402, "rect": [10, 160, 180, 220], "ext": "png"},
        {"image_id": "n3-image", "page_no": 2, "xref": 3, "width": 503, "height": 403, "rect": [10, 160, 180, 190], "ext": "png"},
        {"image_id": "n31-image", "page_no": 2, "xref": 4, "width": 504, "height": 404, "rect": [10, 260, 180, 500], "ext": "png"},
        {"image_id": "n32-image", "page_no": 3, "xref": 5, "width": 505, "height": 405, "rect": [10, 80, 180, 500], "ext": "png"},
        {"image_id": "n33-image", "page_no": 4, "xref": 6, "width": 506, "height": 406, "rect": [10, 80, 180, 500], "ext": "png"},
    ]
    tables = [
        ParsedTable(
            table_id="n3-table",
            page_no=2,
            rows=[["体系", "状态"], ["管理体系", "有效"]],
            bbox=[250, 160, 500, 190],
        )
    ]

    package_module_artifacts(
        candidates=[candidate],
        blocks=blocks,
        tables=tables,
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (
            f"image-{item['image_id']}".encode(),
            str(item.get("ext") or "png"),
        ),
    )

    root = (
        tmp_path
        / "modules"
        / "3、 补充文件"
        / "3.8、 商务评分支撑材料"
        / "3.8.2、 高质量发展评价"
        / "3.8.2.1、 绿色发展规划及管理体系认证"
    )
    n1 = root / "（1）、 建立绿色发展规划制度"
    n2 = root / "（2）、 能源管理体系证书（无）"
    n3 = root / "（3）、 质量管理体系、职业健康安全管理体系及环境管理体系证书"
    n31 = n3 / "（3.1）、 质量管理体系认证证书"
    n32 = n3 / "（3.2）、 职业健康安全管理体系认证证书"
    n33 = n3 / "（3.3）、 环境管理体系认证证书"

    root_md = (root / "material.md").read_text(encoding="utf-8")
    n3_md = (n3 / "material.md").read_text(encoding="utf-8")
    assert "父章节在首个子章节前的说明。" in root_md
    assert "绿色发展规划正文。" not in root_md
    assert "- [（1）、 建立绿色发展规划制度]" in root_md
    assert "- [（2）、 能源管理体系证书（无）]" in root_md
    assert "- [（3）、 质量管理体系、职业健康安全管理体系及环境管理体系证书]" in root_md
    assert "三项管理体系证书总体说明。" in n3_md
    assert "| 体系 | 状态 |" in n3_md
    assert "质量管理体系证书正文。" not in n3_md
    assert "- [（3.1）、 质量管理体系认证证书]" in n3_md
    assert "- [（3.2）、 职业健康安全管理体系认证证书]" in n3_md
    assert "- [（3.3）、 环境管理体系认证证书]" in n3_md
    assert "绿色发展规划正文。" in (n1 / "material.md").read_text(encoding="utf-8")
    assert "无。" in (n2 / "material.md").read_text(encoding="utf-8")
    assert "质量管理体系证书正文。" in (n31 / "material.md").read_text(encoding="utf-8")
    assert "职业健康安全证书正文。" in (n32 / "material.md").read_text(encoding="utf-8")
    assert "环境管理体系证书正文。" in (n33 / "material.md").read_text(encoding="utf-8")
    assert len(list((root / "image_items").glob("*.png"))) == 1
    assert len(list((n3 / "image_items").glob("*.png"))) == 1
    assert len(list((n31 / "image_items").glob("*.png"))) == 1
    assert len(list((n32 / "image_items").glob("*.png"))) == 1
    assert len(list((n33 / "image_items").glob("*.png"))) == 1


def test_package_module_artifacts_uses_previous_page_heading_when_current_page_has_no_new_heading(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 法定代表人授权委托书 / 法定代表人（单位负责人）身份证（扫描件）",
            200,
            201,
            "4、法定代表人授权委托书",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=200, text="（3.1）、安全生产标准化证书", bbox=[0, 20, 100, 30], block_no=1),
    ]
    images = [
        {"image_id": "img-1", "page_no": 201, "xref": 20, "width": 600, "height": 500, "rect": [10, 40, 150, 180], "ext": "png"},
        {"image_id": "img-2", "page_no": 201, "xref": 21, "width": 600, "height": 500, "rect": [10, 190, 150, 330], "ext": "png"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
    )

    module_dir = tmp_path / "modules" / "法定代表人授权委托书" / "法定代表人（单位负责人）身份证（扫描件）" / "image_items"
    assert (module_dir / "安全生产标准化证书_图1.json").exists()
    assert (module_dir / "安全生产标准化证书_图2.json").exists()


def test_package_module_artifacts_ignores_table_internal_text_as_heading(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 3、 补充文件 / 3.8.1、 经营状况",
            579,
            579,
            "3.8.1、 经营状况",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=579, text="序\n号", bbox=[80, 80, 100, 110], block_no=1),
    ]
    tables = [
        ParsedTable(table_id="table-1", page_no=579, rows=[["序号", "年份"]], bbox=[35, 40, 560, 760]),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    table_json = tmp_path / "modules" / "3、 补充文件" / "3.8.1、 经营状况" / "table_items" / "3.8.1、 经营状况_表1.json"
    payload = json.loads(table_json.read_text(encoding="utf-8"))
    assert payload["context_title"] == "3.8.1、 经营状况"
    assert payload["parent_section_title"] == "3.8.1、 经营状况"


def test_package_module_artifacts_skips_non_history_items_and_writes_template_capture(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 商务评分标准涉及的支撑材料 / 一、履约能力评价 / 售后服务",
            10,
            10,
            "3.8.2 售后服务",
            from_history_bid=True,
            has_standard_template=True,
        ),
        _candidate(
            "商务文件 / 商务评分标准涉及的支撑材料 / 二、高质量发展评价 / 绿色发展",
            11,
            11,
            "3.9.1 绿色发展",
            from_history_bid=False,
        ),
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=10, text="（1）、售后服务承诺", bbox=[0, 10, 100, 20], block_no=1),
        PdfTextBlock(block_id="b2", page_no=10, text="承诺内容正文", bbox=[0, 30, 100, 40], block_no=2),
        PdfTextBlock(block_id="b3", page_no=11, text="绿色发展", bbox=[0, 10, 100, 20], block_no=3),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
        top_level_modules=["商务偏差表", "投标保证保险", "补充文件", "法定代表人授权委托书"],
    )

    kept_dir = tmp_path / "modules" / "商务评分标准涉及的支撑材料" / "一、履约能力评价" / "售后服务"
    skipped_dir = tmp_path / "modules" / "商务评分标准涉及的支撑材料" / "二、高质量发展评价" / "绿色发展"
    assert kept_dir.exists()
    assert not skipped_dir.exists()
    assert (kept_dir / "source_capture.json").exists()
    assert (tmp_path / "modules" / "商务偏差表").exists()
    assert (tmp_path / "modules" / "投标保证保险").exists()


def test_package_module_artifacts_precreates_empty_history_tree_dirs(tmp_path: Path) -> None:
    package_module_artifacts(
        candidates=[],
        blocks=[],
        tables=[],
        images=[],
        out_dir=tmp_path,
        top_level_modules=["商务偏差表", "投标保证保险", "补充文件", "法定代表人授权委托书"],
        planned_section_paths=[
            "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 经营状况",
            "商务文件 / 补充文件 / 财务状况 / 经会计师事务所或审计机构审计的财务会计报表 / 利润表",
            "商务文件 / 法定代表人授权委托书 / 被授权人身份证等有效身份证件（扫描件）",
        ],
    )

    assert (tmp_path / "modules" / "商务偏差表" / "module_meta.json").exists()
    assert (tmp_path / "modules" / "投标保证保险" / "module_meta.json").exists()
    assert (
        tmp_path
        / "modules"
        / "补充文件"
        / "“商务评分标准”涉及的支撑材料"
        / "一、履约能力评价"
        / "经营状况"
        / "section_meta.json"
    ).exists()
    assert (
        tmp_path
        / "modules"
        / "补充文件"
        / "财务状况"
        / "经会计师事务所或审计机构审计的财务会计报表"
        / "利润表"
        / "section_meta.json"
    ).exists()
    assert (
        tmp_path
        / "modules"
        / "法定代表人授权委托书"
        / "被授权人身份证等有效身份证件（扫描件）"
        / "section_meta.json"
    ).exists()
    empty_material_md = (
        tmp_path
        / "modules"
        / "补充文件"
        / "“商务评分标准”涉及的支撑材料"
        / "一、履约能力评价"
        / "经营状况"
        / "material.md"
    ).read_text(encoding="utf-8")
    assert empty_material_md.startswith("# 经营状况")
    assert "暂无可直接复用内容" in empty_material_md


def test_package_module_artifacts_filters_repeated_header_logo_images(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 经营状况",
            1,
            20,
            "3.8、招标文件第三章评标办法前附表之三“商务评分标准”涉及的支撑材料",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=1, text="（1.1）、部分业主出具的运行报告16份", bbox=[0, 80, 100, 90], block_no=1),
    ]
    repeated_logo = [
        {
            "image_id": f"logo-{page}",
            "page_no": page,
            "xref": 16,
            "width": 154,
            "height": 70,
            "rect": [72.0, 30.4, 122.5, 53.3],
            "ext": "png",
        }
        for page in range(1, 21)
    ]
    real_image = {
        "image_id": "real-1",
        "page_no": 1,
        "xref": 200,
        "width": 1200,
        "height": 800,
        "rect": [100.0, 160.0, 420.0, 360.0],
        "ext": "jpeg",
    }

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=repeated_logo + [real_image],
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
    )

    image_dir = tmp_path / "modules" / "补充文件" / "“商务评分标准”涉及的支撑材料" / "一、履约能力评价" / "经营状况" / "image_items"
    exported = sorted(path.name for path in image_dir.glob("*.json"))

    assert "部分业主出具的运行报告16份_图1.json" in exported
    assert all("logo" not in name.lower() for name in exported)
    assert len(exported) == 1


def test_package_module_artifacts_filters_items_inside_pp_layout_masks(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / 企业名称变更",
            1,
            1,
            "企业名称变更",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="header", page_no=1, text="国网甘肃省电力公司 商务投标文件", bbox=[20, 20, 500, 40], block_no=1),
        PdfTextBlock(block_id="body", page_no=1, text="企业名称变更正文", bbox=[20, 120, 500, 150], block_no=2),
        PdfTextBlock(block_id="footer", page_no=1, text="22", bbox=[290, 760, 310, 780], block_no=3),
    ]
    tables = [
        ParsedTable(table_id="body-table", page_no=1, rows=[["项目", "内容"]], bbox=[20, 180, 500, 260]),
    ]
    images = [
        {"image_id": "header-logo", "page_no": 1, "xref": 10, "width": 300, "height": 80, "rect": [20, 10, 160, 60], "ext": "png"},
        {"image_id": "body-image", "page_no": 1, "xref": 11, "width": 600, "height": 500, "rect": [20, 320, 260, 520], "ext": "png"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
        layout_masks=[
            {"page_no": 1, "label": "header", "bbox": [0, 0, 600, 80], "page_width": 600, "page_height": 800},
            {"page_no": 1, "label": "number", "bbox": [0, 740, 600, 800], "page_width": 600, "page_height": 800},
        ],
    )

    material_dir = tmp_path / "modules" / "补充文件" / "企业名称变更"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    image_files = sorted(path.name for path in (material_dir / "image_items").glob("*.png"))

    assert "国网甘肃省电力公司" not in material_md
    assert "\n22\n" not in material_md
    assert "企业名称变更正文" in material_md
    assert "| 项目 | 内容 |" in material_md
    assert "| --- | --- |" in material_md
    assert image_files == ["企业名称变更_图1.png"]


def test_package_module_artifacts_filters_repeated_page_header_text(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / 企业营业执照（或事业单位法人证书或其他组织登记证书）（扫描件）",
            22,
            23,
            "3.4、企业营业执照（或事业单位法人证书或其他组织登记证书）（扫描件）",
        )
    ]
    header = "国网甘肃省电力公司【测控及在线监测系统】包05、包06、包07、包08——商务投标文件"
    blocks = [
        PdfTextBlock(block_id="h22", page_no=22, text=header, bbox=[824, 23, 1666, 48], block_no=1),
        PdfTextBlock(block_id="b22", page_no=22, text="企业营业执照副本", bbox=[700, 140, 950, 163], block_no=2),
        PdfTextBlock(block_id="h23", page_no=23, text=header, bbox=[824, 23, 1666, 48], block_no=1),
        PdfTextBlock(block_id="b23", page_no=23, text="统一社会信用代码 913302007251641924", bbox=[180, 240, 900, 260], block_no=2),
    ]
    page_material_items = [
        PageMaterialItem(
            item_id="pp-header-22",
            item_type="text",
            source_type="pp_structure_text_region",
            page_no=22,
            top_y=23,
            bbox=[824, 23, 1666, 48],
            text=header,
            payload={"layout_label": "header"},
        ),
        PageMaterialItem(
            item_id="pp-body-22",
            item_type="text",
            source_type="pp_structure_text_region",
            page_no=22,
            top_y=140,
            bbox=[700, 140, 950, 163],
            text="企业营业执照副本",
            payload={"layout_label": "text"},
        ),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
        page_material_items=page_material_items,
    )

    ordered_path = (
        tmp_path
        / "modules"
        / "补充文件"
        / "企业营业执照（或事业单位法人证书或其他组织登记证书）（扫描件）"
        / "ordered_material.json"
    )
    ordered = json.loads(ordered_path.read_text(encoding="utf-8"))
    dumped = json.dumps(ordered, ensure_ascii=False)

    assert header not in dumped
    assert "企业营业执照副本" in dumped


def test_package_module_artifacts_assigns_stream_image_to_nearest_body_heading(tmp_path: Path, monkeypatch) -> None:
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    class FakePixmap:
        def save(self, path: str | Path) -> None:
            Path(path).write_bytes(b"fake-cropped-image")

    class FakePage:
        rect = SimpleNamespace(width=300.0, height=300.0)

        def get_pixmap(self, **kwargs):
            if "clip" in kwargs:
                assert kwargs["clip"].x0 > 0
            return FakePixmap()

    class FakeDoc:
        page_count = 1

        def load_page(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def insert_pdf(self, *_args, **_kwargs) -> None:
            return None

        def save(self, path: str | Path) -> None:
            Path(path).write_bytes(b"fake-pdf")

        def close(self) -> None:
            return None

    class FakeRect:
        def __init__(self, x0, y0, x1, y1):
            self.x0 = x0
            self.y0 = y0
            self.x1 = x1
            self.y1 = y1

    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(open=lambda *_args, **_kwargs: FakeDoc(), Rect=FakeRect, Matrix=lambda *_args: None))

    candidates = [
        _candidate(
            "商务文件 / 补充文件 / 企业营业执照（或事业单位法人证书或其他组织登记证书）（扫描件）",
            1,
            1,
            "3.4、企业营业执照（或事业单位法人证书或其他组织登记证书）（扫描件）",
        )
    ]
    header = "国网甘肃省电力公司【测控及在线监测系统】包05、包06、包07、包08——商务投标文件"
    blocks = [
        PdfTextBlock(block_id="h22", page_no=1, text=header, bbox=[824, 23, 1666, 48], block_no=1),
        PdfTextBlock(
            block_id="title22",
            page_no=1,
            text="3.4、企业营业执照（或事业单位法人证书或其他组织登记证书）（扫描件）",
            bbox=[20, 90, 950, 117],
            block_no=2,
        ),
        PdfTextBlock(
            block_id="seat-title",
            page_no=1,
            text="（1）座位图片",
            bbox=[20, 150, 300, 170],
            block_no=3,
        ),
    ]
    page_material_items = [
        PageMaterialItem(
            item_id="pp-image-1",
            item_type="image",
            source_type="pp_structure_image_region",
            page_no=1,
            top_y=200,
            bbox=[164, 200, 1502, 1024],
            text="",
            payload={"layout_label": "image", "page_width": 1684, "page_height": 1191},
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
        pdf_path=pdf_path,
        page_material_items=page_material_items,
    )

    ordered_path = (
        tmp_path
        / "modules"
        / "补充文件"
        / "企业营业执照（或事业单位法人证书或其他组织登记证书）（扫描件）"
        / "ordered_material.json"
    )
    ordered = json.loads(ordered_path.read_text(encoding="utf-8"))
    stream_image = next(item for item in ordered["items"] if item.get("item_id") == "pp-image-1")

    assert stream_image["nearest_heading"] == "座位图片"
    assert header not in stream_image["nearest_heading"]
    assert stream_image["file_path"].endswith("image_items/座位图片_图1.png")
    material_md = (
        tmp_path
        / "modules"
        / "补充文件"
        / "企业营业执照（或事业单位法人证书或其他组织登记证书）（扫描件）"
        / "material.md"
    ).read_text(encoding="utf-8")
    assert "![座位图片_图1](image_items/座位图片_图1.png)" in material_md


def test_package_module_artifacts_skips_title_like_pp_image_regions(tmp_path: Path, monkeypatch) -> None:
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    class FakePixmap:
        def save(self, path: str | Path) -> None:
            Path(path).write_bytes(b"fake-cropped-image")

    class FakePage:
        rect = SimpleNamespace(width=595.0, height=842.0)

        def get_pixmap(self, **_kwargs):
            return FakePixmap()

    class FakeDoc:
        page_count = 1

        def load_page(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def insert_pdf(self, *_args, **_kwargs) -> None:
            return None

        def save(self, path: str | Path) -> None:
            Path(path).write_bytes(b"fake-pdf")

        def close(self) -> None:
            return None

    class FakeRect:
        def __init__(self, x0, y0, x1, y1):
            self.x0 = x0
            self.y0 = y0
            self.x1 = x1
            self.y1 = y1

    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(open=lambda *_args, **_kwargs: FakeDoc(), Rect=FakeRect, Matrix=lambda *_args: None))

    candidates = [
        _candidate(
            "技术文件 / 3.6.1.1、技术偏差表",
            1,
            1,
            "3.6.1.1、技术偏差表",
        )
    ]
    page_material_items = [
        PageMaterialItem(
            item_id="pp-image-title",
            item_type="image",
            source_type="pp_structure_image_region",
            page_no=1,
            top_y=72,
            bbox=[72, 72, 152, 88],
            text="",
            payload={
                "layout_label": "image",
                "ocr_texts": ["3.6.1.1、"],
                "ocr_scores": [0.99],
                "page_width": 595,
                "page_height": 842,
            },
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=[],
        tables=[],
        images=[],
        out_dir=tmp_path,
        pdf_path=pdf_path,
        page_material_items=page_material_items,
    )

    material_dir = tmp_path / "modules" / "3.6.1.1、技术偏差表"
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")

    assert all(item.get("item_id") != "pp-image-title" for item in ordered["items"])
    assert "![" not in material_md
    exported_pngs = list((material_dir / "image_items").glob("*.png")) if (material_dir / "image_items").exists() else []
    assert exported_pngs == []


def test_package_module_artifacts_assigns_attachment_stream_image_to_fu_heading(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 法定代表人授权委托书 / 法定代表人授权委托书",
            1,
            1,
            "4、法定代表人授权委托书",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=1, text="4、法定代表人授权委托书", bbox=[0, 20, 300, 40], block_no=1),
        PdfTextBlock(block_id="b2", page_no=1, text="授权正文", bbox=[0, 80, 300, 100], block_no=2),
        PdfTextBlock(block_id="b3", page_no=1, text="附：被授权人身份证等有效身份证件（扫描件）", bbox=[0, 120, 300, 140], block_no=3),
    ]
    page_material_items = [
        PageMaterialItem(
            item_id="pp-image-id-card",
            item_type="image",
            source_type="pp_structure_image_region",
            page_no=1,
            top_y=180,
            bbox=[10, 180, 500, 420],
            text="",
            payload={"layout_label": "image"},
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
        page_material_items=page_material_items,
    )

    ordered_path = (
        tmp_path
        / "modules"
        / "法定代表人授权委托书"
        / "法定代表人授权委托书"
        / "ordered_material.json"
    )
    ordered = json.loads(ordered_path.read_text(encoding="utf-8"))
    stream_image = next(item for item in ordered["items"] if item.get("item_id") == "pp-image-id-card")

    assert stream_image["nearest_heading"] == "附：被授权人身份证等有效身份证件（扫描件）"
    assert all(item["item_type"] != "submaterial" for item in ordered["items"])


def test_package_module_artifacts_does_not_name_image_from_bid_package_context(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 法定代表人授权委托书 / 法定代表人（单位负责人）身份证（扫描件）",
            1,
            1,
            "测控及在线监测系统）（包号：包05、包06、包07、包08）（包名称：测控及在线监测系统）",
        )
    ]
    blocks = [
        PdfTextBlock(
            block_id="package-context",
            page_no=1,
            text="测控及在线监测系统）（包号：包05、包06、包07、包08）（包名称：测控及在线监测系统）",
            bbox=[0, 80, 500, 100],
            block_no=1,
        ),
    ]
    images = [
        {"image_id": "id-card", "page_no": 1, "xref": 20, "width": 600, "height": 500, "rect": [10, 200, 500, 420], "ext": "png"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
    )

    image_dir = tmp_path / "modules" / "法定代表人授权委托书" / "法定代表人（单位负责人）身份证（扫描件）" / "image_items"
    exported = sorted(path.name for path in image_dir.glob("*.json"))

    assert exported == ["法定代表人（单位负责人）身份证（扫描件）_图1.json"]


def test_package_module_artifacts_scopes_planned_authorization_attachment_to_matching_fu_anchor(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 法定代表人授权委托书 / 法定代表人（单位负责人）身份证（扫描件）",
            1,
            1,
            "4、法定代表人授权委托书",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=1, text="4、法定代表人授权委托书", bbox=[0, 20, 300, 40], block_no=1),
        PdfTextBlock(block_id="b2", page_no=1, text="附：法定代表人（单位负责人）身份证（扫描件）", bbox=[0, 100, 300, 120], block_no=2),
        PdfTextBlock(block_id="b3", page_no=1, text="法人身份证文字", bbox=[0, 140, 300, 160], block_no=3),
        PdfTextBlock(block_id="b4", page_no=1, text="附：被授权人身份证等有效身份证件（扫描件）", bbox=[0, 300, 300, 320], block_no=4),
        PdfTextBlock(block_id="b5", page_no=1, text="被授权人身份证文字", bbox=[0, 340, 300, 360], block_no=5),
    ]
    images = [
        {"image_id": "legal-id", "page_no": 1, "xref": 21, "width": 600, "height": 500, "rect": [10, 180, 500, 260], "ext": "png"},
        {"image_id": "agent-id", "page_no": 1, "xref": 22, "width": 600, "height": 500, "rect": [10, 380, 500, 460], "ext": "png"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
    )

    section_dir = tmp_path / "modules" / "法定代表人授权委托书" / "法定代表人（单位负责人）身份证（扫描件）"
    ordered = json.loads((section_dir / "ordered_material.json").read_text(encoding="utf-8"))
    dumped = json.dumps(ordered, ensure_ascii=False)
    exported_images = sorted(path.name for path in (section_dir / "image_items").glob("*.json"))

    assert "被授权人身份证" not in dumped
    assert exported_images == ["法定代表人（单位负责人）身份证（扫描件）_图1.json"]


def test_package_module_artifacts_keeps_two_id_card_sides_and_filters_seal(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 法定代表人授权委托书 / 法定代表人（单位负责人）身份证（扫描件）",
            1,
            1,
            "4、法定代表人授权委托书",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=1, text="4、法定代表人授权委托书", bbox=[0, 20, 300, 40], block_no=1),
        PdfTextBlock(block_id="b2", page_no=1, text="附：法定代表人（单位负责人）身份证（扫描件）", bbox=[0, 100, 300, 120], block_no=2),
        PdfTextBlock(block_id="b3", page_no=1, text="身份证正反面", bbox=[0, 130, 300, 150], block_no=3),
        PdfTextBlock(block_id="b4", page_no=1, text="附：被授权人身份证等有效身份证件（扫描件）", bbox=[0, 520, 300, 540], block_no=4),
    ]
    images = [
        {"image_id": "front", "page_no": 1, "xref": 31, "width": 900, "height": 560, "rect": [10, 170, 420, 300], "ext": "png"},
        {"image_id": "back", "page_no": 1, "xref": 32, "width": 900, "height": 560, "rect": [10, 320, 420, 450], "ext": "png"},
        {"image_id": "seal", "page_no": 1, "xref": 33, "width": 180, "height": 180, "rect": [450, 220, 510, 280], "ext": "png"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
    )

    image_dir = tmp_path / "modules" / "法定代表人授权委托书" / "法定代表人（单位负责人）身份证（扫描件）" / "image_items"
    exported_json = sorted(path.name for path in image_dir.glob("*.json"))
    exported = [json.loads((image_dir / name).read_text(encoding="utf-8")) for name in exported_json]

    assert exported_json == [
        "法定代表人（单位负责人）身份证（扫描件）_图1.json",
        "法定代表人（单位负责人）身份证（扫描件）_图2.json",
    ]
    assert [item["image_id"] for item in exported] == ["front", "back"]
    assert [item["rect"] for item in exported] == [[10, 170, 420, 300], [10, 320, 420, 450]]


def test_package_module_artifacts_filters_tiny_artifact_images(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / 符合招标文件投标人资格要求的证明文件 / 符合招标公告投标人资格要求的证明文件",
            24,
            24,
            "3.6、符合招标文件投标人资格要求的证明文件",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=24, text="3.6.1、符合投标文件投标人资格要求的证明文件", bbox=[0, 80, 100, 90], block_no=1),
    ]
    tiny_artifact = {
        "image_id": "artifact-1",
        "page_no": 24,
        "xref": 1145,
        "width": 129,
        "height": 25,
        "rect": [72.36, 122.04, 118.98, 131.22],
        "ext": "png",
    }
    real_image = {
        "image_id": "real-1",
        "page_no": 24,
        "xref": 300,
        "width": 900,
        "height": 600,
        "rect": [90.0, 180.0, 420.0, 500.0],
        "ext": "jpeg",
    }

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=[tiny_artifact, real_image],
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
    )

    image_dir = (
        tmp_path
        / "modules"
        / "补充文件"
        / "符合招标文件投标人资格要求的证明文件"
        / "符合招标公告投标人资格要求的证明文件"
        / "image_items"
    )
    exported = sorted(path.name for path in image_dir.glob("*.json"))

    assert exported == ["符合投标文件投标人资格要求的证明文件_图1.json"]


def test_package_module_artifacts_filters_title_fragment_pdf_embedded_page_image(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 3、 补充文件 / 3.8、 招标文件第三章评标办法前附表之三“商务评分标准”涉及的支撑材料 / 3.8.10、 研发团队规模 / 3.8.10.1、 拥有高级及以上职称人员和高级技师人员清单",
            769,
            769,
            "3.8.10、 研发团队规模",
        )
    ]
    page_material_items = [
        {
            "item_id": "image-title-fragment",
            "item_type": "image",
            "source_type": "pdf_embedded_image",
            "page_no": 769,
            "reading_order": 12376,
            "top_y": 98.76000213623047,
            "bbox": [72.36000061035156, 98.76000213623047, 125.10000610351562, 107.94000244140625],
            "text": "",
            "payload": {
                "image_id": "image-title-fragment",
                "page_no": 769,
                "xref": 2839,
                "width": 146,
                "height": 25,
                "ext": "png",
                "rect": [72.36000061035156, 98.76000213623047, 125.10000610351562, 107.94000244140625],
            },
        }
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=[],
        tables=[],
        images=[],
        out_dir=tmp_path,
        page_material_items=page_material_items,
    )

    material_dir = (
        tmp_path
        / "modules"
        / "3、 补充文件"
        / "3.8、 招标文件第三章评标办法前附表之三“商务评分标准”涉及的支撑材料"
        / "3.8.10、 研发团队规模"
        / "3.8.10.1、 拥有高级及以上职称人员和高级技师人员清单"
    )
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))
    assert all(item.get("item_id") != "image-title-fragment" for item in ordered["items"])
    assert not (material_dir / "image_items" / "拥有高级及以上职称人员和高级技师人员清单_图1.json").exists()


def test_package_module_artifacts_creates_review_index_subfolders(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 经营状况",
            574,
            640,
            "3.8、招标文件第三章评标办法前附表之三“商务评分标准”涉及的支撑材料",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="idx-title", page_no=2, text="商务评审索引表", bbox=[0, 0, 10, 10], block_no=1),
        PdfTextBlock(block_id="b1", page_no=574, text="（1）、绩效评价结果查询", bbox=[0, 100, 100, 110], block_no=1),
        PdfTextBlock(block_id="b2", page_no=627, text="（1）、企业发展稳健", bbox=[0, 100, 100, 110], block_no=2),
    ]
    tables = [
        ParsedTable(
            table_id="index-table",
            page_no=2,
            rows=[
                ["项目", "评审要素", "评审细则", "", ""],
                ["一、履约能力评价", "经营状况", "", "详见第574页：3.8.1.1、企业履约能力强", ""],
                ["", "", "", "第627页：3.8.1.2、企业整体经营状况优良", ""],
            ],
        ),
        ParsedTable(table_id="real-table-1", page_no=574, rows=[["名称", "分值"]], bbox=[0, 120, 200, 180]),
        ParsedTable(table_id="real-table-2", page_no=627, rows=[["指标", "说明"]], bbox=[0, 120, 200, 180]),
    ]
    images = [
        {"image_id": "img-1", "page_no": 574, "xref": 200, "width": 800, "height": 600, "rect": [10, 200, 210, 340], "ext": "jpeg"},
        {"image_id": "img-2", "page_no": 627, "xref": 201, "width": 800, "height": 600, "rect": [10, 200, 210, 340], "ext": "jpeg"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "jpeg")),
    )

    base = tmp_path / "modules" / "补充文件" / "“商务评分标准”涉及的支撑材料" / "一、履约能力评价" / "经营状况"
    sub_1 = base / "3.8.1.1、企业履约能力强"
    sub_2 = base / "3.8.1.2、企业整体经营状况优良"
    assert (sub_1 / "table_items" / "绩效评价结果查询_表1.json").exists()
    assert (sub_1 / "image_items" / "绩效评价结果查询_图1.json").exists()
    assert (sub_2 / "table_items" / "企业发展稳健_表1.json").exists()
    assert (sub_2 / "image_items" / "企业发展稳健_图1.json").exists()
    parent_markdown = (base / "material.md").read_text(encoding="utf-8")
    assert parent_markdown.startswith("# 经营状况")
    assert "[3.8.1.1、企业履约能力强](3.8.1.1、企业履约能力强/material.md)" in parent_markdown
    assert "[3.8.1.2、企业整体经营状况优良](3.8.1.2、企业整体经营状况优良/material.md)" in parent_markdown


def test_review_index_subfolder_images_do_not_inherit_parent_heading_title(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 三、投标响应 / 报价质量",
            818,
            819,
            "3.8.19、投标响应-报价质量",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="idx-title", page_no=2, text="商务评审索引表", bbox=[0, 0, 10, 10], block_no=1),
        PdfTextBlock(block_id="parent-818", page_no=818, text="3.8.19、投标响应-报价质量", bbox=[0, 80, 260, 100], block_no=2),
        PdfTextBlock(block_id="parent-819", page_no=819, text="3.8.19、投标响应-报价质量", bbox=[0, 80, 260, 100], block_no=3),
    ]
    tables = [
        ParsedTable(
            table_id="index-table",
            page_no=2,
            rows=[
                ["项目", "评审要素", "评审细则", "", ""],
                ["三、投标响应", "报价质量", "", "详见第818页：3.8.19.1、报价质量说明", ""],
                ["", "", "", "第819页：3.8.19.2、报价明细截图", ""],
            ],
        )
    ]
    images = [
        {"image_id": "img-1", "page_no": 818, "xref": 401, "width": 800, "height": 600, "rect": [10, 160, 210, 300], "ext": "png"},
        {"image_id": "img-2", "page_no": 819, "xref": 402, "width": 800, "height": 600, "rect": [10, 160, 210, 300], "ext": "png"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
    )

    base = tmp_path / "modules" / "补充文件" / "“商务评分标准”涉及的支撑材料" / "三、投标响应" / "报价质量"
    sub_1 = base / "3.8.19.1、报价质量说明"
    sub_2 = base / "3.8.19.2、报价明细截图"

    assert (sub_1 / "image_items" / "报价质量说明_图1.json").exists()
    assert (sub_2 / "image_items" / "报价明细截图_图1.json").exists()
    assert not (sub_1 / "image_items" / "投标响应-报价质量_图1.json").exists()
    assert not (sub_2 / "image_items" / "投标响应-报价质量_图2.json").exists()


def test_leaf_material_images_do_not_inherit_parent_container_title(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 3、 补充文件 / 3.8、 招标文件第三章评标办法前附表之三“商务评分标准”涉及的支撑材料 / 3.8.18、 实体清单应对举措 / 3.8.18.2、 税收优惠",
            814,
            815,
            "3.8.18、 实体清单应对举措",
        )
    ]
    images = [
        {"image_id": "tax-1", "page_no": 814, "xref": 501, "width": 800, "height": 600, "rect": [72, 305, 523, 751], "ext": "png"},
        {"image_id": "tax-2", "page_no": 815, "xref": 502, "width": 800, "height": 600, "rect": [72, 72, 523, 403], "ext": "jpeg"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=[],
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
    )

    material_dir = (
        tmp_path
        / "modules"
        / "3、 补充文件"
        / "3.8、 招标文件第三章评标办法前附表之三“商务评分标准”涉及的支撑材料"
        / "3.8.18、 实体清单应对举措"
        / "3.8.18.2、 税收优惠"
    )
    image_dir = material_dir / "image_items"
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))

    assert (image_dir / "税收优惠_图1.json").exists()
    assert (image_dir / "税收优惠_图1.png").exists()
    assert (image_dir / "税收优惠_图2.json").exists()
    assert (image_dir / "税收优惠_图2.jpeg").exists()
    assert not (image_dir / "3.8.18、 实体清单应对举措_图1.json").exists()
    assert [item["image_title"] for item in ordered["items"] if item["type"] == "image"] == [
        "税收优惠_图1",
        "税收优惠_图2",
    ]


def test_package_module_artifacts_expands_pages_using_review_index_ranges(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 经营状况",
            574,
            627,
            "3.8、招标文件第三章评标办法前附表之三“商务评分标准”涉及的支撑材料",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="idx-title", page_no=2, text="商务评审索引表", bbox=[0, 0, 10, 10], block_no=1),
        PdfTextBlock(block_id="b1", page_no=628, text="（2）、具备优秀的团队", bbox=[0, 100, 100, 110], block_no=1),
    ]
    tables = [
        ParsedTable(
            table_id="index-table",
            page_no=2,
            rows=[
                ["项目", "评审要素", "评审细则", "", ""],
                ["一、履约能力评价", "经营状况", "", "详见第574页：3.8.1.1、企业履约能力强", ""],
                ["", "", "", "第627页：3.8.1.2、企业整体经营状况优良", ""],
                ["", "售后服务", "", "详见第641页：3.8.2、售后服务", ""],
            ],
        ),
        ParsedTable(table_id="real-table-2", page_no=628, rows=[["指标", "说明"]], bbox=[0, 120, 200, 180]),
    ]
    images = [
        {"image_id": "img-2", "page_no": 628, "xref": 201, "width": 800, "height": 600, "rect": [10, 200, 210, 340], "ext": "jpeg"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "jpeg")),
    )

    sub_2 = (
        tmp_path
        / "modules"
        / "补充文件"
        / "“商务评分标准”涉及的支撑材料"
        / "一、履约能力评价"
        / "经营状况"
        / "3.8.1.2、企业整体经营状况优良"
    )
    assert (sub_2 / "table_items" / "具备优秀的团队_表1.json").exists()
    assert (sub_2 / "image_items" / "具备优秀的团队_图1.json").exists()


def test_package_module_artifacts_applies_review_index_to_other_score_elements(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 一、履约能力评价 / 售后服务",
            641,
            641,
            "3.8、招标文件第三章评标办法前附表之三“商务评分标准”涉及的支撑材料",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="idx-title", page_no=2, text="商务评审索引表", bbox=[0, 0, 10, 10], block_no=1),
        PdfTextBlock(block_id="b1", page_no=641, text="（1）、制定完善的售后服务方案", bbox=[0, 100, 100, 110], block_no=1),
    ]
    tables = [
        ParsedTable(
            table_id="index-table",
            page_no=2,
            rows=[
                ["项目", "评审要素", "评审细则", "", ""],
                ["一、履约能力评价", "经营状况", "", "详见第574页：3.8.1.1、企业履约能力强", ""],
                ["", "售后服务", "", "详见第641页：3.8.2、售后服务", ""],
            ],
        ),
        ParsedTable(table_id="real-table-1", page_no=641, rows=[["服务", "内容"]], bbox=[0, 120, 200, 180]),
    ]
    images = [
        {"image_id": "img-1", "page_no": 641, "xref": 201, "width": 800, "height": 600, "rect": [10, 200, 210, 340], "ext": "jpeg"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "jpeg")),
    )

    subfolder = (
        tmp_path
        / "modules"
        / "补充文件"
        / "“商务评分标准”涉及的支撑材料"
        / "一、履约能力评价"
        / "售后服务"
        / "3.8.2、售后服务"
    )
    assert (subfolder / "table_items" / "制定完善的售后服务方案_表1.json").exists()
    assert (subfolder / "image_items" / "制定完善的售后服务方案_图1.json").exists()


def test_package_module_artifacts_does_not_apply_review_index_outside_score_materials(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / 企业名称变更",
            574,
            574,
            "3.9、投标人自述的企业名称变更原因说明",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="idx-title", page_no=2, text="商务评审索引表", bbox=[0, 0, 10, 10], block_no=1),
        PdfTextBlock(block_id="b1", page_no=574, text="3.9.2、2007年企业名称变更证明材料", bbox=[0, 100, 100, 110], block_no=1),
    ]
    tables = [
        ParsedTable(
            table_id="index-table",
            page_no=2,
            rows=[
                ["项目", "评审要素", "评审细则", "", ""],
                ["一、履约能力评价", "经营状况", "", "详见第574页：3.8.1.1、企业履约能力强", ""],
            ],
        )
    ]
    images = [
        {"image_id": "img-1", "page_no": 574, "xref": 201, "width": 800, "height": 600, "rect": [10, 200, 210, 340], "ext": "jpeg"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "jpeg")),
    )

    section_dir = tmp_path / "modules" / "补充文件" / "企业名称变更"
    assert (section_dir / "image_items" / "2007年企业名称变更证明材料_图1.json").exists()
    assert not (section_dir / "3.8.1.1、企业履约能力强").exists()


def test_package_module_artifacts_maps_review_index_elements_to_excel_paths(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 研发团队规模",
            769,
            770,
            "3.8、招标文件第三章评标办法前附表之三“商务评分标准”涉及的支撑材料",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="idx-title", page_no=2, text="商务评审索引表", bbox=[0, 0, 10, 10], block_no=1),
        PdfTextBlock(block_id="b1", page_no=771, text="3.8.10.2、职称证书37人", bbox=[0, 100, 100, 110], block_no=1),
    ]
    tables = [
        ParsedTable(
            table_id="index-table",
            page_no=2,
            rows=[
                ["项目", "评审要素", "评审细则", "", ""],
                ["一、履约能力评价", "经营状况", "", "详见第574页：3.8.1.1、企业履约能力强", ""],
                ["", "研发团队规模", "", "详见第769页：3.8.10、研发团队规模", ""],
                ["", "", "", "第771页：3.8.10.2、职称证书37人", ""],
            ],
        ),
        ParsedTable(table_id="real-table-1", page_no=771, rows=[["姓名", "职称"]], bbox=[0, 120, 200, 180]),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
        planned_section_paths=[
            "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 研发团队规模",
        ],
    )

    subfolder = (
        tmp_path
        / "modules"
        / "补充文件"
        / "“商务评分标准”涉及的支撑材料"
        / "二、高质量发展评价"
        / "研发团队规模"
        / "3.8.10.2、职称证书37人"
    )
    assert (subfolder / "table_items" / "职称证书37人_表1.json").exists()


def test_package_module_artifacts_uses_heading_y_bounds_for_cross_page_index_items(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 绿色发展规划",
            706,
            707,
            "3.8、招标文件第三章评标办法前附表之三“商务评分标准”涉及的支撑材料",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="idx-title", page_no=2, text="商务评审索引表", bbox=[0, 0, 10, 10], block_no=1),
        PdfTextBlock(block_id="h1", page_no=706, text="3.8.4.1、绿色发展顶层规划", bbox=[0, 100, 100, 110], block_no=1),
        PdfTextBlock(block_id="h2", page_no=707, text="3.8.4.2、绿色发展执行情况", bbox=[0, 200, 100, 210], block_no=2),
        PdfTextBlock(block_id="before-h2", page_no=707, text="上一小节跨页延续内容", bbox=[0, 120, 100, 130], block_no=3),
        PdfTextBlock(block_id="after-h2", page_no=707, text="下一小节内容", bbox=[0, 240, 100, 250], block_no=4),
    ]
    tables = [
        ParsedTable(
            table_id="index-table",
            page_no=2,
            rows=[
                ["项目", "评审要素", "评审细则", "", ""],
                ["二、高质量发展评价", "绿色发展规划", "", "详见第706页：3.8.4.1、绿色发展顶层规划", ""],
                ["", "", "", "第707页：3.8.4.2、绿色发展执行情况", ""],
            ],
        ),
        ParsedTable(table_id="cross-page-table", page_no=707, rows=[["跨页", "内容"]], bbox=[0, 140, 200, 180]),
        ParsedTable(table_id="next-table", page_no=707, rows=[["执行", "情况"]], bbox=[0, 260, 200, 300]),
    ]
    images = [
        {"image_id": "img-before", "page_no": 707, "xref": 301, "width": 800, "height": 600, "rect": [10, 150, 210, 190], "ext": "jpeg"},
        {"image_id": "img-after", "page_no": 707, "xref": 302, "width": 800, "height": 600, "rect": [10, 270, 210, 330], "ext": "jpeg"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "jpeg")),
    )

    base = tmp_path / "modules" / "补充文件" / "“商务评分标准”涉及的支撑材料" / "二、高质量发展评价" / "绿色发展规划"
    sub_1 = base / "3.8.4.1、绿色发展顶层规划"
    sub_2 = base / "3.8.4.2、绿色发展执行情况"
    assert (sub_1 / "table_items" / "绿色发展顶层规划_表1.json").exists()
    assert (sub_1 / "image_items" / "绿色发展顶层规划_图1.json").exists()
    assert (sub_2 / "table_items" / "绿色发展执行情况_表1.json").exists()
    assert (sub_2 / "image_items" / "绿色发展执行情况_图1.json").exists()


def test_package_module_artifacts_writes_text_items_for_review_index_text_sections(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 创新激励机制、供应链保障措施",
            803,
            807,
            "3.8、招标文件第三章评标办法前附表之三“商务评分标准”涉及的支撑材料",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="idx-title", page_no=2, text="商务评审索引表", bbox=[0, 0, 10, 10], block_no=1),
        PdfTextBlock(block_id="h1", page_no=803, text="3.8.13.2、供应链保障措施", bbox=[0, 100, 100, 110], block_no=1),
        PdfTextBlock(
            block_id="p1",
            page_no=803,
            text="公司建立供应链风险识别机制，\n并制定供应链保障措施。",
            bbox=[0, 130, 200, 180],
            block_no=2,
        ),
        PdfTextBlock(block_id="h2", page_no=808, text="3.8.14、数智化评价", bbox=[0, 100, 100, 110], block_no=3),
    ]
    tables = [
        ParsedTable(
            table_id="index-table",
            page_no=2,
            rows=[
                ["项目", "评审要素", "评审细则", "", ""],
                ["二、高质量发展评价", "创新激励机制、供应链保障措施", "", "详见第803页：3.8.13.2、供应链保障措施", ""],
                ["", "数智化评价", "", "详见第808页：3.8.14、数智化评价", ""],
            ],
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    text_dir = (
        tmp_path
        / "modules"
        / "补充文件"
        / "“商务评分标准”涉及的支撑材料"
        / "二、高质量发展评价"
        / "创新激励机制、供应链保障措施"
        / "3.8.13.2、供应链保障措施"
        / "text_items"
    )
    json_path = text_dir / "供应链保障措施.json"
    md_path = text_dir / "供应链保障措施.md"

    assert json_path.exists()
    assert md_path.exists()
    assert "供应链风险识别机制" in md_path.read_text(encoding="utf-8")


def test_package_module_artifacts_writes_complete_material_package_for_review_index_section(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 创新激励机制、供应链保障措施",
            3,
            4,
            "3.8、招标文件第三章评标办法前附表之三“商务评分标准”涉及的支撑材料",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="idx-title", page_no=2, text="商务评审索引表", bbox=[0, 0, 10, 10], block_no=1),
        PdfTextBlock(block_id="h1", page_no=3, text="3.8.13.2、供应链保障措施", bbox=[0, 100, 100, 110], block_no=1),
        PdfTextBlock(block_id="p1", page_no=3, text="供应链保障正文", bbox=[0, 130, 200, 180], block_no=2),
        PdfTextBlock(block_id="h2", page_no=5, text="3.8.14、数智化评价", bbox=[0, 100, 100, 110], block_no=3),
    ]
    tables = [
        ParsedTable(
            table_id="index-table",
            page_no=2,
            rows=[
                ["项目", "评审要素", "评审细则", "", ""],
                ["二、高质量发展评价", "创新激励机制、供应链保障措施", "", "详见第3页：3.8.13.2、供应链保障措施", ""],
                ["", "数智化评价", "", "详见第5页：3.8.14、数智化评价", ""],
            ],
        ),
        ParsedTable(table_id="material-table", page_no=3, rows=[["措施", "说明"]], bbox=[10, 190, 200, 240]),
    ]
    images = [
        {"image_id": "img-1", "page_no": 3, "xref": 10, "width": 500, "height": 400, "rect": [10, 260, 150, 360], "ext": "png"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
        planned_section_paths=[
            "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 创新激励机制、供应链保障措施",
            "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 数智化评价",
        ],
    )

    material_dir = (
        tmp_path
        / "modules"
        / "补充文件"
        / "“商务评分标准”涉及的支撑材料"
        / "二、高质量发展评价"
        / "创新激励机制、供应链保障措施"
        / "3.8.13.2、供应链保障措施"
    )
    meta = json.loads((material_dir / "material_meta.json").read_text(encoding="utf-8"))
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))

    assert meta["source_page_start"] == 3
    assert meta["source_page_end"] == 5
    assert meta["source_end_y"] == 100.0
    assert meta["original_capture"]["available"] is False
    assert meta["text_item_count"] == 1
    assert meta["table_item_count"] == 1
    assert meta["image_item_count"] == 1
    assert meta["material_path"] == "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 创新激励机制、供应链保障措施 / 3.8.13.2、供应链保障措施"
    assert meta["rule_section_path"] == "商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 创新激励机制、供应链保障措施"
    assert meta["rule_module_name"] == "补充文件"
    assert meta["material_types"] == ["text", "table", "image"]
    assert meta["dominant_material_type"] == "mixed"
    assert meta["raw_context_title"] == "3.8.13.2、供应链保障措施"
    assert meta["title_mapping"]["raw_context_title"] == "3.8.13.2、供应链保障措施"
    assert meta["title_mapping"]["material_title"] == "3.8.13.2、供应链保障措施"
    assert ordered["material_path"] == meta["material_path"]
    assert ordered["rule_section_path"] == meta["rule_section_path"]
    assert ordered["material_types"] == ["text", "table", "image"]
    assert ordered["dominant_material_type"] == "mixed"
    assert [item["type"] for item in ordered["items"]] == ["text", "text", "table", "image"]
    assert ordered["items"][0]["block_id"] == "h1"
    assert [item["order"] for item in ordered["items"]] == [1, 2, 3, 4]
    assert ordered["items"][0]["item_type"] == "text"
    assert ordered["items"][0]["payload_ref"] == "text_items/供应链保障措施.json"
    assert ordered["items"][0]["nearest_heading"] == "3.8.13.2、供应链保障措施"
    assert ordered["items"][0]["rule_section_path"] == meta["rule_section_path"]
    assert ordered["items"][2]["item_type"] == "table"
    assert ordered["items"][2]["payload_ref"].endswith("json")
    assert ordered["items"][3]["item_type"] == "image"
    assert ordered["items"][3]["payload_ref"].endswith("json")
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    assert "3.8.13.2、供应链保障措施" in material_md
    assert "供应链保障正文" in material_md
    assert "![供应链保障措施_图1](image_items/供应链保障措施_图1.png)" in material_md
    assert meta["material_markdown_path"] == "material.md"


def test_package_module_artifacts_writes_complete_section_markdown_with_table_and_image_refs(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / 企业名称变更",
            1,
            1,
            "企业名称变更",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="h1", page_no=1, text="企业名称变更", bbox=[0, 80, 200, 100], block_no=1, font_size=16),
        PdfTextBlock(block_id="p1", page_no=1, text="公司名称已完成变更。", bbox=[0, 120, 300, 140], block_no=2, font_size=10),
        PdfTextBlock(block_id="p2", page_no=1, text="相关证明如下。", bbox=[0, 320, 300, 340], block_no=3, font_size=10),
    ]
    tables = [
        ParsedTable(table_id="name-change-table", page_no=1, rows=[["变更前", "变更后"], ["旧公司", "新公司"]], bbox=[10, 180, 300, 260]),
    ]
    images = [
        {"image_id": "proof-img", "page_no": 1, "xref": 20, "width": 600, "height": 500, "rect": [10, 360, 300, 520], "ext": "png"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
    )

    material_dir = tmp_path / "modules" / "补充文件" / "企业名称变更"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")

    assert material_md.startswith("# 企业名称变更")
    assert "公司名称已完成变更。" in material_md
    assert "相关证明如下。" in material_md
    assert "| 变更前 | 变更后 |" in material_md
    assert "| --- | --- |" in material_md
    assert "| 旧公司 | 新公司 |" in material_md
    assert "[表格：企业名称变更_表1]" not in material_md
    assert "![企业名称变更_图1](image_items/企业名称变更_图1.png)" in material_md
    assert material_md.index("公司名称已完成变更。") < material_md.index("| 变更前 | 变更后 |")
    assert material_md.index("| 旧公司 | 新公司 |") < material_md.index("相关证明如下。")
    assert material_md.index("相关证明如下。") < material_md.index("![企业名称变更_图1]")
    assert (material_dir / "table_items" / "企业名称变更_表1.json").exists()
    assert (material_dir / "image_items" / "企业名称变更_图1.png").exists()


def test_package_module_artifacts_writes_full_document_markdown_in_pdf_order(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "PDF / 2、 专项投标文件 / 2.1、 业绩文件",
            1,
            1,
            "2.1、 业绩文件",
        ),
        _candidate(
            "PDF / 2、 专项投标文件 / 2.1、 业绩文件 / 2.1.1、 供货及运行业绩表",
            1,
            1,
            "2.1.1、 供货及运行业绩表",
        ),
        _candidate(
            "PDF / 2、 专项投标文件 / 2.2、 其他文件",
            2,
            2,
            "2.2、 其他文件",
        ),
    ]
    candidates[0].material_evidence = {"source": "pdf_toc_leaf", "start_y": 80.0, "end_y": None, "start_block_id": "parent-title"}
    candidates[1].material_evidence = {"source": "pdf_toc_leaf", "start_y": 180.0, "end_y": None, "start_block_id": "child-title"}
    candidates[2].material_evidence = {"source": "pdf_toc_leaf", "start_y": 80.0, "end_y": None, "start_block_id": "other-title"}
    blocks = [
        PdfTextBlock(block_id="parent-title", page_no=1, text="2.1、业绩文件", bbox=[0, 80, 200, 100], block_no=1),
        PdfTextBlock(block_id="parent-preface", page_no=1, text="业绩文件总体说明。", bbox=[0, 120, 400, 140], block_no=2),
        PdfTextBlock(block_id="child-title", page_no=1, text="2.1.1、供货及运行业绩表", bbox=[0, 180, 400, 200], block_no=3),
        PdfTextBlock(block_id="child-body", page_no=1, text="供货及运行业绩表正文。", bbox=[0, 220, 400, 240], block_no=4),
        PdfTextBlock(block_id="other-title", page_no=2, text="2.2、其他文件", bbox=[0, 80, 200, 100], block_no=5),
        PdfTextBlock(block_id="other-body", page_no=2, text="其他文件正文。", bbox=[0, 120, 400, 140], block_no=6),
    ]
    tables = [
        ParsedTable(table_id="child-table", page_no=1, rows=[["序号", "工程名称"], ["1", "国网湖北超高压公司"]], bbox=[10, 260, 500, 360]),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
        top_level_modules=["2、 专项投标文件"],
        planned_section_paths=[candidate.section_path for candidate in candidates],
    )

    full_md = (tmp_path / "full_document.md").read_text(encoding="utf-8")
    assert full_md.startswith("# 解析全文")
    assert "业绩文件总体说明。" in full_md
    assert "供货及运行业绩表正文。" in full_md
    assert "| 序号 | 工程名称 |" in full_md
    assert "其他文件正文。" in full_md
    assert full_md.index("业绩文件总体说明。") < full_md.index("供货及运行业绩表正文。")
    assert full_md.index("供货及运行业绩表正文。") < full_md.index("其他文件正文。")
    assert full_md.count("| 序号 | 工程名称 |") == 1


def test_full_document_markdown_uses_root_relative_image_paths(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 3、 补充文件 / 3.5、 有效的税务登记证明（扫描件）",
            5,
            5,
            "3.5、 有效的税务登记证明（扫描件）",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="title", page_no=5, text="3.5、有效的税务登记证明（扫描件）", bbox=[0, 80, 400, 100], block_no=1),
        PdfTextBlock(block_id="body", page_no=5, text="我公司已提供三证合一后的营业执照副本扫描件。", bbox=[0, 120, 500, 140], block_no=2),
    ]
    images = [
        {"image_id": "tax-proof", "page_no": 5, "xref": 20, "width": 600, "height": 500, "rect": [10, 180, 300, 520], "ext": "png"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
    )

    full_md = (tmp_path / "full_document.md").read_text(encoding="utf-8")
    assert "我公司已提供三证合一后的营业执照副本扫描件。" in full_md
    assert "![有效的税务登记证明（扫描件）_图1](modules/3、 补充文件/3.5、 有效的税务登记证明（扫描件）/image_items/有效的税务登记证明（扫描件）_图1.png)" in full_md


def test_package_module_artifacts_omits_text_blocks_inside_rendered_table(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / 财务状况",
            1,
            1,
            "财务状况",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="h1", page_no=1, text="财务状况", bbox=[0, 80, 200, 100], block_no=1),
        PdfTextBlock(block_id="intro", page_no=1, text="以下为财务状况表。", bbox=[0, 120, 300, 140], block_no=2),
        PdfTextBlock(block_id="cell1", page_no=1, text="年份", bbox=[20, 182, 80, 198], block_no=3),
        PdfTextBlock(block_id="cell2", page_no=1, text="2024", bbox=[90, 182, 140, 198], block_no=4),
        PdfTextBlock(block_id="after", page_no=1, text="表格之后的说明。", bbox=[0, 280, 300, 300], block_no=5),
    ]
    tables = [
        ParsedTable(table_id="finance-table", page_no=1, rows=[["年份", "金额"], ["2024", "100"]], bbox=[10, 170, 300, 250]),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_md = (tmp_path / "modules" / "补充文件" / "财务状况" / "material.md").read_text(encoding="utf-8")
    assert "以下为财务状况表。" in material_md
    assert "| 年份 | 金额 |" in material_md
    assert "| 2024 | 100 |" in material_md
    assert "表格之后的说明。" in material_md
    assert material_md.count("年份") == 1
    assert material_md.count("2024") == 1


def test_package_module_artifacts_keeps_table_caption_even_when_inside_table_bbox(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / 财务状况",
            1,
            1,
            "财务状况",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="h1", page_no=1, text="财务状况", bbox=[0, 80, 200, 100], block_no=1),
        PdfTextBlock(block_id="caption", page_no=1, text="表1 近三年财务状况汇总表", bbox=[20, 172, 260, 188], block_no=2),
        PdfTextBlock(block_id="cell1", page_no=1, text="年份", bbox=[20, 202, 80, 218], block_no=3),
        PdfTextBlock(block_id="cell2", page_no=1, text="2024", bbox=[90, 202, 140, 218], block_no=4),
    ]
    tables = [
        ParsedTable(table_id="finance-table", page_no=1, rows=[["年份", "金额"], ["2024", "100"]], bbox=[10, 165, 300, 250]),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_md = (tmp_path / "modules" / "补充文件" / "财务状况" / "material.md").read_text(encoding="utf-8")
    assert "表1 近三年财务状况汇总表" in material_md
    assert "| 年份 | 金额 |" in material_md
    assert material_md.count("年份") == 1
    assert material_md.count("2024") == 1


def test_package_module_artifacts_omits_overlapping_table_text_even_when_center_outside_bbox(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / 财务状况",
            1,
            1,
            "财务状况",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="h1", page_no=1, text="财务状况", bbox=[0, 80, 200, 100], block_no=1),
        PdfTextBlock(block_id="amount", page_no=1, text="金额", bbox=[280, 202, 360, 218], block_no=2),
    ]
    tables = [
        ParsedTable(table_id="finance-table", page_no=1, rows=[["年份", "金额"], ["2024", "100"]], bbox=[10, 170, 300, 250]),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_md = (tmp_path / "modules" / "补充文件" / "财务状况" / "material.md").read_text(encoding="utf-8")
    assert "| 年份 | 金额 |" in material_md
    assert material_md.count("金额") == 1


def test_package_module_artifacts_omits_any_text_inside_table_bbox_even_without_cell_match(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / 投标人与国家电网公司系统人员关系说明",
            1,
            1,
            "投标人与国家电网公司系统人员关系说明",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="h1", page_no=1, text="投标人与国家电网公司系统人员关系说明", bbox=[0, 80, 300, 100], block_no=1),
        PdfTextBlock(block_id="intro", page_no=1, text="具体情况如下。", bbox=[0, 120, 300, 140], block_no=2),
        PdfTextBlock(block_id="inside", page_no=1, text="国家电网公司系统人员基本信息", bbox=[40, 180, 260, 198], block_no=3),
        PdfTextBlock(block_id="after", page_no=1, text="表格之后说明。", bbox=[0, 280, 300, 300], block_no=4),
    ]
    tables = [
        ParsedTable(
            table_id="relation-table",
            page_no=1,
            rows=[["人员姓名", "性别"], ["无", "—"]],
            bbox=[10, 160, 500, 250],
        ),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_md = (
        tmp_path
        / "modules"
        / "补充文件"
        / "投标人与国家电网公司系统人员关系说明"
        / "material.md"
    ).read_text(encoding="utf-8")
    assert "具体情况如下。" in material_md
    assert "| 人员姓名 | 性别 |" in material_md
    assert "国家电网公司系统人员基本信息" not in material_md
    assert "表格之后说明。" in material_md


def test_package_module_artifacts_ignores_page_stream_tables_without_table_items(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / 投标保证金明细表",
            1,
            1,
            "投标保证金明细表",
        )
    ]
    page_material_items = [
        {
            "item_id": "stream-text",
            "item_type": "text",
            "source_type": "pdf_text",
            "page_no": 1,
            "top_y": 100,
            "bbox": [0, 100, 300, 120],
            "text": "表格前说明。",
        },
        {
            "item_id": "stream-table",
            "item_type": "table",
            "source_type": "page_material_stream",
            "page_no": 1,
            "top_y": 130,
            "bbox": [10, 130, 500, 260],
            "payload": {"rows": [["汇款流水号", "包号"], ["218143314-024", "包05"]]},
        },
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=[],
        tables=[],
        images=[],
        out_dir=tmp_path,
        page_material_items=page_material_items,
    )

    material_dir = tmp_path / "modules" / "补充文件" / "投标保证金明细表"
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))["items"]
    meta = json.loads((material_dir / "material_meta.json").read_text(encoding="utf-8"))
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")

    assert [item["item_type"] for item in ordered] == ["text"]
    assert meta["material_types"] == ["text"]
    assert "表格前说明。" in material_md
    assert "汇款流水号" not in material_md


def test_package_module_artifacts_omits_page_stream_text_inside_table_items(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / 投标保证金明细表",
            1,
            1,
            "投标保证金明细表",
        )
    ]
    tables = [
        ParsedTable(
            table_id="deposit-table",
            page_no=1,
            rows=[["汇款流水号", "包号"], ["218143314-024", "包05"]],
            bbox=[10, 130, 500, 260],
        )
    ]
    page_material_items = [
        {
            "item_id": "stream-inside-text",
            "item_type": "text",
            "source_type": "pdf_text",
            "page_no": 1,
            "top_y": 160,
            "bbox": [40, 160, 220, 180],
            "text": "这是表格区域里的拆散文字",
        },
        {
            "item_id": "stream-after-text",
            "item_type": "text",
            "source_type": "pdf_text",
            "page_no": 1,
            "top_y": 300,
            "bbox": [0, 300, 300, 320],
            "text": "表格之后说明。",
        },
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=[],
        tables=tables,
        images=[],
        out_dir=tmp_path,
        page_material_items=page_material_items,
    )

    material_dir = tmp_path / "modules" / "补充文件" / "投标保证金明细表"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))["items"]
    assert "| 汇款流水号 | 包号 |" in material_md
    assert "这是表格区域里的拆散文字" not in material_md
    assert "表格之后说明。" in material_md
    suppressed = [item for item in ordered if item.get("text") == "这是表格区域里的拆散文字"]
    assert suppressed
    assert suppressed[0]["material_role"] == "table_text"
    assert suppressed[0]["suppressed_by_table_id"] == "deposit-table"
    assert suppressed[0]["suppressed_reason"] == "table_geometry"


def test_package_module_artifacts_keeps_form_fields_above_expanded_table_region(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "技术文件 / 技术偏差表",
            1,
            1,
            "技术偏差表",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="技术偏差表", bbox=[0, 80, 200, 100], block_no=1),
        PdfTextBlock(block_id="project", page_no=1, text="项目名称：国网甘肃电力2026 年新增第一次物资公开招标采购", bbox=[72, 140, 420, 152], block_no=2),
        PdfTextBlock(block_id="package-code", page_no=1, text="分标编号：272608-1102000-9998", bbox=[72, 181, 230, 192], block_no=3),
        PdfTextBlock(block_id="package-name", page_no=1, text="包名称：测控及在线监测系统包05；包号：包05", bbox=[72, 201, 304, 212], block_no=4),
        PdfTextBlock(block_id="inside-cell", page_no=1, text="序号", bbox=[80, 235, 110, 248], block_no=5),
    ]
    tables = [
        ParsedTable(
            table_id="deviation-table",
            page_no=1,
            rows=[["序号", "偏差事项"], ["1", "最低检测限值"]],
            bbox=[35, 170, 560, 300],
            table_region_bbox=[35, 220, 560, 300],
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_dir = tmp_path / "modules" / "技术偏差表"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))["items"]

    assert "项目名称：国网甘肃电力2026 年新增第一次物资公开招标采购" in material_md
    assert "分标编号：272608-1102000-9998" in material_md
    assert "包名称：测控及在线监测系统包05；包号：包05" in material_md
    assert "| 序号 | 偏差事项 |" in material_md
    assert material_md.count("序号") == 1

    form_fields = {item.get("block_id"): item for item in ordered if item.get("block_id") in {"package-code", "package-name"}}
    assert form_fields["package-code"]["material_role"] == "body_text"
    assert form_fields["package-name"]["material_role"] == "body_text"
    suppressed = [item for item in ordered if item.get("block_id") == "inside-cell"]
    assert suppressed
    assert suppressed[0]["material_role"] == "table_text"
    assert suppressed[0]["suppressed_reason"] == "table_geometry"


def test_package_module_artifacts_keeps_intro_text_above_overexpanded_table_bbox(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / 科研经费占比",
            1,
            1,
            "科研经费占比",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="科研经费占比", bbox=[20, 70, 220, 92], block_no=1),
        PdfTextBlock(block_id="intro-1", page_no=1, text="近三年，公司持续保持研发投入，增强研发实力。", bbox=[72, 112, 500, 124], block_no=2),
        PdfTextBlock(block_id="intro-2", page_no=1, text="公司近3年科研投入占比均在14%以上：", bbox=[72, 132, 500, 144], block_no=3),
        PdfTextBlock(block_id="header", page_no=1, text="项目\n2024 年\n2023 年\n2022 年", bbox=[76, 155, 466, 166], block_no=4),
        PdfTextBlock(block_id="data", page_no=1, text="研发投入金额（元）\n152784788.03\n160859217.25\n164908106.75", bbox=[76, 173, 495, 184], block_no=5),
    ]
    tables = [
        ParsedTable(
            table_id="research-table",
            page_no=1,
            rows=[
                ["项目", "2024 年", "2023 年", "2022 年"],
                ["研发投入金额（元）", "152784788.03", "160859217.25", "164908106.75"],
            ],
            bbox=[35, 104, 558, 805],
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_md = (tmp_path / "modules" / "补充文件" / "科研经费占比" / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((tmp_path / "modules" / "补充文件" / "科研经费占比" / "ordered_material.json").read_text(encoding="utf-8"))["items"]

    assert "近三年，公司持续保持研发投入，增强研发实力。" in material_md
    assert "公司近3年科研投入占比均在14%以上：" in material_md
    assert material_md.index("公司近3年科研投入占比均在14%以上：") < material_md.index("| 项目 | 2024 年 | 2023 年 | 2022 年 |")
    assert material_md.count("项目") == 1
    assert [item["item_type"] for item in ordered[:4]] == ["text", "text", "text", "table"]


def test_package_module_artifacts_keeps_ocr_table_metadata_rows_and_skips_duplicate_text(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "技术文件 / 技术特性参数表",
            1,
            1,
            "技术特性参数表",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="技术特性参数表", bbox=[20, 70, 220, 92], block_no=1),
        PdfTextBlock(block_id="project", page_no=1, text="项目名称：国网辽宁省电力有限公司 2024 年第一次配网物资协议库存招标采购项目", bbox=[72, 112, 500, 124], block_no=2),
        PdfTextBlock(block_id="bid", page_no=1, text="招标编号：2224AA", bbox=[72, 132, 220, 144], block_no=3),
        PdfTextBlock(block_id="header", page_no=1, text="序号\n名称\n单位\n招标人要求值\n投标人保证值", bbox=[76, 170, 466, 184], block_no=4),
        PdfTextBlock(block_id="row", page_no=1, text="1\n绝缘件公称直径\nmm\n255\n255", bbox=[76, 194, 466, 208], block_no=5),
    ]
    tables = [
        ParsedTable(
            table_id="tech-table",
            page_no=1,
            rows=[
                ["项目名称：国网辽宁省电力有限公司 2024 年第一次配网物资协议库存招标采购项目", "", "", "", ""],
                ["招标编号：2224AA", "", "", "", ""],
                ["序号", "名称", "单位", "招标人要求值", "投标人保证值"],
                ["1", "绝缘件公称直径", "mm", "255", "255"],
            ],
            bbox=[35, 104, 558, 300],
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_md = (tmp_path / "modules" / "技术特性参数表" / "material.md").read_text(encoding="utf-8")

    assert "项目名称：国网辽宁省电力有限公司 2024 年第一次配网物资协议库存招标采购项目" in material_md
    assert "招标编号：2224AA" in material_md
    assert "| 序号 | 名称 | 单位 | 招标人要求值 | 投标人保证值 |" in material_md
    assert "| 项目名称：国网辽宁省电力有限公司 2024 年第一次配网物资协议库存招标采购项目 |  |  |  |  |" in material_md
    assert "| 招标编号：2224AA |  |  |  |  |" in material_md
    assert material_md.count("项目名称：国网辽宁省电力有限公司 2024 年第一次配网物资协议库存招标采购项目") == 1
    assert material_md.count("招标编号：2224AA") == 1


def test_package_module_artifacts_keeps_table_leading_metadata_in_table_when_not_duplicated(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "技术文件 / 技术特性参数表",
            1,
            1,
            "技术特性参数表",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="技术特性参数表", bbox=[20, 70, 220, 92], block_no=1),
        PdfTextBlock(block_id="header", page_no=1, text="序号\n名称\n单位\n招标人要求值\n投标人保证值", bbox=[76, 170, 466, 184], block_no=2),
        PdfTextBlock(block_id="row", page_no=1, text="1\n绝缘件公称直径\nmm\n255\n255", bbox=[76, 194, 466, 208], block_no=3),
    ]
    tables = [
        ParsedTable(
            table_id="tech-table",
            page_no=1,
            rows=[
                ["招标编号：2224AA", "", "分标名称：交流盘形悬式瓷绝缘子", "", ""],
                ["分标编号：2224AA-1405025-3401", "", "包名称：包 1-包 6", "包号：包 1-包 6", ""],
                ["项目单位：国网辽宁省电力有限公司", "", "项目名称：国网辽宁省电力有限公司 2024 年第一次配网物资协议库存招标采购项目", "", ""],
                ["序号", "名称", "单位", "招标人要求值", "投标人保证值"],
                ["1", "绝缘件公称直径", "mm", "255", "255"],
            ],
            bbox=[35, 104, 558, 300],
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_md = (tmp_path / "modules" / "技术特性参数表" / "material.md").read_text(encoding="utf-8")

    assert "| 招标编号：2224AA |  | 分标名称：交流盘形悬式瓷绝缘子 |  |  |" in material_md
    assert "| 分标编号：2224AA-1405025-3401 |  | 包名称：包 1-包 6 | 包号：包 1-包 6 |  |" in material_md
    assert "| 项目单位：国网辽宁省电力有限公司 |  | 项目名称：国网辽宁省电力有限公司 2024 年第一次配网物资协议库存招标采购项目 |  |  |" in material_md
    assert material_md.index("| 项目单位：国网辽宁省电力有限公司") < material_md.index("| 序号 | 名称 | 单位 | 招标人要求值 | 投标人保证值 |")


def test_package_module_artifacts_keeps_preceding_text_and_trims_duplicate_leading_table_rows(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "技术文件 / 技术特性参数表",
            1,
            1,
            "技术特性参数表",
        )
    ]
    outside_text = (
        "项目名称：国网辽宁省电力有限公司2024 年第一次配网物资协议库存招标采购项目\n"
        "招标编号：2224AA                 分标名称：交流盘形悬式瓷绝缘子\n"
        "分标编号：2224AA-1405025-3401     包名称：包1-包6          包号：包1-包6"
    )
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="技术特性参数表", bbox=[20, 70, 220, 92], block_no=1),
        PdfTextBlock(block_id="outside-meta", page_no=1, text=outside_text, bbox=[20, 100, 560, 150], block_no=2),
    ]
    tables = [
        ParsedTable(
            table_id="tech-table",
            page_no=1,
            rows=[
                ["项目名称：国网辽宁省电力有限公司2024 年第一次配网物资协议库存招标采购项目", "", "", "", ""],
                ["招标编号：2224AA", "", "分标名称：交流盘形悬式瓷绝缘子", "", ""],
                ["分标编号：2224AA-1405025-3401", "", "包名称：包1-包6", "包号：包1-包6", ""],
                ["项目单位：国网辽宁省电力有限公司", "", "项目名称：国网辽宁省电力有限公司2024 年第一次配网物资协议库存招标采购项目", "", ""],
                ["序号", "名称", "单位", "招标人要求值", "投标人保证值"],
                ["1", "绝缘件公称直径", "mm", "255", "255"],
            ],
            bbox=[35, 160, 558, 300],
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_md = (tmp_path / "modules" / "技术特性参数表" / "material.md").read_text(encoding="utf-8")

    assert outside_text in material_md
    assert material_md.count("招标编号：2224AA") == 1
    assert material_md.count("分标编号：2224AA-1405025-3401") == 1
    assert "| 项目名称：国网辽宁省电力有限公司2024 年第一次配网物资协议库存招标采购项目 |  |  |  |  |" not in material_md
    assert "| 招标编号：2224AA |  | 分标名称：交流盘形悬式瓷绝缘子 |" not in material_md
    assert "| 分标编号：2224AA-1405025-3401 |  | 包名称：包1-包6 | 包号：包1-包6 |" not in material_md
    assert "| 项目单位：国网辽宁省电力有限公司 |  | 项目名称：国网辽宁省电力有限公司2024 年第一次配网物资协议库存招标采购项目 |  |  |" in material_md
    assert material_md.index(outside_text) < material_md.index("| 项目单位：国网辽宁省电力有限公司")


def test_package_module_artifacts_uses_precise_table_region_for_suppression_when_bbox_is_expanded(
    tmp_path: Path,
) -> None:
    candidates = [
        _candidate(
            "技术文件 / 技术特性参数表",
            1,
            1,
            "技术特性参数表",
        )
    ]
    outside_text = (
        "技术特性参数表\n"
        "项目名称：国网辽宁省电力有限公司2024 年第一次配网物资协议库存招标采购项目\n"
        "招标编号：2224AA                 分标名称：交流盘形悬式瓷绝缘子\n"
        "分标编号：2224AA-1405025-3401     包名称：包1-包6          包号：包1-包6"
    )
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="2.1、技术特性参数表", bbox=[20, 70, 220, 92], block_no=1),
        PdfTextBlock(block_id="outside-meta", page_no=1, text=outside_text, bbox=[53, 118, 494, 198], block_no=2),
        PdfTextBlock(
            block_id="inside-table",
            page_no=1,
            text="项目单位：国网辽宁省电力有限公司\n项目名称：国网辽宁省电力有限公司2024 年第一次配网物资协议库存招标采购项目\n序号\n名称",
            bbox=[53, 202, 542, 260],
            block_no=3,
        ),
    ]
    tables = [
        ParsedTable(
            table_id="tech-table",
            page_no=1,
            rows=[
                ["项目名称：国网辽宁省电力有限公司2024 年第一次配网物资协议库存招标采购项目", "", "", "", ""],
                ["招标编号：2224AA", "", "分标名称：交流盘形悬式瓷绝缘子", "", ""],
                ["分标编号：2224AA-1405025-3401", "", "包名称：包1-包6", "包号：包1-包6", ""],
                ["项目单位：国网辽宁省电力有限公司", "", "项目名称：国网辽宁省电力有限公司2024 年第一次配网物资协议库存招标采购项目", "", ""],
                ["序号", "名称", "单位", "招标人要求值", "投标人保证值"],
                ["1", "绝缘件公称直径", "mm", "255", "255"],
            ],
            bbox=[6, 110, 587, 691],
            table_region_bbox=[46, 200, 547, 658],
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_md = (tmp_path / "modules" / "技术特性参数表" / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((tmp_path / "modules" / "技术特性参数表" / "ordered_material.json").read_text(encoding="utf-8"))["items"]
    outside_item = next(item for item in ordered if item.get("block_id") == "outside-meta")
    inside_item = next(item for item in ordered if item.get("block_id") == "inside-table")

    assert outside_text in material_md
    assert material_md.count("招标编号：2224AA") == 1
    assert "| 招标编号：2224AA |  | 分标名称：交流盘形悬式瓷绝缘子 |" not in material_md
    assert "| 项目单位：国网辽宁省电力有限公司 |" in material_md
    assert outside_item["material_role"] == "body_text"
    assert inside_item["material_role"] == "table_text"
    assert inside_item["suppressed_reason"] == "table_geometry"


def test_package_module_artifacts_keeps_tail_note_crossing_precise_table_bottom(
    tmp_path: Path,
) -> None:
    candidates = [
        _candidate(
            "技术文件 / 技术特性参数表",
            1,
            1,
            "技术特性参数表",
        )
    ]
    tail_text = (
        "10.3 适用于1000kV 年均劣化率 % 0.005 0.005 "
        "11 工频击穿电压 kV 110 110 "
        "12 冲击击穿电压（标幺值） 2.8 "
        "编制说明： 1、请抄录已获取招标文件技术规范书《技术特性参数表》的数据、信息或表述，"
        "填写本表对应条目并逐项对应作出投标。"
    )
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="技术特性参数表", bbox=[20, 70, 220, 92], block_no=1),
        PdfTextBlock(block_id="table-body", page_no=1, text="序号\n名称\n单位\n招标人要求值\n投标人保证值", bbox=[53, 202, 542, 230], block_no=2),
        PdfTextBlock(block_id="tail-crossing", page_no=1, text=tail_text, bbox=[53, 608, 542, 750], block_no=3),
    ]
    tables = [
        ParsedTable(
            table_id="tech-table",
            page_no=1,
            rows=[
                ["项目单位：国网辽宁省电力有限公司", "", "项目名称：国网辽宁省电力有限公司2024 年第一次配网物资协议库存招标采购项目", "", ""],
                ["序号", "名称", "单位", "招标人要求值", "投标人保证值"],
                ["10.3", "适用于1000kV 年均劣化率", "%", "0.005", "0.005"],
                ["11", "工频击穿电压", "kV", "110", "110"],
                ["12", "冲击击穿电压（标幺值）", "", "", "2.8"],
            ],
            bbox=[6, 110, 587, 760],
            table_region_bbox=[46, 200, 547, 658],
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_md = (tmp_path / "modules" / "技术特性参数表" / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((tmp_path / "modules" / "技术特性参数表" / "ordered_material.json").read_text(encoding="utf-8"))["items"]
    tail_item = next(item for item in ordered if item.get("block_id") == "tail-crossing")

    assert "编制说明：" in material_md
    assert "填写本表对应条目并逐项对应作出投标" in material_md
    assert "10.3 适用于1000kV 年均劣化率 % 0.005 0.005 11 工频击穿电压" not in material_md
    assert tail_item["material_role"] == "body_text"


def test_package_module_artifacts_uses_cross_top_text_to_trim_repeated_leading_rows(
    tmp_path: Path,
) -> None:
    candidates = [
        _candidate(
            "通用文件 / 连续表格",
            1,
            1,
            "连续表格",
        )
    ]
    outside_text = (
        "表格说明\n"
        "外部说明：这几行在表格边界上方\n"
        "外部编号：A-001"
    )
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="连续表格", bbox=[20, 70, 220, 92], block_no=1),
        PdfTextBlock(block_id="outside-cross-top", page_no=1, text=outside_text, bbox=[53, 118, 494, 198], block_no=2),
        PdfTextBlock(block_id="inside-table", page_no=1, text="列A\n列B\n1\n值", bbox=[53, 202, 542, 260], block_no=3),
    ]
    tables = [
        ParsedTable(
            table_id="generic-table",
            page_no=1,
            rows=[
                ["外部说明：这几行在表格边界上方", ""],
                ["外部编号：A-001", ""],
                ["列A", "列B"],
                ["1", "值"],
            ],
            bbox=[6, 110, 587, 691],
            table_region_bbox=[46, 200, 547, 658],
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_md = (tmp_path / "modules" / "连续表格" / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((tmp_path / "modules" / "连续表格" / "ordered_material.json").read_text(encoding="utf-8"))["items"]
    outside_item = next(item for item in ordered if item.get("block_id") == "outside-cross-top")
    inside_item = next(item for item in ordered if item.get("block_id") == "inside-table")

    assert outside_text in material_md
    assert "| 列A | 列B |" in material_md
    assert "| 外部说明：这几行在表格边界上方 |" not in material_md
    assert "| 外部编号：A-001 |" not in material_md
    assert material_md.index(outside_text) < material_md.index("| 列A | 列B |")
    assert outside_item["material_role"] == "body_text"
    assert inside_item["material_role"] == "table_text"


def test_package_module_artifacts_keeps_tail_text_below_overexpanded_table_bbox(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "技术文件 / 2、 专项投标文件 / 2.1、 业绩文件 / 2.1.1、 供货及运行业绩表",
            14,
            14,
            "2.1.1、供货及运行业绩表",
        )
    ]
    blocks = [
        PdfTextBlock(
            block_id="header",
            page_no=14,
            text="序号\n产品型式\n工程名称\n数量（单位）或金额（万元）\n投运时间\n联系人及电话\n备注",
            bbox=[76, 82, 535, 106],
            block_no=1,
        ),
        PdfTextBlock(
            block_id="total",
            page_no=14,
            text="合计\n248 套/5817.6232 万元",
            bbox=[76, 593, 392, 604],
            block_no=2,
        ),
        PdfTextBlock(
            block_id="notes",
            page_no=14,
            text="编制说明：\n1.投标人须按照投标人须知前附表的要求提交业绩证明材料。\n2.仅提供发票和合同关键页的扫描件。",
            bbox=[76, 626, 533, 733],
            block_no=3,
        ),
    ]
    tables = [
        ParsedTable(
            table_id="performance-table-p3",
            page_no=14,
            rows=[
                ["序号", "产品型式", "工程名称", "数量（单位）或金额（万元）", "投运时间", "联系人及电话", "备注"],
                ["20", "智能变电站变压器油中溶解气体在线监测装置MGA8000", "国网河北超高压公司500kV瀛州站", "12套/116.3448万元", "2024年", "张弘媛/0311-66093666", "签订日期：2024-06-25"],
                ["合计", "", "", "248套/5817.6232万元", "", "", ""],
            ],
            bbox=[33, 27, 576, 662],
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_dir = (
        tmp_path
        / "modules"
        / "2、 专项投标文件"
        / "2.1、 业绩文件"
        / "2.1.1、 供货及运行业绩表"
    )
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))["items"]
    notes_item = next(item for item in ordered if item.get("block_id") == "notes")

    assert "| 序号 | 产品型式 | 工程名称 |" in material_md
    assert "编制说明：" in material_md
    assert material_md.index("| 序号 | 产品型式 | 工程名称 |") < material_md.index("编制说明：")
    assert notes_item["material_role"] == "body_text"


def test_package_module_artifacts_renders_full_pdf_tail_note_as_table_row_when_table_model_contains_truncated_note(
    tmp_path: Path,
) -> None:
    candidates = [
        _candidate(
            "技术文件 / 2、 专项投标文件 / 2.1、 业绩文件 / 2.1.1、 供货及运行业绩表",
            14,
            14,
            "2.1.1、供货及运行业绩表",
        )
    ]
    full_note = (
        "编制说明：\n"
        "1.投标人须按照投标人须知前附表的要求提交业绩证明材料，证明材料包括供货单位、用户、产品\n"
        "规格型号等重要产品信息、供货数量及价格等内容，上述内容必须涵盖招标公告所注明的业绩要求\n"
        "的所有信息。\n"
        "2.仅提供发票和合同关键页（合同封面和签署页）的扫描件，不需提供整个合同文本。\n"
        "3.应对业绩证明材料进行编号，且与该表格对应业绩的序号一致，并按顺序编制，即对应序号1的\n"
        "业绩的所用证明材料编号全部为1，合同和发票复印件应一一对应，且编制在一起，然后制作序号2\n"
        "的业绩的证明材料，以此类推。"
    )
    blocks = [
        PdfTextBlock(
            block_id="header",
            page_no=14,
            text="序号\n产品型式\n工程名称\n数量（单位）或金额（万元）\n投运时间\n联系人及电话\n备注",
            bbox=[76, 82, 535, 106],
            block_no=1,
        ),
        PdfTextBlock(
            block_id="total",
            page_no=14,
            text="合计\n248 套/5817.6232 万元",
            bbox=[76, 593, 392, 604],
            block_no=2,
        ),
        PdfTextBlock(
            block_id="full-note",
            page_no=14,
            text=full_note,
            bbox=[76, 626, 533, 733],
            block_no=3,
        ),
    ]
    tables = [
        ParsedTable(
            table_id="performance-table-p3",
            page_no=14,
            rows=[
                ["序号", "产品型式", "工程名称", "数量（单位）或金额（万元）", "投运时间", "联系人及电话", "备注"],
                ["20", "智能变电站变压器油中溶解气体在线监测装置MGA8000", "国网河北超高压公司500kV瀛州站", "12套/116.3448万元", "2024年", "张弘媛/0311-66093666", "签订日期：2024-06-25"],
                ["合计", "", "", "248套/5817.6232万元", "", "", ""],
                ["编制说明：", "", "", "", "", "", ""],
                ["1.投标人须按照投标人须知前附表的要求提交业绩证明材料，证明材料包括供货单位、用户、产品", "", "", "", "", "", ""],
            ],
            bbox=[33, 27, 576, 662],
        )
    ]
    page_material_items = [
        {"type": "text", "item_type": "text", "item_id": "pp-note-title", "page_no": 14, "top_y": 1251.9, "text": "编制说明：", "bbox": [149, 1251, 245, 1272], "source_type": "pp_structure_text_region"},
        {"type": "text", "item_type": "text", "item_id": "pp-note-1", "page_no": 14, "top_y": 1278.6, "text": full_note.split("\n", 2)[1], "bbox": [148, 1278, 1057, 1354], "source_type": "pp_structure_text_region"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        page_material_items=page_material_items,
        out_dir=tmp_path,
    )

    material_dir = (
        tmp_path
        / "modules"
        / "2、 专项投标文件"
        / "2.1、 业绩文件"
        / "2.1.1、 供货及运行业绩表"
    )
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))["items"]
    notes_item = next(item for item in ordered if item.get("block_id") == "full-note")

    rendered_note = full_note.replace("\n", "<br>")
    assert f"| {rendered_note} |  |  |  |  |  |  |" in material_md
    assert material_md.count("编制说明：") == 1
    assert "1.投标人须按照投标人须知前附表的要求提交业绩证明材料，证明材料包括供货单位、用户、产品 |" not in material_md
    assert notes_item["material_role"] == "body_text"


def test_package_module_artifacts_suppresses_wide_table_text_outside_narrow_effective_bounds(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 3、补充文件 / （2）、 部分业主出具的履约优秀证明8份",
            579,
            579,
            "（2）、 部分业主出具的履约优秀证明8份",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="title", page_no=579, text="（2）、 部分业主出具的履约优秀证明8份", bbox=[70, 70, 360, 90], block_no=1),
        PdfTextBlock(block_id="table-header", page_no=579, text="序\n号", bbox=[77.6, 103.0, 93.5, 127.1], block_no=2),
        PdfTextBlock(
            block_id="table-header-wide",
            page_no=579,
            text="出具\n年份\n出具的报告名称 业主名称\n项目名称\n运行规模\n安装\n时间",
            bbox=[104.1, 103.0, 514.9, 127.1],
            block_no=3,
        ),
        PdfTextBlock(
            block_id="middle-matched",
            page_no=579,
            text="2\n2025\n运行及评价证明",
            bbox=[77.6, 228.7, 219.4, 238.7],
            block_no=4,
        ),
        PdfTextBlock(
            block_id="late-project",
            page_no=579,
            text="国网河北保定供电公\n司220kV 白石山等站\n主变油色谱在线监测\n装置更换工程",
            bbox=[304.2, 407.1, 404.1, 458.7],
            block_no=5,
        ),
        PdfTextBlock(
            block_id="tail-note",
            page_no=579,
            text="编制说明：仅提供合同关键页的扫描件。",
            bbox=[70, 775, 520, 795],
            block_no=6,
        ),
    ]
    tables = [
        ParsedTable(
            table_id="wide-performance-table",
            page_no=579,
            rows=[
                ["序号", "出具年份", "出具的报告名称", "业主名称", "项目名称", "运行规模", "安装时间"],
                ["2", "2025", "运行及评价证明", "国网山东省电力公司超高压公司", "500kV 泰山站、德州站油色谱在线监测系统改造工程", "12 台智能变电站变压器油中溶解气体在线监测装置", "2021"],
                ["4", "2022", "履约评价证明", "国网山东省电力公司超高压公司", "500kV 密州站等3 座变电站5 组主变油色谱在线装置改造工程", "15 台智能变电站变压器油中溶解气体在线监测装置", "2021"],
            ],
            bbox=[35.4, 44.3, 559.1, 767.0],
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_dir = tmp_path / "modules" / "3、补充文件" / "（2）、 部分业主出具的履约优秀证明8份"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))["items"]
    header_item = next(item for item in ordered if item.get("block_id") == "table-header")
    late_item = next(item for item in ordered if item.get("block_id") == "late-project")
    note_item = next(item for item in ordered if item.get("block_id") == "tail-note")

    assert "序\n号" not in material_md
    assert "白石山" not in material_md
    assert "编制说明：仅提供合同关键页的扫描件。" in material_md
    assert header_item["material_role"] == "table_text"
    assert late_item["material_role"] == "table_text"
    assert note_item["material_role"] == "body_text"


def test_package_module_artifacts_omits_text_repeated_from_inline_table_cells_even_outside_bbox(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "技术文件 / 技术偏差表",
            1,
            1,
            "技术偏差表",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="技术偏差表", bbox=[0, 80, 200, 100], block_no=1),
        PdfTextBlock(
            block_id="cell-repeat",
            page_no=1,
            text="表1 技术特性参数表\n6、乙炔（C₂H₂）\n6.1 最低检测限值：0.5μL/L",
            bbox=[20, 280, 300, 340],
            block_no=2,
        ),
        PdfTextBlock(block_id="after", page_no=1, text="投标人声明：接受其余全部技术条件。", bbox=[0, 360, 500, 380], block_no=3),
    ]
    tables = [
        ParsedTable(
            table_id="deviation-table",
            page_no=1,
            rows=[
                ["序号", "偏差事项", "招标文件要求", "投标文件响应"],
                ["1", "最低检测限值", "表 1 技术特性参数表\n6、乙炔（C2H2）\n6.1 最低检测限值：0.5 μL/L", "0.1 μL/L"],
            ],
            bbox=[10, 120, 560, 250],
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_dir = tmp_path / "modules" / "技术偏差表"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))["items"]
    assert "| 1 | 最低检测限值 |" in material_md
    assert "投标人声明：接受其余全部技术条件。" in material_md
    assert material_md.count("表 1 技术特性参数表") == 1
    assert material_md.count("最低检测限值") == 2
    suppressed = [item for item in ordered if item.get("block_id") == "cell-repeat"]
    assert suppressed
    assert suppressed[0]["material_role"] == "table_text"
    assert suppressed[0]["suppressed_by_table_id"] == "deviation-table"
    assert suppressed[0]["suppressed_reason"] == "table_cell_text"


def test_package_module_artifacts_links_fragmented_wide_table_instead_of_inline_markdown(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / 财务状况",
            1,
            1,
            "财务状况",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="h1", page_no=1, text="财务状况", bbox=[0, 80, 200, 100], block_no=1),
    ]
    tables = [
        ParsedTable(
            table_id="fragmented-table",
            page_no=1,
            rows=[
                ["序", "号", "项", "目", "名", "称", "金", "额"],
                ["1", "", "营", "业", "收", "入", "1", "0"],
                ["2", "", "净", "利", "润", "", "2", "0"],
            ],
            bbox=[10, 130, 500, 260],
        ),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_md = (tmp_path / "modules" / "补充文件" / "财务状况" / "material.md").read_text(encoding="utf-8")
    assert "[表格：财务状况_表1](table_items/财务状况_表1.json)" in material_md
    assert "| 序 | 号 | 项 | 目 | 名 | 称 | 金 | 额 |" not in material_md


def test_package_module_artifacts_links_sparse_wide_table_instead_of_inline_markdown(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / 部分业主出具的履约优秀证明8份",
            579,
            579,
            "部分业主出具的履约优秀证明8份",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="h1", page_no=579, text="部分业主出具的履约优秀证明8份", bbox=[0, 80, 260, 100], block_no=1),
    ]
    tables = [
        ParsedTable(
            table_id="sparse-wide-table",
            page_no=579,
            rows=[
                ["", "", "", "序", "", "", "", "", "出具", "", "出具的报告名称", "", ""],
                ["", "号", "", "", "年份", "", "", "", "", "", "", "时间", ""],
                ["", "1", "", "", "", "", "", "", "", "", "", "", ""],
                ["2", "", "", "", "", "", "", "2025", "", "", "运行及评价证明", "", ""],
                ["3", "", "", "", "", "", "", "2022", "", "", "履约评价证明", "", ""],
            ],
            bbox=[10, 130, 500, 360],
        ),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_md = (
        tmp_path / "modules" / "补充文件" / "部分业主出具的履约优秀证明8份" / "material.md"
    ).read_text(encoding="utf-8")
    assert "[表格：部分业主出具的履约优秀证明8份_表1](table_items/部分业主出具的履约优秀证明8份_表1.json)" in material_md
    assert "|  |  |  | 序 |" not in material_md


def test_package_module_artifacts_keeps_parent_preface_before_first_child_section(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "PDF / 3、 补充文件 / 3.7、 财务状况 / 3.7.1、 2022 年度财务审计报告",
            1,
            1,
            "3.7、 财务状况",
        )
    ]
    candidates[0].material_evidence = {"source": "pdf_toc_leaf", "start_y": 240.0, "end_y": None, "start_block_id": "child-title"}
    blocks = [
        PdfTextBlock(block_id="parent-title", page_no=1, text="3.7、财务状况", bbox=[0, 100, 200, 120], block_no=1),
        PdfTextBlock(block_id="preface", page_no=1, text="投标人近三年财务状况如下。", bbox=[0, 150, 400, 170], block_no=2),
        PdfTextBlock(block_id="child-title", page_no=1, text="3.7.1、2022 年度财务审计报告", bbox=[0, 240, 400, 260], block_no=3),
        PdfTextBlock(block_id="child-body", page_no=1, text="2022 年度报告正文", bbox=[0, 280, 400, 300], block_no=4),
    ]
    tables = [
        ParsedTable(table_id="preface-table", page_no=1, rows=[["年份", "报告"], ["2022", "审计报告"]], bbox=[10, 180, 300, 230]),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
        top_level_modules=["3、 补充文件"],
        planned_section_paths=[candidate.section_path for candidate in candidates],
    )

    parent_md = (
        tmp_path
        / "modules"
        / "3、 补充文件"
        / "3.7、 财务状况"
        / "material.md"
    ).read_text(encoding="utf-8")
    child_md = (
        tmp_path
        / "modules"
        / "3、 补充文件"
        / "3.7、 财务状况"
        / "3.7.1、 2022 年度财务审计报告"
        / "material.md"
    ).read_text(encoding="utf-8")

    assert "投标人近三年财务状况如下。" in parent_md
    assert "| 年份 | 报告 |" in parent_md
    assert "- [3.7.1、 2022 年度财务审计报告](3.7.1、 2022 年度财务审计报告/material.md)" in parent_md
    assert "2022 年度报告正文" in child_md
    assert "投标人近三年财务状况如下。" not in child_md


def test_package_module_artifacts_keeps_all_child_links_when_parent_has_preface(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "PDF / 3、 补充文件 / 3.7、 财务状况 / 3.7.1、 2022 年度财务审计报告",
            1,
            1,
            "3.7、 财务状况",
        ),
        _candidate(
            "PDF / 3、 补充文件 / 3.7、 财务状况 / 3.7.2、 2023 年度财务审计报告",
            1,
            1,
            "3.7、 财务状况",
        ),
    ]
    candidates[0].material_evidence = {"source": "pdf_toc_leaf", "start_y": 240.0, "end_y": 360.0, "start_block_id": "child-2022", "end_block_id": "child-2023"}
    candidates[1].material_evidence = {"source": "pdf_toc_leaf", "start_y": 360.0, "end_y": None, "start_block_id": "child-2023"}
    blocks = [
        PdfTextBlock(block_id="parent-title", page_no=1, text="3.7、财务状况", bbox=[0, 100, 200, 120], block_no=1),
        PdfTextBlock(block_id="preface", page_no=1, text="投标人近三年财务状况如下。", bbox=[0, 150, 400, 170], block_no=2),
        PdfTextBlock(block_id="child-2022", page_no=1, text="3.7.1、2022 年度财务审计报告", bbox=[0, 240, 400, 260], block_no=3),
        PdfTextBlock(block_id="body-2022", page_no=1, text="2022 年度报告正文", bbox=[0, 280, 400, 300], block_no=4),
        PdfTextBlock(block_id="child-2023", page_no=1, text="3.7.2、2023 年度财务审计报告", bbox=[0, 360, 400, 380], block_no=5),
        PdfTextBlock(block_id="body-2023", page_no=1, text="2023 年度报告正文", bbox=[0, 400, 400, 420], block_no=6),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
        top_level_modules=["3、 补充文件"],
        planned_section_paths=[candidate.section_path for candidate in candidates],
    )

    parent_md = (
        tmp_path
        / "modules"
        / "3、 补充文件"
        / "3.7、 财务状况"
        / "material.md"
    ).read_text(encoding="utf-8")

    assert "投标人近三年财务状况如下。" in parent_md
    assert "- [3.7.1、 2022 年度财务审计报告](3.7.1、 2022 年度财务审计报告/material.md)" in parent_md
    assert "- [3.7.2、 2023 年度财务审计报告](3.7.2、 2023 年度财务审计报告/material.md)" in parent_md


def test_package_module_artifacts_does_not_use_toc_entry_as_parent_preface_start(tmp_path: Path) -> None:
    child_path = "PDF / 2、 专项投标文件 / 2.9、 生产场地、生产能力及剩余产能列表、厂房面积 / 2.9.1、 房产证明材料"
    candidates = [
        _candidate(
            child_path,
            168,
            169,
            "2.9.1、 房产证明材料",
        )
    ]
    candidates[0].material_evidence = {"source": "pdf_toc_leaf", "start_y": 120.0, "end_y": None, "start_block_id": "child-title"}
    blocks = [
        PdfTextBlock(
            block_id="toc-29",
            page_no=5,
            text="2.9、生产场地、生产能力及剩余产能列表、厂房面积 .......................................................................... 167",
            bbox=[0, 120, 900, 140],
            block_no=1,
        ),
        PdfTextBlock(
            block_id="toc-210",
            page_no=5,
            text="2.10、生产装备 ....................................................................................... 185",
            bbox=[0, 145, 900, 165],
            block_no=2,
        ),
        PdfTextBlock(block_id="unrelated-title", page_no=20, text="1、技术偏差表", bbox=[0, 80, 200, 100], block_no=3),
        PdfTextBlock(block_id="unrelated-body", page_no=20, text="技术偏差表正文", bbox=[0, 120, 400, 140], block_no=4),
        PdfTextBlock(block_id="child-title", page_no=168, text="2.9.1、房产证明材料", bbox=[0, 120, 300, 140], block_no=5),
        PdfTextBlock(block_id="child-body", page_no=168, text="房产证明正文", bbox=[0, 160, 400, 180], block_no=6),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
        top_level_modules=["2、 专项投标文件"],
        planned_section_paths=[child_path],
    )

    parent_dir = tmp_path / "modules" / "2、 专项投标文件" / "2.9、 生产场地、生产能力及剩余产能列表、厂房面积"
    parent_md = (parent_dir / "material.md").read_text(encoding="utf-8")
    child_md = (parent_dir / "2.9.1、 房产证明材料" / "material.md").read_text(encoding="utf-8")

    assert "2.10、生产装备" not in parent_md
    assert "技术偏差表正文" not in parent_md
    assert "- [2.9.1、 房产证明材料](2.9.1、 房产证明材料/material.md)" in parent_md
    assert "房产证明正文" in child_md


def test_package_module_artifacts_excludes_child_scope_from_parent_material_body(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "PDF / 2、 专项投标文件 / 2.1、 业绩文件",
            1,
            1,
            "2.1、 业绩文件",
        ),
        _candidate(
            "PDF / 2、 专项投标文件 / 2.1、 业绩文件 / 2.1.1、 供货及运行业绩表",
            1,
            1,
            "2.1.1、 供货及运行业绩表",
        ),
    ]
    candidates[0].material_evidence = {"source": "pdf_toc_leaf", "start_y": 100.0, "end_y": None, "start_block_id": "parent-title"}
    candidates[1].material_evidence = {"source": "pdf_toc_leaf", "start_y": 240.0, "end_y": None, "start_block_id": "child-title"}
    blocks = [
        PdfTextBlock(block_id="parent-title", page_no=1, text="2.1、业绩文件", bbox=[0, 100, 200, 120], block_no=1),
        PdfTextBlock(block_id="parent-preface", page_no=1, text="本节汇总投标人业绩文件。", bbox=[0, 150, 400, 170], block_no=2),
        PdfTextBlock(block_id="child-title", page_no=1, text="2.1.1、供货及运行业绩表", bbox=[0, 240, 400, 260], block_no=3),
        PdfTextBlock(block_id="child-body", page_no=1, text="供货及运行业绩表正文", bbox=[0, 280, 400, 300], block_no=4),
    ]
    tables = [
        ParsedTable(table_id="child-table", page_no=1, rows=[["序号", "工程名称"], ["1", "国网湖北超高压公司"]], bbox=[10, 320, 500, 420]),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
        top_level_modules=["2、 专项投标文件"],
        planned_section_paths=[candidate.section_path for candidate in candidates],
    )

    parent_md = (
        tmp_path
        / "modules"
        / "2、 专项投标文件"
        / "2.1、 业绩文件"
        / "material.md"
    ).read_text(encoding="utf-8")
    child_md = (
        tmp_path
        / "modules"
        / "2、 专项投标文件"
        / "2.1、 业绩文件"
        / "2.1.1、 供货及运行业绩表"
        / "material.md"
    ).read_text(encoding="utf-8")

    assert "本节汇总投标人业绩文件。" in parent_md
    assert "供货及运行业绩表正文" not in parent_md
    assert "| 序号 | 工程名称 |" not in parent_md
    assert "- [2.1.1、 供货及运行业绩表](2.1.1、 供货及运行业绩表/material.md)" in parent_md
    assert "供货及运行业绩表正文" in child_md
    assert "| 序号 | 工程名称 |" in child_md


def test_package_module_artifacts_uses_planned_child_heading_scope_to_keep_tables_out_of_parent(tmp_path: Path) -> None:
    parent_path = "PDF / 2、 专项投标文件 / 2.2、 项目团队情况"
    child_path = "PDF / 2、 专项投标文件 / 2.2、 项目团队情况 / 2.2.1、 项目团队情况"
    candidates = [
        _candidate(
            parent_path,
            1,
            1,
            "2.2、 项目团队情况",
        ),
    ]
    candidates[0].material_evidence = {"source": "pdf_toc_leaf", "start_y": 80.0, "end_y": None, "start_block_id": "parent-title"}
    blocks = [
        PdfTextBlock(block_id="parent-title", page_no=1, text="2.2、项目团队情况", bbox=[0, 80, 260, 100], block_no=1),
        PdfTextBlock(block_id="parent-preface", page_no=1, text="本节说明项目团队情况。", bbox=[0, 120, 400, 140], block_no=2),
        PdfTextBlock(block_id="child-title", page_no=1, text="2.2.1、项目团队情况", bbox=[0, 180, 300, 200], block_no=3),
    ]
    tables = [
        ParsedTable(
            table_id="team-table",
            page_no=1,
            rows=[
                ["层级", "职务", "姓名"],
                ["项目负责人", "项目经理", "危雪林"],
            ],
            bbox=[10, 220, 500, 360],
        ),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
        top_level_modules=["2、 专项投标文件"],
        planned_section_paths=[parent_path, child_path],
    )

    parent_md = (
        tmp_path
        / "modules"
        / "2、 专项投标文件"
        / "2.2、 项目团队情况"
        / "material.md"
    ).read_text(encoding="utf-8")
    child_md = (
        tmp_path
        / "modules"
        / "2、 专项投标文件"
        / "2.2、 项目团队情况"
        / "2.2.1、 项目团队情况"
        / "material.md"
    ).read_text(encoding="utf-8")

    assert "本节说明项目团队情况。" in parent_md
    assert "| 层级 | 职务 | 姓名 |" not in parent_md
    assert "- [2.2.1、 项目团队情况](2.2.1、 项目团队情况/material.md)" in parent_md
    assert "| 层级 | 职务 | 姓名 |" in child_md
    assert "项目经理" in child_md


def test_backfill_refreshes_child_links_when_parent_markdown_already_exists(tmp_path: Path) -> None:
    parent_dir = tmp_path / "modules" / "3.7、 财务状况"
    child_2022 = parent_dir / "3.7.1、 2022 年度财务审计报告"
    child_2023 = parent_dir / "3.7.2、 2023 年度财务审计报告"
    child_2022.mkdir(parents=True)
    child_2023.mkdir(parents=True)
    (child_2022 / "material.md").write_text("# 2022\n", encoding="utf-8")
    (child_2023 / "material.md").write_text("# 2023\n", encoding="utf-8")
    (parent_dir / "material.md").write_text(
        "# 3.7、 财务状况\n\n投标人近三年财务状况如下。\n\n## 子章节\n\n"
        "- [3.7.1、 2022 年度财务审计报告](3.7.1、 2022 年度财务审计报告/material.md)\n",
        encoding="utf-8",
    )

    _backfill_missing_material_indexes(tmp_path / "modules")

    parent_md = (parent_dir / "material.md").read_text(encoding="utf-8")
    assert "投标人近三年财务状况如下。" in parent_md
    assert parent_md.count("## 子章节") == 1
    assert "- [3.7.1、 2022 年度财务审计报告](3.7.1、 2022 年度财务审计报告/material.md)" in parent_md
    assert "- [3.7.2、 2023 年度财务审计报告](3.7.2、 2023 年度财务审计报告/material.md)" in parent_md


def test_backfill_strips_expanded_child_body_from_parent_markdown(tmp_path: Path) -> None:
    parent_dir = tmp_path / "modules" / "2、 专项投标文件" / "2.1、 业绩文件"
    child_dir = parent_dir / "2.1.1、 供货及运行业绩表"
    child_dir.mkdir(parents=True)
    (child_dir / "material.md").write_text("# 2.1.1、 供货及运行业绩表\n\n供货及运行业绩表正文\n", encoding="utf-8")
    (parent_dir / "material.md").write_text(
        "# 2.1、 业绩文件\n\n"
        "招标公告附件2：专用资格要求。\n\n"
        "2.1.1、供货及运行业绩表\n\n"
        "供货及运行业绩表\n\n"
        "| 序号 | 产品型式 |\n"
        "| --- | --- |\n"
        "| 1 | MGA8000 |\n",
        encoding="utf-8",
    )

    _backfill_missing_material_indexes(tmp_path / "modules")

    parent_md = (parent_dir / "material.md").read_text(encoding="utf-8")
    assert "招标公告附件2：专用资格要求。" in parent_md
    assert "供货及运行业绩表\n\n| 序号 | 产品型式 |" not in parent_md
    assert "| 1 | MGA8000 |" not in parent_md
    assert "- [2.1.1、 供货及运行业绩表](2.1.1、 供货及运行业绩表/material.md)" in parent_md


def test_backfill_sorts_child_links_by_section_number(tmp_path: Path) -> None:
    parent_dir = tmp_path / "modules" / "3、 补充文件"
    for child_name in [
        "3.10、 制造商授权委托书（无，我公司为制造商投标）",
        "3.1、 投标保证金",
        "3.9、 企业名称变更",
        "3.14、 其他（无）",
        "3.2、 投标人与国家电网公司系统人员关系说明",
    ]:
        child_dir = parent_dir / child_name
        child_dir.mkdir(parents=True)
        (child_dir / "material.md").write_text(f"# {child_name}\n", encoding="utf-8")

    _backfill_missing_material_indexes(tmp_path / "modules")

    parent_md = (parent_dir / "material.md").read_text(encoding="utf-8")
    assert parent_md.index("[3.1、 投标保证金]") < parent_md.index("[3.2、 投标人与国家电网公司系统人员关系说明]")
    assert parent_md.index("[3.9、 企业名称变更]") < parent_md.index("[3.10、 制造商授权委托书（无，我公司为制造商投标）]")
    assert parent_md.index("[3.10、 制造商授权委托书（无，我公司为制造商投标）]") < parent_md.index("[3.14、 其他（无）]")


def test_package_module_artifacts_renders_field_value_text_blocks_as_table(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 投标人基本情况表",
            1,
            1,
            "投标人基本情况表",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="h1", page_no=1, text="投标人基本情况表", bbox=[0, 80, 240, 100], block_no=1),
        PdfTextBlock(block_id="label1", page_no=1, text="投标人名称：", bbox=[20, 130, 120, 150], block_no=2),
        PdfTextBlock(block_id="value1", page_no=1, text="宁波理工环境能源科技股份有限公司", bbox=[140, 130, 420, 150], block_no=3),
        PdfTextBlock(block_id="label2", page_no=1, text="法定代表人：", bbox=[20, 165, 120, 185], block_no=4),
        PdfTextBlock(block_id="value2", page_no=1, text="周方洁", bbox=[140, 165, 220, 185], block_no=5),
        PdfTextBlock(block_id="label3", page_no=1, text="注册地址：", bbox=[20, 200, 120, 220], block_no=6),
        PdfTextBlock(block_id="value3", page_no=1, text="浙江省宁波市北仑区", bbox=[140, 200, 360, 220], block_no=7),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
    )

    material_md = (tmp_path / "modules" / "投标人基本情况表" / "material.md").read_text(encoding="utf-8")
    assert "| 字段 | 内容 |" in material_md
    assert "| 投标人名称 | 宁波理工环境能源科技股份有限公司 |" in material_md
    assert "| 法定代表人 | 周方洁 |" in material_md
    assert "| 注册地址 | 浙江省宁波市北仑区 |" in material_md
    assert "\n投标人名称：\n" not in material_md
    assert "\n周方洁\n" not in material_md


def test_package_module_artifacts_keeps_fu_content_inline_without_submaterials(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 法定代表人授权委托书",
            1,
            1,
            "法定代表人授权委托书",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="h1", page_no=1, text="法定代表人授权委托书", bbox=[0, 80, 240, 100], block_no=1),
        PdfTextBlock(block_id="body", page_no=1, text="委托代理人办理投标事宜。", bbox=[0, 120, 300, 140], block_no=2),
        PdfTextBlock(block_id="attach", page_no=1, text="附：法定代表人（单位负责人）身份证（扫描件）", bbox=[0, 200, 400, 220], block_no=3),
        PdfTextBlock(block_id="child-text", page_no=1, text="身份证号码：3302************", bbox=[0, 250, 400, 270], block_no=4),
    ]
    images = [
        {"image_id": "id-front", "page_no": 1, "xref": 10, "width": 600, "height": 400, "rect": [20, 300, 260, 430], "ext": "png"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
    )

    material_dir = tmp_path / "modules" / "法定代表人授权委托书"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))

    assert "委托代理人办理投标事宜。" in material_md
    assert "附：法定代表人（单位负责人）身份证（扫描件）" in material_md
    assert "身份证号码：3302" in material_md
    assert "![法定代表人（单位负责人）身份证（扫描件）_图1](image_items/法定代表人（单位负责人）身份证（扫描件）_图1.png)" in material_md
    assert not (material_dir / "submaterials").exists()
    assert all(item["item_type"] != "submaterial" for item in ordered["items"])


def test_package_module_artifacts_renders_full_detected_table_without_dropping_rows_or_columns(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / 企业名称变更",
            1,
            1,
            "企业名称变更",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="h1", page_no=1, text="企业名称变更", bbox=[0, 80, 200, 100], block_no=1),
    ]
    tables = [
        ParsedTable(
            table_id="full-table",
            page_no=1,
            rows=[
                ["序号", "变更事项", "变更前", "变更后"],
                ["1", "企业名称", "旧公司", "新公司"],
                ["2", "注册地址", "旧地址", "新地址"],
                ["3", "法定代表人", "张三", "李四"],
            ],
            bbox=[10, 130, 500, 260],
        ),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_md = (tmp_path / "modules" / "补充文件" / "企业名称变更" / "material.md").read_text(encoding="utf-8")
    assert "| 序号 | 变更事项 | 变更前 | 变更后 |" in material_md
    assert "| 1 | 企业名称 | 旧公司 | 新公司 |" in material_md
    assert "| 2 | 注册地址 | 旧地址 | 新地址 |" in material_md
    assert "| 3 | 法定代表人 | 张三 | 李四 |" in material_md
    assert "table_items/企业名称变更_表1.json" not in material_md


def test_package_module_artifacts_renders_table_image_cells_with_material_image_links(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 补充文件 / 科研经费占比",
            1,
            1,
            "科研经费占比",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="h1", page_no=1, text="科研经费占比", bbox=[0, 80, 200, 100], block_no=1),
    ]
    tables = [
        ParsedTable(
            table_id="research-table",
            page_no=1,
            rows=[["项目", "2024 年"], ["研发投入", "[图片]"]],
            bbox=[10, 130, 500, 260],
            table_model={
                "schema_version": "table_model_v1",
                "source": "vlm",
                "row_count": 2,
                "col_count": 2,
                "rows": [["项目", "2024 年"], ["研发投入", "[图片]"]],
                "cells": [
                    {"row": 0, "col": 0, "text": "项目", "rowspan": 1, "colspan": 1},
                    {"row": 0, "col": 1, "text": "2024 年", "rowspan": 1, "colspan": 1},
                    {"row": 1, "col": 0, "text": "研发投入", "rowspan": 1, "colspan": 1},
                    {
                        "row": 1,
                        "col": 1,
                        "text": "[图片]",
                        "rowspan": 1,
                        "colspan": 1,
                        "image_ref": "embedded_images/chart-screenshot.png",
                    },
                ],
                "merged_cells": [],
            },
            embedded_image_refs=[
                {
                    "image_id": "chart-screenshot",
                    "image_ref": "embedded_images/chart-screenshot.png",
                    "page_no": 1,
                    "rect": [100, 160, 240, 220],
                }
            ],
        ),
    ]
    images = [
        {"image_id": "chart-screenshot", "page_no": 1, "xref": 20, "width": 600, "height": 500, "rect": [100, 160, 240, 220], "ext": "png"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
    )

    material_dir = tmp_path / "modules" / "补充文件" / "科研经费占比"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    table_json = json.loads((material_dir / "table_items" / "科研经费占比_表1.json").read_text(encoding="utf-8"))

    assert "| 研发投入 | ![图片](image_items/科研经费占比_图1.png) |" in material_md
    assert (material_dir / "image_items" / "科研经费占比_图1.png").exists()
    assert table_json["table_model"]["cells"][3]["image_ref"] == "image_items/科研经费占比_图1.png"


def test_package_module_artifacts_scopes_toc_leaf_sections_by_same_page_y_bounds(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "PDF / 3、 补充文件 / 3.1、 投标保证金 / 3.1.1、 汇款凭证",
            1,
            1,
            "3.1、 投标保证金",
        ),
        _candidate(
            "PDF / 3、 补充文件 / 3.1、 投标保证金 / 3.1.2、 投标保证金银行保函（无、本项目采用电汇）",
            1,
            1,
            "3.1、 投标保证金",
        ),
        _candidate(
            "PDF / 3、 补充文件 / 3.1、 投标保证金 / 3.1.3、 银行基本账户证明扫描件",
            1,
            1,
            "3.1、 投标保证金",
        ),
    ]
    candidates[0].material_evidence = {"start_y": 100.0, "end_y": 300.0, "start_block_id": "h311", "end_block_id": "h312"}
    candidates[1].material_evidence = {"start_y": 300.0, "end_y": 500.0, "start_block_id": "h312", "end_block_id": "h313"}
    candidates[2].material_evidence = {"start_y": 500.0, "end_y": None, "start_block_id": "h313", "end_block_id": None}
    blocks = [
        PdfTextBlock(block_id="p", page_no=1, text="3、补充文件", bbox=[0, 20, 200, 40], block_no=1),
        PdfTextBlock(block_id="p31", page_no=1, text="3.1、投标保证金", bbox=[0, 60, 200, 80], block_no=2),
        PdfTextBlock(block_id="h311", page_no=1, text="3.1.1、汇款凭证", bbox=[0, 100, 200, 120], block_no=3),
        PdfTextBlock(block_id="h312", page_no=1, text="3.1.2、投标保证金银行保函（无、本项目采用电汇）", bbox=[0, 300, 400, 320], block_no=4),
        PdfTextBlock(block_id="h313", page_no=1, text="3.1.3、银行基本账户证明扫描件", bbox=[0, 500, 300, 520], block_no=5),
    ]
    images = [
        {"image_id": "transfer", "page_no": 1, "xref": 10, "width": 600, "height": 500, "rect": [20, 150, 300, 260], "ext": "jpeg"},
        {"image_id": "bank", "page_no": 1, "xref": 11, "width": 600, "height": 500, "rect": [20, 560, 300, 700], "ext": "jpeg"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "jpeg")),
        top_level_modules=["3、 补充文件"],
        planned_section_paths=[candidate.section_path for candidate in candidates],
    )

    base = tmp_path / "modules" / "3、 补充文件" / "3.1、 投标保证金"
    guarantee_md = (base / "3.1.2、 投标保证金银行保函（无、本项目采用电汇）" / "material.md").read_text(encoding="utf-8")
    transfer_md = (base / "3.1.1、 汇款凭证" / "material.md").read_text(encoding="utf-8")

    assert "3.1.2、投标保证金银行保函" in guarantee_md
    assert "汇款凭证_图1" not in guarantee_md
    assert "银行基本账户证明扫描件_图1" not in guarantee_md
    assert "3.1.1、汇款凭证" not in guarantee_md
    assert "3.1.3、银行基本账户证明扫描件" not in guarantee_md
    assert "![汇款凭证_图1](image_items/汇款凭证_图1.jpeg)" in transfer_md


def test_package_module_artifacts_scopes_tables_by_detected_region_not_expanded_crop(tmp_path: Path) -> None:
    candidate = _candidate(
        "PDF / 3、 补充文件 / 3.8、 商务评分标准涉及的支撑材料 / （2）、 部分业主出具的履约优秀证明8份",
        579,
        579,
        "（2）、 部分业主出具的履约优秀证明8份",
    )
    candidate.material_evidence = {"start_y": 80.0, "end_y": None, "start_block_id": "h2", "end_block_id": None}
    blocks = [
        PdfTextBlock(block_id="h2", page_no=579, text="（2）、部分业主出具的履约优秀证明8份", bbox=[0, 80, 300, 100], block_no=1),
    ]
    tables = [
        ParsedTable(
            table_id="expanded-before-title",
            page_no=579,
            rows=[["序号", "证明"], ["1", "优秀证明"]],
            bbox=[20, 40, 500, 260],
            table_region_bbox=[20, 120, 500, 260],
        ),
    ]

    package_module_artifacts(
        candidates=[candidate],
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
        top_level_modules=["3、 补充文件"],
        planned_section_paths=[candidate.section_path],
    )

    material_dir = (
        tmp_path
        / "modules"
        / "3、 补充文件"
        / "3.8、 商务评分标准涉及的支撑材料"
        / "（2）、 部分业主出具的履约优秀证明8份"
    )
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    table_jsons = list((material_dir / "table_items").glob("*.json"))

    assert table_jsons
    assert "| 序号 | 证明 |" in material_md
    assert "优秀证明" in material_md


def test_package_module_artifacts_keeps_table_when_original_bbox_matches_section_scope(tmp_path: Path) -> None:
    candidate = _candidate(
        "PDF / 3、 补充文件 / 3.8、 商务评分标准涉及的支撑材料 / （2）、 部分业主出具的履约优秀证明8份",
        579,
        579,
        "（2）、 部分业主出具的履约优秀证明8份",
    )
    candidate.material_evidence = {"start_y": 80.0, "end_y": 200.0, "start_block_id": "h2", "end_block_id": "next"}
    blocks = [
        PdfTextBlock(block_id="h2", page_no=579, text="（2）、部分业主出具的履约优秀证明8份", bbox=[0, 80, 300, 100], block_no=1),
        PdfTextBlock(block_id="next", page_no=579, text="1.1 目录不存在的小标题", bbox=[0, 200, 300, 220], block_no=2),
    ]
    tables = [
        ParsedTable(
            table_id="original-bbox-in-scope",
            page_no=579,
            rows=[["工程名称", "评价"], ["项目A", "优秀"]],
            bbox=[20, 120, 500, 180],
            table_region_bbox=[20, 240, 500, 300],
        ),
    ]

    package_module_artifacts(
        candidates=[candidate],
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
        top_level_modules=["3、 补充文件"],
        planned_section_paths=[candidate.section_path],
    )

    material_dir = (
        tmp_path
        / "modules"
        / "3、 补充文件"
        / "3.8、 商务评分标准涉及的支撑材料"
        / "（2）、 部分业主出具的履约优秀证明8份"
    )
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")

    assert "| 工程名称 | 评价 |" in material_md
    assert "项目A" in material_md


def test_package_module_artifacts_assigns_overlapping_table_to_leaf_section(tmp_path: Path) -> None:
    candidate = _candidate(
        "技术文件 / 2、 专项投标文件 / 2.1、 业绩文件 / 2.1.1、 供货及运行业绩表",
        12,
        12,
        "2.1.1、 供货及运行业绩表",
    )
    candidate.material_evidence = {"start_y": 415.0, "end_y": None, "start_block_id": "row-1", "end_block_id": None}
    blocks = [
        PdfTextBlock(block_id="row-1", page_no=12, text="1", bbox=[80, 416, 90, 426], block_no=1),
        PdfTextBlock(block_id="cell-1", page_no=12, text="国网湖北超高压公司", bbox=[165, 459, 273, 510], block_no=2),
    ]
    tables = [
        ParsedTable(
            table_id="supply-performance-table",
            page_no=12,
            rows=[["序号", "工程名称"], ["1", "国网湖北超高压公司"]],
            bbox=[33, 314, 576, 765],
            table_region_bbox=[70, 345, 539, 734],
        ),
    ]

    package_module_artifacts(
        candidates=[candidate],
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
        top_level_modules=["2、 专项投标文件"],
        planned_section_paths=[candidate.section_path],
    )

    material_dir = (
        tmp_path
        / "modules"
        / "2、 专项投标文件"
        / "2.1、 业绩文件"
        / "2.1.1、 供货及运行业绩表"
    )
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")

    assert (material_dir / "table_items" / "2.1.1、 供货及运行业绩表_表1.json").exists()
    assert "| 序号 | 工程名称 |" in material_md
    assert "国网湖北超高压公司" in material_md


def test_package_module_artifacts_keeps_long_folder_titles_readable_without_hash_suffix(tmp_path: Path) -> None:
    long_title = "（1.1）、 01-2025年35kV及以上输变电一次设备和装置材料供应业绩证明文件及运行维护服务评价材料补充说明附件资料"
    candidate = _candidate(
        f"PDF / 3、 补充文件 / {long_title}",
        1,
        1,
        long_title,
    )
    blocks = [
        PdfTextBlock(block_id="h1", page_no=1, text=long_title, bbox=[0, 80, 500, 100], block_no=1),
    ]

    package_module_artifacts(
        candidates=[candidate],
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
        top_level_modules=["3、 补充文件"],
        planned_section_paths=[candidate.section_path],
    )

    child_dirs = [path.name for path in (tmp_path / "modules" / "3、 补充文件").iterdir() if path.is_dir()]
    assert child_dirs
    assert not re.search(r"_[0-9a-f]{8}$", child_dirs[0])
    assert child_dirs[0].startswith("（1.1）、 01-2025年35kV及以上输变电一次设备和装置材料供应")


def test_package_module_artifacts_deduplicates_stream_text_and_skips_page_numbers(tmp_path: Path) -> None:
    candidate = _candidate(
        "商务文件 / 商务评审索引表",
        2,
        2,
        "商务评审索引表",
    )
    blocks = [
        PdfTextBlock(block_id="title", page_no=2, text="商务评审索引表", bbox=[255, 73, 346, 86], block_no=1),
        PdfTextBlock(block_id="page-no", page_no=2, text="2", bbox=[295, 789, 304, 798], block_no=2),
    ]
    stream_items = [
        PageMaterialItem(
            item_id="title",
            item_type="text",
            source_type="pdf_text",
            page_no=2,
            top_y=73,
            bbox=[255, 73, 346, 86],
            text="商务评审索引表",
            payload={"block_id": "title", "block_no": 1},
        ),
        PageMaterialItem(
            item_id="page-no",
            item_type="text",
            source_type="pdf_text",
            page_no=2,
            top_y=789,
            bbox=[295, 789, 304, 798],
            text="2",
            payload={"block_id": "page-no", "block_no": 2},
        ),
    ]

    package_module_artifacts(
        candidates=[candidate],
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
        page_material_items=stream_items,
    )

    ordered = json.loads((tmp_path / "modules" / "商务评审索引表" / "ordered_material.json").read_text(encoding="utf-8"))
    texts = [item.get("text") for item in ordered["items"] if item.get("type") == "text"]
    assert texts == ["商务评审索引表"]


def test_package_module_artifacts_deduplicates_pdf_and_pp_structure_text_in_markdown(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 投标保证保险",
            1,
            1,
            "投标保证保险",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=1, text="投标保证保险正文", bbox=[0, 100, 300, 120], block_no=1),
    ]
    page_material_items = [
        PageMaterialItem(
            item_id="pp-text-1",
            item_type="text",
            source_type="pp_structure_text_region",
            page_no=1,
            top_y=100,
            bbox=[0, 100, 300, 120],
            text="投标保证保险正文",
            payload={"ocr_texts": ["投标保证保险正文"]},
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
        page_material_items=page_material_items,
    )

    material_md = (tmp_path / "modules" / "投标保证保险" / "material.md").read_text(encoding="utf-8")
    assert material_md.count("投标保证保险正文") == 1


def test_package_module_artifacts_keeps_pp_ocr_text_out_of_material_markdown(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 企业营业执照扫描件",
            1,
            1,
            "企业营业执照扫描件",
        )
    ]
    images = [
        {"image_id": "license", "page_no": 1, "xref": 31, "width": 900, "height": 560, "rect": [10, 170, 420, 300], "ext": "png"},
    ]
    page_material_items = [
        PageMaterialItem(
            item_id="pp-text-license",
            item_type="text",
            source_type="pp_structure_text_region",
            page_no=1,
            top_y=180,
            bbox=[10, 180, 420, 280],
            text="营业执照\n统一社会信用代码 913302007251641924",
            payload={"ocr_texts": ["营业执照", "统一社会信用代码 913302007251641924"]},
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=[],
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"pdf-image", item.get("ext", "png")),
        page_material_items=page_material_items,
    )

    material_dir = tmp_path / "modules" / "企业营业执照扫描件"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))

    assert "统一社会信用代码" not in material_md
    assert "![企业营业执照扫描件_图1](image_items/企业营业执照扫描件_图1.png)" in material_md
    assert any(item.get("item_id") == "pp-text-license" for item in ordered["items"])


def test_package_module_artifacts_writes_authorization_identity_attachment_as_images_only_with_pp_text(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 法定代表人授权委托书 / 被授权人身份证等有效身份证件（扫描件）",
            1,
            1,
            "4、法定代表人授权委托书",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=1, text="附：被授权人身份证等有效身份证件（扫描件）", bbox=[0, 80, 300, 100], block_no=1),
        PdfTextBlock(block_id="b2", page_no=1, text="姓名 张三", bbox=[0, 120, 300, 140], block_no=2),
    ]
    images = [
        {"image_id": "front", "page_no": 1, "xref": 31, "width": 900, "height": 560, "rect": [10, 170, 420, 300], "ext": "png"},
        {"image_id": "back", "page_no": 1, "xref": 32, "width": 900, "height": 560, "rect": [10, 320, 420, 450], "ext": "png"},
    ]
    page_material_items = [
        PageMaterialItem(
            item_id="pp-text-id",
            item_type="text",
            source_type="pp_structure_text_region",
            page_no=1,
            top_y=120,
            bbox=[0, 120, 300, 140],
            text="姓名 张三\n身份证号 330000000000000000",
            payload={"ocr_texts": ["姓名 张三", "身份证号 330000000000000000"]},
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
        page_material_items=page_material_items,
    )

    material_dir = tmp_path / "modules" / "法定代表人授权委托书" / "被授权人身份证等有效身份证件（扫描件）"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    assert material_md.count("![") == 2
    assert "姓名 张三" not in material_md
    assert "身份证号" not in material_md
    assert "被授权人身份证等有效身份证件（扫描件）_图1.png" in material_md
    assert "被授权人身份证等有效身份证件（扫描件）_图2.png" in material_md


def test_package_module_artifacts_writes_image_only_material_markdown_in_order(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 法定代表人授权委托书 / 法定代表人（单位负责人）身份证（扫描件）",
            1,
            1,
            "4、法定代表人授权委托书",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=1, text="附：法定代表人（单位负责人）身份证（扫描件）", bbox=[0, 100, 300, 120], block_no=1),
    ]
    images = [
        {"image_id": "front", "page_no": 1, "xref": 31, "width": 900, "height": 560, "rect": [10, 170, 420, 300], "ext": "png"},
        {"image_id": "back", "page_no": 1, "xref": 32, "width": 900, "height": 560, "rect": [10, 320, 420, 450], "ext": "png"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
    )

    material_dir = tmp_path / "modules" / "法定代表人授权委托书" / "法定代表人（单位负责人）身份证（扫描件）"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    image_lines = [line for line in material_md.splitlines() if line.startswith("![")]

    assert image_lines == [
        "![法定代表人（单位负责人）身份证（扫描件）_图1](image_items/法定代表人（单位负责人）身份证（扫描件）_图1.png)",
        "![法定代表人（单位负责人）身份证（扫描件）_图2](image_items/法定代表人（单位负责人）身份证（扫描件）_图2.png)",
    ]
    assert "```json" not in material_md


def test_package_module_artifacts_packages_compound_financial_reports_by_detected_instances_and_children(tmp_path: Path) -> None:
    anchor = "商务文件 / 补充文件 / 财务状况 / 经会计师事务所或审计机构审计的财务会计报表"
    candidates = [
        _candidate(
            f"{anchor} / 利润表",
            10,
            25,
            "财务状况",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="i2022", page_no=10, text="2022年度财务报表", bbox=[0, 80, 200, 95], block_no=1, font_size=16),
        PdfTextBlock(block_id="generic2022", page_no=10, text="财务报表", bbox=[0, 100, 160, 115], block_no=2, font_size=15),
        PdfTextBlock(block_id="toc2022", page_no=11, text="目录", bbox=[0, 80, 100, 95], block_no=2, font_size=15),
        PdfTextBlock(block_id="profit2022", page_no=12, text="利润表", bbox=[0, 80, 100, 95], block_no=3, font_size=15),
        PdfTextBlock(block_id="note2022", page_no=13, text="财务报表附注", bbox=[0, 80, 120, 95], block_no=4, font_size=15),
        PdfTextBlock(block_id="i2023", page_no=20, text="2023年度财务报表", bbox=[0, 80, 200, 95], block_no=5, font_size=16),
        PdfTextBlock(block_id="toc2023", page_no=21, text="目录", bbox=[0, 80, 100, 95], block_no=6, font_size=15),
        PdfTextBlock(block_id="profit2023", page_no=22, text="利润表", bbox=[0, 80, 100, 95], block_no=7, font_size=15),
    ]
    images = [
        {"image_id": "toc-img", "page_no": 11, "xref": 101, "width": 600, "height": 500, "rect": [20, 120, 300, 360], "ext": "png"},
        {"image_id": "profit-img", "page_no": 12, "xref": 102, "width": 600, "height": 500, "rect": [20, 120, 300, 360], "ext": "png"},
        {"image_id": "note-img", "page_no": 13, "xref": 103, "width": 600, "height": 500, "rect": [20, 120, 300, 360], "ext": "png"},
        {"image_id": "profit-2023-img", "page_no": 22, "xref": 104, "width": 600, "height": 500, "rect": [20, 120, 300, 360], "ext": "png"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
        compound_material_rules=[
            {
                "excel_anchor_path": anchor,
                "instance_title_patterns": [r"20\d{2}.*财务报表"],
                "auto_detect_children": True,
                "store_unlisted_children": True,
            }
        ],
    )

    base = tmp_path / "modules" / "补充文件" / "财务状况" / "经会计师事务所或审计机构审计的财务会计报表"
    instance_meta = json.loads((base / "2022年度财务报表" / "compound_instance_meta.json").read_text(encoding="utf-8"))
    anchor_markdown = (base / "material.md").read_text(encoding="utf-8")
    instance_markdown = (base / "2022年度财务报表" / "material.md").read_text(encoding="utf-8")
    assert anchor_markdown.startswith("# 经会计师事务所或审计机构审计的财务会计报表")
    assert "[2022年度财务报表](2022年度财务报表/material.md)" in anchor_markdown
    assert "[2023年度财务报表](2023年度财务报表/material.md)" in anchor_markdown
    assert instance_markdown.startswith("# 2022年度财务报表")
    assert "[财务报表](财务报表/material.md)" not in instance_markdown
    assert "[目录](目录/material.md)" in instance_markdown
    assert "[利润表](利润表/material.md)" in instance_markdown
    assert (base / "2022年度财务报表" / "目录" / "image_items" / "目录_图1.json").exists()
    assert (base / "2022年度财务报表" / "利润表" / "image_items" / "利润表_图1.json").exists()
    assert (base / "2022年度财务报表" / "财务报表附注" / "image_items" / "财务报表附注_图1.json").exists()
    assert (base / "2023年度财务报表" / "利润表" / "image_items" / "利润表_图1.json").exists()
    assert not (base / "2022年度财务报表" / "财务报表").exists()
    assert not (base / "利润表" / "image_items" / "目录_图1.json").exists()
    assert instance_meta["instance_path"] == "商务文件 / 补充文件 / 财务状况 / 经会计师事务所或审计机构审计的财务会计报表 / 2022年度财务报表"
    assert instance_meta["rule_anchor_path"] == anchor
    assert instance_meta["children"][0]["material_path"].endswith("/ 目录")
    assert instance_meta["children"][0]["material_types"] == ["text", "image"]
    assert instance_meta["children"][0]["dominant_material_type"] == "mixed"


def test_package_module_artifacts_uses_excel_instance_layer_for_compound_financial_reports(tmp_path: Path) -> None:
    anchor = "商务文件 / 补充文件 / 财务状况 / 经会计师事务所或审计机构审计的财务会计报表"
    candidates = [
        _candidate(f"{anchor} / 3.7.1、2022 年度财务审计报告 / 封面", 10, 10, "财务状况"),
        _candidate(f"{anchor} / 3.7.1、2022 年度财务审计报告 / 利润表", 12, 12, "财务状况"),
        _candidate(f"{anchor} / 3.7.2、2023 年度财务审计报告 / 封面", 20, 20, "财务状况"),
    ]
    blocks = [
        PdfTextBlock(block_id="cover2022", page_no=10, text="封面正文", bbox=[0, 80, 200, 95], block_no=1, font_size=10),
        PdfTextBlock(block_id="profit2022", page_no=12, text="利润表正文", bbox=[0, 80, 200, 95], block_no=2, font_size=10),
        PdfTextBlock(block_id="cover2023", page_no=20, text="封面正文", bbox=[0, 80, 200, 95], block_no=3, font_size=10),
    ]
    images = [
        {"image_id": "cover-img", "page_no": 10, "xref": 101, "width": 600, "height": 500, "rect": [20, 120, 300, 360], "ext": "png"},
        {"image_id": "profit-img", "page_no": 12, "xref": 102, "width": 600, "height": 500, "rect": [20, 120, 300, 360], "ext": "png"},
        {"image_id": "cover-2023-img", "page_no": 20, "xref": 103, "width": 600, "height": 500, "rect": [20, 120, 300, 360], "ext": "png"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
        compound_material_rules=[
            {
                "excel_anchor_path": anchor,
                "instance_title_patterns": [r"20\d{2}.*(?:财务|审计).*报告"],
                "auto_detect_children": True,
                "store_unlisted_children": True,
            }
        ],
    )

    base = tmp_path / "modules" / "补充文件" / "财务状况" / "经会计师事务所或审计机构审计的财务会计报表"
    anchor_markdown = (base / "material.md").read_text(encoding="utf-8")
    instance_markdown = (base / "3.7.1、2022 年度财务审计报告" / "material.md").read_text(encoding="utf-8")
    instance_meta = json.loads((base / "3.7.1、2022 年度财务审计报告" / "compound_instance_meta.json").read_text(encoding="utf-8"))

    assert "[3.7.1、2022 年度财务审计报告](3.7.1、2022 年度财务审计报告/material.md)" in anchor_markdown
    assert "[封面](封面/material.md)" in instance_markdown
    assert "[利润表](利润表/material.md)" in instance_markdown
    assert (base / "3.7.1、2022 年度财务审计报告" / "封面" / "image_items" / "封面_图1.json").exists()
    assert (base / "3.7.1、2022 年度财务审计报告" / "利润表" / "image_items" / "利润表_图1.json").exists()
    assert not (base / "封面").exists()
    assert instance_meta["instance_title"] == "3.7.1、2022 年度财务审计报告"
    assert instance_meta["children"][0]["material_path"].endswith("/ 3.7.1、2022 年度财务审计报告 / 封面")


def test_package_module_artifacts_keeps_planned_compound_paths_when_no_compound_instance_was_built(tmp_path: Path) -> None:
    anchor = "商务文件 / 补充文件 / 财务状况 / 经会计师事务所或审计机构审计的财务会计报表"

    package_module_artifacts(
        candidates=[],
        blocks=[],
        tables=[],
        images=[],
        out_dir=tmp_path,
        planned_section_paths=[
            f"{anchor} / 3.7.1、2022 年度财务审计报告 / 封面",
            f"{anchor} / 3.7.1、2022 年度财务审计报告 / 利润表",
        ],
        compound_material_rules=[
            {
                "excel_anchor_path": anchor,
                "instance_title_patterns": [r"20\d{2}.*(?:财务|审计).*报告"],
                "auto_detect_children": True,
                "store_unlisted_children": True,
            }
        ],
    )

    base = tmp_path / "modules" / "补充文件" / "财务状况" / "经会计师事务所或审计机构审计的财务会计报表"
    assert (base / "material.md").exists()
    assert (base / "3.7.1、2022 年度财务审计报告" / "封面" / "material.md").exists()
    assert (base / "3.7.1、2022 年度财务审计报告" / "利润表" / "material.md").exists()


def test_package_module_artifacts_keeps_matched_compound_child_when_no_instance_was_built(tmp_path: Path) -> None:
    anchor = "商务文件 / 补充文件 / 财务状况 / 经会计师事务所或审计机构审计的财务会计报表"
    candidates = [
        _candidate(f"{anchor} / 利润表", 12, 12, "财务状况"),
    ]
    blocks = [
        PdfTextBlock(block_id="profit2022", page_no=12, text="利润表正文", bbox=[0, 80, 200, 95], block_no=1, font_size=10),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
        compound_material_rules=[
            {
                "excel_anchor_path": anchor,
                "instance_title_patterns": [r"20\d{2}.*(?:财务|审计).*报告"],
                "auto_detect_children": True,
                "store_unlisted_children": True,
            }
        ],
    )

    base = tmp_path / "modules" / "补充文件" / "财务状况" / "经会计师事务所或审计机构审计的财务会计报表"
    assert (base / "利润表" / "material.md").exists()
    assert "利润表正文" in (base / "利润表" / "material.md").read_text(encoding="utf-8")


def test_package_module_artifacts_uses_candidate_container_as_compound_instance(tmp_path: Path) -> None:
    anchor = "商务文件 / 补充文件 / 财务状况 / 经会计师事务所或审计机构审计的财务会计报表"
    candidates = [
        _candidate(f"{anchor} / 利润表", 12, 12, "3.7.1、2022 年度财务审计报告"),
        _candidate(f"{anchor} / 资产负债表", 11, 11, "3.7.1、2022 年度财务审计报告"),
    ]
    blocks = [
        PdfTextBlock(block_id="balance2022", page_no=11, text="资产负债表正文", bbox=[0, 80, 200, 95], block_no=1, font_size=10),
        PdfTextBlock(block_id="profit2022", page_no=12, text="利润表正文", bbox=[0, 80, 200, 95], block_no=2, font_size=10),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
        compound_material_rules=[
            {
                "excel_anchor_path": anchor,
                "instance_title_patterns": [r"20\d{2}.*(?:财务|审计).*报告"],
                "auto_detect_children": True,
                "store_unlisted_children": True,
            }
        ],
    )

    base = tmp_path / "modules" / "补充文件" / "财务状况" / "经会计师事务所或审计机构审计的财务会计报表"
    assert (base / "3.7.1、2022 年度财务审计报告" / "利润表" / "material.md").exists()
    assert (base / "3.7.1、2022 年度财务审计报告" / "资产负债表" / "material.md").exists()
    assert not (base / "利润表").exists()
    assert "利润表正文" in (base / "3.7.1、2022 年度财务审计报告" / "利润表" / "material.md").read_text(encoding="utf-8")


def test_package_module_artifacts_ignores_pp_structure_titles_for_financial_instance_headings(tmp_path: Path) -> None:
    anchor = "商务文件 / 补充文件 / 财务状况 / 经会计师事务所或审计机构审计的财务会计报表"
    candidates = [
        _candidate(f"{anchor} / 利润表", 12, 22, "财务状况"),
    ]
    blocks = [
        PdfTextBlock(block_id="status-title", page_no=9, text="财务状况", bbox=[0, 80, 200, 95], block_no=1, font_size=16),
        PdfTextBlock(block_id="profit2022", page_no=12, text="利润表正文", bbox=[0, 130, 200, 150], block_no=2, font_size=10),
        PdfTextBlock(block_id="profit2023", page_no=22, text="利润表正文", bbox=[0, 130, 200, 150], block_no=3, font_size=10),
    ]
    page_material_items = [
        PageMaterialItem(
            item_id="pp-instance-2022",
            item_type="text",
            source_type="pp_structure_text",
            page_no=10,
            top_y=80,
            bbox=[0, 80, 300, 100],
            text="3.7.1、2022 年度财务审计报告",
            payload={"layout_label": "doc_title"},
        ),
        PageMaterialItem(
            item_id="pp-child-2022",
            item_type="text",
            source_type="pp_structure_text",
            page_no=12,
            top_y=80,
            bbox=[0, 80, 120, 100],
            text="利润表",
            payload={"layout_label": "paragraph_title"},
        ),
        PageMaterialItem(
            item_id="pp-instance-2023",
            item_type="text",
            source_type="pp_structure_text",
            page_no=20,
            top_y=80,
            bbox=[0, 80, 300, 100],
            text="3.7.2、2023 年度财务审计报告",
            payload={"layout_label": "doc_title"},
        ),
        PageMaterialItem(
            item_id="pp-child-2023",
            item_type="text",
            source_type="pp_structure_text",
            page_no=22,
            top_y=80,
            bbox=[0, 80, 120, 100],
            text="利润表",
            payload={"layout_label": "paragraph_title"},
        ),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
        compound_material_rules=[
            {
                "excel_anchor_path": anchor,
                "instance_title_patterns": [r"20\d{2}.*(?:财务|审计).*报告"],
                "auto_detect_children": True,
                "store_unlisted_children": True,
            }
        ],
        page_material_items=page_material_items,
    )

    base = tmp_path / "modules" / "补充文件" / "财务状况" / "经会计师事务所或审计机构审计的财务会计报表"
    anchor_markdown = (base / "material.md").read_text(encoding="utf-8")

    assert "3.7.1、2022 年度财务审计报告" not in anchor_markdown
    assert "3.7.2、2023 年度财务审计报告" not in anchor_markdown
    assert not (base / "3.7.1、2022 年度财务审计报告").exists()
    assert (base / "利润表" / "material.md").exists()


def test_package_module_artifacts_prefers_pdf_embedded_images_over_pp_structure_crops(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 企业营业执照扫描件",
            1,
            1,
            "企业营业执照扫描件",
        )
    ]
    images = [
        {"image_id": "pdf-img", "page_no": 1, "xref": 31, "width": 900, "height": 560, "rect": [10, 170, 420, 300], "ext": "png"},
    ]
    page_material_items = [
        PageMaterialItem(
            item_id="pp-image-1",
            item_type="image",
            source_type="pp_structure_image_region",
            page_no=1,
            top_y=170,
            bbox=[10, 170, 420, 300],
            text="",
            payload={"layout_label": "image", "page_width": 500, "page_height": 700},
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=[],
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"pdf-image", item.get("ext", "png")),
        page_material_items=page_material_items,
    )

    material_dir = tmp_path / "modules" / "企业营业执照扫描件"
    image_files = sorted(path.name for path in (material_dir / "image_items").glob("*.png"))
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))

    assert image_files == ["企业营业执照扫描件_图1.png"]
    assert all(item.get("item_id") != "pp-image-1" for item in ordered["items"])


def test_package_module_artifacts_suppresses_text_inside_exported_image_regions(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 技术方案 / 系统架构图",
            1,
            1,
            "系统架构图",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="系统架构图", bbox=[20, 40, 180, 60], block_no=1),
        PdfTextBlock(block_id="shape-text", page_no=1, text="流程节点文字", bbox=[120, 130, 220, 150], block_no=2),
        PdfTextBlock(block_id="body", page_no=1, text="图后说明文字", bbox=[20, 320, 220, 340], block_no=3),
    ]
    images = [
        {"image_id": "shape-img", "page_no": 1, "xref": 31, "width": 800, "height": 360, "rect": [90, 100, 330, 260], "ext": "png"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"shape-image", item.get("ext", "png")),
    )

    material_dir = tmp_path / "modules" / "技术方案" / "系统架构图"
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")

    shape_text = next(item for item in ordered["items"] if item.get("block_id") == "shape-text")
    assert shape_text["material_role"] == "image_text"
    assert shape_text["suppressed_by_image_id"] == "shape-img"
    assert shape_text["suppressed_reason"] == "image_geometry"
    assert "流程节点文字" not in material_md
    assert "图后说明文字" in material_md
    assert "![系统架构图_图1](image_items/系统架构图_图1.png)" in material_md


def test_package_module_artifacts_skips_decorative_image_exports(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "技术文件 / 技术特性参数表",
            1,
            1,
            "技术特性参数表",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="技术特性参数表", bbox=[20, 70, 220, 92], block_no=1),
        PdfTextBlock(block_id="body", page_no=1, text="正文内容", bbox=[20, 120, 220, 140], block_no=2),
    ]
    images = [
        {
            "image_id": "stamp-img",
            "page_no": 1,
            "xref": 31,
            "width": 260,
            "height": 260,
            "rect": [300, 520, 420, 640],
            "ext": "png",
            "image_kind": "seal_or_stamp",
        }
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"stamp-image", item.get("ext", "png")),
    )

    material_dir = tmp_path / "modules" / "技术特性参数表"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))["items"]

    assert "正文内容" in material_md
    assert "![技术特性参数表_图1]" not in material_md
    assert not (material_dir / "image_items" / "技术特性参数表_图1.png").exists()
    assert not (material_dir / "image_items" / "技术特性参数表_图1.json").exists()
    assert not any(item.get("image_id") == "stamp-img" for item in ordered)


def test_package_module_artifacts_classifies_red_stamp_image_pixels_as_decorative(tmp_path: Path) -> None:
    from PIL import Image, ImageDraw

    candidates = [
        _candidate(
            "技术文件 / 技术特性参数表",
            1,
            1,
            "技术特性参数表",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="技术特性参数表", bbox=[20, 70, 220, 92], block_no=1),
        PdfTextBlock(block_id="body", page_no=1, text="正文内容", bbox=[20, 120, 220, 140], block_no=2),
    ]
    stamp = Image.new("RGBA", (160, 160), (0, 0, 0, 255))
    draw = ImageDraw.Draw(stamp)
    for offset in range(5):
        draw.ellipse([12 + offset, 12 + offset, 148 - offset, 148 - offset], outline=(255, 0, 0, 255), width=1)
    draw.line([35, 80, 125, 80], fill=(255, 0, 0, 255), width=5)
    draw.line([80, 35, 80, 125], fill=(255, 0, 0, 255), width=5)
    draw.polygon([(80, 52), (88, 72), (110, 72), (92, 85), (100, 108), (80, 94), (60, 108), (68, 85), (50, 72), (72, 72)], outline=(255, 0, 0, 255))
    buffer = BytesIO()
    stamp.save(buffer, format="PNG")
    stamp_bytes = buffer.getvalue()
    images = [
        {
            "image_id": "stamp-img",
            "page_no": 1,
            "xref": 31,
            "width": 160,
            "height": 160,
            "rect": [300, 520, 420, 640],
            "ext": "png",
        }
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (stamp_bytes, item.get("ext", "png")),
    )

    material_dir = tmp_path / "modules" / "技术特性参数表"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))["items"]

    assert "正文内容" in material_md
    assert "![技术特性参数表_图1]" not in material_md
    assert not (material_dir / "image_items" / "技术特性参数表_图1.png").exists()
    assert not (material_dir / "image_items" / "技术特性参数表_图1.json").exists()
    assert not any(item.get("image_id") == "stamp-img" for item in ordered)


def test_write_material_markdown_skips_stamp_images_without_material_role(tmp_path: Path) -> None:
    image_path = tmp_path / "image_items" / "stamp.png"
    image_path.parent.mkdir()
    image_path.write_bytes(b"stamp")
    _write_material_markdown(
        tmp_path,
        "技术特性参数表",
        [
            {
                "type": "text",
                "item_type": "text",
                "text": "正文内容",
                "source_type": "pdf_text",
            },
            {
                "type": "image",
                "item_type": "image",
                "image_id": "stamp-img",
                "image_title": "技术特性参数表_图1",
                "image_kind": "seal_or_stamp",
                "file_path": str(image_path),
            },
        ],
    )

    material_md = (tmp_path / "material.md").read_text(encoding="utf-8")
    assert "正文内容" in material_md
    assert "![技术特性参数表_图1]" not in material_md


def test_write_material_markdown_keeps_tail_note_containing_table_header_phrase(tmp_path: Path) -> None:
    table_dir = tmp_path / "table_items"
    table_dir.mkdir()
    table_json = table_dir / "关系说明表.json"
    table_json.write_text(
        json.dumps(
            {
                "rows": [
                    ["人员姓名", "与国网公司系统人员关系", "任职状态"],
                    ["无", "", ""],
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    _write_material_markdown(
        tmp_path,
        "投标人与国家电网公司系统人员关系说明",
        [
            {
                "type": "table",
                "item_type": "table",
                "table_id": "table-1",
                "payload_ref": "table_items/关系说明表.json",
            },
            {
                "type": "text",
                "item_type": "text",
                "item_id": "note-2",
                "text": "2.与国网公司系统人员关系，应具体填写“本人”、“配偶”、“子女”、“子女配偶”。",
                "source_type": "pdf_text",
            },
        ],
    )

    material_md = (tmp_path / "material.md").read_text(encoding="utf-8")
    assert "2.与国网公司系统人员关系，应具体填写" in material_md


def test_package_module_artifacts_keeps_relation_note_containing_table_header_phrase(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 3、补充文件 / 3.2、投标人与国家电网公司系统人员关系说明",
            13,
            13,
            "3.2、投标人与国家电网公司系统人员关系说明",
        )
    ]
    relation_note = "2.与国网公司系统人员关系，应具体填写“本人”、“配偶”、“子女”、“子女配偶”。"
    blocks = [
        PdfTextBlock(
            block_id="title",
            page_no=13,
            text="3.2、投标人与国家电网公司系统人员关系说明",
            bbox=[20, 70, 500, 92],
            block_no=1,
        ),
        PdfTextBlock(
            block_id="table-header",
            page_no=13,
            text="人员姓名\n与国网公司系统人员关系\n任职状态",
            bbox=[40, 150, 540, 180],
            block_no=2,
        ),
        PdfTextBlock(
            block_id="relation-note",
            page_no=13,
            text=relation_note,
            bbox=[40, 380, 540, 400],
            block_no=3,
        ),
    ]
    tables = [
        ParsedTable(
            table_id="relation-table",
            page_no=13,
            rows=[
                ["人员姓名", "与国网公司系统人员关系", "任职状态"],
                ["无", "", ""],
            ],
            bbox=[32, 130, 560, 360],
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material_dir = tmp_path / "modules" / "3、补充文件" / "3.2、投标人与国家电网公司系统人员关系说明"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))["items"]
    note_item = next(item for item in ordered if item.get("block_id") == "relation-note")

    assert relation_note in material_md
    assert note_item["material_role"] == "body_text"


def test_write_material_markdown_skips_placeholder_row_text_repeated_after_table(tmp_path: Path) -> None:
    table_dir = tmp_path / "table_items"
    table_dir.mkdir()
    table_json = table_dir / "关系说明表.json"
    table_json.write_text(
        json.dumps(
            {
                "rows": [
                    ["人员姓名", "性别", "身份证号", "职务", "任职时间", "与国网公司系统人员关系"],
                    ["无", "—", "—", "—", "—", "—"],
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    _write_material_markdown(
        tmp_path,
        "投标人与国家电网公司系统人员关系说明",
        [
            {
                "type": "table",
                "item_type": "table",
                "table_id": "table-1",
                "payload_ref": "table_items/关系说明表.json",
            },
            {
                "type": "text",
                "item_type": "text",
                "item_id": "placeholder-repeat",
                "text": "无\n— —\n—\n—\n—",
                "source_type": "pdf_text",
            },
        ],
    )

    material_md = (tmp_path / "material.md").read_text(encoding="utf-8")
    assert "| 无 | — | — | — | — | — |" in material_md
    assert "\n无\n— —\n—\n—\n—\n" not in material_md


def test_write_material_markdown_skips_tiny_badge_images_without_material_role(tmp_path: Path) -> None:
    image_path = tmp_path / "image_items" / "logo.png"
    image_path.parent.mkdir()
    image_path.write_bytes(b"logo")
    _write_material_markdown(
        tmp_path,
        "企业资质",
        [
            {
                "type": "text",
                "item_type": "text",
                "text": "正文内容",
                "source_type": "pdf_text",
            },
            {
                "type": "image",
                "item_type": "image",
                "image_id": "logo-img",
                "image_title": "企业资质_图1",
                "width": 140,
                "height": 36,
                "rect": [80, 90, 130, 104],
                "file_path": str(image_path),
            },
        ],
    )

    material_md = (tmp_path / "material.md").read_text(encoding="utf-8")
    assert "正文内容" in material_md
    assert "![企业资质_图1]" not in material_md


def test_package_module_artifacts_keeps_content_images_in_markdown(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "技术文件 / 系统架构图",
            1,
            1,
            "系统架构图",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="系统架构图", bbox=[20, 70, 220, 92], block_no=1),
    ]
    images = [
        {
            "image_id": "content-img",
            "page_no": 1,
            "xref": 41,
            "width": 900,
            "height": 500,
            "rect": [80, 180, 500, 430],
            "ext": "png",
        }
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"content-image", item.get("ext", "png")),
    )

    material_dir = tmp_path / "modules" / "系统架构图"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))["items"]
    ordered_image = next(item for item in ordered if item.get("image_id") == "content-img")

    assert "![系统架构图_图1](image_items/系统架构图_图1.png)" in material_md
    assert ordered_image["material_role"] == "image"
    assert ordered_image["image_kind"] == "content_image"


def test_package_module_artifacts_suppresses_text_inside_pp_structure_image_regions(tmp_path: Path) -> None:
    pdf_path = _write_demo_pdf(tmp_path / "demo.pdf", page_count=1)
    candidates = [
        _candidate(
            "商务文件 / 技术方案 / 图标说明",
            1,
            1,
            "图标说明",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="图标说明", bbox=[20, 40, 160, 60], block_no=1),
        PdfTextBlock(block_id="icon-text", page_no=1, text="图标内部文字", bbox=[130, 135, 230, 155], block_no=2),
        PdfTextBlock(block_id="body", page_no=1, text="图标后的正文", bbox=[20, 330, 220, 350], block_no=3),
    ]
    page_material_items = [
        PageMaterialItem(
            item_id="pp-image-icon",
            item_type="image",
            source_type="pp_structure_image_region",
            page_no=1,
            top_y=110,
            bbox=[100, 110, 330, 260],
            text="",
            payload={"layout_label": "image", "page_width": 595, "page_height": 842},
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
        pdf_path=pdf_path,
        page_material_items=page_material_items,
    )

    material_dir = tmp_path / "modules" / "技术方案" / "图标说明"
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")

    icon_text = next(item for item in ordered["items"] if item.get("block_id") == "icon-text")
    assert icon_text["material_role"] == "image_text"
    assert icon_text["suppressed_by_image_id"] == "pp-image-icon"
    assert "图标内部文字" not in material_md
    assert "图标后的正文" in material_md
    assert "![图标说明_图1](image_items/图标说明_图1.png)" in material_md


def test_package_module_artifacts_keeps_global_fu_content_inline(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 法定代表人授权委托书 / 法定代表人授权委托书",
            1,
            2,
            "4、法定代表人授权委托书",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=1, text="4、法定代表人授权委托书", bbox=[0, 20, 300, 40], block_no=1),
        PdfTextBlock(block_id="b2", page_no=1, text="授权正文第一页", bbox=[0, 60, 300, 80], block_no=2),
        PdfTextBlock(block_id="b3", page_no=1, text="附：法定代表人（单位负责人）身份证（扫描件）", bbox=[0, 120, 300, 140], block_no=3),
        PdfTextBlock(block_id="b4", page_no=1, text="身份证说明文字", bbox=[0, 160, 300, 180], block_no=4),
    ]
    images = [
        {"image_id": "img-1", "page_no": 1, "xref": 20, "width": 600, "height": 500, "rect": [10, 200, 150, 360], "ext": "png"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
    )

    section_dir = tmp_path / "modules" / "法定代表人授权委托书" / "法定代表人授权委托书"
    assert (section_dir / "ordered_material.json").exists()

    parent_ordered = json.loads((section_dir / "ordered_material.json").read_text(encoding="utf-8"))
    material_md = (section_dir / "material.md").read_text(encoding="utf-8")

    assert not any(item["item_type"] == "submaterial" for item in parent_ordered["items"])
    assert "附：法定代表人（单位负责人）身份证（扫描件）" in material_md
    assert "身份证说明文字" in material_md
    assert "![法定代表人（单位负责人）身份证（扫描件）_图1](image_items/法定代表人（单位负责人）身份证（扫描件）_图1.png)" in material_md
    assert not (section_dir / "submaterials").exists()


def test_package_module_artifacts_keeps_duplicate_fu_lines_inline(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 法定代表人授权委托书 / 法定代表人授权委托书",
            1,
            2,
            "4、法定代表人授权委托书",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=1, text="4、法定代表人授权委托书", bbox=[0, 20, 300, 40], block_no=1),
        PdfTextBlock(block_id="b2", page_no=1, text="附：营业执照副本", bbox=[0, 80, 300, 100], block_no=2),
        PdfTextBlock(block_id="b3", page_no=1, text="第一页附件说明", bbox=[0, 120, 300, 140], block_no=3),
        PdfTextBlock(block_id="b4", page_no=2, text="附：营业执照副本", bbox=[0, 80, 300, 100], block_no=4),
        PdfTextBlock(block_id="b5", page_no=2, text="第二页附件说明", bbox=[0, 120, 300, 140], block_no=5),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
    )

    material_dir = tmp_path / "modules" / "法定代表人授权委托书" / "法定代表人授权委托书"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    assert material_md.count("附：营业执照副本") == 2
    assert "第一页附件说明" in material_md
    assert "第二页附件说明" in material_md
    assert not (material_dir / "submaterials").exists()


def test_package_module_artifacts_preserves_cross_page_material_stream_order(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 法定代表人授权委托书 / 法定代表人授权委托书",
            10,
            12,
            "4、法定代表人授权委托书",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=10, text="4、法定代表人授权委托书", bbox=[0, 20, 300, 40], block_no=1),
    ]
    page_material_items = [
        PageMaterialItem(
            item_id="pp-text-10",
            item_type="text",
            source_type="pp_structure_text_region",
            page_no=10,
            top_y=80,
            bbox=[0, 80, 300, 120],
            text="第一页授权正文",
            payload={"ocr_texts": ["第一页授权正文"]},
        ),
        PageMaterialItem(
            item_id="pp-image-11",
            item_type="image",
            source_type="pp_structure_image_region",
            page_no=11,
            top_y=100,
            bbox=[10, 100, 500, 500],
            text="",
            payload={"layout_label": "image"},
        ),
        PageMaterialItem(
            item_id="pp-table-12",
            item_type="table",
            source_type="pp_structure_table_region",
            page_no=12,
            top_y=90,
            bbox=[20, 90, 520, 360],
            text="",
            payload={"layout_label": "table"},
        ),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
        page_material_items=page_material_items,
    )

    ordered_path = tmp_path / "modules" / "法定代表人授权委托书" / "法定代表人授权委托书" / "ordered_material.json"
    ordered = json.loads(ordered_path.read_text(encoding="utf-8"))
    stream_items = [item for item in ordered["items"] if str(item.get("source_type", "")).startswith("pp_structure")]

    assert [item["item_id"] for item in stream_items] == ["pp-text-10", "pp-image-11"]
    assert [item["item_type"] for item in stream_items] == ["text", "image"]
    assert stream_items[0]["text"] == "第一页授权正文"
    assert stream_items[0]["payload"]["ocr_texts"] == ["第一页授权正文"]


def test_pp_structure_table_region_filters_duplicate_pdf_text_but_keeps_caption(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 3、补充文件 / 3.2、投标人与国家电网公司系统人员关系说明",
            1,
            1,
            "3.2、投标人与国家电网公司系统人员关系说明",
        )
    ]
    blocks = [
        PdfTextBlock(
            block_id="title",
            page_no=1,
            text="3.2、投标人与国家电网公司系统人员关系说明",
            bbox=[20, 40, 500, 60],
            block_no=1,
        ),
        PdfTextBlock(
            block_id="intro",
            page_no=1,
            text="具体情况如下。",
            bbox=[20, 80, 240, 100],
            block_no=2,
        ),
        PdfTextBlock(
            block_id="caption",
            page_no=1,
            text="《本企业员工与国家电网公司系统人员关系说明表》",
            bbox=[110, 122, 420, 142],
            block_no=3,
        ),
        PdfTextBlock(
            block_id="cell-text-1",
            page_no=1,
            text="本企业人员基本信息",
            bbox=[50, 165, 180, 185],
            block_no=4,
        ),
        PdfTextBlock(
            block_id="cell-text-2",
            page_no=1,
            text="国家电网公司系统人员基本信息",
            bbox=[260, 165, 470, 185],
            block_no=5,
        ),
        PdfTextBlock(
            block_id="after",
            page_no=1,
            text="1.本表的人员只统计企业法人、出资人、高管涉及的具体情形。",
            bbox=[20, 370, 560, 390],
            block_no=6,
        ),
    ]
    tables = [
        ParsedTable(
            table_id="pp-table-1",
            page_no=1,
            rows=[],
            bbox=[40, 120, 560, 350],
            source_type="pp_structure_table",
            table_content="<table><tr><td>本企业人员基本信息</td></tr></table>",
            source_detail="layout_det_res",
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
    )

    material = (
        tmp_path
        / "modules"
        / "3、补充文件"
        / "3.2、投标人与国家电网公司系统人员关系说明"
        / "material.md"
    ).read_text(encoding="utf-8")

    assert "具体情况如下。" in material
    assert "《本企业员工与国家电网公司系统人员关系说明表》" in material
    assert "[表格：" in material
    assert "1.本表的人员只统计企业法人、出资人、高管涉及的具体情形。" in material
    assert "本企业人员基本信息" not in material
    assert "国家电网公司系统人员基本信息" not in material


def test_ordered_material_skips_pp_structure_table_region_when_table_item_exists(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 1、测试章节",
            1,
            1,
            "1、测试章节",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="1、测试章节", bbox=[20, 40, 200, 60], block_no=1),
    ]
    tables = [
        ParsedTable(
            table_id="pp-table-1",
            page_no=1,
            rows=[["字段", "内容"]],
            bbox=[20, 100, 420, 240],
            source_type="pp_structure_table",
        )
    ]
    page_material_items = [
        PageMaterialItem(
            item_id="pp-table-region-1",
            item_type="table",
            source_type="pp_structure_table_region",
            page_no=1,
            top_y=100,
            bbox=[21, 101, 421, 241],
            payload={"layout_label": "table"},
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
        page_material_items=page_material_items,
    )

    material = (tmp_path / "modules" / "1、测试章节" / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((tmp_path / "modules" / "1、测试章节" / "ordered_material.json").read_text(encoding="utf-8"))

    assert material.count("| 字段 | 内容 |") == 1
    assert sum(1 for item in ordered["items"] if item["item_type"] == "table") == 1


def test_package_module_artifacts_skips_stream_images_inside_table_regions(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "技术文件 / 3.8.11、科研经费占比",
            1,
            1,
            "3.8.11、科研经费占比",
        )
    ]
    image_path = tmp_path / "table-cell-image.png"
    image_path.write_bytes(b"fake-image")
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="3.8.11、科研经费占比", bbox=[20, 40, 220, 60], block_no=1),
        PdfTextBlock(block_id="cell-1", page_no=1, text="项目", bbox=[40, 120, 90, 140], block_no=2),
        PdfTextBlock(block_id="cell-2", page_no=1, text="2024 年", bbox=[160, 120, 230, 140], block_no=3),
        PdfTextBlock(block_id="cell-3", page_no=1, text="研发投入金额（元）", bbox=[40, 160, 150, 180], block_no=4),
    ]
    tables = [
        ParsedTable(
            table_id="table-with-image-cell",
            page_no=1,
            rows=[["项目", "2024 年"], ["研发投入金额（元）", "[图片]"]],
            bbox=[30, 110, 520, 320],
            source_type="pp_structure_table",
        )
    ]
    page_material_items = [
        PageMaterialItem(
            item_id="pp-image-in-table",
            item_type="image",
            source_type="pp_structure_image_region",
            page_no=1,
            top_y=210,
            bbox=[300, 210, 490, 300],
            text="",
            file_path=str(image_path),
            payload={"layout_label": "image"},
        )
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=tables,
        images=[],
        out_dir=tmp_path,
        page_material_items=page_material_items,
    )

    material_dir = tmp_path / "modules" / "3.8.11、科研经费占比"
    material = (material_dir / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))

    assert "| 项目 | 2024 年 |" in material
    assert "[图片]" in material
    assert "![" not in material
    assert "image" not in ordered["material_types"]
    assert all(item.get("item_id") != "pp-image-in-table" for item in ordered["items"])


def test_package_module_artifacts_skips_repeated_header_footer_images(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "技术文件 / 1、测试章节",
            1,
            2,
            "1、测试章节",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="1、测试章节", bbox=[20, 100, 220, 120], block_no=1),
        PdfTextBlock(block_id="body", page_no=1, text="章节正文", bbox=[20, 140, 220, 160], block_no=2),
    ]
    images = [
        {"image_id": "header-1", "page_no": 1, "xref": 101, "width": 260, "height": 80, "rect": [24, 18, 154, 58], "ext": "png"},
        {"image_id": "header-2", "page_no": 2, "xref": 102, "width": 260, "height": 80, "rect": [24, 18, 154, 58], "ext": "png"},
        {"image_id": "wide-header-1", "page_no": 1, "xref": 111, "width": 1100, "height": 180, "rect": [0, 0, 595, 92], "ext": "png"},
        {"image_id": "wide-header-2", "page_no": 2, "xref": 112, "width": 1100, "height": 180, "rect": [0, 0, 595, 92], "ext": "png"},
        {"image_id": "footer-1", "page_no": 1, "xref": 201, "width": 300, "height": 60, "rect": [420, 790, 560, 822], "ext": "png"},
        {"image_id": "footer-2", "page_no": 2, "xref": 202, "width": 300, "height": 60, "rect": [420, 790, 560, 822], "ext": "png"},
        {"image_id": "body-img", "page_no": 1, "xref": 301, "width": 900, "height": 500, "rect": [80, 220, 420, 430], "ext": "png"},
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=images,
        out_dir=tmp_path,
        image_bytes_resolver=lambda item: (b"fake-image", item.get("ext", "png")),
    )

    material_dir = tmp_path / "modules" / "1、测试章节"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))

    assert "![测试章节_图1](image_items/测试章节_图1.png)" in material_md
    assert "header" not in material_md
    assert "footer" not in material_md
    assert [item.get("image_id") for item in ordered["items"] if item.get("item_type") == "image"] == ["body-img"]


def test_package_module_artifacts_skips_stream_header_footer_images(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "技术文件 / 1、测试章节",
            1,
            1,
            "1、测试章节",
        )
    ]
    body_image_path = tmp_path / "body.png"
    body_image_path.write_bytes(b"body")
    header_path = tmp_path / "header.png"
    header_path.write_bytes(b"header")
    footer_path = tmp_path / "footer.png"
    footer_path.write_bytes(b"footer")
    blocks = [
        PdfTextBlock(block_id="title", page_no=1, text="1、测试章节", bbox=[20, 100, 220, 120], block_no=1),
    ]
    page_material_items = [
        PageMaterialItem(
            item_id="stream-header",
            item_type="image",
            source_type="pp_structure_image_region",
            page_no=1,
            top_y=18,
            bbox=[24, 18, 154, 58],
            file_path=str(header_path),
            payload={"layout_label": "header_image", "page_width": 595, "page_height": 842},
        ),
        PageMaterialItem(
            item_id="stream-footer",
            item_type="image",
            source_type="pp_structure_image_region",
            page_no=1,
            top_y=790,
            bbox=[420, 790, 560, 822],
            file_path=str(footer_path),
            payload={"layout_label": "footer_image", "page_width": 595, "page_height": 842},
        ),
        PageMaterialItem(
            item_id="stream-body",
            item_type="image",
            source_type="pp_structure_image_region",
            page_no=1,
            top_y=220,
            bbox=[80, 220, 420, 430],
            file_path=str(body_image_path),
            payload={"layout_label": "image", "page_width": 595, "page_height": 842},
        ),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
        page_material_items=page_material_items,
    )

    material_dir = tmp_path / "modules" / "1、测试章节"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))

    assert "body.png" in material_md
    assert "header.png" not in material_md
    assert "footer.png" not in material_md
    assert [item.get("item_id") for item in ordered["items"] if item.get("item_type") == "image"] == ["stream-body"]


def test_fu_lines_do_not_create_submaterials_or_drop_pp_structure_text(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "商务文件 / 法定代表人授权委托书 / 法定代表人授权委托书",
            1,
            1,
            "4、法定代表人授权委托书",
        )
    ]
    blocks = [
        PdfTextBlock(block_id="b1", page_no=1, text="4、法定代表人授权委托书", bbox=[0, 20, 300, 40], block_no=1),
        PdfTextBlock(block_id="b2", page_no=1, text="附：法定代表人（单位负责人）身份证（扫描件）", bbox=[0, 100, 420, 120], block_no=2),
        PdfTextBlock(block_id="b3", page_no=1, text="附：被授权人身份证等有效身份证件（扫描件）", bbox=[0, 260, 420, 280], block_no=3),
    ]
    page_material_items = [
        PageMaterialItem(
            item_id="pp-text-after-fu-1",
            item_type="text",
            source_type="pp_structure_text",
            page_no=1,
            top_y=140,
            bbox=[0, 140, 500, 170],
            text="此处为换行后的附件正文第一行\n此处为换行后的附件正文第二行",
            payload={"layout_label": "text"},
        ),
        PageMaterialItem(
            item_id="pp-text-after-fu-2",
            item_type="text",
            source_type="pp_structure_text",
            page_no=1,
            top_y=300,
            bbox=[0, 300, 500, 330],
            text="第二个附件正文",
            payload={"layout_label": "text"},
        ),
    ]

    package_module_artifacts(
        candidates=candidates,
        blocks=blocks,
        tables=[],
        images=[],
        out_dir=tmp_path,
        page_material_items=page_material_items,
    )

    material_dir = tmp_path / "modules" / "法定代表人授权委托书" / "法定代表人授权委托书"
    material_md = (material_dir / "material.md").read_text(encoding="utf-8")
    ordered = json.loads((material_dir / "ordered_material.json").read_text(encoding="utf-8"))

    assert "附：法定代表人（单位负责人）身份证（扫描件）" in material_md
    assert "此处为换行后的附件正文第一行" in material_md
    assert "此处为换行后的附件正文第二行" in material_md
    assert "附：被授权人身份证等有效身份证件（扫描件）" in material_md
    assert "第二个附件正文" in material_md
    assert not (material_dir / "submaterials").exists()
    assert all(item["item_type"] != "submaterial" for item in ordered["items"])
