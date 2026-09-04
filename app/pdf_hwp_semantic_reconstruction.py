"""Fail-closed semantic item reconstruction contract for PDF -> HWP work.

The PDF/OCR boundary produces physical rows, but a HWP writer must consume a
reviewed semantic item model.  This module validates that boundary without
opening or embedding any source document.  It is intentionally generic and
copyright-safe: callers provide a JSON manifest containing item structure,
native object evidence, and QA evidence while the source PDF remains outside
the repository.

The validator complements ``math_content_scope`` and ``math_source_manifest``:
scope decides *which* regions may be read, source-manifest QA decides whether
math OCR has been reviewed, and this gate decides whether the reviewed content
has been reconstructed as editable item semantics before endnotes are built.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "pdf-hwp-semantic-reconstruction-v1"
ENDNOTE_MODE = "staged_atomic"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Public, stable finding codes.  Keep these strings in reports and fixtures so
# operators can route failures without depending on Python enum names.
FAIL_CODES = frozenset({
    "PHYSICAL_OCR_ROW_SPLIT",
    "BODY_JUSTIFY_STRETCH",
    "SENTENCE_FRAGMENTATION",
    "CHOICE_LAYOUT_MISMATCH",
    "CONDITION_BOX_NOT_NATIVE",
    "ITEM_COLUMN_MISMATCH",
    "FIGURE_OWNERSHIP_MISMATCH",
    "PARAGRAPH_SPACING_OUT_OF_PROFILE",
    "OCR_UNREVIEWED",
    "FORMULA_NATIVE_MISSING",
    "NON_ITEM_PAGE_INCLUDED",
    "ITEM_SOLUTION_MAPPING_MISMATCH",
    "PRE_ENDNOTE_CHECKPOINT_REQUIRED",
    "ENDNOTE_MODE_INVALID",
    "REOPEN_RENDER_QA_MISSING",
    "FORBIDDEN_CAPTURE_IMAGE",
})

_MAPPING_EVIDENCE = frozenset({
    "section",
    "printed_label",
    "problem_first_sentence",
    "solution_content",
})
_NATIVE_CHOICE_KINDS = frozenset({"native_choice"})
_NATIVE_TABLE_KINDS = frozenset({"native_table", "table"})
_SEMANTIC_ORIGINS = frozenset({"semantic", "semantic_field", "reviewed_semantic"})
_BODY_ROLES = frozenset({"body", "stem", "ask", "choice", "solution_body", "endnote_body"})
_FRAGMENT_SUFFIXES = ("은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "로", "으로", "및", "에서", "부터", "까지")
_EXPECTED_STYLE = {
    "font_family": "함초롬돋움",
    "font_size_pt": 11.0,
    "line_spacing_percent": 160.0,
    "space_before_pt": 0.0,
    "space_after_pt": 2.0,
}
_EXPECTED_EQUATION = {
    "font_family": "HYhwpEQ",
    "font_size_pt": 11.0,
    "base_unit": 1100,
}


def _finding(code: str, message: str, *, item_id: str | None = None, **context: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "message": message}
    if item_id is not None:
        result["item_id"] = item_id
    result.update(context)
    return result


def _digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().upper() in {"PASS", "TRUE", "VERIFIED"})


def _number(value: Any, default: float | int | None = None) -> float | int | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _style_value(row: Mapping[str, Any], key: str, expected: float | str) -> Any:
    """Read style values from flat records or a nested ``style`` object."""

    if key in row:
        return row[key]
    style = row.get("style")
    if isinstance(style, Mapping):
        return style.get(key)
    return None


def _same_number(actual: Any, expected: float | int, *, tolerance: float = 0.001) -> bool:
    value = _number(actual)
    return value is not None and abs(float(value) - float(expected)) <= tolerance


def _validate_semantic_origin(item: Mapping[str, Any], findings: list[dict[str, Any]]) -> None:
    source_mode = str(item.get("source_mode", "")).lower()
    if source_mode in {"physical_ocr_rows", "physical_rows", "ocr_rows", "raw_ocr"}:
        findings.append(_finding(
            "PHYSICAL_OCR_ROW_SPLIT",
            "physical OCR rows must be reassembled into semantic item fields before HWP paragraphs",
            item_id=str(item.get("item_id", "")) or None,
            source_mode=source_mode,
        ))
    paragraphs = _list(item.get("paragraphs"))
    for index, paragraph in enumerate(paragraphs, 1):
        if not isinstance(paragraph, Mapping):
            findings.append(_finding(
                "PHYSICAL_OCR_ROW_SPLIT",
                "paragraph evidence is not a semantic object",
                item_id=str(item.get("item_id", "")) or None,
                paragraph=index,
            ))
            continue
        origin = str(paragraph.get("origin", paragraph.get("source_kind", "semantic"))).lower()
        if origin not in _SEMANTIC_ORIGINS:
            findings.append(_finding(
                "PHYSICAL_OCR_ROW_SPLIT",
                "HWP paragraph is directly sourced from a physical OCR row",
                item_id=str(item.get("item_id", "")) or None,
                paragraph=index,
                origin=origin,
            ))


def _validate_ocr_review(item: Mapping[str, Any], findings: list[dict[str, Any]]) -> None:
    status = str(item.get("ocr_review_status", item.get("review_status", ""))).upper()
    if status != "VERIFIED":
        findings.append(_finding(
            "OCR_UNREVIEWED",
            "OCR candidates must be reviewed against the source before semantic reconstruction",
            item_id=str(item.get("item_id", "")) or None,
            review_status=status or None,
        ))
    for index, paragraph in enumerate(_list(item.get("paragraphs")), 1):
        if isinstance(paragraph, Mapping) and str(paragraph.get("review_status", "VERIFIED")).upper() != "VERIFIED":
            findings.append(_finding(
                "OCR_UNREVIEWED",
                "every semantic paragraph must carry VERIFIED review status",
                item_id=str(item.get("item_id", "")) or None,
                paragraph=index,
            ))


def _validate_sentences(item: Mapping[str, Any], findings: list[dict[str, Any]]) -> None:
    item_id = str(item.get("item_id", "")) or None
    fields = item.get("semantic_fields")
    records: list[Mapping[str, Any]] = []
    if isinstance(fields, Mapping):
        records.extend(value for value in fields.values() if isinstance(value, Mapping))
    records.extend(value for value in _list(item.get("paragraphs")) if isinstance(value, Mapping))
    for index, field in enumerate(records, 1):
        status = str(field.get("sentence_status", field.get("sentence_complete", "COMPLETE"))).upper()
        text = str(field.get("text", "")).strip()
        fragment = (
            field.get("is_fragment") is True
            or field.get("fragment") is True
            or (status != "NOT_APPLICABLE" and not text)
            or (len(text) <= 20 and text.endswith(_FRAGMENT_SUFFIXES) and not text.endswith((".", "!", "?", "다", "요")))
        )
        if status not in {"COMPLETE", "VERIFIED", "NOT_APPLICABLE"} or fragment:
            findings.append(_finding(
                "SENTENCE_FRAGMENTATION",
                "semantic fields must contain complete sentences, not physical-line fragments",
                item_id=item_id,
                field=index,
                sentence_status=status,
                text_preview=text[:80],
            ))


def _validate_paragraphs(item: Mapping[str, Any], findings: list[dict[str, Any]]) -> None:
    item_id = str(item.get("item_id", "")) or None
    for index, paragraph in enumerate(_list(item.get("paragraphs")), 1):
        if not isinstance(paragraph, Mapping):
            continue
        role = str(paragraph.get("role", "body")).lower()
        expected = _EXPECTED_STYLE if role in _BODY_ROLES else {
            **_EXPECTED_STYLE,
            "space_before_pt": 12.0 if role in {"problem_heading", "heading"} else 0.0,
            "space_after_pt": 3.0 if role in {"problem_heading", "heading"} else 2.0,
        }
        mismatches: dict[str, Any] = {}
        for key, target in expected.items():
            actual = _style_value(paragraph, key, target)
            if key in {"font_size_pt", "line_spacing_percent", "space_before_pt", "space_after_pt"}:
                if not _same_number(actual, float(target)):
                    mismatches[key] = actual
            elif actual != target:
                mismatches[key] = actual
        if mismatches:
            findings.append(_finding(
                "PARAGRAPH_SPACING_OUT_OF_PROFILE",
                "paragraph typography or native paragraph margins differ from the approved profile",
                item_id=item_id,
                paragraph=index,
                role=role,
                mismatches=mismatches,
            ))
        stretched = (
            paragraph.get("justify_stretch") is True
            or paragraph.get("last_line_stretched") is True
            or paragraph.get("last_line_is_short") is True
            and str(paragraph.get("alignment", "justify")).lower() == "justify"
            or (isinstance(paragraph.get("visual_flags"), Mapping)
                and paragraph["visual_flags"].get("body_justify_stretch") is True)
        )
        if role in _BODY_ROLES and str(paragraph.get("alignment", "justify")).lower() == "justify" and stretched:
            findings.append(_finding(
                "BODY_JUSTIFY_STRETCH",
                "body justification must not stretch a short or final line",
                item_id=item_id,
                paragraph=index,
            ))


def _validate_choices(item: Mapping[str, Any], findings: list[dict[str, Any]]) -> None:
    item_id = str(item.get("item_id", "")) or None
    choices = item.get("choices")
    expected_count_value = item.get("expected_choice_count")
    if isinstance(choices, list):
        actual_count = len(choices)
        expected_count = int(expected_count_value) if isinstance(expected_count_value, int) else actual_count
        actual_layout = str(item.get("choice_layout", "single_column"))
        expected_layout = str(item.get("expected_choice_layout", "single_column"))
        kind = str(item.get("choice_kind", "native_choice"))
    elif isinstance(choices, Mapping):
        entries = choices.get("items", choices.get("values", []))
        raw_count = choices.get("actual_count", 0)
        parsed_count = _number(raw_count)
        actual_count = len(entries) if isinstance(entries, list) else int(parsed_count) if parsed_count is not None else 0
        expected_count = choices.get("expected_count", expected_count_value if isinstance(expected_count_value, int) else actual_count)
        actual_layout = str(choices.get("actual_layout", choices.get("layout", "")))
        expected_layout = str(choices.get("expected_layout", choices.get("layout", "single_column")))
        kind = str(choices.get("kind", choices.get("object_type", "")))
    else:
        actual_count, expected_count, actual_layout, expected_layout, kind = 0, int(expected_count_value) if isinstance(expected_count_value, int) else 0, "", "", ""
    if expected_count == 0 and actual_count == 0:
        return
    if not isinstance(expected_count, int) or expected_count < 1 or not actual_layout or not expected_layout or actual_layout != expected_layout or actual_count != expected_count or kind not in _NATIVE_CHOICE_KINDS:
        findings.append(_finding(
            "CHOICE_LAYOUT_MISMATCH",
            "choices must be the exact reviewed count of ordered native objects in the reviewed layout",
            item_id=item_id,
            expected_count=expected_count,
            actual_count=actual_count,
            expected_layout=expected_layout,
            actual_layout=actual_layout,
            kind=kind,
        ))


def _validate_condition_and_tables(item: Mapping[str, Any], findings: list[dict[str, Any]]) -> None:
    item_id = str(item.get("item_id", "")) or None
    condition = item.get("condition_box")
    if isinstance(condition, Mapping) and (condition.get("required") is True or condition.get("present") is True):
        kind = str(condition.get("kind", condition.get("object_type", "")))
        if condition.get("native") is False or kind not in _NATIVE_TABLE_KINDS:
            findings.append(_finding(
                "CONDITION_BOX_NOT_NATIVE",
                "a condition/보기 box must be represented by a native HWP table",
                item_id=item_id,
                kind=kind,
            ))
    for index, table in enumerate(_list(item.get("tables")), 1):
        if not isinstance(table, Mapping):
            continue
        kind = str(table.get("kind", table.get("object_type", "")))
        if table.get("native") is False or kind not in _NATIVE_TABLE_KINDS:
            findings.append(_finding(
                "CONDITION_BOX_NOT_NATIVE" if str(table.get("role", "")).lower() == "condition" else "CONDITION_BOX_NOT_NATIVE",
                "semantic tables and condition boxes must remain native HWP tables",
                item_id=item_id,
                table=index,
                kind=kind,
            ))


def _validate_columns(item: Mapping[str, Any], findings: list[dict[str, Any]]) -> None:
    item_id = str(item.get("item_id", "")) or None
    source = item.get("source_column", item.get("expected_column"))
    output = item.get("output_column", item.get("actual_column"))
    if source is not None and output is not None and source != output:
        findings.append(_finding(
            "ITEM_COLUMN_MISMATCH",
            "item output column must match the reviewed source column",
            item_id=item_id,
            expected_column=source,
            actual_column=output,
        ))


def _validate_figures(item: Mapping[str, Any], seen: dict[str, str], findings: list[dict[str, Any]]) -> None:
    item_id = str(item.get("item_id", "")) or None
    item_seen: set[str] = set()
    for index, figure in enumerate(_list(item.get("figures")), 1):
        if not isinstance(figure, Mapping):
            continue
        figure_id = str(figure.get("figure_id", figure.get("id", f"figure-{index}")))
        owner = str(figure.get("owner_item_id", figure.get("item_id", "")))
        if figure_id in item_seen or owner != item_id or (figure_id in seen and seen[figure_id] != item_id):
            findings.append(_finding(
                "FIGURE_OWNERSHIP_MISMATCH",
                "each figure must have exactly one owning semantic item",
                item_id=item_id,
                figure_id=figure_id,
                declared_owner=owner,
                previous_owner=seen.get(figure_id) or item_id,
            ))
        item_seen.add(figure_id)
        seen[figure_id] = owner
        kind = str(figure.get("kind", figure.get("classification", "figure"))).lower()
        content_role = str(figure.get("content_role", "pure_figure")).lower()
        if kind in {"page_capture", "question_capture", "body_capture", "solution_body_capture", "screenshot"} or content_role in {"page_capture", "question_capture", "body_capture"} or content_role not in {"pure_figure", "figure"} or figure.get("contains_text") is True:
            findings.append(_finding(
                "FORBIDDEN_CAPTURE_IMAGE",
                "only tight-cropped pure figures may be retained as images",
                item_id=item_id,
                figure_id=figure_id,
                kind=kind,
            ))


def _validate_formulas(item: Mapping[str, Any], findings: list[dict[str, Any]]) -> int:
    item_id = str(item.get("item_id", "")) or None
    formulas = [value for value in _list(item.get("formulas")) if isinstance(value, Mapping)]
    expected = item.get("expected_formula_count", item.get("formula_count", len(formulas)))
    expected_count = int(expected) if isinstance(expected, int) and not isinstance(expected, bool) and expected >= 0 else -1
    if len(formulas) != expected_count:
        findings.append(_finding(
            "FORMULA_NATIVE_MISSING",
            "every reviewed formula must have a corresponding native HWP equation",
            item_id=item_id,
            expected_count=expected_count,
            actual_count=len(formulas),
        ))
    for index, formula in enumerate(formulas, 1):
        native = formula.get("native") is True or str(formula.get("kind", formula.get("object_type", ""))).lower() in {"native_equation", "equation", "eqed"}
        base_unit = _number(formula.get("base_unit", _EXPECTED_EQUATION["base_unit"]))
        style_bad = (
            formula.get("font_family", _EXPECTED_EQUATION["font_family"]) != _EXPECTED_EQUATION["font_family"]
            or not _same_number(formula.get("font_size_pt", _EXPECTED_EQUATION["font_size_pt"]), _EXPECTED_EQUATION["font_size_pt"])
            or base_unit is None or abs(float(base_unit) - _EXPECTED_EQUATION["base_unit"]) > 0.001
            or not str(formula.get("script", "")).strip()
        )
        if not native or style_bad:
            findings.append(_finding(
                "FORMULA_NATIVE_MISSING",
                "formula is missing an editable native equation, script, or approved HYhwpEQ profile",
                item_id=item_id,
                formula=index,
                native=native,
            ))
    return expected_count


def _validate_formula_chain(manifest: Mapping[str, Any], expected_total: int, findings: list[dict[str, Any]]) -> None:
    chain = manifest.get("formula_chain")
    if expected_total <= 0:
        return
    if not isinstance(chain, Mapping):
        findings.append(_finding(
            "FORMULA_NATIVE_MISSING",
            "formula chain must link reviewed source, HWPX, HWP/COM, and rendered PDF counts",
            expected_count=expected_total,
        ))
        return
    keys = ("source_reviewed_count", "hwpx_count", "hwp_count", "com_count", "rendered_pdf_count")
    counts = {key: chain.get(key) for key in keys}
    if any(value != expected_total for value in counts.values()) or chain.get("font_family", _EXPECTED_EQUATION["font_family"]) != _EXPECTED_EQUATION["font_family"] or chain.get("base_unit", _EXPECTED_EQUATION["base_unit"]) != _EXPECTED_EQUATION["base_unit"]:
        findings.append(_finding(
            "FORMULA_NATIVE_MISSING",
            "reviewed formula count must equal HWPX, HWP/COM, and rendered-PDF native equation counts",
            expected_count=expected_total,
            counts=counts,
        ))


def _validate_pages(manifest: Mapping[str, Any], findings: list[dict[str, Any]]) -> None:
    for page in _list(manifest.get("pages")):
        if not isinstance(page, Mapping):
            continue
        role = str(page.get("role", page.get("page_role", ""))).lower()
        if page.get("included") is True and role not in {"problem", "solution", "mixed"}:
            findings.append(_finding(
                "NON_ITEM_PAGE_INCLUDED",
                "pages without problem/solution item content must be excluded from OCR and output",
                page=page.get("pdf_page"),
                role=role,
            ))
        if page.get("included") is True and not page.get("item_ids"):
            findings.append(_finding(
                "NON_ITEM_PAGE_INCLUDED",
                "an included page must own at least one semantic item",
                page=page.get("pdf_page"),
            ))


def _validate_mapping(items: Sequence[Any], findings: list[dict[str, Any]]) -> None:
    ids: set[str] = set()
    region_owners: dict[str, str] = {}
    if not items:
        findings.append(_finding(
            "ITEM_SOLUTION_MAPPING_MISMATCH",
            "at least one semantic item is required",
        ))
    for index, raw in enumerate(items, 1):
        if not isinstance(raw, Mapping):
            findings.append(_finding("ITEM_SOLUTION_MAPPING_MISMATCH", "item record must be an object", item_id=f"index-{index}"))
            continue
        item_id = str(raw.get("item_id", ""))
        if not item_id or item_id in ids:
            findings.append(_finding("ITEM_SOLUTION_MAPPING_MISMATCH", "item_id must be stable and unique", item_id=item_id or None))
        ids.add(item_id)
        problem_regions = [str(value) for value in _list(raw.get("problem_region_ids")) if str(value)]
        solution_regions = [str(value) for value in _list(raw.get("solution_region_ids")) if str(value)]
        if len(problem_regions) != len(set(problem_regions)) or len(solution_regions) != len(set(solution_regions)):
            findings.append(_finding(
                "ITEM_SOLUTION_MAPPING_MISMATCH",
                "problem and solution region lists may not repeat a region",
                item_id=item_id or None,
            ))
        evidence = {str(value) for value in _list(raw.get("mapping_evidence"))}
        if not problem_regions or not solution_regions or not _MAPPING_EVIDENCE.issubset(evidence):
            findings.append(_finding(
                "ITEM_SOLUTION_MAPPING_MISMATCH",
                "each item needs problem and solution regions plus section/label/first-sentence/content evidence",
                item_id=item_id or None,
                missing_evidence=sorted(_MAPPING_EVIDENCE - evidence),
            ))
        for region in (*problem_regions, *solution_regions):
            if region in region_owners and region_owners[region] != item_id:
                findings.append(_finding(
                    "ITEM_SOLUTION_MAPPING_MISMATCH",
                    "a source region may belong to exactly one item",
                    item_id=item_id or None,
                    region_id=region,
                    previous_owner=region_owners[region],
                ))
            region_owners[region] = item_id


def _validate_pre_endnote(manifest: Mapping[str, Any], findings: list[dict[str, Any]]) -> None:
    if manifest.get("endnote_mode") != ENDNOTE_MODE:
        findings.append(_finding("ENDNOTE_MODE_INVALID", "native endnotes require endnote_mode=staged_atomic"))
    checkpoint = manifest.get("pre_endnote_checkpoint")
    status = checkpoint.get("status") if isinstance(checkpoint, Mapping) else None
    digest = checkpoint.get("artifact_sha256") if isinstance(checkpoint, Mapping) else None
    if str(status).upper() != "PASS" or not _SHA256_RE.fullmatch(str(digest or "").lower()):
        findings.append(_finding(
            "PRE_ENDNOTE_CHECKPOINT_REQUIRED",
            "native endnote insertion is blocked until the pre-endnote editable checkpoint is PASS",
            checkpoint_status=status,
        ))


def _validate_reopen_render(manifest: Mapping[str, Any], findings: list[dict[str, Any]]) -> None:
    qa = manifest.get("reopen_render_qa", manifest.get("qa"))
    required = ("hwp_reopen", "hwpx_reopen", "com_reopen", "rendered_pdf", "visual_review")
    if not isinstance(qa, Mapping) or not all(_is_true(qa.get(key)) for key in required):
        findings.append(_finding(
            "REOPEN_RENDER_QA_MISSING",
            "HWP/HWPX/COM reopen and rendered-PDF visual QA are required before release",
            required=list(required),
        ))


def validate_semantic_reconstruction(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a semantic reconstruction manifest and return JSON-safe evidence.

    ``PASS`` means every included item is reconstructed from reviewed semantic
    fields, native choices/tables/equations are present, and the pre-endnote
    checkpoint plus reopen/render QA evidence is complete.  It never implies
    that a source document was copied into the repository.
    """

    if not isinstance(manifest, Mapping):
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "FAIL",
            "passed": False,
            "counts": {},
            "findings": [_finding("MANIFEST_NOT_OBJECT", "semantic reconstruction manifest must be an object")],
            "gates": {"manifest_valid": False},
        }
    findings: list[dict[str, Any]] = []
    gates: dict[str, bool] = {
        "schema": manifest.get("schema_version") == SCHEMA_VERSION,
        "verified_manifest": str(manifest.get("status", "")).upper() == "VERIFIED" and manifest.get("uncertainties", []) in ([], None),
        "semantic_item_reflow": manifest.get("source_mode") == "semantic_item_reflow",
        "item_pages_only": True,
        "item_solution_mapping": True,
        "native_editable_objects": True,
        "profile": True,
        "formula_chain": True,
        "pre_endnote_checkpoint": True,
        "reopen_render_qa": True,
    }
    if not gates["schema"]:
        findings.append(_finding("SCHEMA_VERSION_INVALID", f"schema_version must be {SCHEMA_VERSION}"))
    if not gates["verified_manifest"]:
        findings.append(_finding("OCR_UNREVIEWED", "manifest must be VERIFIED with no unresolved uncertainties"))
    if not gates["semantic_item_reflow"]:
        findings.append(_finding("PHYSICAL_OCR_ROW_SPLIT", "source_mode must be semantic_item_reflow"))

    _validate_pages(manifest, findings)
    gates["item_pages_only"] = not any(row["code"] == "NON_ITEM_PAGE_INCLUDED" for row in findings)
    items = _list(manifest.get("items"))
    _validate_mapping(items, findings)
    gates["item_solution_mapping"] = not any(row["code"] == "ITEM_SOLUTION_MAPPING_MISMATCH" for row in findings)
    seen_figures: dict[str, str] = {}
    expected_formula_total = 0
    for raw in items:
        if not isinstance(raw, Mapping):
            gates["native_editable_objects"] = False
            continue
        item_findings_start = len(findings)
        _validate_semantic_origin(raw, findings)
        _validate_ocr_review(raw, findings)
        _validate_sentences(raw, findings)
        _validate_paragraphs(raw, findings)
        _validate_choices(raw, findings)
        _validate_condition_and_tables(raw, findings)
        _validate_columns(raw, findings)
        _validate_figures(raw, seen_figures, findings)
        expected_formula_total += _validate_formulas(raw, findings)
        item_codes = {row["code"] for row in findings[item_findings_start:]}
        if item_codes & {"PHYSICAL_OCR_ROW_SPLIT", "CHOICE_LAYOUT_MISMATCH", "CONDITION_BOX_NOT_NATIVE", "FIGURE_OWNERSHIP_MISMATCH", "FORMULA_NATIVE_MISSING", "FORBIDDEN_CAPTURE_IMAGE"}:
            gates["native_editable_objects"] = False
        if item_codes & {"PARAGRAPH_SPACING_OUT_OF_PROFILE", "BODY_JUSTIFY_STRETCH", "SENTENCE_FRAGMENTATION", "ITEM_COLUMN_MISMATCH"}:
            gates["profile"] = False
        if "OCR_UNREVIEWED" in item_codes:
            gates["verified_manifest"] = False
    _validate_formula_chain(manifest, expected_formula_total, findings)
    gates["formula_chain"] = not any(row["code"] == "FORMULA_NATIVE_MISSING" for row in findings)
    _validate_pre_endnote(manifest, findings)
    gates["pre_endnote_checkpoint"] = not any(row["code"] in {"PRE_ENDNOTE_CHECKPOINT_REQUIRED", "ENDNOTE_MODE_INVALID"} for row in findings)
    _validate_reopen_render(manifest, findings)
    gates["reopen_render_qa"] = not any(row["code"] == "REOPEN_RENDER_QA_MISSING" for row in findings)
    passed = all(gates.values()) and not findings
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "counts": {
            "items": len(items),
            "figures": len(seen_figures),
            "expected_formulas": expected_formula_total,
            "findings": len(findings),
        },
        "manifest_sha256": _digest(manifest),
        "findings": findings,
        "gates": gates,
    }


def load_and_validate(path: str | Path) -> dict[str, Any]:
    """Read one JSON manifest; source PDFs and generated HWP files stay external."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "FAIL",
            "passed": False,
            "counts": {},
            "findings": [_finding("MANIFEST_READ_ERROR", str(exc))],
            "gates": {"manifest_valid": False},
        }
    return validate_semantic_reconstruction(payload)


def can_build_native_endnotes(manifest: Mapping[str, Any]) -> bool:
    """Return whether native endnote insertion may be scheduled.

    This helper is deliberately strict and side-effect free so HWP COM code can
    call it immediately before the build stage.  The returned boolean is not a
    substitute for the final native endnote QA report.
    """

    report = validate_semantic_reconstruction(manifest)
    return report["status"] == "PASS" and report["gates"].get("pre_endnote_checkpoint") is True


__all__ = [
    "ENDNOTE_MODE",
    "FAIL_CODES",
    "SCHEMA_VERSION",
    "can_build_native_endnotes",
    "load_and_validate",
    "validate_semantic_reconstruction",
]
