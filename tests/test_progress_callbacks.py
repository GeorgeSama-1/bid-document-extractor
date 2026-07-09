import sys
import types
from pathlib import Path

from bid_knowledge.parsing.ocr_client import run_ocr
from bid_knowledge.parsing.table_extractor import extract_tables
from bid_knowledge.schemas.models import OCRResult


def test_run_ocr_reports_progress(monkeypatch) -> None:
    events: list[tuple[int, int]] = []

    monkeypatch.setattr(
        "bid_knowledge.parsing.ocr_client.ocr_page_image",
        lambda image_path, page_no, endpoint=None, model=None, api_key=None: OCRResult(page_no=page_no, image_path=str(image_path), blocks=[]),
    )

    run_ocr(
        [{"page_no": 1, "image_path": "page1.png"}, {"page_no": 2, "image_path": "page2.png"}],
        progress_callback=lambda current, total: events.append((current, total)),
    )

    assert events == [(1, 2), (2, 2)]


def test_extract_tables_reports_progress(tmp_path: Path, monkeypatch) -> None:
    events: list[tuple[int, int]] = []
    pdf_path = tmp_path / "demo.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    class FakeFoundTable:
        bbox = (0, 0, 10, 10)

        def extract(self):
            return [["名称", "值"]]

    class FakePage:
        def find_tables(self):
            return [FakeFoundTable()]

    class FakePdf:
        pages = [FakePage(), FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    fake_pdfplumber = types.SimpleNamespace(open=lambda _path: FakePdf())
    monkeypatch.setitem(sys.modules, "pdfplumber", fake_pdfplumber)

    tables = extract_tables(pdf_path, progress_callback=lambda current, total: events.append((current, total)))

    assert len(tables) == 2
    assert tables[0].table_model["row_count"] == 1
    assert tables[0].table_model["col_count"] == 2
    assert tables[0].table_model["cells"][0]["text"] == "名称"
    assert events == [(1, 2), (2, 2)]


def test_extract_tables_uses_pdfplumber_cell_geometry_for_spans(tmp_path: Path, monkeypatch) -> None:
    pdf_path = tmp_path / "demo.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    class FakeRow:
        def __init__(self, cells):
            self.cells = cells

    class FakeFoundTable:
        bbox = (0, 0, 130, 30)
        rows = [
            FakeRow([(0, 0, 60, 10), (60, 0, 130, 10)]),
            FakeRow([(0, 10, 10, 20), (10, 10, 20, 20), (20, 10, 30, 20), (30, 10, 40, 20), (40, 10, 50, 20), (50, 10, 60, 20), (60, 10, 70, 20), (70, 10, 80, 20), (80, 10, 90, 20), (90, 10, 100, 20), (100, 10, 110, 20), (110, 10, 120, 20), (120, 10, 130, 20)]),
            FakeRow([(0, 20, 10, 30), (10, 20, 20, 30), (20, 20, 30, 30), (30, 20, 40, 30), (40, 20, 50, 30), (50, 20, 60, 30), (60, 20, 70, 30), (70, 20, 80, 30), (80, 20, 90, 30), (90, 20, 100, 30), (100, 20, 110, 30), (110, 20, 120, 30), (120, 20, 130, 30)]),
        ]

        def extract(self):
            return [
                ["本企业人员基本信息", "国家电网公司系统人员基本信息"],
                ["人员姓名", "性别", "身份证号", "职务", "任职时间", "与国网公司系统人员关系", "人员姓名", "性别", "身份证号", "（曾）任职单位名称", "职务", "任职状态（在职/非在职）", "离职/退休时间"],
                ["无", "—", "—", "—", "—", "—", "—", "—", "—", "—", "—", "—", "—"],
            ]

    class FakePage:
        def find_tables(self):
            return [FakeFoundTable()]

    class FakePdf:
        pages = [FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    fake_pdfplumber = types.SimpleNamespace(open=lambda _path: FakePdf())
    monkeypatch.setitem(sys.modules, "pdfplumber", fake_pdfplumber)

    tables = extract_tables(pdf_path)

    model = tables[0].table_model
    assert model["source"] == "pdfplumber_geometry"
    assert model["row_count"] == 3
    assert model["col_count"] == 13
    assert model["preserves_spans"] is True
    assert model["cells"][0]["text"] == "本企业人员基本信息"
    assert model["cells"][0]["colspan"] == 6
    assert model["cells"][1]["text"] == "国家电网公司系统人员基本信息"
    assert model["cells"][1]["colspan"] == 7
    assert model["rows"][2][12] == "—"
