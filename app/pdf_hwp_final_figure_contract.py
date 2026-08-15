"""Reconcile persisted draft labels with final authoritative figure crops."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

from PIL import Image
from pydantic import ValidationError

from .pdf_hwp_figure_layout import classify_final_layout, display_size_for_width
from .pdf_hwp_pipeline_models import (
    CropArtifact,
    FigureAssetMetadata,
    FigureLayout,
    FigureLayoutMetadata,
    PanelMode,
)
from .pdf_hwp_question_structure import reconcile_final_template


@dataclass(frozen=True, slots=True)
class FinalFigureContract:
    palette_markdown: str
    layout: FigureLayoutMetadata | None


@dataclass(frozen=True, slots=True)
class FinalFigureReview:
    detail: str


def reconcile_final_figure_contract(
    item_number: int,
    palette_markdown: str,
    artifacts: tuple[CropArtifact, ...],
) -> FinalFigureContract | FinalFigureReview:
    """Parse final sidecars and make their geometry authoritative before persistence."""
    if not artifacts:
        return FinalFigureContract(palette_markdown, None)
    try:
        metadata = tuple(
            FigureAssetMetadata.model_validate_json(
                artifact.provenance_path.read_text(encoding="utf-8")
            )
            for artifact in artifacts
        )
    except (OSError, ValidationError):
        return FinalFigureReview("invalid final figure asset metadata")
    detail = _metadata_review_detail(item_number, artifacts, metadata)
    if detail is not None:
        return FinalFigureReview(detail)
    layout = classify_final_layout(
        metadata,
        template_label=_palette_template_label(palette_markdown),
    )
    if layout.layout is FigureLayout.THREE_SMALL:
        three_panel_detail = _proven_three_panel_review_detail(metadata)
        if three_panel_detail is not None:
            return FinalFigureReview(three_panel_detail)
    reconciled = reconcile_final_template(
        palette_markdown,
        layout.layout,
        figure_tokens=tuple(f"\\{artifact.image_path.stem}\\" for artifact in artifacts),
        captions=tuple(value.caption_text.strip() for value in metadata),
    )
    if reconciled is None:
        return FinalFigureReview("final figure layout is incompatible with the emitted template")
    return FinalFigureContract(reconciled, layout)


def _palette_template_label(markdown: str) -> str | None:
    token = next((line.strip() for line in markdown.splitlines() if line.strip()), "")
    if not token.startswith("\\") or not token.endswith("\\"):
        return None
    return token[1:-1]


def _proven_three_panel_review_detail(
    metadata: tuple[FigureAssetMetadata, ...],
) -> str | None:
    if tuple(value.caption_text.strip() for value in metadata) != ("(가)", "(나)", "(다)"):
        return "three-panel captions are incomplete or out of order"
    if any(value.caption_bbox is None for value in metadata):
        return "three-panel caption geometry is unavailable"
    if any(value.panel_mode is not PanelMode.SEPARATE for value in metadata):
        return "three-panel assets are not separate evidence-backed crops"
    if len({(value.source_pdf, value.page_number, value.item_number) for value in metadata}) != 1:
        return "three-panel source identity is inconsistent"
    if any(
        value.caption_bbox is not None and value.image_bbox[3] > value.caption_bbox[1]
        for value in metadata
    ):
        return "three-panel caption pixels were not excluded"
    for index, left in enumerate(metadata):
        for right in metadata[index + 1:]:
            horizontal_overlap = min(left.image_bbox[2], right.image_bbox[2]) - max(
                left.image_bbox[0], right.image_bbox[0]
            )
            vertical_overlap = min(left.image_bbox[3], right.image_bbox[3]) - max(
                left.image_bbox[1], right.image_bbox[1]
            )
            if horizontal_overlap > 0 and vertical_overlap > 0:
                return "three-panel crop geometry overlaps"
    return None


def _metadata_review_detail(
    item_number: int,
    artifacts: tuple[CropArtifact, ...],
    metadata: tuple[FigureAssetMetadata, ...],
) -> str | None:
    expected_indices = tuple(range(1, len(metadata) + 1))
    if tuple(value.panel_index for value in metadata) != expected_indices:
        return "final figure panel order is ambiguous"
    if any(value.asset_count != len(metadata) for value in metadata):
        return "final figure asset count is inconsistent"
    if any(value.item_number != item_number for value in metadata):
        return "final figure item identity mismatch"
    if len({value.arrangement for value in metadata}) != 1:
        return "final figure arrangement is inconsistent"
    if any(value.manual_review_required for value in metadata):
        return "final figure metadata requires manual review"
    for artifact, value in zip(artifacts, metadata, strict=True):
        width_points = value.image_bbox[2] - value.image_bbox[0]
        height_points = value.image_bbox[3] - value.image_bbox[1]
        if width_points <= 0 or height_points <= 0:
            return "final figure source bbox is invalid"
        if value.display_size is not display_size_for_width(width_points):
            return "final figure display size is stale"
        if value.width_px != artifact.width_px or value.height_px != artifact.height_px:
            return "final figure pixel dimensions are stale"
        try:
            actual_hash = hashlib.sha256(artifact.image_path.read_bytes()).hexdigest()
            with Image.open(artifact.image_path) as image:
                actual_size = image.size
        except OSError:
            return "final figure asset is unreadable"
        if actual_hash != value.asset_hash:
            return "final figure asset hash mismatch"
        if actual_size != (value.width_px, value.height_px):
            return "final figure image dimensions do not match metadata"
        pixel_aspect = value.width_px / value.height_px
        source_aspect = width_points / height_points
        if abs(pixel_aspect / source_aspect - 1.0) > 0.05:
            return "final figure source and pixel aspect ratios disagree"
    return None
