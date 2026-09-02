from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from app.pdf_hwp_workflow import (
    AcceptanceStatus,
    EditableDocumentEvidence,
    RasterEvidence,
    audit_editable_workflow,
)


FIXTURE = Path(__file__).parent / "fixtures" / "pdf_hwp_end_to_end_synthetic.json"


def evidence() -> EditableDocumentEvidence:
    return EditableDocumentEvidence.from_mapping(json.loads(FIXTURE.read_text(encoding="utf-8")))


def gate(report, code: str):
    return next(item for item in report.gates if item.code == code)


def test_synthetic_baseline_is_strict_pass() -> None:
    source = evidence()
    report = audit_editable_workflow(source, source)
    assert report.status is AcceptanceStatus.STRICT_PASS
    assert all(item.passed for item in report.gates)


def test_regression_missing_q7_graph_is_fail() -> None:
    source = evidence()
    items = (replace(source.items[0], figure_ids=()), source.items[1])
    actual = replace(source, items=items, figures=())
    report = audit_editable_workflow(source, actual)
    assert report.status is AcceptanceStatus.FAIL
    assert not gate(report, "figure_inventory").passed
    assert not gate(report, "view_table_figure_inventory").passed


def test_regression_figure_assigned_to_wrong_item_is_fail() -> None:
    source = evidence()
    actual_figure = replace(source.figures[0], item_id="COMMON-08")
    report = audit_editable_workflow(source, replace(source, figures=(actual_figure,)))
    assert report.status is AcceptanceStatus.FAIL
    assert not gate(report, "figure_assignment").passed


def test_regression_header_capture_is_forbidden_at_any_size() -> None:
    source = evidence()
    capture = RasterEvidence("BinData/header.png", "header_capture", page=1, bbox=(0, 0, 240, 20))
    report = audit_editable_workflow(source, replace(source, rasters=(capture,)))
    assert report.status is AcceptanceStatus.FAIL
    assert not gate(report, "no_page_header_body_item_capture").passed


def test_regression_five_page_solution_reflowed_to_thirty_is_not_practical() -> None:
    source = evidence()
    actual = replace(source, page_count=30, page_size_pt=source.page_size_pt * 6, page_columns=source.page_columns * 6)
    report = audit_editable_workflow(source, actual)
    assert report.status is AcceptanceStatus.FAIL
    assert not gate(report, "page_count").passed
    assert gate(report, "page_count").practical_required


def test_regression_solution_body_replaced_by_region_image_is_fail() -> None:
    source = evidence()
    item = replace(source.items[0], body_text="7.", native_text=False, body_replaced_by_image=True)
    body = RasterEvidence(
        "BinData/body.png",
        "body_capture",
        page=2,
        item_id="COMMON-07",
        bbox=(20, 20, 110, 160),
        contains_editable_content=True,
    )
    report = audit_editable_workflow(source, replace(source, items=(item, source.items[1]), rasters=(body,)))
    assert report.status is AcceptanceStatus.FAIL
    assert not gate(report, "item_body_present").passed
    assert not gate(report, "native_editability").passed
    assert not gate(report, "no_page_header_body_item_capture").passed


def test_regression_ocr_and_image_duplicate_is_fail() -> None:
    source = evidence()
    item = replace(source.items[0], ocr_image_duplicate=True)
    duplicate = RasterEvidence(
        "BinData/duplicate.png",
        "figure",
        page=2,
        item_id="COMMON-07",
        bbox=(20, 20, 110, 60),
        duplicates_editable_text=True,
    )
    report = audit_editable_workflow(source, replace(source, items=(item, source.items[1]), rasters=(duplicate,)))
    assert report.status is AcceptanceStatus.FAIL
    assert not gate(report, "no_ocr_image_duplication").passed
    assert not gate(report, "no_image_text_duplicate").passed


def test_regression_item_number_without_body_is_fail() -> None:
    source = evidence()
    item = replace(source.items[0], body_text="7.")
    report = audit_editable_workflow(source, replace(source, items=(item, source.items[1])))
    assert report.status is AcceptanceStatus.FAIL
    assert not gate(report, "item_body_present").passed


def test_regression_full_page_image_is_fail() -> None:
    source = evidence()
    page = RasterEvidence("BinData/page.png", "page_capture", page=1, bbox=(0, 0, 240, 180))
    report = audit_editable_workflow(source, replace(source, rasters=(page,)))
    assert report.status is AcceptanceStatus.FAIL
    assert not gate(report, "no_page_header_body_item_capture").passed


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("number", "g(x)=x^2+3/7"),
        ("sign", "g(x)=-x^2+2/7"),
        ("exponent", "g(x)=x^3+2/7"),
        ("fraction", "g(x)=x^2+2/9"),
    ),
)
def test_regression_changed_math_token_is_fail(field: str, replacement: str) -> None:
    source = evidence()
    item = replace(source.items[0], formulas=(replacement,))
    report = audit_editable_workflow(source, replace(source, items=(item, source.items[1])))
    assert report.status is AcceptanceStatus.FAIL, field
    assert not gate(report, "numeric_formula_choice_tokens").passed


def test_limited_raster_figure_is_practical_only_with_reason() -> None:
    source = evidence()
    figure = replace(source.figures[0], raster=True, exception_reason="pure synthetic graph")
    report = audit_editable_workflow(source, replace(source, figures=(figure,)))
    assert report.status is AcceptanceStatus.PRACTICAL_PASS_WITH_EXCEPTIONS
    assert not gate(report, "all_figures_native_vector").passed
    assert gate(report, "raster_exception_ledger").passed


def test_pre_endnote_document_rejects_any_note_marker() -> None:
    source = evidence()
    report = audit_editable_workflow(source, replace(source, plain_endnote_marker_count=1))
    assert report.status is AcceptanceStatus.FAIL
    assert not gate(report, "pre_endnote_document_has_no_notes").passed

