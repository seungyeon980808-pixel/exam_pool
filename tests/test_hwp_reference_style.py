from __future__ import annotations

"""Synthetic, copyright-free tests for the reference style contract."""

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.hwp_reference_style import (
    STYLE_KINDS,
    StyleApplicationError,
    StyleProfileError,
    StyleProfile,
    _equation_offset_guards,
    apply_reference_style,
    canonical_sha256,
    content_manifest,
    preflight_reference_style,
    preserved_content_gate,
)
from app.hwp_style_qa import StyleQAError, audit_style


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "synthetic_math_style"


def _profile_mapping() -> dict:
    value = json.loads((FIXTURE_ROOT / "profile-example.json").read_text(encoding="utf-8"))
    return value


def _profile(*, measured: bool = True) -> StyleProfile:
    value = _profile_mapping()
    value["measured"] = measured
    value["status"] = "VERIFIED" if measured else "DRAFT_PENDING_REFERENCE_ANALYSIS"
    value["reference_measurement_status"] = "verified" if measured else "pending"
    value["reference_source"] = "synthetic://math-style-fixture" if measured else None
    return StyleProfile.from_mapping(value)


class RecordingDocument:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.content = {
            "items": [
                {
                    "item_id": "SYN-Q001",
                    "equation_scripts": ["x over {2}"],
                    "tables": [{"cells": ["a", "b"]}],
                    "images": [{"id": "fig-1", "sha256": "a" * 64}],
                }
            ]
        }

    def capture_content(self):
        return deepcopy(self.content)

    def apply_style(self, kind, settings):
        assert kind in STYLE_KINDS
        assert isinstance(settings, dict)
        self.calls.append(kind)
        return True


def _section_xml(
    *,
    zero_width: bool = False,
    valid_guard: bool = False,
    guard_visible_tail: bool = False,
    blank_paragraph: bool = False,
    unlabelled_note: bool = False,
) -> str:
    guard = "\u200b\u200b" if zero_width else ""
    notes: list[str] = []
    for number in (1, 2):
        before = f'<hp:t>{guard}</hp:t>' if guard and not valid_guard else ""
        after_tail = " continuation" if guard_visible_tail else ""
        after = f'<hp:t>{guard}{after_tail}</hp:t>' if guard and valid_guard else ""
        note_label = "answer" if unlabelled_note else f"ENDNOTE:SYN-Q00{number} answer"
        notes.append(
            f'<hp:fieldBegin name="ENDNOTE:SYN-Q00{number}"/>'
            f'<hp:endNote><hp:p><hp:t>{note_label}</hp:t></hp:p>'
            + before
            + '<hp:equation font="HancomEQN" baseUnit="1100"><hp:pos treatAsChar="1" flowWithText="1" allowOverlap="0"/><hp:script>x over {2}</hp:script></hp:equation>'
            + after
            + '<hp:tbl><hp:tr><hp:tc><hp:t>cell-a</hp:t></hp:tc><hp:tc><hp:t>cell-b</hp:t></hp:tc></hp:tr></hp:tbl>'
            + '<hp:pic><hc:img binaryItemIDRef="BIN0001"/></hp:pic>'
            + '<hp:autoNum numType="ENDNOTE"/></hp:endNote>'
        )
    return (
        '<hs:sec xmlns:hs="urn:synthetic:section" '
        'xmlns:hp="urn:synthetic:hp" xmlns:hc="urn:synthetic:hc">'
        '<hp:secPr><hp:pagePr landscape="WIDELY" width="59528" height="84189">'
        '<hp:margin left="5669" right="5669" top="5102" bottom="5102" header="2268" footer="2268"/>'
        '</hp:pagePr><hp:colPr colCount="1" sameGap="0" sameSz="1"/></hp:secPr>'
        + ('<hp:p><hp:run><hp:t></hp:t></hp:run></hp:p>' if blank_paragraph else '')
        + "".join(notes)
        + "</hs:sec>"
    )


def _write_hwpx(
    path: Path,
    *,
    payload: bytes = b"synthetic-figure",
    zero_width: bool = False,
    valid_guard: bool = False,
    guard_visible_tail: bool = False,
    blank_paragraph: bool = False,
    unlabelled_note: bool = False,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "Contents/section0.xml",
            _section_xml(
                zero_width=zero_width,
                valid_guard=valid_guard,
                guard_visible_tail=guard_visible_tail,
                blank_paragraph=blank_paragraph,
                unlabelled_note=unlabelled_note,
            ),
        )
        archive.writestr("BinData/BIN0001.png", payload)
    return path


def _replace_section(path: Path, old: str, new: str) -> None:
    with ZipFile(path, "r") as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    section = members["Contents/section0.xml"].decode("utf-8")
    assert old in section
    members["Contents/section0.xml"] = section.replace(old, new).encode("utf-8")
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


def _snapshot(profile: StyleProfile) -> dict:
    return deepcopy(dict(profile.settings))


def _layout() -> dict:
    return {
        "pages": [
            {
                "page": 1,
                "width_mm": 210.0,
                "height_mm": 297.0,
                "density": 0.20,
                "objects": [
                    {"object_id": "item-1", "kind": "item", "owner_item_id": "SYN-Q001", "rect_mm": [10.0, 20.0, 90.0, 40.0]},
                    {"object_id": "item-2", "kind": "item", "owner_item_id": "SYN-Q002", "rect_mm": [110.0, 20.0, 200.0, 40.0]},
                ],
            }
        ]
    }


def _application(profile: StyleProfile, **changes) -> dict:
    value = {"status": "PASS", "passed": True, "applied": True, "profile_sha256": profile.profile_sha256}
    value.update(changes)
    return value


def test_draft_profile_preflight_fails_without_inventing_measurements() -> None:
    draft = _profile(measured=False)
    result = preflight_reference_style(draft)
    assert result["status"] == "FAIL"
    assert result["gates"]["measured_reference_profile"] is False
    assert draft.raw["reference_source"] is None
    assert len(draft.profile_sha256) == 64


def test_profile_rejects_non_boolean_measured_flag() -> None:
    value = _profile_mapping()
    value["measured"] = "true"
    with pytest.raises(StyleProfileError, match="measured must be a boolean"):
        StyleProfile.from_mapping(value)


def test_measured_profile_requires_verified_status() -> None:
    value = _profile_mapping()
    value["status"] = "DRAFT"
    profile = StyleProfile.from_mapping(value)
    result = preflight_reference_style(profile)
    assert result["status"] == "FAIL"
    assert result["gates"]["measured_reference_profile"] is False


def test_preflight_rejects_empty_style_family() -> None:
    value = _profile_mapping()
    value["styles"]["figure"] = {}
    profile = StyleProfile.from_mapping(value)
    result = preflight_reference_style(profile)
    assert result["status"] == "FAIL"
    assert result["gates"]["style_families_complete"] is False


def test_style_application_preserves_native_content_and_records_hash() -> None:
    document = RecordingDocument()
    profile = _profile()
    result = apply_reference_style(document, profile)
    assert result["status"] == "PASS"
    assert result["passed"] is True
    assert result["applied"] is True
    assert result["profile_sha256"] == profile.profile_sha256
    assert document.calls == list(STYLE_KINDS)
    assert result["preservation"]["gates"]["equation_offset_guards_preserved"] is True


def test_style_application_allows_unchanged_approved_layout_paragraph() -> None:
    document = RecordingDocument()
    document.content["blank_paragraphs"] = ["synthetic:blank-1"]
    result = apply_reference_style(document, _profile())
    assert result["status"] == "PASS"
    assert result["gates"]["no_blank_or_zero_width_spacing"] is True


def test_style_application_rejects_new_blank_paragraph_spacing() -> None:
    class BlankAddingDocument(RecordingDocument):
        def apply_style(self, kind, settings):
            result = super().apply_style(kind, settings)
            if kind == "page":
                self.content["blank_paragraphs"] = ["synthetic:new-blank"]
            return result

    result = apply_reference_style(BlankAddingDocument(), _profile())
    assert result["status"] == "FAIL"
    assert result["gates"]["no_blank_or_zero_width_spacing"] is False


def test_preserved_content_gate_detects_equation_table_and_image_changes() -> None:
    before = content_manifest(
        {"items": [{"item_id": "SYN-Q001", "equations": ["x"], "tables": [{"cells": ["a"]}], "images": [{"sha256": "a" * 64}]}]}
    )
    after = content_manifest(
        {"items": [{"item_id": "SYN-Q001", "equations": ["y"], "tables": [{"cells": ["b"]}], "images": [{"sha256": "b" * 64}]}]}
    )
    result = preserved_content_gate(before, after)
    assert result["status"] == "FAIL"
    assert {"equation_scripts_sha256", "table_cells_sha256", "image_sha256", "content_sha256"}.issubset(result["mismatches"])


def test_preserved_content_gate_detects_item_reordering() -> None:
    before = content_manifest(
        {"items": [{"item_id": "SYN-Q001"}, {"item_id": "SYN-Q002"}]}
    )
    after = content_manifest(
        {"items": [{"item_id": "SYN-Q002"}, {"item_id": "SYN-Q001"}]}
    )
    result = preserved_content_gate(before, after)
    assert result["status"] == "FAIL"
    assert "item_ids_sha256" in result["mismatches"]


def test_equation_offset_guard_rejects_non_u200b_character() -> None:
    guards, errors = _equation_offset_guards(
        {
            "zero_width_guards": [
                {
                    "length": 1,
                    "adjacent": True,
                    "visible_text": False,
                    "character": "U+200C",
                }
            ]
        }
    )
    assert guards
    assert any("U+200B" in error for error in errors)


def test_content_manifest_fails_closed_for_malformed_collections() -> None:
    manifest = content_manifest(
        {
            "items": [
                {
                    "item_id": "SYN-Q001",
                    "equations": None,
                    "tables": "not-a-list",
                    "images": "not-a-list",
                }
            ],
            "equation_scripts": "not-a-list",
        }
    )
    assert any("tables must be a list" in error for error in manifest["errors"])
    assert any("images must be a list" in error for error in manifest["errors"])
    assert any("document equations must be a list" in error for error in manifest["errors"])


def test_hwpx_style_qa_passes_with_native_content_and_layout(tmp_path: Path) -> None:
    profile = _profile()
    source = _write_hwpx(tmp_path / "source.hwpx")
    generated = _write_hwpx(tmp_path / "generated.hwpx")
    report = audit_style(
        source,
        generated,
        profile,
        style_snapshot=_snapshot(profile),
        style_application=_application(profile),
        layout_snapshot=_layout(),
    )
    assert report["status"] == "PASS", report
    assert report["passed"] is True
    assert all(value is True for value in report["gates"].values())
    assert report["counts"]["source_items"] == 2
    assert report["counts"]["source_endnotes"] == 2
    assert report["counts"]["source_equations"] == 2
    assert report["profile_sha256"] == profile.profile_sha256


def test_hwpx_style_qa_rejects_image_sha_change(tmp_path: Path) -> None:
    profile = _profile()
    source = _write_hwpx(tmp_path / "source.hwpx", payload=b"figure-a")
    generated = _write_hwpx(tmp_path / "generated.hwpx", payload=b"figure-b")
    report = audit_style(
        source,
        generated,
        profile,
        style_snapshot=_snapshot(profile),
        style_application=_application(profile),
        layout_snapshot=_layout(),
    )
    assert report["status"] == "FAIL"
    assert any(row["code"] == "CONTENT_HASH_MISMATCH" and row["field"] == "image_sha256" for row in report["findings"])
    assert report["gates"]["image_sha256_preserved"] is False


def test_hwpx_style_qa_rejects_native_equation_presentation_change(tmp_path: Path) -> None:
    profile = _profile()
    source = _write_hwpx(tmp_path / "source.hwpx")
    generated = _write_hwpx(tmp_path / "generated.hwpx")
    _replace_section(generated, 'font="HancomEQN"', 'font="WrongEquationFont"')
    report = audit_style(
        source,
        generated,
        profile,
        style_snapshot=_snapshot(profile),
        style_application=_application(profile),
        layout_snapshot=_layout(),
    )
    assert report["status"] == "FAIL"
    assert report["gates"]["native_equation_style_exact"] is False
    assert any(row["code"] == "EQUATION_FONT_MISMATCH" for row in report["findings"])


def test_hwpx_style_qa_rejects_unapproved_native_column_change(tmp_path: Path) -> None:
    profile = _profile()
    source = _write_hwpx(tmp_path / "source.hwpx")
    generated = _write_hwpx(tmp_path / "generated.hwpx")
    _replace_section(generated, 'colCount="1" sameGap="0"', 'colCount="2" sameGap="2268"')
    report = audit_style(
        source,
        generated,
        profile,
        style_snapshot=_snapshot(profile),
        style_application=_application(profile),
        layout_snapshot=_layout(),
    )
    assert report["status"] == "FAIL"
    assert report["gates"]["native_page_column_style_exact"] is False
    assert any(row["code"] == "NATIVE_COLUMN_STYLE_MISMATCH" for row in report["findings"])


def test_hwpx_style_qa_accepts_explicit_verified_single_column_variant(tmp_path: Path) -> None:
    mapping = _profile_mapping()
    mapping["styles"]["column"].update(
        {
            "count": 2,
            "gutter_mm": 8.0,
            "gutter_hwpunits": 2268,
            "verified_variants": {
                "reference_default_two_column": {"count": 2, "gutter_mm": 8.0, "gutter_hwpunits": 2268},
                "reference_long_formula_single_column": {"count": 1, "gutter_mm": 0.0, "gutter_hwpunits": 0},
            },
        }
    )
    profile = StyleProfile.from_mapping(mapping)
    source = _write_hwpx(tmp_path / "source.hwpx")
    generated = _write_hwpx(tmp_path / "generated.hwpx")
    snapshot = _snapshot(profile)
    snapshot["column"].update({"count": 1, "gutter_mm": 0.0, "gutter_hwpunits": 0})
    report = audit_style(
        source,
        generated,
        profile,
        style_snapshot=snapshot,
        style_application=_application(profile, column_variant="reference_long_formula_single_column"),
        layout_snapshot=_layout(),
    )
    assert report["status"] == "PASS", report
    assert report["gates"]["native_page_column_style_exact"] is True


def test_hwpx_style_qa_rejects_unclassified_zero_width(tmp_path: Path) -> None:
    profile = _profile()
    source = _write_hwpx(tmp_path / "source.hwpx", zero_width=True)
    generated = _write_hwpx(tmp_path / "generated.hwpx", zero_width=True)
    report = audit_style(
        source,
        generated,
        profile,
        style_snapshot=_snapshot(profile),
        style_application=_application(profile),
        layout_snapshot=_layout(),
    )
    # The synthetic guard is not adjacent to an equation in the same run, so
    # it is intentionally classified as a spacing defect.
    assert report["status"] == "FAIL"
    assert any(row["code"] == "ZERO_WIDTH_SPACING" for row in report["findings"])
    assert report["gates"]["no_blank_or_zero_width_spacing"] is False


def test_hwpx_style_qa_allows_only_valid_equation_offset_guard(tmp_path: Path) -> None:
    profile = _profile()
    source = _write_hwpx(tmp_path / "source.hwpx", zero_width=True, valid_guard=True)
    generated = _write_hwpx(tmp_path / "generated.hwpx", zero_width=True, valid_guard=True)
    report = audit_style(
        source,
        generated,
        profile,
        style_snapshot=_snapshot(profile),
        style_application=_application(profile),
        layout_snapshot=_layout(),
    )
    assert report["status"] == "PASS", report
    assert report["gates"]["no_blank_or_zero_width_spacing"] is True
    assert report["preservation"]["gates"]["equation_offset_guards_preserved"] is True


def test_hwpx_style_qa_allows_guard_prefix_with_visible_continuation(tmp_path: Path) -> None:
    profile = _profile()
    source = _write_hwpx(
        tmp_path / "source.hwpx",
        zero_width=True,
        valid_guard=True,
        guard_visible_tail=True,
        unlabelled_note=True,
    )
    generated = _write_hwpx(
        tmp_path / "generated.hwpx",
        zero_width=True,
        valid_guard=True,
        guard_visible_tail=True,
        unlabelled_note=True,
    )
    report = audit_style(
        source,
        generated,
        profile,
        style_snapshot=_snapshot(profile),
        style_application=_application(profile),
        layout_snapshot=_layout(),
    )
    assert report["status"] == "PASS", report
    assert report["counts"]["source_items"] == 2
    assert report["counts"]["source_equations"] == 2
    assert report["gates"]["no_blank_or_zero_width_spacing"] is True


def test_hwpx_style_qa_allows_exactly_preserved_layout_paragraph(tmp_path: Path) -> None:
    profile = _profile()
    source = _write_hwpx(tmp_path / "source.hwpx", blank_paragraph=True)
    generated = _write_hwpx(tmp_path / "generated.hwpx", blank_paragraph=True)
    report = audit_style(
        source,
        generated,
        profile,
        style_snapshot=_snapshot(profile),
        style_application=_application(profile),
        layout_snapshot=_layout(),
    )
    assert report["status"] == "PASS", report
    assert report["gates"]["no_blank_or_zero_width_spacing"] is True


def test_hwpx_style_qa_rejects_new_layout_paragraph(tmp_path: Path) -> None:
    profile = _profile()
    source = _write_hwpx(tmp_path / "source.hwpx")
    generated = _write_hwpx(tmp_path / "generated.hwpx", blank_paragraph=True)
    report = audit_style(
        source,
        generated,
        profile,
        style_snapshot=_snapshot(profile),
        style_application=_application(profile),
        layout_snapshot=_layout(),
    )
    assert report["status"] == "FAIL"
    assert report["gates"]["no_blank_or_zero_width_spacing"] is False
    assert any(row["code"] == "BLANK_PARAGRAPH_CHANGED" for row in report["findings"])


def test_hwpx_style_qa_requires_application_record_and_hash(tmp_path: Path) -> None:
    profile = _profile()
    source = _write_hwpx(tmp_path / "source.hwpx")
    generated = _write_hwpx(tmp_path / "generated.hwpx")
    report = audit_style(source, generated, profile, style_snapshot=_snapshot(profile), layout_snapshot=_layout())
    assert report["status"] == "FAIL"
    assert report["gates"]["style_application_recorded"] is False
    assert report["gates"]["profile_hash_match"] is False
    assert any(row["code"] == "STYLE_APPLICATION_NOT_PROVEN" for row in report["findings"])


def test_layout_gate_rejects_orphan_object(tmp_path: Path) -> None:
    profile = _profile()
    source = _write_hwpx(tmp_path / "source.hwpx")
    generated = _write_hwpx(tmp_path / "generated.hwpx")
    layout = _layout()
    layout["pages"][0]["objects"].append({"object_id": "orphan", "kind": "figure", "owner_item_id": "MISSING", "rect_mm": [10.0, 50.0, 20.0, 60.0]})
    report = audit_style(
        source,
        generated,
        profile,
        style_snapshot=_snapshot(profile),
        style_application=_application(profile),
        layout_snapshot=layout,
    )
    assert report["status"] == "FAIL"
    assert report["gates"]["orphan_objects_absent"] is False
    assert any(row["code"] == "ORPHAN_OBJECT" for row in report["findings"])


def test_layout_gate_rejects_overlap_clipping_and_large_gap(tmp_path: Path) -> None:
    profile = _profile()
    source = _write_hwpx(tmp_path / "source.hwpx")
    generated = _write_hwpx(tmp_path / "generated.hwpx")
    layout = _layout()
    layout["pages"][0]["objects"].extend(
        [
            {"object_id": "overlap", "kind": "paragraph", "owner_item_id": "SYN-Q001", "rect_mm": [10.0, 20.0, 90.0, 40.0]},
            {"object_id": "clipped", "kind": "paragraph", "owner_item_id": "SYN-Q001", "rect_mm": [1.0, 280.0, 220.0, 300.0]},
            {"object_id": "gap", "kind": "paragraph", "owner_item_id": "SYN-Q001", "rect_mm": [10.0, 80.0, 20.0, 90.0]},
        ]
    )
    report = audit_style(
        source,
        generated,
        profile,
        style_snapshot=_snapshot(profile),
        style_application=_application(profile),
        layout_snapshot=layout,
    )
    assert report["status"] == "FAIL"
    assert report["gates"]["overlap_absent"] is False
    assert report["gates"]["clipping_absent"] is False
    assert report["gates"]["large_gaps_absent"] is False


def test_verified_profile_file_matches_measured_reference_contract() -> None:
    profile = StyleProfile.from_file(Path(__file__).parents[1] / "config" / "math_hwp_reference_style_v1.json")
    assert profile.measured is True
    assert profile.status == "VERIFIED"
    assert preflight_reference_style(profile)["status"] == "PASS"
    assert profile.settings["page"]["width_hwpunits"] == 72852
    assert profile.settings["page"]["height_hwpunits"] == 103180
    assert profile.settings["column"]["count"] == 2
    assert profile.settings["column"]["gutter_hwpunits"] == 2268
    assert profile.settings["column"]["verified_variants"]["reference_long_formula_single_column"] == {
        "count": 1,
        "gutter_mm": 0.0,
        "gutter_hwpunits": 0,
    }
    assert profile.settings["char"]["body"]["font_family"] == "함초롬돋움"
    assert profile.settings["char"]["body"]["size_pt"] == 11.0
    assert profile.settings["char"]["equation"] == {
        "allow_overlap": "0",
        "base_unit": 1100,
        "flow_with_text": "1",
        "font_family": "HYhwpEQ",
        "script_must_remain_unchanged": True,
        "size_pt": 11.0,
        "treat_as_char": "1",
    }
    assert canonical_sha256(profile.raw) == profile.profile_sha256
