from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, routes_pdf_hwp
from app.pdf_hwp_pipeline import CropArtifact, DetectedItem, DetectionResult


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "conversion.db")
    monkeypatch.setattr(db, "_inited", False)
    monkeypatch.setattr(routes_pdf_hwp, "conversion_root", lambda: tmp_path / "pdf_hwp")
    db.init_db()
    app = FastAPI()
    app.include_router(routes_pdf_hwp.router)
    return TestClient(app)


def test_unsupported_draft_keeps_source_preview_and_requires_manual_review(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    uploaded = client.post(
        f"/api/pdf-hwp/jobs/{job_id}/upload",
        files={"file": ("source.pdf", b"%PDF-1.7\nsource", "application/pdf")},
    )
    source = Path(uploaded.json()["source_path"])
    item = DetectedItem(4, 17, 0, (1.0, 2.0, 30.0, 40.0), "graphical choices")
    monkeypatch.setattr(routes_pdf_hwp.pipeline, "detect_items", lambda _: DetectionResult(
        source_pdf=source, source_hash="hash", page_count=4, items=(item,),
    ))

    def unsupported(_source: Path, detected: DetectedItem, output_dir: Path, **_):
        output_dir.mkdir(parents=True, exist_ok=True)
        image = output_dir / "item-17.png"
        provenance = output_dir / "item-17.json"
        image.write_bytes(b"png-17")
        provenance.write_text("{}", encoding="utf-8")
        crop = CropArtifact(image, provenance, 100, 200)
        raise routes_pdf_hwp.pipeline.UnsupportedDraftLayoutError(
            detected.page_number, detected.item_number,
            "graphical answer choices require manual review", crop,
        )

    monkeypatch.setattr(routes_pdf_hwp.pipeline, "build_editable_draft", unsupported)
    response = client.post(f"/api/pdf-hwp/jobs/{job_id}/detect")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["capabilities"] == {
        "review_items": False, "typeset_selected": False, "retry_failed": True,
    }
    persisted = body["items"][0]
    assert persisted["source_number"] == 17
    assert persisted["status"] == "failed"
    assert persisted["selected"] is False
    assert persisted["error"]["code"] == "manual_review_required"
    assert "graphical answer choices require manual review" in persisted["error"]["message"]
    source_asset = body["assets"][0]
    assert source_asset["item_id"] == persisted["id"]
    assert source_asset["role"] == "source_crop"
    preview = client.get(f"/api/pdf-hwp/jobs/{job_id}/assets/{source_asset['id']}")
    assert preview.status_code == 200
    assert preview.content == b"png-17"
