"""DB-agnostic PDF item detection, crop, and HwpPalette conversion pipeline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Final

import fitz

from .exam_items import detect_items as detect_page_items
from .integrations.hwppalette import HwpPaletteError, hwppalette_provider
from .pdf_hwp_hwp_preflight import preflight_units
from .pdf_hwp_pipeline_models import (
    ConversionRequest,
    ConversionResourceLockedError,
    ConversionResult,
    ConversionTypesetError,
    ConversionUnit,
    CropArtifact,
    DetectedItem,
    DetectionResult,
    DocumentTypesetter,
    EmptyConversionError,
    GeneratedDocument,
    InvalidCropError,
    InvalidSourcePdfError,
    LayoutStyle,
    PipelinePhase,
    PipelineProgress,
    ProgressSink,
    DraftArtifact,
    DraftExtractionError,
    UnsupportedDraftLayoutError,
)


DEFAULT_CROP_DPI: Final = 300


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _trim_trailing_page_matter(page: fitz.Page, bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Stop a last-in-column item before separated footer or instruction matter."""
    x0, y0, x1, y1 = bbox
    if y1 < page.rect.height - 40:
        return bbox
    occupied: list[tuple[float, float]] = []
    for block in page.get_text("blocks"):
        block_box = fitz.Rect(block[:4])
        if block_box.x1 >= x0 and block_box.x0 <= x1 and block_box.y1 >= y0:
            occupied.append((max(y0, block_box.y0), min(y1, block_box.y1)))
    for image in page.get_image_info():
        image_box = fitz.Rect(image["bbox"])
        if image_box.x1 >= x0 and image_box.x0 <= x1 and image_box.y1 >= y0:
            occupied.append((max(y0, image_box.y0), min(y1, image_box.y1)))
    occupied.sort()
    previous_end = y0
    for start, end in occupied:
        if start - previous_end >= 72 and previous_end - y0 >= 60:
            return (x0, y0, x1, round(start - 8, 3))
        previous_end = max(previous_end, end)
    return bbox


def detect_items(source_pdf: Path) -> DetectionResult:
    """Detect numbered item bounds and source text across a PDF."""
    source = source_pdf.resolve()
    try:
        document = fitz.open(source)
    except (fitz.FileDataError, FileNotFoundError, OSError) as exc:
        raise InvalidSourcePdfError(source_pdf=source, detail=str(exc)) from exc
    found: list[DetectedItem] = []
    with document:
        for page_index, page in enumerate(document):
            for raw in detect_page_items(page):
                raw_bbox = tuple(round(float(raw[key]), 3) for key in ("x0", "y0", "x1", "y1"))
                bbox = _trim_trailing_page_matter(page, raw_bbox)
                clip = fitz.Rect(bbox)
                found.append(
                    DetectedItem(
                        page_number=page_index + 1,
                        item_number=int(raw["num"]),
                        column=int(raw["col"]),
                        bbox=bbox,
                        source_text=page.get_text(clip=clip).strip(),
                    )
                )
        page_count = document.page_count
    return DetectionResult(
        source_pdf=source,
        source_hash=_sha256(source),
        page_count=page_count,
        items=tuple(found),
    )


def crop_item(
    source_pdf: Path,
    item: DetectedItem,
    output_dir: Path,
    *,
    dpi: int = DEFAULT_CROP_DPI,
) -> CropArtifact:
    """Render one detected item bbox and write complete provenance beside it."""
    source = source_pdf.resolve()
    if dpi < DEFAULT_CROP_DPI or min(item.bbox[2] - item.bbox[0], item.bbox[3] - item.bbox[1]) <= 0:
        raise InvalidCropError(item=item)
    target = output_dir.resolve()
    target.mkdir(parents=True, exist_ok=True)
    stem = f"page-{item.page_number}-item-{item.item_number}-{dpi}dpi"
    image_path = target / f"{stem}.png"
    provenance_path = target / f"{stem}.json"
    try:
        document = fitz.open(source)
    except (fitz.FileDataError, FileNotFoundError, OSError) as exc:
        raise InvalidSourcePdfError(source_pdf=source, detail=str(exc)) from exc
    with document:
        if item.page_number > document.page_count:
            raise InvalidCropError(item=item)
        pixmap = document[item.page_number - 1].get_pixmap(
            dpi=dpi,
            clip=fitz.Rect(item.bbox),
            alpha=False,
        )
        pixmap.save(image_path)
    payload = {
        "asset_mode": "pdf_item_crop_hd",
        "source_pdf": str(source),
        "source_hash": _sha256(source),
        "page_number": item.page_number,
        "item_number": item.item_number,
        "bbox": list(item.bbox),
        "dpi": dpi,
        "width_px": pixmap.width,
        "height_px": pixmap.height,
        "asset_hash": _sha256(image_path),
    }
    provenance_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return CropArtifact(image_path, provenance_path, pixmap.width, pixmap.height)


def build_editable_draft(
    source_pdf: Path,
    item: DetectedItem,
    output_dir: Path,
    *,
    layout_style: LayoutStyle = LayoutStyle.SUNEUNG,
) -> DraftArtifact:
    """Convert a detected PDF item into editable HwpPalette markdown and source assets."""
    from .pdf_hwp_draft import build_draft

    source_image = crop_item(source_pdf, item, output_dir, dpi=DEFAULT_CROP_DPI)
    return build_draft(source_pdf.resolve(), item, output_dir.resolve(), source_image, layout_style)


class HwpPaletteTypesetter:
    """Adapter from the pipeline contract to ExamPool's real HwpPalette provider."""

    def typeset(
        self,
        markdown: str,
        output_dir: Path,
        layout_style: LayoutStyle,
        asset_dirs: tuple[Path, ...],
    ) -> GeneratedDocument:
        try:
            hwppalette_provider.register_photo_dirs(tuple(
                asset_dir.resolve() for asset_dir in asset_dirs
            ))
            preview = hwppalette_provider.render_preview(
                markdown,
                scope="set",
                exam_page=True,
                layout_style=layout_style.value,
                timeout=_typeset_timeout_seconds(markdown),
            )
        except HwpPaletteError as exc:
            raise ConversionTypesetError(detail=str(exc)) from exc
        token = str(preview["token"])
        source_hwp = hwppalette_provider.preview_asset(token, "hwp")
        source_pdf = hwppalette_provider.preview_asset(token, "pdf")
        if source_hwp is None or source_pdf is None:
            raise ConversionTypesetError(detail="preview outputs are missing")
        output_dir.mkdir(parents=True, exist_ok=True)
        hwp_path = output_dir / "converted.hwp"
        pdf_path = output_dir / "converted.pdf"
        shutil.copy2(source_hwp, hwp_path)
        shutil.copy2(source_pdf, pdf_path)
        rendered_pages: list[Path] = []
        for page in preview["pages"]:
            source_page = hwppalette_provider.preview_asset(token, "page", int(page["page_no"]))
            if source_page is None:
                raise ConversionTypesetError(detail=f"rendered page {page['page_no']} is missing")
            target_page = output_dir / f"page-{page['page_no']}.png"
            shutil.copy2(source_page, target_page)
            rendered_pages.append(target_page)
        return GeneratedDocument(hwp_path, pdf_path, tuple(rendered_pages))


def _typeset_timeout_seconds(markdown: str) -> int:
    """Keep single-item latency bounded while allowing real exam-size sets."""
    templates = sum(
        1 for line in markdown.splitlines()
        if line.startswith("\\수능") and line.endswith("\\")
    )
    if templates <= 1:
        return 90
    return min(1800, 60 + templates * 60)


def typeset_conversion(
    request: ConversionRequest,
    *,
    typesetter: DocumentTypesetter | None = None,
    progress: ProgressSink | None = None,
) -> ConversionResult:
    """Typeset ordered units and persist a hash-addressable output manifest."""
    selected = typesetter or HwpPaletteTypesetter()
    total = len(request.units)
    if total == 0:
        raise EmptyConversionError(job_key=request.job_key)
    if progress is not None:
        progress(PipelineProgress(PipelinePhase.PREPARING, 0, total))
    request.output_dir.mkdir(parents=True, exist_ok=True)
    units = preflight_units(request.units, request.layout_style)
    markdown = "\n\n".join(unit.palette_markdown.strip() for unit in units) + "\n"
    if progress is not None:
        progress(PipelineProgress(PipelinePhase.TYPESETTING, 0, total))
    try:
        generated = selected.typeset(
            markdown, request.output_dir, request.layout_style, request.asset_dirs,
        )
    except PermissionError as exc:
        raise ConversionResourceLockedError(detail=str(exc)) from exc
    manifest_path = request.output_dir / "conversion.json"
    manifest = {
        "job_key": request.job_key,
        "item_numbers": [unit.item_number for unit in request.units],
        "layout_style": request.layout_style.value,
        "hwp_sha256": _sha256(generated.hwp_path),
        "pdf_sha256": _sha256(generated.pdf_path),
        "figure_asset_hashes": {
            str(unit.item_number): list(unit.figure_asset_hashes)
            for unit in units if unit.figure_asset_hashes
        },
        "rendered_pages": [path.name for path in generated.rendered_pages],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if progress is not None:
        progress(PipelineProgress(PipelinePhase.COMPLETE, total, total))
    return ConversionResult(
        generated.hwp_path,
        generated.pdf_path,
        generated.rendered_pages,
        manifest_path,
    )

