from bid_knowledge.parsing.attachment_asset_exporter import (
    anchor_matches_candidate,
    assign_images_to_anchors,
    assign_images_to_candidates,
    candidate_page_numbers,
    group_asset_name,
    sanitize_asset_name,
    select_meaningful_images,
)


def test_select_meaningful_images_filters_small_logo() -> None:
    images = [
        {"xref": 16, "width": 154, "height": 70, "rect": [72.0, 30.4, 122.5, 53.3]},
        {"xref": 1117, "width": 1089, "height": 800, "rect": [118.4, 142.0, 476.9, 405.4]},
        {"xref": 1118, "width": 1263, "height": 893, "rect": [104.6, 459.5, 490.8, 732.5]},
    ]

    selected = select_meaningful_images(images)

    assert [item["xref"] for item in selected] == [1117, 1118]


def test_assign_images_to_candidates_uses_top_to_bottom_order() -> None:
    candidates = [
        {"title": "银行保函", "source_page": 11},
        {"title": "银行基本账户证明扫描件", "source_page": 11},
    ]
    images = [
        {"xref": 1118, "rect": [104.6, 459.5, 490.8, 732.5]},
        {"xref": 1117, "rect": [118.4, 142.0, 476.9, 405.4]},
    ]

    assigned = assign_images_to_candidates(candidates, images)

    assert assigned[0]["xref"] == 1117
    assert assigned[1]["xref"] == 1118


def test_sanitize_asset_name_uses_title_and_removes_path_separators() -> None:
    assert sanitize_asset_name("企业营业执照（扫描件）/副本") == "企业营业执照（扫描件）_副本"


def test_group_asset_name_prefers_anchor_text_without_prefix() -> None:
    candidate = {"title": "法定代表人（单位负责人）身份证（扫描件）"}
    assert group_asset_name(candidate, "附：法定代表人（单位负责人）身份证（正、反面扫描件）") == "法定代表人（单位负责人）身份证（正、反面扫描件）"
    assert group_asset_name(candidate, "附：被授权人身份证（扫描件）") == "被授权人身份证（扫描件）"


def test_candidate_page_numbers_supports_page_ranges() -> None:
    candidate = {"source_page": 834, "source_page_end": 835}
    assert candidate_page_numbers(candidate) == [834, 835]


def test_assign_images_to_anchors_groups_images_by_vertical_regions() -> None:
    anchors = [
        {"page_no": 834, "text": "附：法定代表人（单位负责人）身份证（扫描件）", "y": 434.3},
        {"page_no": 834, "text": "附：被授权人身份证（扫描件）", "y": 609.1},
    ]
    images = [
        {"page_no": 834, "xref": 3011, "rect": [72.0, 476.0, 296.2, 615.5]},
        {"page_no": 834, "xref": 3013, "rect": [297.0, 474.5, 522.0, 614.8]},
        {"page_no": 834, "xref": 3015, "rect": [72.0, 630.6, 291.0, 767.9]},
        {"page_no": 834, "xref": 3017, "rect": [295.5, 629.1, 518.2, 768.6]},
    ]

    groups = assign_images_to_anchors(anchors, images)

    assert len(groups) == 2
    assert [image["xref"] for image in groups[0]["images"]] == [3011, 3013]
    assert [image["xref"] for image in groups[1]["images"]] == [3015, 3017]


def test_assign_images_to_anchors_uses_same_page_boundary_anchors() -> None:
    anchors = [
        {"page_no": 834, "text": "附：法定代表人（单位负责人）身份证（扫描件）", "y": 434.3},
        {"page_no": 835, "text": "附：法定代表人（单位负责人）身份证（正、反面扫描件）", "y": 314.0},
    ]
    boundary_anchors = [
        {"page_no": 834, "text": "附：法定代表人（单位负责人）身份证（扫描件）", "y": 434.3},
        {"page_no": 834, "text": "附：被授权人身份证（扫描件）", "y": 609.1},
        {"page_no": 835, "text": "附：法定代表人（单位负责人）身份证（正、反面扫描件）", "y": 314.0},
    ]
    images = [
        {"page_no": 834, "xref": 3011, "rect": [72.0, 476.0, 296.2, 615.5]},
        {"page_no": 834, "xref": 3013, "rect": [297.0, 474.5, 522.0, 614.8]},
        {"page_no": 834, "xref": 3015, "rect": [72.0, 630.6, 291.0, 767.9]},
        {"page_no": 834, "xref": 3017, "rect": [295.5, 629.1, 518.2, 768.6]},
        {"page_no": 835, "xref": 3023, "rect": [76.7, 340.9, 297.6, 478.4]},
        {"page_no": 835, "xref": 3024, "rect": [297.6, 340.9, 518.6, 478.4]},
    ]

    groups = assign_images_to_anchors(anchors, images, boundary_anchors)

    assert len(groups) == 2
    assert [image["xref"] for image in groups[0]["images"]] == [3011, 3013]
    assert [image["xref"] for image in groups[1]["images"]] == [3023, 3024]


def test_anchor_matches_candidate_uses_identity_role_to_filter_groups() -> None:
    assert anchor_matches_candidate("法定代表人（单位负责人）身份证（扫描件）", "附：法定代表人（单位负责人）身份证（扫描件）") is True
    assert anchor_matches_candidate("法定代表人（单位负责人）身份证（扫描件）", "附：被授权人身份证（扫描件）") is False
    assert anchor_matches_candidate("被授权人身份证等有效身份证件（扫描件）", "附：被授权人身份证（扫描件）") is True
    assert anchor_matches_candidate("被授权人身份证等有效身份证件（扫描件）", "附：法定代表人（单位负责人）身份证（扫描件）") is False


def test_anchor_matches_candidate_supports_variant_instance_titles() -> None:
    assert anchor_matches_candidate("企业名称变更", "3.9.2、2007年企业名称变更证明材料") is True
    assert anchor_matches_candidate("企业名称变更", "3.9.3、2015年企业名称变更证明材料") is True
    assert anchor_matches_candidate("企业名称变更", "银行保函") is False
