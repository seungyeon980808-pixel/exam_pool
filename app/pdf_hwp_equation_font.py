"""Per-occurrence HyhwpEQ identity verification for PDF equation glyphs."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Final

import fitz
from fontTools.ttLib import TTLibError

from .pdf_hwp_font_trace import trace_font_glyphs
from .pdf_hwp_font_view import FontView as _FontView, font_view as _font_view
from .pdf_hwp_equation_glyphs import (
    ALTERNATE_EMBEDDED_GLYPH_PROOFS, FRACTION_BAR, GLYPH_MAPPINGS, LOCAL_FONT_PATH, LOCAL_FONT_SHA256,
    RADICAL_SIGN, SCOPED_EMBEDDED_GLYPH_PROOFS, VECTOR_HEAD, VERIFIED_EQUATION_FONT,
)


_PUA_MIN: Final = 0xE000
_PUA_MAX: Final = 0xF8FF


@dataclass(frozen=True, slots=True)
class FontGlyphEvidence:
    codepoint: int
    font_name: str
    font_xref: int | None
    embedded_font_sha256: str | None
    glyph_id: int
    local_glyph_id: int | None
    embedded_outline_sha256: str | None
    local_outline_sha256: str | None
    embedded_metrics: tuple[int, int] | None
    local_metrics: tuple[int, int] | None
    bbox: tuple[float, float, float, float]
    origin: tuple[float, float]
    font_size: float
    verified: bool
    reason: str


@dataclass(frozen=True, slots=True)
class EquationFontContext:
    verified_codepoints: frozenset[int]
    evidence: tuple[FontGlyphEvidence, ...]
    rejections: tuple[str, ...]
    scoped_mappings: tuple[tuple[int, str], ...] = ()


def _local_view() -> _FontView | None:
    path = Path(LOCAL_FONT_PATH)
    if not path.is_file():
        return None
    data = path.read_bytes()
    if sha256(data).hexdigest() != LOCAL_FONT_SHA256:
        return None
    try:
        return _font_view(data)
    except TTLibError:
        return None


def _base_name(value: str) -> str:
    name = value.rsplit("+", 1)[-1]
    return name.split("-Identity-", 1)[0]


def _candidate_fonts(
    document: fitz.Document, page: fitz.Page,
) -> tuple[
    tuple[tuple[int, str, _FontView], ...],
    tuple[tuple[int, str], ...],
]:
    result: list[tuple[int, str, _FontView]] = []
    unreadable: list[tuple[int, str]] = []
    for font in page.get_fonts(full=True):
        xref, base_name = int(font[0]), str(font[3])
        if _base_name(base_name) != VERIFIED_EQUATION_FONT:
            continue
        try:
            data = bytes(document.extract_font(xref)[3])
            if data:
                digest = sha256(data).hexdigest()
                try:
                    result.append((xref, digest, _font_view(data)))
                except TTLibError:
                    unreadable.append((xref, digest))
        except (KeyError, IndexError, ValueError):
            continue
    return tuple(result), tuple(unreadable)


def _font_identities(
    document: fitz.Document, page: fitz.Page, font_name: str,
) -> tuple[tuple[int, str], ...]:
    result = []
    for font in page.get_fonts(full=True):
        xref, base_name = int(font[0]), str(font[3])
        if _base_name(base_name) != font_name:
            continue
        try:
            data = bytes(document.extract_font(xref)[3])
        except (KeyError, IndexError, ValueError):
            continue
        if data:
            result.append((xref, sha256(data).hexdigest()))
    return tuple(result)


def _trace_occurrences(
    page: fitz.Page, clip: tuple[float, float, float, float],
) -> tuple[
    tuple[
        int, int, str, str | None, float, tuple[float, float],
        tuple[float, float, float, float],
    ], ...
]:
    bounds = fitz.Rect(clip)
    result = []
    traced = trace_font_glyphs(page)
    if traced is None:
        spans = (
            (
                int(codepoint), int(glyph_id), str(trace.get("font", "")), None,
                float(trace.get("size", 0.0)), tuple(float(v) for v in origin),
                tuple(float(value) for value in bbox),
            )
            for trace in page.get_texttrace()
            for codepoint, glyph_id, origin, bbox in trace.get("chars", ())
        )
    else:
        spans = (
            (
                glyph.codepoint, glyph.glyph_id, glyph.font_name,
                glyph.font_sha256, glyph.font_size, glyph.origin, glyph.bbox,
            )
            for glyph in traced
        )
    for occurrence in spans:
        codepoint, _, _, _, _, _, box = occurrence
        center = fitz.Point((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        if _PUA_MIN <= codepoint <= _PUA_MAX and bounds.contains(center):
            result.append(occurrence)
    return tuple(result)


def verify_equation_context(
    document: fitz.Document,
    page: fitz.Page,
    clip: tuple[float, float, float, float],
) -> EquationFontContext:
    """Authorize a codepoint only when every clip occurrence has one outline match."""
    local = _local_view()
    candidates, unreadable = (
        _candidate_fonts(document, page) if local is not None else ((), ())
    )
    font_xrefs = tuple(sorted({
        int(font[0]) for font in page.get_fonts(full=True)
        if _base_name(str(font[3])) == VERIFIED_EQUATION_FONT
    }))
    ambiguous_font_reason = (
        f"ambiguous-font-resource-xref:{','.join(map(str, font_xrefs))}"
        if len(font_xrefs) > 1 else None
    )
    evidence: list[FontGlyphEvidence] = []
    scoped_mappings: list[tuple[int, str]] = []
    for occurrence in _trace_occurrences(page, clip):
        codepoint, glyph_id, font_name, trace_sha, size, origin, bbox = occurrence
        local_gid = local.gid_for_codepoint(codepoint) if local is not None else None
        local_digest = local.digest(local_gid) if local is not None and local_gid is not None else None
        local_metrics = local.metric(local_gid) if local is not None and local_gid is not None else None
        readable_bound = tuple(item for item in candidates if item[1] == trace_sha)
        unreadable_bound = tuple(item for item in unreadable if item[1] == trace_sha)
        bound_count = len(readable_bound) + len(unreadable_bound)
        binding_reason = None
        selected = None
        if font_name == VERIFIED_EQUATION_FONT:
            if trace_sha is None:
                binding_reason = "font-occurrence-xref-unavailable"
            elif bound_count != 1:
                binding_reason = (
                    ambiguous_font_reason
                    or "font-occurrence-xref-mismatch"
                )
            elif unreadable_bound:
                binding_reason = f"embedded-font-unreadable:{unreadable_bound[0][0]}"
            else:
                selected = readable_bound[0]
        embedded_digest = selected[2].digest(glyph_id) if selected is not None else None
        embedded_metrics = selected[2].metric(glyph_id) if selected is not None else None
        alternate_match = (
            selected is not None
            and (
                codepoint, selected[1], embedded_digest, embedded_metrics,
            ) in ALTERNATE_EMBEDDED_GLYPH_PROOFS
        )
        scoped_match = SCOPED_EMBEDDED_GLYPH_PROOFS.get(
            (codepoint, font_name, trace_sha, glyph_id)
        )
        outline_match = (
            selected is not None
            and (
                (embedded_digest == local_digest and embedded_metrics == local_metrics)
                or alternate_match
            )
        )
        known = (
            codepoint in GLYPH_MAPPINGS
            or chr(codepoint) in {FRACTION_BAR, RADICAL_SIGN, VECTOR_HEAD}
            or scoped_match is not None
        )
        identity = _font_identities(document, page, font_name)
        match = (
            (selected[0], selected[1], embedded_digest, embedded_metrics)
            if selected is not None else
            (*unreadable_bound[0], None, None) if len(unreadable_bound) == 1 else
            (*identity[0], None, None)
            if font_name != VERIFIED_EQUATION_FONT and len(identity) == 1 else
            (None, None, None, None)
        )
        reason = (
            "unregistered-codepoint" if not known else
            "verified-scoped-embedded-glyph" if scoped_match is not None else
            "wrong-font" if font_name != VERIFIED_EQUATION_FONT else
            "local-font-missing-or-changed" if local is None else
            binding_reason if binding_reason is not None else
            "verified-alternate-embedded-outline" if alternate_match else
            "verified" if outline_match else
            "embedded-outline-match-count:0"
        )
        verified = reason in {
            "verified", "verified-alternate-embedded-outline",
            "verified-scoped-embedded-glyph",
        }
        if scoped_match is not None:
            scoped_mappings.append((codepoint, scoped_match.formula))
        evidence.append(FontGlyphEvidence(
            codepoint, font_name, match[0], match[1], glyph_id, local_gid,
            match[2], local_digest, match[3], local_metrics,
            bbox, origin, size, verified, reason,
        ))
    grouped: dict[int, list[FontGlyphEvidence]] = {}
    for entry in evidence:
        grouped.setdefault(entry.codepoint, []).append(entry)
    # Outline-verified HyhwpEQ mappings stay authorized when the same
    # codepoint is also tagged as 명조 in the clip (PDF font-name error).
    # HyhwpEQ outline mismatches still fail closed.
    mixed_ok = frozenset(
        codepoint for codepoint, entries in grouped.items()
        if entries
        and any(entry.verified for entry in entries)
        and all(entry.verified or entry.reason == "wrong-font" for entry in entries)
    )
    verified_occurrences = tuple(entry for entry in evidence if entry.verified)

    def _adjacent_to_verified(entry: FontGlyphEvidence) -> bool:
        return any(
            abs(
                (other.bbox[1] + other.bbox[3] - entry.bbox[1] - entry.bbox[3]) / 2
            ) <= 3
            and other.bbox[0] - 8 <= entry.bbox[2]
            and entry.bbox[0] <= other.bbox[2] + 8
            for other in verified_occurrences
        )

    sibling_verified = frozenset(
        entry.codepoint
        for entry in evidence
        if not entry.verified
        and entry.reason == "wrong-font"
        and (
            entry.codepoint in GLYPH_MAPPINGS
            or chr(entry.codepoint) in {FRACTION_BAR, RADICAL_SIGN, VECTOR_HEAD}
        )
        and _adjacent_to_verified(entry)
    )
    verified = mixed_ok | sibling_verified
    global_reasons = tuple(sorted({
        entry.reason for entry in evidence
        if entry.reason.startswith((
            "ambiguous-font-resource-xref:",
            "embedded-font-unreadable:",
            "font-occurrence-xref-",
        ))
    }))
    global_reason_set = frozenset(global_reasons)
    rejections = (
        *global_reasons,
        *(
            f"U+{entry.codepoint:04X}:{entry.reason}@{entry.bbox[0]:.2f},{entry.bbox[1]:.2f}"
            for entry in evidence
            if not entry.verified
            and entry.reason not in global_reason_set
            and entry.codepoint not in verified
        ),
    )
    return EquationFontContext(
        verified, tuple(evidence), rejections, tuple(sorted(set(scoped_mappings))),
    )
