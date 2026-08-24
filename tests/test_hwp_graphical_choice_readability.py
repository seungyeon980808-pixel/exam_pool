import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "hwp_templates" / "csat_direct_one_large_graphical_choices.hwp"


def _template_dump() -> str:
    result = subprocess.run(
        ["rhwp", "dump", str(TEMPLATE), "--paragraph", "0.3", "--format", "text"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout


def test_graphical_choice_template_and_runtime_contract_retain_readable_source_scale():
    """The parsed 3-by-2 grid must retain at least 85% of source print width."""
    dump = _template_dump()
    assert "표: 2행×6열" in dump
    assert [f"\\선지사진{index}\\" for index in range(1, 6)] == [
        marker
        for marker in re.findall(r"\\선지사진[1-5]\\", dump)
        if marker
    ]

    from app.integrations import hwppalette_runner

    hwppalette_runner._prefer_exam_pool_runtime()
    from hwp_palette.hwp import engine_library

    usable_widths_mm = [engine_library.GRAPHICAL_CHOICE_IMAGE_CELL_WIDTH_MM - 0.2]
    source_print_width_mm = 110.4 / 72.0 * 25.4
    assert min(usable_widths_mm) / source_print_width_mm >= 0.85


def test_six_column_choice_grid_does_not_inherit_pair_table_inset(monkeypatch):
    from app.integrations import hwppalette_runner

    hwppalette_runner._prefer_exam_pool_runtime()
    from hwp_palette.hwp import engine_library

    class FakeCell:
        def __init__(self):
            self.width = 29.0
            self.margin = {"left": 3.0, "right": 3.0, "top": 1.5, "bottom": 3.0}
            self.columns = []
            self.margin_calls = []

        def get_col_width(self):
            return self.width

        def get_row_height(self):
            return 20.0

        def get_cell_margin(self):
            return self.margin

        def get_col_num(self):
            return 3

        def get_row_num(self):
            return 1

        def get_pos(self):
            return (0, 10, 2)

        def set_col_width(self, width, as_):
            self.columns.append((width, as_))
            self.width = width

        def TableLeftCell(self):
            return True

        def set_cell_margin(self, left, right, top, bottom, as_):
            self.margin_calls.append((left, right, top, bottom, as_))
            if len(self.margin_calls) == 2:
                self.margin = {"left": left, "right": right, "top": top, "bottom": bottom}

        def TableRightCell(self):
            return True

        def set_pos(self, *position):
            assert position == (0, 10, 2)

    fake = FakeCell()
    assert engine_library._prepare_graphical_choice_grid_cell(fake)
    width_mm, height_mm = engine_library._table_picture_bounds_mm(fake)
    assert width_mm == pytest.approx(33.133)
    assert height_mm == pytest.approx(19.0)
    assert fake.columns == [
        (engine_library.GRAPHICAL_CHOICE_LABEL_CELL_WIDTH_MM, "mm"),
        (engine_library.GRAPHICAL_CHOICE_IMAGE_CELL_WIDTH_MM, "mm"),
    ]
    assert fake.margin_calls[0] == (0.0, 0.0, 0.5, 0.5, "mm")
    assert fake.margin_calls[1] == (0.1, 0.1, 0.5, 0.5, "mm")


def test_pair_table_keeps_right_edge_safety_inset():
    from app.integrations import hwppalette_runner

    hwppalette_runner._prefer_exam_pool_runtime()
    from hwp_palette.hwp import engine_library

    class FakeCell:
        def get_col_width(self):
            return 48.0

        def get_row_height(self):
            return 30.0

        def get_cell_margin(self):
            return {"left": 0.5, "right": 0.5, "top": 0.5, "bottom": 0.5}

        def get_col_num(self):
            return 2

        def get_row_num(self):
            return 2

    width_mm, _height_mm = engine_library._table_picture_bounds_mm(FakeCell())
    assert width_mm == pytest.approx(40.0)
