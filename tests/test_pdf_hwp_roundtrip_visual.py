from __future__ import annotations

from pathlib import Path

import fitz
from PIL import Image, ImageStat

from app.pdf_hwp_roundtrip_visual import (
    ComparisonRequest,
    PageAlignment,
    VisualIssue,
    compare_pdf_visuals,
)


def _write_pdf(path: Path, pages: tuple[tuple[str, float, float], ...]) -> None:
    with fitz.open() as document:
        for label, inset, bar_width in pages:
            page = document.new_page(width=300, height=220)
            page.insert_text((40 + inset, 50 + inset), label, fontsize=18)
            page.draw_rect(
                fitz.Rect(40 + inset, 80 + inset, 40 + inset + bar_width, 115 + inset),
                color=(0.0, 0.0, 0.0),
                fill=(0.2, 0.2, 0.2),
            )
        document.save(path)


def test_compare_pdf_visuals_pairs_pages_and_writes_failure_evidence(tmp_path: Path) -> None:
    # Given: two PDFs with a harmless uniform margin shift and one changed second-page bar.
    source = tmp_path / "source.pdf"
    generated = tmp_path / "generated.pdf"
    _write_pdf(source, (("PAGE ONE", 0, 120), ("PAGE TWO", 0, 120)))
    _write_pdf(generated, (("PAGE ONE", 4, 120), ("PAGE TWO", 4, 55)))

    # When: the PDFs cross the real render, alignment, and visual comparison boundary.
    result = compare_pdf_visuals(ComparisonRequest(source, generated, tmp_path / "visual", dpi=120))

    # Then: index pairing is deterministic, small margins are ignored, and real change is cropped.
    assert tuple((page.source_page, page.generated_page) for page in result.pages) == ((1, 1), (2, 2))
    assert result.pages[0].issues == ()
    assert result.pages[1].issues == (VisualIssue.VISUAL_MISMATCH,)
    assert result.pages[1].diff_bbox is not None
    assert result.pages[1].failure_crop is not None
    assert result.pages[1].failure_crop.is_file()
    assert result.issues == (VisualIssue.VISUAL_MISMATCH,)

    with Image.open(result.contact_sheet) as sheet:
        assert sheet.width == 884
        assert sheet.height > 300
        assert ImageStat.Stat(sheet.convert("L")).stddev[0] > 5.0


def test_compare_pdf_visuals_reports_page_count_mismatch(tmp_path: Path) -> None:
    # Given: a two-page source and one-page generated result.
    source = tmp_path / "source.pdf"
    generated = tmp_path / "generated.pdf"
    _write_pdf(source, (("ONE", 0, 100), ("TWO", 0, 100)))
    _write_pdf(generated, (("ONE", 0, 100),))

    # When: pages are paired by their stable one-based index.
    result = compare_pdf_visuals(ComparisonRequest(source, generated, tmp_path / "visual"))

    # Then: the shared page is compared and the missing page has a stable aggregate code.
    assert len(result.pages) == 1
    assert result.alignments == (PageAlignment(1, 1), PageAlignment(2, None))
    assert result.issues == (VisualIssue.PAGE_COUNT_MISMATCH,)


def test_compare_pdf_visuals_reports_empty_render(tmp_path: Path) -> None:
    # Given: two valid PDFs whose sole pages contain no visible marks.
    source = tmp_path / "source.pdf"
    generated = tmp_path / "generated.pdf"
    with fitz.open() as document:
        document.new_page(width=300, height=220)
        document.save(source)
    with fitz.open() as document:
        document.new_page(width=300, height=220)
        document.save(generated)

    # When: blank rendered pages cross the comparison boundary.
    result = compare_pdf_visuals(ComparisonRequest(source, generated, tmp_path / "visual"))

    # Then: blank output cannot silently count as a visual match.
    assert result.pages[0].issues == (VisualIssue.EMPTY_RENDER,)
    assert result.issues == (VisualIssue.EMPTY_RENDER,)
