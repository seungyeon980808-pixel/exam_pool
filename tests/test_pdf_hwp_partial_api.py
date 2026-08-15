from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, routes_pdf_hwp
from app.pdf_hwp_pipeline import ConversionResult, CropArtifact, DetectedItem, DetectionResult


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "conversion.db")
    monkeypatch.setattr(db, "_inited", False)
    monkeypatch.setattr(routes_pdf_hwp, "conversion_root", lambda: tmp_path / "pdf_hwp")
    db.init_db()
    app = FastAPI()
    app.include_router(routes_pdf_hwp.router)
    return TestClient(app)


def _uploaded_job(client: TestClient) -> int:
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    response = client.post(
        f"/api/pdf-hwp/jobs/{job_id}/upload",
        files={"file": ("source.pdf", b"%PDF-1.7\nsource", "application/pdf")},
    )
    assert response.status_code == 200
    return job_id


def _crop(output_dir: Path, number: int) -> CropArtifact:
    output_dir.mkdir(parents=True, exist_ok=True)
    image = output_dir / f"item-{number}.png"
    provenance = output_dir / f"item-{number}.json"
    image.write_bytes(f"png-{number}".encode())
    provenance.write_text("{}", encoding="utf-8")
    return CropArtifact(image, provenance, 100, 200)


@dataclass(frozen=True, slots=True)
class DraftResult:
    palette_markdown: str
    source_text: str
    source_image: CropArtifact
    figure_asset: CropArtifact | None = None
    warnings: tuple[str, ...] = ()
    figure_assets: tuple[CropArtifact, ...] = ()
    graphical_choice_assets: tuple[CropArtifact, ...] = ()


def test_partial_retry_preserves_success_and_retries_only_failed_item(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = _uploaded_job(client)
    source = Path(client.get(f"/api/pdf-hwp/jobs/{job_id}").json()["source_path"])
    items = (
        DetectedItem(1, 19, 0, (1.0, 2.0, 30.0, 40.0), "item 19"),
        DetectedItem(1, 20, 0, (31.0, 2.0, 60.0, 40.0), "item 20"),
    )
    detection_calls = 0

    def detect(_source: Path) -> DetectionResult:
        nonlocal detection_calls
        detection_calls += 1
        return DetectionResult(source, "hash", 1, items)

    monkeypatch.setattr(routes_pdf_hwp.pipeline, "detect_items", detect)
    attempts = {19: 0, 20: 0}

    def draft(_source: Path, item: DetectedItem, output_dir: Path, **_) -> DraftResult:
        attempts[item.item_number] += 1
        if item.item_number == 20 and attempts[20] == 1:
            raise routes_pdf_hwp.pipeline.InvalidCropError(item=item)
        return DraftResult(
            f"\\direct\\\n{item.item_number}\nauto", item.source_text,
            _crop(output_dir, item.item_number),
        )

    monkeypatch.setattr(routes_pdf_hwp.pipeline, "build_editable_draft", draft)
    partial = client.post(f"/api/pdf-hwp/jobs/{job_id}/detect").json()
    ready_id = partial["items"][0]["id"]
    ready_asset_id = partial["assets"][0]["id"]
    reviewed = client.patch(
        f"/api/pdf-hwp/jobs/{job_id}/items/{ready_id}",
        json={"palette_markdown": "\\direct\\\n19\nreviewed", "selected": False},
    ).json()
    retried = client.post(f"/api/pdf-hwp/jobs/{job_id}/retry")

    assert retried.status_code == 200
    body = retried.json()
    assert body["status"] == "review"
    assert body["items"][0]["id"] == ready_id
    assert body["items"][0]["revision"] == reviewed["items"][0]["revision"]
    assert body["items"][0]["selected"] is False
    assert body["items"][0]["draft"]["palette_markdown"] == "\\direct\\\n19\nreviewed"
    assert body["assets"][0]["id"] == ready_asset_id
    assert detection_calls == 1
    assert attempts == {19: 1, 20: 2}


def test_partial_failure_exposes_review_typeset_and_retry_capabilities(
    client: TestClient,
) -> None:
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    with db.transaction() as connection:
        connection.execute(
            "UPDATE conversion_job SET status='partial_failure',error_code='crop_partial_failure',"
            "error_message='1 item failed' WHERE id=?", (job_id,),
        )
        connection.execute(
            "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json,status,"
            "draft_json) VALUES(?,1,4,19,'[1,2,3,4]','ready',?)",
            (job_id, '{"palette_markdown":"\\\\direct\\\\n19\\nready"}'),
        )
        connection.execute(
            "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json,status,"
            "draft_json,error_code,error_message) VALUES(?,2,4,20,'[5,6,7,8]','failed','{}',"
            "'draft_extraction_failed','formula extraction failed')", (job_id,),
        )
    body = client.get(f"/api/pdf-hwp/jobs/{job_id}").json()
    assert body["status"] == "partial_failure"
    assert body["capabilities"] == {
        "review_items": True, "typeset_selected": True, "retry_failed": True,
    }
    assert body["items"][1]["error"]["code"] == "draft_extraction_failed"


def test_detection_with_no_ready_items_is_reported_as_total_failure(
    client: TestClient,
) -> None:
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    with db.transaction() as connection:
        for ordinal in (1, 2):
            connection.execute(
                "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json,"
                "status,selected,draft_json,error_code,error_message) VALUES(?,?,?,?,?,'failed',"
                "0,'{}','manual_review_required','graphical answer choices require manual review')",
                (job_id, ordinal, 1, ordinal, "[1,2,3,4]"),
            )

    body = routes_pdf_hwp.pdf_hwp_store.finish_detection(job_id)

    assert body is not None
    assert body.status == "failed"
    assert body.error is not None
    assert body.error.code == "crop_all_failed"
    assert body.error.message == "변환 가능한 문항이 없습니다. 실패한 2개 문항의 상세 원인을 확인하세요."


def test_partial_typeset_keeps_failed_items_retryable_and_attaches_outputs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    with db.transaction() as connection:
        connection.execute(
            "UPDATE conversion_job SET status='partial_failure',source_path='source.pdf',"
            "error_code='crop_partial_failure',error_message='1 item failed' WHERE id=?", (job_id,),
        )
        ready_id = connection.execute(
            "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json,status,"
            "draft_json) VALUES(?,1,4,19,'[1,2,3,4]','ready',?)",
            (job_id, '{"palette_markdown":"\\\\direct\\\\n19\\nready"}'),
        ).lastrowid
        failed_id = connection.execute(
            "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json,status,"
            "draft_json,error_code,error_message) VALUES(?,2,4,20,'[5,6,7,8]','failed','{}',"
            "'draft_extraction_failed','formula extraction failed')", (job_id,),
        ).lastrowid

    def typeset(request):
        request.output_dir.mkdir(parents=True, exist_ok=True)
        hwp, pdf = request.output_dir / "partial.hwp", request.output_dir / "partial.pdf"
        hwp.write_bytes(b"hwp")
        pdf.write_bytes(b"pdf")
        manifest = request.output_dir / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        return ConversionResult(hwp, pdf, (), manifest)

    monkeypatch.setattr(routes_pdf_hwp.pipeline, "typeset_conversion", typeset)
    response = client.post(f"/api/pdf-hwp/jobs/{job_id}/typeset")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial_failure"
    assert {item["id"] for item in body["items"]} == {ready_id, failed_id}
    failed = next(item for item in body["items"] if item["id"] == failed_id)
    assert failed["error"]["code"] == "draft_extraction_failed"
    assert {output["kind"] for output in body["outputs"]} == {"hwp", "pdf"}
    assert body["capabilities"]["retry_failed"] is True


def test_selection_patch_does_not_erase_failed_item_error(client: TestClient) -> None:
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    with db.transaction() as connection:
        item_id = connection.execute(
            "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json,status,"
            "draft_json,error_code,error_message) VALUES(?,1,4,20,'[1,2,3,4]','failed','{}',"
            "'draft_extraction_failed','formula extraction failed')", (job_id,),
        ).lastrowid
    response = client.patch(
        f"/api/pdf-hwp/jobs/{job_id}/items/{item_id}", json={"selected": False},
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["selected"] is False
    assert item["status"] == "failed"
    assert item["error"]["code"] == "draft_extraction_failed"
