"""Build an editable HWP draft from positioned OCR while keeping only material art rasterized."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import fitz

from .export_palette import question_to_palette
from .pdf_hwp_figure_routing import route_figure
from .pdf_hwp_pipeline_models import (
    CropArtifact,
    DetectedItem,
    DraftArtifact,
    LayoutStyle,
    UnsupportedDraftLayoutError,
)
from .pdf_hwp_question_structure import palette_question
from .pdf_hwp_raster_choices import (
    CIRCLED_CHOICE_MARKERS,
    choice_markers,
    restore_bogi_choice_glyphs,
)
from .pdf_hwp_raster_formula import restore_raster_formulas
from .pdf_hwp_raster_ocr import RasterOcrWord, read_sidecar


def _rows(words: tuple[RasterOcrWord, ...]) -> list[list[RasterOcrWord]]:
    rows: list[list[RasterOcrWord]] = []
    for word in sorted(words, key=lambda value: ((value.bbox[1] + value.bbox[3]) / 2, value.bbox[0])):
        center = (word.bbox[1] + word.bbox[3]) / 2
        row = next((
            candidate for candidate in rows
            if abs(center - sum((item.bbox[1] + item.bbox[3]) / 2 for item in candidate) / len(candidate)) <= 5
        ), None)
        if row is None:
            rows.append([word])
        else:
            row.append(word)
    for row in rows:
        row.sort(key=lambda value: value.bbox[0])
    return rows


def _row_text(row: list[RasterOcrWord]) -> str:
    return " ".join(word.text.strip() for word in row if word.text.strip()).strip()


def _editable_variables(text: str) -> str:
    return restore_raster_formulas(text)


def _fraction(numerator: str, denominator: str) -> str:
    return f"[[formula:{{{numerator}}} over {{{denominator}}}]]"


def _ask_text(rows: list[list[RasterOcrWord]]) -> str:
    words = [word for row in rows for word in row]
    variables = [word for word in words if re.fullmatch(r"v[12]", word.text.strip())]
    if len(variables) >= 2:
        first, second = sorted(variables[:2], key=lambda word: word.bbox[1])
        if abs(first.bbox[0] - second.bbox[0]) <= 8 and second.bbox[1] > first.bbox[1]:
            remainder = " ".join(
                word.text for word in words if word not in {first, second}
            ).strip()
            return f"{_fraction('v_1', 'v_2')}{remainder}"
    return _editable_variables(" ".join(_row_text(row) for row in rows))


def _choice_texts(words: tuple[RasterOcrWord, ...], choice_top: float) -> tuple[str, ...]:
    markers = list(choice_markers(words))
    if len(markers) != 5:
        return ()
    groups: list[list[tuple[float, str]]] = [[] for _ in markers]
    centers = [(marker.bbox[0] + marker.bbox[2]) / 2 for marker in markers]
    for index, marker in enumerate(markers):
        remainder = re.sub(
            rf"^(?:[{CIRCLED_CHOICE_MARKERS}]|[1-5][.)]?)\s*", "", marker.text,
        ).strip()
        if remainder:
            groups[index].append((marker.bbox[1], remainder))
    for word in words:
        if word.bbox[1] < choice_top - 2 or word in markers:
            continue
        center = (word.bbox[0] + word.bbox[2]) / 2
        index = min(range(5), key=lambda value: abs(center - centers[value]))
        groups[index].append((word.bbox[1], word.text.strip()))
    choices: list[str] = []
    for group in groups:
        values = [text for _, text in sorted(group) if text]
        if len(values) == 2 and all(re.fullmatch(r"[\w.+-]+", value) for value in values):
            choices.append(_fraction(values[0], values[1]))
        else:
            choices.append(_editable_variables(" ".join(values)))
    return tuple(choices)


def _crop_raster_region(
    source_pdf: Path,
    item: DetectedItem,
    bbox: tuple[float, float, float, float],
    output_dir: Path,
) -> CropArtifact:
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / f"page-{item.page_number}-item-{item.item_number}-ocr-material.png"
    provenance_path = image_path.with_suffix(".json")
    with fitz.open(source_pdf) as document:
        pixmap = document[item.page_number - 1].get_pixmap(dpi=300, clip=fitz.Rect(bbox), alpha=False)
        pixmap.save(image_path)
    provenance_path.write_text(json.dumps({
        "asset_mode": "raster_ocr_material_crop",
        "source_pdf": str(source_pdf.resolve()),
        "page_number": item.page_number,
        "item_number": item.item_number,
        "bbox": list(bbox),
        "dpi": 300,
        "width_px": pixmap.width,
        "height_px": pixmap.height,
        "asset_hash": hashlib.sha256(image_path.read_bytes()).hexdigest(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return CropArtifact(image_path, provenance_path, pixmap.width, pixmap.height)


def build_raster_draft(
    source_pdf: Path,
    item: DetectedItem,
    output_dir: Path,
    source_image: CropArtifact,
    layout_style: LayoutStyle,
) -> DraftArtifact:
    words = tuple(
        word for word in read_sidecar(source_pdf)
        if word.bbox[1] >= item.bbox[1] and word.bbox[3] <= item.bbox[3] + 2
    )
    rows = _rows(words)
    marker_words = choice_markers(words)
    if len(marker_words) != 5:
        raise UnsupportedDraftLayoutError(
            item.page_number, item.item_number,
            "OCR could not verify all five editable choices", source_image,
        )
    choice_marker_top = min(
        word.bbox[1] for word in marker_words
    )
    choice_top = choice_marker_top - 12
    question_row = next((
        index for index, row in enumerate(rows)
        if min(word.bbox[1] for word in row) < choice_top and "?" in _row_text(row)
    ), None)
    if question_row is None:
        raise UnsupportedDraftLayoutError(
            item.page_number, item.item_number,
            "OCR could not verify an editable question prompt", source_image,
        )
    ask_start = question_row
    while ask_start > 0:
        preceding = rows[ask_start - 1]
        vertical_gap = min(word.bbox[1] for word in rows[ask_start]) - max(
            word.bbox[3] for word in preceding
        )
        preceding_text = re.sub(r"\s", "", _row_text(preceding))
        is_formula_lead = any(
            re.fullmatch(r"v[12]", word.text.strip()) for word in preceding
        )
        is_ask_lead = re.search(
            r"(?:이에대한|이에관한|설명으로|옳은|옳지않은|고른|구한|"
            r"값은|크기는|것만을|보기에서|평균힘|운동량|충격량)",
            preceding_text,
        ) is not None
        if vertical_gap > 12 or not (is_formula_lead or is_ask_lead):
            break
        ask_start -= 1
    prose_candidates = [
        index for index, row in enumerate(rows[:question_row])
        if len(re.sub(r"\s", "", _row_text(row))) >= 18
        and max(word.bbox[2] for word in row) - min(word.bbox[0] for word in row)
        >= (item.bbox[2] - item.bbox[0]) * 0.55
    ]
    passage_end = max(prose_candidates) if prose_candidates else max(0, question_row - 1)
    material_breaks = [
        index for index in range(question_row - 1)
        if min(word.bbox[1] for word in rows[index + 1])
        - max(word.bbox[3] for word in rows[index]) >= 14
    ]
    if material_breaks:
        passage_end = min(passage_end, material_breaks[0])
    passage_left = min(word.bbox[0] for row in rows[:passage_end + 1] for word in row)
    while passage_end + 1 < ask_start:
        continuation = rows[passage_end + 1]
        vertical_gap = min(word.bbox[1] for word in continuation) - max(
            word.bbox[3] for word in rows[passage_end]
        )
        continuation_text = _row_text(continuation)
        continuation_left = min(word.bbox[0] for word in continuation)
        if (
            vertical_gap > 12
            or continuation_left > passage_left + 20
            or not re.search(r"[가-힣.!?]", continuation_text)
        ):
            break
        passage_end += 1
    passage = " ".join(_row_text(row) for row in rows[:passage_end + 1])
    passage = re.sub(rf"^\s*{item.item_number}\s*\.\s*", "", passage).strip()
    passage = passage.replace(". A. B, C", ". A, B, C")
    passage = _editable_variables(passage)
    ask_rows = [
        row for row in rows[ask_start:]
        if min(word.bbox[1] for word in row) < choice_top - 2
    ]
    ask = _ask_text(ask_rows)
    choices = restore_bogi_choice_glyphs(_choice_texts(words, choice_top), ask)
    figure_top = max(word.bbox[3] for word in rows[passage_end]) + 3
    figure_bottom = min(word.bbox[1] for word in rows[ask_start]) - 8
    figure = None
    if figure_bottom - figure_top >= 18:
        figure_words = [
            word for row in rows[passage_end + 1:ask_start] for word in row
        ]
        figure_left = (
            max(item.bbox[0], min(word.bbox[0] for word in figure_words) - 8)
            if figure_words else item.bbox[0]
        )
        figure_right = (
            min(item.bbox[2], max(word.bbox[2] for word in figure_words) + 8)
            if figure_words else item.bbox[2]
        )
        figure = _crop_raster_region(
            source_pdf,
            item,
            (figure_left, figure_top, figure_right, figure_bottom),
            output_dir,
        )
    routed = route_figure(passage, figure) if figure is not None else None
    assets = routed.assets if routed is not None else ()
    question = palette_question(
        passage,
        ask,
        ",".join(asset.image_path.name for asset in assets),
        routed.layout if routed is not None else None,
    )
    markdown = question_to_palette(
        question,
        [{"ord": index + 1, "text": text} for index, text in enumerate(choices)],
        num=item.item_number,
        layout_style=layout_style.value,
    )
    warnings = tuple(
        f"low-confidence OCR: {word.text}"
        for word in words if word.confidence < 0.75
    )
    return DraftArtifact(
        item.item_number,
        markdown,
        f"{passage}\n\n{ask}",
        choices,
        source_image,
        figure,
        warnings,
        assets,
        (),
    )
