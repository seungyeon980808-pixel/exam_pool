"""Copyright-free synthetic regression tests for semantic HWP reconstruction."""

from __future__ import annotations

import json
from pathlib import Path

from app.pdf_hwp_semantic_reconstruction import (
    ENDNOTE_MODE,
    FAIL_CODES,
    SCHEMA_VERSION,
    can_build_native_endnotes,
    load_and_validate,
    validate_semantic_reconstruction,
)


def _paragraph(text: str = "합성 문항의 본문입니다.", *, role: str = "body") -> dict:
    return {
        "role": role,
        "text": text,
        "origin": "semantic",
        "review_status": "VERIFIED",
        "sentence_status": "COMPLETE",
        "font_family": "함초롬돋움",
        "font_size_pt": 11.0,
        "line_spacing_percent": 160.0,
        "space_before_pt": 0.0,
        "space_after_pt": 2.0,
        "alignment": "justify",
        "justify_stretch": False,
    }


def _item(item_id: str, number: int, *, column: int = 1) -> dict:
    return {
        "item_id": item_id,
        "section_id": "SYNTHETIC-SECTION",
        "printed_number": number,
        "problem_region_ids": [f"P-{item_id}"],
        "solution_region_ids": [f"S-{item_id}"],
        "mapping_evidence": ["section", "printed_label", "problem_first_sentence", "solution_content"],
        "ocr_review_status": "VERIFIED",
        "source_mode": "semantic_item_reflow",
        "source_column": column,
        "output_column": column,
        "semantic_fields": {
            "stem": {"text": "합성 문항의 조건을 읽는다.", "sentence_status": "COMPLETE"},
            "ask": {"text": "옳은 것을 고른다.", "sentence_status": "COMPLETE"},
        },
        "paragraphs": [_paragraph()],
        "choices": {
            "kind": "native_choice",
            "items": ["① 합성 선택지", "② 합성 선택지", "③ 합성 선택지", "④ 합성 선택지", "⑤ 합성 선택지"],
            "expected_count": 5,
            "expected_layout": "single_column",
            "actual_layout": "single_column",
        },
        "condition_box": {"required": True, "kind": "native_table", "native": True},
        "tables": [{"role": "condition", "kind": "native_table", "native": True}],
        "figures": [{"figure_id": f"FIG-{item_id}", "owner_item_id": item_id, "kind": "figure", "content_role": "pure_figure", "contains_text": False}],
        "expected_formula_count": 1,
        "formulas": [{"kind": "native_equation", "native": True, "font_family": "HYhwpEQ", "font_size_pt": 11.0, "base_unit": 1100, "script": "x+1"}],
    }


def _manifest() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "VERIFIED",
        "uncertainties": [],
        "source_mode": "semantic_item_reflow",
        "endnote_mode": ENDNOTE_MODE,
        "pages": [
            {"pdf_page": 1, "role": "frontmatter", "included": False, "item_ids": []},
            {"pdf_page": 2, "role": "mixed", "included": True, "item_ids": ["SYN-Q-001"]},
            {"pdf_page": 3, "role": "solution", "included": True, "item_ids": ["SYN-Q-001"]},
        ],
        "items": [_item("SYN-Q-001", 1)],
        "formula_chain": {
            "source_reviewed_count": 1,
            "hwpx_count": 1,
            "hwp_count": 1,
            "com_count": 1,
            "rendered_pdf_count": 1,
            "font_family": "HYhwpEQ",
            "base_unit": 1100,
        },
        "pre_endnote_checkpoint": {
            "status": "PASS",
            "stage": "pre_endnote_editable",
            "artifact_sha256": "a" * 64,
        },
        "reopen_render_qa": {
            "hwp_reopen": True,
            "hwpx_reopen": True,
            "com_reopen": True,
            "rendered_pdf": True,
            "visual_review": True,
        },
    }


def _codes(report: dict) -> set[str]:
    return {str(row["code"]) for row in report["findings"]}


def test_clean_semantic_reconstruction_passes_and_allows_endnotes() -> None:
    report = validate_semantic_reconstruction(_manifest())
    assert report["status"] == "PASS"
    assert report["counts"] == {"items": 1, "figures": 1, "expected_formulas": 1, "findings": 0}
    assert can_build_native_endnotes(_manifest()) is True


def test_physical_rows_are_not_hwp_paragraphs() -> None:
    value = _manifest()
    value["items"][0]["source_mode"] = "physical_ocr_rows"
    value["items"][0]["paragraphs"][0]["origin"] = "physical_ocr_row"
    assert "PHYSICAL_OCR_ROW_SPLIT" in _codes(validate_semantic_reconstruction(value))


def test_body_justify_stretch_and_fragmentation_fail() -> None:
    value = _manifest()
    value["items"][0]["paragraphs"][0]["justify_stretch"] = True
    value["items"][0]["semantic_fields"]["stem"]["sentence_status"] = "FRAGMENT"
    codes = _codes(validate_semantic_reconstruction(value))
    assert {"BODY_JUSTIFY_STRETCH", "SENTENCE_FRAGMENTATION"} <= codes


def test_short_particle_ending_without_terminal_punctuation_is_a_fragment() -> None:
    value = _manifest()
    value["items"][0]["semantic_fields"]["stem"]["text"] = "조건을"
    assert "SENTENCE_FRAGMENTATION" in _codes(validate_semantic_reconstruction(value))


def test_native_choice_condition_table_and_column_contracts_fail_independently() -> None:
    value = _manifest()
    value["items"][0]["choices"]["actual_layout"] = "two_column"
    value["items"][0]["condition_box"]["kind"] = "raster_capture"
    value["items"][0]["tables"][0]["kind"] = "image"
    value["items"][0]["output_column"] = 2
    codes = _codes(validate_semantic_reconstruction(value))
    assert {"CHOICE_LAYOUT_MISMATCH", "CONDITION_BOX_NOT_NATIVE", "ITEM_COLUMN_MISMATCH"} <= codes


def test_open_response_item_without_choices_is_valid() -> None:
    value = _manifest()
    value["items"][0].pop("choices")
    value["items"][0]["expected_choice_count"] = 0
    assert validate_semantic_reconstruction(value)["status"] == "PASS"


def test_figure_ownership_and_capture_images_fail() -> None:
    value = _manifest()
    value["items"][0]["figures"][0]["owner_item_id"] = "SYN-Q-999"
    value["items"][0]["figures"].append({"figure_id": "FIG-CAPTURE", "owner_item_id": "SYN-Q-001", "kind": "question_capture"})
    codes = _codes(validate_semantic_reconstruction(value))
    assert {"FIGURE_OWNERSHIP_MISMATCH", "FORBIDDEN_CAPTURE_IMAGE"} <= codes


def test_style_ocr_formula_and_release_gates_fail_closed() -> None:
    value = _manifest()
    value["items"][0]["ocr_review_status"] = "UNREVIEWED"
    value["items"][0]["paragraphs"][0]["space_after_pt"] = 9.0
    value["items"][0]["formulas"][0]["native"] = False
    value["items"][0]["formulas"][0]["script"] = ""
    value["formula_chain"]["com_count"] = 0
    value["pre_endnote_checkpoint"]["status"] = "FAIL"
    value["reopen_render_qa"]["hwpx_reopen"] = False
    codes = _codes(validate_semantic_reconstruction(value))
    assert {"OCR_UNREVIEWED", "PARAGRAPH_SPACING_OUT_OF_PROFILE", "FORMULA_NATIVE_MISSING", "PRE_ENDNOTE_CHECKPOINT_REQUIRED", "REOPEN_RENDER_QA_MISSING"} <= codes
    assert can_build_native_endnotes(value) is False


def test_non_item_page_and_mapping_are_not_silent() -> None:
    value = _manifest()
    value["pages"][0]["included"] = True
    value["items"][0]["solution_region_ids"] = []
    value["items"][0]["mapping_evidence"] = ["printed_label"]
    codes = _codes(validate_semantic_reconstruction(value))
    assert {"NON_ITEM_PAGE_INCLUDED", "ITEM_SOLUTION_MAPPING_MISMATCH"} <= codes


def test_source_policy_exposes_required_codes_and_json_loader(tmp_path: Path) -> None:
    assert {
        "PHYSICAL_OCR_ROW_SPLIT", "BODY_JUSTIFY_STRETCH", "SENTENCE_FRAGMENTATION",
        "CHOICE_LAYOUT_MISMATCH", "CONDITION_BOX_NOT_NATIVE", "ITEM_COLUMN_MISMATCH",
        "FIGURE_OWNERSHIP_MISMATCH", "PARAGRAPH_SPACING_OUT_OF_PROFILE", "OCR_UNREVIEWED",
        "FORMULA_NATIVE_MISSING",
    } <= FAIL_CODES
    manifest = tmp_path / "semantic.json"
    manifest.write_text(json.dumps(_manifest(), ensure_ascii=False), encoding="utf-8")
    assert load_and_validate(manifest)["status"] == "PASS"
    policy = json.loads((Path(__file__).parents[1] / "config" / "pdf_hwp_semantic_reconstruction_policy_v1.json").read_text(encoding="utf-8"))
    assert policy["endnote_mode"] == ENDNOTE_MODE
    assert policy["endnotes"]["pre_endnote_checkpoint_required"] is True
    assert policy["equations"]["font_family"] == "HYhwpEQ"
    assert policy["equations"]["base_unit"] == 1100
