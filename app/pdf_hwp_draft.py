"""Deterministic PDF text-layer to editable HwpPalette draft conversion."""
# noqa: SIZE_OK — existing ordered layout parser; split only under a dedicated behavior-preserving change.
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Final

import fitz

from .export_palette import question_to_palette
from .formula_markup import to_hwppalette_markup
from .pdf_hwp_choice_text import CIRCLED as _CIRCLED, choice_texts as _choice_texts
from .pdf_hwp_crop_assets import crop_region
from .pdf_hwp_equation_font import verify_equation_context
from .pdf_hwp_equation_layout import (
    join_rows as _join_rows,
    normalize_equations as _normalize_fractions,
    rows as _rows,
)
from .pdf_hwp_equation_types import (
    EquationDecoder as _EquationDecoder,
    EquationGlyph as _Glyph,
    EquationWord as _Word,
    page_words as _page_words,
    pua_font_names as _pua_font_names,
)
from .pdf_hwp_figure_routing import route_figure
from .pdf_hwp_object_segmentation import EmptyFigureSelectionError
from .pdf_hwp_question_structure import palette_question
from .pdf_hwp_pipeline_models import (
    CropArtifact,
    DetectedItem,
    DraftArtifact,
    LayoutStyle,
    UnsupportedDraftLayoutError,
)


def _graphical_choice_bboxes(
    page: fitz.Page,
    item: DetectedItem,
    words: list[_Word],
    choice_start: float,
) -> tuple[tuple[float, float, float, float], ...]:
    """Pair five circled markers with five raster choices in marker order."""
    marker_words: list[_Word] = []
    for marker in _CIRCLED:
        matches = [word for word in words if marker in word.text]
        if len(matches) != 1:
            return ()
        marker_words.append(matches[0])

    bounds = fitz.Rect(item.bbox)
    candidates = [
        fitz.Rect(info["bbox"]) & bounds
        for info in page.get_image_info()
        if not (fitz.Rect(info["bbox"]) & bounds).is_empty
        and (fitz.Rect(info["bbox"]) & bounds).y0 >= choice_start - 8
    ]
    if len(candidates) != len(_CIRCLED):
        return ()

    ordered: list[fitz.Rect] = []
    for marker_word in marker_words:
        marker = fitz.Rect(marker_word.bbox)
        matches = [
            box for box in candidates
            if box not in ordered
            and abs(box.y0 - marker.y0) <= 12
            and box.x0 >= marker.x1 - 2
        ]
        if not matches:
            return ()
        ordered.append(min(matches, key=lambda box: box.x0 - marker.x1))
    return tuple(tuple(float(value) for value in box) for box in ordered)


def _crop_graphical_choices(
    source_pdf: Path,
    item: DetectedItem,
    bboxes: tuple[tuple[float, float, float, float], ...],
    output_dir: Path,
) -> tuple[CropArtifact, ...]:
    assets: list[CropArtifact] = []
    for choice_index, bbox in enumerate(bboxes, 1):
        asset = crop_region(source_pdf, item, bbox, output_dir, f"choice-{choice_index}")
        payload = json.loads(asset.provenance_path.read_text(encoding="utf-8"))
        payload.update(choice_index=choice_index, asset_count=len(bboxes), confidence=1.0)
        asset.provenance_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        assets.append(asset)
    return tuple(assets)


def _graphical_choice_markdown(
    item_number: int,
    passage: str,
    ask: str,
    prompt: CropArtifact,
    choices: tuple[CropArtifact, ...],
) -> str:
    return "\n".join((
        "\\수능정답1대사진그림5선지\\",
        str(item_number),
        to_hwppalette_markup(passage),
        f"\\{prompt.image_path.stem}\\",
        to_hwppalette_markup(ask),
        *(f"\\{asset.image_path.stem}\\" for asset in choices),
    ))


def _embedded_figure_bbox(
    page: fitz.Page,
    item: DetectedItem,
    choice_start: float,
    excluded_bboxes: tuple[tuple[float, float, float, float], ...] = (),
) -> tuple[float, float, float, float] | None:
    bounds = fitz.Rect(item.bbox)
    excluded = tuple(fitz.Rect(bbox) for bbox in excluded_bboxes)
    image_boxes = [
        fitz.Rect(info["bbox"]) & bounds
        for info in page.get_image_info()
        if not (fitz.Rect(info["bbox"]) & bounds).is_empty
        and (fitz.Rect(info["bbox"]) & bounds).y0 < choice_start
        and not any(fitz.Rect(info["bbox"]) == box for box in excluded)
    ]
    if not image_boxes:
        return None
    figure = image_boxes[0]
    for box in image_boxes[1:]:
        figure |= box
    padding = 6
    return (
        max(bounds.x0, figure.x0 - padding),
        max(bounds.y0, figure.y0 - padding),
        min(bounds.x1, figure.x1 + padding),
        min(choice_start - padding, figure.y1 + padding),
    )


def _proven_rasterized_text_bbox(
    figure: CropArtifact,
    item: DetectedItem,
    source_image: CropArtifact,
) -> tuple[float, float, float, float] | None:
    payload = json.loads(figure.provenance_path.read_text(encoding="utf-8"))
    if payload.get("asset_mode") != "pdf_figure_mixed_region_crop_hd":
        return None
    bbox = payload.get("bbox")
    proof_complete = (
        payload.get("segmentation_method") == "mixed_text_vector_region_v1"
        and payload.get("manual_review_required") is False
        and bool(payload.get("protected_texts"))
        and isinstance(bbox, list)
        and len(bbox) == 4
    )
    if not proof_complete:
        raise UnsupportedDraftLayoutError(
            item.page_number,
            item.item_number,
            "ambiguous mixed text/vector prompt region requires manual review",
            source_image,
        )
    return tuple(float(value) for value in bbox)


def _outside_rasterized_text(
    word: _Word,
    rasterized_text_bbox: tuple[float, float, float, float] | None,
) -> bool:
    if rasterized_text_bbox is None:
        return True
    return not fitz.Rect(rasterized_text_bbox).contains(fitz.Rect(word.bbox))


def _crop_prompt_region(
    source_pdf: Path,
    item: DetectedItem,
    bbox: tuple[float, float, float, float],
    output_dir: Path,
    source_image: CropArtifact,
) -> CropArtifact:
    """Keep a dense mixed text/vector prompt region whole when object selection is partial."""
    artifact = crop_region(source_pdf, item, bbox, output_dir, "figure")
    payload = json.loads(artifact.provenance_path.read_text(encoding="utf-8"))
    source = fitz.Rect(bbox)
    selected = fitz.Rect(payload["bbox"])
    excluded = tuple(
        fitz.Rect(region["bbox"])
        for region in payload.get("excluded_body_spans", ())
        if not (fitz.Rect(region["bbox"]) & source).is_empty
    )
    if len(excluded) < 8 or selected.width >= source.width * 0.5:
        return artifact
    excluded_union = excluded[0]
    for region in excluded[1:]:
        excluded_union |= region
    same_band = excluded_union.y0 < selected.y1 and selected.y0 < excluded_union.y1
    complete_horizontal_group = excluded_union.width >= source.width * 0.55 and same_band
    if not complete_horizontal_group:
        raise UnsupportedDraftLayoutError(
            item.page_number,
            item.item_number,
            "ambiguous mixed text/vector prompt region requires manual review",
            source_image,
        )
    with fitz.open(source_pdf) as document:
        pixmap = document[item.page_number - 1].get_pixmap(
            dpi=300, clip=source, alpha=False,
        )
        pixmap.save(artifact.image_path)
    payload.update({
        "asset_mode": "pdf_figure_mixed_region_crop_hd",
        "source_bbox": list(bbox),
        "bbox": list(bbox),
        "width_px": pixmap.width,
        "height_px": pixmap.height,
        "asset_hash": hashlib.sha256(artifact.image_path.read_bytes()).hexdigest(),
        "segmentation_method": "mixed_text_vector_region_v1",
        "layout_axis": "single",
        "panel_bboxes": [list(bbox)],
        "protected_texts": [
            *(str(text) for text in payload.get("protected_texts", ())),
            *(str(region["text"]) for region in payload.get("excluded_body_spans", ())),
        ],
        "excluded_texts": [],
        "excluded_body_spans": [],
        "manual_review_required": False,
        "review_reasons": [],
    })
    artifact.provenance_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return CropArtifact(
        artifact.image_path, artifact.provenance_path, pixmap.width, pixmap.height,
    )


def build_draft(source_pdf: Path, item: DetectedItem, output_dir: Path, source_image: CropArtifact, layout_style: LayoutStyle) -> DraftArtifact:
    """Build one deterministic editable draft from a detected source item."""
    with fitz.open(source_pdf) as document:
        page = document[item.page_number - 1]
        raw_words = _page_words(page, item.bbox)
        font_context = verify_equation_context(document, page, item.bbox)
        preliminary_words = _normalize_fractions(raw_words, _EquationDecoder(font_context))
        choice_start = next(
            word.bbox[1] for word in preliminary_words
            if any(marker in word.text for marker in _CIRCLED)
        )
        choices = _choice_texts(preliminary_words, choice_start)
        graphical_choice_bboxes: tuple[tuple[float, float, float, float], ...] = ()
        if len(choices) != 5 or any(not choice for choice in choices):
            graphical_choice_bboxes = _graphical_choice_bboxes(
                page, item, preliminary_words, choice_start,
            )
            if len(graphical_choice_bboxes) != 5:
                raise UnsupportedDraftLayoutError(
                    item.page_number,
                    item.item_number,
                    "graphical answer choices require manual review",
                    source_image,
                )
            choices = ()
        image_bbox = _embedded_figure_bbox(
            page, item, choice_start, graphical_choice_bboxes,
        )
        preliminary_rows = _rows(preliminary_words)
        preliminary_choice_index = next(
            index for index, row in enumerate(preliminary_rows)
            if any(any(marker in word.text for marker in _CIRCLED) for word in row)
        )
        prompt_gaps = [
            (
                preliminary_rows[index + 1][0].bbox[1]
                - max(word.bbox[3] for word in preliminary_rows[index]),
                index,
            )
            for index in range(preliminary_choice_index - 1)
        ]
        _, separated_passage_end = max(prompt_gaps)
        preliminary_question_index = next(
            index for index, row in enumerate(preliminary_rows[:preliminary_choice_index])
            if any("?" in word.text for word in row)
        )
        if graphical_choice_bboxes and image_bbox is not None:
            preliminary_ask_index = next(
                index for index, row in enumerate(preliminary_rows[:preliminary_choice_index])
                if row[0].bbox[1] > image_bbox[3]
            )
        else:
            preliminary_ask_index = preliminary_question_index
        passage_end_index = (
            preliminary_question_index - 1 if image_bbox is not None else separated_passage_end
        )
        whitespace_bbox = (
            item.bbox[0] + 10,
            max(word.bbox[3] for word in preliminary_rows[passage_end_index]) + 6,
            item.bbox[2] - 10,
            preliminary_rows[preliminary_ask_index][0].bbox[1] - 6,
        )
        figure_bbox = image_bbox or whitespace_bbox
        if figure_bbox[3] <= figure_bbox[1]:
            figure_bbox = (
                item.bbox[0] + 10,
                preliminary_rows[0][0].bbox[1],
                item.bbox[2] - 10,
                preliminary_rows[preliminary_ask_index][0].bbox[1] - 6,
            )
        try:
            figure = _crop_prompt_region(
                source_pdf, item, figure_bbox, output_dir, source_image,
            )
        except EmptyFigureSelectionError:
            figure = None
        rasterized_text_bbox = (
            _proven_rasterized_text_bbox(figure, item, source_image)
            if figure is not None else None
        )
        decoder = _EquationDecoder(font_context)
        words = _normalize_fractions(
            [
                word for word in raw_words
                if _outside_rasterized_text(word, rasterized_text_bbox)
            ],
            decoder,
        )
        rows = _rows(words, decoder)
        if decoder.unknown:
            glyphs = ", ".join(sorted(decoder.unknown))
            raise UnsupportedDraftLayoutError(
                item.page_number,
                item.item_number,
                f"unverified equation glyphs require manual review: {glyphs}",
                source_image,
            )
        choice_row_index = next(
            index for index, row in enumerate(rows)
            if any(any(marker in word.text for marker in _CIRCLED) for word in row)
        )
        choices = () if graphical_choice_bboxes else _choice_texts(words, choice_start)
    question_row_index = next(
        index for index, row in enumerate(rows[:choice_row_index])
        if any("?" in word.text for word in row)
    )
    if graphical_choice_bboxes and image_bbox is not None:
        ask_start_index = next(
            index for index, row in enumerate(rows[:choice_row_index])
            if row[0].bbox[1] > image_bbox[3]
        )
    else:
        ask_start_index = question_row_index
    passage = re.sub(rf"^{item.item_number}\s*\.\s*", "", _join_rows(rows[: ask_start_index]))
    ask = _join_rows(rows[ask_start_index:choice_row_index])
    routed = route_figure(passage, figure) if figure is not None else None
    graphical_choice_assets = _crop_graphical_choices(
        source_pdf, item, graphical_choice_bboxes, output_dir,
    )
    routed_assets = routed.assets if routed is not None else ()
    material = ",".join(asset.image_path.name for asset in routed_assets)
    question = palette_question(
        passage, ask, material, routed.layout if routed is not None else None,
    )
    if graphical_choice_assets:
        if len(routed_assets) != 1:
            raise UnsupportedDraftLayoutError(
                item.page_number,
                item.item_number,
                "graphical answer choices require one prompt figure",
                source_image,
            )
        markdown = _graphical_choice_markdown(
            item.item_number, passage, ask, routed_assets[0], graphical_choice_assets,
        )
    else:
        choice_rows = [{"ord": index + 1, "text": text} for index, text in enumerate(choices)]
        markdown = question_to_palette(
            question, choice_rows, num=item.item_number, layout_style=layout_style.value,
        )
    raw_pua = sorted({
        f"U+{ord(char):04X}"
        for value in (passage, ask, *choices, markdown)
        for char in value
        if 0xE000 <= ord(char) <= 0xF8FF
    })
    if raw_pua:
        raise UnsupportedDraftLayoutError(
            item.page_number,
            item.item_number,
            f"unverified equation glyphs require manual review: {', '.join(raw_pua)}",
            source_image,
        )
    warnings = tuple(f"unmapped equation glyph {code}" for code in sorted(decoder.unknown))
    return DraftArtifact(
        item.item_number, markdown, f"{passage}\n\n{ask}", choices,
        source_image, figure, warnings, routed_assets, graphical_choice_assets,
    )
