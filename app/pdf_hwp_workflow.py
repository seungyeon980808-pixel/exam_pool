"""Reusable inventory and acceptance gates for PDF -> editable HWP work.

The existing converter can prepare text, coordinates, and draft HWP content,
but a draft is never accepted merely because it opens or has the expected page
count.  This module records source-derived evidence and applies two explicit
policies:

``STRICT_PASS``
    Content, native editability, object inventory, page geometry, columns, and
    object coordinates all agree with the source evidence.

``PRACTICAL_PASS_WITH_EXCEPTIONS``
    The non-negotiable content/editability gates still agree.  Only a limited
    raster figure, small pagination/font/line-flow differences, or simplified
    decorative header/footer may remain, and every exception is reported.

The module contains no exam text and is safe to use with local-only manifests.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
import hashlib
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

import fitz


_ROLE_RE = re.compile(
    r"(?i)(?:\(|\[|\{|\s|[-_])*(?:문제(?:지)?|정답(?:\s*및\s*)?해설|해설(?:지)?|solution|answer|problem|questions?)(?:\)|\]|\}|\s|[-_])*"
)
_MATH_TOKEN_RE = re.compile(
    r"(?:\d+(?:\.\d+)?)|(?:<=|>=|!=|->)|(?:[≤≥≠=<>±+\-−×÷*/^(){}\[\]|√∫Σ∑→∞])|"
    r"(?:[A-Za-zΑ-Ωα-ω가-힣])"
)
_HEADING_ONLY_RE = re.compile(r"^\s*\d{1,3}\s*[.)]?\s*$")
_FORBIDDEN_IMAGE_ROLES = frozenset(
    {
        "page_capture",
        "header_capture",
        "footer_capture",
        "body_capture",
        "item_capture",
        "solution_body_capture",
        "formula_and_text_capture",
    }
)


class PdfRole(StrEnum):
    PROBLEM = "problem"
    SOLUTION = "solution"


class PdfSourceKind(StrEnum):
    TEXT = "text"
    SCAN = "scan"
    MIXED = "mixed"


class AcceptanceStatus(StrEnum):
    STRICT_PASS = "STRICT_PASS"
    PRACTICAL_PASS_WITH_EXCEPTIONS = "PRACTICAL_PASS_WITH_EXCEPTIONS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class PdfInputInventory:
    path: Path
    pair_key: str
    role: PdfRole
    sha256: str
    page_count: int
    page_size_pt: tuple[tuple[float, float], ...]
    source_kind: PdfSourceKind


@dataclass(frozen=True, slots=True)
class PdfPairInventory:
    pair_key: str
    problem: PdfInputInventory
    solution: PdfInputInventory


@dataclass(frozen=True, slots=True)
class EditableItemEvidence:
    item_id: str
    page: int
    body_text: str
    formulas: tuple[str, ...] = ()
    choices: tuple[str, ...] = ()
    view_count: int = 0
    table_count: int = 0
    figure_ids: tuple[str, ...] = ()
    column: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    native_text: bool = False
    native_equations: bool = False
    body_replaced_by_image: bool = False
    ocr_image_duplicate: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EditableItemEvidence":
        bbox = value.get("bbox")
        return cls(
            item_id=str(value["item_id"]),
            page=int(value["page"]),
            body_text=str(value.get("body_text", value.get("text", ""))),
            formulas=tuple(str(item) for item in value.get("formulas", ())),
            choices=tuple(str(item) for item in value.get("choices", ())),
            view_count=int(value.get("view_count", value.get("views", 0))),
            table_count=int(value.get("table_count", value.get("tables", 0))),
            figure_ids=tuple(str(item) for item in value.get("figure_ids", ())),
            column=int(value["column"]) if value.get("column") is not None else None,
            bbox=tuple(float(item) for item in bbox) if bbox is not None else None,
            native_text=bool(value.get("native_text", False)),
            native_equations=bool(value.get("native_equations", False)),
            body_replaced_by_image=bool(value.get("body_replaced_by_image", False)),
            ocr_image_duplicate=bool(value.get("ocr_image_duplicate", False)),
        )


@dataclass(frozen=True, slots=True)
class EditableFigureEvidence:
    figure_id: str
    item_id: str
    page: int
    bbox: tuple[float, float, float, float]
    placement: str
    raster: bool = False
    exception_reason: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EditableFigureEvidence":
        return cls(
            figure_id=str(value["figure_id"]),
            item_id=str(value["item_id"]),
            page=int(value["page"]),
            bbox=tuple(float(item) for item in value["bbox"]),
            placement=str(value.get("placement", "inline")),
            raster=bool(value.get("raster", False)),
            exception_reason=str(value.get("exception_reason", "")),
        )


@dataclass(frozen=True, slots=True)
class RasterEvidence:
    resource: str
    role: str
    page: int | None = None
    item_id: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    contains_editable_content: bool = False
    duplicates_editable_text: bool = False
    referenced: bool = True
    reason: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RasterEvidence":
        bbox = value.get("bbox")
        return cls(
            resource=str(value["resource"]),
            role=str(value.get("role", "unclassified")),
            page=int(value["page"]) if value.get("page") is not None else None,
            item_id=str(value["item_id"]) if value.get("item_id") is not None else None,
            bbox=tuple(float(item) for item in bbox) if bbox is not None else None,
            contains_editable_content=bool(value.get("contains_editable_content", False)),
            duplicates_editable_text=bool(value.get("duplicates_editable_text", False)),
            referenced=bool(value.get("referenced", True)),
            reason=str(value.get("reason", "")),
        )


@dataclass(frozen=True, slots=True)
class EditableDocumentEvidence:
    document_id: str
    role: PdfRole
    page_count: int
    page_size_pt: tuple[tuple[float, float], ...]
    page_columns: tuple[int, ...]
    items: tuple[EditableItemEvidence, ...]
    figures: tuple[EditableFigureEvidence, ...] = ()
    rasters: tuple[RasterEvidence, ...] = ()
    header_native: bool = True
    footer_native: bool = True
    page_number_native: bool = True
    hwp_reopen: bool = False
    hwpx_reopen: bool = False
    endnote_count: int = 0
    footnote_count: int = 0
    plain_endnote_marker_count: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EditableDocumentEvidence":
        sizes = value.get("page_size_pt", value.get("page_sizes_pt", ()))
        return cls(
            document_id=str(value["document_id"]),
            role=PdfRole(str(value["role"])),
            page_count=int(value["page_count"]),
            page_size_pt=tuple(tuple(float(part) for part in item) for item in sizes),
            page_columns=tuple(int(item) for item in value.get("page_columns", ())),
            items=tuple(EditableItemEvidence.from_mapping(item) for item in value.get("items", ())),
            figures=tuple(EditableFigureEvidence.from_mapping(item) for item in value.get("figures", ())),
            rasters=tuple(RasterEvidence.from_mapping(item) for item in value.get("rasters", ())),
            header_native=bool(value.get("header_native", True)),
            footer_native=bool(value.get("footer_native", True)),
            page_number_native=bool(value.get("page_number_native", True)),
            hwp_reopen=bool(value.get("hwp_reopen", False)),
            hwpx_reopen=bool(value.get("hwpx_reopen", False)),
            endnote_count=int(value.get("endnote_count", 0)),
            footnote_count=int(value.get("footnote_count", 0)),
            plain_endnote_marker_count=int(value.get("plain_endnote_marker_count", 0)),
        )


@dataclass(frozen=True, slots=True)
class WorkflowGate:
    code: str
    passed: bool
    strict_required: bool
    practical_required: bool
    detail: str


@dataclass(frozen=True, slots=True)
class EditableWorkflowReport:
    status: AcceptanceStatus
    gates: tuple[WorkflowGate, ...]
    exceptions: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.status is not AcceptanceStatus.FAIL

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "exceptions": list(self.exceptions),
            "gates": [
                {
                    "code": gate.code,
                    "passed": gate.passed,
                    "strict_required": gate.strict_required,
                    "practical_required": gate.practical_required,
                    "detail": gate.detail,
                }
                for gate in self.gates
            ],
        }


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _role(path: Path) -> PdfRole:
    name = path.stem.casefold()
    if re.search(r"해설|정답|solution|answer", name):
        return PdfRole.SOLUTION
    if re.search(r"문제|problem|questions?", name):
        return PdfRole.PROBLEM
    raise ValueError(f"PDF role is not explicit in filename: {path.name}")


def normalized_pair_key(path: Path) -> str:
    key = _ROLE_RE.sub(" ", path.stem.casefold())
    key = re.sub(r"[^0-9a-z가-힣]+", " ", key)
    return " ".join(key.split())


def inspect_pdf_input(path: Path) -> PdfInputInventory:
    """Record content-neutral metadata; no source text is persisted."""
    path = path.resolve()
    role = _role(path)
    with fitz.open(path) as document:
        page_sizes = tuple((round(page.rect.width, 3), round(page.rect.height, 3)) for page in document)
        text_pages = sum(1 for page in document if len(page.get_text("text").strip()) >= 20)
        if text_pages == 0:
            kind = PdfSourceKind.SCAN
        elif text_pages == document.page_count:
            kind = PdfSourceKind.TEXT
        else:
            kind = PdfSourceKind.MIXED
        pages = document.page_count
    return PdfInputInventory(path, normalized_pair_key(path), role, _sha256(path), pages, page_sizes, kind)


def identify_pdf_pairs(paths: Iterable[Path]) -> tuple[PdfPairInventory, ...]:
    grouped: dict[str, dict[PdfRole, list[PdfInputInventory]]] = {}
    for path in paths:
        inventory = inspect_pdf_input(path)
        grouped.setdefault(inventory.pair_key, {}).setdefault(inventory.role, []).append(inventory)
    pairs: list[PdfPairInventory] = []
    failures: list[str] = []
    for pair_key, roles in sorted(grouped.items()):
        problems = roles.get(PdfRole.PROBLEM, [])
        solutions = roles.get(PdfRole.SOLUTION, [])
        if len(problems) != 1 or len(solutions) != 1:
            failures.append(f"{pair_key}: problem={len(problems)}, solution={len(solutions)}")
            continue
        pairs.append(PdfPairInventory(pair_key, problems[0], solutions[0]))
    if failures:
        raise ValueError("PDF pair inventory failed: " + "; ".join(failures))
    return tuple(pairs)


def math_tokens(value: str) -> tuple[str, ...]:
    return tuple(_MATH_TOKEN_RE.findall(value.replace("\n", " ")))


def _counter_detail(expected: Sequence[str], actual: Sequence[str]) -> str:
    missing = Counter(expected) - Counter(actual)
    extra = Counter(actual) - Counter(expected)
    return f"missing={dict(missing)} extra={dict(extra)}"


def _bbox_matches(
    expected: tuple[float, float, float, float],
    actual: tuple[float, float, float, float],
    page_size: tuple[float, float],
    tolerance: float,
) -> bool:
    width, height = page_size
    return all(
        abs(left - right) / max(width if index % 2 == 0 else height, 1.0) <= tolerance
        for index, (left, right) in enumerate(zip(expected, actual))
    )


def _gate(
    code: str,
    passed: bool,
    detail: str,
    *,
    strict_required: bool = True,
    practical_required: bool = True,
) -> WorkflowGate:
    return WorkflowGate(code, passed, strict_required, practical_required, detail)


def audit_editable_workflow(
    expected: EditableDocumentEvidence,
    actual: EditableDocumentEvidence,
    *,
    coordinate_tolerance: float = 0.02,
    practical_page_delta_ratio: float = 0.20,
) -> EditableWorkflowReport:
    """Evaluate all evidence; missing evidence is a failure, not an implied PASS."""
    gates: list[WorkflowGate] = []
    exceptions: list[str] = []
    expected_items = Counter(item.item_id for item in expected.items)
    actual_items = Counter(item.item_id for item in actual.items)
    item_inventory_ok = expected_items == actual_items and all(count == 1 for count in actual_items.values())
    gates.append(_gate("item_inventory", item_inventory_ok, _counter_detail(tuple(expected_items.elements()), tuple(actual_items.elements()))))

    expected_by_id = {item.item_id: item for item in expected.items}
    actual_by_id = {item.item_id: item for item in actual.items}
    body_failures: list[str] = []
    token_failures: list[str] = []
    object_failures: list[str] = []
    edit_failures: list[str] = []
    duplicate_failures: list[str] = []
    coordinate_failures: list[str] = []
    for item_id in sorted(set(expected_by_id) | set(actual_by_id)):
        source = expected_by_id.get(item_id)
        result = actual_by_id.get(item_id)
        if source is None or result is None:
            continue
        if not result.body_text.strip() or _HEADING_ONLY_RE.fullmatch(result.body_text):
            body_failures.append(item_id)
        source_tokens = math_tokens(" ".join((source.body_text, *source.formulas, *source.choices)))
        result_tokens = math_tokens(" ".join((result.body_text, *result.formulas, *result.choices)))
        if source_tokens != result_tokens:
            token_failures.append(f"{item_id}: {_counter_detail(source_tokens, result_tokens)}")
        if (source.view_count, source.table_count, source.figure_ids) != (
            result.view_count,
            result.table_count,
            result.figure_ids,
        ):
            object_failures.append(item_id)
        if not result.native_text or (source.formulas and not result.native_equations) or result.body_replaced_by_image:
            edit_failures.append(item_id)
        if result.ocr_image_duplicate:
            duplicate_failures.append(item_id)
        if source.page != result.page or source.column != result.column:
            coordinate_failures.append(f"{item_id}: page/column")
        elif source.bbox is None or result.bbox is None or source.page < 1 or source.page > len(expected.page_size_pt):
            coordinate_failures.append(f"{item_id}: bbox missing")
        elif not _bbox_matches(source.bbox, result.bbox, expected.page_size_pt[source.page - 1], coordinate_tolerance):
            coordinate_failures.append(f"{item_id}: bbox")
    gates.extend(
        (
            _gate("item_body_present", not body_failures, ", ".join(body_failures) or "all item bodies present"),
            _gate("numeric_formula_choice_tokens", not token_failures, "; ".join(token_failures[:12]) or "all math tokens match"),
            _gate("view_table_figure_inventory", not object_failures, ", ".join(object_failures) or "all per-item object counts match"),
            _gate("native_editability", not edit_failures, ", ".join(edit_failures) or "all text and formulas are native/editable"),
            _gate("no_ocr_image_duplication", not duplicate_failures, ", ".join(duplicate_failures) or "no OCR/image duplication"),
            _gate("item_coordinates", not coordinate_failures, "; ".join(coordinate_failures[:12]) or "all coordinates match", practical_required=False),
        )
    )

    expected_figures = Counter(figure.figure_id for figure in expected.figures)
    actual_figures = Counter(figure.figure_id for figure in actual.figures)
    figure_inventory_ok = expected_figures == actual_figures and all(count == 1 for count in actual_figures.values())
    gates.append(_gate("figure_inventory", figure_inventory_ok, _counter_detail(tuple(expected_figures.elements()), tuple(actual_figures.elements()))))
    expected_figure_by_id = {figure.figure_id: figure for figure in expected.figures}
    actual_figure_by_id = {figure.figure_id: figure for figure in actual.figures}
    assignment_failures: list[str] = []
    figure_coordinate_failures: list[str] = []
    raster_failures: list[str] = []
    raster_figures: list[str] = []
    for figure_id in sorted(set(expected_figure_by_id) | set(actual_figure_by_id)):
        source = expected_figure_by_id.get(figure_id)
        result = actual_figure_by_id.get(figure_id)
        if source is None or result is None:
            continue
        if source.item_id != result.item_id or source.placement != result.placement:
            assignment_failures.append(figure_id)
        if source.page != result.page or not _bbox_matches(
            source.bbox,
            result.bbox,
            expected.page_size_pt[source.page - 1],
            coordinate_tolerance,
        ):
            figure_coordinate_failures.append(figure_id)
        if result.raster and not result.exception_reason.strip():
            raster_failures.append(figure_id)
        elif result.raster:
            raster_figures.append(figure_id)
            exceptions.append(f"limited raster figure {figure_id}: {result.exception_reason}")
    gates.extend(
        (
            _gate("figure_assignment", not assignment_failures, ", ".join(assignment_failures) or "all figures belong to the expected item"),
            _gate("figure_coordinates", not figure_coordinate_failures, ", ".join(figure_coordinate_failures) or "all figure coordinates match", practical_required=False),
            _gate("all_figures_native_vector", not raster_figures, ", ".join(raster_figures) or "all figures are native/vector", practical_required=False),
            _gate("raster_exception_ledger", not raster_failures, ", ".join(raster_failures) or "all raster figures have a reason"),
        )
    )

    forbidden_rasters = [
        resource.resource
        for resource in actual.rasters
        if resource.role in _FORBIDDEN_IMAGE_ROLES or resource.contains_editable_content
    ]
    unmapped_rasters = [
        resource.resource
        for resource in actual.rasters
        if not resource.referenced
        or resource.role == "unclassified"
        or (resource.role == "figure" and (not resource.item_id or resource.page is None or resource.bbox is None))
    ]
    raster_duplicates = [resource.resource for resource in actual.rasters if resource.duplicates_editable_text]
    gates.extend(
        (
            _gate("no_page_header_body_item_capture", not forbidden_rasters, ", ".join(forbidden_rasters) or "no forbidden capture image"),
            _gate("all_images_mapped", not unmapped_rasters, ", ".join(unmapped_rasters) or "all image resources mapped"),
            _gate("no_image_text_duplicate", not raster_duplicates, ", ".join(raster_duplicates) or "no image/text duplicate"),
        )
    )

    page_count_ok = expected.page_count == actual.page_count
    delta = abs(expected.page_count - actual.page_count)
    practical_delta_limit = max(1, round(expected.page_count * practical_page_delta_ratio))
    practical_page_ok = delta <= practical_delta_limit
    if not page_count_ok and practical_page_ok:
        exceptions.append(f"minor page-count difference: source={expected.page_count}, result={actual.page_count}")
    gates.append(
        _gate(
            "page_count",
            page_count_ok,
            f"source={expected.page_count}, result={actual.page_count}, practical_limit={practical_delta_limit}",
            practical_required=not practical_page_ok,
        )
    )
    page_size_ok = expected.page_size_pt == actual.page_size_pt
    if not page_size_ok:
        exceptions.append("page-size sequence differs")
    gates.append(_gate("page_size", page_size_ok, "page-size sequences match" if page_size_ok else "page-size sequences differ", practical_required=False))
    columns_ok = expected.page_columns == actual.page_columns
    if not columns_ok:
        exceptions.append("page/column reflow differs")
    gates.append(_gate("page_columns", columns_ok, "column sequence matches" if columns_ok else "column sequence differs", practical_required=False))

    native_header_ok = actual.header_native and actual.footer_native and actual.page_number_native
    if not native_header_ok:
        exceptions.append("header/footer/page-number simplified, without a capture image")
    gates.append(
        _gate(
            "native_header_footer_page_number",
            native_header_ok,
            f"header={actual.header_native}, footer={actual.footer_native}, page_number={actual.page_number_native}",
            practical_required=False,
        )
    )
    gates.append(_gate("hwp_hwpx_reopen", actual.hwp_reopen and actual.hwpx_reopen, f"hwp={actual.hwp_reopen}, hwpx={actual.hwpx_reopen}"))
    no_notes = actual.endnote_count == actual.footnote_count == actual.plain_endnote_marker_count == 0
    gates.append(
        _gate(
            "pre_endnote_document_has_no_notes",
            no_notes,
            f"endnotes={actual.endnote_count}, footnotes={actual.footnote_count}, plain_markers={actual.plain_endnote_marker_count}",
        )
    )

    strict_pass = all(gate.passed for gate in gates if gate.strict_required)
    practical_pass = all(gate.passed for gate in gates if gate.practical_required)
    status = (
        AcceptanceStatus.STRICT_PASS
        if strict_pass
        else AcceptanceStatus.PRACTICAL_PASS_WITH_EXCEPTIONS
        if practical_pass and exceptions
        else AcceptanceStatus.FAIL
    )
    return EditableWorkflowReport(status, tuple(gates), tuple(dict.fromkeys(exceptions)))
