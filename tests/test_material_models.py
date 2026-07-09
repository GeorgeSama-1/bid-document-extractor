from bid_knowledge.schemas.models import (
    CompoundInstanceMeta,
    MaterialItemRef,
    MaterialMeta,
    OrderedMaterialPackage,
    TitleMapping,
)


def test_material_models_capture_raw_and_archive_layers() -> None:
    mapping = TitleMapping(
        raw_context_title="3.8.13.2、供应链保障措施",
        normalized_context_title="供应链保障措施",
        material_title="3.8.13.2、供应链保障措施",
        rule_section_path="商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 创新激励机制、供应链保障措施",
    )

    item = MaterialItemRef(
        type="text",
        item_type="text",
        item_id="text-1",
        page_no=3,
        top_y=100.0,
        payload_ref="text_items/供应链保障措施.json",
        nearest_heading="3.8.13.2、供应链保障措施",
        rule_section_path="商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 创新激励机制、供应链保障措施",
        material_path="商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 创新激励机制、供应链保障措施 / 3.8.13.2、供应链保障措施",
        order=1,
    )

    ordered = OrderedMaterialPackage(
        material_title="3.8.13.2、供应链保障措施",
        section_path="商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 创新激励机制、供应链保障措施",
        material_path=item.material_path,
        rule_section_path=item.rule_section_path,
        material_types=["text", "table", "image"],
        dominant_material_type="mixed",
        items=[item],
    )

    meta = MaterialMeta(
        material_title="3.8.13.2、供应链保障措施",
        section_path="商务文件 / 补充文件 / “商务评分标准”涉及的支撑材料 / 二、高质量发展评价 / 创新激励机制、供应链保障措施",
        material_path=item.material_path,
        rule_section_path=item.rule_section_path,
        rule_module_name="补充文件",
        folder_parts=["补充文件", "“商务评分标准”涉及的支撑材料", "二、高质量发展评价", "创新激励机制、供应链保障措施", "3.8.13.2、供应链保障措施"],
        source_file="demo.pdf",
        source_page_start=3,
        source_page_end=5,
        original_capture={"available": False},
        material_types=["text", "table", "image"],
        dominant_material_type="mixed",
        raw_context_title="3.8.13.2、供应链保障措施",
        title_mapping=mapping,
        text_item_count=1,
        table_item_count=1,
        image_item_count=1,
        ordered_item_count=4,
    )

    compound = CompoundInstanceMeta(
        material_type="compound_instance",
        excel_anchor_path="商务文件 / 补充文件 / 财务状况 / 经会计师事务所或审计机构审计的财务会计报表",
        rule_anchor_path="商务文件 / 补充文件 / 财务状况 / 经会计师事务所或审计机构审计的财务会计报表",
        instance_title="2022年度财务报表",
        instance_path="商务文件 / 补充文件 / 财务状况 / 经会计师事务所或审计机构审计的财务会计报表 / 2022年度财务报表",
        source_page_start=10,
        source_page_end=25,
        child_count=1,
        children=[meta],
    )

    assert ordered.items[0].payload_ref == "text_items/供应链保障措施.json"
    assert meta.title_mapping.raw_context_title == "3.8.13.2、供应链保障措施"
    assert compound.children[0].material_types == ["text", "table", "image"]


def test_material_item_ref_supports_submaterial_type() -> None:
    item = MaterialItemRef(
        type="submaterial",
        item_type="submaterial",
        item_id="submaterial-1",
        page_no=3,
        top_y=120.0,
        payload_ref="submaterials/履约证明材料/ordered_material.json",
        nearest_heading="附：履约证明材料",
        rule_section_path="商务文件 / 补充文件 / 履约能力证明",
        material_path="商务文件 / 补充文件 / 履约能力证明 / 履约证明材料",
        order=3,
    )

    assert item.type == "submaterial"
    assert item.item_type == "submaterial"
    assert item.payload_ref == "submaterials/履约证明材料/ordered_material.json"
