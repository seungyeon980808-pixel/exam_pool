"""Synthetic regression cases for the math-equation QA boundary.

These fixtures intentionally contain no exam text.  Each test represents a
failure observed in OCR/PDF-to-HWP conversion and proves that the native
equation gate fails closed rather than accepting a visually plausible result.
"""

from app.math_formula_semantic_qa import (
    analyze_formula,
    validate_formula_pair,
    validate_native_equation_document,
)


def codes(source: str, actual: str) -> set[str]:
    return {row["code"] for row in validate_formula_pair(source, actual)}


def test_sigma_and_product_require_both_bounds_and_preserve_scope() -> None:
    source = r"\sum_{k=1}^{n}\frac{1}{k(k+2)}+\prod_{i=1}^{m}a_i"
    actual = r"sum_{k=1}^{n}{1} over {k(k+2)}+prod_{i=1}^{m}a_{i}"
    assert validate_formula_pair(source, actual) == []

    missing_lower = r"sum^{n}{1} over {k(k+2)}+prod_{i=1}^{m}a_{i}"
    assert "OPERATOR_BOUNDS_MISMATCH" in codes(source, missing_lower)


def test_limit_checks_approach_variable_target_and_one_sided_direction() -> None:
    source = r"\lim_{h\to0^+}\frac{f(x+h)-f(x)}{h}"
    actual = r"lim_{h→0^+}{f(x+h)-f(x)} over {h}"
    assert validate_formula_pair(source, actual) == []
    assert "LIMIT_APPROACH_MISMATCH" in codes(source, r"lim_{x→0^+}{f(x+h)-f(x)} over {h}")
    assert "LIMIT_APPROACH_MISMATCH" in codes(source, r"lim_{h→0}{f(x+h)-f(x)} over {h}")


def test_subscript_superscript_ownership_is_not_inferred_from_neighbouring_text() -> None:
    source = r"x_{n+1}^{2}+a_i"
    actual = r"x_{n+1}^{2}+a_{i}"
    assert validate_formula_pair(source, actual) == []
    assert "SCRIPT_OWNER_MISMATCH" in codes(source, r"x_{n+1}^{2}+b_{i}")
    assert "SCRIPT_UNSCOPED_ATOM" in codes(source, r"x_{n+1}^2+a_{i}")


def test_piecewise_cases_preserve_rows_and_conditions() -> None:
    source = r"f(x)=\begin{cases}x^{2}&x<0\\x+1&x\geq0\end{cases}"
    actual = r"f(x)=cases {x^{2}&x<0#x+1&x≥0}"
    assert validate_formula_pair(source, actual) == []
    assert "CASES_STRUCTURE_MISMATCH" in codes(source, r"f(x)=cases {x^{2}&x<0#x+1&x>0}")


def test_nested_fraction_and_radical_scopes_are_compared_recursively() -> None:
    source = r"\frac{1+\frac{a}{b}}{\sqrt{x+1}}"
    actual = r"{1+{a} over {b}} over {sqrt {x+1}}"
    assert validate_formula_pair(source, actual) == []
    assert "FRACTION_SCOPE_MISMATCH" in codes(source, r"{1+{a} over {b}} over {sqrt {x+2}}")
    assert "RADICAL_SCOPE_MISMATCH" in codes(source, r"{1+{a} over {b}} over {sqrt {x+2}}")


def test_integral_bounds_and_differential_are_preserved() -> None:
    source = r"\int_{-1}^{2}\sqrt{x+1}\,dx"
    actual = r"int_{-1}^{2}sqrt {x+1} dx"
    assert validate_formula_pair(source, actual) == []
    assert "INTEGRAL_SCOPE_MISMATCH" in codes(source, r"int_{0}^{2}sqrt {x+1} dx")


def test_vector_and_matrix_structure_is_native_formula_content() -> None:
    source = r"\vec{AB}=\begin{pmatrix}1\\2\end{pmatrix}"
    actual = r"vec {AB}=matrix {1#2}"
    assert validate_formula_pair(source, actual) == []
    assert "VECTOR_SCOPE_MISMATCH" in codes(source, r"vec {AC}=matrix {1#2}")
    assert "MATRIX_STRUCTURE_MISMATCH" in codes(source, r"vec {AB}=matrix {1#2#3}")


def test_unsupported_ocr_command_is_not_silently_dropped() -> None:
    findings = validate_formula_pair(r"\sum_{i=1}^{n}x_i", r"sum_{i=1}^{n}x_{i}\prodr")
    assert any(row["code"] == "GENERATED_FORMULA_INVALID" for row in findings)
    assert "unsupported_command:prodr" in analyze_formula(r"x+\prodr")["errors"]


def test_document_gate_rejects_count_shortfall_and_fallback_markers() -> None:
    report = validate_native_equation_document(
        [r"\sum_{i=1}^{n}i", r"\int_{0}^{1}x\,dx"],
        [r"sum_{i=1}^{n}i", "plain-text fallback"],
    )
    assert report["status"] == "FAIL"
    assert report["gates"]["formula_count_exact"] is True
    assert report["gates"]["no_formula_fallback"] is False

    short = validate_native_equation_document(["x", "y"], ["x"])
    assert short["status"] == "FAIL"
    assert short["gates"]["formula_count_exact"] is False


def test_empty_native_script_is_a_hard_failure() -> None:
    report = validate_native_equation_document([r"x^2"], [""])
    assert report["status"] == "FAIL"
    assert report["gates"]["native_scripts_nonempty"] is False
    assert any(row["code"] == "FORMULA_SCRIPT_EMPTY" for row in report["findings"])
