from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import fitz
from PIL import Image, ImageDraw

from app.pdf_hwp_strict_qa import (
    DocumentManifest,
    ItemRecord,
    FigureRecord,
    audit_hwpx_images,
    compare_content_tokens,
    compare_item_structure,
    compare_pdf_pages,
    run_strict_qa,
)


def _pdf(path: Path, pages: int = 1, *, changed: bool = False) -> None:
    with fitz.open() as document:
        for number in range(1, pages + 1):
            page = document.new_page(width=240, height=180)
            page.insert_text((20, 30), f"{number}. x = 2", fontsize=12)
            page.draw_rect(fitz.Rect(20, 50, 100 if not changed else 110, 80), fill=(0, 0, 0))
        document.save(path)


def _png(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    ImageDraw.Draw(image).rectangle((2, 2, max(2, width - 3), max(2, height - 3)), outline="black")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _hwpx(path: Path, *, width: int = 120, height: int = 80) -> tuple[str, bytes]:
    data = _png(width, height)
    resource = "BinData/BIN0001.png"
    xml = (
        '<hp:section xmlns:hp="urn:test" xmlns:hc="urn:test">'
        f'<hp:pic><hc:img binaryItemID="BIN0001"><hc:sz width="{width}" height="{height}"/>'
        "</hc:img></hp:pic></hp:section>"
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(resource, data)
        archive.writestr("Contents/section0.xml", xml)
    import hashlib
    return hashlib.sha256(data).hexdigest(), data


def _manifests(*, actual_bbox=(20, 20, 160, 145), editable=True, equations=True,
               reopen=True) -> tuple[DocumentManifest, DocumentManifest]:
    item = ItemRecord("jongro-p1-q1", 1, (20, 20, 160, 145), "1. x = 2", ("x = 2",), ("① 2",))
    figure = FigureRecord("jongro-p1-q1-f1", item.item_id, 1, (30, 90, 100, 130))
    expected = DocumentManifest(1, (item,), (figure,), ((240.0, 180.0),), (2,), True, True, True, True)
    actual_item = ItemRecord(item.item_id, 1, actual_bbox, item.text, item.formulas, item.choices,
                             figure_ids=item.figure_ids)
    actual_figure = FigureRecord(figure.figure_id, figure.item_id, 1, figure.bbox)
    actual = DocumentManifest(1, (actual_item,), (actual_figure,), ((240.0, 180.0),), (2,),
                              editable, equations, reopen, reopen)
    return expected, actual


def test_figure_only_image_is_mapped_and_page_capture_is_rejected(tmp_path: Path) -> None:
    hwpx = tmp_path / "figure.hwpx"
    digest, _ = _hwpx(hwpx)
    audit = audit_hwpx_images(hwpx, [{"sha256": digest, "role": "figure", "item_id": "q1",
                                     "page": 1, "bbox": [10, 10, 30, 30]}], page_size_px=(600, 450))
    assert audit.passed
    assert audit.records[0].classification == "figure"

    capture = tmp_path / "capture.hwpx"
    capture_digest, _ = _hwpx(capture, width=600, height=450)
    capture_audit = audit_hwpx_images(capture, [{"sha256": capture_digest, "role": "figure",
                                                "item_id": "q1", "page": 1, "bbox": [0, 0, 600, 450]}],
                                     page_size_px=(600, 450))
    assert not capture_audit.passed
    assert capture_audit.page_or_body_captures


def test_page_count_mismatch_fails_even_when_files_are_readable(tmp_path: Path) -> None:
    source, generated = tmp_path / "source.pdf", tmp_path / "generated.pdf"
    _pdf(source, 2)
    _pdf(generated, 1)
    hwpx = tmp_path / "result.hwpx"
    digest, _ = _hwpx(hwpx)
    expected, actual = _manifests()
    expected = DocumentManifest(2, expected.items, expected.figures, ((240.0, 180.0),) * 2,
                                expected.page_columns, True, True, True, True)
    report = run_strict_qa(source, generated, hwpx, expected, output_dir=tmp_path / "qa",
                           actual=actual, figure_manifest=[{"sha256": digest, "role": "figure",
                           "item_id": "jongro-p1-q1", "page": 1, "bbox": [30, 90, 100, 130]}])
    assert not report.passed
    assert not next(x for x in report.gates if x.name == "page_count").passed


def test_numeric_formula_choice_delta_fails() -> None:
    expected = (ItemRecord("q1", 1, text="a=2", formulas=("x=2",), choices=("① 2",)),)
    actual = (ItemRecord("q1", 1, text="a=3", formulas=("x=2",), choices=("① 2",)),)
    passed, detail = compare_content_tokens(expected, actual)
    assert not passed
    assert "missing" in detail and "extra" in detail


def test_missing_duplicate_item_and_figure_cannot_pass() -> None:
    expected, actual = _manifests()
    duplicated = DocumentManifest(actual.page_count, actual.items + actual.items, actual.figures,
                                  actual.page_size_pt, actual.page_columns, True, True, True, True)
    passed, detail = compare_item_structure(expected, duplicated)
    assert not passed
    assert "item ids differ" in detail


def test_visual_overlay_is_page_exact_and_reports_delta(tmp_path: Path) -> None:
    source, generated = tmp_path / "source.pdf", tmp_path / "generated.pdf"
    _pdf(source)
    _pdf(generated, changed=True)
    comparisons = compare_pdf_pages(source, generated, tmp_path / "renders", dpi=72, threshold=0.001)
    assert len(comparisons) == 1
    assert not comparisons[0].passed
    assert comparisons[0].overlay_path.is_file()
    assert comparisons[0].diff_path.is_file()


def test_all_gates_are_required_not_just_open_and_page_count(tmp_path: Path) -> None:
    source, generated = tmp_path / "source.pdf", tmp_path / "generated.pdf"
    _pdf(source)
    _pdf(generated)
    hwpx = tmp_path / "result.hwpx"
    digest, _ = _hwpx(hwpx)
    expected, actual = _manifests(editable=False, equations=False, reopen=False)
    report = run_strict_qa(source, generated, hwpx, expected, output_dir=tmp_path / "qa", actual=actual,
                           figure_manifest=[{"sha256": digest, "role": "figure", "item_id": "jongro-p1-q1",
                                             "page": 1, "bbox": [30, 90, 100, 130]}], dpi=72)
    assert not report.passed
    assert not next(x for x in report.gates if x.name == "native_editable_text_equations").passed
    assert not next(x for x in report.gates if x.name == "hwp_hwpx_reopen").passed


def test_coordinate_mismatch_fails() -> None:
    expected, actual = _manifests(actual_bbox=(80, 80, 200, 170))
    passed, detail = __import__("app.pdf_hwp_strict_qa", fromlist=["compare_coordinates"]).compare_coordinates(expected, actual)
    assert not passed
    assert "bbox" in detail


def test_regression_first_page_overflow_blank_page_is_page_count_failure(tmp_path: Path) -> None:
    source, generated = tmp_path / "source.pdf", tmp_path / "generated.pdf"
    _pdf(source, 1)
    _pdf(generated, 2)
    comparisons = compare_pdf_pages(source, generated, tmp_path / "renders", dpi=72)
    assert len(comparisons) == 1


def test_regression_q7_graph_must_be_one_figure_after_choices() -> None:
    expected = DocumentManifest(1, (ItemRecord("q7", 1, (1, 1, 2, 2), choices=("①",)),),
                                (FigureRecord("q7-graph", "q7", 1, (1, 2, 2, 3)),))
    actual = DocumentManifest(1, expected.items, expected.figures + expected.figures)
    passed, _ = compare_item_structure(expected, actual)
    assert not passed


def test_regression_three_column_solution_layout_is_part_of_structure() -> None:
    expected = DocumentManifest(1, (ItemRecord("solution-q1", 1, (1, 1, 2, 2), column=3),))
    actual = DocumentManifest(1, (ItemRecord("solution-q1", 1, (1, 1, 2, 2), column=1),))
    passed, detail = compare_item_structure(expected, actual)
    assert not passed and "column" in detail


def test_regression_image_inventory_requires_every_resource_mapping(tmp_path: Path) -> None:
    hwpx = tmp_path / "unmapped.hwpx"
    _hwpx(hwpx)
    audit = audit_hwpx_images(hwpx, [], page_size_px=(600, 450))
    assert not audit.passed
    assert audit.unclassified


def test_regression_header_and_body_capture_roles_fail_below_page_coverage_threshold(tmp_path: Path) -> None:
    for role in ("header_capture", "body_capture"):
        hwpx = tmp_path / f"{role}.hwpx"
        digest, _ = _hwpx(hwpx, width=200, height=40)
        audit = audit_hwpx_images(
            hwpx,
            [{"sha256": digest, "role": role, "item_id": "q1", "page": 1,
              "bbox": [0, 0, 200, 40], "contains_editable_content": True}],
            page_size_px=(600, 450),
        )
        assert not audit.passed
        assert audit.page_or_body_captures[0].classification == role


def test_regression_ocr_text_and_image_duplicate_fails_image_audit(tmp_path: Path) -> None:
    hwpx = tmp_path / "duplicate.hwpx"
    digest, _ = _hwpx(hwpx)
    audit = audit_hwpx_images(
        hwpx,
        [{"sha256": digest, "role": "figure", "item_id": "q1", "page": 1,
          "bbox": [10, 10, 30, 30], "duplicates_editable_text": True}],
        page_size_px=(600, 450),
    )
    assert not audit.passed
    assert audit.ocr_duplicates


def test_regression_same_figure_id_owned_by_wrong_item_fails_structure() -> None:
    expected, actual = _manifests()
    wrong = FigureRecord(actual.figures[0].figure_id, "q2", actual.figures[0].page, actual.figures[0].bbox)
    changed = DocumentManifest(
        actual.page_count,
        actual.items,
        (wrong,),
        actual.page_size_pt,
        actual.page_columns,
        True,
        True,
        True,
        True,
    )
    passed, detail = compare_item_structure(expected, changed)
    assert not passed
    assert "owner differs" in detail
