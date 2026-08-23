"""Recover EBS question prose from its reliable text-layer reading order."""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


_CHOICE_RE = re.compile(r"^[①②③④⑤]\s*(.*)$")
_LOCAL_NUMBER_RE = re.compile(r"^\d{2}\s+")
_SOURCE_ID_RE = re.compile(r"^\[26023-\d{4}\]$")
_COMPACT_FRACTION_RE = re.compile(r"(?P<edge>[;:])(?P<body>[^;:\s]+)(?P=edge)")
_NUMERATOR_DIGIT = {
    **dict(zip("!@#$%^&*()", "1234567890", strict=True)),
    "Á": "1", "ª": "2", "£": "3", "¢": "4", "°": "5",
    "»": "9", "¼": "0",
}
_MATH_RUN_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<math>(?=(?:[A-Za-z0-9_{}^+\-=/.*<>()]|\\[A-Za-z]+)*"
    r"(?:[A-Za-z0-9]|\\[A-Za-z]+))"
    r"(?:(?:\\[A-Za-z]+)|[A-Za-z0-9_{}^+\-=/.*<>()])+?)"
    r"(?=[^A-Za-z0-9_{}^+\-=/.*<>()]|$)"
)


@dataclass(frozen=True, slots=True)
class EbsTextSlots:
    passage: str
    ask: str
    choices: tuple[str, ...]


def rejoin_ebs_soft_wraps(text: str, source_text: str) -> str:
    """Undo syllable-level wraps while preserving source word-boundary wraps."""
    value = text
    raw_lines = source_text.splitlines()
    for left_line, right_line in zip(raw_lines, raw_lines[1:]):
        if not left_line or left_line[-1].isspace():
            continue
        left_match = re.search(r"([가-힣]+)$", _clean_line(left_line))
        right_match = re.match(r"\s*([가-힣]+)", _clean_line(right_line))
        if left_match is None or right_match is None:
            continue
        left, right = left_match.group(1), right_match.group(1)
        value = value.replace(f"{left} {right}", f"{left}{right}", 1)
    return value


def normalize_ebs_legacy_equations(text: str) -> str:
    """Convert EBS equation-font text codes into semantic editable formula runs."""
    value = text
    def compact_fraction(match: re.Match[str]) -> str:
        body = match.group("body")
        numerator = "".join(_NUMERATOR_DIGIT.get(char, "") for char in body)
        denominator = "".join(char for char in body if char.isascii() and char.isdigit())
        return rf"\frac{{{numerator}}}{{{denominator}}}" if numerator and denominator else match.group()

    value = _COMPACT_FRACTION_RE.sub(compact_fraction, value)
    # In this EBS edition a grave accent is also used as a thin unit spacer.
    # The sequence ``38.0`¾`` is the equation-font spelling of 38.0 °C, not a
    # square root.
    value = re.sub(r"(?P<number>\d+(?:\.\d+)?)`¾", r"\g<number> °C", value)
    value = value.replace("1Ú", r"\rightarrow")
    value = value.replace("A'B'Ó", r"\bar{A′B′}")
    value = re.sub(r"([A-Za-z](?:'?[A-Za-z]){0,2}'?)Ó", lambda match: rf"\bar{{{match.group(1)}}}", value)
    value = value.replace("´", "·").replace("ù", "°").replace("Ñ", "-")
    value = re.sub(r"(?<=[A-Za-z])¼", "_0", value)
    value = re.sub(r"(?<=[A-Za-z])Á", "_1", value)
    value = re.sub(r"(?<=[A-Za-z])ª", "_2", value)
    value = re.sub(r"(?<=[A-Za-z])£", "_3", value)
    for encoded, subscript in {
        "¹": "p", "Ï": "q", "û": "k", "¸": "P", "º": "b",
        "Î": "Q", "ä": "R",
    }.items():
        value = re.sub(rf"(?<=[A-Za-z]){encoded}", f"_{subscript}", value)
    value = re.sub(r"(?<=[A-Za-z])¢", "_4", value)
    value = re.sub(r"(?<=[A-Za-z])°", "_5", value)
    # A lone grave after the small set of indexed physics symbols denotes 1.
    # Colons and SI units use it only as equation-font spacing.
    value = re.sub(r"(?P<base>[vdaI])`(?!(?:`|:|[A-Za-z]))", r"\g<base>_1", value)
    value = re.sub(r"(?<=[A-Za-z])õ", "_2", value)
    for encoded, digit in {
        "Ú": "1", "Û": "2", "Ü": "3", "Ý": "4", "Þ": "5",
        "ß": "6", "à": "7", "á": "8", "â": "0",
    }.items():
        value = re.sub(rf"(?<=[A-Za-z0-9)]){encoded}", f"^{digit}", value)
    super_digits = {"Ú": "1", "Û": "2", "Ü": "3", "Ý": "4", "Þ": "5", "ß": "6", "à": "7", "á": "8", "â": "0"}
    sub_digits = {"¼": "0", "Á": "1", "ª": "2", "£": "3", "¢": "4", "°": "5"}
    value = re.sub(
        r"(?P<super>[ÚÛÜÝÞßàáâ`]+)(?P<sub>[¼Áª£¢°]+)(?P<base>[A-Za-z]+)",
        lambda match: (
            "{}^{" + "".join(super_digits.get(char, "") for char in match.group("super"))
            + "}_{" + "".join(sub_digits[char] for char in match.group("sub"))
            + "}" + match.group("base")
        ),
        value,
    )
    value = re.sub(
        r"1\s*\n\s*(?P<a>k_\d)\s*=\s*1\s*\n\s*(?P<b>k_\d)\s*\+\s*1\s*\n\s*(?P<c>k_\d)",
        lambda match: (
            "[[formula:" + r"\frac{1}{" + match.group("a") + "}="
            + r"\frac{1}{" + match.group("b") + "}+"
            + r"\frac{1}{" + match.group("c") + "}]]"
        ),
        value,
    )
    value = re.sub(
        r"(?P<num>E_0)\s*\n\s*(?P<den>h)\s*=\s*\{?\s*1\s*\n\s*(?P<a>k_\d)"
        r"\s*-\s*1\s*\n\s*(?P<b>k_\d)\}?c",
        lambda match: (
            "[[formula:" + r"\frac{E_0}{h}=(\frac{1}{" + match.group("a")
            + "}-" + r"\frac{1}{" + match.group("b") + "})c]]"
        ),
        value,
    )
    value = re.sub(
        r"\{?\s*1\s*\n\s*(?P<a>k_\d)\s*-\s*1\s*\n\s*(?P<b>k_\d)\}?c",
        lambda match: (
            "[[formula:(" + r"\frac{1}{" + match.group("a") + "}-"
            + r"\frac{1}{" + match.group("b") + "})c]]"
        ),
        value,
    )
    value = re.sub(
        r"sinh_1\s*\n\s*sinh_2\s*=\s*(?P<a>\\bar\{AB\})\s*\n\s*"
        r"(?P<b>\\bar\{A′B′\})",
        lambda match: (
            "[[formula:" + r"\frac{\sin h_1}{\sin h_2}="
            + r"\frac{" + match.group("a") + "}{" + match.group("b") + "}]]"
        ),
        value,
    )
    # Reconstruct radicals before deleting equation-font spacing markers.  A
    # following source line is the vertically stacked denominator.  The two
    # encodings differ semantically: ¾Ð puts the fraction inside the radical,
    # while apostrophe/¶ usually puts a radical in the numerator of an outer
    # fraction.
    value = re.sub(
        r"¾Ð\s*(?P<num>[A-Za-z0-9_{}^]+)\s*\n\s*(?P<den>[A-Za-z0-9_{}^]+)",
        lambda match: rf"[[formula:\sqrt{{\frac{{{match.group('num')}}}{{{match.group('den')}}}}}]]",
        value,
    )
    value = re.sub(
        r"(?P<coef>[A-Za-z0-9_{}^]+)¾\s*(?P<num>[A-Za-z0-9_{}^]+)\s*\n\s*(?P<den>[A-Za-z0-9_{}^]+)",
        lambda match: (
            "[[formula:" + match.group("coef") + r"\sqrt{\frac{"
            + match.group("num") + "}{" + match.group("den") + "}}]]"
        ),
        value,
    )
    value = re.sub(
        r"(?P<ncoef>[0-9]*)'(?P<nrad>[0-9]+)\\?(?P<ntail>[A-Za-z]+)\s*\n\s*"
        r"(?P<dcoef>[0-9]+)'(?P<drad>[A-Za-z]+)¶(?P<dtail>[A-Za-z]+)",
        lambda match: (
            "[[formula:" + r"\frac{"
            + match.group("ncoef") + r"\sqrt{" + match.group("nrad") + "}"
            + match.group("ntail") + "}{" + match.group("dcoef")
            + r"\sqrt{" + match.group("drad") + match.group("dtail") + "}}]]"
        ),
        value,
    )
    value = re.sub(
        r"¾\s*(?P<fraction>\\frac\{[^{}]+\}\{[^{}]+\})",
        lambda match: rf"[[formula:\sqrt{{{match.group('fraction')}}}]]",
        value,
    )
    # No coefficient before the radical after '=' means the stacked fraction
    # belongs inside the root (v1 = sqrt(3/2) v2).
    value = re.sub(
        r"=\s*'(?P<num>[A-Za-z0-9_{}^]+)\s*\n\s*(?P<den>[A-Za-z0-9_{}^]+)",
        lambda match: (
            "=[[formula:" + r"\sqrt{\frac{" + match.group("num")
            + "}{" + match.group("den") + "}}]]"
        ),
        value,
    )
    value = re.sub(
        r"(?P<prefix>[A-Za-z0-9_{}^]*)'(?P<rad>[A-Za-z0-9_{}^]*)¶(?P<tail>[A-Za-z0-9_{}^]+)"
        r"(?:\s*\n\s*(?P<den>[A-Za-z0-9_{}^]+))?",
        lambda match: (
            "[[formula:"
            + (
                r"\frac{" + match.group("prefix") + r"\sqrt{"
                + match.group("rad") + match.group("tail") + "}}{"
                + match.group("den") + "}"
                if match.group("den")
                else match.group("prefix") + r"\sqrt{"
                + match.group("rad") + match.group("tail") + "}"
            )
            + "]]"
        ),
        value,
    )
    value = re.sub(
        r"(?P<coef>[A-Za-z0-9_{}^]+)'(?P<rad>[A-Za-z0-9_{}^`]+)\s*\n\s*(?P<den>[A-Za-z0-9_{}^]+)",
        lambda match: (
            "[[formula:" + r"\frac{" + match.group("coef") + r"\sqrt{"
            + match.group("rad").replace("`", "") + "}}{" + match.group("den") + "}]]"
        ),
        value,
    )
    value = re.sub(
        r"'(?P<rad>[A-Za-z0-9_{}^`]+)",
        lambda match: rf"[[formula:\sqrt{{{match.group('rad').replace('`', '')}}}]]",
        value,
    )
    value = value.replace("É", "<=")
    value = re.sub(r"(?<=파장은)\s*k(?=(?:이다|이고|이며|[,.]))", r" \\lambda", value)
    value = re.sub(r"(?<=ㄱ\.\s)k(?==)", r"\\lambda", value)
    value = value.replace("`", "")

    formulas: list[str] = []

    def hold_existing(match: re.Match[str]) -> str:
        formulas.append(match.group(1))
        return chr(0xE300 + len(formulas) - 1)

    protected = re.sub(r"\[\[formula:(.+?)\]\]", hold_existing, value)
    protected = _MATH_RUN_RE.sub(
        lambda match: f"[[formula:{match.group('math')}]]",
        protected,
    )
    for index, source in enumerate(formulas):
        protected = protected.replace(chr(0xE300 + index), f"[[formula:{source}]]")
    while "]]" + "[[formula:" in protected:
        protected = protected.replace("]]" + "[[formula:", "")
    protected = re.sub(
        r"\[\[formula:(?P<left>[^\[\]]+)\]\][ \t]+\[\[formula:(?P<right>[^\[\]]+)\]\]",
        lambda match: f"[[formula:{match.group('left')}{match.group('right')}]]",
        protected,
    )
    protected = re.sub(
        r"\[\[formula:(?P<left>[^\[\]]+)\]\]\s+"
        r"\[\[formula:(?P<right>[<>]=?[^\[\]]+)\]\]",
        lambda match: f"[[formula:{match.group('left')}{match.group('right')}]]",
        protected,
    )
    protected = re.sub(
        r"(\[\[formula:[^\[\]]+\]\])\s+(?=(?:은|는|이|가|을|를|에|에서|와|과|로)\b)",
        r"\1",
        protected,
    )
    return protected


def _normalize_block(lines: list[str]) -> str:
    """Normalize a visual multi-line block without losing stacked math."""
    return re.sub(r"\s+", " ", normalize_ebs_legacy_equations("\n".join(lines))).strip()


def _formula_only(line: str) -> bool:
    normalized = normalize_ebs_legacy_equations(line)
    return re.fullmatch(r"\s*\[\[formula:.+?\]\]\s*", normalized) is not None


def _stacked_question_fraction(ask: str) -> str:
    pattern = re.compile(
        r"(?P<num>\[\[formula:(?P<num_src>[^\[\]]+?)\]\])\s+"
        r"(?P<den>\[\[formula:(?P<den_src>[^\[\]]+?)\]\])\s*(?P<tail>[은는]\?)"
    )
    return pattern.sub(
        lambda match: (
            f"[[formula:\\frac{{{match.group('num_src')}}}"
            f"{{{match.group('den_src')}}}]]{match.group('tail')}"
        ),
        ask,
        count=1,
    )


def _clean_line(line: str) -> str:
    value = line.replace("\ue287", "[[formula:○]]")
    return "".join(
        char for char in value
        if unicodedata.category(char) != "Cc" and char not in {"\u200c", "\ufeff"}
    ).strip()


def parse_ebs_text_slots(source_text: str) -> EbsTextSlots:
    """Extract the final question frame and five choices without geometry reordering."""
    lines: list[str] = []
    trailing_space: list[bool] = []
    for raw_line in source_text.splitlines():
        line = _clean_line(raw_line)
        if _SOURCE_ID_RE.fullmatch(line):
            break
        if line:
            lines.append(line)
            trailing_space.append(bool(raw_line) and raw_line[-1].isspace())
    choice_indices = [index for index, line in enumerate(lines) if _CHOICE_RE.match(line)]
    if not choice_indices:
        return EbsTextSlots("", "", ())
    choice_start = choice_indices[0]
    question_indices = [
        index for index, line in enumerate(lines[:choice_start]) if "?" in line
    ]
    if not question_indices:
        return EbsTextSlots("", "", ())
    ask_start = question_indices[-1]
    standard_starts = [
        index for index, line in enumerate(lines[: ask_start + 1])
        if re.search(r"(?:이에|위\s+.+에)\s+대한", line)
    ]
    if standard_starts:
        ask_start = standard_starts[-1]
    else:
        # Only a short ratio prompt (a numerator/denominator followed by 은/는?)
        # needs formula-line backtracking.  Long direct questions may have figure
        # labels immediately above them; pulling those labels into the prompt was
        # the cause of the former q275 corruption.
        if len(lines[ask_start].split("?", 1)[0]) < 16:
            backed_up = 0
            while ask_start > 0 and backed_up < 2 and _formula_only(lines[ask_start - 1]):
                ask_start -= 1
                backed_up += 1
        prose_start = ask_start - 1
        if prose_start >= 0 and len(lines[prose_start]) >= 18 and re.search(r"[가-힣]", lines[prose_start]):
            ask_start = prose_start
            while (
                ask_start > 0
                and len(lines[ask_start - 1]) >= 18
                and re.search(r"[가-힣]", lines[ask_start - 1])
                and not lines[ask_start - 1].endswith("이다.")
            ):
                ask_start -= 1
    ask_lines = [
        line for line in lines[ask_start:choice_start]
        if line != "보기"
    ]
    # PDF text extraction frequently places ④ and ⑤ on one source line.  Split
    # the whole band by every marker while retaining internal newlines used by
    # stacked fractions and radicals.
    choice_source = "\n".join(lines[choice_start:])
    marker_matches = list(re.finditer(r"[①②③④⑤]", choice_source))
    choice_blocks = [
        choice_source[match.end(): marker_matches[index + 1].start() if index + 1 < len(marker_matches) else None]
        .strip().splitlines()
        for index, match in enumerate(marker_matches)
    ]
    sentence_indices = [
        index for index, line in enumerate(lines[:ask_start])
        if re.search(r"(?:다|이다|한다|된다)\.$", line)
    ]
    passage = ""
    if sentence_indices:
        passage_end = sentence_indices[-1]
        pieces = lines[: passage_end + 1]
        if pieces and pieces[0].startswith("정답과 해설"):
            pieces.pop(0)
            trailing_space.pop(0)
        pieces[0] = _LOCAL_NUMBER_RE.sub("", pieces[0])
        passage = _normalize_block(pieces)
        synthetic_source = "\n".join(
            piece + (" " if trailing_space[index] else "")
            for index, piece in enumerate(pieces)
        )
        passage = rejoin_ebs_soft_wraps(passage, synthetic_source)
    normalized_ask = _stacked_question_fraction(
        _normalize_block([_LOCAL_NUMBER_RE.sub("", line) for line in ask_lines])
    )
    normalized_ask = rejoin_ebs_soft_wraps(normalized_ask, source_text)
    return EbsTextSlots(
        passage,
        normalized_ask,
        tuple(_normalize_block(block) for block in choice_blocks),
    )
