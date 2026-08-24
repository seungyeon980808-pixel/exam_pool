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


def test_detect_auto_draft_and_selection_drive_typeset_without_manual_edit(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two detected items whose pipeline can build editable drafts automatically.
    job_id = _uploaded_job(client)
    source = Path(client.get(f"/api/pdf-hwp/jobs/{job_id}").json()["source_path"])
    items = (
        DetectedItem(1, 19, 0, (1.0, 2.0, 30.0, 40.0), "item 19"),
        DetectedItem(1, 20, 0, (31.0, 2.0, 60.0, 40.0), "item 20"),
    )
    monkeypatch.setattr(routes_pdf_hwp.pipeline, "detect_items", lambda _: DetectionResult(
        source_pdf=source, source_hash="hash", page_count=1, items=items,
    ))
    monkeypatch.setattr(
        routes_pdf_hwp.pipeline, "build_editable_draft",
        lambda _source, item, output_dir, **_: DraftResult(
            f"\\direct\\\n{item.item_number}\nauto", item.source_text,
            _crop(output_dir, item.item_number),
        ), raising=False,
    )
    captured_numbers: list[int] = []

    def typeset(request):
        captured_numbers.extend(unit.item_number for unit in request.units)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        hwp, pdf = request.output_dir / "result.hwp", request.output_dir / "result.pdf"
        hwp.write_bytes(b"hwp")
        pdf.write_bytes(b"pdf")
        manifest = request.output_dir / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        return ConversionResult(hwp, pdf, (), manifest)

    monkeypatch.setattr(routes_pdf_hwp.pipeline, "typeset_conversion", typeset)

    # When: detection runs, item 19 is deselected, and typesetting starts without a draft PATCH.
    detected = client.post(f"/api/pdf-hwp/jobs/{job_id}/detect")
    item19 = detected.json()["items"][0]
    selected = client.patch(
        f"/api/pdf-hwp/jobs/{job_id}/items/{item19['id']}", json={"selected": False},
    )
    converted = client.post(f"/api/pdf-hwp/jobs/{job_id}/typeset")

    # Then: selection persists and only selected ready item 20 enters the pipeline.
    assert detected.status_code == 200
    assert selected.json()["items"][0]["selected"] is False
    assert converted.status_code == 200
    assert captured_numbers == [20]


def test_source_and_asset_previews_are_scoped_to_owning_job(
    client: TestClient, tmp_path: Path,
) -> None:
    # Given: two jobs and a crop asset owned by the first.
    first = _uploaded_job(client)
    second = _uploaded_job(client)
    crop = tmp_path / "crop.png"
    crop.write_bytes(b"crop")
    with db.transaction() as connection:
        item_id = connection.execute(
            "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json) "
            "VALUES(?,1,1,20,'[1,2,3,4]')", (first,),
        ).lastrowid
        asset_id = connection.execute(
            "INSERT INTO conversion_asset(job_id,item_id,role,file_path,sha256,media_type) "
            "VALUES(?,?, 'source_crop', ?, 'hash', 'image/png')",
            (first, item_id, str(crop)),
        ).lastrowid

    # When: source and asset previews are requested through production routes.
    source = client.get(f"/api/pdf-hwp/jobs/{first}/source")
    asset = client.get(f"/api/pdf-hwp/jobs/{first}/assets/{asset_id}")
    foreign = client.get(f"/api/pdf-hwp/jobs/{second}/assets/{asset_id}")

    # Then: owned previews stream and cross-job access is hidden.
    assert source.status_code == 200
    assert source.headers["content-type"].startswith("application/pdf")
    assert asset.content == b"crop"
    assert foreign.status_code == 404


def test_zero_detection_is_failed_and_retryable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an uploaded PDF for which detection finds no question regions.
    job_id = _uploaded_job(client)
    source = Path(client.get(f"/api/pdf-hwp/jobs/{job_id}").json()["source_path"])
    calls = 0

    def detect(_source: Path) -> DetectionResult:
        nonlocal calls
        calls += 1
        return DetectionResult(source, "hash", 1, ())

    monkeypatch.setattr(routes_pdf_hwp.pipeline, "detect_items", detect)

    # When: detection and retry are requested.
    detected = client.post(f"/api/pdf-hwp/jobs/{job_id}/detect")
    retried = client.post(f"/api/pdf-hwp/jobs/{job_id}/retry")

    # Then: both attempts remain explicit failed states and retry invokes detection again.
    assert detected.json()["status"] == "failed"
    assert detected.json()["error"]["code"] == "no_items_detected"
    assert retried.json()["status"] == "failed"
    assert calls == 2


def test_sequential_item_mutations_increment_parent_revision_monotonically(
    client: TestClient,
) -> None:
    # Given: one review item under a job whose aggregate revision is known.
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    with db.transaction() as connection:
        item_id = connection.execute(
            "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json,status) "
            "VALUES(?,1,1,20,'[1,2,3,4]','ready')", (job_id,),
        ).lastrowid
    initial = client.get(f"/api/pdf-hwp/jobs/{job_id}").json()

    # When: independent selection and palette changes arrive sequentially.
    selection = client.patch(
        f"/api/pdf-hwp/jobs/{job_id}/items/{item_id}", json={"selected": False},
    ).json()
    palette = client.patch(
        f"/api/pdf-hwp/jobs/{job_id}/items/{item_id}",
        json={"palette_markdown": "\\direct\\\n20\nnew"},
    ).json()
    persisted = client.get(f"/api/pdf-hwp/jobs/{job_id}").json()

    # Then: each returned whole aggregate has a unique, monotonic parent revision.
    assert selection["revision"] == initial["revision"] + 1
    assert palette["revision"] == selection["revision"] + 1
    assert persisted["revision"] == palette["revision"]
    assert persisted["items"][0]["revision"] == 2
