import hashlib
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "extract_source_crop_hd.py"
SOURCE = ROOT / "PDF" / "p1_2024_11.pdf"


def test_item20_source_crop_hd_is_reproducible_with_complete_metadata(tmp_path: Path) -> None:
    # Given: the local KICE source PDF and an isolated destination.
    output_dir = tmp_path / "crop"

    # When: the source-crop extractor runs through its real CLI boundary.
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--pdf",
            str(SOURCE),
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: it emits the selected lossless crop and stable provenance metadata.
    assert completed.returncode == 0, completed.stderr
    image_path = output_dir / "item20_source_crop_hd.png"
    metadata_path = output_dir / "item20_source_crop_hd.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    with Image.open(image_path) as image:
        assert image.format == "PNG"
        assert image.mode == "L"
        assert image.size == (2285, 668)
    assert metadata == {
        "asset_mode": "source_crop_hd",
        "image_path": "assets/item_figures/item20_source_crop_hd.png",
        "source_pdf": "PDF/p1_2024_11.pdf",
        "page_no": 4,
        "bbox": [463.8, 718.261, 738.0, 798.661],
        "dpi": 600,
        "width_px": 2285,
        "height_px": 668,
        "aspect_ratio": 3.420659,
        "source_hash": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "asset_hash": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        "semantic_region_coverage": 1.0,
        "selected_from": "embedded_lossless_png",
    }


def test_item20_source_crop_hd_compares_600_and_1200_dpi_without_upscale_selection(
    tmp_path: Path,
) -> None:
    # Given: the item-20 semantic region in the source PDF.
    output_dir = tmp_path / "comparison"

    # When: the extractor evaluates both requested render resolutions.
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--pdf",
            str(SOURCE),
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: both comparison renders cover the region, but native 600 dpi is selected.
    assert completed.returncode == 0, completed.stderr
    with Image.open(output_dir / "item20_region_600dpi.png") as render_600:
        assert render_600.width >= 1800
        assert render_600.height >= 500
        assert 3.0 < render_600.width / render_600.height < 4.0
    with Image.open(output_dir / "item20_region_1200dpi.png") as render_1200:
        assert render_1200.width == 2 * 2285
        assert render_1200.height >= 2 * 668
    comparison = json.loads((output_dir / "item20_render_comparison.json").read_text(encoding="utf-8"))
    assert comparison["selected_dpi"] == 600
    assert comparison["native_embedded_size_px"] == [2285, 668]
    assert comparison["renders"]["600"]["width_px"] == 2285
    assert comparison["renders"]["1200"]["width_px"] == 4570
