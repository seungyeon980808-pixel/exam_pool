from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import db, pdf_hwp_figure_routing as routing, pdf_hwp_pipeline_models as models
from app import routes_pdf_hwp
from tests.pdf_hwp_figure_contract_support import SourceArtifactSpec, source_artifact


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "conversion.db")
    monkeypatch.setattr(db, "_inited", False)
    monkeypatch.setattr(routes_pdf_hwp, "conversion_root", lambda: tmp_path / "pdf_hwp")
    db.init_db()
    app = FastAPI()
    app.include_router(routes_pdf_hwp.router)
    return TestClient(app)


@dataclass(frozen=True, slots=True)
class _DraftResult:
    palette_markdown: str
    source_text: str
    source_image: models.CropArtifact
    figure_asset: models.CropArtifact | None
    warnings: tuple[str, ...]
    figure_assets: tuple[models.CropArtifact, ...]
    graphical_choice_assets: tuple[models.CropArtifact, ...] = ()


@dataclass(frozen=True, slots=True)
class _DraftSetup:
    client: TestClient
    tmp_path: Path
    monkeypatch: pytest.MonkeyPatch
    panel_count: int
    manual_review_required: bool = False


def _prepare_draft(setup: _DraftSetup) -> tuple[int, routing.RoutedFigure]:
    job_id = setup.client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    upload = setup.client.post(
        f"/api/pdf-hwp/jobs/{job_id}/upload",
        files={"file": ("source.pdf", b"%PDF-1.7\nsource", "application/pdf")},
    )
    source_pdf = Path(upload.json()["source_path"])
    detected = models.DetectedItem(2, 7, 0, (0.0, 0.0, 300.0, 150.0), "panels")
    setup.monkeypatch.setattr(
        routes_pdf_hwp.pipeline,
        "detect_items",
        lambda _: models.DetectionResult(source_pdf, "source-hash", 2, (detected,)),
    )
    boxes: tuple[models.BoundingBox, ...] = (
        ((0.0, 0.0, 300.0, 300.0),)
        if setup.panel_count == 1
        else ((0.0, 0.0, 140.0, 150.0), (160.0, 0.0, 300.0, 150.0))
    )
    captions = () if setup.panel_count == 1 else (
        {"text": "(가)", "bbox": (55.0, 130.0, 85.0, 145.0)},
        {"text": "(나)", "bbox": (215.0, 130.0, 245.0, 145.0)},
    )
    source = source_artifact(setup.tmp_path / "assets", SourceArtifactSpec(
        name="figure", panel_bboxes=boxes, captions=captions,
        drawing_count=setup.panel_count, image_count=0,
        manual_review_required=setup.manual_review_required,
    ))
    passage = "한 장면을 관찰한다." if setup.panel_count == 1 else "(가)와 (나)를 비교한다."
    routed = routing.route_figure(passage, source)
    template = "수능정답1소사진5선지" if setup.panel_count == 1 else "수능정답2소사진무캡션5선지"
    setup.monkeypatch.setattr(
        routes_pdf_hwp.pipeline,
        "build_editable_draft",
        lambda *_args, **_kwargs: _DraftResult(
            f"\\{template}\\\n7\nbody", "panels", source, source, (), routed.assets,
        ),
    )
    return job_id, routed


def test_api_persists_panel_contract_and_reconstructs_ordered_typeset_assets(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a detected item with two typed final panels and no caption slots.
    job_id, routed = _prepare_draft(_DraftSetup(client, tmp_path, monkeypatch, panel_count=2))
    persisted = client.post(f"/api/pdf-hwp/jobs/{job_id}/detect")
    captured: list[models.ConversionUnit] = []

    def typeset(request: models.ConversionRequest) -> models.ConversionResult:
        captured.extend(request.units)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        hwp = request.output_dir / "result.hwp"
        pdf = request.output_dir / "result.pdf"
        manifest = request.output_dir / "manifest.json"
        hwp.write_bytes(b"hwp")
        pdf.write_bytes(b"pdf")
        manifest.write_text("{}", encoding="utf-8")
        return models.ConversionResult(hwp, pdf, (), manifest)

    monkeypatch.setattr(routes_pdf_hwp.pipeline, "typeset_conversion", typeset)

    # When: the persisted item is later reconstructed for HWP typesetting.
    converted = client.post(f"/api/pdf-hwp/jobs/{job_id}/typeset")

    # Then: DB/API roles and the typed handoff preserve panel count, hash, mode, and order.
    assert persisted.status_code == 200
    panel_rows = [asset for asset in persisted.json()["assets"] if asset["role"] == "figure_panel"]
    assert [asset["metadata"]["panel_index"] for asset in panel_rows] == [1, 2]
    assert all(asset["metadata"]["panel_mode"] == "separate" for asset in panel_rows)
    assert all(asset["metadata"]["source_kind"] == "vector" for asset in panel_rows)
    assert converted.status_code == 200
    assert len(captured) == 1
    assert [asset.metadata.panel_index for asset in captured[0].figure_assets] == [1, 2]
    assert [asset.image_path.name for asset in captured[0].figure_assets] == [
        routed.assets[0].image_path.name,
        routed.assets[1].image_path.name,
    ]


def test_typeset_manual_review_error_is_persisted_on_the_affected_item(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one ready item whose downstream registered-template preflight is unsafe.
    job_id, _ = _prepare_draft(_DraftSetup(client, tmp_path, monkeypatch, panel_count=1))
    client.post(f"/api/pdf-hwp/jobs/{job_id}/detect")
    monkeypatch.setattr(
        routes_pdf_hwp.pipeline,
        "typeset_conversion",
        lambda _request: (_ for _ in ()).throw(
            models.ManualReviewRequiredError(7, "figure slot count mismatch")
        ),
    )

    # When: HWP preflight rejects that exact unit.
    response = client.post(f"/api/pdf-hwp/jobs/{job_id}/typeset")
    persisted = client.get(f"/api/pdf-hwp/jobs/{job_id}").json()

    # Then: the API reports review-needed and stores it on the affected item.
    assert response.status_code == 409
    assert persisted["items"][0]["status"] == "failed"
    assert persisted["items"][0]["selected"] is False
    assert persisted["items"][0]["error"]["code"] == "manual_review_required"


def test_detection_persists_unsafe_figure_metadata_as_manual_review(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: routing preserved evidence assets but classified their separation as unsafe.
    job_id, _ = _prepare_draft(_DraftSetup(
        client, tmp_path, monkeypatch, panel_count=2, manual_review_required=True,
    ))

    # When: the detection API persists that routed draft.
    response = client.post(f"/api/pdf-hwp/jobs/{job_id}/detect")
    body = response.json()

    # Then: review is required before HWP while both evidence panels remain previewable.
    assert response.status_code == 200
    assert body["items"][0]["status"] == "failed"
    assert body["items"][0]["selected"] is False
    assert body["items"][0]["error"]["code"] == "manual_review_required"
    panels = [asset for asset in body["assets"] if asset["role"] == "figure_panel"]
    assert len(panels) == 2
    assert all(asset["metadata"]["manual_review_required"] is True for asset in panels)
