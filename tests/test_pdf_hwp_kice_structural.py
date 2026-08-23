from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import fitz
import pytest
from PIL import Image, ImageDraw

from app.pdf_hwp_kice_structural import (
    FigurePlacement,
    KiceFigureDiagnostic,
    KiceFigureExpectation,
    KiceFigureIssue,
    KiceStructuralRequest,
    KiceStructuralResult,
    PreparedVisualKind,
    inspect_kice_figure_structure,
)
from app.pdf_hwp_kice_profile_verifier import KiceStructuralImageOwnershipVerifier
from app.pdf_hwp_pipeline_models import (
    DetectedItem,
    DisplaySize,
    FigureArrangement,
    FigureAsset,
    FigureAssetMetadata,
    PanelMode,
    ConversionUnit,
    LayoutStyle,
)
from app.pdf_hwp_roundtrip_generated_detection import detect_generated_items
from app.pdf_hwp_roundtrip_structure import parse_prepared_structure
from app.pdf_hwp_roundtrip_unit_store import PreparedUnitRecord


FINAL_NAMESPACE = Path(
    "data/pdf_hwp/roundtrip_harness/approved-first-run/namespaces/"
    "5c2ea3441d9aa4338fc9/sources"
)


def _pattern(path: Path, seed: int) -> None:
    image = Image.new("RGB", (80, 60), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((5 + seed, 5, 35 + seed, 45), fill=(20 * seed, 40, 180))
    draw.line((0, 55 - seed, 79, 10 + seed), fill="black", width=3)
    image.save(path)


def _asset(path: Path, item: int, panel: tuple[int, int]) -> FigureAsset:
    index, count = panel
    return FigureAsset(path, FigureAssetMetadata(
        source_pdf=path.with_suffix(".pdf"), page_number=1, item_number=item,
        image_bbox=(0.0, 0.0, 80.0, 60.0), caption_text="", caption_bbox=None,
        asset_count=count, panel_index=index,
        panel_mode=PanelMode.SINGLE if count == 1 else PanelMode.SEPARATE,
        arrangement=FigureArrangement.COMPOSITE if count == 1 else FigureArrangement.HORIZONTAL,
        display_size=DisplaySize.SMALL, dpi=300, width_px=80, height_px=60,
        asset_hash=hashlib.sha256(path.read_bytes()).hexdigest(), confidence=1.0,
    ))


def _pdf(path: Path, images: tuple[tuple[Path, fitz.Rect], ...], *, table: bool = False) -> None:
    with fitz.open() as document:
        page = document.new_page(width=500, height=700)
        page.insert_text((40, 60), "1. stem")
        page.insert_text((40, 300), "ask?")
        page.insert_text((40, 650), "2. next")
        for image, rect in images:
            page.insert_image(rect, filename=str(image))
        if table:
            for x in (80, 140, 200):
                page.draw_line((x, 120), (x, 220))
            for y in (120, 170, 220):
                page.draw_line((80, y), (200, y))
        document.save(path)


def _item(number: int, bbox: tuple[float, float, float, float]) -> DetectedItem:
    return DetectedItem(1, number, 0, bbox, f"{number}. stem ask?")


def _codes(result: KiceStructuralResult) -> tuple[KiceFigureIssue, ...]:
    return tuple(issue.code for issue in result.issues)


def _fixture_assets(root: Path, item_number: int) -> tuple[FigureAsset, ...]:
    payload = json.loads((root / "prepared-units.json").read_text(encoding="utf-8"))
    unit = next(value for value in payload["units"] if value["item_number"] == item_number)
    return tuple(FigureAsset(
        Path(value["image_path"]), FigureAssetMetadata.model_validate(value["metadata"]),
    ) for value in unit["figure_assets"])


def test_no_figure_and_text_only_hapdap_have_no_visual_issue(tmp_path: Path) -> None:
    generated = tmp_path / "none.pdf"
    _pdf(generated, ())
    request = KiceStructuralRequest(
        generated, (_item(1, (20, 20, 480, 340)),),
        (KiceFigureExpectation(1, (), PreparedVisualKind.FIGURE, FigurePlacement.NONE),),
    )

    result = inspect_kice_figure_structure(request)

    assert result.issues == ()
    assert result.items[0].observed_count == 0


def test_one_figure_between_stem_and_ask_is_observed(tmp_path: Path) -> None:
    image = tmp_path / "one.png"
    _pattern(image, 1)
    generated = tmp_path / "one.pdf"
    _pdf(generated, ((image, fitz.Rect(100, 100, 180, 160)),))
    request = KiceStructuralRequest(
        generated, (_item(1, (20, 20, 480, 340)),),
        (KiceFigureExpectation(1, (_asset(image, 1, (1, 1)),)),),
    )

    result = inspect_kice_figure_structure(request)

    assert result.issues == ()
    assert result.items[0].placement is FigurePlacement.BETWEEN_STEM_AND_ASK


def test_two_panels_reversed_emit_stable_order_issue(tmp_path: Path) -> None:
    left, right = tmp_path / "left.png", tmp_path / "right.png"
    _pattern(left, 1)
    _pattern(right, 2)
    generated = tmp_path / "reversed.pdf"
    _pdf(generated, (
        (right, fitz.Rect(80, 120, 160, 180)),
        (left, fitz.Rect(180, 120, 260, 180)),
    ))
    assets = (_asset(left, 1, (1, 2)), _asset(right, 1, (2, 2)))

    result = inspect_kice_figure_structure(KiceStructuralRequest(
        generated, (_item(1, (20, 20, 480, 340)),), (KiceFigureExpectation(1, assets),),
    ))

    assert _codes(result) == (KiceFigureIssue.PANEL_ORDER_MISMATCH,)
    assert tuple(value.code for value in result.diagnostics) == (
        KiceFigureDiagnostic.SOURCE_CROP_UNOBSERVED,
        KiceFigureDiagnostic.SOURCE_CROP_UNOBSERVED,
    )


def test_three_panels_are_counted_and_ordered(tmp_path: Path) -> None:
    paths = tuple(tmp_path / f"panel-{index}.png" for index in range(1, 4))
    for index, path in enumerate(paths, 1):
        _pattern(path, index)
    generated = tmp_path / "three.pdf"
    _pdf(generated, tuple(
        (path, fitz.Rect(50 + index * 100, 120, 130 + index * 100, 180))
        for index, path in enumerate(paths)
    ))
    assets = tuple(_asset(path, 1, (index, 3)) for index, path in enumerate(paths, 1))

    result = inspect_kice_figure_structure(KiceStructuralRequest(
        generated, (_item(1, (20, 20, 480, 340)),), (KiceFigureExpectation(1, assets),),
    ))

    assert result.issues == ()
    assert result.items[0].observed_count == 3


def test_small_render_and_cross_item_spill_are_blocking(tmp_path: Path) -> None:
    image = tmp_path / "small.png"
    _pattern(image, 1)
    generated = tmp_path / "spill.pdf"
    _pdf(generated, ((image, fitz.Rect(100, 310, 140, 340)),))
    items = (_item(1, (20, 20, 480, 330)), _item(2, (20, 330, 480, 680)))

    result = inspect_kice_figure_structure(KiceStructuralRequest(
        generated, items, (KiceFigureExpectation(
            1, (_asset(image, 1, (1, 1)),), placement=FigurePlacement.AFTER_ASK,
        ),),
    ))

    assert set(_codes(result)) == {
        KiceFigureIssue.SCALE_UNREADABLE,
        KiceFigureIssue.CROSS_ITEM_SPILL,
    }


def test_profile_adapter_exposes_spill_and_scale_codes(tmp_path: Path) -> None:
    image = tmp_path / "small.png"
    _pattern(image, 1)
    asset = _asset(image, 1, (1, 1))
    generated = tmp_path / "spill.pdf"
    _pdf(generated, ((image, fitz.Rect(100, 310, 140, 340)),))
    markdown = (
        "\\수능정답1대사진5선지\\\n1\nstem\n\\small\\\nask?\n"
        "①\n②\n③\n④\n⑤"
    )
    unit = ConversionUnit(1, markdown, (asset,))
    record = PreparedUnitRecord(
        unit, "a" * 64,
        parse_prepared_structure(unit, 1, (20, 20, 480, 330), LayoutStyle.SUNEUNG),
    )

    result = KiceStructuralImageOwnershipVerifier(
        generated,
        (_item(1, (20, 20, 480, 330)), _item(2, (20, 330, 480, 680))),
    ).verify((record,))

    assert result.passed is False
    assert {issue.code for issue in result.issues} >= {
        KiceFigureIssue.CROSS_ITEM_SPILL.value,
        KiceFigureIssue.SCALE_UNREADABLE.value,
    }


def test_profile_adapter_does_not_treat_small_display_size_as_before_stem(
    tmp_path: Path,
) -> None:
    image = tmp_path / "mid-stem.png"
    _pattern(image, 1)
    source = image.with_suffix(".pdf")
    generated = tmp_path / "generated.pdf"
    figure_bbox = fitz.Rect(100, 100, 180, 160)
    _pdf(source, ((image, figure_bbox),))
    _pdf(generated, ((image, figure_bbox),))
    asset = _asset(image, 12, (1, 1))
    asset = FigureAsset(asset.image_path, asset.metadata.model_copy(update={
        "image_bbox": tuple(figure_bbox),
    }))
    markdown = (
        "\\수능정답1대사진5선지\\\n12\nstem before figure\n\\small\\\n"
        "stem continues, then ask?\n①\n②\n③\n④\n⑤"
    )
    unit = ConversionUnit(12, markdown, (asset,))
    item_bbox = (20.0, 20.0, 480.0, 340.0)
    record = PreparedUnitRecord(
        unit, "a" * 64,
        parse_prepared_structure(unit, 1, item_bbox, LayoutStyle.SUNEUNG),
    )

    result = KiceStructuralImageOwnershipVerifier(
        generated, (_item(12, item_bbox),),
    ).verify((record,))

    assert not any(
        issue.code == KiceFigureIssue.PLACEMENT_MISMATCH.value
        for issue in result.issues
    )


def test_figure_above_all_stem_text_is_before_ask(tmp_path: Path) -> None:
    image = tmp_path / "above.png"
    _pattern(image, 1)
    generated = tmp_path / "above.pdf"
    _pdf(generated, ((image, fitz.Rect(100, 10, 180, 40)),))

    result = inspect_kice_figure_structure(KiceStructuralRequest(
        generated, (_item(1, (20, 0, 480, 340)),),
        (KiceFigureExpectation(
            1, (_asset(image, 1, (1, 1)),), placement=FigurePlacement.BEFORE_ASK,
        ),),
    ))

    assert KiceFigureIssue.PLACEMENT_MISMATCH not in _codes(result)
    assert result.items[0].placement is FigurePlacement.BEFORE_ASK


def test_matching_figure_owned_by_another_item_is_reported(tmp_path: Path) -> None:
    image = tmp_path / "wrong-owner.png"
    _pattern(image, 2)
    generated = tmp_path / "wrong-owner.pdf"
    _pdf(generated, ((image, fitz.Rect(100, 400, 180, 460)),))
    items = (_item(1, (20, 20, 480, 330)), _item(2, (20, 330, 480, 680)))

    result = inspect_kice_figure_structure(KiceStructuralRequest(
        generated, items, (KiceFigureExpectation(1, (_asset(image, 1, (1, 1)),)),),
    ))

    assert KiceFigureIssue.FIGURE_OWNER_MISMATCH in _codes(result)


def test_vector_ruled_table_is_explicitly_unobserved(tmp_path: Path) -> None:
    prepared = tmp_path / "table.png"
    _pattern(prepared, 3)
    generated = tmp_path / "table.pdf"
    _pdf(generated, (), table=True)

    result = inspect_kice_figure_structure(KiceStructuralRequest(
        generated, (_item(1, (20, 20, 480, 340)),),
        (KiceFigureExpectation(
            1, (_asset(prepared, 1, (1, 1)),), PreparedVisualKind.TABLE,
        ),),
    ))

    assert result.issues == ()
    assert tuple(value.code for value in result.diagnostics) == (
        KiceFigureDiagnostic.VECTOR_OR_TABLE_UNOBSERVED,
    )


@dataclass(frozen=True, slots=True)
class _RealCase:
    pattern: str
    item: int
    count: int
    kind: PreparedVisualKind


@pytest.mark.parametrize("case", (
    _RealCase("c2_2023_06-*", 1, 0, PreparedVisualKind.FIGURE),
    _RealCase("c1_2024_06-*", 1, 1, PreparedVisualKind.FIGURE),
    _RealCase("p2_2026_11-*", 2, 2, PreparedVisualKind.FIGURE),
    _RealCase("e2_2023_11-*", 8, 3, PreparedVisualKind.FIGURE),
    _RealCase("b1_2024_09-*", 7, 1, PreparedVisualKind.TABLE),
    _RealCase("b1_2024_09-*", 1, 0, PreparedVisualKind.FIGURE),
))
def test_approved_real_fixture_is_deterministic(case: _RealCase) -> None:
    roots = tuple(FINAL_NAMESPACE.glob(case.pattern))
    if len(roots) != 1:
        pytest.skip(f"fixture unavailable: {case.pattern}")
    assets = _fixture_assets(roots[0], case.item)
    item = next(value for value in detect_generated_items(
        roots[0] / "conversion" / "converted.pdf",
    ).items if value.item_number == case.item)
    expectation = KiceFigureExpectation(
        case.item, assets, case.kind,
        FigurePlacement.NONE if case.count == 0 else FigurePlacement.BETWEEN_STEM_AND_ASK,
    )
    request = KiceStructuralRequest(
        roots[0] / "conversion" / "converted.pdf", (item,), (expectation,),
    )

    first = inspect_kice_figure_structure(request)
    second = inspect_kice_figure_structure(request)

    assert len(assets) == case.count
    assert first == second
    assert first.items[0].expected_count == case.count
    assert KiceFigureIssue.SOURCE_CROP_CONTAMINATION not in _codes(first)
    if case.count == 0:
        assert first.issues == ()


@pytest.mark.parametrize(("item_number", "expected_bbox"), (
    (2, (81.78, 487.245, 362.40, 627.405)),
    (5, (430.44, 807.045, 758.58, 909.361)),
    (18, (81.78, 674.445, 397.80, 888.301)),
))
def test_real_mixed_region_crop_with_source_prose_is_blocking(
    item_number: int,
    expected_bbox: tuple[float, float, float, float],
) -> None:
    roots = tuple(FINAL_NAMESPACE.glob("p1_2019_11-*"))
    if len(roots) != 1:
        pytest.skip("fixture unavailable: p1_2019_11")
    assets = _fixture_assets(roots[0], item_number)
    assert tuple(assets[0].metadata.image_bbox) == pytest.approx(expected_bbox)
    item = next(value for value in detect_generated_items(
        roots[0] / "conversion" / "converted.pdf",
    ).items if value.item_number == item_number)

    result = inspect_kice_figure_structure(KiceStructuralRequest(
        roots[0] / "conversion" / "converted.pdf", (item,),
        (KiceFigureExpectation(item_number, assets),),
    ))

    assert KiceFigureIssue.SOURCE_CROP_CONTAMINATION in _codes(result)
