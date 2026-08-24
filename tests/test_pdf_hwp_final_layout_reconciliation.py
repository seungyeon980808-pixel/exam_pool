from __future__ import annotations

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
P2_2026_SOURCE = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일" / "p2_2026_11.pdf"
sys.path.insert(0, str(ROOT / "vendor" / "hwp_typesetter"))
from hwp_palette.hwp import engine_library, hwp_engine  # noqa: E402


def test_detection_persists_real_q2_as_editable_table_without_raster_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: real q2, whose source material is now recovered as editable text/table/formulas.
    real_detection = detect_items(SOURCE)
    item2 = next(item for item in real_detection.items if item.item_number == 2)
    real_draft = build_editable_draft(SOURCE, item2, tmp_path / "real-q2")
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
        routes_pdf_hwp.pipeline, "build_editable_draft", lambda *_args, **_kwargs: real_draft,
    )
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    with SOURCE.open("rb") as source_file:
        client.post(
            f"/api/pdf-hwp/jobs/{job_id}/upload",
            files={"file": (SOURCE.name, source_file, "application/pdf")},
        )

    # When: detection persists the structured draft.
    response = client.post(f"/api/pdf-hwp/jobs/{job_id}/detect")
    payload = response.json()
    assert response.status_code == 200, payload
    item = payload["items"][0]
    # Then: the table and formulas stay editable instead of being flattened to a figure.
    markdown = item["draft"]["palette_markdown"]
    assert item["status"] == "ready"
    assert markdown.splitlines()[0] == "\\수능AI실제합답형\\"
    assert "\\표4*2\\" in markdown
    assert "\\수식{" in markdown
    assert not [asset for asset in payload["assets"] if asset["role"] in {"figure", "figure_panel"}]


@pytest.mark.parametrize(
    ("item_number", "expected_label", "expected_size"),
    [
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
    # Given: real q11 metadata followed by an unexpected final PNG replacement.
    item11 = next(item for item in detect_items(SOURCE).items if item.item_number == 11)
    draft = build_editable_draft(SOURCE, item11, tmp_path / "q11-hash")
    path = draft.figure_assets[0].image_path
    with Image.open(path) as opened:
        changed = opened.convert("RGB")
    changed.putpixel((0, 0), (0, 0, 0))
    changed.save(path, dpi=(300, 300))

    # When: persistence reconciles the final artifact.
    result = reconcile_final_figure_contract(
        11, draft.palette_markdown, draft.figure_assets,
    )

    # Then: the ambiguous replacement cannot silently retain a template.
    assert isinstance(result, FinalFigureReview)
    assert result.detail == "final figure asset hash mismatch"


def test_final_contract_keeps_one_composite_when_panel_split_is_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one readable full figure whose (가)/(나) panel split is not authoritative.
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name="uncertain-composite",
        panel_bboxes=((0.0, 0.0, 300.0, 100.0),),
        captions=(),
        drawing_count=0,
        image_count=1,
    ))
    routed = route_figure("그림 (가), (나)를 비교한다.", source)
    asset = routed.assets[0]
    markdown = (
        "\\수능정답1대사진5선지\\\n7\n본문\n"
        f"\\{asset.image_path.stem}\\\n발문\n1\n2\n3\n4\n5"
    )

    # When: the final handoff validates the readable composite PNG.
    result = reconcile_final_figure_contract(7, markdown, (asset,))

    # Then: uncertain segmentation is a warning, not a lost structured question.
    assert isinstance(result, FinalFigureContract)
    assert routes_pdf_hwp._figure_review_error((asset,)) is None
    monkeypatch.setattr(
        routes_pdf_hwp.palette_registry,
        "active_template",
        lambda _style, _label: {
            "label": "수능정답1대사진5선지",
            "slot_count": 9,
            "slot_names": ["문항번호", "문두", "사진1", "발문", "1", "2", "3", "4", "5"],
        },
    )
    preflighted = preflight_unit(
        ConversionUnit(
            item_number=7,
            palette_markdown=result.palette_markdown,
            figure_assets=(FigureAsset(
                asset.image_path,
                FigureAssetMetadata.model_validate_json(
                    asset.provenance_path.read_text(encoding="utf-8"),
                ),
            ),),
        ),
        LayoutStyle.SUNEUNG,
    )
    assert preflighted.item_number == 7


def test_real_q15_insertion_dimensions_preserve_full_crop_aspect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: q15's final 1277x254 wide figure and the production figure frame.
    item15 = next(item for item in detect_items(SOURCE).items if item.item_number == 15)
    draft = build_editable_draft(SOURCE, item15, tmp_path / "q15-aspect")
    path = draft.figure_assets[0].image_path
    monkeypatch.setattr(hwp_engine, "S", {
        "layout": {"column_width_mm": 93.99, "figure_frame_width_mm": 114.3,
                   "figure_target_ratio": 0.845},
    })

    # When: the insertion sizing plan fits the final PNG to the print frame.
    native = engine_library._image_size_mm(path)
    assert native is not None
    fitted = engine_library._fit_picture_size_mm(*native)

    # Then: fitting changes scale only, never the image's authoritative physical aspect.
    assert fitted[0] / fitted[1] == pytest.approx(native[0] / native[1], rel=0.002)


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


@pytest.mark.parametrize("item_number", [11, 12, 15, 16])
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
    assert result.palette_markdown.splitlines()[0] == "\\수능합답1소사진5선지\\"


def test_final_contract_promotes_medium_direct_figure_to_large_slot(tmp_path: Path) -> None:
    item = next(value for value in detect_items(SOURCE).items if value.item_number == 18)
    draft = build_editable_draft(SOURCE, item, tmp_path / "medium-direct-18")

    result = reconcile_final_figure_contract(18, draft.palette_markdown, draft.figure_assets)

    assert isinstance(result, FinalFigureContract)
    assert result.layout is not None
    assert result.layout.layout is FigureLayout.ONE_LARGE
    assert result.layout.minimum_projected_scale >= result.layout.readability_threshold
    assert result.palette_markdown.splitlines()[0] == "\\수능정답1대사진5선지\\"


@pytest.mark.parametrize(("item_number", "expected_label"), (
    (8, "\\수능합답1소사진5선지\\"),
    (10, "\\수능합답1대사진5선지\\"),
    (14, "\\수능정답1소사진5선지\\"),
    (18, "\\수능정답1소사진5선지\\"),
))
def test_real_p2_single_figure_template_preserves_source_placement(
    tmp_path: Path,
    item_number: int,
    expected_label: str,
) -> None:
    if not P2_2026_SOURCE.is_file():
        pytest.skip("p2_2026_11 source PDF is unavailable")
    item = next(
        value for value in detect_items(P2_2026_SOURCE).items
        if value.item_number == item_number
    )
    draft = build_editable_draft(
        P2_2026_SOURCE, item, tmp_path / f"p2-placement-{item_number}",
    )

    result = reconcile_final_figure_contract(
        item_number, draft.palette_markdown, draft.figure_assets,
        source_item=item,
    )

    assert isinstance(result, FinalFigureContract)
    assert result.palette_markdown.splitlines()[0] == expected_label


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


def test_final_contract_rebuilds_text_only_direct_into_registered_hapdap_three_small(
    tmp_path: Path,
) -> None:
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
        name="text-only-three", panel_bboxes=boxes, captions=captions,
        drawing_count=0, image_count=1,
    ))
    routed = route_figure("(가), (나), (다)를 비교한다.", source)
    stale = "\n".join((
        "\\수능AI실제직접형\\", "18", "문두", "발문", "3",
        "①", "②", "③", "④", "⑤",
    ))

    result = reconcile_final_figure_contract(7, stale, routed.assets)

    assert isinstance(result, FinalFigureContract)
    lines = result.palette_markdown.splitlines()
    assert lines[0] == "\\수능합답3소사진5선지\\"
    assert lines[3:9] == [
        f"\\{routed.assets[0].image_path.stem}\\", "(가)",
        f"\\{routed.assets[1].image_path.stem}\\", "(나)",
        f"\\{routed.assets[2].image_path.stem}\\", "(다)",
    ]
    assert lines[9] == "발문 [3점]"
    assert lines[10:13] == ["-", "-", "-"]
    assert lines[13:] == ["①", "②", "③", "④", "⑤"]


def test_final_contract_rebuilds_direct_one_large_into_registered_hapdap_three_small(
    tmp_path: Path,
) -> None:
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
        name="direct-three", panel_bboxes=boxes, captions=captions,
        drawing_count=0, image_count=1,
    ))
    routed = route_figure("(가), (나), (다)를 비교한다.", source)
    stale = "\n".join((
        "\\수능정답1대사진5선지\\", "8", "문두", "\\old-composite\\", "발문",
        "①", "②", "③", "④", "⑤",
    ))

    result = reconcile_final_figure_contract(7, stale, routed.assets)

    assert isinstance(result, FinalFigureContract)
    lines = result.palette_markdown.splitlines()
    assert lines[0] == "\\수능합답3소사진5선지\\"
    assert lines[3:9] == [
        f"\\{routed.assets[0].image_path.stem}\\", "(가)",
        f"\\{routed.assets[1].image_path.stem}\\", "(나)",
        f"\\{routed.assets[2].image_path.stem}\\", "(다)",
    ]
    assert lines[9:] == ["발문", "-", "-", "-", "①", "②", "③", "④", "⑤"]


def test_final_contract_rebuilds_abc_three_panel_hapdap_slots(tmp_path: Path) -> None:
    boxes = (
        (0.0, 0.0, 90.0, 130.0),
        (100.0, 0.0, 190.0, 130.0),
        (200.0, 0.0, 290.0, 130.0),
    )
    captions = tuple(
        {"text": text, "bbox": (box[0] + 30.0, 110.0, box[0] + 60.0, 126.0)}
        for text, box in zip(("A", "B", "C"), boxes, strict=True)
    )
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name="abc-three", panel_bboxes=boxes, captions=captions,
        drawing_count=0, image_count=1,
    ))
    routed = route_figure("그림 A, B, C는 예를 나타낸 것이다.", source)
    stale = "\n".join((
        "\\수능합답1대사진5선지\\", "5", "문두", "\\old-composite\\", "발문",
        "ㄱ 내용", "ㄴ 내용", "ㄷ 내용", "ㄱ", "ㄴ", "ㄱ,ㄷ", "ㄴ,ㄷ", "ㄱ,ㄴ,ㄷ",
    ))

    result = reconcile_final_figure_contract(7, stale, routed.assets)

    assert isinstance(result, FinalFigureContract)
    lines = result.palette_markdown.splitlines()
    assert lines[0] == "\\수능합답3소사진5선지\\"
    assert lines[3:9] == [
        f"\\{routed.assets[0].image_path.stem}\\", "A",
        f"\\{routed.assets[1].image_path.stem}\\", "B",
        f"\\{routed.assets[2].image_path.stem}\\", "C",
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
