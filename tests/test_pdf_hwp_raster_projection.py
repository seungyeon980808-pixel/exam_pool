from __future__ import annotations

from PIL import Image, ImageDraw

from app.pdf_hwp_raster_caption_segmentation import _ink_column_counts, _ink_row_counts


def test_binary_ink_projections_count_only_pixels_below_threshold() -> None:
    image = Image.new("L", (5, 4), "white")
    draw = ImageDraw.Draw(image)
    draw.point((0, 0), fill=0)
    draw.point((2, 0), fill=234)
    draw.point((4, 0), fill=235)
    draw.point((2, 2), fill=10)
    draw.point((3, 3), fill=100)

    assert _ink_row_counts(image) == (2, 0, 1, 1)
    assert _ink_column_counts(image, 0, 4) == (1, 0, 2, 1, 0)
    assert _ink_column_counts(image, 2, 4) == (0, 0, 1, 1, 0)
