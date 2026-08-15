from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app import db, routes_pdf_hwp
from app.pdf_hwp_pipeline_models import (
    ConversionRequest,
    ConversionResult,
    CropArtifact,
    DetectedItem,
    DetectionResult,
)


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
class _Draft:
    item_number: int
    palette_markdown: str
    source_text: str
    choice_texts: tuple[str, ...]
    source_image: CropArtifact
    figure_asset: CropArtifact | None
    warnings: tuple[str, ...]
    figure_assets: tuple[CropArtifact, ...] = ()
    graphical_choice_assets: tuple[CropArtifact, ...] = ()


def _crop(folder: Path, name: str, metadata: dict[str, str | int | float | bool | list[str]]) -> CropArtifact:
    folder.mkdir(parents=True, exist_ok=True)
    image_path = folder / f"{name}.png"
    Image.new("RGB", (80, 60), "white").save(image_path)
    provenance_path = folder / f"{name}.json"
    provenance_path.write_text(json.dumps(metadata), encoding="utf-8")
    return CropArtifact(image_path, provenance_path, 80, 60)


def _prompt(folder: Path, item_number: int) -> CropArtifact:
    crop = _crop(folder, f"q{item_number}_prompt", {})
    digest = hashlib.sha256(crop.image_path.read_bytes()).hexdigest()
    crop.provenance_path.write_text(json.dumps({
        "source_pdf": str(folder / "source.pdf"), "page_number": 1,
        "item_number": item_number, "image_bbox": [0, 0, 180, 135],
        "caption_text": "", "caption_bbox": None, "asset_count": 1,
        "panel_index": 1, "panel_mode": "single", "arrangement": "horizontal",
        "source_kind": "raster", "display_size": "large", "dpi": 300,
        "width_px": 80, "height_px": 60, "asset_hash": digest,
        "confidence": 1.0, "caption_in_image": False,
    }), encoding="utf-8")
    return crop


def _choices(
    folder: Path,
    item_number: int,
    indices: tuple[int, ...] = (1, 2, 3, 4, 5),
    hash_mismatch: bool = False,
) -> tuple[CropArtifact, ...]:
    choices: list[CropArtifact] = []
    for position, index in enumerate(indices, 1):
        crop = _crop(folder, f"q{item_number}_choice_{position}", {})
        digest = hashlib.sha256(crop.image_path.read_bytes()).hexdigest()
        crop.provenance_path.write_text(json.dumps({
            "source_pdf": str(folder / "source.pdf"), "page_number": 1,
            "item_number": item_number, "choice_index": index, "asset_count": 5,
            "dpi": 300, "width_px": 80, "height_px": 60,
            "asset_hash": "0" * 64 if hash_mismatch and position == 1 else digest,
            "confidence": 1.0,
            "manual_review_required": False, "review_reasons": [],
        }), encoding="utf-8")
        choices.append(crop)
    return tuple(choices)


def _setup_three_items(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    *, choice_indices: tuple[int, ...] = (1, 2, 3, 4, 5),
    hash_mismatch: bool = False, template_label: str = "수능정답1대사진그림5선지",
) -> int:
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    uploaded = client.post(
        f"/api/pdf-hwp/jobs/{job_id}/upload",
        files={"file": ("source.pdf", b"%PDF-1.7\nsource", "application/pdf")},
    ).json()
    source_pdf = Path(uploaded["source_path"])
    items = tuple(
        DetectedItem(1, number, 0, (0.0, 0.0, 100.0, 100.0), f"q{number}")
        for number in (16, 17, 18)
    )
    monkeypatch.setattr(
        routes_pdf_hwp.pipeline, "detect_items",
        lambda _path: DetectionResult(source_pdf, "source-hash", 1, items),
    )
    folder = tmp_path / "assets"
    source_crop = _crop(folder, "source", {"kind": "source"})
    prompt = _prompt(folder, 17)
    choices = _choices(folder, 17, choice_indices, hash_mismatch)
    choice_slots = "\n".join(f"\\{choice.image_path.stem}\\" for choice in choices)
    drafts = {
        16: _Draft(16, "\\direct\\\n16\nstem\nask", "q16", ("1", "2"), source_crop, None, ()),
        17: _Draft(
            17,
            f"\\{template_label}\\\n17\nstem\n\\q17_prompt\\\nask\n{choice_slots}",
            "q17", (), source_crop, prompt, (), (prompt,), choices,
        ),
        18: _Draft(18, "\\direct\\\n18\nstem\nask", "q18", ("1", "2"), source_crop, None, ()),
    }
    monkeypatch.setattr(
        routes_pdf_hwp.pipeline, "build_editable_draft",
        lambda _source, item, _folder, **_kwargs: drafts[item.item_number],
    )
    return job_id


def test_q17_graphical_choices_traverse_detection_and_typeset_in_order(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: q16/q18 text-choice items surround q17 with one prompt and five graphical choices.
    job_id = _setup_three_items(client, tmp_path, monkeypatch)
    detected = client.post(f"/api/pdf-hwp/jobs/{job_id}/detect")
    captured: list[ConversionRequest] = []

    def typeset(request: ConversionRequest) -> ConversionResult:
        captured.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        hwp, pdf, manifest = (
            request.output_dir / "result.hwp",
            request.output_dir / "result.pdf",
            request.output_dir / "manifest.json",
        )
        hwp.write_bytes(b"hwp")
        pdf.write_bytes(b"pdf")
        manifest.write_text("{}", encoding="utf-8")
        return ConversionResult(hwp, pdf, (), manifest)

    monkeypatch.setattr(routes_pdf_hwp.pipeline, "typeset_conversion", typeset)

    # When: the detected batch traverses the persisted selection boundary.
    converted = client.post(f"/api/pdf-hwp/jobs/{job_id}/typeset")

    # Then: q17 is selected safely with five typed ordered choices; neighbours remain unchanged.
    assert detected.status_code == 200
    assert converted.status_code == 200
    assert [unit.item_number for unit in captured[0].units] == [16, 17, 18]
    q16, q17, q18 = captured[0].units
    assert q16.graphical_choice_assets == ()
    assert [asset.metadata.choice_index for asset in q17.graphical_choice_assets] == [1, 2, 3, 4, 5]
    assert [asset.image_path.stem for asset in q17.graphical_choice_assets] == [
        f"q17_choice_{index}" for index in range(1, 6)
    ]
    assert q18.graphical_choice_assets == ()


@pytest.mark.parametrize(
    ("choice_indices", "hash_mismatch", "template_label", "expected"),
    [
        ((1, 2, 3, 4), False, "수능정답1대사진그림5선지", "count"),
        ((1, 3, 2, 4, 5), False, "수능정답1대사진그림5선지", "ordered"),
        ((1, 2, 3, 4, 5), True, "수능정답1대사진그림5선지", "hash"),
        ((1, 2, 3, 4, 5), False, "수능정답1대사진5선지", "template"),
    ],
)
def test_detection_blocks_malformed_graphical_choice_contracts(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    choice_indices: tuple[int, ...],
    hash_mismatch: bool,
    template_label: str,
    expected: str,
) -> None:
    # Given: one malformed dimension of an otherwise q17-like graphical-choice draft.
    job_id = _setup_three_items(
        client, tmp_path, monkeypatch,
        choice_indices=choice_indices,
        hash_mismatch=hash_mismatch,
        template_label=template_label,
    )

    # When: detection parses the draft at the persistence boundary.
    response = client.post(f"/api/pdf-hwp/jobs/{job_id}/detect")
    q17 = next(item for item in response.json()["items"] if item["source_number"] == 17)

    # Then: q17 is retained for review but cannot enter the selected typeset set.
    assert response.status_code == 200
    assert q17["status"] == "failed"
    assert q17["selected"] is False
    assert q17["error"]["code"] == "manual_review_required"
    assert expected in q17["error"]["message"]
    assert response.json()["capabilities"]["typeset_selected"] is True


def test_typeset_blocks_choice_file_changed_after_safe_detection(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: q17 passed detection, then one selected graphical-choice file changed on disk.
    job_id = _setup_three_items(client, tmp_path, monkeypatch)
    detected = client.post(f"/api/pdf-hwp/jobs/{job_id}/detect").json()
    choice = next(asset for asset in detected["assets"] if asset["role"] == "graphical_choice")
    Path(choice["file_path"]).write_bytes(b"tampered")

    # When: typeset reconstructs selected units from persistence.
    response = client.post(f"/api/pdf-hwp/jobs/{job_id}/typeset")
    persisted = client.get(f"/api/pdf-hwp/jobs/{job_id}").json()
    q17 = next(item for item in persisted["items"] if item["source_number"] == 17)

    # Then: the hash mismatch becomes persisted manual review before HWP runs.
    assert response.status_code == 409
    assert q17["status"] == "failed"
    assert q17["selected"] is False
    assert "hash mismatch" in q17["error"]["message"]
