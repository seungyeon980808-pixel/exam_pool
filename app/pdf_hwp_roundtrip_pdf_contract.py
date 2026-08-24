"""Per-item structural contracts for generated round-trip PDFs."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import re
from typing import Final

import fitz

from .pdf_hwp_pipeline_models import ConversionUnit, DetectedItem, InvalidSourcePdfError
from .pdf_hwp_roundtrip_generated_detection import detect_generated_items


RawBBox = tuple[float, float, float, float]
_CHOICES: Final = tuple("①②③④⑤")
_BOGI_CLAIMS: Final = tuple("ㄱㄴㄷ")
_FIFTH_CHOICE: Final = "⑤ㄱ,ㄴ,ㄷ"
_LEADER: Final = re.compile(r"^\s*([1-9]\d{0,3})\.")
_FORMULA_COMBINATION: Final = re.compile(r"^\s*\\수식\{[^{}\n]+\}(?:\s*,\s*\\수식\{[^{}\n]+\}){2,}\s*$")
_BASELINE_TOLERANCE: Final = 2.5


class GeneratedPdfIssue(StrEnum):
    """Stable issue codes emitted by the generated-PDF contract."""

    UNREADABLE_PDF = "unreadable_pdf"
    MISSING_GENERATED_ITEM = "missing_generated_item"
    DUPLICATE_GENERATED_ITEM = "duplicate_generated_item"
    MISSING_CHOICE = "missing_choice"
    MISSING_BOGI_MARKER = "missing_bogi_marker"
    MISSING_BOGI_CLAIM = "missing_bogi_claim"
    FIFTH_CHOICE_TEXT_MISMATCH = "fifth_choice_text_mismatch"
    FIFTH_CHOICE_WRAPPED = "fifth_choice_wrapped"
    ITEM_BOUNDARY_SPILL = "item_boundary_spill"


@dataclass(frozen=True, slots=True)
class InvalidGeneratedPdfContractRequest(ValueError):
    """Raised when hapdap identities are not part of the selected set."""

    unselected_hapdap_items: tuple[int, ...]

    def __str__(self) -> str:
        return "hapdap items are not selected: " + ",".join(
            str(number) for number in self.unselected_hapdap_items
        )


@dataclass(frozen=True, slots=True)
class InvalidExpectedFifthChoice(ValueError):
    item_number: int

    def __str__(self) -> str:
        return f"expected fifth choice is empty: {self.item_number}"


@dataclass(frozen=True, slots=True)
class GeneratedPdfContractRequest:
    """Typed boundary for one generated PDF and its expected item identities."""

    generated_pdf: Path
    selected_item_numbers: tuple[int, ...]
    hapdap_item_numbers: tuple[int, ...] = ()
    expected_fifth_choices: tuple[ExpectedFifthChoice, ...] = ()

    def __post_init__(self) -> None:
        unselected = tuple(sorted(
            set(self.hapdap_item_numbers) - set(self.selected_item_numbers),
        ))
        if unselected:
            raise InvalidGeneratedPdfContractRequest(unselected)
        expected_numbers = tuple(value.item_number for value in self.expected_fifth_choices)
        invalid = tuple(sorted(
            (set(expected_numbers) - set(self.selected_item_numbers))
            | {number for number in expected_numbers if expected_numbers.count(number) > 1},
        ))
        if invalid:
            raise InvalidGeneratedPdfContractRequest(invalid)


@dataclass(frozen=True, slots=True)
class ExpectedFifthChoice:
    """Prepared fifth-choice text expected for one generated item."""

    item_number: int
    prepared_text: str

    def __post_init__(self) -> None:
        if not self.prepared_text.strip():
            raise InvalidExpectedFifthChoice(self.item_number)


def generated_pdf_contract_request(generated_pdf: Path, units: tuple[ConversionUnit, ...],
                                   ) -> GeneratedPdfContractRequest:
    """Build complete per-item expectations from persisted prepared units."""
    numbers = tuple(unit.item_number for unit in units)
    hapdap = tuple(unit.item_number for unit in units if "합답" in unit.palette_markdown)
    choices = tuple(
        ExpectedFifthChoice(unit.item_number, unit.palette_markdown.splitlines()[-1])
        for unit in units if unit.item_number in hapdap
        or _FORMULA_COMBINATION.fullmatch(unit.palette_markdown.splitlines()[-1])
    )
    return GeneratedPdfContractRequest(generated_pdf, numbers, hapdap, choices)


@dataclass(frozen=True, slots=True)
class GeneratedPdfContractIssue:
    """One located generated-PDF contract failure."""

    code: GeneratedPdfIssue
    item_number: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class GeneratedItemContract:
    """Observable text geometry and issues for one unique selected item."""

    item_number: int
    page_number: int
    bbox: RawBBox
    baselines: tuple[str, ...]
    issues: tuple[GeneratedPdfContractIssue, ...]


@dataclass(frozen=True, slots=True)
class GeneratedPdfContractResult:
    """Complete deterministic generated-PDF structural verdict."""

    generated_pdf: Path
    items: tuple[GeneratedItemContract, ...]
    issues: tuple[GeneratedPdfContractIssue, ...]


@dataclass(frozen=True, slots=True)
class _Span:
    text: str
    x: float
    baseline: float


def _normalized(text: str) -> str:
    return "".join(text.split())


def _formula_visible_text(text: str) -> str:
    """Expose formula sources so prepared choice structure can be compared."""
    result: list[str] = []
    index = 0
    prefix = r"\수식{"
    while index < len(text):
        if not text.startswith(prefix, index):
            result.append(text[index])
            index += 1
            continue
        start = index + len(prefix)
        depth = 1
        end = start
        while end < len(text) and depth:
            depth += (text[end] == "{") - (text[end] == "}")
            end += 1
        result.append(text[start:end - 1] if depth == 0 else text[start:])
        index = end
    return "".join(result)


def _baselines(page: fitz.Page, item: DetectedItem) -> tuple[str, ...]:
    payload = page.get_text("dict", clip=fitz.Rect(item.bbox), sort=True)
    spans = tuple(
        _Span(str(span["text"]), float(span["bbox"][0]), float(span["bbox"][3]))
        for block in payload["blocks"] for line in block.get("lines", ())
        for span in line.get("spans", ()) if str(span["text"]).strip()
    )
    groups: list[list[_Span]] = []
    for span in sorted(spans, key=lambda value: (value.baseline, value.x)):
        group = next((
            candidate for candidate in groups
            if abs(candidate[0].baseline - span.baseline) <= _BASELINE_TOLERANCE
        ), None)
        if group is None:
            groups.append([span])
        else:
            group.append(span)
    geometry = tuple(
        " ".join(span.text for span in sorted(group, key=lambda value: value.x))
        for group in sorted(groups, key=lambda value: value[0].baseline)
    )
    physical_lines = tuple(filter(None, map(str.strip, item.source_text.splitlines())))
    return tuple(dict.fromkeys((*geometry, *physical_lines)))


def _issue(code: GeneratedPdfIssue, item_number: int | None,
           detail: str) -> GeneratedPdfContractIssue:
    return GeneratedPdfContractIssue(code, item_number, detail)


def _item_issues(item: DetectedItem, baselines: tuple[str, ...], hapdap: bool,
                 expected_fifth: ExpectedFifthChoice | None,
                 ) -> tuple[GeneratedPdfContractIssue, ...]:
    compact = _normalized(item.source_text)
    issues: list[GeneratedPdfContractIssue] = []
    missing_choices = tuple(marker for marker in _CHOICES if marker not in compact)
    if missing_choices:
        issues.append(_issue(
            GeneratedPdfIssue.MISSING_CHOICE, item.item_number,
            "missing=" + "".join(missing_choices),
        ))
    if hapdap and "<보기>" not in compact:
        issues.append(_issue(
            GeneratedPdfIssue.MISSING_BOGI_MARKER, item.item_number, "missing=<보기>",
        ))
    missing_claims = tuple(
        marker for marker in _BOGI_CLAIMS if f"{marker}." not in compact
    )
    if hapdap and missing_claims:
        issues.append(_issue(
            GeneratedPdfIssue.MISSING_BOGI_CLAIM, item.item_number,
            "missing=" + "".join(missing_claims),
        ))
    expected_target = _FIFTH_CHOICE if hapdap and _FIFTH_CHOICE in compact else None
    if expected_fifth is not None and "⑤" in compact:
        generated_fifth = compact.rsplit("⑤", 1)[1]
        prepared = _normalized(_formula_visible_text(expected_fifth.prepared_text))
        formula = r"\수식{" in expected_fifth.prepared_text
        same_structure = len(generated_fifth.split(",")) == len(prepared.split(","))
        if not same_structure or (not formula and not generated_fifth.startswith(prepared)):
            issues.append(_issue(
                GeneratedPdfIssue.FIFTH_CHOICE_TEXT_MISMATCH, item.item_number,
                f"prepared={prepared};generated={generated_fifth}",
            ))
        expected_value = generated_fifth if formula and not generated_fifth.startswith(prepared) else prepared
        expected_target = "⑤" + expected_value
    if expected_target and not any(
        expected_target in _normalized(line) for line in baselines
    ):
        issues.append(_issue(
            GeneratedPdfIssue.FIFTH_CHOICE_WRAPPED, item.item_number,
            "expected one baseline containing complete fifth choice",
        ))
    spilled = tuple(sorted({
        int(match.group(1))
        for line in baselines for match in _LEADER.finditer(line)
        if int(match.group(1)) != item.item_number
    }))
    if spilled:
        issues.append(_issue(
            GeneratedPdfIssue.ITEM_BOUNDARY_SPILL, item.item_number,
            "leaders=" + ",".join(str(number) for number in spilled),
        ))
    return tuple(issues)


def inspect_generated_pdf_contract(
    request: GeneratedPdfContractRequest,
) -> GeneratedPdfContractResult:
    """Inspect every selected generated item without page-index assumptions."""
    source = request.generated_pdf.resolve()
    try:
        detection = detect_generated_items(source)
    except InvalidSourcePdfError as error:
        issue = _issue(GeneratedPdfIssue.UNREADABLE_PDF, None, str(error))
        return GeneratedPdfContractResult(source, (), (issue,))
    groups = {
        number: tuple(item for item in detection.items if item.item_number == number)
        for number in request.selected_item_numbers
    }
    expected_by_item = {
        value.item_number: value for value in request.expected_fifth_choices
    }
    items: list[GeneratedItemContract] = []
    issues: list[GeneratedPdfContractIssue] = []
    with fitz.open(source) as document:
        for number in request.selected_item_numbers:
            matches = groups[number]
            if not matches:
                issues.append(_issue(
                    GeneratedPdfIssue.MISSING_GENERATED_ITEM, number, "detected=0",
                ))
                continue
            if len(matches) > 1:
                issues.append(_issue(
                    GeneratedPdfIssue.DUPLICATE_GENERATED_ITEM, number,
                    f"detected={len(matches)}",
                ))
                continue
            item = matches[0]
            baselines = _baselines(document[item.page_number - 1], item)
            item_issues = _item_issues(
                item, baselines, number in request.hapdap_item_numbers,
                expected_by_item.get(number),
            )
            items.append(GeneratedItemContract(
                number, item.page_number, item.bbox, baselines, item_issues,
            ))
            issues.extend(item_issues)
    return GeneratedPdfContractResult(source, tuple(items), tuple(issues))
