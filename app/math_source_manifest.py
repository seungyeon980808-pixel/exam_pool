"""Fail-closed validation for reviewed PDF mathematical source manifests.

The PDF is the only content authority for a math conversion.  OCR output is
only a candidate and may not be sent directly to the HWP writer.  This module
validates the reviewed, copyright-safe manifest that sits between PDF review
and native HWP/HWPX generation.  It deliberately does not read or embed a PDF.

Each formula record carries a page/item anchor, a 600/900 dpi crop digest, the
reviewed source expression, an explicit operator-bound policy, and a stable
ordinal.  A builder must refuse to run unless every page and formula is
``VERIFIED`` and the manifest contains no unresolved uncertainty.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from .math_formula_semantic_qa import analyze_formula
except ImportError:  # direct module execution
    from math_formula_semantic_qa import analyze_formula  # type: ignore


SCHEMA_VERSION = "math-source-manifest-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STATUS = {"VERIFIED", "UNREVIEWED", "BLOCKED"}
_BOUNDS_MODES = {"explicit", "none_as_printed"}
_KOREAN_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")


def _has_disallowed_korean(source: str) -> bool:
    """Reject prose in formula sources but allow explicit TeX text atoms.

    Korean connective words can be part of a mathematically printed formula
    when the PDF uses ``\\text{...}`` (for example, a case condition joined by
    “또는”).  They remain native equation content after conversion.  Korean
    outside an explicit text/style group is still treated as OCR prose and
    fails closed.
    """

    value = str(source or "")
    # Remove balanced one-level text/style groups before checking for Korean.
    # Nested mathematical groups are not removed, so malformed OCR remains a
    # failure rather than being hidden by this exception.
    value = re.sub(r"\\(?:text|mathrm|operatorname|mathtt)\{[^{}]*\}", "", value)
    return bool(_KOREAN_RE.search(value))


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _source_digest(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _error(code: str, message: str, **context: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **context}


def _bbox_valid(value: Any) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        return False
    try:
        nums = [float(item) for item in value]
    except (TypeError, ValueError):
        return False
    return all(num >= 0 for num in nums) and nums[2] > nums[0] and nums[3] > nums[1]


def _formula_records(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for page in manifest.get("pages", []):
        for item in page.get("items", []) if isinstance(page, Mapping) else []:
            for formula in item.get("formulas", []) if isinstance(item, Mapping) else []:
                if isinstance(formula, Mapping):
                    records.append({"page": page, "item": item, "formula": formula})
    # A flat list is accepted for manifests produced by an extractor, but it
    # must still carry the same page/item anchors.
    if records:
        return records
    return [
        {"page": {}, "item": {}, "formula": formula}
        for formula in manifest.get("formulas", [])
        if isinstance(formula, Mapping)
    ]


def validate_source_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one reviewed source manifest and return literal boolean gates."""

    findings: list[dict[str, Any]] = []
    if not isinstance(manifest, Mapping):
        return {"status": "FAIL", "passed": False, "findings": [_error("MANIFEST_NOT_OBJECT", "manifest must be an object")], "gates": {"manifest_valid": False}}

    gates: dict[str, bool] = {
        "manifest_valid": True,
        "schema_version": manifest.get("schema_version") == SCHEMA_VERSION,
        "verified_status": str(manifest.get("status", "")).upper() == "VERIFIED",
        "source_pdf_sha256": bool(_SHA256_RE.fullmatch(str(manifest.get("source_pdf_sha256", "")).lower())),
        "page_count_positive": isinstance(manifest.get("page_count"), int) and manifest.get("page_count", 0) > 0,
        "pages_complete": False,
        "no_uncertainties": manifest.get("uncertainties", []) in ([], None),
        "formula_count_exact": False,
        "formula_ordinals_contiguous": False,
        "formula_records_verified": False,
        "mathir_complete": False,
        "content_digest_present": bool(_SHA256_RE.fullmatch(str(manifest.get("content_sha256", "")).lower())),
    }
    if not gates["schema_version"]:
        findings.append(_error("SCHEMA_VERSION_INVALID", f"schema_version must be {SCHEMA_VERSION}"))
    if not gates["verified_status"]:
        findings.append(_error("MANIFEST_NOT_VERIFIED", "source manifest must have status VERIFIED"))
    if not gates["source_pdf_sha256"]:
        findings.append(_error("SOURCE_PDF_HASH_INVALID", "source_pdf_sha256 must be a 64-character SHA-256"))
    if not gates["no_uncertainties"]:
        findings.append(_error("UNCERTAINTY_REMAINS", "uncertainties must be empty before building"))

    pages = manifest.get("pages")
    if not isinstance(pages, list) or len(pages) != manifest.get("page_count"):
        findings.append(_error("PAGE_COUNT_MISMATCH", "pages length must equal page_count"))
    else:
        page_numbers: list[int] = []
        page_ok = True
        for page_index, page in enumerate(pages, 1):
            if not isinstance(page, Mapping):
                page_ok = False
                findings.append(_error("PAGE_RECORD_INVALID", "page record must be an object", page=page_index))
                continue
            printed = page.get("pdf_page", page_index)
            page_numbers.append(printed if isinstance(printed, int) else -1)
            if str(page.get("status", "")).upper() != "VERIFIED":
                page_ok = False
                findings.append(_error("PAGE_NOT_VERIFIED", "every PDF page must be VERIFIED", page=printed))
            if page.get("render_dpi") not in (600, 900):
                page_ok = False
                findings.append(_error("PAGE_RENDER_DPI_INVALID", "page review must record 600 or 900 dpi", page=printed))
            if page.get("page_crop_sha256") and not _SHA256_RE.fullmatch(str(page.get("page_crop_sha256")).lower()):
                page_ok = False
                findings.append(_error("PAGE_CROP_HASH_INVALID", "page_crop_sha256 is not SHA-256", page=printed))
        gates["pages_complete"] = page_ok and page_numbers == list(range(1, len(pages) + 1))
        if not gates["pages_complete"]:
            findings.append(_error("PAGE_SEQUENCE_INVALID", "PDF page anchors must be contiguous and ordered"))

    records = _formula_records(manifest)
    declared = manifest.get("formula_count")
    gates["formula_count_exact"] = isinstance(declared, int) and declared == len(records)
    if not gates["formula_count_exact"]:
        findings.append(_error("FORMULA_COUNT_MISMATCH", "formula_count must equal reviewed formula records", declared=declared, actual=len(records)))

    ordinals: list[int] = []
    records_ok = True
    mathir_ok = True
    for position, row in enumerate(records, 1):
        page = row["page"]
        item = row["item"]
        formula = row["formula"]
        page_num = page.get("pdf_page", "?") if isinstance(page, Mapping) else "?"
        item_id = item.get("item_id", "?") if isinstance(item, Mapping) else "?"
        ordinal = formula.get("ordinal")
        if not isinstance(ordinal, int):
            records_ok = False
            findings.append(_error("FORMULA_ORDINAL_INVALID", "formula ordinal must be an integer", page=page_num, item_id=item_id, position=position))
        else:
            ordinals.append(ordinal)
        if str(formula.get("review_status", "")).upper() != "VERIFIED":
            records_ok = False
            findings.append(_error("FORMULA_NOT_VERIFIED", "every formula must be VERIFIED", page=page_num, item_id=item_id, ordinal=ordinal))
        if not _bbox_valid(formula.get("bbox_pt")):
            records_ok = False
            findings.append(_error("FORMULA_BBOX_INVALID", "formula bbox_pt must be a positive PDF-point rectangle", page=page_num, item_id=item_id, ordinal=ordinal))
        if not _SHA256_RE.fullmatch(str(formula.get("source_crop_sha256", "")).lower()):
            records_ok = False
            findings.append(_error("FORMULA_CROP_HASH_INVALID", "source_crop_sha256 must be SHA-256", page=page_num, item_id=item_id, ordinal=ordinal))
        source = str(formula.get("source", "")).strip()
        if not source:
            records_ok = False
            findings.append(_error("FORMULA_SOURCE_EMPTY", "reviewed formula source is empty", page=page_num, item_id=item_id, ordinal=ordinal))
        features = analyze_formula(source)
        if features["errors"] or _has_disallowed_korean(source):
            mathir_ok = False
            findings.append(_error("FORMULA_MATHIR_INVALID", "formula source contains unsupported syntax or prose", page=page_num, item_id=item_id, ordinal=ordinal, errors=features["errors"]))
        mode = str(formula.get("operator_bounds_mode", ""))
        if any(op["operator"] in {"sum", "prod"} for op in features["operators"]) and mode not in _BOUNDS_MODES:
            mathir_ok = False
            findings.append(_error("OPERATOR_BOUNDS_MODE_MISSING", "Σ/Π requires explicit or none_as_printed policy", page=page_num, item_id=item_id, ordinal=ordinal))
        if isinstance(formula.get("mathir"), Mapping):
            if formula["mathir"].get("source_sha256") not in {None, _source_digest(source)}:
                mathir_ok = False
                findings.append(_error("MATHIR_SOURCE_HASH_MISMATCH", "mathir.source_sha256 does not match source", page=page_num, item_id=item_id, ordinal=ordinal))
        else:
            mathir_ok = False
            findings.append(_error("MATHIR_MISSING", "every formula must include a MathIR record", page=page_num, item_id=item_id, ordinal=ordinal))
    gates["formula_records_verified"] = records_ok
    gates["mathir_complete"] = mathir_ok
    gates["formula_ordinals_contiguous"] = ordinals == list(range(1, len(ordinals) + 1))
    if not gates["formula_ordinals_contiguous"]:
        findings.append(_error("FORMULA_ORDER_INVALID", "formula ordinals must be contiguous from 1"))

    gates["manifest_valid"] = not findings
    passed = all(value is True for value in gates.values()) and not findings
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "counts": {"pages": len(pages) if isinstance(pages, list) else 0, "formulas": len(records)},
        "manifest_content_sha256": _digest(manifest),
        "findings": findings,
        "gates": {key: value is True for key, value in gates.items()},
    }


def load_and_validate(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"status": "FAIL", "passed": False, "findings": [_error("MANIFEST_READ_ERROR", str(exc))], "gates": {"manifest_valid": False}}
    return validate_source_manifest(payload)


__all__ = ["SCHEMA_VERSION", "load_and_validate", "validate_source_manifest"]
