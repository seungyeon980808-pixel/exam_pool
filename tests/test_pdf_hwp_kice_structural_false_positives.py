from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
from pathlib import Path

import fitz
import pytest
from PIL import Image, ImageDraw

from app.pdf_hwp_kice_profile_verifier import (
    KiceStructuralImageOwnershipVerifier,
)
from app.pdf_hwp_kice_source_placement import resolve_source_bbox
from app.pdf_hwp_kice_structural import (
    FigurePlacement,
    KiceFigureExpectation,
    KiceFigureIssue,
    KiceStructuralRequest,
    inspect_kice_figure_structure,
)
from app.pdf_hwp_kice_structural_geometry import (
    classify_figure_placement,
    is_zero_information_raster,
    scale_is_readable,
)
from app.pdf_hwp_pipeline_models import (
    DetectedItem,
    DisplaySize,
    FigureArrangement,
    FigureAsset,
    FigureAssetMetadata,
    PanelMode,
)
from app.pdf_hwp_roundtrip_generated_detection import detect_generated_items
from app.pdf_hwp_roundtrip_unit_store import load_prepared_units


OFFICIAL_NAMESPACE = Path(
    "data/pdf_hwp/roundtrip_harness/source-profiles/namespaces/"
    "b8eb33741056293a9a2e/sources"
)


@dataclass(frozen=True, slots=True)
class _OfficialCase:
    source: str
    item_number: int
    rejected_codes: frozenset[str]


@pytest.mark.parametrize("case", (
    _OfficialCase("p2_2026_11-*", 1, frozenset({"kice_structural_placement_mismatch"})),
    _OfficialCase("c2_2023_06-*", 14, frozenset({"kice_structural_placement_mismatch"})),
    _OfficialCase("e1_2024_06-*", 8, frozenset({"kice_structural_placement_mismatch"})),
    _OfficialCase("e1_2024_06-*", 9, frozenset({"kice_structural_placement_mismatch"})),
    _OfficialCase("p2_2013_11-*", 2, frozenset({"kice_structural_placement_mismatch"})),
    _OfficialCase("p2_2013_11-*", 10, frozenset({"kice_structural_scale_unreadable"})),
    _OfficialCase("p2_2013_11-*", 14, frozenset({"kice_structural_panel_count_mismatch"})),
    _OfficialCase("e2_2025_09-*", 2, frozenset({"kice_structural_panel_count_mismatch"})),
    _OfficialCase("e2_2025_09-*", 17, frozenset({"kice_structural_placement_mismatch"})),
))
def test_official_generated_figure_false_positive_is_rejected(case: _OfficialCase) -> None:
    roots = tuple(OFFICIAL_NAMESPACE.glob(case.source))
    if len(roots) != 1:
        pytest.skip(f"official fixture unavailable: {case.source}")
    prepared = load_prepared_units(roots[0] / "prepared-units.json")
    record = next(value for value in prepared.records
                  if value.structure.number == case.item_number)
    generated_pdf = roots[0] / "conversion" / "converted.pdf"
    item = next(value for value in detect_generated_items(generated_pdf).items
                if value.item_number == case.item_number)

    result = KiceStructuralImageOwnershipVerifier(generated_pdf, (item,)).verify((record,))

    assert case.rejected_codes.isdisjoint(issue.code for issue in result.issues)


def _pattern(path: Path, seed: int) -> None:
    image = Image.new("RGB", (80, 60), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((5 + seed, 5, 35 + seed, 45), fill=(20 * seed, 40, 180))
    image.save(path)


def _asset(path: Path, panel_index: int, bbox: tuple[float, float, float, float]
           ) -> FigureAsset:
    return FigureAsset(path, FigureAssetMetadata(
        source_pdf=path.with_suffix(".pdf"), page_number=1, item_number=1,
        image_bbox=bbox, caption_text="", caption_bbox=None,
        asset_count=2, panel_index=panel_index, panel_mode=PanelMode.SEPARATE,
        arrangement=FigureArrangement.COMPOSITE, display_size=DisplaySize.SMALL,
        dpi=300, width_px=80, height_px=60,
        asset_hash=hashlib.sha256(path.read_bytes()).hexdigest(), confidence=1.0,
    ))


def test_horizontal_composite_order_ignores_vertical_jitter(tmp_path: Path) -> None:
    left = tmp_path / "left.png"
    right = tmp_path / "right.png"
    _pattern(left, 1)
    _pattern(right, 2)
    generated = tmp_path / "generated.pdf"
    with fitz.open() as document:
        page = document.new_page(width=500, height=700)
        page.insert_text((40, 60), "1. stem")
        page.insert_text((40, 300), "ask?")
        page.insert_image(fitz.Rect(80, 121, 160, 181), filename=str(left))
        page.insert_image(fitz.Rect(180, 120, 260, 180), filename=str(right))
        document.save(generated)
    assets = (
        _asset(left, 1, (80, 120, 160, 180)),
        _asset(right, 2, (180, 121, 260, 181)),
    )
    item = DetectedItem(1, 1, 0, (20, 20, 480, 340), "1. stem ask?")

    result = inspect_kice_figure_structure(KiceStructuralRequest(
        generated, (item,), (KiceFigureExpectation(1, assets),),
    ))

    assert KiceFigureIssue.PANEL_ORDER_MISMATCH not in tuple(
        issue.code for issue in result.issues
    )


def test_horizontal_composite_reversal_remains_blocking(tmp_path: Path) -> None:
    left = tmp_path / "left.png"
    right = tmp_path / "right.png"
    _pattern(left, 1)
    _pattern(right, 2)
    generated = tmp_path / "reversed.pdf"
    with fitz.open() as document:
        page = document.new_page(width=500, height=700)
        page.insert_text((40, 60), "1. stem")
        page.insert_text((40, 300), "ask?")
        page.insert_image(fitz.Rect(80, 121, 160, 181), filename=str(right))
        page.insert_image(fitz.Rect(180, 120, 260, 180), filename=str(left))
        document.save(generated)
    assets = (
        _asset(left, 1, (80, 120, 160, 180)),
        _asset(right, 2, (180, 121, 260, 181)),
    )

    result = inspect_kice_figure_structure(KiceStructuralRequest(
        generated, (DetectedItem(1, 1, 0, (20, 20, 480, 340), "1. stem ask?"),),
        (KiceFigureExpectation(1, assets),),
    ))

    assert KiceFigureIssue.PANEL_ORDER_MISMATCH in tuple(
        issue.code for issue in result.issues
    )


def test_side_by_side_uses_full_question_flow(tmp_path: Path) -> None:
    image = tmp_path / "figure.png"
    _pattern(image, 1)
    generated = tmp_path / "side.pdf"
    with fitz.open() as document:
        page = document.new_page(width=500, height=700)
        page.insert_text((40, 60), "1. stem")
        page.insert_textbox(fitz.Rect(40, 90, 240, 170), "question flow\ncontinues ask?")
        page.insert_image(fitz.Rect(300, 90, 380, 150), filename=str(image))
        document.save(generated)
    item = DetectedItem(1, 1, 0, (20, 20, 480, 340), "1. stem question flow ask?")
    with fitz.open(generated) as document:
        placement = classify_figure_placement(
            document[0], item, ((300, 90, 380, 150),),
        )

    assert placement is FigurePlacement.SIDE_BY_SIDE


def test_side_by_side_uses_stem_line_when_ask_starts_below_image(tmp_path: Path) -> None:
    image = tmp_path / "figure.png"
    _pattern(image, 1)
    generated = tmp_path / "stem-side.pdf"
    with fitz.open() as document:
        page = document.new_page(width=500, height=700)
        page.insert_text((40, 60), "1. stem begins")
        page.insert_text((40, 100), "substantive stem line beside figure")
        page.insert_image(fitz.Rect(300, 80, 380, 150), filename=str(image))
        page.insert_text((40, 180), "ask?")
        document.save(generated)
    item = DetectedItem(1, 1, 0, (20, 20, 480, 220), "1. stem ask?")
    with fitz.open(generated) as document:
        placement = classify_figure_placement(
            document[0], item, ((300, 80, 380, 150),),
        )

    assert placement is FigurePlacement.SIDE_BY_SIDE


def test_figure_between_stem_and_ask_without_side_line_stays_between(tmp_path: Path) -> None:
    image = tmp_path / "figure.png"
    _pattern(image, 1)
    generated = tmp_path / "between.pdf"
    with fitz.open() as document:
        page = document.new_page(width=500, height=700)
        page.insert_text((40, 60), "1. stem line")
        page.insert_image(fitz.Rect(180, 90, 260, 150), filename=str(image))
        page.insert_text((40, 180), "ask?")
        document.save(generated)
    item = DetectedItem(1, 1, 0, (20, 20, 480, 220), "1. stem ask?")
    with fitz.open(generated) as document:
        placement = classify_figure_placement(
            document[0], item, ((180, 90, 260, 150),),
        )

    assert placement is FigurePlacement.BETWEEN_STEM_AND_ASK


def test_side_by_side_expected_but_figure_above_ask_blocks(tmp_path: Path) -> None:
    image = tmp_path / "figure.png"
    _pattern(image, 1)
    generated = tmp_path / "moved.pdf"
    with fitz.open() as document:
        page = document.new_page(width=500, height=700)
        page.insert_image(fitz.Rect(300, 10, 380, 70), filename=str(image))
        page.insert_text((40, 90), "1. stem")
        page.insert_text((40, 160), "ask?")
        document.save(generated)
    asset = FigureAsset(image, FigureAssetMetadata(
        source_pdf=image.with_suffix(".pdf"), page_number=1, item_number=1,
        image_bbox=(300, 90, 380, 150), caption_text="", caption_bbox=None,
        asset_count=1, panel_index=1, panel_mode=PanelMode.SINGLE,
        arrangement=FigureArrangement.COMPOSITE, display_size=DisplaySize.SMALL,
        dpi=300, width_px=80, height_px=60,
        asset_hash=hashlib.sha256(image.read_bytes()).hexdigest(), confidence=1.0,
    ))

    result = inspect_kice_figure_structure(KiceStructuralRequest(
        generated, (DetectedItem(1, 1, 0, (20, 0, 480, 340), "1. stem ask?"),),
        (KiceFigureExpectation(
            1, (asset,), placement=FigurePlacement.SIDE_BY_SIDE,
        ),),
    ))

    assert KiceFigureIssue.PLACEMENT_MISMATCH in tuple(
        issue.code for issue in result.issues
    )


@pytest.mark.parametrize(("shortfall", "readable"), ((0.04, True), (0.06, False)))
def test_scale_tolerance_is_physical_and_narrow(shortfall: float, readable: bool) -> None:
    source = (0.0, 0.0, 100.0, 100.0)
    generated = (0.0, 0.0, 70.0 - shortfall, 70.0)

    observed = scale_is_readable(source, generated, 0.70)

    assert observed is readable


def test_unmasked_near_white_raster_remains_observable() -> None:
    image = Image.new("RGB", (20, 20), (255, 255, 255))
    payload = BytesIO()
    image.save(payload, format="PNG")

    observed = is_zero_information_raster(payload.getvalue(), has_mask=False)

    assert observed is False


def test_source_provenance_replaces_local_crop_coordinates(tmp_path: Path) -> None:
    image = tmp_path / "page-1-item-4-figure-1.png"
    image.write_bytes(b"figure")
    image.with_suffix(".json").write_text(
        '{"page_number":1,"item_number":4,'
        '"source_bbox":[486.48,340.801,719.88,621.841]}',
        encoding="utf-8",
    )

    resolved = resolve_source_bbox(
        image.with_suffix(".json"), (0.0, 0.0, 211.752, 137.904), 1, 4,
        (428.56, 251.205, 834.0, 807.045),
    )

    assert resolved == (486.48, 340.801, 719.88, 621.841)


def test_local_crop_coordinates_are_not_used_as_source_page_bbox(tmp_path: Path) -> None:
    image = tmp_path / "figure.png"
    image.write_bytes(b"figure")

    resolved = resolve_source_bbox(
        image.with_suffix(".json"), (0.0, 0.0, 211.752, 137.904), 1, 4,
        (428.56, 251.205, 834.0, 807.045),
    )

    assert resolved is None
