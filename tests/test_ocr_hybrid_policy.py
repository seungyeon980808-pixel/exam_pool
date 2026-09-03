from __future__ import annotations

from pathlib import Path

from app.ocr_hybrid_policy import (
    CONVERTER_COMMIT,
    CONVERTER_RELEASE,
    SCHEMA_VERSION,
    SOURCE_MANIFEST_SCHEMA,
    canonical_manifest_sha256,
    load_policy,
    validate_ocr_provenance,
)


def _manifest() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_manifest_schema": SOURCE_MANIFEST_SCHEMA,
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
    report = validate_ocr_provenance(_manifest())
    assert report["status"] == "PASS"
    assert all(report["gates"].values())


def test_converter_fallback_is_fail_closed() -> None:
    value = _manifest()
    value["ocr_candidates"][1]["formula_plain_text_fallback"] = "allowed"
    report = validate_ocr_provenance(value)
    assert report["status"] == "FAIL"
    assert "CONVERTER_STRICT_WRAPPER_MISSING" in {row["code"] for row in report["findings"]}


def test_invalid_provenance_hashes_fail_closed() -> None:
    value = _manifest()
    value["source_manifest_sha256"] = "not-a-hash"
    value["engine_config_hashes"]["paddle"] = ""
    report = validate_ocr_provenance(value)
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
    report = validate_ocr_provenance(value, source_is_copyrighted=True, hosted_transfer_approved=False)
    assert report["status"] == "FAIL"
    assert "ANYDOC_HOSTED_TRANSFER_NOT_APPROVED" in {row["code"] for row in report["findings"]}


def test_unresolved_ocr_disagreement_fails() -> None:
    value = _manifest()
    value["disagreement_count"] = 1
    value["disagreement_records"] = [{"page": 1, "resolved": False}]
    report = validate_ocr_provenance(value)
    assert report["status"] == "FAIL"
    assert "OCR_DISAGREEMENT_UNRESOLVED" in {row["code"] for row in report["findings"]}


def test_provenance_hash_is_stable() -> None:
    value = _manifest()
    assert canonical_manifest_sha256(value) == canonical_manifest_sha256(dict(value))


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
