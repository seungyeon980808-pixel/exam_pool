from __future__ import annotations

import json
from pathlib import Path

import fitz

from app.pdf_hwp_crop_assets import crop_region
from app.pdf_hwp_pipeline_models import DetectedItem


def _vector_pdf(path: Path) -> tuple[float, float, float, float]:
    source_bbox = (80.0, 80.0, 340.0, 310.0)
    with fitz.open() as document:
        page = document.new_page(width=420, height=360)
        page.draw_rect(fitz.Rect(120, 100, 280, 200), color=(0, 0, 0), width=1)
        page.draw_line(fitz.Point(120, 150), fitz.Point(280, 150), color=(0, 0, 0), width=1)
        page.insert_text((108, 155), "q", fontsize=12)
        page.insert_text((285, 155), "m/s", fontsize=12)
        page.insert_text(
            (190, 235),
            "(\uac00)",
            fontsize=12,
            fontname="malgun",
            fontfile="C:/Windows/Fonts/malgun.ttf",
        )
        page.insert_text((100, 285), "BODY OUTSIDE FIGURE", fontsize=12)
        document.save(path)
    return source_bbox


def _span_map(page: fitz.Page) -> dict[str, list[float]]:
    found: dict[str, list[float]] = {}
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                found[str(span["text"])] = [round(float(value), 3) for value in span["bbox"]]
    return found


def test_vector_object_union_keeps_connected_labels_and_excludes_caption_and_body(tmp_path: Path) -> None:
    # Given: vector geometry has two connected labels, a separate PDF-text caption, and adjacent prose.
    source_pdf = tmp_path / "vector.pdf"
    source_bbox = _vector_pdf(source_pdf)
    item = DetectedItem(1, 7, 0, source_bbox, "\uadf8\ub9bc (\uac00)\ub97c \ub098\ud0c0\ub0b8 \uac83\uc774\ub2e4.")
    with fitz.open(source_pdf) as document:
        page = document[0]
        spans = _span_map(page)
        expected = fitz.Rect(120, 100, 280, 200)
        expected |= fitz.Rect(spans["q"])
        expected |= fitz.Rect(spans["m/s"])
        expected_bbox = [round(float(value), 3) for value in expected]
        expected_drawing_count = len(page.get_drawings())

    # When: the crop is segmented from vector objects and connected PDF text.
    artifact = crop_region(source_pdf, item, source_bbox, tmp_path / "output", "figure")
    metadata = json.loads(artifact.provenance_path.read_text(encoding="utf-8"))

    # Then: only the drawing cluster and labels are rendered; caption/body remain independent evidence.
    assert metadata["bbox"] == expected_bbox
    assert metadata["image_count"] == 0
    assert metadata["drawing_count"] == expected_drawing_count
    assert metadata["protected_texts"] == ["q", "m/s"]
    assert metadata["excluded_texts"] == ["BODY OUTSIDE FIGURE"]
    assert metadata["caption_detection_source"] == "pdf_text"
    assert metadata["caption_text_source"] == "pdf_text"
    assert metadata["caption_candidates"] == [{
        "text": "(\uac00)",
        "bbox": spans["(\uac00)"],
        "pixel_bbox": [0, 0, 0, 0],
        "detection_source": "pdf_text",
        "excluded": True,
        "confidence": 1.0,
    }]
    assert metadata["manual_review_required"] is False


def test_vector_cluster_excludes_nearby_sentence_but_keeps_short_diagram_label(tmp_path: Path) -> None:
    # Given: a short label and nearby body sentences all fall within the geometric label radius.
    source_pdf = tmp_path / "vector-near-body.pdf"
    source_bbox = (70.0, 70.0, 440.0, 260.0)
    sentence = "물체는 오른쪽으로 이동한다."
    with fitz.open() as document:
        page = document.new_page(width=500, height=320)
        page.draw_rect(fitz.Rect(120, 100, 280, 200), color=(0, 0, 0), width=1)
        page.insert_text((108, 155), "q", fontsize=12)
        page.insert_text(
            (282, 115), sentence, fontsize=10,
            fontname="malgun", fontfile="C:/Windows/Fonts/malgun.ttf",
        )
        document.save(source_pdf)
    item = DetectedItem(1, 7, 0, source_bbox, "도형을 나타낸 것이다.")

    # When: vector objects are clustered independently from nearby prose.
    artifact = crop_region(source_pdf, item, source_bbox, tmp_path / "output", "figure")
    metadata = json.loads(artifact.provenance_path.read_text(encoding="utf-8"))

    # Then: proximity preserves q, but sentence grammar keeps prose out of the figure bbox.
    assert metadata["protected_texts"] == ["q"]
    assert metadata["excluded_texts"] == [sentence]


def test_vector_cluster_excludes_disconnected_page_separator(tmp_path: Path) -> None:
    # Given: a coherent diagram component and an unrelated horizontal rule share the requested bbox.
    source_pdf = tmp_path / "vector-with-separator.pdf"
    source_bbox = (70.0, 70.0, 350.0, 270.0)
    with fitz.open() as document:
        page = document.new_page(width=420, height=320)
        page.draw_rect(fitz.Rect(120, 100, 280, 200), color=(0, 0, 0), width=1)
        page.draw_line(fitz.Point(120, 150), fitz.Point(280, 150), color=(0, 0, 0), width=1)
        page.draw_line(fitz.Point(80, 245), fitz.Point(340, 245), color=(0, 0, 0), width=1)
        page.insert_text((108, 155), "q", fontsize=12)
        document.save(source_pdf)
    item = DetectedItem(1, 7, 0, source_bbox, "도형을 나타낸 것이다.")
    with fitz.open(source_pdf) as document:
        spans = _span_map(document[0])
        expected = fitz.Rect(120, 100, 280, 200) | fitz.Rect(spans["q"])

    # When: drawing objects are segmented by connected component.
    artifact = crop_region(source_pdf, item, source_bbox, tmp_path / "output", "figure")
    metadata = json.loads(artifact.provenance_path.read_text(encoding="utf-8"))

    # Then: the page separator cannot expand the selected figure or its drawing count.
    assert metadata["bbox"] == [round(float(value), 3) for value in expected]
    assert metadata["drawing_count"] == 2


def test_vector_cluster_excludes_page_border_that_crosses_source_bbox(tmp_path: Path) -> None:
    # Given: an old exam PDF has one page-border drawing that crosses every item,
    # while the requested prompt contains a smaller self-contained diagram.
    source_pdf = tmp_path / "vector-with-page-border.pdf"
    source_bbox = (70.0, 170.0, 350.0, 300.0)
    with fitz.open() as document:
        page = document.new_page(width=420, height=360)
        page.draw_rect(fitz.Rect(20, 20, 400, 340), color=(0, 0, 0), width=1)
        page.draw_rect(fitz.Rect(120, 190, 280, 260), color=(0, 0, 0), width=1)
        page.draw_line(fitz.Point(120, 225), fitz.Point(280, 225), color=(0, 0, 0), width=1)
        document.save(source_pdf)
    item = DetectedItem(1, 7, 0, source_bbox, "도형을 나타낸 것이다.")

    # When: vector objects are selected inside the requested prompt region.
    artifact = crop_region(source_pdf, item, source_bbox, tmp_path / "output", "figure")
    metadata = json.loads(artifact.provenance_path.read_text(encoding="utf-8"))

    # Then: the page border cannot win the component score or escape the prompt.
    assert metadata["bbox"] == [120.0, 190.0, 280.0, 260.0]
    assert metadata["drawing_count"] == 2
