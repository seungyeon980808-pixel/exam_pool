from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app import db, routes_pdf_hwp
from app.pdf_hwp_final_figure_contract import (
    FinalFigureContract,
    FinalFigureReview,
    reconcile_final_figure_contract,
)
from app.pdf_hwp_hwp_preflight import preflight_unit
from app.pdf_hwp_pipeline_models import (
    ConversionUnit,
    FigureAsset,
    FigureAssetMetadata,
    FigureLayout,
    LayoutStyle,
)
from app.pdf_hwp_figure_routing import route_figure
from tests.pdf_hwp_figure_contract_support import SourceArtifactSpec, source_artifact
from app.pdf_hwp_pipeline import DetectionResult, build_editable_draft, detect_items


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "PDF" / "p1_2024_11.pdf"
sys.path.insert(0, str(ROOT / "vendor" / "hwp_typesetter"))
from hwp_palette.hwp import engine_library, hwp_engine  # noqa: E402


def test_detection_reselects_large_hapdap_template_from_real_q2_final_crop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: real q2's expanded mixed-region crop but a stale pre-expansion small label.
    real_detection = detect_items(SOURCE)
    item2 = next(item for item in real_detection.items if item.item_number == 2)
    real_draft = build_editable_draft(SOURCE, item2, tmp_path / "real-q2")
    stale_markdown = real_draft.palette_markdown.replace(
        "\\수능합답1대사진5선지\\", "\\수능합답1소사진5선지\\", 1,
    )
    stale_draft = replace(real_draft, palette_markdown=stale_markdown)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "conversion.db")
    monkeypatch.setattr(db, "_inited", False)
    monkeypatch.setattr(routes_pdf_hwp, "conversion_root", lambda: tmp_path / "pdf_hwp")
    db.init_db()
    app = FastAPI()
    app.include_router(routes_pdf_hwp.router)
    client = TestClient(app)
    monkeypatch.setattr(routes_pdf_hwp.pipeline, "detect_items", lambda source: DetectionResult(
        source, real_detection.source_hash, real_detection.page_count, (item2,),
    ))
    monkeypatch.setattr(
        routes_pdf_hwp.pipeline, "build_editable_draft", lambda *_args, **_kwargs: stale_draft,
    )
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    with SOURCE.open("rb") as source_file:
        client.post(
            f"/api/pdf-hwp/jobs/{job_id}/upload",
            files={"file": (SOURCE.name, source_file, "application/pdf")},
        )

    # When: detection persists the final authoritative figure contract.
    response = client.post(f"/api/pdf-hwp/jobs/{job_id}/detect")
    item = response.json()["items"][0]
    figure = next(asset for asset in response.json()["assets"] if asset["role"] == "figure")

    # Then: the full bbox/hash/dimensions and large template agree before typesetting.
    assert response.status_code == 200
    assert item["draft"]["palette_markdown"].splitlines()[0] == "\\수능합답1대사진5선지\\"
    assert figure["metadata"]["image_bbox"] == pytest.approx(
        [89.9, 561.71240234375, 418.56, 621.3529052734375]
    )
    assert [figure["metadata"]["width_px"], figure["metadata"]["height_px"]] == [1370, 249]
    assert figure["metadata"]["display_size"] == "large"
    assert figure["sha256"] == "5fd82a3e598ec2d26823457e2d2c01567f5fa0626ccce4401d4d19f02e74b30b"


@pytest.mark.parametrize(
    ("item_number", "expected_label", "expected_size"),
    [
        (2, "수능합답1대사진5선지", "large"),
        (11, "수능합답1소사진5선지", "small"),
        (12, "수능합답1소사진5선지", "small"),
        (15, "수능합답1소사진5선지", "small"),
        (16, "수능합답1소사진5선지", "small"),
    ],
)
def test_real_single_hapdap_items_use_final_crop_size_without_item_overrides(
    tmp_path: Path,
    item_number: int,
    expected_label: str,
    expected_size: str,
) -> None:
    # Given: real one-figure 합답 items whose final crop widths differ.
    item = next(value for value in detect_items(SOURCE).items if value.item_number == item_number)

    # When: each item completes extraction and final routing.
    draft = build_editable_draft(SOURCE, item, tmp_path / f"item-{item_number}")
    metadata = json.loads(draft.figure_assets[0].provenance_path.read_text(encoding="utf-8"))

    # Then: the template follows final geometry without an item-number branch.
    assert draft.palette_markdown.splitlines()[0] == f"\\{expected_label}\\"
    assert metadata["display_size"] == expected_size


def test_final_contract_blocks_asset_changed_after_routing(tmp_path: Path) -> None:
    # Given: real q2 metadata followed by an unexpected final PNG replacement.
    item2 = next(item for item in detect_items(SOURCE).items if item.item_number == 2)
    draft = build_editable_draft(SOURCE, item2, tmp_path / "q2-hash")
    path = draft.figure_assets[0].image_path
    with Image.open(path) as opened:
        changed = opened.convert("RGB")
    changed.putpixel((0, 0), (0, 0, 0))
    changed.save(path, dpi=(300, 300))

    # When: persistence reconciles the final artifact.
    result = reconcile_final_figure_contract(
        2, draft.palette_markdown, draft.figure_assets,
    )

    # Then: the ambiguous replacement cannot silently retain a template.
    assert isinstance(result, FinalFigureReview)
    assert result.detail == "final figure asset hash mismatch"


def test_real_q2_insertion_dimensions_preserve_full_crop_aspect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: q2's final 1370x249 full mixed-region PNG and the production figure frame.
    item2 = next(item for item in detect_items(SOURCE).items if item.item_number == 2)
    draft = build_editable_draft(SOURCE, item2, tmp_path / "q2-aspect")
    path = draft.figure_assets[0].image_path
    monkeypatch.setattr(hwp_engine, "S", {
        "layout": {"column_width_mm": 93.99, "figure_frame_width_mm": 114.3,
                   "figure_target_ratio": 0.845},
    })

    # When: the insertion sizing plan fits the final PNG to the print frame.
    native = engine_library._image_size_mm(path)
    assert native is not None
    fitted = engine_library._fit_picture_size_mm(*native)

    # Then: fitting changes scale only, never the authoritative full-crop aspect.
    assert fitted[0] / fitted[1] == pytest.approx(1370 / 249, rel=0.002)


@pytest.mark.parametrize("item_number", [4, 9])
def test_final_contract_promotes_real_assets_when_registered_small_cell_scales_below_half(
    tmp_path: Path,
    item_number: int,
) -> None:
    # Given: a real final asset currently emitted for a registered small-photo template.
    item = next(value for value in detect_items(SOURCE).items if value.item_number == item_number)
    draft = build_editable_draft(SOURCE, item, tmp_path / f"readability-{item_number}")
    original_lines = draft.palette_markdown.splitlines()

    # When: final reconciliation evaluates the actual template cell and contain-fit inset.
    result = reconcile_final_figure_contract(
        item_number, draft.palette_markdown, draft.figure_assets,
    )

    # Then: the final template is promoted without changing its caption/material slot payload.
    assert isinstance(result, FinalFigureContract)
    expected_layout = FigureLayout.TWO_LARGE
    assert result.layout is not None
    assert result.layout.layout is expected_layout
    assert result.layout.minimum_projected_scale >= result.layout.readability_threshold
    assert result.palette_markdown.splitlines()[1:] == original_lines[1:]
    assert "대사진" in result.palette_markdown.splitlines()[0]


@pytest.mark.parametrize("item_number", [11, 12, 15, 16, 18])
def test_final_contract_keeps_real_hapdap_small_assets_above_half_scale(
    tmp_path: Path,
    item_number: int,
) -> None:
    # Given: a true small one-photo hapdap item and its final authoritative crop.
    item = next(value for value in detect_items(SOURCE).items if value.item_number == item_number)
    draft = build_editable_draft(SOURCE, item, tmp_path / f"small-regression-{item_number}")

    # When: final reconciliation evaluates its exact hapdap small-photo cell.
    result = reconcile_final_figure_contract(
        item_number, draft.palette_markdown, draft.figure_assets,
    )

    # Then: the crop remains in the small template and still clears the readability floor.
    assert isinstance(result, FinalFigureContract)
    assert result.layout is not None
    assert result.layout.layout is FigureLayout.ONE_SMALL
    assert result.layout.minimum_projected_scale >= result.layout.readability_threshold
    expected_prefix = "수능정답" if item_number == 18 else "수능합답"
    assert result.palette_markdown.splitlines()[0] == f"\\{expected_prefix}1소사진5선지\\"


def test_final_contract_rebuilds_proven_three_panel_hapdap_slots_from_stale_composite(
    tmp_path: Path,
) -> None:
    # Given: three ordered, non-overlapping panels with separate caption geometry,
    # while persisted markdown still carries the old one-composite template shape.
    boxes = (
        (0.0, 0.0, 90.0, 130.0),
        (100.0, 0.0, 190.0, 130.0),
        (200.0, 0.0, 290.0, 130.0),
    )
    captions = tuple(
        {"text": text, "bbox": (box[0] + 30.0, 110.0, box[0] + 60.0, 126.0)}
        for text, box in zip(("(가)", "(나)", "(다)"), boxes, strict=True)
    )
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name="proven-three", panel_bboxes=boxes, captions=captions,
        drawing_count=0, image_count=1,
    ))
    routed = route_figure("(가), (나), (다)를 비교한다.", source)
    stale = "\n".join((
        "\\수능합답1대사진5선지\\", "7", "문두", "\\old-composite\\", "발문",
        "ㄱ 내용", "ㄴ 내용", "ㄷ 내용", "ㄱ", "ㄴ", "ㄱ,ㄷ", "ㄴ,ㄷ", "ㄱ,ㄴ,ㄷ",
    ))

    # When: authoritative final assets reconcile the stale composite markdown.
    result = reconcile_final_figure_contract(7, stale, routed.assets)

    # Then: all three captionless assets and editable captions occupy the new full slots.
    assert isinstance(result, FinalFigureContract)
    assert result.layout is not None
    assert result.layout.layout is FigureLayout.THREE_SMALL
    lines = result.palette_markdown.splitlines()
    assert lines[0] == "\\수능합답3소사진5선지\\"
    assert lines[3:9] == [
        f"\\{routed.assets[0].image_path.stem}\\", "(가)",
        f"\\{routed.assets[1].image_path.stem}\\", "(나)",
        f"\\{routed.assets[2].image_path.stem}\\", "(다)",
    ]
    assert lines[9:] == [
        "발문", "ㄱ 내용", "ㄴ 내용", "ㄷ 내용", "ㄱ", "ㄴ", "ㄱ,ㄷ", "ㄴ,ㄷ", "ㄱ,ㄴ,ㄷ",
    ]


def test_final_contract_blocks_three_panel_rewrite_when_caption_order_is_incomplete(
    tmp_path: Path,
) -> None:
    # Given: three geometric crops but the third editable caption is missing.
    boxes = (
        (0.0, 0.0, 90.0, 130.0),
        (100.0, 0.0, 190.0, 130.0),
        (200.0, 0.0, 290.0, 130.0),
    )
    captions = tuple(
        {"text": text, "bbox": (box[0] + 30.0, 110.0, box[0] + 60.0, 126.0)}
        for text, box in zip(("(가)", "(나)", "(다)"), boxes, strict=True)
    )
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name="ambiguous-three", panel_bboxes=boxes, captions=captions,
        drawing_count=0, image_count=1,
    ))
    routed = route_figure("(가), (나), (다)를 비교한다.", source)
    third = routed.assets[2]
    payload = json.loads(third.provenance_path.read_text(encoding="utf-8"))
    payload["caption_text"] = ""
    third.provenance_path.write_text(json.dumps(payload), encoding="utf-8")
    stale = "\n".join((
        "\\수능합답1대사진5선지\\", "7", "문두", "\\old-composite\\", "발문",
        "ㄱ 내용", "ㄴ 내용", "ㄷ 내용", "ㄱ", "ㄴ", "ㄱ,ㄷ", "ㄴ,ㄷ", "ㄱ,ㄴ,ㄷ",
    ))

    # When: final reconciliation reaches the cross-count rewrite seam.
    result = reconcile_final_figure_contract(7, stale, routed.assets)

    # Then: it refuses to invent the missing editable label or silently lose meaning.
    assert isinstance(result, FinalFigureReview)
    assert result.detail == "three-panel captions are incomplete or out of order"


def test_real_q3_final_contract_preserves_three_panels_and_editable_caption_slots(
    tmp_path: Path,
) -> None:
    # Given: q3's real white-gutter seams and isolated raster-caption geometry.
    item = next(value for value in detect_items(SOURCE).items if value.item_number == 3)
    draft = build_editable_draft(SOURCE, item, tmp_path / "real-q3-three-panel")

    # When: the authoritative three-panel contract reaches final reconciliation.
    result = reconcile_final_figure_contract(3, draft.palette_markdown, draft.figure_assets)

    # Then: no composite fallback or fabricated split survives into final template slots.
    assert isinstance(result, FinalFigureContract)
    assert result.layout is not None
    assert result.layout.layout is FigureLayout.THREE_SMALL
    assert len(draft.figure_assets) == 3
    metadata = [
        json.loads(asset.provenance_path.read_text(encoding="utf-8"))
        for asset in draft.figure_assets
    ]
    expected_boxes = (
        (153.9, 870.601, 241.5, 905.457),
        (241.5, 870.601, 301.26, 905.457),
        (301.26, 870.601, 350.7, 905.457),
    )
    assert all(
        value["image_bbox"] == pytest.approx(expected)
        for value, expected in zip(metadata, expected_boxes, strict=True)
    )
    assert [value["caption_text"] for value in metadata] == ["(가)", "(나)", "(다)"]
    lines = result.palette_markdown.splitlines()
    assert lines[0] == "\\수능합답3소사진5선지\\"
    assert lines[3:9] == [
        f"\\{draft.figure_assets[0].image_path.stem}\\", "(가)",
        f"\\{draft.figure_assets[1].image_path.stem}\\", "(나)",
        f"\\{draft.figure_assets[2].image_path.stem}\\", "(다)",
    ]
    assets = tuple(
        FigureAsset(
            artifact.image_path,
            FigureAssetMetadata.model_validate_json(
                artifact.provenance_path.read_text(encoding="utf-8"),
            ),
        )
        for artifact in draft.figure_assets
    )
    preflighted = preflight_unit(
        ConversionUnit(3, result.palette_markdown, assets),
        LayoutStyle.SUNEUNG,
    )
    assert preflighted.item_number == 3
    assert len(preflighted.figure_asset_hashes) == 3
