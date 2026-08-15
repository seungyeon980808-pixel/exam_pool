"""Render source and generated PDFs into labeled full-page comparison sheets."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont, ImageStat


SUBJECTS = ("c1", "c2", "b1", "b2", "e1", "e2")


def render_pdf(path: Path, output_dir: Path, prefix: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    with fitz.open(path) as document:
        if document.page_count < 1:
            raise RuntimeError(f"{path}: PDF contains no pages")
        for index, page in enumerate(document, 1):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            target = output_dir / f"{prefix}-page-{index}.png"
            pixmap.save(target)
            paths.append(target)
    return paths


def page_metrics(path: Path) -> dict:
    with Image.open(path) as image:
        gray = image.convert("L")
        histogram = gray.histogram()
        total = max(1, sum(histogram))
        nonwhite = sum(histogram[:248]) / total
        standard_deviation = ImageStat.Stat(gray).stddev[0]
        return {
            "path": str(path.resolve()),
            "width": image.width,
            "height": image.height,
            "nonwhite_fraction": nonwhite,
            "grayscale_stddev": standard_deviation,
            "visibly_nonblank": nonwhite > 0.005 and standard_deviation > 2.0,
        }


def make_sheet(source_pages: list[Path], output_pages: list[Path], target: Path) -> None:
    if not source_pages or not output_pages:
        raise RuntimeError("comparison sheet requires non-empty source and generated pages")
    column_width = 640
    gutter = 24
    header_height = 52
    row_gap = 18
    font = ImageFont.load_default()

    def resized(path: Path) -> Image.Image:
        image = Image.open(path).convert("RGB")
        height = round(image.height * column_width / image.width)
        return image.resize((column_width, height), Image.Resampling.LANCZOS)

    source_images = [resized(path) for path in source_pages]
    output_images = [resized(path) for path in output_pages]
    left_height = sum(image.height + row_gap for image in source_images)
    right_height = sum(image.height + row_gap for image in output_images)
    canvas = Image.new(
        "RGB",
        (column_width * 2 + gutter * 3, header_height + max(left_height, right_height) + gutter),
        "#e8ebef",
    )
    draw = ImageDraw.Draw(canvas)
    draw.text((gutter, 18), "SOURCE PDF — full pages", fill="#111827", font=font)
    right_x = gutter * 2 + column_width
    draw.text((right_x, 18), "GENERATED PDF — preflight-safe items", fill="#111827", font=font)
    y = header_height
    for index, image in enumerate(source_images, 1):
        canvas.paste(image, (gutter, y))
        draw.rectangle((gutter, y, gutter + 76, y + 22), fill="#ffffff")
        draw.text((gutter + 6, y + 6), f"source {index}", fill="#111827", font=font)
        y += image.height + row_gap
    y = header_height
    for index, image in enumerate(output_images, 1):
        canvas.paste(image, (right_x, y))
        draw.rectangle((right_x, y, right_x + 88, y + 22), fill="#ffffff")
        draw.text((right_x + 6, y + 6), f"generated {index}", fill="#111827", font=font)
        y += image.height + row_gap
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target)
    for image in (*source_images, *output_images):
        image.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generated", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.generated.read_text(encoding="utf-8"))
    subjects = receipt.get("subjects")
    if not isinstance(subjects, list):
        raise RuntimeError("generation receipt subjects must be a list")
    names = tuple(row.get("subject") for row in subjects)
    if names != SUBJECTS or any(row.get("status") != "ready" for row in subjects):
        raise RuntimeError(
            f"generation receipt must contain six ready subjects {SUBJECTS}, observed {names}"
        )
    rows = []
    for subject in subjects:
        root = args.generated.parent / subject["subject"] / "visual-comparison"
        source_pages = render_pdf(Path(subject["source_path"]), root, "source")
        output_pages = render_pdf(Path(subject["pdf_path"]), root, "generated")
        sheet = root / "source-vs-generated.png"
        make_sheet(source_pages, output_pages, sheet)
        rows.append({
            "subject": subject["subject"],
            "source_path": subject["source_path"],
            "generated_pdf_path": subject["pdf_path"],
            "source_pages": [page_metrics(path) for path in source_pages],
            "generated_pages": [page_metrics(path) for path in output_pages],
            "comparison_sheet": str(sheet.resolve()),
        })
    result = {
        "rendered_at_utc": datetime.now(UTC).isoformat(),
        "subjects": rows,
        "all_generated_pages_visibly_nonblank": all(
            page["visibly_nonblank"] for row in rows for page in row["generated_pages"]
        ),
    }
    if len(rows) != len(SUBJECTS) or any(not row["generated_pages"] for row in rows):
        raise RuntimeError("visual comparison did not render every required subject")
    target = args.generated.parent / "visual-comparison-summary.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "summary": str(target.resolve()),
        "sheets": len(rows),
        "all_generated_pages_visibly_nonblank": result["all_generated_pages_visibly_nonblank"],
    }, ensure_ascii=False))
    return 0 if result["all_generated_pages_visibly_nonblank"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
