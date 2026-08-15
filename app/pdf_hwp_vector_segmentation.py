"""Cluster PDF vector drawings with only their geometrically connected labels."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final

import fitz


BBox = tuple[float, float, float, float]
_CAPTION_RE: Final = re.compile(r"^\([가나다]\)$")
_LABEL_DISTANCE: Final = 12.0


@dataclass(frozen=True, slots=True)
class VectorText:
    text: str
    bbox: BBox


@dataclass(frozen=True, slots=True)
class DrawingRegion:
    bbox: BBox


@dataclass(frozen=True, slots=True)
class VectorSelection:
    selected_bbox: BBox
    protected: tuple[VectorText, ...]
    captions: tuple[VectorText, ...]
    excluded_body: tuple[VectorText, ...]
    drawing_count: int
    text_span_count: int


def _bbox(rect: fitz.Rect) -> BBox:
    return tuple(round(float(value), 3) for value in rect)


def _texts(page: fitz.Page, source: fitz.Rect) -> tuple[VectorText, ...]:
    found: list[VectorText] = []
    for block in page.get_text("dict", clip=source)["blocks"]:
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                text = str(span.get("text", "")).strip()
                if text:
                    found.append(VectorText(text, _bbox(fitz.Rect(span["bbox"]))))
    return tuple(found)


def _near(cluster: fitz.Rect, region: VectorText) -> bool:
    expanded = fitz.Rect(
        cluster.x0 - _LABEL_DISTANCE,
        cluster.y0 - _LABEL_DISTANCE,
        cluster.x1 + _LABEL_DISTANCE,
        cluster.y1 + _LABEL_DISTANCE,
    )
    return not (expanded & fitz.Rect(region.bbox)).is_empty


def _touches(left: fitz.Rect, right: fitz.Rect) -> bool:
    return left.x1 >= right.x0 and right.x1 >= left.x0 and left.y1 >= right.y0 and right.y1 >= left.y0


def _connected(left: fitz.Rect, right: fitz.Rect) -> bool:
    gap = 2.0
    expanded = fitz.Rect(left.x0 - gap, left.y0 - gap, left.x1 + gap, left.y1 + gap)
    return _touches(expanded, right)


def _main_drawing_component(drawings: tuple[DrawingRegion, ...]) -> tuple[DrawingRegion, ...]:
    pending = list(drawings)
    components: list[tuple[DrawingRegion, ...]] = []
    while pending:
        component = [pending.pop(0)]
        changed = True
        while changed:
            changed = False
            for drawing in tuple(pending):
                if any(_connected(fitz.Rect(member.bbox), fitz.Rect(drawing.bbox)) for member in component):
                    component.append(drawing)
                    pending.remove(drawing)
                    changed = True
        components.append(tuple(component))

    def score(component: tuple[DrawingRegion, ...]) -> tuple[float, int, float]:
        rect = fitz.Rect(component[0].bbox)
        for drawing in component[1:]:
            rect |= fitz.Rect(drawing.bbox)
        return rect.get_area(), len(component), max(rect.width, rect.height)

    return max(components, key=score)


def _is_diagram_label(text: str) -> bool:
    if len(text) > 16 or text.endswith((".", "?", "!", "다", "요")):
        return False
    return " " not in text or any(character.isdigit() for character in text)


def select_vector_figure(page: fitz.Page, source_bbox: BBox) -> VectorSelection:
    """Select drawing geometry, connected short labels, captions, and prose separately."""
    source = fitz.Rect(source_bbox)
    source_drawings = tuple(
        DrawingRegion(_bbox(fitz.Rect(drawing["rect"]))) for drawing in page.get_drawings()
        if _touches(fitz.Rect(drawing["rect"]), source)
    )
    texts = _texts(page, source)
    captions = tuple(region for region in texts if _CAPTION_RE.fullmatch(region.text))
    if not source_drawings:
        return VectorSelection(_bbox(source), (), captions, tuple(
            region for region in texts if region not in captions
        ), 0, len(texts))
    drawings = _main_drawing_component(source_drawings)
    cluster = fitz.Rect(drawings[0].bbox)
    for drawing in drawings[1:]:
        cluster |= fitz.Rect(drawing.bbox)
    eligible = tuple(
        region for region in texts
        if region not in captions and _is_diagram_label(region.text)
    )
    protected: list[VectorText] = []
    pending = list(eligible)
    changed = True
    while changed:
        changed = False
        for region in tuple(pending):
            if _near(cluster, region):
                protected.append(region)
                pending.remove(region)
                cluster |= fitz.Rect(region.bbox)
                changed = True
    excluded = tuple(
        region for region in texts
        if region not in captions and region not in protected
    )
    return VectorSelection(
        _bbox(cluster),
        tuple(protected),
        captions,
        excluded,
        len(drawings),
        len(texts),
    )
