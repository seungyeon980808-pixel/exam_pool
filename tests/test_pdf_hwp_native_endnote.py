from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.pdf_hwp_native_endnote import (
    CorrespondenceRecord,
    NativeEndnoteInventory,
    audit_native_endnotes,
    canonicalize_heading_numbers,
    inspect_hwpx_native_endnotes,
    normalized_text_hash,
    validate_correspondence,
)


def _hwpx(path: Path, *, native: bool = True, plain_marker: bool = False, outside_autonum: bool = False) -> None:
    if native:
        note = (
            '<hp:p><hp:run><hp:t>1. </hp:t><hp:ctrl>'
            '<hp:endNote number="1"><hp:subList><hp:p><hp:run><hp:ctrl>'
            '<hp:autoNum number="1" numType="ENDNOTE"/>'
            '</hp:ctrl><hp:t>1. 합성 해설 본문</hp:t></hp:run></hp:p></hp:subList></hp:endNote>'
            '</hp:ctrl></hp:run></hp:p>'
        )
    else:
        note = '<hp:p><hp:run><hp:t>[미주 1]</hp:t></hp:run></hp:p>' if plain_marker else ""
    outside = '<hp:autoNum number="99" numType="ENDNOTE"/>' if outside_autonum else ""
    xml = f'<hp:section xmlns:hp="urn:synthetic">{note}{outside}</hp:section>'
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("Contents/section0.xml", xml)


def gate(report, code: str):
    return next(item for item in report.gates if item.code == code)


def test_native_hwpx_structure_passes_and_ignores_outside_autonum(tmp_path: Path) -> None:
    path = tmp_path / "native.hwpx"
    _hwpx(path, outside_autonum=True)
    inventory = inspect_hwpx_native_endnotes(path, ("COMMON-01",))
    assert inventory.references == inventory.bodies == inventory.autonumbers == 1
    report = audit_native_endnotes(
        inventory,
        ("COMMON-01",),
        hwp_reopen=True,
        hwpx_reopen=True,
        copy_follows=True,
        move_follows=True,
    )
    assert report.passed


def test_plain_endnote_marker_is_not_native(tmp_path: Path) -> None:
    path = tmp_path / "plain.hwpx"
    _hwpx(path, native=False, plain_marker=True)
    inventory = inspect_hwpx_native_endnotes(path, ("COMMON-01",))
    assert inventory.references == inventory.bodies == inventory.autonumbers == 0
    assert inventory.plain_markers == 1
    report = audit_native_endnotes(
        inventory,
        ("COMMON-01",),
        hwp_reopen=True,
        hwpx_reopen=True,
        copy_follows=True,
        move_follows=True,
    )
    assert not report.passed
    assert not gate(report, "no_plain_text_endnote_fallback").passed


def test_endnote_reference_body_count_mismatch_is_fail() -> None:
    inventory = NativeEndnoteInventory(2, 1, 1, 0, ())
    report = audit_native_endnotes(
        inventory,
        ("COMMON-01",),
        hwp_reopen=True,
        hwpx_reopen=True,
        copy_follows=True,
        move_follows=True,
    )
    assert not report.passed
    assert not gate(report, "problem_solution_reference_body_count").passed


def test_copy_without_endnote_is_fail(tmp_path: Path) -> None:
    path = tmp_path / "native.hwpx"
    _hwpx(path)
    inventory = inspect_hwpx_native_endnotes(path, ("COMMON-01",))
    report = audit_native_endnotes(
        inventory,
        ("COMMON-01",),
        hwp_reopen=True,
        hwpx_reopen=True,
        copy_follows=False,
        move_follows=True,
    )
    assert not report.passed
    assert not gate(report, "copy_carries_endnote").passed


def test_elective_repeated_number_wrong_mapping_is_fail() -> None:
    first = "합성 확률 문항"
    records = (
        CorrespondenceRecord("synthetic", "PROBABILITY-23", 23, first, "합성 확률 해설", True),
        CorrespondenceRecord(
            "synthetic",
            "PROBABILITY-23",
            23,
            "합성 미적분 문항",
            "합성 미적분 해설",
            True,
            normalized_text_hash(first),
        ),
    )
    report = validate_correspondence(records, ("PROBABILITY-23", "CALCULUS-23"))
    assert not report.passed
    assert not gate(report, "correspondence_item_ids").passed
    assert not gate(report, "correspondence_first_sentences").passed


def test_worked_step_heading_is_capped_but_missing_real_heading_fails() -> None:
    assert canonicalize_heading_numbers((1, 1), ("COMMON-01",)) == ("COMMON-01",)
    try:
        canonicalize_heading_numbers((23,), ("PROBABILITY-23", "CALCULUS-23"))
    except ValueError as exc:
        assert "mismatch" in str(exc)
    else:
        raise AssertionError("missing elective occurrence must fail")

