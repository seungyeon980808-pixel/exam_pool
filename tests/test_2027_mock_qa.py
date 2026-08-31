from __future__ import annotations

"""Synthetic, copyright-free QA contract for the 2027 mock-essay route."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.hwp_reference_style import StyleProfile
from app.hwp_style_qa import audit_style


ROOT = Path(__file__).parent
FIXTURE = ROOT / "fixtures" / "synthetic_math_style" / "2027_mock_qa_fixture.json"
PROFILE = ROOT.parent / "config" / "math_hwp_reference_style_v1.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _profile() -> StyleProfile:
    return StyleProfile.from_file(PROFILE)


def _snapshot(profile: StyleProfile) -> dict:
    return deepcopy(dict(profile.settings))


def _application(profile: StyleProfile, **changes: object) -> dict:
    value: dict[str, object] = {
        "status": "PASS",
        "passed": True,
        "applied": True,
        "profile_id": profile.profile_id,
        "profile_sha256": profile.profile_sha256,
    }
    value.update(changes)
    return value


def _layout(inventory: dict) -> dict:
    return {"pages": deepcopy(inventory["pages"])}


def _audit(
    generated: dict | None = None,
    *,
    application: dict | None = None,
    snapshot: dict | None = None,
    layout: dict | None = None,
) -> dict:
    profile = _profile()
    source = _fixture()
    actual = deepcopy(generated if generated is not None else source)
    return audit_style(
        source,
        actual,
        profile,
        style_snapshot=snapshot if snapshot is not None else _snapshot(profile),
        style_application=application if application is not None else _application(profile),
        layout_snapshot=layout if layout is not None else _layout(actual),
    )


def test_2027_problem_only_fixture_passes_with_v1_contract() -> None:
    report = _audit()

    assert report["status"] == "PASS", report
    assert report["profile_id"] == "수능완성_수학_문제해설_미주_기본서식_v1"
    assert report["counts"] == {
        "source_items": 2,
        "generated_items": 2,
        "source_endnotes": 0,
        "generated_endnotes": 0,
        "source_equations": 4,
        "generated_equations": 4,
    }
    assert all(value is True for value in report["gates"].values())
    assert report["findings"] == []


@pytest.mark.parametrize(
    ("mutation", "gate", "finding"),
    [
        ("equation", "equation_scripts_preserved", "CONTENT_HASH_MISMATCH"),
        ("reorder", "item_ids_preserved", "ITEM_ID_SET_OR_ORDER_MISMATCH"),
        ("image", "image_sha256_preserved", "CONTENT_HASH_MISMATCH"),
        ("endnote", "native_endnotes_preserved", "ENDNOTE_COUNT_MISMATCH"),
    ],
)
def test_2027_content_mutations_fail_closed(mutation: str, gate: str, finding: str) -> None:
    generated = _fixture()
    if mutation == "equation":
        generated["items"][0]["equation_scripts"][0] = "x^2+2"
        generated["document_equation_scripts"][0] = "x^2+2"
        generated["equation_style"][0]["script"] = "x^2+2"
    elif mutation == "reorder":
        generated["item_ids"] = list(reversed(generated["item_ids"]))
        generated["items"] = list(reversed(generated["items"]))
        generated["document_equation_scripts"] = list(reversed(generated["document_equation_scripts"]))
        generated["equation_style"] = list(reversed(generated["equation_style"]))
    elif mutation == "image":
        generated["items"][0]["images"][0]["sha256"] = "c" * 64
    elif mutation == "endnote":
        generated["endnote_count"] = 1
        generated["autonum_count"] = 1

    report = _audit(generated)

    assert report["status"] == "FAIL", report
    assert report["gates"][gate] is False
    assert any(row["code"] == finding for row in report["findings"])


def test_2027_native_equation_presentation_is_checked_independently() -> None:
    generated = _fixture()
    generated["equation_style"][0]["font"] = "WrongEquationFont"

    report = _audit(generated)

    assert report["status"] == "FAIL", report
    assert report["gates"]["equation_scripts_preserved"] is True
    assert report["gates"]["native_equation_style_exact"] is False
    assert any(row["code"] == "EQUATION_FONT_MISMATCH" for row in report["findings"])


def test_2027_single_column_long_formula_variant_requires_explicit_record() -> None:
    profile = _profile()
    generated = _fixture()
    generated["column_styles"][0].update({"count": "1", "gutter_hwpunits": "0"})
    snapshot = _snapshot(profile)
    snapshot["column"].update({"count": 1, "gutter_mm": 0.0, "gutter_hwpunits": 0})

    report = _audit(
        generated,
        application=_application(profile, column_variant="reference_long_formula_single_column"),
        snapshot=snapshot,
    )

    assert report["status"] == "PASS", report
    assert report["gates"]["native_page_column_style_exact"] is True


def test_2027_column_variant_without_verified_application_fails() -> None:
    generated = _fixture()
    generated["column_styles"][0].update({"count": "1", "gutter_hwpunits": "0"})

    report = _audit(generated)

    assert report["status"] == "FAIL", report
    assert report["gates"]["native_page_column_style_exact"] is False
    assert any(row["code"] == "NATIVE_COLUMN_STYLE_MISMATCH" for row in report["findings"])


def test_2027_application_proof_and_profile_hash_are_required() -> None:
    profile = _profile()
    report = _audit(application={"status": "PASS", "passed": True, "applied": True})

    assert report["status"] == "FAIL"
    assert report["gates"]["style_application_recorded"] is True
    assert report["gates"]["profile_hash_match"] is False
    assert any(row["code"] == "STYLE_PROFILE_HASH_MISMATCH" for row in report["findings"])
    assert report["profile_sha256"] == profile.profile_sha256


def test_2027_new_blank_paragraph_or_zero_width_spacing_fails() -> None:
    generated = _fixture()
    generated["blank_paragraphs"] = ["Contents/section0.xml:paragraph[99]"]
    generated["zero_width_strays"] = ["Contents/section0.xml:text[99]"]

    report = _audit(generated)

    assert report["status"] == "FAIL", report
    assert report["gates"]["no_blank_or_zero_width_spacing"] is False
    assert any(row["code"] == "BLANK_PARAGRAPH_CHANGED" for row in report["findings"])
    assert any(row["code"] == "ZERO_WIDTH_SPACING" for row in report["findings"])


def test_2027_layout_orphan_is_not_hidden_by_content_pass() -> None:
    generated = _fixture()
    layout = _layout(generated)
    layout["pages"][0]["objects"].append(
        {
            "object_id": "MOCK-SYN-NAT-ORPHAN",
            "kind": "figure",
            "owner_item_id": "MOCK-SYN-NAT-MISSING",
            "rect_mm": [20.0, 100.0, 40.0, 120.0],
        }
    )

    report = _audit(generated, layout=layout)

    assert report["status"] == "FAIL", report
    assert report["preservation"]["gates"]["content_hash_preserved"] is True
    assert report["gates"]["orphan_objects_absent"] is False
    assert any(row["code"] == "ORPHAN_OBJECT" for row in report["findings"])


@pytest.mark.parametrize(
    ("object_id", "rect", "gate", "finding"),
    [
        (
            "MOCK-SYN-NAT-Q002-body",
            [100.0, 20.0, 237.0, 80.0],
            "overlap_absent",
            "OBJECT_OVERLAP",
        ),
        (
            "MOCK-SYN-NAT-Q001-body",
            [-1.0, 20.0, 120.0, 80.0],
            "clipping_absent",
            "OBJECT_CLIPPED",
        ),
        (
            "MOCK-SYN-NAT-Q002-body",
            [137.0, 120.0, 237.0, 180.0],
            "large_gaps_absent",
            "LARGE_VERTICAL_GAP",
        ),
    ],
)
def test_2027_layout_overlap_clipping_and_gap_fail(
    object_id: str,
    rect: list[float],
    gate: str,
    finding: str,
) -> None:
    generated = _fixture()
    layout = _layout(generated)
    if gate == "large_gaps_absent":
        # A gap is evaluated within one owner/flow group; a single object
        # cannot itself prove a gap, so add a second object for Q002.
        layout["pages"][0]["objects"].append(
            {
                "object_id": "MOCK-SYN-NAT-Q002-gap",
                "kind": "paragraph",
                "owner_item_id": "MOCK-SYN-NAT-Q002",
                "rect_mm": rect,
            }
        )
    else:
        for row in layout["pages"][0]["objects"]:
            if row["object_id"] == object_id:
                row["rect_mm"] = rect
                break
        else:  # pragma: no cover - protects the fixture contract
            raise AssertionError(f"fixture object missing: {object_id}")

    report = _audit(generated, layout=layout)

    assert report["status"] == "FAIL", report
    assert report["gates"][gate] is False
    assert any(row["code"] == finding for row in report["findings"])
