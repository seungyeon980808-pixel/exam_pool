"""Turn a recovered source prompt into HwpPalette question fields."""
from __future__ import annotations

import re
from typing import Final, TypedDict

from .pdf_hwp_pipeline_models import FigureLayout


_BOGI_BODY = re.compile(
    r"^\s*<보\s*기>\s*"
    r"ㄱ\.\s*(?P<first>.*?)\s+"
    r"ㄴ\.\s*(?P<second>.*?)\s+"
    r"ㄷ\.\s*(?P<third>.*?)\s*$",
    re.DOTALL,
)

DIRECT_TEMPLATE_BY_LAYOUT: Final = {
    FigureLayout.ONE_LARGE: "수능정답1대사진5선지",
    FigureLayout.ONE_SMALL: "수능정답1소사진5선지",
    FigureLayout.TWO_SMALL: "수능정답2소사진무캡션5선지",
    FigureLayout.TWO_LARGE: "수능정답2대사진5선지",
    FigureLayout.TWO_VERTICAL: "수능정답상하사진5선지",
    FigureLayout.THREE_SMALL: "수능정답3소사진무캡션5선지",
}
HAPDAP_TEMPLATE_BY_LAYOUT: Final = {
    FigureLayout.ONE_LARGE: "수능합답1대사진5선지",
    FigureLayout.ONE_SMALL: "수능합답1소사진5선지",
    FigureLayout.TWO_SMALL: "수능합답2소사진무캡션5선지",
    FigureLayout.TWO_LARGE: "수능합답2대사진5선지",
    FigureLayout.TWO_VERTICAL: "수능합답상하사진5선지",
    FigureLayout.THREE_SMALL: "수능합답3소사진5선지",
}
GRAPHICAL_CHOICE_TEMPLATE: Final = "수능정답1대사진그림5선지"
_LEGACY_HAPDAP_THREE_SMALL_TEMPLATE: Final = "수능합답3소사진무캡션5선지"
_THREE_PANEL_CAPTIONS: Final = ("(가)", "(나)", "(다)")


class BogiItem(TypedDict):
    label: str
    text: str


class PaletteQuestion(TypedDict, total=False):
    qtype: str
    passage: str
    ask: str
    material: str
    default_points: int
    is_negative: bool
    bogi_items: list[BogiItem]
    style_meta: dict[str, str]


def palette_question(
    passage: str,
    ask_block: str,
    material: str,
    figure_layout: FigureLayout | None,
) -> PaletteQuestion:
    """Separate a KICE ask and its optional three-claim ``<보기>`` block."""
    question = PaletteQuestion(
        qtype="정답형",
        passage=passage,
        ask=ask_block.strip(),
        material=material,
        default_points=3,
        is_negative=False,
    )
    head, marker, tail = ask_block.partition("?")
    bogi_marker = re.search(r"<보\s*기>", tail) if marker else None
    bogi_body = tail[bogi_marker.start():] if bogi_marker is not None else ""
    ask_suffix = tail[:bogi_marker.start()].strip() if bogi_marker is not None else ""
    match = _BOGI_BODY.fullmatch(bogi_body) if bogi_marker is not None else None
    if match is None:
        if figure_layout is not None:
            question["style_meta"] = {"palette_template": DIRECT_TEMPLATE_BY_LAYOUT[figure_layout]}
        return question

    question.update(
        qtype="합답형",
        ask=" ".join(part for part in (f"{head.strip()}?", ask_suffix) if part),
        bogi_items=[
            {"label": "ㄱ", "text": match.group("first").strip()},
            {"label": "ㄴ", "text": match.group("second").strip()},
            {"label": "ㄷ", "text": match.group("third").strip()},
        ],
    )
    if figure_layout is not None:
        question["style_meta"] = {"palette_template": HAPDAP_TEMPLATE_BY_LAYOUT[figure_layout]}
    return question


def reconcile_final_template(
    markdown: str,
    layout: FigureLayout,
    *,
    figure_tokens: tuple[str, ...] = (),
    captions: tuple[str, ...] = (),
) -> str | None:
    """Replace a geometry-compatible stale label from the same answer family."""
    lines = markdown.splitlines()
    first_index = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_index is None:
        return None
    token = lines[first_index].strip()
    if not token.startswith("\\") or not token.endswith("\\"):
        return None
    label = token[1:-1]
    if label == GRAPHICAL_CHOICE_TEMPLATE:
        return markdown if layout is FigureLayout.ONE_LARGE else None
    direct_layout = next((key for key, value in DIRECT_TEMPLATE_BY_LAYOUT.items() if value == label), None)
    hapdap_layout = next((key for key, value in HAPDAP_TEMPLATE_BY_LAYOUT.items() if value == label), None)
    if label == _LEGACY_HAPDAP_THREE_SMALL_TEMPLATE:
        hapdap_layout = FigureLayout.THREE_SMALL
    current_layout = direct_layout or hapdap_layout
    proven_composite_split = (
        current_layout is FigureLayout.ONE_LARGE
        and layout is FigureLayout.THREE_SMALL
        and hapdap_layout is not None
    )
    if current_layout is None or (
        _asset_count(current_layout) != _asset_count(layout)
        and not proven_composite_split
    ):
        return None
    expected = (
        DIRECT_TEMPLATE_BY_LAYOUT[layout]
        if direct_layout is not None else HAPDAP_TEMPLATE_BY_LAYOUT[layout]
    )
    if layout is FigureLayout.THREE_SMALL and hapdap_layout is not None:
        rebuilt = _rebuild_hapdap_three_panel_values(
            lines[first_index + 1:], current_layout, figure_tokens, captions,
        )
        if rebuilt is None:
            return None
        lines[first_index + 1:] = rebuilt
    lines[first_index] = f"\\{expected}\\"
    return "\n".join(lines)


def _rebuild_hapdap_three_panel_values(
    values: list[str],
    current_layout: FigureLayout,
    figure_tokens: tuple[str, ...],
    captions: tuple[str, ...],
) -> list[str] | None:
    if len(figure_tokens) != 3 or captions != _THREE_PANEL_CAPTIONS:
        return None
    material_values = [
        value
        for pair in zip(figure_tokens, captions, strict=True)
        for value in pair
    ]
    match current_layout:
        case FigureLayout.ONE_LARGE:
            if len(values) != 12:
                return None
            return [*values[:2], *material_values, *values[3:]]
        case FigureLayout.THREE_SMALL:
            if len(values) != 17:
                return None
            return [*values[:2], *material_values, *values[8:]]
        case FigureLayout.ONE_SMALL | FigureLayout.TWO_SMALL | FigureLayout.TWO_LARGE | FigureLayout.TWO_VERTICAL:
            return None
        case unreachable:
            assert_never(unreachable)


def _asset_count(layout: FigureLayout) -> int:
    if layout in {FigureLayout.ONE_SMALL, FigureLayout.ONE_LARGE}:
        return 1
    if layout in {FigureLayout.TWO_SMALL, FigureLayout.TWO_LARGE, FigureLayout.TWO_VERTICAL}:
        return 2
    return 3
