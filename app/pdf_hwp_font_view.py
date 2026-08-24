"""Parsed-font views with a bounded, ownership-aware LRU cache."""
from __future__ import annotations

from collections import OrderedDict
from hashlib import sha256
from io import BytesIO
from typing import Final

from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont


_FONT_CACHE_LIMIT: Final = 64


class FontView:
    def __init__(self, data: bytes) -> None:
        self.sha256 = sha256(data).hexdigest()
        self.font = TTFont(BytesIO(data), lazy=False)
        self.order = self.font.getGlyphOrder()
        self.glyphs = self.font.getGlyphSet()
        self.metrics = self.font["hmtx"].metrics
        self.cmap = self.font.getBestCmap() or {} if "cmap" in self.font else {}
        self._digests: dict[int, str] = {}

    def gid_for_codepoint(self, codepoint: int) -> int | None:
        name = self.cmap.get(codepoint)
        return self.order.index(name) if name in self.order else None

    def digest(self, glyph_id: int) -> str | None:
        if not 0 <= glyph_id < len(self.order):
            return None
        if glyph_id not in self._digests:
            pen = RecordingPen()
            self.glyphs[self.order[glyph_id]].draw(pen)
            self._digests[glyph_id] = sha256(repr(pen.value).encode()).hexdigest()
        return self._digests[glyph_id]

    def metric(self, glyph_id: int) -> tuple[int, int] | None:
        if not 0 <= glyph_id < len(self.order):
            return None
        return self.metrics.get(self.order[glyph_id])

    def close(self) -> None:
        self.font.close()


_FONT_CACHE: OrderedDict[str, FontView] = OrderedDict()


def font_view(data: bytes) -> FontView:
    digest = sha256(data).hexdigest()
    cached = _FONT_CACHE.get(digest)
    if cached is not None:
        _FONT_CACHE.move_to_end(digest)
        return cached
    result = FontView(data)
    _FONT_CACHE[digest] = result
    while len(_FONT_CACHE) > _FONT_CACHE_LIMIT:
        _, evicted = _FONT_CACHE.popitem(last=False)
        evicted.close()
    return result
