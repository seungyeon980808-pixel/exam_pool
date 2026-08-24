from __future__ import annotations

import re
from typing import Final, TypedDict, assert_never

from .pdf_hwp_pipeline_models import FigureLayout
from .pdf_hwp_experiment import is_experiment_text


_BOGI_MARKER = r"<`?보\s*기`?>"
_BOGI_BODY = re.compile(
    rf"^\s*{_BOGI_MARKER}\s*"
    r"ㄱ\.\s*(?P<first>.*?)\s+"
    r"ㄴ\.\s*(?P<second>.*?)\s+"
    r"ㄷ\.\s*(?P<third>.*?)\s*$",
    re.DOTALL,
)
_SCORE = re.compile(r"\s*\[(?P<points>\d+)점\]\s*")
_EXPERIMENT_ASK_TAIL = re.compile(r"^(?:이에|위\s+.+에)\s+대한\s+.+(?:고른|옳은|알맞은)\s*$")
_EMPTY_RESULT_ROW = re.compile(r"(?m)^\s*-(?:\s*&\s*-)+\s*$")
_ATOMIC_BOX = re.compile(r"(?m)^\\표1\*1\\\s*$")
_ATOMIC_BOX_MIN_CHARS: Final = 380

DIRECT_TEMPLATE_BY_LAYOUT: Final = {
    FigureLayout.ONE_LARGE: "수능정답1대사진5선지",
    FigureLayout.ONE_SMALL: "수능정답1소사진5선지",
    FigureLayout.TWO_SMALL: "수능정답2소사진무캡션5선지",
    FigureLayout.TWO_LARGE: "수능정답2대사진5선지",
    FigureLayout.TWO_VERTICAL: "수능정답상하사진5선지",
    FigureLayout.THREE_SMALL: "수능합답3소사진5선지",
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
EXPERIMENT_TEMPLATE: Final = "수능AI실제실험형"
EXPERIMENT_REFLOW_TEMPLATE: Final = "수능합답실험1대사진5선지"
DIRECT_TEXT_TEMPLATE: Final = "수능AI실제직접형"
DIRECT_ATOMIC_TEMPLATE: Final = "수능AI실제직접형새쪽"
COMPARISON_TEMPLATE: Final = "수능AI실제비교선지형"
_LEGACY_HAPDAP_THREE_SMALL_TEMPLATE: Final = "수능합답3소사진무캡션5선지"
_THREE_PANEL_CAPTION_SETS: Final = frozenset({("(가)", "(나)", "(다)"), ("A", "B", "C")})


class BogiItem(TypedDict):
    label: str
    text: str


class PaletteQuestion(TypedDict, total=False):
    qtype: str
    passage: str
    ask: str
    material: str
    choice_header: str
    default_points: int
    is_negative: bool
    bogi_items: list[BogiItem]
    style_meta: dict[str, str]


def palette_question(
    passage: str, ask_block: str, material: str, figure_layout: FigureLayout | None,
) -> PaletteQuestion:
    if is_experiment_text(passage):
        passage, ask_block = _recover_experiment_ask(passage, ask_block)
    score = _SCORE.search(ask_block)
    ask_block = _SCORE.sub(" ", ask_block).strip()
    question = PaletteQuestion(
        qtype="정답형",
        passage=passage,
        ask=ask_block.strip(),
        material=material,
        default_points=int(score.group("points")) if score is not None else 3,
        is_negative=False,
    )
    head, marker, tail = ask_block.partition("?")
    bogi_marker = re.search(_BOGI_MARKER, tail) if marker else None
    embedded_bogi_marker = re.search(_BOGI_MARKER, head) if marker else None
    bogi_body = (
        tail[bogi_marker.start():]
        if bogi_marker is not None
        else f"<보기>{tail}"
        if embedded_bogi_marker is not None
        else ""
    )
    ask_suffix = tail[:bogi_marker.start()].strip() if bogi_marker is not None else ""
    match = _BOGI_BODY.fullmatch(bogi_body) if bogi_body else None
    experiment_reflow = is_experiment_text(passage) and _EMPTY_RESULT_ROW.search(passage) is not None
    if match is None:
        if figure_layout is not None and (not is_experiment_text(passage) or experiment_reflow):
            question["style_meta"] = {"palette_template": DIRECT_TEMPLATE_BY_LAYOUT[figure_layout]}
        elif is_experiment_text(passage):
            question["style_meta"] = {"palette_template": EXPERIMENT_TEMPLATE}
        elif _ATOMIC_BOX.search(passage) is not None and len(passage) >= _ATOMIC_BOX_MIN_CHARS:
            question["style_meta"] = {"palette_template": DIRECT_ATOMIC_TEMPLATE}
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
    if experiment_reflow and figure_layout is FigureLayout.ONE_LARGE:
        question["style_meta"] = {"palette_template": EXPERIMENT_REFLOW_TEMPLATE}
    elif figure_layout is not None and not is_experiment_text(passage):
        question["style_meta"] = {"palette_template": HAPDAP_TEMPLATE_BY_LAYOUT[figure_layout]}
    elif is_experiment_text(passage):
        question["style_meta"] = {"palette_template": EXPERIMENT_TEMPLATE}
    return question


def _recover_experiment_ask(passage: str, ask_block: str) -> tuple[str, str]:
    """Join an ask sentence split after an experiment result table.

    PDF text order can leave the first half of a question as the last material
    line and put only ``것은?`` in the ask block.  Restrict the repair to an
    experiment passage, a known question continuation, and one trailing line;
    ordinary result prose therefore remains untouched.
    """
    lines = passage.rstrip().splitlines()
    if not lines or not re.match(r"^것은\?", ask_block.strip()):
        return passage, ask_block
    prefix = lines[-1].strip()
    if not _EXPERIMENT_ASK_TAIL.fullmatch(prefix):
        return passage, ask_block
    repaired_passage = "\n".join(lines[:-1]).rstrip()
    repaired_ask = f"{prefix} {ask_block.strip()}"
    return repaired_passage, repaired_ask


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
    if label == EXPERIMENT_REFLOW_TEMPLATE:
        return markdown if layout is FigureLayout.ONE_LARGE else None
    if label == EXPERIMENT_TEMPLATE:
        return markdown
    if label == COMPARISON_TEMPLATE:
        values = lines[first_index + 1:]
        prompt_token = figure_tokens[0] if len(figure_tokens) == 1 else ""
        return (
            markdown
            if layout in {FigureLayout.ONE_SMALL, FigureLayout.ONE_LARGE}
            and len(values) >= 9
            and bool(prompt_token)
            and sum(value.count(prompt_token) for value in values) == 1
            and all(prompt_token not in value for value in values[-7:])
            else None
        )
    if label == DIRECT_TEXT_TEMPLATE and layout is FigureLayout.THREE_SMALL:
        rebuilt = _rebuild_text_only_to_hapdap_three(
            lines[first_index + 1:], figure_tokens, captions,
        )
        if rebuilt is None:
            return None
        lines[first_index] = f"\\{HAPDAP_TEMPLATE_BY_LAYOUT[layout]}\\"
        lines[first_index + 1:] = rebuilt
        return "\n".join(lines)
    if label == GRAPHICAL_CHOICE_TEMPLATE:
        # There is one registered graphical-choice template.  Its prompt slot is
        # intentionally large enough for both measured small and large prompts.
        return markdown if layout in {FigureLayout.ONE_SMALL, FigureLayout.ONE_LARGE} else None
    if label == "수능정답0사진그림5선지":
        return markdown
    direct_layout = next((key for key, value in DIRECT_TEMPLATE_BY_LAYOUT.items() if value == label), None)
    hapdap_layout = next((key for key, value in HAPDAP_TEMPLATE_BY_LAYOUT.items() if value == label), None)
    if label == _LEGACY_HAPDAP_THREE_SMALL_TEMPLATE:
        hapdap_layout = FigureLayout.THREE_SMALL
    current_layout = direct_layout or hapdap_layout
    proven_composite_split = (
        current_layout in {FigureLayout.ONE_LARGE, FigureLayout.ONE_SMALL}
        and layout is FigureLayout.THREE_SMALL
        and (hapdap_layout is not None or direct_layout is not None)
    )
    if current_layout is None or (
        _asset_count(current_layout) != _asset_count(layout)
        and not proven_composite_split
    ):
        return None
    expected = (
        HAPDAP_TEMPLATE_BY_LAYOUT[layout]
        if layout is FigureLayout.THREE_SMALL
        else DIRECT_TEMPLATE_BY_LAYOUT[layout]
        if direct_layout is not None else HAPDAP_TEMPLATE_BY_LAYOUT[layout]
    )
    if layout is FigureLayout.THREE_SMALL:
        rebuilt = _rebuild_hapdap_three_panel_values(
            lines[first_index + 1:], current_layout, figure_tokens, captions,
        )
        if rebuilt is None:
            return None
        lines[first_index + 1:] = rebuilt
    lines[first_index] = f"\\{expected}\\"
    return "\n".join(lines)


def _rebuild_text_only_to_hapdap_three(
    values: list[str],
    figure_tokens: tuple[str, ...],
    captions: tuple[str, ...],
) -> list[str] | None:
    """Map the 9-slot text-only direct template onto the registered three-panel hapdap."""
    if len(values) != 9 or len(figure_tokens) != 3 or captions not in _THREE_PANEL_CAPTION_SETS:
        return None
    material_values = [
        value
        for pair in zip(figure_tokens, captions, strict=True)
        for value in pair
    ]
    ask = values[2]
    points = values[3].strip()
    if points and points != "-" and f"[{points}점]" not in ask:
        ask = f"{ask} [{points}점]"
    return [*values[:2], *material_values, ask, "-", "-", "-", *values[4:]]


def _rebuild_hapdap_three_panel_values(
    values: list[str],
    current_layout: FigureLayout,
    figure_tokens: tuple[str, ...],
    captions: tuple[str, ...],
) -> list[str] | None:
    if len(figure_tokens) != 3 or captions not in _THREE_PANEL_CAPTION_SETS:
        return None
    material_values = [
        value
        for pair in zip(figure_tokens, captions, strict=True)
        for value in pair
    ]
    match current_layout:
        case FigureLayout.ONE_LARGE | FigureLayout.ONE_SMALL:
            if len(values) == 12:
                return [*values[:2], *material_values, *values[3:]]
            if len(values) == 9:
                return [*values[:2], *material_values, values[3], "-", "-", "-", *values[4:]]
            return None
        case FigureLayout.THREE_SMALL:
            if len(values) != 17:
                return None
            return [*values[:2], *material_values, *values[8:]]
        case FigureLayout.TWO_SMALL | FigureLayout.TWO_LARGE | FigureLayout.TWO_VERTICAL:
            return None
        case unreachable:
            assert_never(unreachable)


def _asset_count(layout: FigureLayout) -> int:
    if layout in {FigureLayout.ONE_SMALL, FigureLayout.ONE_LARGE}:
        return 1
    if layout in {FigureLayout.TWO_SMALL, FigureLayout.TWO_LARGE, FigureLayout.TWO_VERTICAL}:
        return 2
    return 3
