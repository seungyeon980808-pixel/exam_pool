from __future__ import annotations

from pathlib import Path

import fitz
from PIL import Image, ImageStat
import pytest

import app.pdf_hwp_roundtrip_item_alignment as alignment_subject
from app.pdf_hwp_pipeline_models import DetectedItem, DetectionResult
from app.pdf_hwp_roundtrip_item_alignment import (
    AlignmentIssue,
    ItemAlignmentRequest,
    align_and_compare_items,
)
from app.pdf_hwp_roundtrip_visual import PageAlignment, align_page_numbers


def _write_items(path: Path, items: tuple[tuple[int, str, float], ...]) -> None:
    with fitz.open() as document:
        for number, label, bar_width in items:
            page = document.new_page(width=300, height=220)
            page.insert_text((32, 42), f"{number}. {label}", fontsize=16)
            page.draw_rect(
                fitz.Rect(42, 82, 42 + bar_width, 112),
                color=(0.0, 0.0, 0.0),
                fill=(0.2, 0.2, 0.2),
            )
        document.save(path)


def test_page_alignment_pin_remains_positional_for_subset() -> None:
    # Given: the existing whole-PDF page alignment receives a three-to-two subset.
    # When: positional pages are aligned.
    result = align_page_numbers(3, 2)
    # Then: the existing primitive remains positional and exposes why item alignment is separate.
    assert result == (PageAlignment(1, 1), PageAlignment(2, 2), PageAlignment(3, None))


def test_item_alignment_maps_reordered_subset_by_item_number(tmp_path: Path) -> None:
    # Given: generated output contains only items 3 and 1, in reversed source order.
    source = tmp_path / "source.pdf"
    generated = tmp_path / "generated.pdf"
    _write_items(source, ((1, "ALPHA", 100), (2, "BETA", 90), (3, "GAMMA", 80)))
    _write_items(generated, ((3, "GAMMA", 80), (1, "ALPHA", 100)))

    # When: only manifest-selected items 1 and 3 are compared.
    result = align_and_compare_items(ItemAlignmentRequest(
        source, generated, (1, 3), tmp_path / "evidence", dpi=120,
    ))

    # Then: item identity, not page index, drives both aligned clips.
    assert tuple(
        (pair.item_number, pair.source.page_number, pair.generated.page_number)
        for pair in result.pairs
    ) == ((1, 1, 2), (3, 3, 1))
    assert result.issues == ()
    assert all(comparison.pixel_mae < 0.001 for comparison in result.comparisons)
    assert all(comparison.source_render.is_file() for comparison in result.comparisons)


def test_item_alignment_uses_generated_detector_for_three_digit_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: source selection already resolved item 234 and generated text starts with 234.
    source = tmp_path / "source.pdf"
    generated = tmp_path / "generated.pdf"
    _write_items(source, ((234, "ALPHA", 100),))
    _write_items(generated, ((234, "ALPHA", 100),))
    source_item = DetectedItem(1, 234, 0, (24.0, 16.0, 292.0, 190.0), "234. ALPHA")
    monkeypatch.setattr(
        alignment_subject,
        "detect_items",
        lambda path: DetectionResult(path, "0" * 64, 1, (source_item,)),
    )

    # When: alignment independently detects the generated output.
    result = align_and_compare_items(ItemAlignmentRequest(
        source, generated, (234,), tmp_path / "evidence", dpi=96,
    ))

    # Then: the three-digit item is paired and never reported missing.
    assert tuple(pair.item_number for pair in result.pairs) == (234,)
    assert result.missing_generated_items == ()


def test_item_alignment_reports_missing_and_duplicate_items(tmp_path: Path) -> None:
    # Given: item 2 is duplicated at source and item 3 is absent from generated output.
    source = tmp_path / "source.pdf"
    generated = tmp_path / "generated.pdf"
    _write_items(source, ((1, "ONE", 80), (2, "TWO", 80), (2, "TWO COPY", 80), (3, "THREE", 80)))
    _write_items(generated, ((1, "ONE", 80), (2, "TWO", 80)))

    # When: all three selected identities are aligned.
    result = align_and_compare_items(ItemAlignmentRequest(
        source, generated, (1, 2, 3), tmp_path / "evidence", dpi=96,
    ))

    # Then: ambiguous and absent identities are stable machine-readable failures.
    assert result.duplicate_source_items == (2,)
    assert result.missing_generated_items == (3,)
    assert result.issues == (
        AlignmentIssue.DUPLICATE_SOURCE_ITEM,
        AlignmentIssue.MISSING_GENERATED_ITEM,
    )


def test_item_alignment_writes_failure_only_contact_sheet(tmp_path: Path) -> None:
    # Given: one aligned item has a meaningful graphical change.
    source = tmp_path / "source.pdf"
    generated = tmp_path / "generated.pdf"
    _write_items(source, ((7, "SEVEN", 130),))
    _write_items(generated, ((7, "SEVEN", 45),))

    # When: the item clips cross the real PyMuPDF and Pillow boundary.
    result = align_and_compare_items(ItemAlignmentRequest(
        source, generated, (7,), tmp_path / "evidence", dpi=120,
    ))

    # Then: metrics, meaningful bounds, and labeled failure evidence are material.
    comparison = result.comparisons[0]
    assert comparison.issues == (AlignmentIssue.VISUAL_MISMATCH,)
    assert comparison.diff_bbox is not None
    assert comparison.pixel_mae > 0.01
    with Image.open(result.contact_sheet) as sheet:
        assert sheet.width == 884
        assert sheet.height > 200
        assert ImageStat.Stat(sheet.convert("L")).stddev[0] > 5.0
