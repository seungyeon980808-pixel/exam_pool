"""Fail-closed semantic checks for editable mathematical equations.

This module is deliberately independent of Hanword/COM so that a PDF-to-HWP
builder can validate its source manifest *before* opening Hanword.  A formula
is not considered preserved merely because its rendered text looks plausible:
operator bounds, script ownership, scopes, and piecewise rows are compared as
small structural signatures.

The accepted input is a LaTeX-like source formula and the linear HancomEQN
script emitted by a writer.  This is a QA boundary, not a renderer.  If an
equation cannot be represented by the supported grammar, the result is FAIL;
there is intentionally no plain-text or image fallback.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


_SUPPORTED_COMMANDS = {
    "alpha", "beta", "gamma", "delta", "epsilon", "theta", "lambda", "mu",
    "pi", "rho", "sigma", "phi", "omega", "Delta", "Gamma", "Theta",
    "Lambda", "Phi", "Sigma", "Omega", "times", "cdot", "div", "pm",
    "leq", "geq", "neq", "approx", "to", "infty", "perp", "parallel",
    "ell", "frac", "dfrac", "tfrac", "sqrt", "vec", "bar", "overline",
    "underline", "sin", "cos", "tan", "log", "ln", "exp", "lim", "sum",
    "prod", "int", "oint", "left", "right", "big", "Big", "bigl", "bigr",
    "Bigl", "Bigr", "lbrack", "rbrack", "mathrm", "text", "rm", "begin", "end",
}
_COMMAND_RE = re.compile(r"(?<!\\)\\([A-Za-z]+)")
_FALLBACK_RE = re.compile(r"(?:fallback|plain.?text|image.?equation|ocr.?placeholder|@@EQ|\[\[formula:)", re.I)
_KOREAN_RE = re.compile(r"[가-힣]")
_OPERATOR_RE = re.compile(r"(?P<op>\\?(?:sum|prod|int|oint|lim)|[Σ∏∫])")
_SCRIPT_RE = re.compile(r"(?P<marker>[_^])")


def _group(text: str, start: int) -> tuple[str, int] | None:
    """Return the balanced group at ``start`` and the exclusive end index."""

    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
    return None


def _operand(text: str, start: int) -> tuple[str, int] | None:
    while start < len(text) and text[start].isspace():
        start += 1
    if start >= len(text):
        return None
    if text[start] == "{":
        return _group(text, start)
    if text[start] == "\\" and start + 1 < len(text):
        match = re.match(r"\\[A-Za-z]+", text[start:])
        if match:
            return match.group(0), start + len(match.group(0))
    return text[start], start + 1


def _norm(value: str) -> str:
    """Normalize syntax spelling without changing mathematical scope."""

    result = str(value or "").strip()
    replacements = {
        r"\times": "×", r"\cdot": "·", r"\leq": "≤", r"\geq": "≥",
        r"\neq": "≠", r"\to": "→", r"\rightarrow": "→", r"\infty": "∞",
        r"\lbrack": "[", r"\rbrack": "]", r"\left": "", r"\right": "",
        r"\bigl": "", r"\bigr": "", r"\Bigl": "", r"\Bigr": "",
    }
    for old, new in replacements.items():
        result = result.replace(old, new)
    result = re.sub(r"\\([A-Za-z]+)", lambda match: match.group(1), result)
    return re.sub(r"\s+", "", result)


def _scope(value: str) -> str:
    """Canonicalise nested TeX fractions to Hancom's linear scope notation."""

    result = str(value or "")
    cursor = 0
    while True:
        match = re.search(r"\\(?:frac|dfrac|tfrac)\s*\{", result[cursor:])
        if not match:
            break
        start = cursor + match.start()
        first = _group(result, cursor + match.end() - 1)
        if not first:
            break
        numerator, end = first
        second = _operand(result, end)
        if not second:
            break
        denominator, final = second
        replacement = "{" + _scope(numerator) + "} over {" + _scope(denominator) + "}"
        result = result[:start] + replacement + result[final:]
        cursor = start + len(replacement)
    return result


def _fraction_signatures(value: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for match in re.finditer(r"\\?(?:frac|dfrac|tfrac)\s*\{", value):
        first = _group(value, match.end() - 1)
        if not first:
            result.append(("<unbalanced>", "<unbalanced>"))
            continue
        numerator, end = first
        second = _operand(value, end)
        if not second:
            result.append((_norm(numerator), "<missing>"))
            continue
        denominator, _ = second
        result.append((_norm(_scope(numerator)), _norm(_scope(denominator))))
    # Hancom's linear grammar uses ``{a} over {b}``.
    for match in re.finditer(r"\{", value):
        left = _group(value, match.start())
        if not left:
            continue
        numerator, end = left
        over = re.match(r"\s+over\s*", value[end:])
        if not over:
            continue
        second_start = end + over.end()
        denominator = _operand(value, second_start)
        if denominator:
            result.append((_norm(_scope(numerator)), _norm(_scope(denominator[0]))))
    return result


def _operator_signatures(value: str) -> list[dict[str, str | None]]:
    result: list[dict[str, str | None]] = []
    for match in _OPERATOR_RE.finditer(value):
        raw = match.group("op")
        op = {"Σ": "sum", "∏": "prod", "∫": "int"}.get(raw, raw.lstrip("\\"))
        cursor = match.end()
        lower = upper = None
        for marker in ("_", "^"):
            found = re.match(rf"\s*{re.escape(marker)}", value[cursor:])
            if not found:
                continue
            atom = _operand(value, cursor + found.end())
            if not atom:
                continue
            content, cursor = atom
            if marker == "_":
                lower = _norm(content)
            else:
                upper = _norm(content)
        result.append({"operator": op, "lower": lower, "upper": upper})
    return result


def _limit_signatures(value: str) -> list[tuple[str | None, str | None]]:
    result: list[tuple[str | None, str | None]] = []
    for item in _operator_signatures(value):
        if item["operator"] != "lim":
            continue
        lower = item["lower"]
        if lower is None:
            result.append((None, None))
            continue
        # Both ``x\\to0`` and ``x→0`` are accepted after normalization.
        arrow = re.split(r"→|to", lower, maxsplit=1)
        result.append((arrow[0], arrow[1] if len(arrow) == 2 else None))
    return result


def _integral_signatures(value: str) -> list[tuple[str | None, str | None]]:
    return [
        (item["lower"], item["upper"])
        for item in _operator_signatures(value)
        if item["operator"] in {"int", "oint"}
    ]


def _attachment_signatures(value: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    operator_spans: list[tuple[int, int]] = []
    for operator in _OPERATOR_RE.finditer(value):
        cursor = operator.end()
        for marker in ("_", "^"):
            found = re.match(rf"\s*{re.escape(marker)}", value[cursor:])
            if not found:
                continue
            marker_pos = cursor + found.end() - 1
            atom = _operand(value, cursor + found.end())
            if atom:
                operator_spans.append((marker_pos, atom[1]))
                cursor = atom[1]
    index = 0
    while index < len(value):
        if value[index] not in "_^":
            index += 1
            continue
        if any(start <= index < end for start, end in operator_spans):
            index += 1
            continue
        marker = value[index]
        owner_end = index
        owner_start = owner_end - 1
        # A marker following a grouped script belongs to the atom before the
        # group: in ``x_{n+1}^{2}`` the owner of ``^`` is x, not ``n+1}``.
        if owner_start >= 0 and value[owner_start] == "}":
            depth = 0
            while owner_start >= 0:
                if value[owner_start] == "}":
                    depth += 1
                elif value[owner_start] == "{":
                    depth -= 1
                    if depth == 0:
                        owner_start -= 1
                        break
                owner_start -= 1
        if owner_start >= 0 and value[owner_start] in "_^":
            owner_start -= 1
        while owner_start >= 0 and (value[owner_start].isalnum() or value[owner_start] in ")]."):
            owner_start -= 1
        owner = value[owner_start + 1:owner_end]
        # Operator bounds are validated by their dedicated signatures and
        # should not be double-counted as generic attachments.
        if owner in {"sum", "prod", "int", "oint", "lim", "Σ", "∏", "∫"}:
            index += 1
            continue
        atom = _operand(value, index + 1)
        if not atom:
            result.append({"marker": marker, "owner": owner, "value": None, "grouped": False})
            index += 1
            continue
        content, end = atom
        result.append({"marker": "subscript" if marker == "_" else "superscript", "owner": _norm(owner), "value": _norm(content), "grouped": value[index + 1:].lstrip().startswith("{")})
        index = end
    return result


def _row_signatures(value: str, command: str) -> list[str]:
    aliases = "cases" if command == "cases" else "(?:matrix|pmatrix|bmatrix|vmatrix)"
    begin = re.search(rf"\\begin\s*\{{{aliases}\}}", value)
    if begin:
        end = re.search(rf"\\end\s*\{{{aliases}\}}", value[begin.end():])
        if end:
            body = (value[begin.end():begin.end() + end.start()], begin.end() + end.end())
        else:
            body = None
    else:
        match = re.search(rf"\\?{aliases}\s*\{{", value)
        if not match:
            return []
        body = _group(value, match.end() - 1)
    if not body:
        return ["<unbalanced>"]
    content, _ = body
    # LaTeX uses \\ and Hancom uses # as the row separator.  Do not attempt
    # to repair a malformed expression by dropping rows.
    rows = re.split(r"(?:\\\\|#)", content)
    return [_norm(row) for row in rows]


def _radical_signatures(value: str) -> list[tuple[str | None, str | None]]:
    result: list[tuple[str | None, str | None]] = []
    for match in re.finditer(r"\\?sqrt\s*\{|root\s*\{", value):
        body = _group(value, match.end() - 1)
        if not body:
            result.append((None, "<unbalanced>"))
            continue
        radicand, end = body
        index: str | None = None
        if value[match.start():match.end()].startswith("root"):
            of = re.match(r"\s*of\s*", value[end:])
            if of:
                index = _norm(radicand)
                radicand_atom = _operand(value, end + of.end())
                radicand = radicand_atom[0] if radicand_atom else "<missing>"
        result.append((index, _norm(radicand)))
    return result


def _vector_signatures(value: str) -> list[str]:
    result: list[str] = []
    for match in re.finditer(r"\\?(?:vec|bar|overline)\s*\{", value):
        body = _group(value, match.end() - 1)
        result.append(_norm(body[0]) if body else "<unbalanced>")
    return result


def analyze_formula(value: str) -> dict[str, Any]:
    """Return structural signatures and grammar defects for one formula."""

    source = str(value or "").strip()
    errors: list[str] = []
    depth = 0
    escaped = False
    for char in source:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                errors.append("unbalanced_braces")
                break
    if depth:
        errors.append("unbalanced_braces")
    unsupported = sorted(set(_COMMAND_RE.findall(source)) - _SUPPORTED_COMMANDS)
    if unsupported:
        errors.append("unsupported_command:" + ",".join(unsupported))
    return {
        "operators": _operator_signatures(source),
        "limits": _limit_signatures(source),
        "integrals": _integral_signatures(source),
        "attachments": _attachment_signatures(source),
        "fractions": sorted(_fraction_signatures(source)),
        "radicals": _radical_signatures(source),
        "cases": _row_signatures(source, "cases"),
        "matrices": _row_signatures(source, "matrix"),
        "vectors": _vector_signatures(source),
        "errors": errors,
        "has_korean": bool(_KOREAN_RE.search(source)),
    }


def _finding(code: str, message: str, *, index: int = 0, source: str = "", actual: str = "") -> dict[str, Any]:
    return {"code": code, "message": message, "index": index, "source": source, "actual": actual}


def validate_formula_pair(source: str, actual: str, *, index: int = 1) -> list[dict[str, Any]]:
    """Compare source and generated equation structure; never silently repair."""

    source = str(source or "").strip()
    actual = str(actual or "").strip()
    findings: list[dict[str, Any]] = []
    if not actual:
        return [_finding("FORMULA_SCRIPT_EMPTY", "generated native equation script is empty", index=index, source=source, actual=actual)]
    if _FALLBACK_RE.search(actual):
        findings.append(_finding("FORMULA_FALLBACK", "generated equation contains a fallback marker", index=index, source=source, actual=actual))
    if _KOREAN_RE.search(actual):
        findings.append(_finding("FORMULA_PROSE_IN_SCRIPT", "Korean prose must be a text block, not an equation script", index=index, source=source, actual=actual))
    actual_features = analyze_formula(actual)
    source_features = analyze_formula(source)
    if source_features["errors"]:
        findings.append(_finding("SOURCE_FORMULA_INVALID", ", ".join(source_features["errors"]), index=index, source=source, actual=actual))
    if actual_features["errors"]:
        findings.append(_finding("GENERATED_FORMULA_INVALID", ", ".join(actual_features["errors"]), index=index, source=source, actual=actual))
    checks = (
        ("operators", "OPERATOR_BOUNDS_MISMATCH", "operator or Σ/Π/∫ bounds changed"),
        ("limits", "LIMIT_APPROACH_MISMATCH", "lim approach variable, target, or direction changed"),
        ("integrals", "INTEGRAL_SCOPE_MISMATCH", "integral bounds changed"),
        ("attachments", "SCRIPT_OWNER_MISMATCH", "subscript/superscript owner or scope changed"),
        ("fractions", "FRACTION_SCOPE_MISMATCH", "fraction numerator/denominator scope changed"),
        ("radicals", "RADICAL_SCOPE_MISMATCH", "radical index or radicand scope changed"),
        ("cases", "CASES_STRUCTURE_MISMATCH", "piecewise row/condition structure changed"),
        ("matrices", "MATRIX_STRUCTURE_MISMATCH", "matrix row/column structure changed"),
        ("vectors", "VECTOR_SCOPE_MISMATCH", "vector argument scope changed"),
    )
    for key, code, message in checks:
        expected = source_features[key]
        observed = actual_features[key]
        if key == "attachments":
            # Grouping is a writer-side safety requirement, not a semantic
            # change: canonical source may use ``a_i`` while Hancom output
            # must use ``a_{i}``.  Ownership and value remain exact.
            expected = [(item.get("marker"), item.get("owner"), item.get("value")) for item in expected]
            observed = [(item.get("marker"), item.get("owner"), item.get("value")) for item in observed]
        if key == "operators" and any(item["operator"] in {"sum", "prod"} and (item["lower"] is None or item["upper"] is None) for item in expected):
            findings.append(_finding("OPERATOR_BOUNDS_MISSING", "Σ/Π source occurrence has no explicit lower and upper bound", index=index, source=source, actual=actual))
        if expected != observed:
            findings.append(_finding(code, message, index=index, source=source, actual=actual))
    for attachment in actual_features["attachments"]:
        if not attachment.get("grouped"):
            findings.append(_finding("SCRIPT_UNSCOPED_ATOM", "generated subscript/superscript operand is not grouped", index=index, source=source, actual=actual))
            break
    return findings


def validate_native_equation_document(expected: Sequence[str], actual: Sequence[str], *, metadata: Sequence[Mapping[str, Any] | None] | None = None) -> dict[str, Any]:
    """Strict document-level gate for native equation scripts.

    ``expected`` is the reviewed source manifest sequence.  ``actual`` must be
    the sequence read back from HWPX/COM ``eqed`` controls.  A shortfall,
    excess, empty script, unsupported command, or fallback marker is FAIL.
    """

    expected_list = [str(item or "").strip() for item in expected]
    actual_list = [str(item or "").strip() for item in actual]
    findings: list[dict[str, Any]] = []
    if len(expected_list) != len(actual_list):
        findings.append(_finding("FORMULA_COUNT_MISMATCH", "source and native equation counts differ"))
    for index, (source, observed) in enumerate(zip(expected_list, actual_list), 1):
        findings.extend(validate_formula_pair(source, observed, index=index))
        if metadata and index <= len(metadata) and metadata[index - 1]:
            meta = metadata[index - 1] or {}
            if meta.get("fallback") or meta.get("used_fallback"):
                findings.append(_finding("FORMULA_FALLBACK", "equation metadata reports fallback", index=index, source=source, actual=observed))
    status = "PASS" if not findings and expected_list else "FAIL"
    return {
        "status": status,
        "expected_formula_count": len(expected_list),
        "actual_formula_count": len(actual_list),
        "findings": findings,
        "gates": {
            "formula_count_exact": len(expected_list) == len(actual_list),
            "native_scripts_nonempty": all(bool(item) for item in actual_list),
            "no_formula_fallback": not any(item["code"] == "FORMULA_FALLBACK" for item in findings),
            "semantic_scope_exact": not any(item["code"].endswith("_MISMATCH") or item["code"].endswith("_MISSING") or item["code"] == "SCRIPT_UNSCOPED_ATOM" for item in findings),
        },
    }


__all__ = [
    "analyze_formula",
    "validate_formula_pair",
    "validate_native_equation_document",
]
