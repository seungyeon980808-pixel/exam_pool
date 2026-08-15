"""Version-gated loaded-font identity tracing for PyMuPDF text spans."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Final, NotRequired, Protocol, TypedDict

import fitz


SUPPORTED_PYMUPDF_VERSIONS: Final = frozenset({"1.28.0"})
_REQUIRED_FITZ_SYMBOLS: Final = (
    "JM_BinFromBuffer",
    "JM_new_texttrace_device",
    "jm_lineart_fill_text",
    "jm_lineart_ignore_text",
    "jm_lineart_stroke_text",
)
_REQUIRED_MUPDF_SYMBOLS: Final = (
    "FzBuffer",
    "FzCookie",
    "FzMatrix",
    "FzTextSpan",
    "fz_bound_page",
    "fz_close_device",
    "fz_run_page",
    "ll_fz_keep_buffer",
)
_NATIVE_TRACE_EXCEPTIONS: Final = (
    AttributeError, IndexError, KeyError, OverflowError, RuntimeError,
    SystemError, TypeError, ValueError,
)


type _Coordinate = int | float
type _RawPoint = tuple[_Coordinate, ...]
type _RawGlyph = tuple[int, int, _RawPoint, _RawPoint]


class _TraceSpan(TypedDict):
    font: str
    size: _Coordinate
    seqno: int
    chars: tuple[_RawGlyph, ...]
    _loaded_font_sha256: NotRequired[str | None]


class _NativeColor(Protocol):
    def __getitem__(self, index: int, /) -> float: ...


class _TraceDevice(Protocol):
    ptm: fitz.mupdf.FzMatrix
    exact: bool


@dataclass(frozen=True, slots=True)
class _TraceShapeError(Exception):
    field: str

    def __str__(self) -> str:
        return f"invalid native text-trace field: {self.field}"


@dataclass(frozen=True, slots=True)
class TracedFontGlyph:
    codepoint: int
    glyph_id: int
    font_name: str
    font_sha256: str | None
    font_size: float
    origin: tuple[float, float]
    bbox: tuple[float, float, float, float]
    seqno: int


def _runtime_supported() -> bool:
    return (
        fitz.VersionBind in SUPPORTED_PYMUPDF_VERSIONS
        and all(hasattr(fitz, name) for name in _REQUIRED_FITZ_SYMBOLS)
        and all(hasattr(fitz.mupdf, name) for name in _REQUIRED_MUPDF_SYMBOLS)
    )


def _font_digest(
    span_pointer: fitz.mupdf.fz_text_span,
    cache: dict[int, str | None],
) -> str | None:
    span = fitz.mupdf.FzTextSpan(span_pointer)
    font = span.font()
    identity = int(font.m_internal_value())
    if identity not in cache:
        raw_buffer = font.m_internal.buffer
        if raw_buffer:
            kept = fitz.mupdf.ll_fz_keep_buffer(raw_buffer)
            buffer = fitz.mupdf.FzBuffer(kept)
            cache[identity] = sha256(bytes(fitz.JM_BinFromBuffer(buffer))).hexdigest()
        else:
            cache[identity] = None
    return cache[identity]


def _span_digests(
    text: fitz.mupdf.fz_text,
    cache: dict[int, str | None],
) -> list[str | None]:
    result: list[str | None] = []
    span = text.head
    while span:
        result.append(_font_digest(span, cache))
        span = span.next
    return result


def _annotate_new_spans(
    output: list[_TraceSpan],
    start: int,
    digests: list[str | None],
) -> bool:
    emitted = output[start:]
    if len(emitted) != len(digests):
        return False
    for entry, digest in zip(emitted, digests, strict=True):
        entry["_loaded_font_sha256"] = digest
    return True


def _new_device(output: list[_TraceSpan]) -> _TraceDevice:
    class LoadedFontTraceDevice(fitz.JM_new_texttrace_device):
        """Mutable accumulator required by the MuPDF device callback API."""

        def __init__(self, trace_output: list[_TraceSpan]) -> None:
            super().__init__(trace_output)
            self.trace_output = trace_output
            self.font_digests: dict[int, str | None] = {}
            self.exact = True

        def fill_text(
            self,
            ctx: fitz.mupdf.fz_context,
            text: fitz.mupdf.fz_text,
            ctm: fitz.mupdf.fz_matrix,
            colorspace: fitz.mupdf.fz_colorspace | None,
            color: _NativeColor,
            alpha: float,
            color_params: fitz.mupdf.fz_color_params,
        ) -> None:
            start = len(self.trace_output)
            digests = _span_digests(text, self.font_digests)
            fitz.jm_lineart_fill_text(
                self, ctx, text, ctm, colorspace, color, alpha, color_params,
            )
            self.exact &= _annotate_new_spans(self.trace_output, start, digests)

        def stroke_text(
            self,
            ctx: fitz.mupdf.fz_context,
            text: fitz.mupdf.fz_text,
            stroke: fitz.mupdf.fz_stroke_state,
            ctm: fitz.mupdf.fz_matrix,
            colorspace: fitz.mupdf.fz_colorspace | None,
            color: _NativeColor,
            alpha: float,
            color_params: fitz.mupdf.fz_color_params,
        ) -> None:
            start = len(self.trace_output)
            digests = _span_digests(text, self.font_digests)
            fitz.jm_lineart_stroke_text(
                self, ctx, text, stroke, ctm, colorspace, color, alpha, color_params,
            )
            self.exact &= _annotate_new_spans(self.trace_output, start, digests)

        def ignore_text(
            self,
            text: fitz.mupdf.fz_text,
            ctm: fitz.mupdf.fz_matrix,
        ) -> None:
            start = len(self.trace_output)
            digests = _span_digests(text, self.font_digests)
            fitz.jm_lineart_ignore_text(self, text, ctm)
            self.exact &= _annotate_new_spans(self.trace_output, start, digests)

    return LoadedFontTraceDevice(output)


def _pair(values: Sequence[_Coordinate]) -> tuple[float, float]:
    if len(values) != 2:
        raise _TraceShapeError(field="point")
    return float(values[0]), float(values[1])


def _quad(values: Sequence[_Coordinate]) -> tuple[float, float, float, float]:
    if len(values) != 4:
        raise _TraceShapeError(field="rectangle")
    return (
        float(values[0]), float(values[1]),
        float(values[2]), float(values[3]),
    )


def _parse(output: list[_TraceSpan]) -> tuple[TracedFontGlyph, ...]:
    result: list[TracedFontGlyph] = []
    for span in output:
        font_name = span.get("font")
        digest = span.get("_loaded_font_sha256")
        chars = span.get("chars")
        if (
            not isinstance(font_name, str)
            or digest is not None and not isinstance(digest, str)
            or not isinstance(chars, tuple)
        ):
            raise _TraceShapeError(field="span")
        for char in chars:
            if not isinstance(char, tuple) or len(char) != 4:
                raise _TraceShapeError(field="glyph")
            result.append(TracedFontGlyph(
                int(char[0]), int(char[1]), font_name, digest,
                float(span["size"]), _pair(char[2]), _quad(char[3]),
                int(span["seqno"]),
            ))
    return tuple(result)


def trace_font_glyphs(page: fitz.Page) -> tuple[TracedFontGlyph, ...] | None:
    """Return exact loaded-font digests, or ``None`` on any unsupported state."""
    if not _runtime_supported():
        return None
    old_rotation = page.rotation
    result: tuple[TracedFontGlyph, ...] | None = None
    output: list[_TraceSpan] = []
    try:
        if old_rotation:
            page.set_rotation(0)
        device = _new_device(output)
        page_pointer = page.this
        bounds = fitz.mupdf.fz_bound_page(page_pointer)
        device.ptm = fitz.mupdf.FzMatrix(1, 0, 0, -1, 0, bounds.y1)
        try:
            fitz.mupdf.fz_run_page(
                page_pointer, device, fitz.mupdf.FzMatrix(), fitz.mupdf.FzCookie(),
            )
        finally:
            fitz.mupdf.fz_close_device(device)
        if device.exact:
            result = _parse(output)
    except (*_NATIVE_TRACE_EXCEPTIONS, _TraceShapeError):
        # This is the native-extension boundary: uncertainty must not authorize text.
        result = None
    finally:
        if old_rotation:
            try:
                page.set_rotation(old_rotation)
            except (RuntimeError, ValueError):
                result = None
    return result
