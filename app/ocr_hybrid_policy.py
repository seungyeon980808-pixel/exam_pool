"""Fail-closed policy checks for local OCR, ExamPool and anydoc candidates.

This module stores provenance and role rules only.  It does not OCR files or
merge candidate text.  The reviewed PDF source manifest remains the sole math
authority; disagreements must be resolved against the source image.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "ocr-hybrid-policy-v1"
SOURCE_MANIFEST_SCHEMA = "math-source-manifest-v1"
CONVERTER_RELEASE = "hwp-converter-v0.1.1"
CONVERTER_COMMIT = "be1893f"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _finding(code: str, message: str, **context: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **context}


def _sha256(value: Any) -> bool:
    return bool(_SHA256_RE.fullmatch(str(value or "").lower()))


def _hash_map(value: Any) -> bool:
    """Return true for a non-empty mapping whose values are SHA-256 strings."""

    return (
        isinstance(value, Mapping)
        and bool(value)
        and all(isinstance(key, str) and _sha256(item_hash) for key, item_hash in value.items())
    )


def load_policy(path: str | Path) -> Mapping[str, Any]:
    """Load a JSON policy without accepting comments or implicit defaults."""

    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_ocr_provenance(
    manifest: Mapping[str, Any],
    *,
    source_is_copyrighted: bool = True,
    hosted_transfer_approved: bool = False,
) -> dict[str, Any]:
    """Validate an OCR candidate manifest and return literal boolean gates.

    ``manifest`` is intentionally separate from ``math-source-manifest-v1``:
    candidate OCR is evidence and never the authoritative mathematical text.
    """

    findings: list[dict[str, Any]] = []
    if not isinstance(manifest, Mapping):
        return {
            "status": "FAIL",
            "passed": False,
            "gates": {"manifest_object": False},
            "findings": [_finding("MANIFEST_NOT_OBJECT", "OCR provenance must be an object")],
        }

    candidates = manifest.get("ocr_candidates", [])
    if not isinstance(candidates, list):
        candidates = []
        findings.append(_finding("OCR_CANDIDATES_INVALID", "ocr_candidates must be a list"))

    gates: dict[str, bool] = {
        "schema_version": manifest.get("schema_version") == SCHEMA_VERSION,
        "source_manifest_schema": manifest.get("source_manifest_schema") == SOURCE_MANIFEST_SCHEMA,
        "authoritative_source": manifest.get("authoritative_source") == "reviewed_pdf_manifest",
        "source_pdf_sha256": _sha256(manifest.get("source_pdf_sha256")),
        "provenance_hashes": False,
        "primary_local_ocr": False,
        "converter_pinned": True,
        "converter_strict_wrapper": True,
        "anydoc_transfer_policy": True,
        "manual_disagreement_policy": manifest.get("merge_policy") == "manual_on_disagreement",
        "disagreement_resolved": False,
        "provenance_complete": True,
    }

    if not gates["schema_version"]:
        findings.append(_finding("OCR_POLICY_SCHEMA_INVALID", f"schema_version must be {SCHEMA_VERSION}"))
    if not gates["source_manifest_schema"]:
        findings.append(_finding("SOURCE_MANIFEST_SCHEMA_INVALID", f"source_manifest_schema must be {SOURCE_MANIFEST_SCHEMA}"))
    if not gates["authoritative_source"]:
        findings.append(_finding("OCR_AUTHORITY_INVALID", "reviewed_pdf_manifest must remain the authority"))
    if not gates["source_pdf_sha256"]:
        findings.append(_finding("SOURCE_PDF_HASH_INVALID", "source_pdf_sha256 must be a SHA-256"))

    gates["provenance_hashes"] = (
        _sha256(manifest.get("source_manifest_sha256"))
        and isinstance(manifest.get("engine_versions"), Mapping)
        and bool(manifest.get("engine_versions"))
        and all(isinstance(value, str) and value.strip() for value in manifest["engine_versions"].values())
        and _hash_map(manifest.get("engine_config_hashes"))
        and _hash_map(manifest.get("candidate_output_sha256"))
    )
    if not gates["provenance_hashes"]:
        findings.append(_finding(
            "OCR_PROVENANCE_HASH_INVALID",
            "source manifest, engine config, and candidate output hashes must be valid SHA-256 values",
        ))

    primary = [
        row for row in candidates
        if isinstance(row, Mapping)
        and row.get("engine") == "paddle_local_primary"
        and row.get("mode") == "local"
        and row.get("role") == "coordinate_ocr_candidate"
    ]
    gates["primary_local_ocr"] = len(primary) == 1
    if not gates["primary_local_ocr"]:
        findings.append(_finding("PRIMARY_LOCAL_OCR_MISSING", "exactly one local primary OCR candidate is required"))

    converter_rows = [row for row in candidates if isinstance(row, Mapping) and row.get("engine") == "exam_pool_v0.1.1"]
    for row in converter_rows:
        pinned = row.get("release") == CONVERTER_RELEASE and row.get("commit") == CONVERTER_COMMIT
        gates["converter_pinned"] &= pinned
        if not pinned:
            findings.append(_finding("CONVERTER_NOT_PINNED", "ExamPool converter release and commit must be pinned"))
        strict = (
            row.get("mode") == "local"
            and row.get("strict_wrapper") is True
            and row.get("fallback_policy") == "fail_closed"
            and row.get("full_page_fallback") == "forbidden"
            and row.get("formula_image_fallback") == "forbidden"
            and row.get("formula_plain_text_fallback") == "forbidden"
            and row.get("equation_font") == "HancomEQN"
            and row.get("equation_base_unit") == 1100
        )
        gates["converter_strict_wrapper"] &= strict
        if not strict:
            findings.append(_finding("CONVERTER_STRICT_WRAPPER_MISSING", "converter fallback and equation gates are not fail-closed"))

    anydoc_rows = [row for row in candidates if isinstance(row, Mapping) and row.get("engine") == "firecrawl_anydoc"]
    for row in anydoc_rows:
        mode = row.get("mode")
        if mode == "hosted":
            allowed = bool(row.get("transfer_approved")) and hosted_transfer_approved
            allowed &= bool(row.get("whole_document_sent"))
            allowed &= row.get("page_selection") in (None, "unsupported")
            if source_is_copyrighted and not bool(row.get("transfer_approved")):
                allowed = False
            gates["anydoc_transfer_policy"] &= allowed
            if not allowed:
                findings.append(_finding("ANYDOC_HOSTED_TRANSFER_NOT_APPROVED", "hosted anydoc OCR requires explicit whole-document transfer approval"))
        elif mode != "local":
            gates["anydoc_transfer_policy"] = False
            findings.append(_finding("ANYDOC_MODE_INVALID", "anydoc mode must be local or hosted"))

    disagreement_count = manifest.get("disagreement_count", 0)
    records = manifest.get("disagreement_records", [])
    if not isinstance(disagreement_count, int) or disagreement_count < 0:
        findings.append(_finding("DISAGREEMENT_COUNT_INVALID", "disagreement_count must be a non-negative integer"))
        disagreement_count = -1
    if not isinstance(records, list):
        records = []
        findings.append(_finding("DISAGREEMENT_RECORDS_INVALID", "disagreement_records must be a list"))
    gates["disagreement_resolved"] = disagreement_count == 0 or (
        len(records) == disagreement_count and all(isinstance(row, Mapping) and row.get("resolved") is True for row in records)
    )
    if not gates["disagreement_resolved"]:
        findings.append(_finding("OCR_DISAGREEMENT_UNRESOLVED", "all OCR disagreements must be resolved against the PDF source"))

    required_provenance = (
        "source_pdf_sha256",
        "source_manifest_sha256",
        "engine_versions",
        "engine_config_hashes",
        "candidate_output_sha256",
        "disagreement_records",
        "transfer_approval",
    )
    gates["provenance_complete"] = (
        all(key in manifest for key in required_provenance)
        and isinstance(manifest.get("transfer_approval"), bool)
    )
    if not gates["provenance_complete"]:
        findings.append(_finding("OCR_PROVENANCE_INCOMPLETE", "required OCR provenance fields are missing"))

    passed = all(gates.values()) and not findings
    return {"status": "PASS" if passed else "FAIL", "passed": passed, "gates": gates, "findings": findings}


def canonical_manifest_sha256(manifest: Mapping[str, Any]) -> str:
    """Hash provenance deterministically for reports and cache keys."""

    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
