from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, routes_pdf_hwp
from app.pdf_hwp_pipeline import ConversionRequest, ConversionResult, DetectionResult, detect_items


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "PDF" / "p1_2024_11.pdf"


def test_real_item20_upload_detect_select_and_typeset_uses_auto_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the real 2024 Physics I PDF and isolated production API persistence.
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "conversion.db")
    monkeypatch.setattr(db, "_inited", False)
    monkeypatch.setattr(routes_pdf_hwp, "conversion_root", lambda: tmp_path / "pdf_hwp")
    db.init_db()
    app = FastAPI()
    app.include_router(routes_pdf_hwp.router)
    client = TestClient(app)
    real_detection = detect_items(SOURCE)
    item20 = next(item for item in real_detection.items if item.item_number == 20)
    monkeypatch.setattr(routes_pdf_hwp.pipeline, "detect_items", lambda source: DetectionResult(
        source, real_detection.source_hash, real_detection.page_count, (item20,),
    ))
    captured_markdown: list[str] = []

    def typeset(request):
        captured_markdown.extend(unit.palette_markdown for unit in request.units)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        hwp, pdf = request.output_dir / "item20.hwp", request.output_dir / "item20.pdf"
        hwp.write_bytes(b"hwp")
        pdf.write_bytes(b"pdf")
        manifest = request.output_dir / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        return ConversionResult(hwp, pdf, (), manifest)

    monkeypatch.setattr(routes_pdf_hwp.pipeline, "typeset_conversion", typeset)

    # When: item 20 runs through upload, detection, selection, and typesetting routes.
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    with SOURCE.open("rb") as source_file:
        uploaded = client.post(
            f"/api/pdf-hwp/jobs/{job_id}/upload",
            files={"file": (SOURCE.name, source_file, "application/pdf")},
        )
    detected = client.post(f"/api/pdf-hwp/jobs/{job_id}/detect")
    item = detected.json()["items"][0]
    client.patch(f"/api/pdf-hwp/jobs/{job_id}/items/{item['id']}", json={"selected": False})
    selected = client.patch(
        f"/api/pdf-hwp/jobs/{job_id}/items/{item['id']}", json={"selected": True},
    )
    converted = client.post(f"/api/pdf-hwp/jobs/{job_id}/typeset")

    # Then: no manual markdown edit is needed and the selected real item is converted.
    assert uploaded.status_code == 200
    assert item["source_number"] == 20
    assert item["status"] == "ready"
    assert item["draft"]["palette_markdown"].strip()
    assert selected.json()["items"][0]["selected"] is True
    assert converted.json()["status"] == "completed"
    assert captured_markdown == [item["draft"]["palette_markdown"]]


def test_real_q16_q17_q18_preserve_graphical_choices_through_typeset_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the real neighbouring source items around graphical-choice q17.
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "conversion.db")
    monkeypatch.setattr(db, "_inited", False)
    monkeypatch.setattr(routes_pdf_hwp, "conversion_root", lambda: tmp_path / "pdf_hwp")
    db.init_db()
    app = FastAPI()
    app.include_router(routes_pdf_hwp.router)
    client = TestClient(app)
    real_detection = detect_items(SOURCE)
    neighbours = tuple(
        item for item in real_detection.items if item.item_number in {16, 17, 18}
    )
    monkeypatch.setattr(routes_pdf_hwp.pipeline, "detect_items", lambda source: DetectionResult(
        source, real_detection.source_hash, real_detection.page_count, neighbours,
    ))
    captured: list[ConversionRequest] = []

    def typeset(request: ConversionRequest) -> ConversionResult:
        captured.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        hwp, pdf = request.output_dir / "items16-18.hwp", request.output_dir / "items16-18.pdf"
        hwp.write_bytes(b"hwp")
        pdf.write_bytes(b"pdf")
        manifest = request.output_dir / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        return ConversionResult(hwp, pdf, (), manifest)

    monkeypatch.setattr(routes_pdf_hwp.pipeline, "typeset_conversion", typeset)
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    with SOURCE.open("rb") as source_file:
        client.post(
            f"/api/pdf-hwp/jobs/{job_id}/upload",
            files={"file": (SOURCE.name, source_file, "application/pdf")},
        )

    # When: real crops cross detection, DB reload, selection, and the typeset seam.
    detected = client.post(f"/api/pdf-hwp/jobs/{job_id}/detect")
    converted = client.post(f"/api/pdf-hwp/jobs/{job_id}/typeset")

    # Then: only q17 carries five ordered graphical choices and all three remain safe.
    assert detected.status_code == 200
    assert [
        (item["source_number"], item["status"], item["error"])
        for item in detected.json()["items"]
    ] == [(16, "ready", None), (17, "ready", None), (18, "ready", None)]
    assert converted.status_code == 200
    assert [unit.item_number for unit in captured[0].units] == [16, 17, 18]
    by_number = {unit.item_number: unit for unit in captured[0].units}
    assert by_number[16].graphical_choice_assets == ()
    assert [
        asset.metadata.choice_index for asset in by_number[17].graphical_choice_assets
    ] == [1, 2, 3, 4, 5]
    assert len(by_number[17].figure_assets) == 1
    assert by_number[18].graphical_choice_assets == ()
