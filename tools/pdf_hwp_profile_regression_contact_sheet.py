# /// script
# requires-python = ">=3.13"
# dependencies = ["pillow"]
# ///
# ─── How to run ───
# Imported by pdf_hwp_profile_regression_evidence.py; it has no standalone CLI.
"""Deterministic paired-render contact sheets for profile regression evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from app.pdf_hwp_atomic import atomic_replace


@dataclass(frozen=True, slots=True)
class ContactSheetError(RuntimeError):
    label: str

    def __str__(self) -> str:
        return f"paired render absent: {self.label}"


def render_rows(
    root: Path, items: tuple[int, ...], label: str,
) -> tuple[tuple[str, Path, Path], ...]:
    """Resolve stable source/generated render pairs for selected item numbers."""
    renders = root / "verification-evidence" / "renders"
    return tuple((
        f"{label} q{number}", renders / f"source-item-{number:04d}.png",
        renders / f"generated-item-{number:04d}.png",
    ) for number in items)


def write_contact_sheet(
    rows: tuple[tuple[str, Path, Path], ...], target: Path,
) -> Path:
    """Write a byte-deterministic two-column contact sheet."""
    width, row_height, panel_size = 1600, 220, (770, 180)
    canvas = Image.new("RGB", (width, row_height * len(rows) + 28), "white")
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 6), "SOURCE", fill="black")
    draw.text((808, 6), "GENERATED", fill="black")
    for index, (label, source, generated) in enumerate(rows):
        if not source.is_file() or not generated.is_file():
            raise ContactSheetError(label)
        top = 28 + index * row_height
        draw.text((8, top + 2), label, fill="black")
        for left, path in ((8, source), (808, generated)):
            with Image.open(path) as opened:
                image = opened.convert("RGB")
            image.thumbnail(panel_size, Image.Resampling.LANCZOS)
            canvas.paste(image, (left, top + 24))
            image.close()
        draw.line(
            (0, top + row_height - 1, width, top + row_height - 1), fill="#808080",
        )
    temporary = target.with_suffix(".tmp.png")
    canvas.save(temporary, format="PNG", compress_level=9)
    atomic_replace(temporary, target)
    canvas.close()
    return target
