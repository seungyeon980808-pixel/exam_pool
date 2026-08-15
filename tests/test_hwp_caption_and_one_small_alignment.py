from __future__ import annotations

import importlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "assets" / "hwp_templates"


def _engine():
    runtime = ROOT / "vendor" / "hwp_typesetter"
    with patch.object(sys, "path", [str(runtime), *sys.path]):
        return importlib.import_module("hwp_palette.hwp.engine_library")


def _dump(name: str) -> str:
    if shutil.which("rhwp") is None:
        pytest.skip("rhwp CLI is required")
    return subprocess.run(
        ["rhwp", "dump", str(TEMPLATES / name), "--section", "0"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _anchor_trace(name: str, needle: str) -> str:
    if shutil.which("rhwp") is None:
        pytest.skip("rhwp CLI is required")
    return subprocess.run(
        [
            "rhwp", "hwp5-anchor-trace", str(TEMPLATES / name),
            "--needle", needle, "--section", "0", "--window", "2",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def test_editable_panel_caption_is_centered_when_filled() -> None:
    engine = _engine()
    hwp = MagicMock()
    hwp.GetPos.return_value = (0, 0, 0)
    with patch.object(engine, "_h", return_value=hwp), \
         patch.object(engine, "find_text", return_value=True), \
         patch.object(engine, "insert_plain"), \
         patch.object(engine, "strip_slot_markers"), \
         patch.object(engine, "_set_current_paragraph_word_boundary_wrap"):
        assert engine.fill_slots(
            (0, 0, 0), ["(가)"], end_para=1, slot_count=1,
            slot_names=["(가)"],
        ) == (1, 1)

    hwp.HAction.Run.assert_any_call("ParagraphShapeAlignCenter")


@pytest.mark.parametrize(
    "slot_name",
    ["사진1", "사진2", "사진3", "자료", "선지사진1", "선지사진5"],
)
def test_every_photo_slot_centers_the_picture_from_a_zero_cell_start(slot_name: str) -> None:
    engine = _engine()
    hwp = MagicMock()
    hwp.GetPos.return_value = (0, 0, 0)
    paragraph = hwp.HParameterSet.HParaShape
    with patch.object(engine, "_h", return_value=hwp), \
         patch.object(engine, "find_text", return_value=True), \
         patch.object(engine, "delete_selection"), \
         patch.object(engine, "insert_rich_line"), \
         patch.object(engine, "strip_slot_markers"), \
         patch.object(engine, "_set_current_paragraph_word_boundary_wrap"):
        assert engine.fill_slots(
            (0, 0, 0),
            [[{"text": "", "image": "figure.png", "style": None}]],
            end_para=1,
            slot_count=1,
            slot_names=[slot_name],
        ) == (1, 1)

    hwp.HAction.Execute.assert_any_call("ParagraphShape", paragraph.HSet)
    assert paragraph.LeftMargin == 0
    assert paragraph.Indentation == 0
    assert paragraph.AlignType == 3


@pytest.mark.parametrize(
    "name", ["csat_direct_one_small.hwp", "csat_hapdap_one_small.hwp"],
)
def test_one_small_table_uses_registered_floating_wrap(name: str) -> None:
    dump = _dump(name)
    table = dump.split('text="\\사진1\\"', maxsplit=1)[0].rsplit("[common]", maxsplit=2)[-2]

    assert "treat_as_char=false" in table
    assert "wrap=어울림" in table


@pytest.mark.parametrize(
    "name", ["csat_direct_one_small.hwp", "csat_hapdap_one_small.hwp"],
)
def test_one_small_table_is_anchored_to_the_upper_right(name: str) -> None:
    dump = _dump(name)
    table = dump.split('text="\\사진1\\"', maxsplit=1)[0].rsplit("[common]", maxsplit=2)

    assert "vert=문단(0=0.0mm)" in table[-2]
    assert "horz=단(0=0.0mm)" in table[-2]
    assert "valign=Top, halign=Right" in table[-1]
    cell = re.search(r"셀\[0\].*h=(\d+) w=(\d+)", dump)
    assert cell is not None
    assert int(cell.group(2)) / 283.465 == pytest.approx(43.0, abs=0.2)
    assert int(cell.group(1)) / 283.465 == pytest.approx(29.0, abs=0.2)


def test_one_small_hapdap_floating_table_shares_the_body_paragraph() -> None:
    dump = _dump("csat_hapdap_one_small.hwp")
    paragraphs = dump.split("--- 문단 0.")[1:]

    assert len(paragraphs) == 4
    assert all(
        "keep: with_next=true keep_lines=true" in paragraph
        for paragraph in paragraphs[:3]
    )
    assert "keep: with_next=false" in paragraphs[3]
    assert "표: 1행×1열" in paragraphs[0]
    assert "\\문두\\" in paragraphs[0]
    assert "\\발문\\" in paragraphs[0]
    assert "\\사진1\\" in paragraphs[0]


def test_one_small_hapdap_table_anchor_precedes_the_question_text() -> None:
    trace = _anchor_trace("csat_hapdap_one_small.hwp", "\\문두\\")
    annotated = next(line for line in trace.splitlines() if line.startswith("- annotated:"))

    assert annotated.index("<0x000b:Table") < annotated.index("\\문항번호\\")


def test_contain_size_can_enlarge_only_when_explicitly_enabled() -> None:
    engine = _engine()

    assert engine._contain_picture_size_mm(20.0, 10.0, 39.0, 28.0) == (20.0, 10.0)
    assert engine._contain_picture_size_mm(
        20.0, 10.0, 39.0, 28.0, allow_upscale=True,
    ) == pytest.approx((39.0, 19.5))
