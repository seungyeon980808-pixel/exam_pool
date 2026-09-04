from __future__ import annotations

from pathlib import Path

from app.ocr_hybrid_policy import (
    CONTENT_SCOPE_MANIFEST_SCHEMA,
    CONVERTER_COMMIT,
    CONVERTER_RELEASE,
    SCHEMA_VERSION,
    SOURCE_MANIFEST_SCHEMA,
    canonical_manifest_sha256,
    load_policy,
    validate_ocr_provenance,
)
from app.math_content_scope import canonical_scope_sha256


def _scope_manifest() -> dict:
    return {
        "schema_version": CONTENT_SCOPE_MANIFEST_SCHEMA,
        "status": "VERIFIED",
        "uncertainties": [],
        "source_documents": {
            "problem": {"sha256": "1" * 64, "page_count": 1},
            "solution": {"sha256": "2" * 64, "page_count": 1},
        },
        "scope": {
            "policy": "problem_solution_regions_only",
            "page_index_base": 1,
            "problem_min_pdf_page": 1,
            "output_layout_mode": "item_reflow",
        },
        "pages": [
            {
                "document": "problem",
                "pdf_page": 1,
                "printed_page": 1,
                "page_role": "problem",
                "review_status": "VERIFIED",
                "regions": [{
                    "region_id": "P-Q1", "role": "problem", "item_id": "SYN-001",
                    "bbox": [1, 1, 100, 100], "crop_sha256": "3" * 64,
                    "reading_order": 1, "column_id": "full", "review_status": "VERIFIED",
                }],
            },
            {
                "document": "solution",
                "pdf_page": 1,
                "printed_page": 1,
                "page_role": "solution",
                "review_status": "VERIFIED",
                "regions": [{
                    "region_id": "S-Q1", "role": "solution", "item_id": "SYN-001",
                    "bbox": [1, 1, 100, 100], "crop_sha256": "4" * 64,
                    "reading_order": 1, "column_id": "full", "review_status": "VERIFIED",
                }],
            },
        ],
        "ocr_inputs": {"problem_region_ids": ["P-Q1"], "solution_region_ids": ["S-Q1"]},
        "items": [{
            "item_id": "SYN-001", "sequence": 1, "section_id": "SYN", "printed_label": "1",
            "problem_first_sentence": "합성 문제", "solution_first_sentence": "합성 풀이",
            "problem_text_sha256": "5" * 64, "solution_text_sha256": "6" * 64,
            "problem_region_ids": ["P-Q1"], "solution_region_ids": ["S-Q1"],
            "mapping_evidence": ["section", "printed_label", "problem_first_sentence", "solution_content"],
            "review_status": "VERIFIED", "uncertainties": [],
        }],
        "endnote_policy": {
            "mode": "one_per_problem_item", "mapping": "stable_item_id_exact", "page_round_robin": False,
            "expected_problem_items": 1, "expected_solution_items": 1, "expected_endnotes": 1,
        },
    }


def _manifest() -> dict:
    scope = _scope_manifest()
    return {
        "schema_version": SCHEMA_VERSION,
        "source_manifest_schema": SOURCE_MANIFEST_SCHEMA,
        "content_scope_manifest_schema": CONTENT_SCOPE_MANIFEST_SCHEMA,
        "content_scope_manifest_sha256": canonical_scope_sha256(scope),
        "ocr_region_ids": ["P-Q1", "S-Q1"],
        "authoritative_source": "reviewed_pdf_manifest",
        "source_pdf_sha256": "a" * 64,
        "source_manifest_sha256": "b" * 64,
        "engine_versions": {"paddle": "3.7.0", "exam_pool": CONVERTER_RELEASE},
        "engine_config_hashes": {"paddle": "c" * 64, "exam_pool": "d" * 64},
        "candidate_output_sha256": {"paddle": "e" * 64},
        "transfer_approval": False,
        "merge_policy": "manual_on_disagreement",
        "disagreement_count": 0,
        "disagreement_records": [],
        "ocr_candidates": [
            {
                "engine": "paddle_local_primary",
                "mode": "local",
                "role": "coordinate_ocr_candidate",
                "scope_mode": "reviewed_regions_only",
            },
            {
                "engine": "exam_pool_v0.1.1",
                "mode": "local",
                "release": CONVERTER_RELEASE,
                "commit": CONVERTER_COMMIT,
                "strict_wrapper": True,
                "fallback_policy": "fail_closed",
                "full_page_fallback": "forbidden",
                "formula_image_fallback": "forbidden",
                "formula_plain_text_fallback": "forbidden",
                "equation_font": "HancomEQN",
                "equation_base_unit": 1100,
            },
        ],
    }


def test_local_primary_and_pinned_converter_pass() -> None:
    report = validate_ocr_provenance(_manifest(), scope_manifest=_scope_manifest())
    assert report["status"] == "PASS"
    assert all(report["gates"].values())


def test_converter_fallback_is_fail_closed() -> None:
    value = _manifest()
    value["ocr_candidates"][1]["formula_plain_text_fallback"] = "allowed"
    report = validate_ocr_provenance(value, scope_manifest=_scope_manifest())
    assert report["status"] == "FAIL"
    assert "CONVERTER_STRICT_WRAPPER_MISSING" in {row["code"] for row in report["findings"]}


def test_invalid_provenance_hashes_fail_closed() -> None:
    value = _manifest()
    value["source_manifest_sha256"] = "not-a-hash"
    value["engine_config_hashes"]["paddle"] = ""
    report = validate_ocr_provenance(value, scope_manifest=_scope_manifest())
    assert report["status"] == "FAIL"
    assert "OCR_PROVENANCE_HASH_INVALID" in {row["code"] for row in report["findings"]}


def test_anydoc_hosted_requires_transfer_approval() -> None:
    value = _manifest()
    value["ocr_candidates"].append({
        "engine": "firecrawl_anydoc",
        "mode": "hosted",
        "transfer_approved": False,
        "whole_document_sent": True,
        "page_selection": "unsupported",
    })
    report = validate_ocr_provenance(value, scope_manifest=_scope_manifest(), source_is_copyrighted=True, hosted_transfer_approved=False)
    assert report["status"] == "FAIL"
    assert "ANYDOC_HOSTED_TRANSFER_NOT_APPROVED" in {row["code"] for row in report["findings"]}


def test_unresolved_ocr_disagreement_fails() -> None:
    value = _manifest()
    value["disagreement_count"] = 1
    value["disagreement_records"] = [{"page": 1, "resolved": False}]
    report = validate_ocr_provenance(value, scope_manifest=_scope_manifest())
    assert report["status"] == "FAIL"
    assert "OCR_DISAGREEMENT_UNRESOLVED" in {row["code"] for row in report["findings"]}


def test_provenance_hash_is_stable() -> None:
    value = _manifest()
    assert canonical_manifest_sha256(value) == canonical_manifest_sha256(dict(value))


def test_missing_scope_manifest_fails_closed() -> None:
    report = validate_ocr_provenance(_manifest())
    assert report["status"] == "FAIL"
    assert "CONTENT_SCOPE_MANIFEST_MISSING" in {row["code"] for row in report["findings"]}


def test_ocr_region_leak_against_reviewed_scope_fails() -> None:
    value = _manifest()
    value["ocr_region_ids"].append("P-FRONTMATTER")
    report = validate_ocr_provenance(value, scope_manifest=_scope_manifest())
    assert report["status"] == "FAIL"
    assert "OCR_SCOPE_REGION_MISMATCH" in {row["code"] for row in report["findings"]}


def test_checked_in_policy_pins_converter_and_blocks_shortcuts() -> None:
    path = Path(__file__).parents[1] / "config" / "ocr_hybrid_policy_v1.json"
    policy = load_policy(path)
    converter = policy["exam_pool_converter"]
    assert converter["release"] == CONVERTER_RELEASE
    assert converter["commit"] == CONVERTER_COMMIT
    strict = converter["required_when_used"]
    assert strict["strict_wrapper"] is True
    assert strict["fallback_policy"] == "fail_closed"
    assert strict["full_page_fallback"] == "forbidden"
    assert strict["formula_image_fallback"] == "forbidden"
    assert strict["formula_plain_text_fallback"] == "forbidden"
    assert strict["equation_font"] == "HancomEQN"
    assert strict["equation_base_unit"] == 1100
    assert "majority_vote_for_math_tokens" in policy["forbidden_shortcuts"]
    assert policy["content_scope_manifest_schema"] == CONTENT_SCOPE_MANIFEST_SCHEMA
    assert "page_round_robin_solution_mapping" in policy["forbidden_shortcuts"]
