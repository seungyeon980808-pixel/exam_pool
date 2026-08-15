from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db
from app import routes_pdf_hwp
from app.pdf_hwp_pipeline import (
    ConversionResult,
    DetectedItem,
    DetectionResult,
    InvalidCropError,
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Given: an isolated real SQLite database and artifact root.
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "conversion.db")
    monkeypatch.setattr(db, "_inited", False)
    monkeypatch.setattr(routes_pdf_hwp, "conversion_root", lambda: tmp_path / "pdf_hwp")
    db.init_db()
    app = FastAPI()
    app.include_router(routes_pdf_hwp.router)
    return TestClient(app)


def test_conversion_schema_is_isolated_from_authoring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a fresh database.
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "schema.db")
    monkeypatch.setattr(db, "_inited", False)

    # When: migrations initialize it.
    db.init_db()

    # Then: all conversion aggregates exist independently of authoring sessions.
    with sqlite3.connect(tmp_path / "schema.db") as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {"conversion_job", "conversion_item", "conversion_asset", "conversion_output"} <= tables


def test_create_upload_and_get_job_when_pdf_is_valid(client: TestClient) -> None:
    # Given: a new conversion job.
    created = client.post("/api/pdf-hwp/jobs", json={"name": "중간고사", "layout_style": "suneung"})
    job_id = created.json()["id"]

    # When: a PDF is uploaded.
    uploaded = client.post(
        f"/api/pdf-hwp/jobs/{job_id}/upload",
        files={"file": ("source.pdf", b"%PDF-1.7\nsource", "application/pdf")},
    )

    # Then: the job owns the stored source and remains isolated from authoring state.
    assert created.status_code == 201
    assert uploaded.status_code == 200
    body = client.get(f"/api/pdf-hwp/jobs/{job_id}").json()
    assert body["status"] == "uploaded"
    assert body["source_filename"] == "source.pdf"
    assert body["source_sha256"]
    assert body["items"] == []
    assert body["assets"] == []
    assert body["outputs"] == []
    assert "authoring_session_id" not in body


def test_upload_rejects_malformed_pdf_without_partial_state(client: TestClient) -> None:
    # Given: an empty conversion job.
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]

    # When: malformed bytes are submitted as a PDF.
    response = client.post(
        f"/api/pdf-hwp/jobs/{job_id}/upload",
        files={"file": ("broken.pdf", b"not-a-pdf", "application/pdf")},
    )

    # Then: validation fails and the durable job is unchanged.
    assert response.status_code == 422
    body = client.get(f"/api/pdf-hwp/jobs/{job_id}").json()
    assert body["status"] == "draft"
    assert body["source_path"] == ""
    assert body["error"] is None


def test_output_download_is_scoped_to_owning_job(client: TestClient, tmp_path: Path) -> None:
    # Given: two jobs and one persisted output owned by the first.
    first = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    second = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    output_path = tmp_path / "result.hwp"
    output_path.write_bytes(b"HWP output")
    with db.transaction() as connection:
        output_id = connection.execute(
            "INSERT INTO conversion_output(job_id,kind,status,file_path,sha256,size_bytes) "
            "VALUES(?, 'hwp', 'ready', ?, 'abc', ?)",
            (first, str(output_path), output_path.stat().st_size),
        ).lastrowid

    # When: each job requests the same output id.
    owned = client.get(f"/api/pdf-hwp/jobs/{first}/outputs/{output_id}")
    foreign = client.get(f"/api/pdf-hwp/jobs/{second}/outputs/{output_id}")

    # Then: only the owning job can retrieve it.
    assert owned.status_code == 200
    assert owned.content == b"HWP output"
    assert foreign.status_code == 404


def test_pdf_hwp_router_does_not_delegate_to_authoring_routes() -> None:
    # Given: the dedicated conversion route module.
    source = Path(routes_pdf_hwp.__file__).read_text(encoding="utf-8")

    # When/Then: its API boundary contains no authoring route dependency.
    assert "/api/authoring" not in source
    assert "routes_authoring" not in source


def test_main_app_wires_only_the_dedicated_pdf_hwp_prefix(client: TestClient) -> None:
    # Given: the production FastAPI application.
    from app.main import app

    # When: the production app is called through its real ASGI router.
    response = TestClient(app).get("/api/pdf-hwp/jobs")

    # Then: conversion is reachable under its isolated prefix.
    assert response.status_code == 200
    assert TestClient(app).get("/api/authoring/pdf-hwp/jobs").status_code == 404


def test_detect_persists_items_assets_and_partial_failure(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an uploaded job whose detector finds two items but one crop fails.
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    client.post(
        f"/api/pdf-hwp/jobs/{job_id}/upload",
        files={"file": ("source.pdf", b"%PDF-1.7\nsource", "application/pdf")},
    )
    source = Path(client.get(f"/api/pdf-hwp/jobs/{job_id}").json()["source_path"])
    first = DetectedItem(1, 1, 0, (1.0, 2.0, 30.0, 40.0), "first")
    second = DetectedItem(1, 2, 0, (31.0, 2.0, 60.0, 40.0), "second")
    monkeypatch.setattr(routes_pdf_hwp.pipeline, "detect_items", lambda _: DetectionResult(
        source_pdf=source, source_hash="hash", page_count=1, items=(first, second),
    ))

    def crop(_source: Path, item: DetectedItem, output_dir: Path, *, dpi: int = 300):
        if item.item_number == 2:
            raise InvalidCropError(item=item)
        image = output_dir / "item-1.png"
        meta = output_dir / "item-1.json"
        output_dir.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"png")
        meta.write_text("{}", encoding="utf-8")
        return routes_pdf_hwp.pipeline.CropArtifact(image, meta, 100, 200)

    monkeypatch.setattr(routes_pdf_hwp.pipeline, "crop_item", crop)

    def draft(source_pdf: Path, item: DetectedItem, output_dir: Path, **_):
        source_image = crop(source_pdf, item, output_dir)
        return routes_pdf_hwp.pipeline.DraftArtifact(
            item.item_number, f"\\direct\\\n{item.item_number}\nauto", item.source_text,
            (), source_image, None, (),
        )

    monkeypatch.setattr(routes_pdf_hwp.pipeline, "build_editable_draft", draft)

    # When: detection is requested.
    response = client.post(f"/api/pdf-hwp/jobs/{job_id}/detect")

    # Then: successful work is durable and the failed item remains retryable.
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial_failure"
    assert [item["status"] for item in body["items"]] == ["ready", "failed"]
    assert len(body["assets"]) == 1
    assert body["error"]["code"] == "crop_partial_failure"


def test_typeset_and_retry_persist_owned_outputs(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a review-ready job with one palette unit and a first typeset failure.
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    with db.transaction() as connection:
        connection.execute(
            "UPDATE conversion_job SET status='review',source_path='source.pdf' WHERE id=?", (job_id,),
        )
        connection.execute(
            "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json,status,draft_json) "
            "VALUES(?,1,1,20,'[1,2,3,4]','ready',?)",
            (job_id, '{"palette_markdown":"\\\\template\\\\n20\\nquestion"}'),
        )
    calls = 0

    def typeset(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise routes_pdf_hwp.pipeline.ConversionTypesetError(detail="HWP unavailable")
        request.output_dir.mkdir(parents=True, exist_ok=True)
        hwp = request.output_dir / "converted.hwp"
        pdf = request.output_dir / "converted.pdf"
        manifest = request.output_dir / "conversion.json"
        hwp.write_bytes(b"hwp")
        pdf.write_bytes(b"pdf")
        manifest.write_text("{}", encoding="utf-8")
        return ConversionResult(hwp, pdf, (), manifest)

    monkeypatch.setattr(routes_pdf_hwp.pipeline, "typeset_conversion", typeset)

    # When: typesetting fails and the same durable job is retried.
    failed = client.post(f"/api/pdf-hwp/jobs/{job_id}/typeset")
    retried = client.post(f"/api/pdf-hwp/jobs/{job_id}/retry")

    # Then: failure is observable, retry completes, and outputs belong to the job.
    assert failed.status_code == 503
    assert retried.status_code == 200
    body = retried.json()
    assert body["status"] == "completed"
    assert {output["kind"] for output in body["outputs"]} == {"hwp", "pdf"}
    assert all(output["status"] == "ready" for output in body["outputs"])


def test_patch_item_persists_reviewed_palette_markdown(client: TestClient) -> None:
    # Given: a detected conversion item.
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    with db.transaction() as connection:
        item_id = connection.execute(
            "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json) "
            "VALUES(?,1,4,20,'[1,2,3,4]')", (job_id,),
        ).lastrowid

    # When: the review UI stores its palette-ready representation.
    response = client.patch(
        f"/api/pdf-hwp/jobs/{job_id}/items/{item_id}",
        json={"palette_markdown": "\\template\\\n20\nquestion"},
    )

    # Then: the item becomes ready without touching another job aggregate.
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["status"] == "ready"
    assert item["draft"]["palette_markdown"] == "\\template\\\n20\nquestion"
