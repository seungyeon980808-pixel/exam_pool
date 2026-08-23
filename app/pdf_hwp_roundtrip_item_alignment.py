"""Item-number-aligned PDF clip comparison for subset round-trip QA."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

import fitz
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageStat

from .pdf_hwp_roundtrip_generated_detection import detect_generated_items
from .pdf_hwp_pipeline import detect_items
from .pdf_hwp_pipeline_models import DetectedItem


PixelBBox = tuple[int, int, int, int]
_CONTENT_WHITE: Final = 248
_DIFF_LEVEL: Final = 24
_NORMALIZED_WIDTH: Final = 900
_COLUMN_WIDTH: Final = 420
_CELL_HEIGHT: Final = 280
_GUTTER: Final = 16


class AlignmentIssue(StrEnum):
    """Stable item-alignment failure codes consumed by reports."""

    DUPLICATE_SOURCE_ITEM = "duplicate_source_item"
    DUPLICATE_GENERATED_ITEM = "duplicate_generated_item"
    MISSING_SOURCE_ITEM = "missing_source_item"
    MISSING_GENERATED_ITEM = "missing_generated_item"
    VISUAL_MISMATCH = "visual_mismatch"
    EMPTY_RENDER = "empty_render"


_ISSUE_ORDER: Final = tuple(AlignmentIssue)


@dataclass(frozen=True, slots=True)
class ItemAlignmentRequest:
    """Paths, selected identities, and explicit visual thresholds."""

    source_pdf: Path
    generated_pdf: Path
    selected_item_numbers: tuple[int, ...]
    output_dir: Path
    dpi: int
    mismatch_threshold: float = 0.02


@dataclass(frozen=True, slots=True)
class AlignedItemPair:
    """One unambiguous source/generated item pair."""

    item_number: int
    source: DetectedItem
    generated: DetectedItem


@dataclass(frozen=True, slots=True)
class ItemVisualComparison:
    """Rendered evidence and normalized metrics for one aligned item."""

    item_number: int
    source_render: Path
    generated_render: Path
    pixel_mae: float
    edge_mae: float
    diff_bbox: PixelBBox | None
    issues: tuple[AlignmentIssue, ...]


@dataclass(frozen=True, slots=True)
class ItemAlignmentResult:
    """Complete deterministic subset-alignment verdict."""

    pairs: tuple[AlignedItemPair, ...]
    comparisons: tuple[ItemVisualComparison, ...]
    missing_source_items: tuple[int, ...]
    missing_generated_items: tuple[int, ...]
    duplicate_source_items: tuple[int, ...]
    duplicate_generated_items: tuple[int, ...]
    issues: tuple[AlignmentIssue, ...]
    contact_sheet: Path


@dataclass(frozen=True, slots=True)
class _RenderClip:
    item: DetectedItem
    target: Path
    dpi: int


@dataclass(frozen=True, slots=True)
class _ContactRow:
    item_number: int
    issues: tuple[AlignmentIssue, ...]
    source_render: Path | None
    generated_render: Path | None


def _selected_items(
    items: tuple[DetectedItem, ...], selected: tuple[int, ...],
) -> dict[int, tuple[DetectedItem, ...]]:
    return {
        number: tuple(item for item in items if item.item_number == number)
        for number in selected
    }


def _render_clip(document: fitz.Document, spec: _RenderClip) -> None:
    page = document[spec.item.page_number - 1]
    pixmap = page.get_pixmap(
        dpi=spec.dpi,
        colorspace=fitz.csRGB,
        alpha=False,
        clip=fitz.Rect(spec.item.bbox),
    )
    spec.target.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(spec.target)


def _content_crop(image: Image.Image) -> Image.Image | None:
    gray = image.convert("L")
    mask = gray.point(lambda value: 255 if value < _CONTENT_WHITE else 0)
    bbox = mask.getbbox()
    gray.close()
    mask.close()
    return image.crop(bbox).convert("L") if bbox is not None else None


def _normalized_pair(left: Image.Image, right: Image.Image) -> tuple[Image.Image, Image.Image] | None:
    crops = (_content_crop(left), _content_crop(right))
    if crops[0] is None or crops[1] is None:
        for crop in crops:
            if crop is not None:
                crop.close()
        return None
    heights = tuple(max(1, round(crop.height * _NORMALIZED_WIDTH / crop.width)) for crop in crops)
    height = max(heights)
    normalized: list[Image.Image] = []
    for crop, resized_height in zip(crops, heights, strict=True):
        resized = crop.resize((_NORMALIZED_WIDTH, resized_height), Image.Resampling.LANCZOS)
        canvas = Image.new("L", (_NORMALIZED_WIDTH, height), 255)
        canvas.paste(resized, (0, (height - resized_height) // 2))
        resized.close()
        crop.close()
        normalized.append(canvas)
    return normalized[0], normalized[1]


def _difference_metrics(left: Image.Image, right: Image.Image) -> tuple[float, float, PixelBBox | None]:
    pixel_diff = ImageChops.difference(left, right)
    left_edges = left.filter(ImageFilter.FIND_EDGES)
    right_edges = right.filter(ImageFilter.FIND_EDGES)
    edge_diff = ImageChops.difference(left_edges, right_edges)
    meaningful = pixel_diff.point(lambda value: 255 if value >= _DIFF_LEVEL else 0)
    result = (
        round(ImageStat.Stat(pixel_diff).mean[0] / 255, 6),
        round(ImageStat.Stat(edge_diff).mean[0] / 255, 6),
        meaningful.getbbox(),
    )
    for image in (pixel_diff, left_edges, right_edges, edge_diff, meaningful):
        image.close()
    return result


def _write_contact_sheet(rows: tuple[_ContactRow, ...], target: Path) -> None:
    row_height = 24 + _CELL_HEIGHT + _GUTTER
    width = _COLUMN_WIDTH * 2 + _GUTTER * 2 + 12
    canvas = Image.new("RGB", (width, max(80, _GUTTER + len(rows) * row_height)), "#e8ebef")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    for row_index, row in enumerate(rows):
        y = _GUTTER + row_index * row_height
        label = ", ".join(issue.value for issue in row.issues)
        draw.text((_GUTTER, y), f"ITEM {row.item_number} - {label}", fill="black", font=font)
        for render, x, side in (
            (row.source_render, _GUTTER, "SOURCE"),
            (row.generated_render, _GUTTER + _COLUMN_WIDTH + 12, "GENERATED"),
        ):
            draw.text((x, y + 12), side, fill="black", font=font)
            if render is None:
                draw.text((x + 150, y + 100), "MISSING", fill="#9b1c1c", font=font)
                continue
            with Image.open(render) as opened:
                thumbnail = opened.convert("RGB")
            thumbnail.thumbnail((_COLUMN_WIDTH, _CELL_HEIGHT - 20), Image.Resampling.LANCZOS)
            canvas.paste(thumbnail, (x + (_COLUMN_WIDTH - thumbnail.width) // 2, y + 30))
            thumbnail.close()
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target)
    canvas.close()


def align_and_compare_items(request: ItemAlignmentRequest) -> ItemAlignmentResult:
    """Detect, align, render, and compare only the requested item identities."""
    request.output_dir.mkdir(parents=True, exist_ok=True)
    source_groups = _selected_items(detect_items(request.source_pdf).items, request.selected_item_numbers)
    generated_groups = _selected_items(
        detect_generated_items(request.generated_pdf).items, request.selected_item_numbers,
    )
    missing_source = tuple(number for number in request.selected_item_numbers if not source_groups[number])
    missing_generated = tuple(number for number in request.selected_item_numbers if not generated_groups[number])
    duplicate_source = tuple(number for number in request.selected_item_numbers if len(source_groups[number]) > 1)
    duplicate_generated = tuple(number for number in request.selected_item_numbers if len(generated_groups[number]) > 1)
    pairs = tuple(
        AlignedItemPair(number, source_groups[number][0], generated_groups[number][0])
        for number in request.selected_item_numbers
        if len(source_groups[number]) == len(generated_groups[number]) == 1
    )
    render_dir = request.output_dir / "renders"
    source_renders: dict[int, Path] = {}
    generated_renders: dict[int, Path] = {}
    with fitz.open(request.source_pdf) as source_document, fitz.open(request.generated_pdf) as generated_document:
        for number in request.selected_item_numbers:
            if len(source_groups[number]) == 1:
                target = render_dir / f"source-item-{number:04d}.png"
                _render_clip(source_document, _RenderClip(source_groups[number][0], target, request.dpi))
                source_renders[number] = target
            if len(generated_groups[number]) == 1:
                target = render_dir / f"generated-item-{number:04d}.png"
                _render_clip(generated_document, _RenderClip(generated_groups[number][0], target, request.dpi))
                generated_renders[number] = target
    comparisons: list[ItemVisualComparison] = []
    rows: list[_ContactRow] = []
    for pair in pairs:
        with Image.open(source_renders[pair.item_number]) as left, Image.open(generated_renders[pair.item_number]) as right:
            normalized = _normalized_pair(left, right)
        if normalized is None:
            metrics = (0.0, 0.0, None)
            pair_issues = (AlignmentIssue.EMPTY_RENDER,)
        else:
            metrics = _difference_metrics(*normalized)
            pair_issues = (
                (AlignmentIssue.VISUAL_MISMATCH,)
                if max(metrics[0], metrics[1]) >= request.mismatch_threshold else ()
            )
            normalized[0].close()
            normalized[1].close()
        comparison = ItemVisualComparison(
            pair.item_number, source_renders[pair.item_number], generated_renders[pair.item_number],
            metrics[0], metrics[1], metrics[2], pair_issues,
        )
        comparisons.append(comparison)
        if pair_issues:
            rows.append(_ContactRow(
                pair.item_number, pair_issues, comparison.source_render, comparison.generated_render,
            ))
    for number in request.selected_item_numbers:
        item_issues = tuple(issue for issue, members in (
            (AlignmentIssue.DUPLICATE_SOURCE_ITEM, duplicate_source),
            (AlignmentIssue.DUPLICATE_GENERATED_ITEM, duplicate_generated),
            (AlignmentIssue.MISSING_SOURCE_ITEM, missing_source),
            (AlignmentIssue.MISSING_GENERATED_ITEM, missing_generated),
        ) if number in members)
        if item_issues:
            rows.append(_ContactRow(number, item_issues, source_renders.get(number), generated_renders.get(number)))
    issue_set = {issue for row in rows for issue in row.issues}
    contact_sheet = request.output_dir / "item-failures.png"
    _write_contact_sheet(tuple(rows), contact_sheet)
    return ItemAlignmentResult(
        pairs, tuple(comparisons), missing_source, missing_generated,
        duplicate_source, duplicate_generated,
        tuple(issue for issue in _ISSUE_ORDER if issue in issue_set), contact_sheet,
    )
