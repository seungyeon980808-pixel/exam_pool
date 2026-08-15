from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageChops, ImageDraw

from app.pdf_hwp_crop_assets import crop_region
from app.pdf_hwp_pipeline import build_editable_draft, detect_items
from app.pdf_hwp_pipeline_models import DetectedItem, LayoutStyle


_REAL_SOURCE = Path("PDF/p1_2024_11.pdf")
_PANEL_SOURCE_TEXT = "\uadf8\ub9bc (\uac00)\uc640 (\ub098)\ub97c \ub098\ud0c0\ub0b8 \uac83\uc774\ub2e4."


@dataclass(frozen=True, slots=True)
class _RasterFixture:
    pdf_path: Path
    image_png: bytes
    image_bbox: tuple[float, float, float, float]
    source_bbox: tuple[float, float, float, float]


def _diagram_png(*, caption_groups: int) -> bytes:
    image = Image.new("L", (400, 200), "white")
    draw = ImageDraw.Draw(image)
    draw.line((20, 130, 380, 130), fill="black", width=2)
    draw.line((80, 25, 80, 150), fill="black", width=2)
    draw.polygon(((380, 130), (365, 123), (365, 137)), fill="black")
    for y in range(40, 121, 16):
        draw.line((200, y, 200, y + 7), fill="black", width=2)
    draw.rectangle((35, 138, 48, 151), outline="black", width=2)  # q-like label
    draw.line((260, 146, 355, 146), fill="black", width=2)  # lower plane / dimension
    centers = (105, 295)[:caption_groups]
    for center in centers:
        draw.rectangle((center - 25, 175, center - 18, 192), outline="black", width=2)
        draw.rectangle((center - 10, 175, center + 10, 192), outline="black", width=2)
        draw.rectangle((center + 18, 175, center + 25, 192), outline="black", width=2)
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _raster_fixture(tmp_path: Path, *, caption_groups: int) -> _RasterFixture:
    image_png = _diagram_png(caption_groups=caption_groups)
    pdf_path = tmp_path / f"raster-{caption_groups}.pdf"
    image_bbox = (100.0, 80.0, 500.0, 280.0)
    source_bbox = (90.0, 70.0, 510.0, 310.0)
    with fitz.open() as document:
        page = document.new_page(width=600, height=400)
        page.insert_image(fitz.Rect(image_bbox), stream=image_png)
        page.insert_text((110, 300), "BODY OUTSIDE IMAGE", fontsize=12)
        document.save(pdf_path)
    return _RasterFixture(pdf_path, image_png, image_bbox, source_bbox)


def _item(bbox: tuple[float, float, float, float], source_text: str = "") -> DetectedItem:
    return DetectedItem(page_number=1, item_number=7, column=0, bbox=bbox, source_text=source_text)


def _metadata(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_same(left: Image.Image, right: Image.Image) -> None:
    assert ImageChops.difference(left, right).getbbox() is None


def test_object_crop_excludes_page_body_independently_from_raster_caption(tmp_path: Path) -> None:
    # Given: body text is inside the requested crop but outside the embedded figure object.
    fixture = _raster_fixture(tmp_path, caption_groups=2)

    # When: the figure is cropped from PDF object evidence.
    item = _item(fixture.source_bbox, _PANEL_SOURCE_TEXT)
    artifact = crop_region(fixture.pdf_path, item, fixture.source_bbox, tmp_path, "figure")
    metadata = _metadata(artifact.provenance_path)

    # Then: the final crop is the exact image object and records the excluded body span.
    assert metadata["source_bbox"] == list(fixture.source_bbox)
    assert metadata["bbox"] == list(fixture.image_bbox)
    assert metadata["excluded_body_spans"] == [
        {"text": "BODY OUTSIDE IMAGE", "bbox": [110.0, 287.1, 242.696, 303.588]},
    ]
    assert Image.open(artifact.image_path).size == (400, 200)


def test_safe_caption_removal_preserves_q_planes_axes_units_arrows_dashes_and_dimensions(
    tmp_path: Path,
) -> None:
    # Given: two isolated caption groups sit below protected lower diagram ink.
    fixture = _raster_fixture(tmp_path, caption_groups=2)

    # When: raster caption evidence is removed.
    item = _item(fixture.source_bbox, _PANEL_SOURCE_TEXT)
    artifact = crop_region(fixture.pdf_path, item, fixture.source_bbox, tmp_path, "figure")
    metadata = _metadata(artifact.provenance_path)
    source = Image.open(BytesIO(fixture.image_png)).convert("L")
    improved = Image.open(artifact.image_path).convert("L")

    # Then: exactly the two caption boxes change; every protected pixel remains byte-identical.
    captions = metadata["caption_candidates"]
    assert metadata["caption_detection_source"] == "raster_geometry"
    assert metadata["manual_review_required"] is False
    assert [caption["text"] for caption in captions] == ["(\uac00)", "(\ub098)"]
    expected = source.copy()
    expected_draw = ImageDraw.Draw(expected)
    for caption in captions:
        assert caption["excluded"] is True
        expected_draw.rectangle(tuple(caption["pixel_bbox"]), fill="white")
    _assert_same(expected, improved)
    _assert_same(source.crop((0, 0, 400, 160)), improved.crop((0, 0, 400, 160)))


def test_object_layout_evidence_distinguishes_horizontal_and_vertical_panels(tmp_path: Path) -> None:
    # Given: the same two PDF image objects are arranged along different axes.
    panel = Image.new("L", (80, 60), "white")
    ImageDraw.Draw(panel).rectangle((5, 5, 74, 54), outline="black", width=3)
    stream = BytesIO()
    panel.save(stream, format="PNG")
    panel_png = stream.getvalue()
    arrangements = {
        "horizontal": ((80.0, 80.0, 160.0, 140.0), (240.0, 80.0, 320.0, 140.0)),
        "vertical": ((80.0, 80.0, 160.0, 140.0), (80.0, 220.0, 160.0, 280.0)),
    }

    for expected_axis, boxes in arrangements.items():
        pdf_path = tmp_path / f"{expected_axis}.pdf"
        with fitz.open() as document:
            page = document.new_page(width=400, height=360)
            for box in boxes:
                page.insert_image(fitz.Rect(box), stream=panel_png)
            document.save(pdf_path)
        source_bbox = tuple(fitz.Rect(boxes[0]) | fitz.Rect(boxes[1]))

        # When: the object cluster is cropped.
        artifact = crop_region(pdf_path, _item(source_bbox), source_bbox, tmp_path / expected_axis, "figure")
        metadata = _metadata(artifact.provenance_path)

        # Then: source object coordinates prove the arrangement and reading-order panels.
        assert metadata["layout_axis"] == expected_axis
        assert metadata["panel_bboxes"] == [list(box) for box in boxes]
        assert metadata["image_count"] == 2


def test_ambiguous_single_scanned_caption_is_preserved_for_manual_review(tmp_path: Path) -> None:
    # Given: a caption-like isolated bottom row has only one group, so geometry cannot prove it is a caption.
    fixture = _raster_fixture(tmp_path, caption_groups=1)

    # When: object segmentation evaluates the scan.
    artifact = crop_region(fixture.pdf_path, _item(fixture.source_bbox), fixture.source_bbox, tmp_path, "figure")
    metadata = _metadata(artifact.provenance_path)

    # Then: no destructive masking occurs and the unsafe case is explicit.
    assert metadata["manual_review_required"] is True
    assert metadata["review_reasons"] == ["ambiguous_raster_caption"]
    assert metadata["caption_candidates"][0]["excluded"] is False
    _assert_same(
        Image.open(BytesIO(fixture.image_png)).convert("L"),
        Image.open(artifact.image_path).convert("L"),
    )


def test_equal_raster_groups_without_expected_panel_labels_are_manual_review(tmp_path: Path) -> None:
    # Given: geometry looks like two captions but source text provides no semantic panel-label set.
    fixture = _raster_fixture(tmp_path, caption_groups=2)

    # When: segmentation has no independent text evidence.
    artifact = crop_region(fixture.pdf_path, _item(fixture.source_bbox), fixture.source_bbox, tmp_path, "figure")
    metadata = _metadata(artifact.provenance_path)

    # Then: geometry alone cannot authorize destructive masking.
    assert metadata["manual_review_required"] is True
    assert metadata["review_reasons"] == ["ambiguous_raster_caption"]
    assert all(caption["excluded"] is False for caption in metadata["caption_candidates"])
    _assert_same(
        Image.open(BytesIO(fixture.image_png)).convert("L"),
        Image.open(artifact.image_path).convert("L"),
    )


def test_real_items_remove_only_caption_pixels_and_keep_final_panels_captionless(tmp_path: Path) -> None:
    # Given: real items contain two or three raster captions below semantic diagram ink.
    items = {item.item_number: item for item in detect_items(_REAL_SOURCE).items}
    expected_labels = {
        3: ["(가)", "(나)", "(다)"],
        7: ["(가)", "(나)"],
        8: ["(가)", "(나)"],
        9: ["(가)", "(나)"],
    }

    with fitz.open(_REAL_SOURCE) as document:
        for number, labels in expected_labels.items():
            page = document[items[number].page_number - 1]
            raw_info = next(
                info for info in page.get_image_info(xrefs=True)
                if not (fitz.Rect(info["bbox"]) & fitz.Rect(items[number].bbox)).is_empty
            )
            source = Image.open(BytesIO(document.extract_image(raw_info["xref"])["image"])).convert("L")

            # When: the real draft creates source and routed panel assets.
            draft = build_editable_draft(
                _REAL_SOURCE, items[number], tmp_path / f"item-{number}", layout_style=LayoutStyle.SUNEUNG,
            )
            metadata = _metadata(draft.figure_asset.provenance_path)
            improved = Image.open(draft.figure_asset.image_path).convert("L")

            # Then: the full asset differs only inside proven raster caption boxes.
            assert metadata["caption_detection_source"] == "raster_geometry"
            assert metadata["layout_axis"] == "horizontal"
            assert metadata["manual_review_required"] is False
            assert [caption["text"] for caption in metadata["caption_candidates"]] == labels
            expected = source.copy()
            expected_draw = ImageDraw.Draw(expected)
            for caption in metadata["caption_candidates"]:
                expected_draw.rectangle(tuple(caption["pixel_bbox"]), fill="white")
            _assert_same(expected, improved)
            panel_metadata = tuple(_metadata(asset.provenance_path) for asset in draft.figure_assets)
            assert all(panel["caption_in_image"] is False for panel in panel_metadata)
            assert [panel["caption_text"] for panel in panel_metadata] == labels
            assert all(panel["source_kind"] == "raster" for panel in panel_metadata)
            pdf_width = metadata["bbox"][2] - metadata["bbox"][0]
            pdf_height = metadata["bbox"][3] - metadata["bbox"][1]
            for asset, panel, caption in zip(
                draft.figure_assets, panel_metadata, metadata["caption_candidates"],
            ):
                assert panel["bbox"][3] <= caption["bbox"][1]
                pixel_box = (
                    round((panel["bbox"][0] - metadata["bbox"][0]) * improved.width / pdf_width),
                    round((panel["bbox"][1] - metadata["bbox"][1]) * improved.height / pdf_height),
                    round((panel["bbox"][2] - metadata["bbox"][0]) * improved.width / pdf_width),
                    round((panel["bbox"][3] - metadata["bbox"][1]) * improved.height / pdf_height),
                )
                with Image.open(asset.image_path) as opened:
                    routed = opened.convert("L")
                _assert_same(improved.crop(pixel_box), routed)


def test_real_item_9_q_and_horizontal_plane_pixels_are_byte_preserved(tmp_path: Path) -> None:
    # Given: q and horizontal-plane labels sit immediately above item 9's captions.
    item = next(item for item in detect_items(_REAL_SOURCE).items if item.item_number == 9)
    with fitz.open(_REAL_SOURCE) as document:
        page = document[item.page_number - 1]
        raw_info = next(
            info for info in page.get_image_info(xrefs=True)
            if not (fitz.Rect(info["bbox"]) & fitz.Rect(item.bbox)).is_empty
        )
        source = Image.open(BytesIO(document.extract_image(raw_info["xref"])["image"])).convert("L")

    # When: caption segmentation runs on the real source image.
    draft = build_editable_draft(_REAL_SOURCE, item, tmp_path, layout_style=LayoutStyle.SUNEUNG)
    improved = Image.open(draft.figure_asset.image_path).convert("L")

    # Then: both q/plane regions still contain ink and are byte-identical.
    protected_regions = ((110, 555, 710, 760), (1110, 555, 1715, 760))
    for box in protected_regions:
        source_region = source.crop(box)
        improved_region = improved.crop(box)
        assert source_region.getextrema()[0] < 64
        _assert_same(source_region, improved_region)


def test_real_items_15_and_16_use_exact_object_bbox_and_exclude_body_spans(tmp_path: Path) -> None:
    # Given: the former 6-point crop padding overlaps body text on items 15 and 16.
    items = {item.item_number: item for item in detect_items(_REAL_SOURCE).items}
    expected = {
        15: [601.56, 550.261, 754.56, 580.861],
        16: [651.12, 836.221, 753.12, 912.421],
    }

    for number in (15, 16):
        # When: the real item is extracted from its image object.
        draft = build_editable_draft(
            _REAL_SOURCE, items[number], tmp_path / f"item-{number}", layout_style=LayoutStyle.SUNEUNG,
        )
        metadata = _metadata(draft.figure_asset.provenance_path)

        # Then: padding-only prose is evidence, not image content.
        assert metadata["bbox"] == expected[number]
        assert metadata["source_bbox"] != metadata["bbox"]
        assert metadata["excluded_body_spans"]
        assert metadata["caption_detection_source"] == "none"
        assert _metadata(draft.figure_assets[0].provenance_path)["source_kind"] == "raster"
