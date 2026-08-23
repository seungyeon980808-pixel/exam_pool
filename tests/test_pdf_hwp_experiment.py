from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.export_palette import question_to_palette
from app.pdf_hwp_experiment import ExperimentTable, split_experiment_passage
from app.pdf_hwp_figure_routing import route_figure
from app.pdf_hwp_hwp_preflight import preflight_unit
from app.pdf_hwp_pipeline import LayoutStyle, build_editable_draft, detect_items
from app.pdf_hwp_pipeline_models import ConversionUnit, FigureAsset, FigureAssetMetadata
from app.pdf_hwp_question_structure import palette_question
from tests.pdf_hwp_figure_contract_support import SourceArtifactSpec, source_artifact


SOURCE = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일\p1_2025_09.pdf")
C1_2024 = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일\c1_2024_06.pdf")
B2_2022 = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일\b2_2022_06.pdf")


def test_experiment_table_markup_uses_explicit_empty_cells() -> None:
    table = ExperimentTable((0.0, 0.0, 30.0, 20.0), 2, 3, (
        ("(나)", "(다)", "(라)"),
        ("", "", ""),
    ))

    assert table.palette_markup().splitlines()[-1] == "-&-&-"


def test_experiment_passage_unwraps_outer_box_and_keeps_only_result_table() -> None:
    # Given: source recovery flattened one outer material box and one real result table.
    passage = "\n".join((
        "다음은 중화 적정 실험이다.",
        "\\표2*3\\",
        "식초&A&B",
        "질량&16w&15w",
        "\\표1*1\\",
        "[자료] ◦분자량은 60이다. [실험 과정] (가) 식초를 준비한다. "
        "[실험 결과] ◦부피를 측정한다.",
    ))

    # When: the experiment is split for the registered template's typed slots.
    parts = split_experiment_passage(passage)

    # Then: the 1x1 source border becomes body text, not a peer/nested generated table.
    assert parts.intro == "다음은 중화 적정 실험이다."
    assert parts.body.startswith("[자료]")
    assert "[실험 과정]" in parts.body
    assert "\\표1*1\\" not in parts.result_markup
    assert parts.result_markup == "\\표2*3\\\n식초&A&B\n질량&16w&15w"


def test_experiment_question_uses_bordered_template_and_keeps_table_markup() -> None:
    # Given: an experiment passage whose result is an editable two-column table.
    passage = "\n".join((
        "다음은 빛의 세기에 대한 실험이다.",
        "[실험 과정]",
        "(가) 센서로 빛의 세기를 측정한다.",
        "[실험 결과]",
        "\\표2*2\\",
        "거리&빛의 세기",
        "1&4",
    ))
    question = palette_question(
        passage,
        "이에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은? "
        "<보 기> ㄱ. 첫째이다. ㄴ. 둘째이다. ㄷ. 셋째이다.",
        "",
        None,
    )

    # When: the recovered question is serialized for the Suneung palette.
    markdown = question_to_palette(
        question,
        [
            {"ord": 1, "text": "ㄱ"},
            {"ord": 2, "text": "ㄴ"},
            {"ord": 3, "text": "ㄱ, ㄴ"},
            {"ord": 4, "text": "ㄴ, ㄷ"},
            {"ord": 5, "text": "ㄱ, ㄴ, ㄷ"},
        ],
        num=13,
        layout_style="suneung",
    )

    # Then: the experiment fragment owns the material box and the table stays structural.
    assert markdown.splitlines()[0] == "\\수능AI실제실험형\\"
    assert "\\표2*2\\" in markdown
    assert "거리&빛의 세기" in markdown


def test_experiment_question_recovers_ask_prefix_attached_to_result_table() -> None:
    # Given: PDF line recovery attached the first half of the question to the
    # experiment result while leaving only the final "것은?" in the ask block.
    passage = "\n".join((
        "다음은 회로에 대한 실험이다.",
        "[실험 과정]",
        "(가) 회로를 구성한다.",
        "[실험 결과]",
        "\\표2*2\\",
        "스위치&결과",
        "a&켜짐",
        "이에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른",
    ))

    # When: the question fields are reconstructed.
    question = palette_question(passage, "것은? [3점]", "", None)

    # Then: the complete question is outside the bordered material passage.
    assert "이에 대한" not in question["passage"]
    assert question["ask"].startswith("이에 대한 설명으로")
    assert question["ask"].endswith("것은?")
    assert question["default_points"] == 3


def test_experiment_step_labels_do_not_fabricate_multiple_figure_panels(tmp_path: Path) -> None:
    # Given: one real circuit figure and (가)/(나) labels that name experiment steps.
    source = source_artifact(tmp_path, SourceArtifactSpec(
        name="experiment-one-figure",
        panel_bboxes=((0.0, 0.0, 300.0, 300.0),),
        captions=(),
        drawing_count=0,
        image_count=1,
    ))
    passage = "[실험 과정]\n(가) 회로를 구성한다.\n(나) 스위치를 연결한다."

    # When: figure geometry is routed for HWP.
    routed = route_figure(passage, source)

    # Then: step labels do not demand nonexistent (가)/(나) figure captions.
    assert routed.manual_review_required is False
    assert len(routed.assets) == 1


def test_real_experiment_recovers_result_table_as_editable_hwp_structure(
    tmp_path: Path,
) -> None:
    if not SOURCE.is_file():
        pytest.skip("real 2025-09 source PDF is not available")
    # Given: the exact 2025-09 item whose process/result table was flattened.
    item = next(value for value in detect_items(SOURCE).items if value.item_number == 13)

    # When: the production PDF draft builder reconstructs the item.
    draft = build_editable_draft(
        SOURCE, item, tmp_path / "experiment-13", layout_style=LayoutStyle.SUNEUNG,
    )

    # Then: the bordered experiment template contains a real editable 5x3 table.
    assert draft.palette_markdown.splitlines()[0] == "\\수능AI실제실험형\\"
    assert "\\표5*3\\" in draft.palette_markdown
    assert "빛이 방출된 LED" in draft.palette_markdown
    ask_at = draft.palette_markdown.index("이에 대한")
    passage_end = draft.palette_markdown.rfind("}", 0, ask_at)
    assert passage_end >= 0
    assert not draft.palette_markdown[passage_end + 1:ask_at].strip()
    passage_block = draft.palette_markdown[:passage_end + 1]
    ask_block = draft.palette_markdown[ask_at:]
    assert "이에 대한" not in passage_block
    assert "이에 대한" in ask_block
    assert draft.palette_markdown.count("[3점]") == 1
    metadata = json.loads(draft.figure_assets[0].provenance_path.read_text(encoding="utf-8"))
    assert metadata["manual_review_required"] is False
    figure_assets = tuple(
        FigureAsset(
            asset.image_path,
            FigureAssetMetadata.model_validate_json(
                asset.provenance_path.read_text(encoding="utf-8"),
            ),
        )
        for asset in draft.figure_assets
    )
    preflighted = preflight_unit(
        ConversionUnit(13, draft.palette_markdown, figure_assets),
        LayoutStyle.SUNEUNG,
    )
    assert len(preflighted.figure_asset_hashes) == 1


def test_real_c1_q16_separates_material_body_from_editable_result_table(
    tmp_path: Path,
) -> None:
    if not C1_2024.is_file():
        pytest.skip("real 2024-06 chemistry source PDF is not available")
    # Given: q16 has one outer source border containing one actual 2x3 result table.
    item = next(value for value in detect_items(C1_2024).items if value.item_number == 16)

    # When: the production draft is rebuilt for the active experiment template.
    draft = build_editable_draft(
        C1_2024, item, tmp_path / "c1-q16", layout_style=LayoutStyle.SUNEUNG,
    )

    # Then: only the result table is serialized; the template supplies the outer border.
    markdown = draft.palette_markdown
    assert markdown.splitlines()[0] == "\\수능AI실제실험형\\"
    assert draft.figure_assets == ()
    assert "\\표1*1\\" not in markdown
    assert markdown.count("\\표2*3\\") == 1
    assert "식초&A&B" in markdown
    assert "\\수식{16w}&\\수식{15w}" in markdown
    assert markdown.index("[자료]") < markdown.index("\\표2*3\\")
    assert markdown.index("\\표2*3\\") < markdown.index("\\frac{x}{y}")
    for choice in (
        "\\frac{4d_{B}}{3d_{A}}", "\\frac{6d_{B}}{5d_{A}}",
        "\\frac{5d_{B}}{6d_{A}}", "\\frac{3d_{B}}{4d_{A}}",
        "\\frac{d_{B}}{2d_{A}}",
    ):
        assert choice in markdown


def test_real_b2_q16_boxed_report_does_not_gain_a_duplicate_raster(
    tmp_path: Path,
) -> None:
    if not B2_2022.is_file():
        pytest.skip("real 2022-06 biology source PDF is not available")
    item = next(value for value in detect_items(B2_2022).items if value.item_number == 16)

    draft = build_editable_draft(
        B2_2022, item, tmp_path / "b2-q16", layout_style=LayoutStyle.SUNEUNG,
    )

    assert draft.figure_assets == ()
    assert draft.palette_markdown.count("\\표1*1\\") == 1
    assert "<보기>" in draft.palette_markdown
    assert all(choice in draft.palette_markdown for choice in ("ㄱ", "ㄴ", "ㄷ"))
    assert draft.palette_markdown.rstrip().endswith("ㄴ,ㄷ")
