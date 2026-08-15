from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
import pytest

from app import (
    pdf_hwp_figure_routing as routing,
    pdf_hwp_pipeline_models as models,
)
from tests.pdf_hwp_figure_contract_support import SourceArtifactSpec, source_artifact


def _metadata_payload(source_kind: str = "raster") -> dict[str, str | int | float | bool | list[float]]:
    return {
        "source_pdf": "source.pdf",
        "page_number": 2,
        "item_number": 7,
        "image_bbox": [10.0, 20.0, 110.0, 120.0],
        "caption_text": "(가)",
        "caption_bbox": [45.0, 108.0, 65.0, 118.0],
        "asset_count": 2,
        "panel_index": 1,
        "panel_mode": "separate",
        "arrangement": "horizontal",
        "source_kind": source_kind,
        "display_size": "small",
        "dpi": 300,
        "width_px": 500,
        "height_px": 400,
        "asset_hash": "a" * 64,
        "confidence": 0.95,
        "caption_in_image": False,
        "manual_review_required": False,
    }


def test_figure_asset_metadata_parses_raster_and_vector_with_captionless_invariant() -> None:
    # Given: the same complete external metadata in raster and vector modes.
    raster_payload = _metadata_payload("raster")
    vector_payload = _metadata_payload("vector")

    # When: both payloads cross the typed figure-asset boundary.
    raster = models.FigureAssetMetadata.model_validate(raster_payload)
    vector = models.FigureAssetMetadata.model_validate(vector_payload)

    # Then: both modes expose the same complete captionless contract.
    assert raster.panel_mode is models.PanelMode.SEPARATE
    assert vector.panel_mode is models.PanelMode.SEPARATE
    assert raster.source_kind is models.SourceKind.RASTER
    assert vector.source_kind is models.SourceKind.VECTOR
    assert set(raster.model_dump()) >= set(raster_payload)
    with pytest.raises(ValidationError):
        models.FigureAssetMetadata.model_validate({**raster_payload, "caption_in_image": True})


@pytest.mark.parametrize(
    ("boxes", "expected"),
    [
        (((0, 0, 40, 100), (60, 0, 100, 100)), "horizontal"),
        (((0, 0, 100, 40), (0, 60, 100, 100)), "vertical"),
        (((0, 0, 40, 40), (60, 0, 100, 40), (0, 60, 100, 100)), "grid"),
        (((0, 0, 80, 80), (20, 20, 100, 100)), "composite"),
    ],
)
def test_detect_arrangement_classifies_panel_geometry(
    boxes: tuple[tuple[float, float, float, float], ...], expected: str,
) -> None:
    # Given: ordered panel boxes with one of the supported spatial relationships.
    # When: their arrangement is classified without inspecting raster/vector content.
    arrangement = routing.detect_arrangement(boxes)

    # Then: the stable routing value reflects geometry alone.
    assert arrangement.value == expected


def test_route_figure_builds_three_captionless_grid_assets_with_complete_metadata(
    tmp_path: Path,
) -> None:
    # Given: three panel boxes and captions expressed in authoritative PDF coordinates.
    boxes = ((0.0, 0.0, 140.0, 130.0), (160.0, 0.0, 300.0, 130.0), (0.0, 160.0, 300.0, 300.0))
    captions = (
        {"text": "(가)", "bbox": (55.0, 112.0, 85.0, 126.0)},
        {"text": "(나)", "bbox": (215.0, 112.0, 245.0, 126.0)},
        {"text": "(다)", "bbox": (135.0, 282.0, 165.0, 296.0)},
    )
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name="grid", panel_bboxes=boxes, captions=captions,
        drawing_count=0, image_count=1,
    ))

    # When: automatic routing emits the final HWP-compatible assets.
    routed = routing.route_figure("세 장면을 비교한다.", source)

    # Then: every panel excludes its caption and carries the complete shared contract.
    assert routed.layout is routing.FigureLayout.THREE_SMALL
    assert len(routed.assets) == 3
    assert [asset.metadata.panel_index for asset in routed.figure_assets] == [1, 2, 3]
    assert all(asset.metadata.asset_count == 3 for asset in routed.figure_assets)
    assert all(asset.metadata.panel_mode is models.PanelMode.SEPARATE for asset in routed.figure_assets)
    assert all(asset.metadata.arrangement is models.FigureArrangement.GRID for asset in routed.figure_assets)
    assert all(asset.metadata.caption_in_image is False for asset in routed.figure_assets)
    assert [asset.metadata.caption_text for asset in routed.figure_assets] == ["(가)", "(나)", "(다)"]
    assert all(
        asset.metadata.image_bbox[3] <= asset.metadata.caption_bbox[1]
        for asset in routed.figure_assets
        if asset.metadata.caption_bbox is not None
    )


def test_route_figure_requires_review_when_three_labels_have_only_single_panel_evidence(
    tmp_path: Path,
) -> None:
    # Given: passage labels imply three panels but object segmentation found one composite box.
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name="unresolved", panel_bboxes=((0.0, 0.0, 300.0, 300.0),), captions=(),
        drawing_count=0, image_count=1,
    ))

    # When: routing reconciles semantic labels with authoritative object evidence.
    routed = routing.route_figure("(가), (나), (다)를 비교한다.", source)

    # Then: it never invents three crops and requires review of the unresolved composite.
    assert len(routed.assets) == 1
    assert routed.manual_review_required is True
    assert "panel evidence does not match expected count" in routed.figure_assets[0].metadata.review_reasons


@pytest.mark.parametrize(
    ("passage", "boxes"),
    [
        ("(가)와 (나)를 비교한다.", ((0.0, 0.0, 140.0, 300.0), (160.0, 0.0, 300.0, 300.0))),
        (
            "(가), (나), (다)를 비교한다.",
            ((0.0, 0.0, 90.0, 300.0), (105.0, 0.0, 195.0, 300.0), (210.0, 0.0, 300.0, 300.0)),
        ),
    ],
)
def test_route_figure_never_emits_gap_inferred_panels_without_object_evidence(
    tmp_path: Path, passage: str, boxes: tuple[models.BoundingBox, ...],
) -> None:
    # Given: raster ink has obvious gaps but segmentation supplied no authoritative panel boxes.
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name="gap-only", panel_bboxes=boxes, captions=(), drawing_count=0, image_count=1,
        report_panel_bboxes=False,
    ))

    # When: routing encounters semantic labels without object-coordinate evidence.
    routed = routing.route_figure(passage, source)

    # Then: one composite is preserved and unsafe pixel-gap crops never become final assets.
    assert len(routed.assets) == 1
    assert routed.manual_review_required is True
    assert "panel evidence unavailable" in routed.figure_assets[0].metadata.review_reasons


def test_route_figure_requires_caption_evidence_for_explicit_labeled_panels(
    tmp_path: Path,
) -> None:
    # Given: two object-backed panels are explicit, but their referenced captions are absent.
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name="caption-missing",
        panel_bboxes=((0.0, 0.0, 140.0, 300.0), (160.0, 0.0, 300.0, 300.0)),
        captions=(), drawing_count=0, image_count=1,
    ))

    # When: routing sees passage labels that require caption identity.
    routed = routing.route_figure("(가)와 (나)를 비교한다.", source)

    # Then: geometry alone cannot authorize a captionless HWP handoff.
    assert len(routed.assets) == 2
    assert routed.manual_review_required is True
    assert "caption geometry unavailable" in routed.figure_assets[0].metadata.review_reasons


def test_route_figure_keeps_authoritative_panels_when_passage_starts_with_daeumeun(
    tmp_path: Path,
) -> None:
    # Given: a valid KICE frame starts with 다음은 and still names two source panels.
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name="daeumeun",
        panel_bboxes=((0.0, 0.0, 140.0, 300.0), (160.0, 0.0, 300.0, 300.0)),
        captions=(
            {"text": "(가)", "bbox": (55.0, 270.0, 85.0, 290.0)},
            {"text": "(나)", "bbox": (215.0, 270.0, 245.0, 290.0)},
        ),
        drawing_count=0, image_count=1,
    ))

    # When: object-coordinate routing reconciles the source text.
    routed = routing.route_figure("다음은 그림 (가), (나)를 나타낸 것이다.", source)

    # Then: the sentence prefix never collapses authoritative panel evidence.
    assert len(routed.assets) == 2
    assert routed.manual_review_required is False
    assert routed.figure_assets[0].metadata.arrangement is models.FigureArrangement.HORIZONTAL


@pytest.mark.parametrize(
    ("boxes", "arrangement"),
    [
        (
            ((0.0, 0.0, 140.0, 300.0), (160.0, 0.0, 300.0, 300.0)),
            models.FigureArrangement.HORIZONTAL,
        ),
        (
            ((0.0, 0.0, 300.0, 140.0), (0.0, 160.0, 300.0, 300.0)),
            models.FigureArrangement.VERTICAL,
        ),
    ],
)
def test_route_figure_trusts_captionless_explicit_two_panel_object_geometry(
    tmp_path: Path,
    boxes: tuple[models.BoundingBox, ...],
    arrangement: models.FigureArrangement,
) -> None:
    # Given: segmentation proves two panels and the source passage requires no panel captions.
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name="captionless-two", panel_bboxes=boxes, captions=(), drawing_count=2, image_count=0,
    ))

    # When: routing uses the explicit geometry without a source-text split heuristic.
    routed = routing.route_figure("두 대상을 비교한다.", source)

    # Then: both vector panels are safe, separate, and ordered in their measured arrangement.
    assert len(routed.assets) == 2
    assert routed.manual_review_required is False
    assert all(asset.metadata.panel_mode is models.PanelMode.SEPARATE for asset in routed.figure_assets)
    assert all(asset.metadata.arrangement is arrangement for asset in routed.figure_assets)
    assert all(asset.metadata.source_kind is models.SourceKind.VECTOR for asset in routed.figure_assets)


def test_route_figure_orders_staggered_horizontal_objects_left_to_right(tmp_path: Path) -> None:
    # Given: authoritative horizontal objects arrive in reverse PDF object order.
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name="reverse-horizontal",
        panel_bboxes=((170.0, 10.0, 300.0, 150.0), (0.0, 0.0, 150.0, 140.0)),
        captions=(),
        drawing_count=0,
        image_count=2,
    ))

    # When: routing assigns stable panel indexes for preview and HWP consumers.
    routed = routing.route_figure("Compare the two figures.", source)

    # Then: reading order is spatial, not dependent on PDF object enumeration.
    assert [asset.metadata.panel_index for asset in routed.figure_assets] == [1, 2]
    assert [asset.metadata.image_bbox[0] for asset in routed.figure_assets] == [0.0, 170.0]
