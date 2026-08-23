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
from .pdf_hwp_experiment import is_experiment_text
from .pdf_hwp_raster_caption_segmentation import expected_panel_labels


class _CaptionCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    text: str | None = None
    bbox: BoundingBox | None = None
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
        if candidate.bbox is not None
        and box[0] <= (candidate.bbox[0] + candidate.bbox[2]) / 2 <= box[2]
        and box[1] <= (candidate.bbox[1] + candidate.bbox[3]) / 2 <= box[3] + 24
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
    if caption_bbox is not None and bbox[1] < caption_bbox[1] < bbox[3]:
        trimmed = (bbox[0], bbox[1], bbox[2], caption_bbox[1])
        if trimmed[2] > trimmed[0] and trimmed[3] > trimmed[1]:
            bbox = trimmed
    x_span = build.source_bbox[2] - build.source_bbox[0]
    y_span = build.source_bbox[3] - build.source_bbox[1]
    empty_geometry = x_span <= 0 or y_span <= 0
    if empty_geometry:
        pixel_box = (0, 0, build.source_image.width, build.source_image.height)
    else:
        x_scale = build.source_image.width / x_span
        y_scale = build.source_image.height / y_span
        left = round((bbox[0] - build.source_bbox[0]) * x_scale)
        top = round((bbox[1] - build.source_bbox[1]) * y_scale)
        right = round((bbox[2] - build.source_bbox[0]) * x_scale)
        bottom = round((bbox[3] - build.source_bbox[1]) * y_scale)
        if left > right or top > bottom:
            empty_geometry = True
        pixel_box = (
            max(0, min(left, right)),
            max(0, min(top, bottom)),
            min(build.source_image.width, max(left, right)),
            min(build.source_image.height, max(top, bottom)),
        )
        if pixel_box[2] <= pixel_box[0] or pixel_box[3] <= pixel_box[1]:
            empty_geometry = True
            pixel_box = (0, 0, build.source_image.width, build.source_image.height)
    panel = build.source_image.crop(pixel_box)
    image_path = build.source.image_path.with_name(
        f"{build.source.image_path.stem}-{build.panel_index}.png"
    )
    provenance_path = image_path.with_suffix(".json")
    dpi_x = panel.width * 72.0 / max(bbox[2] - bbox[0], 0.1)
    dpi_y = panel.height * 72.0 / max(bbox[3] - bbox[1], 0.1)
    panel.save(image_path, format="PNG", dpi=(dpi_x, dpi_y))
    asset_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
    review_reasons = build.review_reasons
    if empty_geometry:
        review_reasons = tuple(dict.fromkeys((*review_reasons, "empty panel geometry")))
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


def stamp_single_prompt_figure(source: CropArtifact) -> CropArtifact:
    """Write the final one-panel contract onto an unsplit prompt crop."""
    payload = json.loads(source.provenance_path.read_text(encoding="utf-8"))
    bbox = payload.get("bbox") or payload.get("image_bbox") or payload.get("source_bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return source
    image_bbox = tuple(float(value) for value in bbox)
    metadata = FigureAssetMetadata(
        source_pdf=Path(str(payload.get("source_pdf") or "source.pdf")),
        page_number=int(payload.get("page_number") or 1),
        item_number=int(payload.get("item_number") or 1),
        image_bbox=image_bbox,
        caption_text="",
        caption_bbox=None,
        asset_count=1,
        panel_index=1,
        panel_mode=PanelMode.COMPOSITE,
        arrangement=FigureArrangement.COMPOSITE,
        source_kind=_source_kind(_SourceMetadata.model_validate(payload)),
        display_size=display_size_for_width(image_bbox[2] - image_bbox[0]),
        dpi=int(payload.get("dpi") or 300),
        width_px=source.width_px,
        height_px=source.height_px,
        asset_hash=hashlib.sha256(source.image_path.read_bytes()).hexdigest(),
        confidence=0.9,
        manual_review_required=False,
        review_reasons=(),
    )
    stamped = metadata.model_dump(mode="json")
    stamped.update({
        "asset_mode": payload.get("asset_mode") or "pdf_figure_object_crop_hd",
        "bbox": list(image_bbox),
    })
    source.provenance_path.write_text(
        json.dumps(stamped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return source


def _emit_experiment_composite(
    source: CropArtifact,
    metadata: _SourceMetadata,
    source_bbox: BoundingBox,
    image: Image.Image,
    boxes: tuple[BoundingBox, ...],
) -> tuple[CropArtifact, FigureAsset]:
    """Join disjoint image-only experiment components without intervening prose."""
    x_scale = image.width / (source_bbox[2] - source_bbox[0])
    y_scale = image.height / (source_bbox[3] - source_bbox[1])
    panels = tuple(image.crop((
        round((box[0] - source_bbox[0]) * x_scale),
        round((box[1] - source_bbox[1]) * y_scale),
        round((box[2] - source_bbox[0]) * x_scale),
        round((box[3] - source_bbox[1]) * y_scale),
    )) for box in boxes)
    gap = max(8, round(metadata.dpi / 20))
    bottom = panels[1:]
    bottom_width = sum(panel.width for panel in bottom) + gap * max(len(bottom) - 1, 0)
    horizontal_padding = round(25.0 * x_scale)
    content_width = max(panels[0].width, bottom_width)
    composite = Image.new(
        "RGB", (content_width + 2 * horizontal_padding,
                panels[0].height + gap + max(panel.height for panel in bottom)),
        "white",
    )
    composite.paste(panels[0], ((composite.width - panels[0].width) // 2, 0))
    left = (composite.width - bottom_width) // 2
    for panel in bottom:
        composite.paste(panel, (left, panels[0].height + gap))
        left += panel.width + gap
    image_path = source.image_path.with_name(f"{source.image_path.stem}-1.png")
    provenance_path = image_path.with_suffix(".json")
    composite.save(image_path, format="PNG", dpi=(metadata.dpi, metadata.dpi))
    gap_points = gap / x_scale
    content_width_points = max(
        boxes[0][2] - boxes[0][0],
        sum(box[2] - box[0] for box in boxes[1:]) + gap_points * max(len(boxes) - 2, 0),
    )
    content_fraction = content_width / composite.width
    print_width = content_width_points * 0.85 / content_fraction
    print_height = print_width * composite.height / composite.width
    print_bbox = (0.0, 0.0, print_width, print_height)
    figure_metadata = FigureAssetMetadata(
        source_pdf=metadata.source_pdf, page_number=metadata.page_number,
        item_number=metadata.item_number, image_bbox=print_bbox,
        caption_text="", caption_bbox=None, asset_count=1, panel_index=1,
        panel_mode=PanelMode.COMPOSITE, arrangement=FigureArrangement.COMPOSITE,
        source_kind=_source_kind(metadata), display_size=display_size_for_width(print_width),
        dpi=metadata.dpi, width_px=composite.width, height_px=composite.height,
        asset_hash=hashlib.sha256(image_path.read_bytes()).hexdigest(),
        confidence=0.98, manual_review_required=False, review_reasons=(),
    )
    payload = figure_metadata.model_dump(mode="json")
    payload.update(
        asset_mode="pdf_figure_experiment_components_hd",
        source_bbox=list(source_bbox), bbox=list(print_bbox),
        component_bboxes=[list(box) for box in boxes],
    )
    provenance_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    artifact = CropArtifact(
        image_path, provenance_path, composite.width, composite.height,
    )
    return artifact, FigureAsset(image_path, figure_metadata)


def route_single_composite(source: CropArtifact) -> RoutedFigure:
    """Keep one exact prompt crop when panel separation is not source-safe."""
    artifact = stamp_single_prompt_figure(source)
    metadata = FigureAssetMetadata.model_validate_json(
        artifact.provenance_path.read_text(encoding="utf-8")
    )
    figure_asset = FigureAsset(artifact.image_path, metadata)
    layout_metadata = classify_final_layout((metadata,))
    return RoutedFigure(
        layout_metadata.layout,
        (artifact,),
        (figure_asset,),
        layout_metadata,
    )


def route_figure(passage: str, source: CropArtifact) -> RoutedFigure:
    """Return ordered captionless assets and the matching registered layout class."""
    metadata = _SourceMetadata.model_validate_json(source.provenance_path.read_text(encoding="utf-8"))
    with Image.open(source.image_path) as opened:
        image = opened.convert("RGB")
    source_bbox = metadata.bbox or (0.0, 0.0, float(image.width), float(image.height))
    experiment = is_experiment_text(passage)
    expected_count = 1 if experiment else expected_panel_count(passage)
    boxes = metadata.panel_bboxes if 1 <= len(metadata.panel_bboxes) <= 3 else (source_bbox,)
    if experiment and len(boxes) > 1:
        artifact, figure_asset = _emit_experiment_composite(
            source, metadata, source_bbox, image, boxes,
        )
        layout = classify_final_layout((figure_asset.metadata,))
        return RoutedFigure(layout.layout, (artifact,), (figure_asset,), layout)
    component_group = (
        metadata.component_count > len(boxes)
        and not metadata.caption_candidates
        and metadata.image_count <= 1
    )
    caption_texts = tuple((candidate.text or "").strip() for candidate in metadata.caption_candidates)
    unlabeled_triple = (
        not experiment
        and expected_count == 1
        and len(boxes) == 3
        and not any(caption_texts)
    )
    if component_group or unlabeled_triple:
        boxes = (source_bbox,)
    semantic_panel_count = 1 if component_group else expected_count
    arrangement = detect_arrangement(boxes)
    boxes = order_panel_bboxes(boxes, arrangement)
    expected_labels = expected_panel_labels(passage)
    semantic_caption_order = (
        len(expected_labels) == len(boxes)
        and len(boxes) > 1
        and bool(metadata.panel_bboxes)
    )
    caption_candidates = metadata.caption_candidates
    if semantic_caption_order and len(caption_candidates) != len(boxes):
        caption_candidates = tuple(
            _CaptionCandidate(text=label, bbox=None, confidence=1.0)
            for label in expected_labels
        )
    elif (
        len(boxes) == 2
        and not any((candidate.text or "").strip() for candidate in caption_candidates)
    ):
        caption_candidates = (
            _CaptionCandidate(text="(가)", bbox=None, confidence=1.0),
            _CaptionCandidate(text="(나)", bbox=None, confidence=1.0),
        )
    captions = (
        caption_candidates
        if len(caption_candidates) == len(boxes)
        else tuple(_caption_for(box, caption_candidates) for box in boxes)
    )
    unsafe_composite = (
        len(boxes) > 1
        and arrangement is FigureArrangement.COMPOSITE
        and not experiment
    )
    missing_caption_text = semantic_panel_count > 1 and any(
        not (candidate.text or "").strip() for candidate in caption_candidates)
    missing_caption_geometry = (
        semantic_panel_count > 1 and len(caption_candidates) != len(boxes)
    )
    panel_evidence_unavailable = semantic_panel_count > 1 and not metadata.panel_bboxes
    panel_evidence_mismatch = (
        semantic_panel_count > 1
        and bool(metadata.panel_bboxes)
        and len(metadata.panel_bboxes) != semantic_panel_count
    )
    source_review_reasons = tuple(
        reason for reason in metadata.review_reasons
        if not (semantic_caption_order and reason == "caption geometry unavailable")
        and (semantic_panel_count > 1 or reason != "ambiguous_raster_caption")
    )
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
