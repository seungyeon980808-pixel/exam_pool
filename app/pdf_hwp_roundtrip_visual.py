"""Deterministic PDF visual comparison primitives for round-trip QA."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

import fitz
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageStat


PixelBBox = tuple[int, int, int, int]
_CONTENT_WHITE: Final = 248
_DIFF_LEVEL: Final = 24
_NORMALIZED_WIDTH: Final = 900
_SHEET_COLUMN_WIDTH: Final = 420
_SHEET_CELL_HEIGHT: Final = 300
_SHEET_GUTTER: Final = 16
_ISSUE_ORDER: Final = (
    "page_count_mismatch",
    "visual_mismatch",
    "empty_render",
)


class VisualIssue(StrEnum):
    """Stable failure codes consumed by round-trip reports."""

    PAGE_COUNT_MISMATCH = "page_count_mismatch"
    VISUAL_MISMATCH = "visual_mismatch"
    EMPTY_RENDER = "empty_render"


@dataclass(frozen=True, slots=True)
class ComparisonRequest:
    """Validated paths and thresholds for one PDF comparison."""

    source_pdf: Path
    generated_pdf: Path
    output_dir: Path
    dpi: int = 144
    mismatch_threshold: float = 0.02


@dataclass(frozen=True, slots=True)
class PageAlignment:
    """One deterministic one-based page pairing, including missing sides."""

    source_page: int | None
    generated_page: int | None


@dataclass(frozen=True, slots=True)
class RenderedPage:
    """One rasterized PDF page."""

    page_number: int
    path: Path


@dataclass(frozen=True, slots=True)
class PageVisualResult:
    """Normalized metrics and evidence for one aligned page pair."""

    source_page: int
    generated_page: int
    pixel_mae: float
    edge_mae: float
    diff_bbox: PixelBBox | None
    failure_crop: Path | None
    issues: tuple[VisualIssue, ...]


@dataclass(frozen=True, slots=True)
class VisualComparisonResult:
    """Aggregate visual verdict with stable page alignment and artifacts."""

    issues: tuple[VisualIssue, ...]
    alignments: tuple[PageAlignment, ...]
    pages: tuple[PageVisualResult, ...]
    contact_sheet: Path


def align_page_numbers(source_count: int, generated_count: int) -> tuple[PageAlignment, ...]:
    """Pair pages by stable one-based index and retain unmatched positions."""
    return tuple(
        PageAlignment(
            index if index <= source_count else None,
            index if index <= generated_count else None,
        )
        for index in range(1, max(source_count, generated_count) + 1)
    )


def _render_pdf(request: ComparisonRequest, path: Path, prefix: str) -> tuple[RenderedPage, ...]:
    render_dir = request.output_dir / "renders"
    render_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[RenderedPage] = []
    with fitz.open(path) as document:
        for index, page in enumerate(document, 1):
            pixmap = page.get_pixmap(dpi=request.dpi, colorspace=fitz.csRGB, alpha=False)
            target = render_dir / f"{prefix}-page-{index:03d}.png"
            pixmap.save(target)
            rendered.append(RenderedPage(index, target))
    return tuple(rendered)


def _content_crop(image: Image.Image) -> Image.Image | None:
    gray = image.convert("L")
    mask = gray.point(lambda value: 255 if value < _CONTENT_WHITE else 0)
    bbox = mask.getbbox()
    gray.close()
    mask.close()
    return image.crop(bbox).convert("L") if bbox is not None else None


def _normalized_pair(left: Image.Image, right: Image.Image) -> tuple[Image.Image, Image.Image] | None:
    left_crop = _content_crop(left)
    right_crop = _content_crop(right)
    if left_crop is None or right_crop is None:
        if left_crop is not None:
            left_crop.close()
        if right_crop is not None:
            right_crop.close()
        return None
    left_height = max(1, round(left_crop.height * _NORMALIZED_WIDTH / left_crop.width))
    right_height = max(1, round(right_crop.height * _NORMALIZED_WIDTH / right_crop.width))
    height = max(left_height, right_height)
    normalized: list[Image.Image] = []
    for crop, resized_height in ((left_crop, left_height), (right_crop, right_height)):
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
    pixel_mae = ImageStat.Stat(pixel_diff).mean[0] / 255
    edge_mae = ImageStat.Stat(edge_diff).mean[0] / 255
    meaningful = pixel_diff.point(lambda value: 255 if value >= _DIFF_LEVEL else 0)
    bbox = meaningful.getbbox()
    meaningful.close()
    pixel_diff.close()
    left_edges.close()
    right_edges.close()
    edge_diff.close()
    return round(pixel_mae, 6), round(edge_mae, 6), bbox


def write_failure_crop(
    pair: tuple[Image.Image, Image.Image], bbox: PixelBBox, target: Path,
) -> None:
    """Write a labeled side-by-side crop around a meaningful normalized difference."""
    expanded = (
        max(0, bbox[0] - 12),
        max(0, bbox[1] - 12),
        min(pair[0].width, bbox[2] + 12),
        min(pair[0].height, bbox[3] + 12),
    )
    crops = tuple(image.crop(expanded).convert("RGB") for image in pair)
    canvas = Image.new("RGB", (crops[0].width * 2 + 12, crops[0].height + 24), "white")
    draw = ImageDraw.Draw(canvas)
    draw.text((4, 5), "SOURCE", fill="black", font=ImageFont.load_default())
    draw.text((crops[0].width + 16, 5), "GENERATED", fill="black", font=ImageFont.load_default())
    canvas.paste(crops[0], (0, 24))
    canvas.paste(crops[1], (crops[0].width + 12, 24))
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target)
    canvas.close()
    for crop in crops:
        crop.close()


def write_contact_sheet(
    pairs: tuple[tuple[RenderedPage, RenderedPage, tuple[VisualIssue, ...]], ...], target: Path,
) -> None:
    """Write fixed-size labeled rows for deterministically paired rendered pages."""
    row_height = 24 + _SHEET_CELL_HEIGHT + _SHEET_GUTTER
    width = _SHEET_COLUMN_WIDTH * 2 + _SHEET_GUTTER * 2 + 12
    canvas = Image.new("RGB", (width, max(80, _SHEET_GUTTER + len(pairs) * row_height)), "#e8ebef")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    for row, (source, generated, issues) in enumerate(pairs):
        y = _SHEET_GUTTER + row * row_height
        issue_label = ", ".join(issue.value for issue in issues) or "match"
        draw.text((_SHEET_GUTTER, y), f"SOURCE p{source.page_number} - {issue_label}", fill="black", font=font)
        right_x = _SHEET_GUTTER + _SHEET_COLUMN_WIDTH + 12
        draw.text((right_x, y), f"GENERATED p{generated.page_number}", fill="black", font=font)
        for page, x in ((source, _SHEET_GUTTER), (generated, right_x)):
            with Image.open(page.path) as opened:
                thumbnail = opened.convert("RGB")
            thumbnail.thumbnail((_SHEET_COLUMN_WIDTH, _SHEET_CELL_HEIGHT), Image.Resampling.LANCZOS)
            cell_x = x + (_SHEET_COLUMN_WIDTH - thumbnail.width) // 2
            canvas.paste(thumbnail, (cell_x, y + 24))
            thumbnail.close()
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target)
    canvas.close()


def compare_pdf_visuals(request: ComparisonRequest) -> VisualComparisonResult:
    """Render, align, compare, crop failures, and write a labeled contact sheet."""
    request.output_dir.mkdir(parents=True, exist_ok=True)
    source_pages = _render_pdf(request, request.source_pdf, "source")
    generated_pages = _render_pdf(request, request.generated_pdf, "generated")
    alignments = align_page_numbers(len(source_pages), len(generated_pages))
    page_results: list[PageVisualResult] = []
    sheet_pairs: list[tuple[RenderedPage, RenderedPage, tuple[VisualIssue, ...]]] = []
    for source, generated in zip(source_pages, generated_pages, strict=False):
        with Image.open(source.path) as source_image, Image.open(generated.path) as generated_image:
            normalized = _normalized_pair(source_image, generated_image)
        if normalized is None:
            issues = (VisualIssue.EMPTY_RENDER,)
            result = PageVisualResult(source.page_number, generated.page_number, 0.0, 0.0, None, None, issues)
        else:
            pixel_mae, edge_mae, bbox = _difference_metrics(*normalized)
            score = max(pixel_mae, edge_mae)
            issues = (VisualIssue.VISUAL_MISMATCH,) if score >= request.mismatch_threshold else ()
            crop_path = request.output_dir / "failures" / f"page-{source.page_number:03d}.png" if issues and bbox else None
            if crop_path is not None and bbox is not None:
                write_failure_crop(normalized, bbox, crop_path)
            result = PageVisualResult(
                source.page_number, generated.page_number, pixel_mae, edge_mae, bbox, crop_path, issues,
            )
            normalized[0].close()
            normalized[1].close()
        page_results.append(result)
        sheet_pairs.append((source, generated, issues))
    aggregate = {issue.value for page in page_results for issue in page.issues}
    if len(source_pages) != len(generated_pages):
        aggregate.add(VisualIssue.PAGE_COUNT_MISMATCH.value)
    contact_sheet = request.output_dir / "contact-sheet.png"
    write_contact_sheet(tuple(sheet_pairs), contact_sheet)
    issues = tuple(VisualIssue(code) for code in _ISSUE_ORDER if code in aggregate)
    return VisualComparisonResult(issues, alignments, tuple(page_results), contact_sheet)
