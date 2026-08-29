"""Strict, source-driven QA for editable PDF -> HWP/HWPX conversions.

The converter is allowed to use a small raster figure, but never a page or a
question capture.  This module deliberately requires every acceptance gate;
page count, file readability, or a non-empty text stream can never produce a
PASS on its own.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Mapping, Sequence
from zipfile import BadZipFile, ZipFile

import fitz
from PIL import Image, ImageChops


PAGE_SIZE_TOLERANCE_PT = 0.5
DEFAULT_VISUAL_THRESHOLD = 0.03
CAPTURE_COVERAGE_THRESHOLD = 0.70
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
_TOKEN_RE = re.compile(
    r"(?:\d+(?:\.\d+)?)|(?:[≤≥≠=<>±+\-×÷*/^(){}\[\]|√∫Σ→∞])|(?:[A-Za-z가-힣])",
)
_ITEM_RE = re.compile(r"(?<!\d)(\d{1,3})\s*[.)]")
_PIC_RE = re.compile(r"<hp:pic\b.*?</hp:pic\s*>", re.I | re.S)
_BIN_ID_RE = re.compile(r"(?:binaryItemID|binDataID|binDataRef|href)\s*=\s*[\"']([^\"']+)", re.I)
_SIZE_RE = re.compile(
    r"<(?:hc:sz|hp:sz|hc:extent|hp:extent)\b[^>]*?width\s*=\s*[\"']([0-9.]+)[\"'][^>]*?height\s*=\s*[\"']([0-9.]+)[\"']",
    re.I | re.S,
)


@dataclass(frozen=True, slots=True)
class ItemRecord:
    """One source or generated question observation."""

    item_id: str
    page: int
    bbox: tuple[float, float, float, float] | None = None
    text: str = ""
    formulas: tuple[str, ...] = ()
    choices: tuple[str, ...] = ()
    view_count: int = 0
    table_count: int = 0
    figure_ids: tuple[str, ...] = ()
    column: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ItemRecord":
        bbox = value.get("bbox")
        return cls(
            item_id=str(value["item_id"] if "item_id" in value else value["id"]),
            page=int(value["page"]),
            bbox=tuple(float(x) for x in bbox) if bbox is not None else None,
            text=str(value.get("text", "")),
            formulas=tuple(str(x) for x in value.get("formulas", ())),
            choices=tuple(str(x) for x in value.get("choices", ())),
            view_count=int(value.get("view_count", value.get("views", 0))),
            table_count=int(value.get("table_count", value.get("tables", 0))),
            figure_ids=tuple(str(x) for x in value.get("figure_ids", ())),
            column=int(value["column"]) if value.get("column") is not None else None,
        )


@dataclass(frozen=True, slots=True)
class FigureRecord:
    figure_id: str
    item_id: str
    page: int
    bbox: tuple[float, float, float, float]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FigureRecord":
        return cls(
            figure_id=str(value["figure_id"] if "figure_id" in value else value["id"]),
            item_id=str(value["item_id"]),
            page=int(value["page"]),
            bbox=tuple(float(x) for x in value["bbox"]),
        )


@dataclass(frozen=True, slots=True)
class DocumentManifest:
    page_count: int
    items: tuple[ItemRecord, ...]
    figures: tuple[FigureRecord, ...] = ()
    page_size_pt: tuple[tuple[float, float], ...] = ()
    page_columns: tuple[int, ...] = ()
    editable_text: bool = False
    native_equations: bool = False
    reopen_hwp: bool = False
    reopen_hwpx: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DocumentManifest":
        sizes = value.get("page_size_pt", value.get("page_sizes_pt", ()))
        return cls(
            page_count=int(value["page_count"]),
            items=tuple(ItemRecord.from_mapping(x) for x in value.get("items", ())),
            figures=tuple(FigureRecord.from_mapping(x) for x in value.get("figures", ())),
            page_size_pt=tuple(tuple(float(y) for y in x) for x in sizes),
            page_columns=tuple(int(x) for x in value.get("page_columns", ())),
            editable_text=bool(value.get("editable_text", False)),
            native_equations=bool(value.get("native_equations", False)),
            reopen_hwp=bool(value.get("reopen_hwp", False)),
            reopen_hwpx=bool(value.get("reopen_hwpx", False)),
        )


@dataclass(frozen=True, slots=True)
class GateResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class PageComparison:
    page: int
    diff_ratio: float
    source_path: Path
    result_path: Path
    overlay_path: Path
    diff_path: Path
    passed: bool


@dataclass(frozen=True, slots=True)
class ImageRecord:
    resource: str
    sha256: str
    width: int
    height: int
    reference_count: int
    declared_width: float | None
    declared_height: float | None
    coverage_ratio: float | None
    classification: str
    item_id: str | None = None
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True, slots=True)
class ImageAudit:
    records: tuple[ImageRecord, ...]

    @property
    def page_or_body_captures(self) -> tuple[ImageRecord, ...]:
        return tuple(x for x in self.records if x.classification == "page_capture")

    @property
    def unclassified(self) -> tuple[ImageRecord, ...]:
        return tuple(x for x in self.records if x.classification in {"unclassified", "unused"})

    @property
    def passed(self) -> bool:
        return not self.page_or_body_captures and not self.unclassified


@dataclass(frozen=True, slots=True)
class StrictQAReport:
    source_pdf: Path
    generated_pdf: Path
    hwpx: Path
    gates: tuple[GateResult, ...]
    page_comparisons: tuple[PageComparison, ...]
    image_audit: ImageAudit

    @property
    def passed(self) -> bool:
        return bool(self.gates) and all(gate.passed for gate in self.gates)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "PASS" if self.passed else "FAIL",
            "gates": [
                {"name": gate.name, "passed": gate.passed, "detail": gate.detail}
                for gate in self.gates
            ],
            "page_comparisons": [
                {"page": x.page, "diff_ratio": x.diff_ratio, "passed": x.passed,
                 "source": str(x.source_path), "result": str(x.result_path),
                 "overlay": str(x.overlay_path), "diff": str(x.diff_path)}
                for x in self.page_comparisons
            ],
            "images": [
                {"resource": x.resource, "sha256": x.sha256, "width": x.width,
                 "height": x.height, "reference_count": x.reference_count,
                 "declared_width": x.declared_width, "declared_height": x.declared_height,
                 "coverage_ratio": x.coverage_ratio, "classification": x.classification,
                 "item_id": x.item_id, "page": x.page, "bbox": list(x.bbox) if x.bbox else None}
                for x in self.image_audit.records
            ],
        }


def load_manifest(path: Path) -> DocumentManifest:
    """Load a converter-owned JSON manifest; source documents stay outside Git."""
    return DocumentManifest.from_mapping(json.loads(path.read_text(encoding="utf-8")))


def _pdf_observation(path: Path) -> tuple[int, tuple[tuple[float, float], ...], tuple[str, ...]]:
    with fitz.open(path) as document:
        return (
            document.page_count,
            tuple((round(page.rect.width, 3), round(page.rect.height, 3)) for page in document),
            tuple(page.get_text("text") for page in document),
        )


def extract_item_ids_from_text(text: str) -> tuple[str, ...]:
    """Extract conservative numbered headings for simple PDF readback checks."""
    return tuple(match.group(1) for match in _ITEM_RE.finditer(text))


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(value.replace("\\n", " ")))


def _counter_delta(left: Iterable[str], right: Iterable[str]) -> str:
    missing = Counter(left) - Counter(right)
    extra = Counter(right) - Counter(left)
    return f"missing={dict(missing)} extra={dict(extra)}"


def compare_content_tokens(expected: Sequence[ItemRecord], actual: Sequence[ItemRecord]) -> tuple[bool, str]:
    """Compare numbers, signs, variables, choices and formula tokens per item."""
    source = {item.item_id: item for item in expected}
    generated = {item.item_id: item for item in actual}
    failures: list[str] = []
    for item_id in sorted(set(source) | set(generated)):
        left = source.get(item_id)
        right = generated.get(item_id)
        if left is None or right is None:
            failures.append(f"{item_id}: item missing")
            continue
        left_tokens = _tokens(left.text + " " + " ".join(left.formulas + left.choices))
        right_tokens = _tokens(right.text + " " + " ".join(right.formulas + right.choices))
        if left_tokens != right_tokens:
            failures.append(f"{item_id}: {_counter_delta(left_tokens, right_tokens)}")
    return not failures, "; ".join(failures[:12]) or "all content tokens match"


def _counter_ids(items: Sequence[ItemRecord]) -> Counter[str]:
    return Counter(item.item_id for item in items)


def compare_item_structure(expected: DocumentManifest, actual: DocumentManifest) -> tuple[bool, str]:
    source_ids, generated_ids = _counter_ids(expected.items), _counter_ids(actual.items)
    if source_ids != generated_ids:
        return False, f"item ids differ: {_counter_delta(source_ids.elements(), generated_ids.elements())}"
    source_figures = Counter(x.figure_id for x in expected.figures)
    generated_figures = Counter(x.figure_id for x in actual.figures)
    if source_figures != generated_figures:
        return False, f"figure ids differ: {_counter_delta(source_figures.elements(), generated_figures.elements())}"
    failures: list[str] = []
    for item_id in sorted(source_ids):
        left = next(x for x in expected.items if x.item_id == item_id)
        right = next(x for x in actual.items if x.item_id == item_id)
        if (left.page, left.column, left.view_count, left.table_count, left.figure_ids) != (
            right.page, right.column, right.view_count, right.table_count, right.figure_ids
        ):
            failures.append(f"{item_id}: page/column/view/table/figure placement differs")
    return not failures, "; ".join(failures[:12]) or "item and structure inventories match"


def compare_coordinates(expected: DocumentManifest, actual: DocumentManifest, tolerance: float = 0.02) -> tuple[bool, str]:
    """Require page, column and normalized bbox agreement for items and figures."""
    failures: list[str] = []
    sizes = expected.page_size_pt or actual.page_size_pt
    def check(label: str, page: int, left: tuple[float, float, float, float] | None,
              right: tuple[float, float, float, float] | None) -> None:
        if left is None or right is None or page < 1 or page > len(sizes):
            failures.append(f"{label}: bbox missing")
            return
        width, height = sizes[page - 1]
        if width <= 0 or height <= 0 or any(abs(a - b) / max(width if i % 2 == 0 else height, 1.0) > tolerance
                                            for i, (a, b) in enumerate(zip(left, right))):
            failures.append(f"{label}: bbox outside {tolerance:.1%} normalized tolerance")
    for source in expected.items:
        target = next((x for x in actual.items if x.item_id == source.item_id), None)
        if target is None or source.page != target.page or source.column != target.column:
            failures.append(f"{source.item_id}: page/column changed")
        else:
            check(source.item_id, source.page, source.bbox, target.bbox)
    for source in expected.figures:
        target = next((x for x in actual.figures if x.figure_id == source.figure_id), None)
        if target is None or source.page != target.page or source.item_id != target.item_id:
            failures.append(f"{source.figure_id}: item/page changed")
        else:
            check(source.figure_id, source.page, source.bbox, target.bbox)
    return not failures, "; ".join(failures[:12]) or "all item and figure coordinates match"


def _render_pdf(path: Path, directory: Path, prefix: str, dpi: int) -> tuple[Path, ...]:
    directory.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []
    with fitz.open(path) as document:
        for page_number, page in enumerate(document, 1):
            target = directory / f"{prefix}-page-{page_number:03d}.png"
            page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False).save(target)
            rendered.append(target)
    return tuple(rendered)


def compare_pdf_pages(source_pdf: Path, generated_pdf: Path, output_dir: Path,
                      *, dpi: int = 300, threshold: float = DEFAULT_VISUAL_THRESHOLD) -> tuple[PageComparison, ...]:
    """Render every page at *dpi*, then emit same-size overlay and diff images."""
    source_paths = _render_pdf(source_pdf, output_dir / "source", "source", dpi)
    generated_paths = _render_pdf(generated_pdf, output_dir / "generated", "generated", dpi)
    comparisons: list[PageComparison] = []
    for page, (source_path, generated_path) in enumerate(zip(source_paths, generated_paths), 1):
        overlay_path = output_dir / "overlay" / f"page-{page:03d}.png"
        diff_path = output_dir / "diff" / f"page-{page:03d}.png"
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source_path) as left, Image.open(generated_path) as right:
            left = left.convert("RGB")
            right = right.convert("RGB")
            if left.size != right.size:
                ratio = 1.0
                overlay = Image.new("RGB", (max(left.width, right.width), max(left.height, right.height)), "red")
                diff = overlay.copy()
            else:
                delta = ImageChops.difference(left, right).convert("L")
                histogram = delta.histogram()
                changed = sum(histogram[24:])
                ratio = changed / max(1, left.width * left.height)
                overlay = Image.blend(left, right, 0.5)
                diff = Image.new("RGB", left.size, "white")
                diff.paste((220, 0, 0), mask=delta.point(lambda value: 255 if value >= 24 else 0))
            overlay.save(overlay_path)
            diff.save(diff_path)
            left.close(); right.close(); overlay.close(); diff.close()
        comparisons.append(PageComparison(page, round(ratio, 6), source_path, generated_path,
                                           overlay_path, diff_path, ratio <= threshold))
    return tuple(comparisons)


def _file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit_hwpx_images(hwpx_path: Path, figure_manifest: Sequence[Mapping[str, Any]] = (),
                      *, page_size_px: tuple[int, int] | None = None) -> ImageAudit:
    """Inventory every HWPX image and reject unreferenced/unmapped captures.

    A figure manifest is intentional provenance, not a way to weaken QA: each
    raster must be mapped to exactly one item/page/bbox and must be below the
    page-capture coverage threshold.  Header logos/decorations may be marked
    ``decorative`` but still cannot cover most of a page.
    """
    allowed: dict[str, Mapping[str, Any]] = {}
    for value in figure_manifest:
        key = str(value.get("sha256", value.get("resource", ""))).lower()
        if key:
            allowed[key] = value
    try:
        with ZipFile(hwpx_path) as archive:
            resources = [name for name in archive.namelist()
                         if name.lower().startswith("bindata/") and Path(name).suffix.lower() in _IMAGE_SUFFIXES]
            xml = "\n".join(archive.read(name).decode("utf-8", "ignore")
                             for name in archive.namelist() if name.lower().endswith(".xml"))
            records: list[ImageRecord] = []
            for resource in sorted(resources):
                data = archive.read(resource)
                digest = _file_hash(data)
                stem = Path(resource).stem.lower()
                refs = sum(1 for value in _BIN_ID_RE.findall(xml)
                            if stem == Path(value).stem.lower() or stem in value.lower())
                if refs == 0:
                    refs = len(re.findall(re.escape(Path(resource).name), xml, re.I))
                pic = next((x for x in _PIC_RE.findall(xml)
                            if stem in x.lower() or Path(resource).name.lower() in x.lower()), "")
                size_match = _SIZE_RE.search(pic)
                declared_width = float(size_match.group(1)) if size_match else None
                declared_height = float(size_match.group(2)) if size_match else None
                coverage = None
                if page_size_px and page_size_px[0] > 0 and page_size_px[1] > 0:
                    coverage = (declared_width * declared_height / (page_size_px[0] * page_size_px[1])
                                if declared_width and declared_height else len(data) / 1_000_000)
                entry = allowed.get(digest) or allowed.get(resource) or allowed.get(Path(resource).name)
                item_id = str(entry["item_id"]) if entry and entry.get("item_id") is not None else None
                page = int(entry["page"]) if entry and entry.get("page") is not None else None
                bbox = tuple(float(x) for x in entry["bbox"]) if entry and entry.get("bbox") else None
                if coverage is not None and coverage >= CAPTURE_COVERAGE_THRESHOLD:
                    classification = "page_capture"
                elif refs == 0:
                    classification = "unused"
                elif entry and entry.get("role") == "decorative" and refs > 0:
                    classification = "decorative"
                elif entry and entry.get("role") == "figure" and item_id and page and bbox:
                    classification = str(entry["role"])
                else:
                    classification = "unclassified"
                with Image.open(__import__("io").BytesIO(data)) as image:
                    width, height = image.size
                records.append(ImageRecord(resource, digest, width, height, refs, declared_width,
                                           declared_height, coverage, classification, item_id, page, bbox))
            return ImageAudit(tuple(records))
    except (BadZipFile, OSError, KeyError) as exc:
        return ImageAudit((ImageRecord("<invalid-hwpx>", "", 0, 0, 0, None, None, 1.0, "page_capture"),))


def run_strict_qa(source_pdf: Path, generated_pdf: Path, hwpx_path: Path,
                  expected: DocumentManifest, *, output_dir: Path,
                  actual: DocumentManifest | None = None,
                  figure_manifest: Sequence[Mapping[str, Any]] = (),
                  dpi: int = 300, visual_threshold: float = DEFAULT_VISUAL_THRESHOLD,
                  reopen_check: Callable[[], Mapping[str, bool]] | None = None) -> StrictQAReport:
    """Run every gate and return FAIL if even one required observation is absent."""
    output_dir.mkdir(parents=True, exist_ok=True)
    actual_manifest = actual
    source_pages, source_sizes, source_text = _pdf_observation(source_pdf)
    result_pages, result_sizes, result_text = _pdf_observation(generated_pdf)
    if actual_manifest is None:
        actual_manifest = DocumentManifest(
            page_count=result_pages,
            items=tuple(ItemRecord(item_id=item_id, page=page + 1, text=text)
                        for page, text in enumerate(result_text)
                        for item_id in extract_item_ids_from_text(text)),
            page_size_pt=result_sizes,
        )
    comparisons = compare_pdf_pages(source_pdf, generated_pdf, output_dir / "render_300dpi",
                                    dpi=dpi, threshold=visual_threshold)
    page_pixels = (round(source_sizes[0][0] * dpi / 72), round(source_sizes[0][1] * dpi / 72)) if source_sizes else None
    audit = audit_hwpx_images(hwpx_path, figure_manifest, page_size_px=page_pixels)
    reopen = reopen_check() if reopen_check else {"hwp": actual_manifest.reopen_hwp, "hwpx": actual_manifest.reopen_hwpx}
    page_pass = source_pages == expected.page_count == result_pages
    manifest_sizes = expected.page_size_pt or source_sizes
    size_pass = len(source_sizes) == len(result_sizes) == len(manifest_sizes) == expected.page_count and all(
        abs(a - b) <= PAGE_SIZE_TOLERANCE_PT
        for source_size, result_size, manifest_size in zip(source_sizes, result_sizes, manifest_sizes)
        for a, b in (*zip(source_size, result_size), *zip(source_size, manifest_size))
    )
    structure_pass, structure_detail = compare_item_structure(expected, actual_manifest)
    token_pass, token_detail = compare_content_tokens(expected.items, actual_manifest.items)
    coordinate_pass, coordinate_detail = compare_coordinates(expected, actual_manifest)
    figure_count_pass = len(expected.figures) == len(actual_manifest.figures)
    visual_pass = len(comparisons) == expected.page_count == result_pages and all(x.passed for x in comparisons)
    editable_pass = actual_manifest.editable_text and actual_manifest.native_equations
    reopen_pass = bool(reopen.get("hwp")) and bool(reopen.get("hwpx"))
    gates = (
        GateResult("page_count", page_pass, f"source={source_pages}, expected={expected.page_count}, generated={result_pages}"),
        GateResult("page_size", size_pass, f"tolerance={PAGE_SIZE_TOLERANCE_PT}pt"),
        GateResult("item_view_table_inventory", structure_pass, structure_detail),
        GateResult("figure_count", figure_count_pass and structure_pass, f"source={len(expected.figures)}, generated={len(actual_manifest.figures)}"),
        GateResult("image_audit", audit.passed, f"page_capture={len(audit.page_or_body_captures)}, unmapped={len(audit.unclassified)}"),
        GateResult("no_page_or_body_capture_images", not audit.page_or_body_captures, "whole-page/body raster is forbidden"),
        GateResult("numeric_formula_choice_tokens", token_pass, token_detail),
        GateResult("visual_300dpi_overlay", visual_pass, f"threshold={visual_threshold:.1%}"),
        GateResult("item_figure_coordinates", coordinate_pass, coordinate_detail),
        GateResult("hwp_hwpx_reopen", reopen_pass, f"hwp={bool(reopen.get('hwp'))}, hwpx={bool(reopen.get('hwpx'))}"),
        GateResult("native_editable_text_equations", editable_pass, f"editable_text={actual_manifest.editable_text}, native_equations={actual_manifest.native_equations}"),
    )
    return StrictQAReport(source_pdf, generated_pdf, hwpx_path, gates, comparisons, audit)
