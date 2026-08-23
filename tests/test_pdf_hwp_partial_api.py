from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
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
    assert body.error.message == "실패한 2개 문항도 자동 추출 원문으로 HWP에 보존합니다."


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
            "draft_json,error_code,error_message) VALUES(?,2,4,20,'[5,6,7,8]','failed',"
            "'{\"source_text\":\"31\\n3\\n20. 원문\"}',"
            "'draft_extraction_failed','formula extraction failed')", (job_id,),
        ).lastrowid

    captured = {}

    def typeset(request):
        captured["markdown"] = "\n\n".join(unit.palette_markdown for unit in request.units)
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
    failed_block = captured["markdown"].split("\n\n")[-1]
    failed_lines = failed_block.splitlines()
    assert failed_lines[0] == "\\수능AI실제직접형\\"
    assert failed_lines[1] == "20"
    assert failed_lines[2].startswith("{[자동 추출 원문]")
    assert failed_lines[3] == "원문}"
    assert len(failed_lines) == 11  # template marker + 9 slots; passage block uses two lines
    assert failed_lines[-7:] == ["-"] * 7


def test_failed_item_fallback_preserves_the_exact_source_crop(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    crop = tmp_path / "item-7-source.png"
    crop.write_bytes(b"source-crop")
    digest = hashlib.sha256(crop.read_bytes()).hexdigest()
    metadata = {
        "source_pdf": "source.pdf", "page_number": 1, "item_number": 7,
        "bbox": [10.0, 20.0, 410.0, 620.0], "dpi": 300,
        "width_px": 1667, "height_px": 2500,
    }
    with db.transaction() as connection:
        item_id = connection.execute(
            "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json,status,"
            "draft_json,error_code,error_message) VALUES(?,1,1,7,'[10,20,410,620]','failed',"
            "'{\"source_text\":\"7. 원문\"}','draft_extraction_failed','ocr failed')",
            (job_id,),
        ).lastrowid
        connection.execute(
            "INSERT INTO conversion_asset(job_id,item_id,role,file_path,sha256,media_type,metadata_json) "
            "VALUES(?,?,?,?,?,'image/png',?)",
            (job_id, item_id, "source_crop", str(crop), digest,
             json.dumps(metadata, ensure_ascii=False)),
        )

    captured = {}

    def typeset(request):
        captured["unit"] = request.units[0]
        request.output_dir.mkdir(parents=True, exist_ok=True)
        hwp, pdf = request.output_dir / "fallback.hwp", request.output_dir / "fallback.pdf"
        hwp.write_bytes(b"hwp")
        pdf.write_bytes(b"pdf")
        manifest = request.output_dir / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        return ConversionResult(hwp, pdf, (), manifest)

    monkeypatch.setattr(routes_pdf_hwp.pipeline, "typeset_conversion", typeset)
    response = client.post(f"/api/pdf-hwp/jobs/{job_id}/typeset")

    assert response.status_code == 200
    unit = captured["unit"]
    assert unit.palette_markdown == f"\\수능원문1대사진\\\n\\{crop.stem}\\"
    assert len(unit.figure_assets) == 1
    assert unit.figure_assets[0].image_path == crop
    assert unit.figure_assets[0].metadata.manual_review_required is False

    with db.transaction() as connection:
        connection.execute(
            "UPDATE conversion_job SET source_filename='clipboard-image.png' WHERE id=?", (job_id,),
        )
    blocked = client.post(f"/api/pdf-hwp/jobs/{job_id}/typeset")
    assert blocked.status_code == 409
    assert "전체 사진으로 대체하지 않습니다" in blocked.json()["detail"]


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


def test_async_typeset_starts_when_every_item_needs_text_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    with db.transaction() as connection:
        connection.execute(
            "UPDATE conversion_job SET status='failed' WHERE id=?", (job_id,),
        )
        connection.execute(
            "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json,status,"
            "draft_json,error_code,error_message) VALUES(?,1,1,7,'[1,2,3,4]','failed',"
            "'{\"source_text\":\"7. 원문\"}','draft_extraction_failed','ocr failed')", (job_id,),
        )
    started = {}
    monkeypatch.setattr(routes_pdf_hwp, "_run_typeset_operation", lambda operation_id, current_id: started.update(operation_id=operation_id, job_id=current_id))
    response = client.post(f"/api/pdf-hwp/jobs/{job_id}/typeset/start")
    assert response.status_code == 200
    assert response.json()["selection_snapshot"] == [7]
    assert started["job_id"] == job_id


def test_async_typeset_automatically_retries_manual_review_with_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: preflight rejects one structured item before HWP is launched.
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    operation_id = "auto-fallback"
    with db.transaction() as connection:
        connection.execute(
            "INSERT INTO conversion_operation(id,job_id,kind,status,progress,"
            "selection_snapshot_json) VALUES(?,?,?,'queued',0,'[11]')",
            (operation_id, job_id, "typeset"),
        )
    attempts = 0

    def typeset(_job_id: int, _selection: list[int]) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise HTTPException(409, "item 11 requires manual review")

    monkeypatch.setattr(routes_pdf_hwp, "_typeset", typeset)

    # When: the background conversion handles the preflight result.
    routes_pdf_hwp._run_typeset_operation(operation_id, job_id)

    # Then: the failed item is retried through its text fallback without user action.
    operation = routes_pdf_hwp._operation_read(operation_id)
    assert attempts == 2
    assert operation is not None
    assert operation["status"] == "completed"


def test_failed_item_text_edit_becomes_selected_review_candidate(client: TestClient) -> None:
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    with db.transaction() as connection:
        item_id = connection.execute(
            "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json,status,"
            "selected,draft_json,error_code,error_message) VALUES(?,1,1,7,'[1,2,3,4]','failed',"
            "0,?,'draft_extraction_failed','ocr failed')",
            (job_id, '{"source_text":"7. 원문"}'),
        ).lastrowid
        connection.execute(
            "INSERT INTO conversion_output(job_id,kind,status,file_path) VALUES(?,?,'ready','old.hwp')",
            (job_id, "hwp"),
        )

    response = client.patch(
        f"/api/pdf-hwp/jobs/{job_id}/items/{item_id}",
        json={
            "source_text": "7. 수정된 원문",
            "passage": "7. 수정된 원문",
            "prompt": "이에 대한 설명으로 옳은 것은?",
            "manual_blocks": [{"kind": "whole_source", "text": "7. 수정된 원문"}],
            "whole_source_text": True,
            "palette_markdown": "",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "review"
    item = body["items"][0]
    assert item["status"] == "ready"
    assert item["selected"] is True
    assert item["error"] is None
    assert body["outputs"] == []
