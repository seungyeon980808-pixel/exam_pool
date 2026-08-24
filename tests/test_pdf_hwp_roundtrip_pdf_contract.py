from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from app.pdf_hwp_pipeline_models import ConversionUnit
from app.pdf_hwp_roundtrip_pdf_contract import (
    ExpectedFifthChoice,
    GeneratedPdfContractRequest,
    GeneratedPdfIssue,
    generated_pdf_contract_request,
    inspect_generated_pdf_contract,
)
from app.pdf_hwp_roundtrip_units import load_prepared_units


COMBINED_GREEN = Path(
    ".omo/evidence/ulw/pdf-hwp-roundtrip-v1/"
    "G001-implement-and-execute-a-resumable-to/a1/"
    "w10-hapdap-choice-final-output/converted.pdf"
)
FULL_GREEN = Path(
    ".omo/evidence/ulw/pdf-hwp-roundtrip-v1/"
    "G001-implement-and-execute-a-resumable-to/a1/"
    "w12-hapdap-full-final-output/converted.pdf"
)
FINAL_NAMESPACE = Path(
    "data/pdf_hwp/roundtrip_harness/approved-first-run/namespaces/"
    "5c2ea3441d9aa4338fc9/sources"
)
FONT = Path(r"C:\Windows\Fonts\malgun.ttf")


def _write_pdf(path: Path, lines: tuple[tuple[float, float, str], ...]) -> None:
    with fitz.open() as document:
        page = document.new_page(width=420, height=500)
        for x, y, value in lines:
            page.insert_text(
                (x, y), value, fontsize=11, fontname="contract-font", fontfile=FONT,
            )
        document.save(path)


def _codes(
    path: Path,
    selected: tuple[int, ...],
    hapdap: tuple[int, ...] = (),
    expected: tuple[ExpectedFifthChoice, ...] = (),
) -> tuple[GeneratedPdfIssue, ...]:
    result = inspect_generated_pdf_contract(
        GeneratedPdfContractRequest(path, selected, hapdap, expected),
    )
    return tuple(issue.code for issue in result.issues)


def test_complete_hapdap_item_passes_on_one_choice_baseline(tmp_path: Path) -> None:
    source = tmp_path / "complete.pdf"
    _write_pdf(source, (
        (36, 60, "1. prompt"),
        (36, 90, "<보기> ㄱ. one ㄴ. two ㄷ. three"),
        (36, 120, "① ㄱ ② ㄴ ③ ㄱ, ㄴ ④ ㄴ, ㄷ ⑤ ㄱ, ㄴ, ㄷ"),
    ))

    assert _codes(source, (1,), (1,)) == ()


def test_prepared_units_supply_per_item_fifth_choice_expectations(tmp_path: Path) -> None:
    direct = ConversionUnit(192, "\\수능정답1대사진5선지\\\n192\nstem\nfigure\nask\nA\nB\nA, B\nB, C\n\\수식{A}, \\수식{B}, \\수식{C}")
    hapdap = ConversionUnit(234, "\\수능합답1대사진5선지\\\n234\nstem\nfigure\nask\nㄱ\nㄴ\nㄷ\nㄱ\nㄷ\nㄱ, ㄴ\nㄴ, ㄷ\nㄱ, ㄴ, ㄷ")
    ordinary = ConversionUnit(24, "\\수능정답0사진5선지\\\n24\nstem\nask\none\ntwo\nthree\nfour\na long prose fifth choice")

    request = generated_pdf_contract_request(tmp_path / "generated.pdf", (ordinary, direct, hapdap))

    assert request.selected_item_numbers == (24, 192, 234)
    assert request.hapdap_item_numbers == (234,)
    assert tuple(value.prepared_text for value in request.expected_fifth_choices) == (
        r"\수식{A}, \수식{B}, \수식{C}", "ㄱ, ㄴ, ㄷ",
    )


def test_missing_choice_and_hapdap_claim_have_stable_codes(tmp_path: Path) -> None:
    source = tmp_path / "incomplete.pdf"
    _write_pdf(source, (
        (36, 60, "1. prompt"),
        (36, 90, "<보기> ㄱ. one ㄴ. two"),
        (36, 120, "① ㄱ ② ㄴ ③ ㄱ, ㄴ ④ ㄴ, ㄷ"),
    ))

    assert _codes(source, (1,), (1,)) == (
        GeneratedPdfIssue.MISSING_CHOICE,
        GeneratedPdfIssue.MISSING_BOGI_CLAIM,
    )


def test_shorter_valid_hapdap_fifth_choice_does_not_require_long_combo(
    tmp_path: Path,
) -> None:
    source = tmp_path / "short-fifth.pdf"
    _write_pdf(source, (
        (36, 60, "1. prompt"),
        (36, 90, "<보기> ㄱ. one ㄴ. two ㄷ. three"),
        (36, 120, "① ㄱ ② ㄴ ③ ㄷ ④ ㄱ, ㄴ ⑤ ㄴ, ㄷ"),
    ))

    assert _codes(source, (1,), (1,)) == ()


def test_expected_formula_fifth_choice_must_share_marker_baseline(
    tmp_path: Path,
) -> None:
    source = tmp_path / "formula-wrap.pdf"
    _write_pdf(source, (
        (36, 60, "192. prompt"),
        (36, 100, "① A ② C ③ A, B ④ B, C ⑤ A, B,"),
        (36, 124, "C"),
    ))

    assert _codes(
        source, (192,), expected=(ExpectedFifthChoice(192, r"\수식{A}, \수식{B}, \수식{C}"),),
    ) == (GeneratedPdfIssue.FIFTH_CHOICE_WRAPPED,)


def test_page_number_after_complete_plain_fifth_choice_is_not_part_of_choice(
    tmp_path: Path,
) -> None:
    source = tmp_path / "footer.pdf"
    _write_pdf(source, (
        (36, 60, "6. prompt"),
        (36, 100, "① ㄱ ② ㄷ ③ ㄱ,ㄴ ④ ㄴ,ㄷ ⑤ ㄱ,ㄴ,ㄷ"),
        (200, 470, "8"),
    ))

    assert _codes(
        source, (6,), expected=(ExpectedFifthChoice(6, "ㄱ,ㄴ,ㄷ"),),
    ) == ()


def test_item_text_containing_an_undetected_next_leader_is_boundary_spill(
    tmp_path: Path,
) -> None:
    source = tmp_path / "spill.pdf"
    _write_pdf(source, (
        (36, 60, "1. prompt"),
        (36, 90, "① A ② B ③ C ④ D ⑤ E"),
        (100, 180, "2. leaked next item"),
        (36, 300, "3. aligned peer"),
    ))

    assert _codes(source, (1,)) == (GeneratedPdfIssue.ITEM_BOUNDARY_SPILL,)


def test_decimal_inside_claim_is_not_an_item_boundary_spill(tmp_path: Path) -> None:
    source = tmp_path / "decimal.pdf"
    _write_pdf(source, (
        (36, 60, "18. prompt"),
        (36, 90, "ㄱ. value is 12.5"),
        (36, 120, "① A ② B ③ C ④ D ⑤ E"),
    ))

    assert _codes(source, (18,)) == ()


def test_source_text_physical_line_proves_continuation_but_not_true_wrap(
    tmp_path: Path,
) -> None:
    complete = tmp_path / "complete-continuation.pdf"
    wrapped = tmp_path / "wrapped-continuation.pdf"
    for path, fifth_lines in (
        (complete, ((36, 130, "① ㄱ ② ㄴ ③ ㄷ ④ ㄱ, ㄴ ⑤ ㄱ, ㄴ, ㄷ"),)),
        (wrapped, ((36, 130, "① ㄱ ② ㄴ ③ ㄷ ④ ㄱ, ㄴ ⑤ ㄱ, ㄴ,"), (36, 150, "ㄷ"))),
    ):
        with fitz.open() as document:
            page = document.new_page(width=420, height=500)
            page.insert_text((36, 60), "10. LEFT", fontsize=11, fontname="contract-font", fontfile=FONT)
            page.insert_text((230, 300), "11. RIGHT STEM", fontsize=11, fontname="contract-font", fontfile=FONT)
            next_page = document.new_page(width=420, height=500)
            next_page.insert_text((230, 60), "13. RIGHT", fontsize=11, fontname="contract-font", fontfile=FONT)
            for x, y, text in fifth_lines:
                next_page.insert_text((x, y), text, fontsize=8, fontname="contract-font", fontfile=FONT)
            next_page.insert_text((36, 300), "12. LEFT", fontsize=11, fontname="contract-font", fontfile=FONT)
            document.save(path)

    expected = (ExpectedFifthChoice(11, "ㄱ, ㄴ, ㄷ"),)
    assert _codes(complete, (11,), expected=expected) == ()
    assert _codes(wrapped, (11,), expected=expected) == (
        GeneratedPdfIssue.FIFTH_CHOICE_WRAPPED,
    )


def test_combined_real_hapdap_pilot_satisfies_all_four_item_contracts() -> None:
    if not COMBINED_GREEN.is_file():
        pytest.skip("combined q234/q235/q237/q238 pilot is unavailable")

    result = inspect_generated_pdf_contract(
        GeneratedPdfContractRequest(
            COMBINED_GREEN,
            (234, 235, 237, 238),
            (234, 235, 237, 238),
            tuple(ExpectedFifthChoice(number, "ㄱ, ㄴ, ㄷ") for number in (234, 235)),
        ),
    )

    assert result.issues == ()
    assert tuple(item.item_number for item in result.items) == (234, 235, 237, 238)
    assert all(len(item.baselines) > 0 for item in result.items)


def test_full_real_q192_formula_fifth_choice_is_complete_on_one_baseline() -> None:
    if not FULL_GREEN.is_file():
        pytest.skip("full q192 corrected pilot is unavailable")
    result = inspect_generated_pdf_contract(GeneratedPdfContractRequest(
        FULL_GREEN, (192,), (),
        (ExpectedFifthChoice(192, r"\수식{A}, \수식{B}, \수식{C}"),),
    ))

    assert result.issues == ()


def test_final_namespace_distinguishes_true_wraps_from_page_footer() -> None:
    cases = (
        ("e2_2023_11-*", 4, (GeneratedPdfIssue.FIFTH_CHOICE_WRAPPED,)),
        ("e2_2025_09-*", 5, (GeneratedPdfIssue.FIFTH_CHOICE_WRAPPED,)),
        ("c1_2024_06-*", 6, ()),
    )
    observed = []
    for pattern, number, expected in cases:
        roots = tuple(FINAL_NAMESPACE.glob(pattern))
        if len(roots) != 1:
            pytest.skip(f"final source unavailable: {pattern}")
        prepared_path = roots[0] / "prepared-units.json"
        if json.loads(prepared_path.read_text(encoding="utf-8"))["schema_version"] != 2:
            pytest.skip("final namespace contains intentionally rejected schema v1 evidence")
        units = load_prepared_units(prepared_path).prepared_units
        result = inspect_generated_pdf_contract(generated_pdf_contract_request(
            roots[0] / "conversion" / "converted.pdf", units,
        ))
        codes = tuple(issue.code for issue in result.issues if issue.item_number == number)
        observed.append(codes)
        assert codes == expected
    assert len(observed) == 3
