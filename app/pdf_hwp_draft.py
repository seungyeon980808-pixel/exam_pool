"""Deterministic PDF text-layer to editable HwpPalette draft conversion."""
# noqa: SIZE_OK — existing ordered layout parser; split only under a dedicated behavior-preserving change.
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from dataclasses import replace
from typing import Final

import fitz
from PIL import Image

from .export_palette import question_to_palette
from .formula_markup import to_hwppalette_markup
from .pdf_hwp_choice_text import CIRCLED as _CIRCLED, choice_texts as _choice_texts
from .pdf_hwp_crop_assets import crop_region
from .pdf_hwp_equation_font import verify_equation_context
from .pdf_hwp_equation_fraction import bind_drawn_fractions
from .pdf_hwp_equation_layout import (
    join_rows as _join_rows,
    normalize_equations as _normalize_fractions,
    rows as _rows,
)
from .pdf_hwp_equation_radical import bind_drawn_inline_radicals
from .pdf_hwp_equation_types import (
    EquationDecoder as _EquationDecoder,
    EquationGlyph as _Glyph,
    EquationWord as _Word,
    page_words as _page_words,
    pua_font_names as _pua_font_names,
)
from .pdf_hwp_ebs_text import parse_ebs_text_slots, rejoin_ebs_soft_wraps
from .pdf_hwp_figure_routing import (
    route_figure, route_single_composite, stamp_single_prompt_figure,
)
from .pdf_hwp_experiment import (
    has_editable_experiment_region,
    insert_result_tables,
    is_experiment_text,
    recover_bulleted_box,
    recover_experiment_tables,
    recover_ruled_tables,
    without_table_words,
)
from .pdf_hwp_object_segmentation import EmptyFigureSelectionError
from .pdf_hwp_raster_caption_segmentation import expected_panel_labels, panel_label_token
from .pdf_hwp_question_structure import palette_question
from .pdf_hwp_pipeline_models import (
    CropArtifact,
    DetectedItem,
    DraftArtifact,
    LayoutStyle,
    UnsupportedDraftLayoutError,
)


_EBS_SOURCE_TOKEN: Final = re.compile(r"\[26023-\d{4}\]")
_VERTICAL_SUBJECT_HEADERS: Final = ("물리", "화학", "생명과학", "지구과학")
_LEGACY_ISOTOPE = re.compile(r"⁄`›(?P<atomic>[§¶])(?P<element>[A-Z])")
_LEGACY_ATOMIC_NUMBER: Final = {"§": "6", "¶": "7"}
_CLAIM_MARKER: Final = re.compile(r"^[ㄱㄴㄷ]\.$")


def _normalize_legacy_isotopes(text: str) -> str:
    """Decode the measured old-PDF isotope glyph sequence as editable math."""
    return _LEGACY_ISOTOPE.sub(
        lambda match: (
            "[[formula:{}_{" + _LEGACY_ATOMIC_NUMBER[match.group("atomic")]
            + "}^{14}" + match.group("element") + "]]"
        ),
        text,
    )


def _align_plain_claim_words(words: list[_Word]) -> list[_Word]:
    """Align a claim that geometrically belongs to a nearby ㄱ/ㄴ/ㄷ marker.

    Old two-column PDFs can place the marker about one point below its claim.
    The greedy row builder then joins the claim to the preceding continuation.
    Formula and PUA words are intentionally excluded so equation ownership is
    still handled by the stricter equation-row contract.
    """
    markers = tuple(word for word in words if _CLAIM_MARKER.fullmatch(word.text))
    if not markers:
        return words
    aligned: list[_Word] = []
    for word in words:
        if (
            _CLAIM_MARKER.fullmatch(word.text)
            or word.text.startswith("[[formula:")
            or re.search(r"[\ue000-\uf8ff]", word.raw)
        ):
            aligned.append(word)
            continue
        candidates = tuple(
            marker for marker in markers
            if word.bbox[0] >= marker.bbox[2] - 1
            and 0 < marker.bbox[1] - word.bbox[1] <= 3
            and min(word.bbox[3], marker.bbox[3]) > max(word.bbox[1], marker.bbox[1])
        )
        aligned.append(
            replace(word, bbox=(word.bbox[0], candidates[0].bbox[1], word.bbox[2], word.bbox[3]))
            if len(candidates) == 1 else word
        )
    return aligned


def _without_vertical_subject_header(
    words: list[_Word], item: DetectedItem,
) -> list[_Word]:
    """Drop a proven one-glyph-per-line subject tab at the outer page edge."""
    edge = item.bbox[0] + (item.bbox[2] - item.bbox[0]) * 0.8
    candidates = [
        word for word in words
        if word.bbox[0] >= edge and len(word.text.strip()) == 1
    ]
    groups: list[list[_Word]] = []
    for word in sorted(candidates, key=lambda value: value.bbox[0]):
        center = (word.bbox[0] + word.bbox[2]) / 2
        group = next((
            value for value in groups
            if abs(center - sum((member.bbox[0] + member.bbox[2]) / 2 for member in value)
                   / len(value)) <= 8
        ), None)
        if group is None:
            groups.append([word])
        else:
            group.append(word)
    excluded: set[int] = set()
    for group in groups:
        ordered = sorted(group, key=lambda value: value.bbox[1])
        label = "".join(word.text.strip() for word in ordered)
        if any(label.startswith(subject) for subject in _VERTICAL_SUBJECT_HEADERS):
            excluded.update(id(word) for word in ordered)
    return [word for word in words if id(word) not in excluded]


def _ebs_student_dialogue(
    words: list[_Word], source_text: str,
) -> tuple[tuple[str, str], ...]:
    """Recover three speech columns as editable rows instead of a mixed crop."""
    if re.search(r"학생\s*A,\s*B,\s*C가\s*대화", source_text) is None:
        return ()
    labels: dict[str, _Word] = {}
    student_words = [word for word in words if word.text.strip() == "학생"]
    for label in "ABC":
        candidates = [
            word for word in words
            if word.text.strip() == label
            and any(
                abs(word.bbox[1] - student.bbox[1]) <= 3
                and 0 <= word.bbox[0] - student.bbox[2] <= 20
                for student in student_words
            )
        ]
        if len(candidates) != 1:
            return ()
        labels[label] = candidates[0]
    label_top = min(word.bbox[1] for word in labels.values())
    intro = next((word for word in words if word.text.strip().endswith("것이다.")), None)
    if intro is None:
        return ()
    centers = {
        label: (word.bbox[0] + word.bbox[2]) / 2 for label, word in labels.items()
    }
    source_lines = [line for line in source_text.splitlines() if line.strip()]
    cleaned_lines = [line.strip() for line in source_lines]
    label_start = next((
        index for index in range(len(cleaned_lines) - 2)
        if cleaned_lines[index:index + 3] == ["학생 A", "학생 B", "학생 C"]
    ), None)
    if label_start is None:
        return ()
    chunks: list[list[str]] = []
    chunk: list[str] = []
    for line in source_lines[label_start + 3:]:
        chunk.append(line)
        if line.rstrip().endswith("."):
            chunks.append(chunk)
            chunk = []
    if chunk or len(chunks) != 3:
        return ()
    recovered: dict[str, str] = {}
    for lines in chunks:
        first = lines[0].split()[0]
        candidates = [
            word for word in words
            if word.text.strip() == first
            and word.bbox[1] > intro.bbox[3]
            and word.bbox[3] < label_top - 2
        ]
        if len(candidates) != 1:
            return ()
        center = (candidates[0].bbox[0] + candidates[0].bbox[2]) / 2
        label = min(centers, key=lambda value: abs(center - centers[value]))
        recovered[label] = rejoin_ebs_soft_wraps(
            " ".join(line.strip() for line in lines), "\n".join(lines),
        )
    return tuple((label, recovered[label]) for label in "ABC") if len(recovered) == 3 else ()


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
        and (fitz.Rect(info["bbox"]) & bounds).y0 >= choice_start - 20
    ]
    grouped = _group_choice_images(marker_words, candidates, bounds)
    if grouped:
        return grouped
    return _drawing_choice_bboxes(page, item, marker_words, choice_start)


def _marker_owns_box(marker: fitz.Rect, box: fitz.Rect, later_x: float) -> bool:
    return (
        box.y0 < marker.y1 + 2
        and box.y1 > marker.y0 - 2
        and marker.x1 - 2 <= box.x0 < later_x
    )


def _group_choice_images(
    marker_words: list[_Word],
    candidates: list[fitz.Rect],
    bounds: fitz.Rect,
) -> tuple[tuple[float, float, float, float], ...]:
    if not candidates:
        return ()
    remaining = list(candidates)
    groups: list[list[fitz.Rect]] = []
    for marker_index, marker_word in enumerate(marker_words):
        marker = fitz.Rect(marker_word.bbox)
        later_x = min(
            (
                other.bbox[0]
                for other in marker_words[marker_index + 1:]
                if other.bbox[1] < marker.y1 + 2
                and other.bbox[3] > marker.y0 - 2
                and other.bbox[0] > marker.x1
            ),
            default=bounds.x1 + 1,
        )
        owned = [box for box in remaining if _marker_owns_box(marker, box, later_x)]
        if not owned:
            return ()
        groups.append(owned)
        remaining = [box for box in remaining if box not in owned]
    counts = {len(group) for group in groups}
    if remaining or len(counts) != 1:
        return ()
    ordered: list[fitz.Rect] = []
    for owned in groups:
        union = owned[0]
        for box in owned[1:]:
            union |= box
        ordered.append(union)
    return tuple(tuple(float(value) for value in box) for box in ordered)


def _drawing_choice_bboxes(
    page: fitz.Page,
    item: DetectedItem,
    marker_words: list[_Word],
    choice_start: float,
) -> tuple[tuple[float, float, float, float], ...]:
    """Crop five stacked or grid-arranged vector choices when no images exist."""
    marker_xs = tuple(word.bbox[0] for word in marker_words)
    marker_ys = tuple(word.bbox[1] for word in marker_words)
    gaps = tuple(
        right - left for left, right in zip(marker_ys, marker_ys[1:])
    )
    bounds = fitz.Rect(item.bbox)
    marker_right = max(word.bbox[2] for word in marker_words)
    all_drawings = [
        fitz.Rect(info["rect"]) & bounds
        for info in page.get_drawings()
        if not (fitz.Rect(info["rect"]) & bounds).is_empty
        and (fitz.Rect(info["rect"]) & bounds).y0 >= choice_start - 8
    ]
    stacked = (
        max(marker_xs) - min(marker_xs) <= 8
        and len(gaps) == 4
        and min(gaps) >= 12
        and max(gaps) <= 36
        and max(gaps) - min(gaps) <= 8
    )
    if stacked:
        row_gap = sum(gaps) / len(gaps)
        drawings = [
            box for box in all_drawings
            if box.x0 >= marker_right - 2 and box.height <= row_gap * 1.5
        ]
        bands: list[tuple[float, float]] = []
        for index, marker in enumerate(marker_words):
            top = marker.bbox[1] - 2 if index == 0 else (marker_words[index - 1].bbox[3] + marker.bbox[1]) / 2
            bottom = (
                marker.bbox[3] + 2
                if index == len(marker_words) - 1
                else (marker.bbox[3] + marker_words[index + 1].bbox[1]) / 2
            )
            bands.append((top, bottom))
        row_unions: list[fitz.Rect] = []
        for top, bottom in bands:
            matches = [box for box in drawings if box.y0 < bottom and box.y1 > top]
            if len(matches) < 2:
                break
            union = matches[0]
            for box in matches[1:]:
                union |= box
            if union.width < 40:
                break
            row_unions.append(union)
        if len(row_unions) == 5:
            left = min(box.x0 for box in row_unions) - 2
            right = max(box.x1 for box in row_unions) + 4
            return tuple(
                (float(left), float(top), float(right), float(bottom))
                for (top, bottom), _union in zip(bands, row_unions)
            )

    spatial = sorted(marker_words, key=lambda word: (word.bbox[1], word.bbox[0]))
    marker_rows: list[list[_Word]] = []
    for marker in spatial:
        if not marker_rows or marker.bbox[1] - marker_rows[-1][0].bbox[1] > 8:
            marker_rows.append([marker])
        else:
            marker_rows[-1].append(marker)
    by_marker: dict[str, tuple[float, float, float, float]] = {}
    for row_index, row in enumerate(marker_rows):
        top = min(marker.bbox[1] for marker in row) - 2
        next_top = (
            min(marker.bbox[1] for marker in marker_rows[row_index + 1]) - 1
            if row_index + 1 < len(marker_rows)
            else bounds.y1
        )
        for column_index, marker in enumerate(row):
            left = marker.bbox[2] + 2
            right = (
                row[column_index + 1].bbox[0] - 2
                if column_index + 1 < len(row)
                else bounds.x1 - 4
            )
            cell = fitz.Rect(left, top, right, next_top)
            matches = [
                box for box in all_drawings
                if not (box & cell).is_empty
                and (
                    cell.contains(box)
                    or (box.get_area() > 0 and (box & cell).get_area() >= box.get_area() * 0.5)
                )
            ]
            if len(matches) < 2:
                return ()
            union = matches[0]
            for box in matches[1:]:
                union |= box
            if union.width < 40 or union.height < 20:
                return ()
            bottom = min(next_top, max(marker.bbox[3] + 8, union.y1 + 8))
            by_marker[marker.text] = (float(left), float(top), float(right), float(bottom))
    return tuple(by_marker.get(marker.text, ()) for marker in marker_words)


def _choice_bboxes_include_images(
    source_pdf: Path,
    item: DetectedItem,
    bboxes: tuple[tuple[float, float, float, float], ...],
) -> bool:
    clips = tuple(fitz.Rect(bbox) for bbox in bboxes)
    with fitz.open(source_pdf) as document:
        page = document[item.page_number - 1]
        return any(
            any(not (fitz.Rect(info["bbox"]) & clip).is_empty for clip in clips)
            for info in page.get_image_info()
        )


def _crop_exact_choice_region(
    source_pdf: Path,
    item: DetectedItem,
    bbox: tuple[float, float, float, float],
    output_dir: Path,
    label: str,
) -> CropArtifact:
    """Rasterize one stacked vector-choice row without collapsing to a single stroke."""
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / f"page-{item.page_number}-item-{item.item_number}-{label}.png"
    provenance_path = image_path.with_suffix(".json")
    source = fitz.Rect(bbox)
    with fitz.open(source_pdf) as document:
        pixmap = document[item.page_number - 1].get_pixmap(dpi=300, clip=source, alpha=False)
        pixmap.save(image_path)
    payload = {
        "asset_mode": "pdf_exact_choice_row_crop_hd",
        "source_pdf": str(source_pdf.resolve()),
        "source_hash": hashlib.sha256(source_pdf.read_bytes()).hexdigest(),
        "page_number": item.page_number,
        "item_number": item.item_number,
        "source_bbox": list(bbox),
        "bbox": list(bbox),
        "dpi": 300,
        "width_px": pixmap.width,
        "height_px": pixmap.height,
        "asset_hash": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        "segmentation_method": "stacked_vector_choice_row_v1",
        "layout_axis": "single",
        "panel_bboxes": [list(bbox)],
        "component_count": 1,
        "drawing_count": 1,
        "image_count": 0,
        "text_span_count": 0,
        "protected_texts": [],
        "excluded_texts": [],
        "excluded_body_spans": [],
        "caption_candidates": [],
        "caption_detection_source": "none",
        "caption_text_source": "none",
        "manual_review_required": False,
        "review_reasons": [],
    }
    provenance_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return CropArtifact(image_path, provenance_path, pixmap.width, pixmap.height)


def _crop_graphical_choices(
    source_pdf: Path,
    item: DetectedItem,
    bboxes: tuple[tuple[float, float, float, float], ...],
    output_dir: Path,
) -> tuple[CropArtifact, ...]:
    assets: list[CropArtifact] = []
    exact = bool(bboxes) and not _choice_bboxes_include_images(source_pdf, item, bboxes)
    for choice_index, bbox in enumerate(bboxes, 1):
        label = f"choice-{choice_index}"
        asset = (
            _crop_exact_choice_region(source_pdf, item, bbox, output_dir, label)
            if exact
            else crop_region(source_pdf, item, bbox, output_dir, label)
        )
        payload = json.loads(asset.provenance_path.read_text(encoding="utf-8"))
        payload.update(
            choice_index=choice_index,
            asset_count=len(bboxes),
            confidence=1.0,
            manual_review_required=False,
            review_reasons=[],
        )
        asset.provenance_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        assets.append(asset)
    return tuple(assets)


def _markdown_slot(text: str) -> str:
    value = text.strip()
    if not value:
        return "-"
    return "{" + value + "}" if "\n" in value else value


def _graphical_choice_markdown(
    item_number: int,
    passage: str,
    ask: str,
    prompt: CropArtifact | None,
    choices: tuple[CropArtifact, ...],
) -> str:
    label = "수능정답1대사진그림5선지" if prompt is not None else "수능정답0사진그림5선지"
    prompt_slot = (f"\\{prompt.image_path.stem}\\",) if prompt is not None else ()
    return "\n".join((
        f"\\{label}\\",
        str(item_number),
        _markdown_slot(to_hwppalette_markup(passage)),
        *prompt_slot,
        _markdown_slot(to_hwppalette_markup(ask)),
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
    image_boxes = []
    for info in page.get_image_info():
        clipped = fitz.Rect(info["bbox"]) & bounds
        if clipped.is_empty:
            continue
        if excluded and any(not (clipped & box).is_empty for box in excluded):
            continue
        if not excluded and clipped.y0 >= choice_start:
            continue
        image_boxes.append(clipped)
    if not image_boxes:
        return None
    figure = image_boxes[0]
    for box in image_boxes[1:]:
        figure |= box
    vertical_margin = max(12.0, figure.height * 1.5)
    vertical_band = fitz.Rect(
        bounds.x0,
        max(bounds.y0, figure.y0 - vertical_margin),
        bounds.x1,
        min(choice_start, figure.y1 + vertical_margin),
    )
    aligned_drawings: list[fitz.Rect] = []
    for drawing in page.get_drawings():
        drawing_rect = fitz.Rect(drawing["rect"])
        if (
            drawing_rect.width >= bounds.width * 0.9
            or drawing_rect.height >= bounds.height * 0.5
        ):
            continue
        clipped = drawing_rect & vertical_band
        if not clipped.is_empty:
            aligned_drawings.append(clipped)
    for box in aligned_drawings:
        figure |= box
    padding = 6
    caption_bottom: float | None = None
    caption_top: float | None = None
    labels = expected_panel_labels(item.source_text)
    caption_left: float | None = None
    if labels:
        label_words = [
            word for word in page.get_text("words", clip=bounds)
            if str(word[4]).strip() in labels
            and _word_is_panel_caption(word, image_boxes, choice_start)
        ]
        found = {
            panel_label_token(str(word[4]), labels)
            for word in label_words
        }
        if all(label in found for label in labels):
            caption_bottom = max(float(word[3]) for word in label_words) + 2
            caption_left = min(float(word[0]) for word in label_words)
            caption_top = min(float(word[1]) for word in label_words) - 2
    left = figure.x0 if caption_left is None else min(figure.x0, caption_left)
    top = figure.y0 if caption_top is None else min(figure.y0, caption_top)
    below_ask_ratio = max(0.0, figure.y1 - choice_start) / max(figure.height, 1.0)
    preserve_full_embedded_height = below_ask_ratio <= 0.65
    return (
        max(bounds.x0, left - padding),
        max(bounds.y0, top - padding),
        min(bounds.x1, figure.x1 + padding),
        min(
            bounds.y1 if preserve_full_embedded_height else choice_start - padding,
            max(figure.y1 + padding, caption_bottom or figure.y1),
        ),
    )


def _word_is_panel_caption(
    word: tuple,
    image_boxes: list[fitz.Rect],
    choice_start: float,
) -> bool:
    x0, y0, x1, y1 = (float(word[0]), float(word[1]), float(word[2]), float(word[3]))
    x_mid = (x0 + x1) / 2
    for box in image_boxes:
        aligned = box.x0 - 28 <= x_mid <= box.x1 + 8
        below = box.y1 - 2 <= y0 < min(choice_start, box.y1 + 28)
        above = box.y0 - 28 <= y1 <= box.y0 + 2
        if aligned and (below or above):
            return True
    return False


def _choice_content_boundary(rows: list[list[_Word]]) -> tuple[int, str]:
    """Return the first choice-content row and an optional repeated column header."""
    marker_index = next(
        index for index, row in enumerate(rows)
        if any(any(marker in word.text for marker in _CIRCLED) for word in row)
    )
    if marker_index == 0:
        return marker_index, ""
    preceding = tuple(word for word in rows[marker_index - 1] if not word.suppressed)
    marker_top = min(word.bbox[1] for word in rows[marker_index])
    if (
        len(preceding) >= 2
        and marker_top - min(word.bbox[1] for word in preceding) <= 12
        and all(
            word.text.startswith("[[formula:")
            or re.fullmatch(r"\d+(?:\.\d+)?", word.text.strip()) is not None
            for word in preceding
        )
    ):
        return marker_index - 1, ""
    labels = tuple(word.text.strip() for word in preceding)
    parenthetical = 2 <= len(labels) <= 4 and all(
        re.fullmatch(r"\([가-힣]\)", label) is not None for label in labels
    )
    half = len(labels) // 2
    repeated = len(labels) >= 4 and len(labels) % 2 == 0 and labels[:half] == labels[half:]
    if parenthetical or repeated:
        cells = labels if parenthetical else labels[:half]
        return marker_index - 1, "\t".join(cells)
    return marker_index, ""


def _proven_rasterized_text_bbox(
    figure: CropArtifact,
    item: DetectedItem,
    source_image: CropArtifact,
) -> tuple[float, float, float, float] | None:
    payload = json.loads(figure.provenance_path.read_text(encoding="utf-8"))
    asset_mode = payload.get("asset_mode")
    if (
        asset_mode == "pdf_exact_prose_free_mixed_region_crop_hd"
        and payload.get("segmentation_method") == "right_of_prose_mixed_region_v1"
        and payload.get("manual_review_required") is False
    ):
        return tuple(float(value) for value in payload["bbox"])
    if (
        asset_mode == "pdf_figure_mixed_region_crop_hd"
        and not payload.get("protected_texts")
        and int(payload.get("text_span_count") or 0) == 0
    ):
        return None
    vector_text_object = (
        asset_mode == "pdf_figure_object_crop_hd"
        and int(payload.get("drawing_count") or 0) > 0
        and int(payload.get("image_count") or 0) == 0
        and bool(payload.get("protected_texts"))
    )
    if asset_mode != "pdf_figure_mixed_region_crop_hd" and not vector_text_object:
        return None
    bbox = payload.get("bbox")
    proof_complete = (
        payload.get("segmentation_method") in {"mixed_text_vector_region_v1", "pdf_objects_v1"}
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


def _has_excluded_caption(figure: CropArtifact | None) -> bool:
    if figure is None:
        return False
    payload = json.loads(figure.provenance_path.read_text(encoding="utf-8"))
    return any(
        candidate.get("excluded") is True
        for candidate in payload.get("caption_candidates", ())
    )


def _outside_rasterized_text(
    word: _Word,
    rasterized_text_bboxes: tuple[tuple[float, float, float, float], ...],
) -> bool:
    return not any(
        not (fitz.Rect(bbox) & fitz.Rect(word.bbox)).is_empty
        for bbox in rasterized_text_bboxes
    )


def _ask_row_start(rows: list[list[_Word]], fallback: int) -> int:
    """Include a wrapped KICE question lead-in in the editable ask slot."""
    for index, row in enumerate(rows[: fallback + 1]):
        text = _join_rows([row]).strip()
        if re.search(r"(?:이에|이\s*.+?에|위\s*.+?에)\s*대한", text) and any(
            token in text for token in ("설명", "옳", "고른", "알맞")
        ):
            return index
    return fallback


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
    if (
        int(payload.get("image_count") or 0) > 0
        and int(payload.get("drawing_count") or 0) == 0
        and not payload.get("protected_texts")
    ):
        return artifact
    if (
        int(payload.get("image_count") or 0) == 0
        and (payload.get("protected_texts") or payload.get("excluded_body_spans"))
    ):
        with fitz.open(source_pdf) as document:
            clean_bbox = _prose_free_vector_bbox(
                document[item.page_number - 1], item, bbox,
            )
        if clean_bbox is not None:
            return _crop_exact_vector_region(
                source_pdf, item, clean_bbox, output_dir,
            )
    if (
        _EBS_SOURCE_TOKEN.search(item.source_text) is not None
        and int(payload.get("image_count") or 0) > 0
        and payload.get("protected_texts")
        and payload.get("excluded_body_spans")
    ):
        with fitz.open(source_pdf) as document:
            clean_bbox = _right_of_prose_mixed_bbox(
                document[item.page_number - 1], bbox,
                tuple(float(value) for value in payload["bbox"]),
            )
        if clean_bbox is not None:
            return _crop_exact_mixed_figure_region(
                source_pdf, item, clean_bbox, output_dir,
            )
    safe_ebs_vector = (
        int(payload.get("image_count") or 0) == 0
        and int(payload.get("drawing_count") or 0) > 0
        and selected.width >= source.width * 0.5
        and bool(payload.get("protected_texts"))
        and bool(payload.get("excluded_body_spans"))
    )
    force_source_region = (
        (
            _EBS_SOURCE_TOKEN.search(item.source_text) is not None
            and not safe_ebs_vector
        )
        or not source.contains(selected)
        or (
            int(payload.get("image_count") or 0) > 0
            and (
                selected.width < source.width * 0.75
                or selected.height < source.height * 0.75
            )
        )
    )
    if not force_source_region:
        if int(payload.get("image_count") or 0) > 0:
            return artifact
        if len(excluded) < 8 or selected.width >= source.width * 0.5:
            return artifact
    complete_horizontal_group = False
    if excluded:
        excluded_union = excluded[0]
        for region in excluded[1:]:
            excluded_union |= region
        same_band = excluded_union.y0 < selected.y1 and selected.y0 < excluded_union.y1
        complete_horizontal_group = excluded_union.width >= source.width * 0.55 and same_band
    vector_sliver = (
        int(payload.get("drawing_count") or 0) > 0
        and int(payload.get("image_count") or 0) == 0
        and selected.width < source.width * 0.4
    )
    if not force_source_region and not complete_horizontal_group and not vector_sliver:
        raise UnsupportedDraftLayoutError(
            item.page_number,
            item.item_number,
            "ambiguous mixed text/vector prompt region requires manual review",
            source_image,
        )
    with fitz.open(source_pdf) as document:
        page = document[item.page_number - 1]
        pixmap = page.get_pixmap(
            dpi=300, clip=source, alpha=False,
        )
        pixmap.save(artifact.image_path)
        source_words = [
            str(word[4])
            for word in page.get_text("words", clip=source)
            if str(word[4]).strip()
        ]
    if (
        not source_words
        and int(payload.get("drawing_count") or 0) == 0
        and int(payload.get("image_count") or 0) == 0
        and int(payload.get("text_span_count") or 0) == 0
    ):
        raise EmptyFigureSelectionError(
            tuple(float(value) for value in bbox),
            tuple(float(value) for value in payload["bbox"]),
        )
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
            *source_words,
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


def _crop_exact_data_region(
    source_pdf: Path,
    item: DetectedItem,
    bbox: tuple[float, float, float, float],
    output_dir: Path,
) -> CropArtifact:
    """Rasterize a proven diagram/table region without shrinking surrounding prose."""
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / f"page-{item.page_number}-item-{item.item_number}-figure.png"
    provenance_path = image_path.with_suffix(".json")
    source = fitz.Rect(bbox)
    with fitz.open(source_pdf) as document:
        pixmap = document[item.page_number - 1].get_pixmap(dpi=300, clip=source, alpha=False)
        pixmap.save(image_path)
    payload = {
        "asset_mode": "pdf_exact_data_region_crop_hd",
        "source_pdf": str(source_pdf.resolve()),
        "source_hash": hashlib.sha256(source_pdf.read_bytes()).hexdigest(),
        "page_number": item.page_number,
        "item_number": item.item_number,
        "source_bbox": list(bbox),
        "bbox": list(bbox),
        "dpi": 300,
        "width_px": pixmap.width,
        "height_px": pixmap.height,
        "asset_hash": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        "segmentation_method": "ruled_table_region_v1",
        "layout_axis": "single",
        "panel_bboxes": [list(bbox)],
        "component_count": 1,
        "drawing_count": 1,
        "image_count": 0,
        "text_span_count": 0,
        "protected_texts": [],
        "excluded_texts": [],
        "excluded_body_spans": [],
        "caption_candidates": [],
        "caption_detection_source": "none",
        "caption_text_source": "none",
        "manual_review_required": False,
        "review_reasons": [],
    }
    provenance_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return CropArtifact(
        image_path, provenance_path, pixmap.width, pixmap.height,
    )


def _crop_exact_vector_region(
    source_pdf: Path,
    item: DetectedItem,
    bbox: tuple[float, float, float, float],
    output_dir: Path,
) -> CropArtifact:
    """Rasterize one proven prose-free vector cluster with its safety padding."""
    artifact = _crop_exact_data_region(source_pdf, item, bbox, output_dir)
    payload = json.loads(artifact.provenance_path.read_text(encoding="utf-8"))
    payload.update(
        asset_mode="pdf_exact_vector_region_crop_hd",
        segmentation_method="prose_free_vector_cluster_v1",
    )
    artifact.provenance_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return artifact


def _crop_exact_mixed_figure_region(
    source_pdf: Path,
    item: DetectedItem,
    bbox: tuple[float, float, float, float],
    output_dir: Path,
) -> CropArtifact:
    """Rasterize a geometry-proven mixed figure isolated beside paragraph prose."""
    artifact = _crop_exact_data_region(source_pdf, item, bbox, output_dir)
    payload = json.loads(artifact.provenance_path.read_text(encoding="utf-8"))
    payload.update(
        asset_mode="pdf_exact_prose_free_mixed_region_crop_hd",
        segmentation_method="right_of_prose_mixed_region_v1",
    )
    artifact.provenance_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return artifact


def _right_of_prose_mixed_bbox(
    page: fitz.Page,
    source_bbox: tuple[float, float, float, float],
    selected_bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float] | None:
    """Prove a mixed-object figure in a column beside, rather than within, prose."""
    source = fitz.Rect(source_bbox)
    selected = fitz.Rect(selected_bbox)
    prose = tuple(
        fitz.Rect(block[:4])
        for block in page.get_text("blocks", clip=source)
        if len("".join(str(block[4]).split())) >= 12
        and len(str(block[4]).split()) >= 4
        and block[1] < selected.y1
        and block[3] > selected.y0
        and block[2] <= selected.x0
    )
    if not prose:
        return None
    prose_right = max(block.x1 for block in prose)
    split = prose_right + 6
    if split >= source.x1 - 36:
        return None
    objects: list[fitz.Rect] = []
    for info in page.get_image_info(xrefs=True):
        rect = fitz.Rect(info["bbox"]) & source
        if not rect.is_empty and rect.x0 >= split:
            objects.append(rect)
    for drawing in page.get_drawings():
        rect = fitz.Rect(drawing["rect"]) & source
        if rect.is_empty:
            continue
        if rect.width >= source.width * 0.8 and rect.height >= source.height * 0.8:
            continue
        if rect.x0 >= split:
            objects.append(rect)
    labels = tuple(
        fitz.Rect(block[:4])
        for block in page.get_text("blocks", clip=source)
        if block[0] >= split and len("".join(str(block[4]).split())) < 12
    )
    objects.extend(labels)
    if len(objects) < 3:
        return None
    figure = objects[0]
    for rect in objects[1:]:
        figure |= rect
    figure = fitz.Rect(
        max(source.x0, figure.x0 - 6), max(source.y0, figure.y0 - 6),
        min(source.x1, figure.x1 + 6), min(source.y1, figure.y1 + 6),
    )
    if figure.width < 40 or figure.height < 40:
        return None
    if any(not (figure & block).is_empty for block in prose):
        return None
    return tuple(float(value) for value in figure)


def _crop_exact_data_components(
    source_pdf: Path,
    item: DetectedItem,
    component_bboxes: tuple[tuple[float, float, float, float], ...],
    output_dir: Path,
) -> CropArtifact:
    """Compose disjoint table/image components without rasterizing prose between them."""
    union = fitz.Rect(component_bboxes[0])
    for bbox in component_bboxes[1:]:
        union |= fitz.Rect(bbox)
    scale = 300 / 72
    width = max(1, round(union.width * scale))
    height = max(1, round(union.height * scale))
    canvas = Image.new("RGB", (width, height), "white")
    with fitz.open(source_pdf) as document:
        page = document[item.page_number - 1]
        for bbox in component_bboxes:
            rect = fitz.Rect(bbox)
            pixmap = page.get_pixmap(dpi=300, clip=rect, alpha=False)
            panel = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            canvas.paste(panel, (
                round((rect.x0 - union.x0) * scale),
                round((rect.y0 - union.y0) * scale),
            ))
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / f"page-{item.page_number}-item-{item.item_number}-figure.png"
    provenance_path = image_path.with_suffix(".json")
    canvas.save(image_path, dpi=(300, 300))
    payload = {
        "asset_mode": "pdf_exact_data_components_crop_hd",
        "source_pdf": str(source_pdf.resolve()),
        "source_hash": hashlib.sha256(source_pdf.read_bytes()).hexdigest(),
        "page_number": item.page_number,
        "item_number": item.item_number,
        "source_bbox": list(union),
        "bbox": list(union),
        "component_bboxes": [list(bbox) for bbox in component_bboxes],
        "dpi": 300,
        "width_px": width,
        "height_px": height,
        "asset_hash": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        "segmentation_method": "disjoint_data_components_v1",
        "layout_axis": "single",
        "panel_bboxes": [list(union)],
        "component_count": len(component_bboxes),
        "drawing_count": len(component_bboxes),
        "image_count": 0,
        "text_span_count": 0,
        "protected_texts": [],
        "excluded_texts": [],
        "excluded_body_spans": [],
        "caption_candidates": [],
        "caption_detection_source": "none",
        "caption_text_source": "none",
        "manual_review_required": False,
        "review_reasons": [],
    }
    provenance_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return CropArtifact(image_path, provenance_path, width, height)


def _prose_free_vector_bbox(
    page: fitz.Page,
    item: DetectedItem,
    source_bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float] | None:
    """Select vector ink outside paragraph blocks, excluding enclosing report frames."""
    source = fitz.Rect(source_bbox)
    prose = tuple(
        fitz.Rect(block[:4]) for block in page.get_text("blocks", clip=source)
        if len("".join(str(block[4]).split())) >= 24 and len(str(block[4]).split()) >= 8
    )
    enclosing_frame = False
    candidates: list[fitz.Rect] = []
    for drawing in page.get_drawings():
        rect = fitz.Rect(drawing["rect"]) & source
        if rect.is_empty:
            continue
        if rect.width >= source.width * 0.65 and rect.height >= source.height * 0.5:
            enclosing_frame = True
            continue
        if any((rect & text).get_area() / max(rect.get_area(), 1) >= 0.2 for text in prose):
            continue
        candidates.append(rect)
    if not enclosing_frame or not candidates:
        return None
    groups: list[tuple[fitz.Rect, int]] = []
    for rect in sorted(candidates, key=lambda value: (value.y0, value.x0)):
        expanded = fitz.Rect(
            rect.x0 - 12, rect.y0 - 12, rect.x1 + 12, rect.y1 + 12,
        )
        matches = [
            index for index, (group, _count) in enumerate(groups)
            if not (group & expanded).is_empty
        ]
        if not matches:
            groups.append((rect, 1))
            continue
        merged = rect
        count = 1
        for index in reversed(matches):
            group, group_count = groups.pop(index)
            merged |= group
            count += group_count
        groups.append((merged, count))
    selected, selected_count = max(
        groups, key=lambda entry: (entry[1], entry[0].get_area()),
    )
    if selected_count < 3 or selected.get_area() < 400:
        return None
    return tuple(float(value) for value in (
        max(source.x0, selected.x0 - 6), max(source.y0, selected.y0 - 6),
        min(source.x1, selected.x1 + 6), min(source.y1, selected.y1 + 6),
    ))


def _vector_prompt_bbox(
    page: fitz.Page,
    item: DetectedItem,
    question_bottom: float,
) -> tuple[float, float, float, float] | None:
    """Return the bounded union of diagram drawings above the final question row."""
    bounds = fitz.Rect(item.bbox)
    # A right-floating graph can extend below the first ask baseline.  Bound
    # vector ink by the end of the question sentence, not its first row; the
    # later 보기/choice area remains excluded.
    prompt = fitz.Rect(bounds.x0, bounds.y0, bounds.x1, question_bottom + 2)
    drawings: list[fitz.Rect] = []
    for drawing in page.get_drawings():
        clipped = fitz.Rect(drawing["rect"]) & prompt
        if clipped.is_empty:
            continue
        if (
            clipped.width >= bounds.width * 0.95
            or clipped.height >= bounds.height * 0.8
            or (clipped.width < 2 and clipped.height > bounds.height * 0.3)
            or (clipped.height < 1.5 and clipped.width > bounds.width * 0.8)
            or (clipped.width < 1 and clipped.height < 1)
        ):
            continue
        drawings.append(clipped)
    if not drawings:
        return None
    figure = drawings[0]
    for drawing in drawings[1:]:
        figure |= drawing
    return (
        max(bounds.x0, figure.x0 - 10),
        max(bounds.y0, figure.y0 - 10),
        min(bounds.x1, figure.x1 + 10),
        min(question_bottom + 2, figure.y1 + 10),
    )


def _drawn_fraction_bars(
    page: fitz.Page, item: DetectedItem,
) -> tuple[tuple[float, float, float, float], ...]:
    bounds = fitz.Rect(item.bbox)
    fonts = {
        str(span.get("font", ""))
        for block in page.get_text("dict", clip=bounds)["blocks"]
        for line in block.get("lines", ())
        for span in line.get("spans", ())
    }
    if "GSMediumB1" not in fonts:
        return ()
    bars: list[tuple[float, float, float, float]] = []
    for drawing in page.get_drawings():
        for component in drawing.get("items", ()):
            if component[0] != "l" or len(component) < 3:
                continue
            left, right = component[1], component[2]
            if abs(left.y - right.y) > 0.8:
                continue
            x0, x1 = sorted((float(left.x), float(right.x)))
            if not 8 <= x1 - x0 <= 45 or not bounds.contains(fitz.Point(x0, left.y)):
                continue
            bars.append((x0, float(left.y), x1, float(right.y)))
    return tuple(bars)


def build_draft(source_pdf: Path, item: DetectedItem, output_dir: Path, source_image: CropArtifact, layout_style: LayoutStyle) -> DraftArtifact:
    """Build one deterministic editable draft from a detected source item."""
    with fitz.open(source_pdf) as document:
        page = document[item.page_number - 1]
        is_ebs_textbook = bool(re.search(r"\[26023-\d{4}\]", item.source_text))
        raw_words = [
            word for word in _page_words(page, item.bbox)
            if not is_ebs_textbook or _EBS_SOURCE_TOKEN.fullmatch(word.text.strip()) is None
        ]
        raw_words = _without_vertical_subject_header(raw_words, item)
        if not raw_words:
            raise UnsupportedDraftLayoutError(
                item.page_number,
                item.item_number,
                "raster-only source preserved as image",
                source_image,
            )
        font_context = verify_equation_context(document, page, item.bbox)
        preliminary_words = _normalize_fractions(raw_words, _EquationDecoder(font_context))
        choice_start = next(
            word.bbox[1] for word in preliminary_words
            if any(marker in word.text for marker in _CIRCLED)
        )
        radical_bboxes = tuple(
            tuple(float(value) for value in drawing["rect"])
            for drawing in page.get_drawings()
            if 35 <= fitz.Rect(drawing["rect"]).width <= 80
            and 7 <= fitz.Rect(drawing["rect"]).height <= 16
        )
        statement_radicals = tuple(
            bbox for bbox in radical_bboxes if bbox[1] < choice_start - 2
        )
        preliminary_words = bind_drawn_inline_radicals(
            preliminary_words, statement_radicals,
        )
        all_fraction_bars = _drawn_fraction_bars(page, item)
        has_boxed_report = sum(
            1 for word in preliminary_words
            if word.text.lstrip().startswith(("◦", "○"))
        ) >= 2
        fraction_bars = (
            all_fraction_bars
            if has_boxed_report else
            tuple(bar for bar in all_fraction_bars if bar[1] < choice_start - 2)
        )
        preliminary_words = bind_drawn_fractions(preliminary_words, fraction_bars)
        preliminary_words = _align_plain_claim_words(preliminary_words)
        choices = _choice_texts(preliminary_words, choice_start, radical_bboxes)
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
        ruled_tables = recover_ruled_tables(page, item, preliminary_words, choice_start)
        editable_data_tables = tuple(
            table for table in ruled_tables
            if table.row_count >= 3 and table.column_count == 2
        )
        dense_data_tables = tuple(
            table for table in ruled_tables
            if table.row_count >= 3 and table.column_count >= 3
        )
        preliminary_rows = _rows(preliminary_words)
        preliminary_choice_index, _ = _choice_content_boundary(preliminary_rows)
        preliminary_question_index = max(
            index for index, row in enumerate(preliminary_rows[:preliminary_choice_index])
            if any("?" in word.text for word in row)
        )
        preliminary_ask_index = _ask_row_start(
            preliminary_rows, preliminary_question_index,
        )
        editable_dialogue = _ebs_student_dialogue(raw_words, item.source_text)
        image_bbox = _embedded_figure_bbox(
            page,
            item,
            preliminary_rows[preliminary_ask_index][0].bbox[1],
            graphical_choice_bboxes,
        )
        if editable_dialogue:
            image_bbox = None
        editable_experiment_region = has_editable_experiment_region(
            item.source_text, image_bbox, ruled_tables,
        )
        prompt_gaps = [
            (
                preliminary_rows[index + 1][0].bbox[1]
                - max(word.bbox[3] for word in preliminary_rows[index]),
                index,
            )
            for index in range(preliminary_choice_index - 1)
        ]
        significant_prompt_gaps = [entry for entry in prompt_gaps if entry[0] >= 8.0]
        _, separated_passage_end = (
            min(significant_prompt_gaps, key=lambda entry: entry[1])
            if significant_prompt_gaps else max(prompt_gaps)
        )
        passage_end_index = (
            preliminary_question_index - 1 if image_bbox is not None else separated_passage_end
        )
        bullet_start = (
            passage_end_index + 1
            if is_experiment_text(item.source_text) else
            next((
                index for index, row in enumerate(preliminary_rows[:preliminary_ask_index])
                if _join_rows([row]).lstrip().startswith(("◦", "○"))
            ), passage_end_index + 1)
        )
        boxed_tables = recover_bulleted_box(
            preliminary_rows[bullet_start:preliminary_ask_index],
        )
        whitespace_bbox = (
            item.bbox[0] + 10,
            max(word.bbox[3] for word in preliminary_rows[passage_end_index]) + 6,
            item.bbox[2] - 10,
            preliminary_rows[preliminary_ask_index][0].bbox[1] - 6,
        )
        embedded_component = False
        if image_bbox is not None:
            image_rect = fitz.Rect(image_bbox)
            embedded_component = (
                image_rect.width < (item.bbox[2] - item.bbox[0]) * 0.2
                and any(
                    not (fitz.Rect(drawing["rect"]) & image_rect).is_empty
                    for drawing in page.get_drawings()
                )
            )
        figure_bbox = whitespace_bbox if embedded_component else image_bbox or whitespace_bbox
        figure_search_rect = fitz.Rect(item.bbox)
        embedded_visual_bboxes = tuple(
            fitz.Rect(info["bbox"])
            for info in page.get_image_info(xrefs=True)
            if not (fitz.Rect(info["bbox"]) & figure_search_rect).is_empty
            and not any(
                not (fitz.Rect(info["bbox"]) & fitz.Rect(choice_bbox)).is_empty
                for choice_bbox in graphical_choice_bboxes
            )
        )
        has_embedded_visual = bool(embedded_visual_bboxes)
        if image_bbox is None and embedded_visual_bboxes:
            visual_region = embedded_visual_bboxes[0]
            for visual_bbox in embedded_visual_bboxes[1:]:
                visual_region |= visual_bbox
            figure_bbox = tuple(float(value) for value in visual_region)
        has_prompt_figure = (
            image_bbox is not None
            or has_embedded_visual
            or (
                is_ebs_textbook
                and "그림" in item.source_text
                and figure_bbox[3] - figure_bbox[1] >= 24
            )
            or (
                figure_bbox[3] - figure_bbox[1] >= 24
                and _prose_free_vector_bbox(page, item, figure_bbox) is not None
            )
        )
        if image_bbox is None and figure_bbox[3] - figure_bbox[1] < 24:
            vector_bbox = _vector_prompt_bbox(
                page,
                item,
                max(
                    word.bbox[3]
                    for row in preliminary_rows[
                        preliminary_ask_index:preliminary_question_index + 1
                    ]
                    for word in row
                ),
            )
            if vector_bbox is not None:
                figure_bbox = vector_bbox
                has_prompt_figure = True
        boxed_visual_bbox = None
        if boxed_tables and image_bbox is None:
            boxed_visual_bbox = _prose_free_vector_bbox(
                page, item, figure_bbox,
            )
            if boxed_visual_bbox is not None:
                figure_bbox = boxed_visual_bbox
                has_prompt_figure = True
        if figure_bbox[3] <= figure_bbox[1]:
            figure_bbox = (
                item.bbox[0] + 10,
                preliminary_rows[0][0].bbox[1],
                item.bbox[2] - 10,
                preliminary_rows[preliminary_ask_index][0].bbox[1] - 6,
            )
        exact_data_bbox: tuple[float, float, float, float] | None = None
        exact_data_components: tuple[tuple[float, float, float, float], ...] = ()
        if (
            (editable_experiment_region and not has_embedded_visual)
            or
            editable_dialogue
            or (
                boxed_tables and image_bbox is None
                and not has_embedded_visual
                and (figure_bbox[2] - figure_bbox[0])
                >= (item.bbox[2] - item.bbox[0]) * 0.75
            )
            or (
                editable_data_tables
                and image_bbox is None
                and not has_embedded_visual
                and (
                    not is_ebs_textbook
                    or _prose_free_vector_bbox(page, item, figure_bbox) is None
                )
            )
        ):
            figure = None
        elif dense_data_tables and not is_experiment_text(item.source_text):
            components = [table.bbox for table in dense_data_tables]
            if image_bbox is not None:
                image_region = fitz.Rect(image_bbox)
                components.extend(
                    tuple(float(value) for value in info["bbox"])
                    for info in page.get_image_info(xrefs=True)
                    if image_region.contains(fitz.Rect(info["bbox"]))
                )
            exact_data_components = tuple(dict.fromkeys(components))
            data_region = fitz.Rect(exact_data_components[0])
            for component in exact_data_components[1:]:
                data_region |= fitz.Rect(component)
            exact_data_bbox = tuple(float(value) for value in data_region)
            figure = (
                _crop_exact_data_components(
                    source_pdf, item, exact_data_components, output_dir,
                )
                if len(exact_data_components) > 1 else
                _crop_exact_data_region(source_pdf, item, exact_data_bbox, output_dir)
            )
        else:
            if not has_prompt_figure:
                figure = None
            elif boxed_visual_bbox is not None:
                figure = _crop_exact_vector_region(
                    source_pdf, item, boxed_visual_bbox, output_dir,
                )
            else:
                try:
                    figure = _crop_prompt_region(
                        source_pdf, item, figure_bbox, output_dir, source_image,
                    )
                except EmptyFigureSelectionError:
                    figure = None
        proven_bbox = (
            _proven_rasterized_text_bbox(figure, item, source_image)
            if figure is not None and not exact_data_components else None
        )
        rasterized_text_bboxes = exact_data_components or ((proven_bbox,) if proven_bbox else ())
        decoder = _EquationDecoder(font_context)
        words = _normalize_fractions(
            [
                word for word in raw_words
                if _outside_rasterized_text(word, rasterized_text_bboxes)
            ],
            decoder,
        )
        words = bind_drawn_inline_radicals(words, statement_radicals)
        words = bind_drawn_fractions(words, fraction_bars)
        words = _align_plain_claim_words(words)
        experiment_tables = recover_experiment_tables(page, item, words, choice_start)
        structured_tables = (*experiment_tables, *editable_data_tables, *boxed_tables)
        words = without_table_words(words, structured_tables)
        rows = _rows(words, decoder)
        blocking_unknown = {
            value for value in decoder.unknown
            if not (
                is_ebs_textbook
                and value.startswith("ambiguous-equation-row@")
            )
        }
        if blocking_unknown:
            glyphs = ", ".join(sorted(blocking_unknown))
            raise UnsupportedDraftLayoutError(
                item.page_number,
                item.item_number,
                f"unverified equation glyphs require manual review: {glyphs}",
                source_image,
            )
        choice_row_index, choice_header = _choice_content_boundary(rows)
        radical_bboxes = tuple(
            tuple(float(value) for value in drawing["rect"])
            for drawing in page.get_drawings()
            if 35 <= fitz.Rect(drawing["rect"]).width <= 80
            and 7 <= fitz.Rect(drawing["rect"]).height <= 16
        )
        choices = () if graphical_choice_bboxes else _choice_texts(
            words, choice_start, radical_bboxes,
        )
    question_row_index = next(
        index for index, row in enumerate(rows[:choice_row_index])
        if any("?" in word.text for word in row)
    )
    if (image_bbox is not None or exact_data_bbox is not None) and not is_experiment_text(item.source_text):
        content_bottom = (exact_data_bbox or image_bbox)[3]
        geometry_ask_index = next(
            (
                index for index, row in enumerate(rows[:choice_row_index])
                if row and row[0].bbox[1] > content_bottom
            ),
            question_row_index,
        )
        ask_start_index = min(
            geometry_ask_index, _ask_row_start(rows, question_row_index),
        )
    else:
        ask_start_index = _ask_row_start(rows, question_row_index)
    passage_rows = rows[: ask_start_index]
    passage_text = (
        "\n".join(_join_rows([row]) for row in passage_rows)
        if is_experiment_text(item.source_text)
        else _join_rows(passage_rows)
    )
    passage = re.sub(rf"^{item.item_number}\s*\.\s*", "", passage_text)
    if is_ebs_textbook:
        passage = re.sub(r"^\d{2}\s+", "", passage)
        final_period = passage.rfind(".")
        if final_period >= 0:
            passage = passage[: final_period + 1]
        passage = rejoin_ebs_soft_wraps(passage, item.source_text)
    if figure is not None:
        passage = re.sub(r"(?:\s*\((?:가|나|다)\)){1,3}\s*$", "", passage).rstrip()
    passage = insert_result_tables(
        passage, (*experiment_tables, *editable_data_tables, *boxed_tables),
    )
    ask = _join_rows(rows[ask_start_index:choice_row_index])
    passage = _normalize_legacy_isotopes(passage)
    ask = _normalize_legacy_isotopes(ask)
    if _has_excluded_caption(figure):
        ask = re.sub(r"^(?:(?:\([가나다]\))\s+){1,3}(?=\S)", "", ask)
    if is_ebs_textbook:
        ebs_slots = parse_ebs_text_slots(item.source_text)
        if editable_dialogue and ebs_slots.ask:
            dialogue_frame = re.split(r"(?<=\.)\s+", ebs_slots.ask, maxsplit=1)
            if len(dialogue_frame) == 2:
                passage, ask = dialogue_frame
        if ebs_slots.passage:
            passage = ebs_slots.passage
        if editable_dialogue:
            passage = "\n".join((
                passage,
                *(f"학생 {label}: {text}" for label, text in editable_dialogue),
            )).strip()
        if ebs_slots.ask and not editable_dialogue:
            ask = ebs_slots.ask
        if not graphical_choice_bboxes and len(ebs_slots.choices) == 5:
            choices = ebs_slots.choices
    routed = (
        route_single_composite(figure)
        if figure is not None and is_ebs_textbook else
        route_figure(passage, figure)
        if figure is not None else None
    )
    graphical_choice_assets = _crop_graphical_choices(
        source_pdf, item, graphical_choice_bboxes, output_dir,
    )
    routed_assets = routed.assets if routed is not None else ()
    if graphical_choice_assets and len(routed_assets) != 1 and figure is not None:
        routed_assets = (stamp_single_prompt_figure(figure),)
    material = ",".join(asset.image_path.name for asset in routed_assets)
    question = palette_question(
        passage, ask, material, routed.layout if routed is not None else None,
    )
    if choice_header:
        question["choice_header"] = choice_header
        question["style_meta"] = {"palette_template": "수능AI실제비교선지형"}
    if graphical_choice_assets:
        if len(routed_assets) > 1:
            raise UnsupportedDraftLayoutError(
                item.page_number,
                item.item_number,
                "graphical answer choices require one prompt figure",
                source_image,
            )
        markdown = _graphical_choice_markdown(
            item.item_number, passage, ask,
            routed_assets[0] if routed_assets else None,
            graphical_choice_assets,
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
