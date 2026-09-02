"""Copyright-free regression tests for the reviewed PDF source-manifest gate."""

from __future__ import annotations

import hashlib

from app.math_source_manifest import SCHEMA_VERSION, validate_source_manifest


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _manifest(*, formula_source: str = r"\sum_{k=1}^{n}k", status: str = "VERIFIED") -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "source_pdf_sha256": "a" * 64,
        "page_count": 1,
        "uncertainties": [],
        "content_sha256": "b" * 64,
        "pages": [
            {
                "pdf_page": 1,
                "status": "VERIFIED",
                "render_dpi": 600,
                "page_crop_sha256": "c" * 64,
                "items": [
                    {
                        "item_id": "SYN-P1-Q1",
                        "formulas": [
                            {
                                "ordinal": 1,
                                "review_status": "VERIFIED",
                                "bbox_pt": [10, 20, 50, 40],
                                "source_crop_sha256": "d" * 64,
                                "source": formula_source,
                                "operator_bounds_mode": "explicit",
                                "mathir": {"source_sha256": _sha(formula_source), "kind": "operator"},
                            }
                        ],
                    }
                ],
            }
        ],
        "formula_count": 1,
    }


def test_reviewed_manifest_passes_only_after_page_and_formula_provenance() -> None:
    report = validate_source_manifest(_manifest())
    assert report["status"] == "PASS"
    assert report["gates"]["pages_complete"] is True
    assert report["gates"]["mathir_complete"] is True


def test_manifest_rejects_unreviewed_scan_or_uncertainty() -> None:
    value = _manifest(status="DRAFT")
    value["pages"][0]["status"] = "UNREVIEWED"
    value["uncertainties"] = ["small subscript"]
    report = validate_source_manifest(value)
    assert report["status"] == "FAIL"
    codes = {row["code"] for row in report["findings"]}
    assert {"MANIFEST_NOT_VERIFIED", "PAGE_NOT_VERIFIED", "UNCERTAINTY_REMAINS"} <= codes


def test_manifest_rejects_formula_count_order_and_mathir_errors() -> None:
    value = _manifest(formula_source=r"\sumn_{k=1}^{n}k")
    value["formula_count"] = 2
    value["pages"][0]["items"][0]["formulas"][0]["ordinal"] = 2
    value["pages"][0]["items"][0]["formulas"][0]["mathir"]["source_sha256"] = "e" * 64
    report = validate_source_manifest(value)
    assert report["status"] == "FAIL"
    codes = {row["code"] for row in report["findings"]}
    assert {"FORMULA_COUNT_MISMATCH", "FORMULA_ORDER_INVALID", "FORMULA_MATHIR_INVALID", "MATHIR_SOURCE_HASH_MISMATCH"} <= codes


def test_none_as_printed_is_explicitly_allowed_but_missing_policy_fails() -> None:
    value = _manifest(formula_source="\\sum N")
    value["pages"][0]["items"][0]["formulas"][0]["operator_bounds_mode"] = "none_as_printed"
    value["pages"][0]["items"][0]["formulas"][0]["mathir"]["source_sha256"] = _sha("\\sum N")
    assert validate_source_manifest(value)["status"] == "PASS"

    value["pages"][0]["items"][0]["formulas"][0]["operator_bounds_mode"] = ""
    assert validate_source_manifest(value)["status"] == "FAIL"
