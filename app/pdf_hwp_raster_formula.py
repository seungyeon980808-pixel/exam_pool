"""Deterministic formula recovery for OCR-flattened exam text."""
from __future__ import annotations

import re
from typing import Final

from .formula_markup import FORMULA_RE


_EXPLICIT_INTERVAL: Final = re.compile(
    r"(?<![A-Za-z0-9])t\s*(?P<first>[0-9])\s*[~～∼-]\s*t?\s*"
    r"(?P<second>[0-9])(?=\s*동안)",
    re.IGNORECASE,
)
_COMPACT_INTERVAL: Final = re.compile(
    r"(?<![A-Za-z0-9])t(?P<first>[0-9])(?P<second>[0-9])(?=\s*동안)",
    re.IGNORECASE,
)
_INITIAL_VELOCITY_PRODUCT: Final = re.compile(
    r"(?<![A-Za-z0-9])(?P<sign>[+-]?)v0t(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_RELATION: Final = re.compile(
    r"(?<![A-Za-z0-9])(?P<left>[A-Za-z][0-9]?)(?P<operator>[=<>≤≥])"
    r"(?P<right>[+-]?(?:[A-Za-z][0-9]?|[0-9]+(?:\.[0-9]+)?))(?![A-Za-z0-9])",
)
_INDEXED_VARIABLE: Final = re.compile(
    r"(?<![A-Za-z0-9])(?P<symbol>[tTvVfFIPSE])(?P<index>[0-9])(?![A-Za-z0-9])",
)
_NAMED_SUBSCRIPT: Final = re.compile(r"(?<![A-Za-z0-9])(?P<token>Imax|VS)(?![A-Za-z0-9])")


def _indexed_atom(value: str) -> str:
    sign = value[0] if value.startswith(("+", "-")) else ""
    atom = value[len(sign):]
    match = re.fullmatch(r"(?P<symbol>[A-Za-z])(?P<index>[0-9])", atom)
    if match is None:
        return value
    return f"{sign}{match.group('symbol')}_{{{match.group('index')}}}"


def restore_raster_formulas(text: str) -> str:
    """Promote only unambiguous OCR conventions to canonical editable formulas."""
    formulas: list[str] = []

    def hold(source: str) -> str:
        formulas.append(source.strip())
        return f"\u0000RASTERFORMULA{len(formulas) - 1}\u0000"

    def replace_interval(match: re.Match[str]) -> str:
        return (
            hold(f"t_{{{match.group('first')}}}")
            + "~"
            + hold(f"t_{{{match.group('second')}}}")
        )

    value = FORMULA_RE.sub(lambda match: hold(match.group(1)), text)
    value = _EXPLICIT_INTERVAL.sub(replace_interval, value)
    value = _COMPACT_INTERVAL.sub(replace_interval, value)
    value = _INITIAL_VELOCITY_PRODUCT.sub(
        lambda match: hold(f"{match.group('sign')}v_{{0}}t"), value,
    )
    value = _RELATION.sub(
        lambda match: hold(
            _indexed_atom(match.group("left"))
            + match.group("operator")
            + _indexed_atom(match.group("right"))
        ),
        value,
    )
    value = _NAMED_SUBSCRIPT.sub(
        lambda match: hold("I_{max}" if match.group("token") == "Imax" else "V_{S}"),
        value,
    )
    value = _INDEXED_VARIABLE.sub(
        lambda match: hold(f"{match.group('symbol')}_{{{match.group('index')}}}"), value,
    )
    for index, source in enumerate(formulas):
        value = value.replace(f"\u0000RASTERFORMULA{index}\u0000", f"[[formula:{source}]]")
    return value
