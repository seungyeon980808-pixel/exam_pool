"""DB-agnostic PDF item detection, crop, and HwpPalette conversion pipeline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Final

import fitz

from .exam_items import detect_items as detect_page_items
from .pdf_hwp_ebs_detection import detect_ebs_textbook_items
from .integrations.hwppalette import HwpPaletteError, hwppalette_provider
from .pdf_hwp_hwp_preflight import preflight_units
from .pdf_hwp_hwp_retry import TypesetInvocation, typeset_with_transient_restart
from .pdf_hwp_raster_ocr import read_sidecar
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
_MAX_EDITABLE_PAGE_VECTORS: Final = 10_000


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
            trailing_words = page.get_text(
                "words", clip=fitz.Rect(x0, start, x1, y1),
            )
            if any(
                marker in str(word[4])
                for word in trailing_words
                for marker in "①②③④⑤"
            ):
                previous_end = max(previous_end, end)
                continue
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
        ocr_words = read_sidecar(source)
        if ocr_words:
            markers = tuple(
                (int(match.group(1)), word)
                for word in ocr_words
                if (match := re.match(r"^\s*(\d{1,2})\s*\.\s*", word.text)) is not None
            )
            for marker_index, (item_number, marker) in enumerate(markers):
                next_y = (
                    markers[marker_index + 1][1].bbox[1]
                    if marker_index + 1 < len(markers) else document[0].rect.y1
                )
                selected = tuple(
                    word for word in ocr_words
                    if word.bbox[1] >= marker.bbox[1] - 2 and word.bbox[1] < next_y
                )
                if not selected:
                    continue
                found.append(DetectedItem(
                    page_number=1,
                    item_number=item_number,
                    column=0,
                    bbox=(
                        max(0.0, min(word.bbox[0] for word in selected) - 8),
                        max(0.0, min(word.bbox[1] for word in selected) - 8),
                        min(document[0].rect.x1, max(word.bbox[2] for word in selected) + 8),
                        min(document[0].rect.y1, max(word.bbox[3] for word in selected) + 8),
                    ),
                    source_text="\n".join(word.text for word in selected),
                ))
        ebs_items = None if ocr_words else detect_ebs_textbook_items(document)
        if ebs_items is not None:
            found.extend(ebs_items)
        else:
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
            if not found:
                raster_pages = tuple(
                    page for page in document
                    if not page.get_text().strip() and bool(page.get_images())
                )
                if len(raster_pages) == document.page_count:
                    found.extend(
                        DetectedItem(
                            page_number=page.number + 1,
                            item_number=page.number + 1,
                            column=0,
                            bbox=tuple(round(float(value), 3) for value in page.rect),
                            source_text=f"[raster source page {page.number + 1}]",
                        )
                        for page in raster_pages
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


def _requires_exact_source_fallback(source_pdf: Path, item: DetectedItem) -> bool:
    """Avoid pathological vector segmentation while preserving the exact item."""
    try:
        with fitz.open(source_pdf) as document:
            if item.page_number > document.page_count:
                return False
            return len(document[item.page_number - 1].get_cdrawings()) > _MAX_EDITABLE_PAGE_VECTORS
    except (fitz.FileDataError, FileNotFoundError, OSError):
        return False


def _exact_source_draft(
    item: DetectedItem, source_image: CropArtifact, warning: str,
) -> DraftArtifact:
    from .pdf_hwp_figure_routing import stamp_single_prompt_figure

    exact_image = stamp_single_prompt_figure(source_image)
    markdown = "\n".join((
        "\\수능원문1대사진\\",
        f"\\{exact_image.image_path.stem}\\",
    ))
    return DraftArtifact(
        item.item_number,
        markdown,
        item.source_text,
        (),
        exact_image,
        exact_image,
        (warning,),
        (exact_image,),
        (),
    )


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
    if read_sidecar(source_pdf):
        from .pdf_hwp_raster_draft import build_raster_draft

        return build_raster_draft(
            source_pdf.resolve(), item, output_dir.resolve(), source_image, layout_style,
        )
    if _requires_exact_source_fallback(source_pdf, item):
        return _exact_source_draft(
            item, source_image, "excessive vector complexity preserved as source image",
        )
    try:
        return build_draft(
            source_pdf.resolve(), item, output_dir.resolve(), source_image, layout_style,
        )
    except StopIteration as exc:
        raise UnsupportedDraftLayoutError(
            item.page_number,
            item.item_number,
            "editable row iterator exhausted; source image preserved",
            source_image,
        ) from exc


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
    if request.header_subject.strip():
        markdown = f"\\수능과목머리말\\\n{request.header_subject.strip()}\n{markdown}"
    if progress is not None:
        progress(PipelineProgress(PipelinePhase.TYPESETTING, 0, total))
    try:
        generated = typeset_with_transient_restart(
            selected,
            TypesetInvocation(
                markdown, request.output_dir, request.layout_style, request.asset_dirs,
            ),
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

