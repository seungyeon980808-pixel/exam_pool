from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile

import fitz
import pytest

from app.pdf_hwp_roundtrip_generated_detection import detect_generated_items


_Q234_PDF_SHA256 = "4469115abdbd6a4c44056be7303e39e57e8ac7794fd4f7be4e9aa7a708824d92"
_REAL_NAMESPACE = Path(
    "data/pdf_hwp/roundtrip_harness/approved-first-run/namespaces/265d8950cc5af185b702/sources"
)
_FINAL_E2_PDF = Path(
    "data/pdf_hwp/roundtrip_harness/approved-first-run/namespaces/124703f604ca39314e39/"
    "sources/e2_2023_11-0d351a04cbce/conversion/converted.pdf"
)
_REAL_CASES = (
    ("b1_2024_09", "ae174585e1ae29e63c448862d0611604dd130b0a91007972a264fef2f476ceae", (1, 2, 3, 5, 6, 8, 9, 10, 11, 15, 20)),
    ("b2_2022_06", "c13daa253a712e0573aacf1413251085da3dcd248c92095c5086721d2d7e1d98", (1, 3, 5, 6, 8, 9, 10, 11, 12, 13, 15, 17, 18, 20)),
    ("c1_2024_06", "0bdbdd3db45b9b357b3cf90ff2168d7d6ff6c3720d59283fcaaefdc78e690ce9", (1, 5, 6, 7, 8, 9, 10, 12, 13, 14, 18, 19, 20)),
    ("e2_2025_09", "da5410b89355f5f37a465ef523a7d5e0deff247bed659bd9b20488dcadf8ce94", (2, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 17)),
    ("ebs_2027_physics1", "51af9dde3dbd7c2a9084242999bf48bc67954a67a1e7706281121b71a3926d85", (12, 48, 60, 72, 84, 96, 108, 120, 132, 144, 156, 168, 180, 192, 204, 216, 228, 234, 235, 237, 238, 254, 262, 270, 278)),
    ("p2_2013_11", "09a3413726ee1e3c6200b53ea3ed0e0502ea8ce5217d3b41f9a26dca40089a5a", (1, 2, 6, 7, 8, 9, 12, 15, 16, 17, 18, 19, 20)),
)


def _write_generated_pdf(path: Path) -> None:
    with fitz.open() as document:
        first = document.new_page(width=420, height=500)
        for x, y, text in (
            (36, 60, "101. LEFT A"),
            (70, 125, "detail 777. inline marker"),
            (36, 225, "102. LEFT B"),
            (230, 70, "305. RIGHT A"),
            (230, 245, "306. RIGHT B"),
            (36, 485, "999. footer"),
        ):
            first.insert_text((x, y), text, fontsize=12)
        second = document.new_page(width=420, height=500)
        second.insert_text((36, 60), "7. NEXT PAGE", fontsize=12)
        second.insert_text((150, 180), "555.", fontsize=12)
        second.insert_text((36, 485), "2026. footer", fontsize=12)
        document.save(path)


def test_generated_detector_handles_columns_pages_and_three_digit_numbers(tmp_path: Path) -> None:
    # Given: generated output has reordered columns, multiple pages, and numeric noise.
    source = tmp_path / "generated.pdf"
    _write_generated_pdf(source)

    # When: the generated-output detector reads observable leading-number geometry.
    result = detect_generated_items(source)

    # Then: 1-4 digit question leaders are bounded by their own column only.
    assert tuple((item.item_number, item.page_number, item.column) for item in result.items) == (
        (101, 1, 0), (102, 1, 0), (305, 1, 1), (306, 1, 1), (7, 2, 0),
    )
    assert result.items[0].bbox[3] <= result.items[1].bbox[1]
    assert result.items[0].bbox[2] <= result.items[2].bbox[0]
    assert all(item.item_number not in (555, 777, 999, 2026) for item in result.items)


def test_last_right_item_owns_next_page_text_before_first_left_leader(tmp_path: Path) -> None:
    source = tmp_path / "cross-page.pdf"
    with fitz.open() as document:
        page = document.new_page(width=420, height=500)
        page.insert_text((36, 60), "10. LEFT", fontsize=12)
        page.insert_text((230, 300), "11. RIGHT STEM", fontsize=12)
        next_page = document.new_page(width=420, height=500)
        next_page.insert_text((230, 60), "13. RIGHT", fontsize=12)
        next_page.insert_text((36, 130), "<보기> ㄱ. A ㄴ. B ㄷ. C", fontsize=12)
        next_page.insert_text((36, 160), "① A ② B ③ C ④ D ⑤ E", fontsize=12)
        next_page.insert_text((36, 300), "12. LEFT", fontsize=12)
        document.save(source)

    item = next(value for value in detect_generated_items(source).items if value.item_number == 11)

    assert "RIGHT STEM" in item.source_text
    assert "A" in item.source_text and "E" in item.source_text
    assert "12. LEFT" not in item.source_text


@pytest.mark.parametrize(("source_id", "sha256", "expected"), _REAL_CASES)
def test_real_first_run_generated_leaders_are_recovered(
    source_id: str, sha256: str, expected: tuple[int, ...],
) -> None:
    candidates = tuple(_REAL_NAMESPACE.glob(f"{source_id}-*/conversion/converted.pdf"))
    if not candidates:
        pytest.skip("approved first-run generated PDF is unavailable")
    source = candidates[0]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == sha256

    result = detect_generated_items(source)

    assert tuple(item.item_number for item in result.items) == expected


def test_real_cross_column_continuation_keeps_fifth_choice() -> None:
    candidates = tuple(_REAL_NAMESPACE.glob("e2_2023_11-*/conversion/converted.pdf"))
    if not candidates:
        pytest.skip("approved first-run generated PDF is unavailable")
    source = candidates[0]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == (
        "6fafcfd082d0ec2509873312842f1aca38f36b15b015ba909c0057ede6ffc045"
    )

    item = next(value for value in detect_generated_items(source).items if value.item_number == 3)

    assert "⑤" in item.source_text
    assert "4. 다음은" not in item.source_text


def test_final_run_e2_recovers_glued_item_12_leader() -> None:
    if not _FINAL_E2_PDF.is_file():
        pytest.skip("final-run e2 generated PDF is unavailable")
    assert hashlib.sha256(_FINAL_E2_PDF.read_bytes()).hexdigest() == (
        "711505716985952bc08afa98ed7ff05feeba6bc344f7f72186d862456d8f87bd"
    )

    result = detect_generated_items(_FINAL_E2_PDF)

    assert tuple(item.item_number for item in result.items) == (
        1, 2, 3, 4, 6, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 20,
    )
    item3 = next(item for item in result.items if item.item_number == 3)
    assert "⑤" in item3.source_text
    assert "4. 다음은" not in item3.source_text


def test_real_q234_generated_pdf_detects_item_234() -> None:
    # Given: the retained exact q234 generated PDF, pinned by content hash.
    candidates = tuple(Path(tempfile.gettempdir()).glob(
        "exampool-w6-q234-*/output/conversion/converted.pdf",
    ))
    if not candidates:
        pytest.skip("retained q234 pilot is unavailable")
    source = candidates[0]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == _Q234_PDF_SHA256

    # When: generated detection runs against its real text geometry.
    result = detect_generated_items(source)

    # Then: the visible three-digit question leader produces an item bbox.
    assert tuple(item.item_number for item in result.items) == (234,)
    assert result.items[0].page_number == 1
    assert "234." in result.items[0].source_text
