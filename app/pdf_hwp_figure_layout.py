"""Derive final layout from authoritative assets and registered template cells."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final, assert_never

from .pdf_hwp_pipeline_models import (
    DisplaySize,
    FigureArrangement,
    FigureAssetMetadata,
    FigureLayout,
    FigureLayoutMetadata,
)


SMALL_PANEL_MAX_WIDTH_POINTS: Final = 168.0
TWO_SMALL_MAX_COMBINED_WIDTH_POINTS: Final = 92.53
POINTS_TO_MM: Final = 25.4 / 72.0
MINIMUM_READABLE_SCALE: Final = 0.70


@dataclass(frozen=True, slots=True)
class TemplateUsableSize:
    width_mm: float
    height_mm: float | None


# These are final14's registered cell interiors after padding and the generic
# 3.5 mm multi-cell safety inset on each horizontal edge.
TWO_SMALL_USABLE_SIZE: Final = TemplateUsableSize(11.45, 18.03)
TWO_LARGE_USABLE_SIZE: Final = TemplateUsableSize(42.90, 28.01)
ONE_SMALL_DIRECT_USABLE_SIZE: Final = TemplateUsableSize(39.42, 28.01)
ONE_SMALL_HAPDAP_USABLE_SIZE: Final = TemplateUsableSize(39.42, 28.01)
ONE_LARGE_DIRECT_USABLE_SIZE: Final = TemplateUsableSize(107.0, None)
ONE_LARGE_HAPDAP_USABLE_SIZE: Final = TemplateUsableSize(103.4, 27.0)

_DIRECT_LABEL_PREFIX: Final = "수능정답"
_HAPDAP_LABEL_PREFIX: Final = "수능합답"


def display_size_for_width(width_points: float) -> DisplaySize:
    return DisplaySize.SMALL if width_points <= SMALL_PANEL_MAX_WIDTH_POINTS else DisplaySize.LARGE


def classify_final_layout(
    assets: tuple[FigureAssetMetadata, ...],
    *,
    template_label: str | None = None,
    single_layout: FigureLayout | None = None,
) -> FigureLayoutMetadata:
    """Classify the final persisted geometry, never an earlier crop candidate."""
    panel_widths = tuple(asset.image_bbox[2] - asset.image_bbox[0] for asset in assets)
    panel_heights = tuple(asset.image_bbox[3] - asset.image_bbox[1] for asset in assets)
    combined_width = sum(panel_widths)
    arrangement = assets[0].arrangement
    small_pair_scales = _projected_scales(
        panel_widths, panel_heights, TWO_SMALL_USABLE_SIZE,
    ) if len(assets) == 2 else ()
    small_pair_readable = (
        len(assets) == 2
        and arrangement is FigureArrangement.HORIZONTAL
        and all(width <= SMALL_PANEL_MAX_WIDTH_POINTS for width in panel_widths)
        and combined_width <= TWO_SMALL_MAX_COMBINED_WIDTH_POINTS
        and min(small_pair_scales) >= MINIMUM_READABLE_SCALE
    )
    if len(assets) == 1:
        candidate_layout = (
            FigureLayout.ONE_SMALL
            if assets[0].display_size is DisplaySize.SMALL
            else FigureLayout.ONE_LARGE
        )
        candidate_size = _template_size(candidate_layout, template_label)
        candidate_scales = (
            _projected_scales(panel_widths, panel_heights, candidate_size)
            if candidate_size is not None else ()
        )
        if single_layout is not None:
            if single_layout not in {FigureLayout.ONE_SMALL, FigureLayout.ONE_LARGE}:
                msg = "single_layout must use a one-picture layout"
                raise ValueError(msg)
            layout = single_layout
        else:
            layout = (
                FigureLayout.ONE_LARGE
                if candidate_layout is FigureLayout.ONE_SMALL
                and candidate_scales
                and min(candidate_scales) < MINIMUM_READABLE_SCALE
                else candidate_layout
            )
    elif len(assets) == 2:
        match arrangement:
            case FigureArrangement.VERTICAL:
                candidate_layout = FigureLayout.TWO_VERTICAL
            case FigureArrangement.HORIZONTAL:
                candidate_layout = FigureLayout.TWO_SMALL
            case FigureArrangement.GRID | FigureArrangement.COMPOSITE:
                candidate_layout = FigureLayout.TWO_LARGE
            case unreachable:
                assert_never(unreachable)
        layout = (
            FigureLayout.TWO_LARGE
            if candidate_layout is FigureLayout.TWO_SMALL and not small_pair_readable
            else candidate_layout
        )
        candidate_size = _template_size(candidate_layout, template_label)
        candidate_scales = (
            _projected_scales(panel_widths, panel_heights, candidate_size)
            if candidate_size is not None else ()
        )
    else:
        candidate_layout = FigureLayout.THREE_SMALL
        layout = FigureLayout.THREE_SMALL
        candidate_size = None
        candidate_scales = ()
    final_size = _template_size(layout, template_label)
    final_scales = (
        _projected_scales(panel_widths, panel_heights, final_size)
        if final_size is not None else ()
    )
    return FigureLayoutMetadata(
        layout=layout,
        candidate_layout=candidate_layout,
        asset_count=len(assets),
        panel_width_points=panel_widths,
        combined_width_points=combined_width,
        arrangement=arrangement,
        small_pair_readable=small_pair_readable,
        template_usable_size_mm=(
            (final_size.width_mm, final_size.height_mm) if final_size is not None else None
        ),
        projected_scale_factors=final_scales,
        minimum_projected_scale=min(final_scales) if final_scales else None,
        candidate_minimum_projected_scale=(
            min(candidate_scales) if candidate_scales else None
        ),
        readability_threshold=MINIMUM_READABLE_SCALE,
    )


def _template_size(
    layout: FigureLayout,
    template_label: str | None,
) -> TemplateUsableSize | None:
    match layout:
        case FigureLayout.TWO_SMALL:
            return TWO_SMALL_USABLE_SIZE
        case FigureLayout.TWO_LARGE:
            return TWO_LARGE_USABLE_SIZE
        case FigureLayout.ONE_SMALL:
            if template_label is None or template_label.startswith(_DIRECT_LABEL_PREFIX):
                return ONE_SMALL_DIRECT_USABLE_SIZE
            if template_label.startswith(_HAPDAP_LABEL_PREFIX):
                return ONE_SMALL_HAPDAP_USABLE_SIZE
            return ONE_SMALL_DIRECT_USABLE_SIZE
        case FigureLayout.ONE_LARGE:
            if template_label is None or template_label.startswith(_DIRECT_LABEL_PREFIX):
                return ONE_LARGE_DIRECT_USABLE_SIZE
            if template_label.startswith(_HAPDAP_LABEL_PREFIX):
                return ONE_LARGE_HAPDAP_USABLE_SIZE
            return None
        case FigureLayout.TWO_VERTICAL | FigureLayout.THREE_SMALL:
            return None
        case unreachable:
            assert_never(unreachable)


def _projected_scales(
    panel_widths_points: tuple[float, ...],
    panel_heights_points: tuple[float, ...],
    usable_size: TemplateUsableSize,
) -> tuple[float, ...]:
    return tuple(
        min(
            usable_size.width_mm / (width * POINTS_TO_MM),
            (
                usable_size.height_mm / (height * POINTS_TO_MM)
                if usable_size.height_mm is not None else float("inf")
            ),
        )
        for width, height in zip(panel_widths_points, panel_heights_points, strict=True)
    )
