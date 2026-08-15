# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf>=1.24", "pillow", "typer>=0.12"]
# ///
# --- How to run ---
# uv run tools/extract_source_crop_hd.py --pdf PDF/p1_2024_11.pdf --output-dir assets/item_figures
"""Reproduce the lossless item-20 source crop and resolution comparison."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Final

import fitz
from PIL import Image, ImageChops, ImageStat
import typer
from typing_extensions import Annotated


PAGE_NO: Final = 4
TARGET_BBOX: Final = (463.8, 718.261, 738.0, 798.661)
NATIVE_SIZE: Final = (2285, 668)
CANONICAL_IMAGE_PATH: Final = "assets/item_figures/item20_source_crop_hd.png"
MIN_WIDTH_PX: Final = 1800
MIN_HEIGHT_PX: Final = 500
MIN_ASPECT_RATIO: Final = 3.0
MAX_ASPECT_RATIO: Final = 4.0


@dataclass(frozen=True, slots=True)
class CropRequest:
    """Validated input paths for one deterministic extraction."""

    pdf: Path
    output_dir: Path


@dataclass(frozen=True, slots=True)
class SourceOccurrence:
    """One embedded image occurrence that covers the semantic region."""

    xref: int
    bbox: tuple[float, float, float, float]
    width_px: int
    height_px: int


class SourceRegionMissingError(RuntimeError):
    """Raised when the verified semantic region is absent from the source."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _find_occurrence(page: fitz.Page) -> SourceOccurrence:
    for info in page.get_image_info(xrefs=True):
        bbox = tuple(round(float(value), 3) for value in info["bbox"])
        size = (int(info["width"]), int(info["height"]))
        if bbox == TARGET_BBOX and size == NATIVE_SIZE:
            return SourceOccurrence(
                xref=int(info["xref"]),
                bbox=TARGET_BBOX,
                width_px=size[0],
                height_px=size[1],
            )
    raise SourceRegionMissingError


def _render_region(page: fitz.Page, occurrence: SourceOccurrence, dpi: int, output: Path) -> None:
    pixmap = page.get_pixmap(
        dpi=dpi,
        clip=fitz.Rect(occurrence.bbox),
        colorspace=fitz.csGRAY,
        alpha=False,
    )
    pixmap.save(output)


def _render_record(path: Path) -> dict[str, int | str]:
    with Image.open(path) as image:
        return {
            "width_px": image.width,
            "height_px": image.height,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }


def extract(request: CropRequest) -> None:
    request.output_dir.mkdir(parents=True, exist_ok=True)
    selected_path = request.output_dir / "item20_source_crop_hd.png"
    render_600_path = request.output_dir / "item20_region_600dpi.png"
    render_1200_path = request.output_dir / "item20_region_1200dpi.png"

    with fitz.open(request.pdf) as document:
        page = document[PAGE_NO - 1]
        occurrence = _find_occurrence(page)
        embedded = document.extract_image(occurrence.xref)
        selected_path.write_bytes(embedded["image"])
        _render_region(page, occurrence, 600, render_600_path)
        _render_region(page, occurrence, 1200, render_1200_path)

    with Image.open(selected_path) as selected:
        aspect_ratio = selected.width / selected.height
        if selected.width < MIN_WIDTH_PX or selected.height < MIN_HEIGHT_PX:
            raise SourceRegionMissingError
        if not MIN_ASPECT_RATIO < aspect_ratio < MAX_ASPECT_RATIO:
            raise SourceRegionMissingError

    with Image.open(render_600_path) as render_600, Image.open(render_1200_path) as render_1200:
        reduced = render_1200.resize(render_600.size, Image.Resampling.LANCZOS)
        downsample_mae = ImageStat.Stat(ImageChops.difference(render_600, reduced)).mean[0]

    metadata = {
        "asset_mode": "source_crop_hd",
        "image_path": CANONICAL_IMAGE_PATH,
        "source_pdf": "PDF/p1_2024_11.pdf",
        "page_no": PAGE_NO,
        "bbox": list(TARGET_BBOX),
        "dpi": 600,
        "width_px": occurrence.width_px,
        "height_px": occurrence.height_px,
        "aspect_ratio": round(aspect_ratio, 6),
        "source_hash": _sha256(request.pdf),
        "asset_hash": _sha256(selected_path),
        "semantic_region_coverage": 1.0,
        "selected_from": "embedded_lossless_png",
    }
    comparison = {
        "selected_dpi": 600,
        "selection_reason": "native embedded PNG already supplies 600 dpi horizontal detail",
        "native_embedded_size_px": list(NATIVE_SIZE),
        "renders": {
            "600": _render_record(render_600_path),
            "1200": _render_record(render_1200_path),
        },
        "downsample_1200_to_600_mae": round(downsample_mae, 6),
    }
    (request.output_dir / "item20_source_crop_hd.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (request.output_dir / "item20_render_comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(
    pdf: Annotated[Path, typer.Option(exists=True, file_okay=True, dir_okay=False)],
    output_dir: Annotated[Path, typer.Option(file_okay=False)],
) -> None:
    """Extract item 20 from PDF into OUTPUT_DIR without generative processing."""
    extract(CropRequest(pdf=pdf.resolve(), output_dir=output_dir.resolve()))


if __name__ == "__main__":
    typer.run(main)
