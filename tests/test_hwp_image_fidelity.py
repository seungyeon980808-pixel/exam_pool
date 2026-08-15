from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
VENDORED_RUNTIME = ROOT / "vendor" / "hwp_typesetter"
sys.path.insert(0, str(VENDORED_RUNTIME))

from hwp_palette.hwp import engine_library, hwp_engine  # noqa: E402


class _Properties:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    def SetItem(self, name: str, value: int) -> None:
        self.values[name] = value


class _Control:
    def __init__(self) -> None:
        self.Properties = _Properties()


class _Hwp:
    def __init__(self) -> None:
        self.control = _Control()
        self.calls: list[tuple[str, bool, bool, int]] = []

    def insert_picture(
        self,
        path: str,
        *,
        treat_as_char: bool,
        embedded: bool,
        sizeoption: int,
    ) -> _Control:
        self.calls.append((path, treat_as_char, embedded, sizeoption))
        return self.control

    @staticmethod
    def MiliToHwpUnit(value: float) -> int:
        return round(value * 100)


def _source_crop(path: Path) -> None:
    image = Image.new("L", (2285, 668), color=255)
    image.save(path, dpi=(600, 600), compress_level=0)


def test_source_crop_is_embedded_from_same_asset_without_conversion(tmp_path: Path, monkeypatch) -> None:
    # Given: a lossless 600 dpi source crop and conversion disabled.
    source = tmp_path / "item20_source_crop_hd.png"
    _source_crop(source)
    fake = _Hwp()
    monkeypatch.setattr(hwp_engine, "S", {"exam_image_style": "", "layout": {"column_width_mm": 93.99}})
    monkeypatch.setattr(hwp_engine, "in_table", lambda: False)

    # When: HwpPalette inserts the figure.
    engine_library._insert_picture_sized(fake, source)

    # Then: Hancom receives the exact source path as an embedded PNG.
    assert fake.calls == [(str(source), True, True, 3)]


def test_source_crop_targets_84_to_85_percent_frame_with_locked_aspect(tmp_path: Path, monkeypatch) -> None:
    # Given: the native item-20 raster and the measured KICE question frame.
    source = tmp_path / "item20_source_crop_hd.png"
    _source_crop(source)
    fake = _Hwp()
    monkeypatch.setattr(
        hwp_engine,
        "S",
        {
            "exam_image_style": "",
            "layout": {
                "column_width_mm": 93.99,
                "figure_frame_width_mm": 114.3,
                "figure_target_ratio": 0.845,
            },
        },
    )
    monkeypatch.setattr(hwp_engine, "in_table", lambda: False)

    # When: HwpPalette sizes the embedded figure.
    engine_library._insert_picture_sized(fake, source)

    # Then: width is 84-85% of the frame and height preserves source aspect.
    width_mm = fake.control.Properties.values["Width"] / 100
    height_mm = fake.control.Properties.values["Height"] / 100
    assert 0.84 <= width_mm / 114.3 <= 0.85
    assert abs(width_mm / height_mm - 2285 / 668) < 0.002
