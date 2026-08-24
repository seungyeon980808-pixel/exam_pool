"""Canonical inline formula markup shared by authoring, preview, and HWP export.

ExamPool stores formulas as ``[[formula:<LaTeX-like source>]]`` inside existing
text fields.  The source follows 5E's formula grammar, so the semantic source is
preserved even when a consumer can only show a linear-text fallback.
"""
from __future__ import annotations

import copy
import re


FORMULA_RE = re.compile(r"\[\[formula:(.+?)\]\]", re.S)
_LINEAR_FRACTION_RE = re.compile(
    r"(?<![A-Za-z0-9_\]])(?P<num>\d+)\s*/\s*(?P<den>\d+)(?P<symbol>[A-Za-z])?"
)
_ATTACHED_QUANTITY_RE = re.compile(
    r"(?<![A-Za-z0-9_\]])(?P<coefficient>\d+(?:\.\d+)?)(?P<symbol>[A-Za-z])(?![A-Za-z0-9_])"
)
_CONTEXT_QUANTITY_RE = re.compile(
    r"(?P<prefix>(?:질량|높이|속력|속도|가속도|힘|전류|전압|저항|에너지|거리|시간)"
    r"(?:이|가|은|는)?\s*)(?P<symbol>[A-Za-z](?:_[A-Za-z0-9]+|\^[A-Za-z0-9]+)?)"
    r"(?=(?:인|이고|이며|이다|일|[,.?)]))"
)
_ASK_QUANTITY_RE = re.compile(r"(?<![A-Za-z0-9_])(?P<symbol>[A-Za-z])(?=는\?)")
_COMMANDS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
    "theta": "θ", "lambda": "λ", "mu": "μ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "phi": "φ", "omega": "ω", "Delta": "Δ",
    "Gamma": "Γ", "Theta": "Θ", "Lambda": "Λ", "Phi": "Φ", "Sigma": "Σ",
    "Omega": "Ω", "times": "×", "cdot": "·", "div": "÷", "pm": "±",
    "leq": "≤", "geq": "≥", "neq": "≠", "approx": "≈", "to": "→",
    "infty": "∞", "perp": "⊥", "parallel": "∥", "ell": "ℓ",
}
_SUPER = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
_SUB = str.maketrans("0123456789+-=()aeoxhklmnpst", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜ")


def formula_sources(text: str) -> list[str]:
    return [m.group(1).strip() for m in FORMULA_RE.finditer(text or "")]


def _normalize_reconstruction_text(text: str) -> str:
    """Promote unambiguous OCR-style quantities to canonical formula runs."""
    formulas: list[str] = []

    def hold(source: str) -> str:
        formulas.append(source.strip())
        return f"\u0000FORMULA{len(formulas) - 1}\u0000"

    value = FORMULA_RE.sub(lambda match: hold(match.group(1)), str(text or ""))
    value = _LINEAR_FRACTION_RE.sub(
        lambda match: hold(
            rf"\frac{{{match.group('num')}}}{{{match.group('den')}}}{match.group('symbol') or ''}"
        ),
        value,
    )
    value = _ATTACHED_QUANTITY_RE.sub(
        lambda match: hold(f"{match.group('coefficient')}{match.group('symbol')}"),
        value,
    )
    value = _CONTEXT_QUANTITY_RE.sub(
        lambda match: match.group("prefix") + hold(match.group("symbol")),
        value,
    )
    value = _ASK_QUANTITY_RE.sub(lambda match: hold(match.group("symbol")), value)
    for index, source in enumerate(formulas):
        value = value.replace(f"\u0000FORMULA{index}\u0000", f"[[formula:{source}]]")
    return value


def normalize_reconstruction_draft_formulas(draft: dict) -> dict:
    """Return a reconstruction draft with semantic math preserved for HWP export."""
    normalized = copy.deepcopy(draft)
    for field in ("passage", "ask", "explanation"):
        if isinstance(normalized.get(field), str):
            normalized[field] = _normalize_reconstruction_text(normalized[field])
    for field in ("bogi_items", "choices"):
        rows = normalized.get(field)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("text"), str):
                row["text"] = _normalize_reconstruction_text(row["text"])
    return normalized


def validate_formula_source(source: str) -> list[str]:
    errors: list[str] = []
    source = str(source or "").strip()
    if not source:
        return ["수식 원문이 비어 있습니다."]
    depth = 0
    for char in source:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                errors.append("수식 중괄호의 순서가 올바르지 않습니다.")
                break
    if depth:
        errors.append("수식 중괄호가 닫히지 않았습니다.")
    if re.search(r"[₀-₉⁰-⁹]", source):
        errors.append("완성된 유니코드 첨자 대신 v_0, x^2 형식을 사용하세요.")
    if re.search(r"[α-ωΑ-Ω]", source):
        errors.append("그리스 문자를 직접 넣지 말고 \\theta, \\Delta 같은 원문을 사용하세요.")
    allowed_commands = set(_COMMANDS) | {
        "bar", "frac", "sqrt", "vec", "sin", "cos", "tan", "log", "ln",
        "exp", "lim", "leftarrow", "rightarrow", "leftrightarrow",
    }
    for command in re.findall(r"\\([A-Za-z]+)", source):
        if command not in allowed_commands:
            errors.append(f"지원하지 않는 수식 명령입니다: \\{command}")
    return errors


def validate_formula_markup(text: str) -> list[str]:
    raw = text or ""
    errors: list[str] = []
    if raw.count("[[formula:") != len(FORMULA_RE.findall(raw)):
        errors.append("닫히지 않은 [[formula:...]] 수식 구간이 있습니다.")
    outside = FORMULA_RE.sub("", raw)
    math_commands = r"frac|sqrt|vec|theta|lambda|mu|pi|Delta|alpha|beta|gamma|omega|times"
    if re.search(rf"\\(?:{math_commands})\b", outside):
        errors.append("수식 명령은 반드시 [[formula:...]] 구간 안에 넣으세요.")
    if re.search(r"[₀-₉⁰-⁹]", outside):
        errors.append("본문에 완성된 유니코드 첨자를 넣지 말고 [[formula:v_0]] 형식을 사용하세요.")
    for source in formula_sources(raw):
        errors.extend(validate_formula_source(source))
    return errors


def _read_group(source: str, start: int) -> tuple[str, int] | None:
    if start >= len(source) or source[start] != "{":
        return None
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1:index], index + 1
    return None


def formula_to_plain(source: str) -> str:
    """Readable Unicode fallback; the original source remains stored unchanged."""
    value = str(source or "").strip()
    # Resolve nested structural commands from the inside out.
    for _ in range(12):
        changed = False
        for command in ("bar", "frac", "sqrt", "vec"):
            match = re.search(rf"\\?{command}\s*\{{", value)
            if not match:
                continue
            first = _read_group(value, match.end() - 1)
            if not first:
                continue
            body, end = first
            if command == "frac":
                second_start = end
                while second_start < len(value) and value[second_start].isspace():
                    second_start += 1
                second = _read_group(value, second_start)
                if not second:
                    continue
                denominator, final = second
                replacement = f"({formula_to_plain(body)})/({formula_to_plain(denominator)})"
            elif command == "sqrt":
                final = end
                replacement = f"√({formula_to_plain(body)})"
            elif command == "bar":
                final = end
                replacement = "".join(f"{char}\u0305" for char in formula_to_plain(body))
            else:
                final = end
                replacement = f"{formula_to_plain(body)}⃗"
            value = value[:match.start()] + replacement + value[final:]
            changed = True
            break
        if not changed:
            break
    value = re.sub(r"\\([A-Za-z]+)\s*", lambda m: _COMMANDS.get(m.group(1), m.group(1)), value)
    value = re.sub(r"(?<=[A-Za-z)])_\{?([0-9A-Za-z+\-=()]+)\}?",
                   lambda m: m.group(1).translate(_SUB), value)
    value = re.sub(r"(?<=[A-Za-z0-9)])\^\{?([0-9+\-=()n]+)\}?",
                   lambda m: m.group(1).translate(_SUPER), value)
    return value.replace("{", "").replace("}", "").strip()


def render_formula_markup(text: str) -> str:
    return FORMULA_RE.sub(lambda m: formula_to_plain(m.group(1)), text or "")


def to_hwppalette_markup(text: str) -> str:
    """Escape prose braces while preserving formulas as HwpPalette rich runs."""
    formulas: list[str] = []
    def hold(match: re.Match) -> str:
        source = match.group(1).strip()
        # Hancom's equation grammar keeps consuming an unbraced script through
        # the following operator/identifier (``d_2=`` became subscript ``2=``).
        # Preserve the authoring grammar while making every single-character
        # sub/superscript boundary explicit at the HwpPalette export boundary.
        source = re.sub(r"_(?!\{)([A-Za-z0-9])", r"_{\1}", source)
        source = re.sub(r"\^(?!\{)([A-Za-z0-9])", r"^{\1}", source)
        formulas.append(source)
        return f"\u0000FORMULA{len(formulas) - 1}\u0000"
    protected = FORMULA_RE.sub(hold, text or "").replace("}", "\\}")
    for index, source in enumerate(formulas):
        protected = protected.replace(f"\u0000FORMULA{index}\u0000", f"\\수식{{{source}}}")
    return protected
