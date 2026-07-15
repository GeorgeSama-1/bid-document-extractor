from pathlib import Path


STATIC = Path("bid_knowledge/service/static")


def test_job_ui_has_exact_fields_defaults_and_assets() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in (
        "pdf",
        "gpu_id",
        "path_root",
        "enable_pp_structure",
        "pp_structure_device",
        "pp_structure_use_doc_orientation_classify",
        "pp_structure_use_doc_unwarping",
        "pp_structure_use_textline_orientation",
        "enable_vlm_table",
        "vlm_endpoint",
        "vlm_model",
        "api_key",
        "vlm_timeout",
        "vlm_max_tokens",
        "vlm_workers",
    ):
        assert f'name="{name}"' in html
    assert 'name="api_key" type="password"' in html
    assert 'name="path_root" type="text" value="PDF"' in html
    assert 'name="vlm_timeout" type="number" value="1800"' in html
    assert 'name="vlm_max_tokens" type="number" value="8192"' in html
    assert 'name="vlm_workers" type="number" value="16"' in html
    assert 'href="/jobs.css?v=' in html
    assert 'src="/jobs.js?v=' in html
    assert 'id="jobsClearHistory"' in html
    assert 'id="toastRegion"' in html
    assert "投标文档智能解析服务" in html
    assert "运行与历史" in html
    assert "结果浏览" in html
    assert "expandedPaths" in html
    assert "tree-toggle" in html


def test_job_script_covers_api_actions_and_never_persists_key() -> None:
    script = (STATIC / "jobs.js").read_text(encoding="utf-8")
    for path in (
        "/api/system/gpus",
        "/api/jobs",
        "/cancel",
        "/archive",
    ):
        assert path in script
    assert "FormData" in script
    assert "api_key" in script
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert ".value = \"\"" in script
    assert "/files" not in script
    assert "fileUrl" not in script
    assert "下载完整 ZIP" in script
    assert ".download =" in script
    assert 'method: "DELETE"' in script
    assert "previousTop" in script
    assert "renderedJobId" in script
    assert "showServiceToast" in script
    assert "toast-region" in (STATIC / "jobs.css").read_text(encoding="utf-8")
