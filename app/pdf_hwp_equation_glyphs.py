"""Evidence-backed semantic catalog for the installed HyhwpEQ font."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final


VERIFIED_EQUATION_FONT: Final = "HyhwpEQ"
LOCAL_FONT_PATH: Final = r"C:\Windows\Fonts\HYHWPEQ.TTF"
LOCAL_FONT_SHA256: Final = (
    "a3a3cd992a89ac38e7707b58143ebdb1da9511d3c462f019cda7f92c30467807"
)
FRACTION_BAR: Final = "\ue06d"
RADICAL_SIGN: Final = "\ue05c"


@dataclass(frozen=True, slots=True)
class GlyphMapping:
    codepoint: int
    formula: str
    mapping_source: str
    proof: tuple[str, ...]


def _legacy(codepoint: int, formula: str, *extra_proof: str) -> GlyphMapping:
    return GlyphMapping(
        codepoint, formula, "existing-corpus-verified-map",
        ("legacy-corpus", *extra_proof),
    )


def _corpus(codepoint: int, formula: str, *papers: str) -> GlyphMapping:
    return GlyphMapping(codepoint, formula, "pdf-context+local-font-outline", papers)


_MAPPINGS: Final = (
    _legacy(0xE000, "A"), _legacy(0xE001, "B"), _legacy(0xE003, "D"),
    _legacy(0xE004, "E"), _legacy(0xE005, "F"), _legacy(0xE007, "H"),
    _legacy(0xE008, "I"), _legacy(0xE00B, "L"), _legacy(0xE00C, "M"),
    _legacy(0xE00D, "N"), _legacy(0xE00F, "P"), _legacy(0xE010, "Q"),
    _legacy(0xE011, "R", "local-HyhwpEQ-v1.13-independent-specimen"),
    _legacy(0xE012, "S"), _legacy(0xE013, "T"), _legacy(0xE015, "V"),
    _legacy(0xE016, "W", "local-HyhwpEQ-v1.13-independent-specimen"),
    _corpus(0xE00A, "K", "c1_2027_06:q17", "c1_2026_11:q16"),
    _corpus(0xE019, "Z", "c1_2026_06:q12", "local-HyhwpEQ-v1.13"),
    _legacy(0xE034, "1"), _legacy(0xE035, "2"), _legacy(0xE036, "3"),
    _legacy(0xE037, "4"), _legacy(0xE038, "5"), _legacy(0xE039, "6"),
    _legacy(0xE03A, "7"), _legacy(0xE03B, "8"), _legacy(0xE03C, "9"),
    _legacy(0xE03D, "0"),
    _corpus(0xE043, "*", "c1_2026_06:q12", "local-HyhwpEQ-v1.13"),
    _legacy(0xE044, "("), _legacy(0xE045, ")"), _legacy(0xE046, "-"),
    _legacy(0xE047, "="), _legacy(0xE048, "+"),
    _corpus(0xE049, "[", "c2_2027_06:q8", "c2_2026_11:q2"),
    _corpus(0xE04A, "]", "c2_2027_06:q8", "c2_2026_11:q2"),
    _corpus(0xE04D, "|", "c2_2026_11:q18", "e2_2025_11:q13"),
    _legacy(0xE04F, ":"), _legacy(0xE052, ","), _legacy(0xE053, "."),
    _legacy(0xE054, "/"), _legacy(0xE055, "<"), _legacy(0xE056, ">"),
    _corpus(0xE088, "\\Delta", "c2_2027_06:q13", "c2_2026_11:q7"),
    _legacy(0xE099, "\\Phi"),
    _corpus(0xE09D, "\\alpha", "b1_2026_11:q10", "local-HyhwpEQ-v1.13"),
    _corpus(0xE09E, "\\beta", "b1_2027_06:q11", "b1_2026_11:q10"),
    _corpus(0xE0A0, "\\delta", "c1_2027_06:q7", "c1_2026_11:q6"),
    _legacy(0xE0A4, "\\theta"), _legacy(0xE0A7, "\\lambda"),
    _legacy(0xE0AD, "\\rho"),
    _corpus(0xE0B2, "\\chi", "c1_2026_06:q7", "c2_2026_11:q20"),
    _legacy(0xE0BB, "\\ell"), _legacy(0xE0E5, "a"),
    _corpus(0xE0E6, "b", "c1_2027_06:q5", "c1_2026_11:q7"),
    _legacy(0xE0E7, "c"), _legacy(0xE0E8, "d"),
    _legacy(0xE0E9, "e", "local-HyhwpEQ-v1.13-independent-specimen"),
    _legacy(0xE0EA, "f"), _legacy(0xE0EB, "g"), _legacy(0xE0EC, "h"),
    _legacy(0xE0ED, "i"), _legacy(0xE0EF, "k"),
    _corpus(0xE0F0, "l", "c1_2027_06:q5", "c1_2026_11:q5"),
    _legacy(0xE0F1, "m"), _legacy(0xE0F2, "n"), _legacy(0xE0F4, "p"),
    _corpus(0xE0F5, "q", "c1_2027_06:q12", "c1_2026_11:q11"),
    _legacy(0xE0F6, "r"),
    _corpus(0xE0F7, "s", "c1_2027_06:q2", "c1_2026_11:q3"),
    _legacy(0xE0F8, "t"), _legacy(0xE0FA, "v"), _legacy(0xE0FB, "w"),
    _legacy(0xE0FC, "x"), _legacy(0xE0FD, "y"),
    _corpus(0xE0FE, "z", "c1_2026_09:q20", "c1_2026_06:q14"),
    _legacy(0xE101, "|"),
)


GLYPH_MAPPINGS: Final = {mapping.codepoint: mapping for mapping in _MAPPINGS}

STRUCTURAL_GLYPH_PROOFS: Final = {
    ord(RADICAL_SIGN): GlyphMapping(
        ord(RADICAL_SIGN), "\\sqrt", "pdf-geometry+local-font-outline",
        ("p1_2020_11:q16", "e1_2025_11:q18"),
    ),
    ord(FRACTION_BAR): GlyphMapping(
        ord(FRACTION_BAR), "\\frac", "pdf-geometry+local-font-outline",
        ("p1_2022_09:q6", "p1_2022_06:q20"),
    ),
}

# Compatibility view only. Runtime authorization happens per occurrence.
VERIFIED_PUA_GLYPHS: Final = {
    chr(codepoint): mapping.formula for codepoint, mapping in GLYPH_MAPPINGS.items()
}
