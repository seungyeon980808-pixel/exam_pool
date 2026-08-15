"""Classify captionless source-PDF figure assets for preview and HWP."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from .pdf_hwp_figure_geometry import detect_arrangement, expected_panel_count, order_panel_bboxes
from .pdf_hwp_figure_layout import (
    SMALL_PANEL_MAX_WIDTH_POINTS,
    classify_final_layout,
    display_size_for_width,
)
from .pdf_hwp_pipeline_models import (
    BoundingBox,
    CropArtifact,
    FigureArrangement,
    FigureAsset,
    FigureAssetMetadata,
    FigureLayout,
    PanelMode,
    SourceKind,
)


class _CaptionCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    text: str | None = None
    bbox: BoundingBox
    excluded: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class _SourceMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    source_pdf: Path = Path()
    page_number: int = Field(default=1, ge=1)
    item_number: int = Field(default=1, ge=1)
    bbox: BoundingBox | None = None
    dpi: int = Field(default=300, gt=0)
    drawing_count: int = Field(default=0, ge=0)
    image_count: int = Field(default=0, ge=0)
    component_count: int = Field(default=0, ge=0)
    panel_bboxes: tuple[BoundingBox, ...] = ()
    caption_candidates: tuple[_CaptionCandidate, ...] = ()
    manual_review_required: bool = False
    review_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RoutedFigure:
    layout: FigureLayout
    assets: tuple[CropArtifact, ...]
    figure_assets: tuple[FigureAsset, ...]
    layout_metadata: FigureLayoutMetadata

    @property
    def manual_review_required(self) -> bool:
        return any(asset.metadata.manual_review_required for asset in self.figure_assets)


@dataclass(frozen=True, slots=True)
class _PanelBuild:
    source: CropArtifact
    source_metadata: _SourceMetadata
    source_bbox: BoundingBox
    source_image: Image.Image
    panel_bbox: BoundingBox
    caption: _CaptionCandidate | None
    asset_count: int
    panel_index: int
    source_kind: SourceKind
    arrangement: FigureArrangement
    confidence: float
    review_reasons: tuple[str, ...]


def _caption_for(box: BoundingBox, candidates: tuple[_CaptionCandidate, ...]) -> _CaptionCandidate | None:
    return next((
        candidate for candidate in candidates
        if box[0] <= (candidate.bbox[0] + candidate.bbox[2]) / 2 <= box[2]
        and box[1] <= (candidate.bbox[1] + candidate.bbox[3]) / 2 <= box[3]
    ), None)


def _source_kind(metadata: _SourceMetadata) -> SourceKind:
    has_vector = metadata.drawing_count > 0
    has_raster = metadata.image_count > 0
    if has_vector and has_raster:
        return SourceKind.MIXED
    if has_vector:
        return SourceKind.VECTOR
    return SourceKind.RASTER


def _emit_panel(build: _PanelBuild) -> tuple[CropArtifact, FigureAsset]:
    bbox = build.panel_bbox
    caption_bbox = build.caption.bbox if build.caption is not None else None
    caption_text = (build.caption.text or "") if build.caption is not None else ""
    if (
        caption_bbox is not None
        and build.caption is not None
        and not build.caption.excluded
        and bbox[1] < caption_bbox[1] < bbox[3]
    ):
        bbox = (bbox[0], bbox[1], bbox[2], caption_bbox[1])
    x_scale = build.source_image.width / (build.source_bbox[2] - build.source_bbox[0])
    y_scale = build.source_image.height / (build.source_bbox[3] - build.source_bbox[1])
    pixel_box = (
        round((bbox[0] - build.source_bbox[0]) * x_scale),
        round((bbox[1] - build.source_bbox[1]) * y_scale),
        round((bbox[2] - build.source_bbox[0]) * x_scale),
        round((bbox[3] - build.source_bbox[1]) * y_scale),
    )
    panel = build.source_image.crop(pixel_box)
    image_path = build.source.image_path.with_name(
        f"{build.source.image_path.stem}-{build.panel_index}.png"
    )
    provenance_path = image_path.with_suffix(".json")
    panel.save(image_path, format="PNG")
    asset_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
    review_reasons = build.review_reasons
    source_manual_review = build.source_metadata.manual_review_required and not set(
        build.source_metadata.review_reasons).difference(review_reasons)
    manual_review = source_manual_review or bool(review_reasons)
    metadata = FigureAssetMetadata(
        source_pdf=build.source_metadata.source_pdf,
        page_number=build.source_metadata.page_number,
        item_number=build.source_metadata.item_number,
        image_bbox=bbox,
        caption_text=caption_text,
        caption_bbox=caption_bbox,
        asset_count=build.asset_count,
        panel_index=build.panel_index,
        panel_mode=(
            PanelMode.SINGLE
            if build.asset_count == 1
            else PanelMode.COMPOSITE
            if build.arrangement is FigureArrangement.COMPOSITE
            else PanelMode.SEPARATE
        ),
        arrangement=build.arrangement,
        source_kind=build.source_kind,
        display_size=(
            display_size_for_width(bbox[2] - bbox[0])
        ),
        dpi=build.source_metadata.dpi,
        width_px=panel.width,
        height_px=panel.height,
        asset_hash=asset_hash,
        confidence=build.confidence,
        manual_review_required=manual_review,
        review_reasons=review_reasons,
    )
    payload = metadata.model_dump(mode="json")
    payload.update(asset_mode="pdf_figure_panel_crop_hd", bbox=list(bbox))
    provenance_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifact = CropArtifact(image_path, provenance_path, panel.width, panel.height)
    return artifact, FigureAsset(image_path, metadata)


def route_figure(passage: str, source: CropArtifact) -> RoutedFigure:
    """Return ordered captionless assets and the matching registered layout class."""
    metadata = _SourceMetadata.model_validate_json(source.provenance_path.read_text(encoding="utf-8"))
    with Image.open(source.image_path) as opened:
        image = opened.convert("RGB")
    source_bbox = metadata.bbox or (0.0, 0.0, float(image.width), float(image.height))
    expected_count = expected_panel_count(passage)
    boxes = metadata.panel_bboxes if 1 <= len(metadata.panel_bboxes) <= 3 else (source_bbox,)
    component_group = (
        metadata.component_count > len(boxes)
        and not metadata.caption_candidates
    )
    if component_group:
        boxes = (source_bbox,)
    semantic_panel_count = 1 if component_group else expected_count
    arrangement = detect_arrangement(boxes)
    boxes = order_panel_bboxes(boxes, arrangement)
    captions = (
        metadata.caption_candidates
        if len(metadata.caption_candidates) == len(boxes)
        else tuple(_caption_for(box, metadata.caption_candidates) for box in boxes)
    )
    unsafe_composite = len(boxes) > 1 and arrangement is FigureArrangement.COMPOSITE
    missing_caption_text = semantic_panel_count > 1 and any(
        not (candidate.text or "").strip() for candidate in metadata.caption_candidates)
    missing_caption_geometry = (
        semantic_panel_count > 1 and len(metadata.caption_candidates) != len(boxes)
    )
    panel_evidence_unavailable = semantic_panel_count > 1 and not metadata.panel_bboxes
    panel_evidence_mismatch = (
        semantic_panel_count > 1
        and bool(metadata.panel_bboxes)
        and len(metadata.panel_bboxes) != semantic_panel_count
    )
    source_review_reasons = tuple(reason for reason in metadata.review_reasons
                                  if semantic_panel_count > 1 or reason != "ambiguous_raster_caption")
    reasons = tuple(dict.fromkeys((*source_review_reasons, *(
        ("overlapping panel geometry",) if unsafe_composite else ()
    ), *(("panel evidence unavailable",) if panel_evidence_unavailable else ()), *(
        ("caption geometry unavailable",) if missing_caption_geometry else ()
    ), *(
        ("caption text unavailable",) if missing_caption_text else ()
    ), *(
        ("panel evidence does not match expected count",) if panel_evidence_mismatch else ()
    ))))
    confidence = 0.98 if metadata.panel_bboxes and not reasons else 0.70 if not reasons else 0.45
    outputs = tuple(
        _emit_panel(_PanelBuild(
            source=source,
            source_metadata=metadata,
            source_bbox=source_bbox,
            source_image=image,
            panel_bbox=box,
            caption=captions[index - 1],
            asset_count=len(boxes),
            panel_index=index,
            source_kind=_source_kind(metadata),
            arrangement=arrangement,
            confidence=confidence,
            review_reasons=reasons,
        ))
        for index, box in enumerate(boxes, 1)
    )
    artifacts = tuple(output[0] for output in outputs)
    figure_assets = tuple(output[1] for output in outputs)
    layout_metadata = classify_final_layout(tuple(asset.metadata for asset in figure_assets))
    return RoutedFigure(layout_metadata.layout, artifacts, figure_assets, layout_metadata)
