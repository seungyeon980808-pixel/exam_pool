"""Detect isolated raster panel captions without interpreting diagram labels."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final

from PIL import Image


BBox = tuple[float, float, float, float]
PixelBBox = tuple[int, int, int, int]
_INK_THRESHOLD: Final = 235
_PANEL_LABEL_RE: Final = re.compile(r"\(([가나다])\)")
_VALID_PANEL_LABELS: Final = (("(가)", "(나)"), ("(가)", "(나)", "(다)"))
_VALID_THREE_PANEL_LABELS: Final = frozenset({
    ("(가)", "(나)", "(다)"),
    ("A", "B", "C"),
})
_ALL_PANEL_LABELS: Final = ("(가)", "(나)", "(다)", "A", "B", "C")


@dataclass(frozen=True, slots=True)
class RasterCaption:
    text: str
    bbox: BBox
    pixel_bbox: PixelBBox
    excluded: bool
    confidence: float


_LABEL_PARTICLES: Final = frozenset({"", "는", "은", "이", "가", "."})
_INQUIRY_HEADING: Final = re.compile(r"\[탐구\s*(?:과정|활동|목표|결과)\]")
_PROCESS_STEP_TAIL: Final = re.compile(r"\((?:라|마|바|사|아)\)")
_CAPTION_TITLE: Final = re.compile(r"^\s+\S.{0,23}$")


def panel_label_token(text: str, labels: tuple[str, ...]) -> str | None:
    """Map a PDF word or span onto an expected panel label without guessing."""
    stripped = text.strip().rstrip(".")
    if stripped in labels:
        return stripped
    for label in labels:
        extra = stripped[len(label):] if stripped.startswith(label) else None
        if extra is None:
            continue
        if extra in _LABEL_PARTICLES:
            return label
        if _CAPTION_TITLE.fullmatch(extra) and not re.search(r"[.?!다요]$", extra.strip()):
            return label
    return None


def expected_panel_labels(source_text: str) -> tuple[str, ...]:
    """Read an ordered 2/3-panel label set from the authoritative item text."""
    found: list[str] = []
    for match in _PANEL_LABEL_RE.finditer(source_text):
        label = f"({match.group(1)})"
        if label not in found:
            found.append(label)
    labels = tuple(found)
    if labels not in _VALID_PANEL_LABELS:
        return ()
    # Inquiry steps enumerate (가)~(마) as procedure, not figure captions.
    if _INQUIRY_HEADING.search(source_text) and _PROCESS_STEP_TAIL.search(source_text):
        return ()
    return labels


def infer_three_panel_labels(
    texts: tuple[tuple[str, tuple[float, float, float, float]], ...],
    panels: tuple[tuple[float, float, float, float], ...],
) -> tuple[str, ...]:
    """Read A/B/C or (가)/(나)/(다) only from the caption band under three panels."""
    if len(panels) != 3:
        return ()
    band_top = min(panel[3] for panel in panels) - 2
    band_bottom = max(panel[3] for panel in panels) + 22
    found: list[str] = []
    for text, bbox in sorted(texts, key=lambda item: item[1][0]):
        token = panel_label_token(text, _ALL_PANEL_LABELS)
        y_mid = (bbox[1] + bbox[3]) / 2
        if token is None or token in found or not (band_top < y_mid <= band_bottom):
            continue
        found.append(token)
    labels = tuple(found)
    return labels if labels in _VALID_THREE_PANEL_LABELS else ()


def _runs(values: tuple[int, ...]) -> tuple[tuple[int, int], ...]:
    found: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate((*values, 0)):
        if value > 0 and start is None:
            start = index
        elif value == 0 and start is not None:
            found.append((start, index))
            start = None
    return tuple(found)


def _binary_ink(image: Image.Image) -> Image.Image:
    return image.point(lambda value: int(value < _INK_THRESHOLD), mode="L")


def _ink_row_counts(image: Image.Image) -> tuple[int, ...]:
    """Count dark pixels per row using Pillow's native lookup conversion."""
    width, height = image.size
    ink = _binary_ink(image).tobytes()
    return tuple(sum(ink[y * width:(y + 1) * width]) for y in range(height))


def _ink_column_counts(image: Image.Image, top: int, bottom: int) -> tuple[int, ...]:
    """Count dark pixels per column within a vertical interval."""
    band = _binary_ink(image.crop((0, top, image.width, bottom)))
    transposed = band.transpose(Image.Transpose.TRANSPOSE)
    height = bottom - top
    ink = transposed.tobytes()
    return tuple(sum(ink[x * height:(x + 1) * height]) for x in range(image.width))


def _pdf_box(pixel_box: PixelBBox, image_size: tuple[int, int], image_bbox: BBox) -> BBox:
    width, height = image_size
    x_scale = (image_bbox[2] - image_bbox[0]) / width
    y_scale = (image_bbox[3] - image_bbox[1]) / height
    x0, y0, x1, y1 = pixel_box
    return tuple(round(value, 3) for value in (
        image_bbox[0] + x0 * x_scale,
        image_bbox[1] + y0 * y_scale,
        image_bbox[0] + x1 * x_scale,
        image_bbox[1] + y1 * y_scale,
    ))


def detect_raster_captions(
    image: Image.Image,
    image_bbox: BBox,
    expected_labels: tuple[str, ...],
) -> tuple[tuple[RasterCaption, ...], bool]:
    """Return isolated bottom groups and whether their identity is ambiguous."""
    width, height = image.size
    row_counts = _ink_row_counts(image)
    significant = tuple(
        run for run in _runs(row_counts)
        if sum(row_counts[run[0]:run[1]]) >= max(8, round(width * 0.01))
    )
    if len(significant) < 2:
        return (), False
    candidate_index: int | None = None
    for index in range(len(significant) - 1, 0, -1):
        y0, y1 = significant[index]
        run_height = y1 - y0
        if (
            y0 / height >= 0.72
            and 0.04 <= run_height / height <= 0.22
            and max(row_counts[y0:y1]) / width <= 0.25
            and y0 - significant[index - 1][1] >= max(4, round(height * 0.015))
        ):
            candidate_index = index
            break
    if candidate_index is None:
        return (), False
    y0, y1 = significant[candidate_index]
    run_height = y1 - y0
    column_counts = _ink_column_counts(image, y0, y1)
    raw_groups = _runs(column_counts)
    joined: list[list[int]] = []
    join_gap = max(3, round(run_height * 0.45))
    for x0, x1 in raw_groups:
        if not joined or x0 - joined[-1][1] > join_gap:
            joined.append([x0, x1])
        else:
            joined[-1][1] = x1
    widths = tuple(x1 - x0 for x0, x1 in joined)
    geometry_safe = (
        1 <= len(joined) <= 3
        and (len(joined) == 1 or min(widths) / max(widths) >= 0.75)
        and max(widths) / width <= 0.18
    )
    has_later_ink = candidate_index < len(significant) - 1
    safe = geometry_safe and len(expected_labels) == len(joined) and not has_later_ink
    padding = max(2, round(run_height * 0.08))
    texts = expected_labels if len(expected_labels) == len(joined) else ("",) * len(joined)
    regions = tuple(
        RasterCaption(
            texts[index],
            _pdf_box(pixel_box, image.size, image_bbox),
            pixel_box,
            safe,
            0.99 if safe else 0.5,
        )
        for index, (x0, x1) in enumerate(joined)
        for pixel_box in ((
            max(0, x0 - padding),
            max(0, y0 - padding),
            min(width - 1, x1 + padding),
            min(height - 1, y1 + padding),
        ),)
    )
    return regions, not safe


def _seam_pixel(image: Image.Image, left: float, right: float, bottom: int) -> int:
    start = max(0, round(left))
    stop = min(image.width, round(right) + 1)
    ink_counts = _ink_column_counts(image, 0, bottom)[start:stop]
    minimum_width = max(3, round((right - left) * 0.02))
    midpoint = (left + right) / 2
    candidates: tuple[tuple[int, int], ...] = ()
    for maximum_ink in (0, max(1, round(bottom * 0.001))):
        candidates = tuple(
            (run_start + start, run_end + start)
            for run_start, run_end in _runs(tuple(count <= maximum_ink for count in ink_counts))
            if run_end - run_start >= minimum_width
        )
        if candidates:
            break
    if not candidates:
        return round(midpoint)
    seam = min(
        candidates,
        key=lambda run: (abs((run[0] + run[1]) / 2 - midpoint), -(run[1] - run[0])),
    )
    return round((seam[0] + seam[1]) / 2)


def caption_panel_bboxes(
    image: Image.Image,
    captions: tuple[RasterCaption, ...],
    image_bbox: BBox,
) -> tuple[BBox, ...]:
    pixel_centers = tuple((caption.pixel_bbox[0] + caption.pixel_bbox[2]) / 2 for caption in captions)
    caption_top = min(caption.pixel_bbox[1] for caption in captions)
    seam_pixels = tuple(
        _seam_pixel(image, pixel_centers[index], pixel_centers[index + 1], caption_top)
        for index in range(len(pixel_centers) - 1)
    )
    pdf_width = image_bbox[2] - image_bbox[0]
    boundaries = tuple(
        round(image_bbox[0] + seam * pdf_width / image.width, 3)
        for seam in seam_pixels
    )
    starts = (image_bbox[0], *boundaries)
    ends = (*boundaries, image_bbox[2])
    panel_bottom = min(caption.bbox[1] for caption in captions)
    return tuple((round(x0, 3), image_bbox[1], round(x1, 3), panel_bottom) for x0, x1 in zip(starts, ends))
