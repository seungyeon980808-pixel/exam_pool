from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image
import pytest

from app.integrations import palette_registry
from app.pdf_hwp_hwp_preflight import preflight_unit
from app.pdf_hwp_pipeline_models import (
    ConversionUnit,
    DisplaySize,
    FigureArrangement,
    FigureAsset,
    FigureAssetMetadata,
    LayoutStyle,
    ManualReviewRequiredError,
    PanelMode,
    SourceKind,
)


_SLOTS = ("문항번호", "문두", "사진1", "사진2", "발문", "1", "2", "3", "4", "5")


def _asset(tmp_path: Path, index: int, caption: str) -> FigureAsset:
    path = tmp_path / f"pair-{index}.png"
    Image.new("RGB", (100, 70), color=(index * 30, 20, 10)).save(path)
    return FigureAsset(
        path,
        FigureAssetMetadata(
            source_pdf=tmp_path / "source.pdf",
            page_number=1,
            item_number=20,
            image_bbox=(10.0, 20.0, 110.0, 90.0),
            caption_text=caption,
            caption_bbox=None,
            asset_count=2,
            panel_index=index,
            panel_mode=PanelMode.SEPARATE,
            arrangement=FigureArrangement.HORIZONTAL,
            source_kind=SourceKind.RASTER,
            display_size=DisplaySize.LARGE,
            dpi=600,
            width_px=100,
            height_px=70,
            asset_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
            confidence=1.0,
        ),
    )


def _unit(tmp_path: Path, second_caption: str) -> ConversionUnit:
    assets = (_asset(tmp_path, 1, "(가)"), _asset(tmp_path, 2, second_caption))
    values = (
        "20", "prompt", f"\\{assets[0].image_path.stem}\\",
        f"\\{assets[1].image_path.stem}\\", "ask", "1", "2", "3", "4", "5",
    )
    return ConversionUnit(
        item_number=20,
        palette_markdown="\n".join(("\\수능정답2대사진5선지\\", *values)),
        figure_assets=assets,
    )


@pytest.fixture
def registered_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        palette_registry,
        "active_template",
        lambda _style, _label: {
            "label": "수능정답2대사진5선지",
            "slot_count": len(_SLOTS),
            "slot_names": _SLOTS,
        },
    )


def test_static_pair_captions_match_registered_derived_template(
    tmp_path: Path, registered_pair: None,
) -> None:
    result = preflight_unit(_unit(tmp_path, "(나)"), LayoutStyle.SUNEUNG)
    assert len(result.figure_asset_hashes) == 2


def test_static_pair_caption_mutation_requires_manual_review(
    tmp_path: Path, registered_pair: None,
) -> None:
    with pytest.raises(ManualReviewRequiredError, match="registered static captions"):
        preflight_unit(_unit(tmp_path, "(다)"), LayoutStyle.SUNEUNG)
