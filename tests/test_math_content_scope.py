from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from app.math_content_scope import (
    SCHEMA_VERSION,
    canonical_scope_sha256,
    load_scope_manifest,
    validate_content_scope,
)


def _region(region_id: str, role: str, item_id: str | None, order: int, column: str = "left") -> dict:
    value = {
        "region_id": region_id,
        "role": role,
        "bbox": [10, 10 + order * 20, 200, 25 + order * 20],
        "crop_sha256": "c" * 64,
        "reading_order": order,
        "column_id": column,
        "review_status": "VERIFIED",
    }
    if item_id:
        value["item_id"] = item_id
    return value


def _item(item_id: str, sequence: int, problem_regions: list[str], solution_regions: list[str]) -> dict:
    return {
        "item_id": item_id,
        "sequence": sequence,
        "section_id": "THEME-01",
        "printed_label": f"3단계 문제 {sequence}",
        "problem_first_sentence": f"합성 문제 {sequence}의 첫 문장",
        "solution_first_sentence": f"합성 문제 {sequence}의 풀이 첫 문장",
        "problem_text_sha256": "d" * 64,
        "solution_text_sha256": "e" * 64,
        "problem_region_ids": problem_regions,
        "solution_region_ids": solution_regions,
        "mapping_evidence": [
            "section",
            "printed_label",
            "problem_first_sentence",
            "solution_content",
        ],
        "review_status": "VERIFIED",
        "uncertainties": [],
    }


def _manifest() -> dict:
    pages = [
        {
            "document": "problem",
            "pdf_page": 1,
            "printed_page": None,
            "page_role": "study_plan",
            "review_status": "VERIFIED",
            "regions": [_region("P-FRONT", "excluded", None, 1, "full")],
        },
        {
            "document": "problem",
            "pdf_page": 2,
            "printed_page": 6,
            "page_role": "mixed",
            "review_status": "VERIFIED",
            "regions": [
                _region("P-CONCEPT", "excluded", None, 1, "left"),
                _region("P-Q1", "problem", "SYN-001", 2, "left"),
                _region("P-Q2", "problem", "SYN-002", 3, "right"),
            ],
        },
        {
            "document": "problem",
            "pdf_page": 3,
            "printed_page": 7,
            "page_role": "problem",
            "review_status": "VERIFIED",
            "regions": [_region("P-Q3", "problem", "SYN-003", 1, "full")],
        },
        {
            "document": "solution",
            "pdf_page": 1,
            "printed_page": 2,
            "page_role": "solution",
            "review_status": "VERIFIED",
            "regions": [
                _region("S-Q1", "solution", "SYN-001", 1, "left"),
                _region("S-Q2A", "solution", "SYN-002", 2, "right"),
            ],
        },
        {
            "document": "solution",
            "pdf_page": 2,
            "printed_page": 3,
            "page_role": "solution",
            "review_status": "VERIFIED",
            "regions": [
                _region("S-Q2B", "solution", "SYN-002", 1, "left"),
                _region("S-Q3", "solution", "SYN-003", 2, "right"),
            ],
        },
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "VERIFIED",
        "uncertainties": [],
        "source_documents": {
            "problem": {"sha256": "a" * 64, "page_count": 3},
            "solution": {"sha256": "b" * 64, "page_count": 2},
        },
        "scope": {
            "policy": "problem_solution_regions_only",
            "page_index_base": 1,
            "problem_min_pdf_page": 2,
            "output_layout_mode": "item_reflow",
        },
        "pages": pages,
        "ocr_inputs": {
            "problem_region_ids": ["P-Q1", "P-Q2", "P-Q3"],
            "solution_region_ids": ["S-Q1", "S-Q2A", "S-Q2B", "S-Q3"],
        },
        "items": [
            _item("SYN-001", 1, ["P-Q1"], ["S-Q1"]),
            _item("SYN-002", 2, ["P-Q2"], ["S-Q2A", "S-Q2B"]),
            _item("SYN-003", 3, ["P-Q3"], ["S-Q3"]),
        ],
        "endnote_policy": {
            "mode": "one_per_problem_item",
            "mapping": "stable_item_id_exact",
            "page_round_robin": False,
            "expected_problem_items": 3,
            "expected_solution_items": 3,
            "expected_endnotes": 3,
        },
    }


def _codes(report: dict) -> set[str]:
    return {row["code"] for row in report["findings"]}


def test_reviewed_region_scope_and_exact_mapping_pass() -> None:
    report = validate_content_scope(_manifest())
    assert report["status"] == "PASS"
    assert report["counts"]["items"] == 3
    assert report["counts"]["expected_endnotes"] == 3
    assert all(report["gates"].values())


def test_first_nine_pages_excluded_and_mixed_page_ten_only_ocr_problem_regions() -> None:
    value = _manifest()
    problem_regions = [
        _region("P-CONCEPT", "excluded", None, 1, "left"),
        _region("P-Q1", "problem", "SYN-001", 2, "left"),
        _region("P-Q2", "problem", "SYN-002", 3, "right"),
        _region("P-Q3", "problem", "SYN-003", 4, "right"),
    ]
    front_pages = [
        {
            "document": "problem",
            "pdf_page": page,
            "printed_page": None,
            "page_role": "frontmatter",
            "review_status": "VERIFIED",
            "regions": [_region(f"P-FRONT-{page}", "excluded", None, 1, "full")],
        }
        for page in range(1, 10)
    ]
    mixed_page = {
        "document": "problem",
        "pdf_page": 10,
        "printed_page": 6,
        "page_role": "mixed",
        "review_status": "VERIFIED",
        "regions": problem_regions,
    }
    solution_pages = [page for page in value["pages"] if page["document"] == "solution"]
    value["pages"] = front_pages + [mixed_page] + solution_pages
    value["source_documents"]["problem"]["page_count"] = 10
    value["scope"]["problem_min_pdf_page"] = 10
    report = validate_content_scope(value)
    assert report["status"] == "PASS"
    assert report["counts"]["problem_ocr_regions"] == 3


def test_pages_before_user_start_are_not_ocr_inputs() -> None:
    value = _manifest()
    front = value["pages"][0]["regions"][0]
    front["role"] = "problem"
    front["item_id"] = "SYN-001"
    value["ocr_inputs"]["problem_region_ids"].append("P-FRONT")
    report = validate_content_scope(value)
    assert report["status"] == "FAIL"
    assert "OCR_BEFORE_USER_START_PAGE" in _codes(report)


def test_excluded_region_leaking_into_ocr_fails() -> None:
    value = _manifest()
    value["ocr_inputs"]["problem_region_ids"].append("P-CONCEPT")
    report = validate_content_scope(value)
    assert "OCR_EXCLUDED_REGION_LEAK" in _codes(report)


def test_one_page_with_two_items_remains_two_endnotes() -> None:
    value = _manifest()
    value["endnote_policy"]["expected_endnotes"] = 2
    report = validate_content_scope(value)
    assert report["status"] == "FAIL"
    assert "ENDNOTE_POLICY_INVALID" in _codes(report)


def test_page_round_robin_mapping_is_forbidden() -> None:
    value = _manifest()
    value["endnote_policy"]["mapping"] = "page_round_robin_practical"
    value["endnote_policy"]["page_round_robin"] = True
    report = validate_content_scope(value)
    assert "ENDNOTE_PAGE_ROUND_ROBIN_FORBIDDEN" in _codes(report)


def test_solution_continuation_must_be_owned_exactly_once() -> None:
    value = _manifest()
    value["items"][1]["solution_region_ids"] = ["S-Q2A"]
    report = validate_content_scope(value)
    assert "ITEM_REGION_SET_MISMATCH" in _codes(report)


def test_number_only_mapping_evidence_is_not_enough() -> None:
    value = _manifest()
    value["items"][0]["mapping_evidence"] = ["printed_label"]
    report = validate_content_scope(value)
    assert "ITEM_IDENTITY_UNVERIFIED" in _codes(report)


def test_duplicate_reading_order_in_two_column_page_fails() -> None:
    value = _manifest()
    value["pages"][1]["regions"][2]["reading_order"] = 2
    report = validate_content_scope(value)
    assert "READING_ORDER_DUPLICATE" in _codes(report)


def test_scope_hash_is_stable_and_policy_is_checked_in() -> None:
    value = _manifest()
    assert canonical_scope_sha256(value) == canonical_scope_sha256(deepcopy(value))
    path = Path(__file__).parents[1] / "config" / "math_content_scope_policy_v1.json"
    policy = load_scope_manifest(path)
    assert policy["manifest_schema"] == SCHEMA_VERSION
    assert policy["mixed_page_rule"] == "crop_and_ocr_only_reviewed_problem_or_solution_regions"
    assert policy["item_mapping"]["page_round_robin"] == "forbidden"
