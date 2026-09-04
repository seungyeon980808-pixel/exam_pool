"""Fail-closed page/region scope and problem-solution mapping validation.

Scanned workbooks commonly mix covers, study plans, concept explanations,
worked examples and exercises.  OCR must therefore consume reviewed regions,
not every PDF page.  This module validates the copyright-free manifest which
records those decisions before OCR or HWP/endnote construction starts.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "math-content-scope-manifest-v1"
PAGE_INDEX_BASE = 1
INCLUDED_REGION_ROLES = {"problem", "solution"}
PAGE_ROLES = {
    "frontmatter",
    "cover",
    "toc",
    "study_plan",
    "instructions",
    "concept",
    "divider",
    "answer_key",
    "problem",
    "solution",
    "mixed",
    "blank",
    "advertisement",
}
REQUIRED_MAPPING_EVIDENCE = {
    "section",
    "printed_label",
    "problem_first_sentence",
    "solution_content",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _finding(code: str, message: str, **context: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **context}


def _sha256(value: Any) -> bool:
    return bool(_SHA256_RE.fullmatch(str(value or "").lower()))


def _bbox(value: Any) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        return False
    try:
        x0, y0, x1, y1 = (float(item) for item in value)
    except (TypeError, ValueError):
        return False
    return x0 >= 0 and y0 >= 0 and x1 > x0 and y1 > y0


def load_scope_manifest(path: str | Path) -> Mapping[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def canonical_scope_sha256(manifest: Mapping[str, Any]) -> str:
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_content_scope(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate reviewed page roles, OCR regions and exact item mapping."""

    findings: list[dict[str, Any]] = []
    if not isinstance(manifest, Mapping):
        return {
            "status": "FAIL",
            "passed": False,
            "gates": {"manifest_object": False},
            "findings": [_finding("SCOPE_MANIFEST_NOT_OBJECT", "scope manifest must be an object")],
        }

    gates: dict[str, bool] = {
        "schema_version": manifest.get("schema_version") == SCHEMA_VERSION,
        "reviewed_status": manifest.get("status") == "VERIFIED" and manifest.get("uncertainties") == [],
        "source_documents": True,
        "page_inventory_complete": True,
        "page_roles_reviewed": True,
        "region_inventory_valid": True,
        "ocr_regions_exact": True,
        "user_scope_respected": True,
        "item_inventory_exact": True,
        "problem_solution_mapping_exact": True,
        "endnote_policy_exact": True,
    }

    if not gates["schema_version"]:
        findings.append(_finding("SCOPE_SCHEMA_INVALID", f"schema_version must be {SCHEMA_VERSION}"))
    if not gates["reviewed_status"]:
        findings.append(_finding("SCOPE_NOT_VERIFIED", "status must be VERIFIED and uncertainties must be empty"))

    source_documents = manifest.get("source_documents")
    page_counts: dict[str, int] = {}
    if not isinstance(source_documents, Mapping):
        source_documents = {}
    for document in ("problem", "solution"):
        record = source_documents.get(document)
        valid = (
            isinstance(record, Mapping)
            and _sha256(record.get("sha256"))
            and isinstance(record.get("page_count"), int)
            and int(record.get("page_count", 0)) > 0
        )
        if valid:
            page_counts[document] = int(record["page_count"])
        else:
            gates["source_documents"] = False
            findings.append(_finding("SOURCE_DOCUMENT_INVALID", "source PDF hash/page_count missing", document=document))

    pages = manifest.get("pages")
    if not isinstance(pages, list):
        pages = []
        gates["page_inventory_complete"] = False
        findings.append(_finding("PAGE_INVENTORY_INVALID", "pages must be a list"))

    page_numbers: defaultdict[str, list[int]] = defaultdict(list)
    regions: dict[str, Mapping[str, Any]] = {}
    included_regions: defaultdict[str, set[str]] = defaultdict(set)
    region_owner: dict[str, str] = {}
    page_region_orders: defaultdict[tuple[str, int], list[int]] = defaultdict(list)

    for page in pages:
        if not isinstance(page, Mapping):
            gates["page_inventory_complete"] = False
            findings.append(_finding("PAGE_RECORD_INVALID", "page record must be an object"))
            continue
        document = str(page.get("document", ""))
        pdf_page = page.get("pdf_page")
        role = page.get("page_role")
        if document not in {"problem", "solution"} or not isinstance(pdf_page, int) or pdf_page < PAGE_INDEX_BASE:
            gates["page_inventory_complete"] = False
            findings.append(_finding("PAGE_ID_INVALID", "document/pdf_page is invalid", document=document, pdf_page=pdf_page))
            continue
        page_numbers[document].append(pdf_page)
        if role not in PAGE_ROLES or page.get("review_status") != "VERIFIED":
            gates["page_roles_reviewed"] = False
            findings.append(_finding("PAGE_ROLE_UNVERIFIED", "page role must be reviewed", document=document, pdf_page=pdf_page, role=role))
        page_regions = page.get("regions", [])
        if not isinstance(page_regions, list):
            page_regions = []
            gates["region_inventory_valid"] = False
            findings.append(_finding("PAGE_REGIONS_INVALID", "regions must be a list", document=document, pdf_page=pdf_page))
        seen_roles: set[str] = set()
        for region in page_regions:
            if not isinstance(region, Mapping):
                gates["region_inventory_valid"] = False
                findings.append(_finding("REGION_RECORD_INVALID", "region must be an object", document=document, pdf_page=pdf_page))
                continue
            region_id = str(region.get("region_id", "")).strip()
            region_role = str(region.get("role", "")).strip()
            seen_roles.add(region_role)
            order = region.get("reading_order")
            valid = (
                bool(region_id)
                and region_id not in regions
                and region_role in {"problem", "solution", "excluded"}
                and _bbox(region.get("bbox"))
                and _sha256(region.get("crop_sha256"))
                and region.get("review_status") == "VERIFIED"
                and isinstance(order, int)
                and order > 0
                and bool(str(region.get("column_id", "")).strip())
            )
            if not valid:
                gates["region_inventory_valid"] = False
                findings.append(_finding("REGION_INVALID", "region identity/bbox/hash/order/review is invalid", document=document, pdf_page=pdf_page, region_id=region_id))
                continue
            if region_role == "problem" and document != "problem" or region_role == "solution" and document != "solution":
                gates["region_inventory_valid"] = False
                findings.append(_finding("REGION_DOCUMENT_ROLE_MISMATCH", "region role does not match source document", region_id=region_id, document=document, role=region_role))
            regions[region_id] = region
            page_region_orders[(document, pdf_page)].append(order)
            if region_role in INCLUDED_REGION_ROLES:
                included_regions[document].add(region_id)
                owner = str(region.get("item_id", "")).strip()
                if not owner:
                    gates["region_inventory_valid"] = False
                    findings.append(_finding("REGION_ITEM_ID_MISSING", "included OCR region requires item_id", region_id=region_id))
                else:
                    region_owner[region_id] = owner
        if role == "mixed" and not (seen_roles & INCLUDED_REGION_ROLES and "excluded" in seen_roles):
            gates["region_inventory_valid"] = False
            findings.append(_finding("MIXED_PAGE_REGION_PARTITION_MISSING", "mixed page requires included and excluded regions", document=document, pdf_page=pdf_page))

    for key, orders in page_region_orders.items():
        if len(orders) != len(set(orders)):
            gates["region_inventory_valid"] = False
            findings.append(_finding("READING_ORDER_DUPLICATE", "reading_order must be unique within each page", document=key[0], pdf_page=key[1]))

    for document, expected_count in page_counts.items():
        actual = page_numbers.get(document, [])
        if Counter(actual) != Counter(range(PAGE_INDEX_BASE, expected_count + PAGE_INDEX_BASE)):
            gates["page_inventory_complete"] = False
            findings.append(_finding("PAGE_INVENTORY_INCOMPLETE", "every physical PDF page must be classified exactly once", document=document, expected=expected_count, actual=len(actual)))

    ocr_inputs = manifest.get("ocr_inputs")
    if not isinstance(ocr_inputs, Mapping):
        ocr_inputs = {}
    for document in ("problem", "solution"):
        key = f"{document}_region_ids"
        ids = ocr_inputs.get(key, [])
        actual = [str(value) for value in ids] if isinstance(ids, list) else []
        expected = included_regions.get(document, set())
        if len(actual) != len(set(actual)) or set(actual) != expected:
            gates["ocr_regions_exact"] = False
            extra = sorted(set(actual) - expected)
            missing = sorted(expected - set(actual))
            code = "OCR_EXCLUDED_REGION_LEAK" if extra else "OCR_INCLUDED_REGION_MISSING"
            findings.append(_finding(code, "OCR inputs must equal reviewed included regions", document=document, extra=extra, missing=missing))

    scope = manifest.get("scope")
    if (
        not isinstance(scope, Mapping)
        or scope.get("policy") != "problem_solution_regions_only"
        or scope.get("page_index_base") != PAGE_INDEX_BASE
        or scope.get("output_layout_mode") not in {"item_reflow", "source_region_layout"}
    ):
        gates["user_scope_respected"] = False
        findings.append(_finding(
            "SCOPE_POLICY_INVALID",
            "scope must use one-based problem_solution_regions_only and an explicit output layout mode",
        ))
    else:
        minimum = scope.get("problem_min_pdf_page")
        if minimum is not None:
            if not isinstance(minimum, int) or minimum < PAGE_INDEX_BASE:
                gates["user_scope_respected"] = False
                findings.append(_finding("PROBLEM_MIN_PAGE_INVALID", "problem_min_pdf_page must be a positive integer"))
            else:
                early = []
                for page in pages:
                    if isinstance(page, Mapping) and page.get("document") == "problem" and isinstance(page.get("pdf_page"), int) and page["pdf_page"] < minimum:
                        early.extend(
                            str(region.get("region_id"))
                            for region in page.get("regions", [])
                            if isinstance(region, Mapping) and region.get("role") == "problem"
                        )
                if early:
                    gates["user_scope_respected"] = False
                    findings.append(_finding("OCR_BEFORE_USER_START_PAGE", "problem regions occur before the user-approved start page", region_ids=early, minimum=minimum))

    items = manifest.get("items")
    if not isinstance(items, list):
        items = []
    item_ids: list[str] = []
    sequences: list[int] = []
    referenced_problem_regions: list[str] = []
    referenced_solution_regions: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            gates["item_inventory_exact"] = False
            findings.append(_finding("ITEM_RECORD_INVALID", "item must be an object"))
            continue
        item_id = str(item.get("item_id", "")).strip()
        sequence = item.get("sequence")
        problem_ids = item.get("problem_region_ids", [])
        solution_ids = item.get("solution_region_ids", [])
        evidence = set(item.get("mapping_evidence", [])) if isinstance(item.get("mapping_evidence"), list) else set()
        item_ids.append(item_id)
        if isinstance(sequence, int):
            sequences.append(sequence)
        valid_identity = (
            bool(item_id)
            and isinstance(sequence, int)
            and sequence > 0
            and bool(str(item.get("section_id", "")).strip())
            and bool(str(item.get("printed_label", "")).strip())
            and bool(str(item.get("problem_first_sentence", "")).strip())
            and bool(str(item.get("solution_first_sentence", "")).strip())
            and _sha256(item.get("problem_text_sha256"))
            and _sha256(item.get("solution_text_sha256"))
            and item.get("review_status") == "VERIFIED"
            and item.get("uncertainties") == []
            and REQUIRED_MAPPING_EVIDENCE.issubset(evidence)
        )
        if not valid_identity:
            gates["item_inventory_exact"] = False
            findings.append(_finding("ITEM_IDENTITY_UNVERIFIED", "item identity, text hashes or mapping evidence is incomplete", item_id=item_id))
        if not isinstance(problem_ids, list) or not problem_ids or not isinstance(solution_ids, list) or not solution_ids:
            gates["problem_solution_mapping_exact"] = False
            findings.append(_finding("ITEM_SOLUTION_MAPPING_MISSING", "every item requires problem and solution regions", item_id=item_id))
            continue
        problem_ids = [str(value) for value in problem_ids]
        solution_ids = [str(value) for value in solution_ids]
        referenced_problem_regions.extend(problem_ids)
        referenced_solution_regions.extend(solution_ids)
        for region_id in (*problem_ids, *solution_ids):
            if region_id not in regions or region_owner.get(region_id) != item_id:
                gates["problem_solution_mapping_exact"] = False
                findings.append(_finding("ITEM_REGION_OWNERSHIP_MISMATCH", "item references a missing or differently owned region", item_id=item_id, region_id=region_id))

    if not items or len(item_ids) != len(set(item_ids)) or sorted(sequences) != list(range(1, len(items) + 1)):
        gates["item_inventory_exact"] = False
        findings.append(_finding("ITEM_SET_INVALID", "item IDs must be unique and sequence must be contiguous", item_count=len(items)))
    for document, refs in (("problem", referenced_problem_regions), ("solution", referenced_solution_regions)):
        expected = included_regions.get(document, set())
        if len(refs) != len(set(refs)) or set(refs) != expected:
            gates["problem_solution_mapping_exact"] = False
            findings.append(_finding("ITEM_REGION_SET_MISMATCH", "included regions must belong to exactly one item", document=document))

    endnote = manifest.get("endnote_policy")
    endnote_valid = (
        isinstance(endnote, Mapping)
        and endnote.get("mode") == "one_per_problem_item"
        and endnote.get("mapping") == "stable_item_id_exact"
        and endnote.get("page_round_robin") is False
        and endnote.get("expected_problem_items") == len(items)
        and endnote.get("expected_solution_items") == len(items)
        and endnote.get("expected_endnotes") == len(items)
    )
    if not endnote_valid:
        gates["endnote_policy_exact"] = False
        code = "ENDNOTE_PAGE_ROUND_ROBIN_FORBIDDEN" if isinstance(endnote, Mapping) and endnote.get("page_round_robin") is not False else "ENDNOTE_POLICY_INVALID"
        findings.append(_finding(code, "endnotes must map one reviewed problem item to one reviewed solution item by stable ID"))

    passed = all(gates.values()) and not findings
    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "gates": gates,
        "counts": {
            "classified_problem_pages": len(page_numbers.get("problem", [])),
            "classified_solution_pages": len(page_numbers.get("solution", [])),
            "problem_ocr_regions": len(included_regions.get("problem", set())),
            "solution_ocr_regions": len(included_regions.get("solution", set())),
            "items": len(items),
            "expected_endnotes": len(items),
        },
        "findings": findings,
    }
