from pathlib import Path
import hashlib

from PIL import Image


ASSET = Path(__file__).parents[1] / "assets" / "item_figures" / "item20_original.png"


def test_item20_original_figure_crop_has_expected_geometry():
    """The source-faithful item 20 crop must exclude surrounding question text."""
    assert ASSET.exists()
    with Image.open(ASSET) as image:
        assert image.format == "PNG"
        assert image.size == (1718, 555)
        assert image.mode in {"RGB", "RGBA"}
    assert hashlib.sha256(ASSET.read_bytes()).hexdigest() == (
        "cf278add312d1f6ccd2cad9d2491bc285f07a9b802c9b3fc9c6119acf2e0416c"
    )
