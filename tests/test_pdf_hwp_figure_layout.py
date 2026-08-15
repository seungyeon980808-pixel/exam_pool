from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import (
    pdf_hwp_figure_routing as routing,
    pdf_hwp_pipeline_models as models,
    pdf_hwp_question_structure as question_structure,
)
from tests.pdf_hwp_figure_contract_support import SourceArtifactSpec, source_artifact


def test_route_figure_uses_large_pair_when_one_horizontal_panel_is_large(tmp_path: Path) -> None:
    # Given: item 13's measured shape, with one panel wider than the small-photo slot.
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name="mixed-size-two",
        panel_bboxes=((0.0, 0.0, 187.755, 64.281), (187.755, 0.0, 298.8, 64.281)),
        captions=(), drawing_count=2, image_count=0,
    ))

    # When: the two authoritative panels are routed by their measured geometry.
    routed = routing.route_figure("두 대상을 비교한다.", source)

    # Then: asset count alone cannot force the mixed-size pair into TWO_SMALL.
    assert routed.layout is routing.FigureLayout.TWO_LARGE


@pytest.mark.parametrize(
    ("total_width", "expected_layout", "expected_readable"),
    [
        (129.82, routing.FigureLayout.TWO_SMALL, True),
        (129.84, routing.FigureLayout.TWO_LARGE, False),
    ],
)
def test_route_figure_uses_registered_cell_scale_boundary_for_small_pair(
    tmp_path: Path,
    total_width: float,
    expected_layout: routing.FigureLayout,
    expected_readable: bool,
) -> None:
    # Given: equal horizontal panels straddling the final14 cell's 50% contain-scale limit.
    split = total_width / 2
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name=f"combined-{total_width}",
        panel_bboxes=((0.0, 0.0, split, 45.0), (split, 0.0, total_width, 45.0)),
        captions=(), drawing_count=2, image_count=0,
    ))

    # When: routing measures both panels and their aggregate width.
    routed = routing.route_figure("두 대상을 비교한다.", source)

    # Then: the boundary is inclusive and exposed in final decision metadata.
    assert routed.layout is expected_layout
    assert routed.layout_metadata.combined_width_points == pytest.approx(total_width)
    assert routed.layout_metadata.small_pair_readable is expected_readable
    assert routed.layout_metadata.minimum_projected_scale is not None
    assert routed.layout_metadata.minimum_projected_scale >= 0.5


def test_route_figure_uses_vertical_pair_for_stacked_panels(tmp_path: Path) -> None:
    # Given: two small authoritative panels stacked in reading order.
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name="vertical-pair",
        panel_bboxes=((0.0, 0.0, 100.0, 90.0), (0.0, 110.0, 100.0, 200.0)),
        captions=(), drawing_count=2, image_count=0,
    ))

    # When: the pair is routed.
    routed = routing.route_figure("두 대상을 비교한다.", source)

    # Then: arrangement wins over the small-width classification.
    assert routed.layout is routing.FigureLayout.TWO_VERTICAL
    assert routed.layout_metadata.arrangement is models.FigureArrangement.VERTICAL


def test_route_figure_keeps_unlabeled_multi_object_material_as_one_captionless_composite(
    tmp_path: Path,
) -> None:
    # Given: item 14-like component images without semantic panel labels or captions.
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name="unlabeled-material",
        panel_bboxes=((0.0, 10.0, 132.0, 82.0), (152.0, 0.0, 257.0, 87.0)),
        captions=(), drawing_count=0, image_count=2, component_count=3,
    ))

    # When: routing reconciles object geometry with a passage describing one material.
    routed = routing.route_figure("그림은 실험 결과를 나타낸 것이다.", source)

    # Then: component objects do not invent a two-panel question or a ghost caption slot.
    assert routed.layout is routing.FigureLayout.ONE_LARGE
    assert len(routed.figure_assets) == 1
    assert routed.figure_assets[0].metadata.panel_mode is models.PanelMode.SINGLE
    assert routed.figure_assets[0].metadata.caption_in_image is False
    assert routed.manual_review_required is False


def test_route_figure_preserves_single_captionless_small_figure(tmp_path: Path) -> None:
    # Given: one compact source figure with no caption candidate.
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name="single-captionless",
        panel_bboxes=((0.0, 0.0, 120.0, 100.0),),
        captions=(), drawing_count=1, image_count=0,
    ))

    # When: the single-figure path is routed.
    routed = routing.route_figure("그림을 나타낸 것이다.", source)

    # Then: the fix does not add a caption or change the established single layout.
    assert routed.layout is routing.FigureLayout.ONE_SMALL
    assert routed.figure_assets[0].metadata.caption_in_image is False


def test_route_figure_does_not_require_panel_caption_for_single_raster_figure(
    tmp_path: Path,
) -> None:
    # Given: q17-like raster evidence contains a blank caption candidate from OCR ambiguity.
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name="single-raster-blank-caption",
        panel_bboxes=((0.0, 0.0, 180.0, 100.0),),
        captions=({"text": "", "bbox": (70.0, 90.0, 90.0, 100.0)},),
        drawing_count=0, image_count=1,
    ))
    metadata = json.loads(source.provenance_path.read_text(encoding="utf-8"))
    metadata["review_reasons"] = ["ambiguous_raster_caption"]
    metadata["manual_review_required"] = True
    source.provenance_path.write_text(json.dumps(metadata), encoding="utf-8")

    # When: the single prompt figure is routed without any panel-caption slots.
    routed = routing.route_figure("그림을 보고 옳은 것을 고른다.", source)

    # Then: caption ambiguity cannot block a semantically captionless single figure.
    assert routed.layout is routing.FigureLayout.ONE_LARGE
    assert routed.manual_review_required is False
    assert routed.figure_assets[0].metadata.caption_in_image is False


@pytest.mark.parametrize(
    ("layout", "ask", "expected_template"),
    [
        (routing.FigureLayout.TWO_LARGE, "옳은 것은?", "수능정답2대사진5선지"),
        (routing.FigureLayout.TWO_VERTICAL, "옳은 것은?", "수능정답상하사진5선지"),
        (
            routing.FigureLayout.TWO_LARGE,
            "옳은 것은? <보 기> ㄱ. 첫째 ㄴ. 둘째 ㄷ. 셋째",
            "수능합답2대사진5선지",
        ),
        (
            routing.FigureLayout.TWO_VERTICAL,
            "옳은 것은? <보 기> ㄱ. 첫째 ㄴ. 둘째 ㄷ. 셋째",
            "수능합답상하사진5선지",
        ),
    ],
)
def test_palette_question_maps_pair_layout_to_complete_choice_template(
    layout: routing.FigureLayout,
    ask: str,
    expected_template: str,
) -> None:
    # Given: a final pair layout and either direct or multiple-statement answer structure.
    # When: the machine-consumed palette question is built.
    question = question_structure.palette_question("자료", ask, "a.png,b.png", layout)

    # Then: routing selects a full template that retains all five answer choices.
    assert question["style_meta"]["palette_template"] == expected_template


def test_palette_question_maps_three_panel_hapdap_to_editable_caption_template() -> None:
    # Given: three proven captionless assets and a hapdap answer structure.
    # When: the machine-consumed palette question selects its full template.
    question = question_structure.palette_question(
        "자료", "옳은 것은? <보 기> ㄱ. 첫째 ㄴ. 둘째 ㄷ. 셋째",
        "a.png,b.png,c.png", routing.FigureLayout.THREE_SMALL,
    )

    # Then: the template contract includes editable panel captions, not a stale no-caption label.
    assert question["style_meta"]["palette_template"] == "수능합답3소사진5선지"
