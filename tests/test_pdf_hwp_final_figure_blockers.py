from __future__ import annotations

from pathlib import Path

import pytest

from app.pdf_hwp_final_figure_contract import final_figure_metadata_requires_review
from app.pdf_hwp_pipeline import detect_items
from app.pdf_hwp_pipeline_models import LayoutStyle
from app.pdf_hwp_pipeline_models import FigureLayout
from app.pdf_hwp_question_structure import reconcile_final_template
from app.pdf_hwp_roundtrip_units import PreparationResult, prepare_units


SOURCE_ROOT = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일"
B2_2022 = SOURCE_ROOT / "b2_2022_06.pdf"
B2_2024 = SOURCE_ROOT / "b2_2024_09.pdf"
E2_2025 = SOURCE_ROOT / "e2_2025_09.pdf"


def _prepare(source: Path, item_number: int, output_root: Path) -> PreparationResult:
    item = next(value for value in detect_items(source).items if value.item_number == item_number)
    return prepare_units(source, (item,), output_root, LayoutStyle.SUNEUNG)


@pytest.mark.skipif(not B2_2022.exists(), reason="real KICE source corpus is unavailable")
def test_real_b2_2022_q7_accepts_only_shallow_final_panel_boundary_overlap(
    tmp_path: Path,
) -> None:
    result = _prepare(B2_2022, 7, tmp_path / "b2-2022-q7")

    assert result.item_failures == ()
    assert len(result.prepared_units) == 1
    unit = result.prepared_units[0]
    assert unit.palette_markdown.splitlines()[0] == "\\수능합답2대사진5선지\\"
    assert len(unit.figure_assets) == 2
    assert [asset.metadata.caption_text for asset in unit.figure_assets] == ["(가)", "(나)"]
    assert all(asset.metadata.item_number == 7 for asset in unit.figure_assets)


@pytest.mark.skipif(not E2_2025.exists(), reason="real KICE source corpus is unavailable")
def test_real_e2_2025_q2_keeps_comparison_stem_figure_ask_and_five_choices(
    tmp_path: Path,
) -> None:
    result = _prepare(E2_2025, 2, tmp_path / "e2-2025-q2")

    assert result.item_failures == ()
    assert len(result.prepared_units) == 1
    unit = result.prepared_units[0]
    assert unit.palette_markdown.splitlines()[0] == "\\수능AI실제비교선지형\\"
    assert len(unit.figure_assets) == 1
    assert len(result.records) == 1
    structure = result.records[0].structure
    assert structure.ask == "A, B, C에 해당하는 것으로 가장 적절한 것은?"
    assert structure.choices == (
        "지균풍 경도풍 지상풍",
        "지균풍 지상풍 경도풍",
        "지상풍 경도풍 지균풍",
        "지상풍 지균풍 경도풍",
        "경도풍 지균풍 지상풍",
    )
    assert len(structure.asset_refs) == 1
    assert structure.asset_refs[0].owner_item_number == 2


@pytest.mark.skipif(not B2_2024.exists(), reason="real KICE source corpus is unavailable")
def test_real_b2_2024_q7_remains_a_clean_single_final_figure(tmp_path: Path) -> None:
    result = _prepare(B2_2024, 7, tmp_path / "b2-2024-q7")

    assert result.item_failures == ()
    assert len(result.prepared_units) == 1
    unit = result.prepared_units[0]
    assert len(unit.figure_assets) == 1
    assert unit.figure_assets[0].metadata.manual_review_required is False


def test_comparison_template_rejects_a_final_figure_token_in_choice_slots() -> None:
    token = "\\figure-1\\"
    markdown = "\n".join((
        "\\수능AI실제비교선지형\\",
        "2",
        "editable stem",
        "editable ask?",
        "A B C",
        token,
        "choice 2",
        "choice 3",
        "choice 4",
        "choice 5",
    ))

    assert reconcile_final_template(
        markdown,
        FigureLayout.ONE_LARGE,
        figure_tokens=(token,),
    ) is None


@pytest.mark.skipif(not B2_2022.exists(), reason="real KICE source corpus is unavailable")
def test_real_b2_2022_q7_exception_does_not_accept_deep_panel_overlap(
    tmp_path: Path,
) -> None:
    result = _prepare(B2_2022, 7, tmp_path / "b2-2022-deep-overlap")
    metadata = tuple(asset.metadata for asset in result.prepared_units[0].figure_assets)
    second = metadata[1]
    deep_overlap = (
        metadata[0],
        second.model_copy(update={
            "image_bbox": (
                metadata[0].image_bbox[2] - 20.0,
                second.image_bbox[1],
                second.image_bbox[2],
                second.image_bbox[3],
            ),
        }),
    )

    assert final_figure_metadata_requires_review(deep_overlap) is True
