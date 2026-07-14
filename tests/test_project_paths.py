from __future__ import annotations

from pathlib import Path


def test_default_project_root_is_repository_parent(monkeypatch) -> None:
    from bid_knowledge.utils.project_paths import project_root

    monkeypatch.delenv("BID_SOURCE_ROOT", raising=False)
    assert project_root() == Path(__file__).resolve().parents[2]


def test_project_path_helpers_resolve_data_inputs_and_root_outputs(tmp_path: Path, monkeypatch) -> None:
    from bid_knowledge.utils.project_paths import (
        project_root,
        resolve_output_path,
        resolve_raw_input_path,
    )

    monkeypatch.setenv("BID_SOURCE_ROOT", str(tmp_path))
    raw_pdf = tmp_path / "data" / "raw" / "2、商务文件.pdf"
    raw_pdf.parent.mkdir(parents=True)
    raw_pdf.write_bytes(b"%PDF")

    assert project_root() == tmp_path
    assert resolve_raw_input_path("2、商务文件.pdf") == raw_pdf
    assert resolve_output_path("outputs/pdf_toc_run_business_v11") == tmp_path / "outputs" / "pdf_toc_run_business_v11"
    assert resolve_output_path("pdf_toc_run_business_v11") == tmp_path / "outputs" / "pdf_toc_run_business_v11"
