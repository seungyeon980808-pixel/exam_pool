from __future__ import annotations

from pathlib import Path

import pytest

from app.pdf_hwp_figure_routing import FigureLayout, route_figure
from app.pdf_hwp_pipeline import build_editable_draft, detect_items
from app.pdf_hwp_pipeline_models import (
    FigureArrangement,
    FigureAssetMetadata,
    PanelMode,
    SourceKind,
)


SOURCE = Path(__file__).resolve().parents[1] / "PDF" / "p1_2024_11.pdf"


def test_real_key_items_route_by_size_arrangement_and_readability(tmp_path: Path) -> None:
    # Given: the eight source items identified by the source-layout inventory.
    items = {item.item_number: item for item in detect_items(SOURCE).items}
    expected = {
        4: FigureLayout.TWO_LARGE,
        7: FigureLayout.TWO_LARGE,
        8: FigureLayout.TWO_LARGE,
        9: FigureLayout.TWO_LARGE,
        10: FigureLayout.TWO_LARGE,
        13: FigureLayout.TWO_LARGE,
        14: FigureLayout.ONE_LARGE,
        20: FigureLayout.TWO_LARGE,
    }

    # When: each real crop is passed through the final routing seam.
    routed = {
        item_number: route_figure(
            draft.source_text.split("\n\n", 1)[0],
            draft.figure_asset,
        )
        for item_number in expected
        for draft in (
            build_editable_draft(SOURCE, items[item_number], tmp_path / f"item-{item_number}"),
        )
        if draft.figure_asset is not None
    }

    # Then: the final14 cell floor promotes q4/q9, while q14 remains one composite.
    assert {item: result.layout for item, result in routed.items()} == expected
    assert routed[4].layout_metadata.candidate_minimum_projected_scale == pytest.approx(
        0.2512, rel=0.001,
    )
    assert routed[9].layout_metadata.candidate_minimum_projected_scale == pytest.approx(
        0.3131, rel=0.001,
    )
    assert routed[4].layout_metadata.minimum_projected_scale >= 0.5
    assert routed[9].layout_metadata.minimum_projected_scale >= 0.5
    assert routed[13].layout_metadata.panel_width_points == pytest.approx((187.755, 111.045))
    assert routed[14].figure_assets[0].metadata.panel_mode is PanelMode.SINGLE
    assert routed[14].manual_review_required is False
    assert all(
        asset.metadata.caption_in_image is False
        for result in routed.values()
        for asset in result.figure_assets
    )


@pytest.mark.parametrize("item_number", [7, 8, 9])
def test_real_raster_panels_end_before_separate_caption_geometry(
    tmp_path: Path, item_number: int,
) -> None:
    # Given: real source figures with raster-geometry (가)/(나) caption evidence.
    item = next(value for value in detect_items(SOURCE).items if value.item_number == item_number)

    # When: object-backed routing builds captionless final panel assets.
    draft = build_editable_draft(SOURCE, item, tmp_path / f"item-{item_number}")
    metadata = tuple(
        FigureAssetMetadata.model_validate_json(asset.provenance_path.read_text(encoding="utf-8"))
        for asset in draft.figure_assets
    )

    # Then: captions remain typed and every final raster ends at the caption top.
    assert len(metadata) == 2
    assert tuple(asset.caption_text for asset in metadata) == ("(가)", "(나)")
    assert all(asset.caption_bbox is not None for asset in metadata)
    assert all(
        asset.image_bbox[3] <= asset.caption_bbox[1]
        for asset in metadata
        if asset.caption_bbox is not None
    )
    assert all(asset.height_px < draft.figure_asset.height_px for asset in metadata)
    assert all(asset.panel_mode is PanelMode.SEPARATE for asset in metadata)
    assert all(asset.arrangement is FigureArrangement.HORIZONTAL for asset in metadata)
    assert all(asset.source_kind is SourceKind.RASTER for asset in metadata)
    assert all(asset.caption_in_image is False for asset in metadata)
    assert all(asset.manual_review_required is False for asset in metadata)
