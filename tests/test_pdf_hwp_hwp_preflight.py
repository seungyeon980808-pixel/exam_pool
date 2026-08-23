from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from PIL import Image
import pytest

from app.integrations import palette_registry
from app.pdf_hwp_pipeline import typeset_conversion
from app.pdf_hwp_pipeline_models import (
    ConversionRequest,
    ConversionUnit,
    DisplaySize,
    FigureArrangement,
    FigureAsset,
    FigureAssetMetadata,
    GeneratedDocument,
    LayoutStyle,
    ManualReviewRequiredError,
    PanelMode,
    SourceKind,
)


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor" / "hwp_typesetter"))


class _RecordingTypesetter:
    def __init__(self) -> None:
        self.markdown = ""

    def typeset(
        self,
        markdown: str,
        output_dir: Path,
        layout_style: LayoutStyle,
        asset_dirs: tuple[Path, ...],
    ) -> GeneratedDocument:
        self.markdown = markdown
        hwp_path = output_dir / "converted.hwp"
        pdf_path = output_dir / "converted.pdf"
        hwp_path.write_bytes(b"HWP fixture")
        pdf_path.write_bytes(b"PDF fixture")
        return GeneratedDocument(hwp_path, pdf_path, ())


def _figure_asset(
    tmp_path: Path, index: int, caption: str = "", *, asset_count: int = 1,
) -> FigureAsset:
    image_path = tmp_path / f"figure-{index}.png"
    Image.new("RGB", (120, 80), color=(index * 40, 10, 20)).save(image_path, format="PNG")
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    return FigureAsset(
        image_path=image_path,
        metadata=FigureAssetMetadata(
            source_pdf=tmp_path / "source.pdf",
            page_number=1,
            item_number=20,
            image_bbox=(10.0, 20.0, 110.0, 90.0),
            caption_text=caption,
            caption_bbox=None,
            asset_count=asset_count,
            panel_index=index,
            panel_mode=PanelMode.SINGLE,
            arrangement=FigureArrangement.HORIZONTAL,
            source_kind=SourceKind.RASTER,
            display_size=DisplaySize.SMALL,
            dpi=600,
            width_px=120,
            height_px=80,
            asset_hash=digest,
            confidence=1.0,
        ),
    )


def _request(tmp_path: Path, unit: ConversionUnit) -> ConversionRequest:
    return ConversionRequest(
        job_key="preflight",
        units=(unit,),
        output_dir=tmp_path / "output",
        layout_style=LayoutStyle.SUNEUNG,
        asset_dirs=(tmp_path,),
    )


def test_asset_count_mismatch_requires_manual_review_before_hwp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a registered two-photo template but only one final figure asset.
    monkeypatch.setattr(
        palette_registry,
        "active_template",
        lambda _style, _label: {
            "label": "two-photo",
            "slot_count": 3,
            "slot_names": ["문항번호", "사진1", "사진2"],
        },
    )
    asset = _figure_asset(tmp_path, 1)
    unit = ConversionUnit(
        item_number=20,
        palette_markdown=f"\\two-photo\\\n20\n\\{asset.image_path.stem}\\\n-",
        figure_assets=(asset,),
    )
    typesetter = _RecordingTypesetter()

    # When/Then: preflight leaves the item for manual review without launching HWP.
    with pytest.raises(ManualReviewRequiredError, match="figure slot count"):
        typeset_conversion(_request(tmp_path, unit), typesetter=typesetter)
    assert typesetter.markdown == ""


def test_caption_count_mismatch_requires_manual_review_before_hwp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a registered caption slot but a captionless asset with no separate caption text.
    monkeypatch.setattr(
        palette_registry,
        "active_template",
        lambda _style, _label: {
            "label": "captioned",
            "slot_count": 3,
            "slot_names": ["문항번호", "사진1", "캡션1"],
        },
    )
    asset = _figure_asset(tmp_path, 1)
    unit = ConversionUnit(
        item_number=20,
        palette_markdown=f"\\captioned\\\n20\n\\{asset.image_path.stem}\\\n-",
        figure_assets=(asset,),
    )
    typesetter = _RecordingTypesetter()

    # When/Then: the absent caption is a typed manual-review result, not an HWP failure.
    with pytest.raises(ManualReviewRequiredError, match="caption slot count"):
        typeset_conversion(_request(tmp_path, unit), typesetter=typesetter)
    assert typesetter.markdown == ""


def test_captionless_template_ignores_unused_asset_caption_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        palette_registry,
        "active_template",
        lambda _style, _label: {
            "label": "one-photo",
            "slot_count": 3,
            "slot_names": ["문항번호", "발문", "사진1"],
        },
    )
    asset = _figure_asset(tmp_path, 1, caption="(가)")
    markdown = f"\\one-photo\\\n20\n발문\n\\{asset.image_path.stem}\\"
    unit = ConversionUnit(item_number=20, palette_markdown=markdown, figure_assets=(asset,))

    typeset_conversion(_request(tmp_path, unit), typesetter=_RecordingTypesetter())


def test_captionless_png_and_caption_remain_separate_hwp_slot_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one captionless PNG plus separately recovered caption text.
    monkeypatch.setattr(
        palette_registry,
        "active_template",
        lambda _style, _label: {
            "label": "captioned",
            "slot_count": 3,
            "slot_names": ["문항번호", "사진1", "캡션1"],
        },
    )
    asset = _figure_asset(tmp_path, 1, caption="(가)")
    markdown = f"\\captioned\\\n20\n\\{asset.image_path.stem}\\\n(가)"
    unit = ConversionUnit(item_number=20, palette_markdown=markdown, figure_assets=(asset,))
    typesetter = _RecordingTypesetter()

    # When: the preflighted unit is handed to HwpPalette.
    result = typeset_conversion(_request(tmp_path, unit), typesetter=typesetter)

    # Then: image and caption occupy independent values and the HWP manifest owns the PNG hash.
    assert typesetter.markdown.splitlines()[2:4] == [f"\\{asset.image_path.stem}\\", "(가)"]
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["figure_asset_hashes"] == {
        "20": [asset.metadata.asset_hash],
    }
    from hwp_palette.model import parser as hwp_parser

    lookup = {
        "captioned": (
            "템플릿",
            {"slot_count": 3, "slot_names": ["문항번호", "사진1", "캡션1"]},
        ),
        asset.image_path.stem: ("사진", {"path": str(asset.image_path)}),
    }
    ops, warnings = hwp_parser.build_library_plan(typesetter.markdown, lookup)
    fills = next(operation[2] for operation in ops if operation[0] == "template")
    assert warnings == []
    assert fills[1][0]["image"] == str(asset.image_path)
    assert fills[2] == "(가)"


def test_revised_experiment_template_accepts_its_dedicated_photo_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot_names = [
        "문항번호", "문두", "실험내용", "사진1", "표", "발문",
        "ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5",
    ]
    monkeypatch.setattr(
        palette_registry,
        "active_template",
        lambda _style, _label: {
            "label": "수능AI실제실험형",
            "slot_count": len(slot_names),
            "slot_names": slot_names,
        },
    )
    asset = _figure_asset(tmp_path, 1)
    values = (
        "4", "intro", "experiment", f"\\{asset.image_path.stem}\\", "-", "ask",
        "a", "b", "c", "1", "2", "3", "4", "5",
    )
    unit = ConversionUnit(
        4,
        "\n".join(("\\수능AI실제실험형\\", *values)),
        (asset,),
    )

    result = typeset_conversion(_request(tmp_path, unit), typesetter=_RecordingTypesetter())

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["figure_asset_hashes"]["4"] == [asset.metadata.asset_hash]


def test_editable_kice_panel_labels_are_typed_caption_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the three-panel HWP contract uses the visible KICE labels themselves
    # as editable marker names instead of generic caption1/caption2/caption3 names.
    slot_names = [
        "문항번호", "문두", "사진1", "(가)", "사진2", "(나)",
        "사진3", "(다)", "발문", "ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5",
    ]
    monkeypatch.setattr(
        palette_registry,
        "active_template",
        lambda _style, _label: {
            "label": "수능합답3소사진5선지",
            "slot_count": len(slot_names),
            "slot_names": slot_names,
        },
    )
    captions = ("(가)", "(나)", "(다)")
    assets = tuple(
        FigureAsset(
            base.image_path,
            base.metadata.model_copy(update={
                "item_number": 3,
                "asset_count": 3,
                "panel_index": index,
                "panel_mode": PanelMode.SEPARATE,
                "caption_text": caption,
                "caption_bbox": (20.0, 91.0, 40.0, 99.0),
            }),
        )
        for index, caption in enumerate(captions, start=1)
        for base in (_figure_asset(tmp_path, index, asset_count=3),)
    )
    values = ["3", "passage"]
    for asset, caption in zip(assets, captions, strict=True):
        values.extend((f"\\{asset.image_path.stem}\\", caption))
    values.extend(("ask?", "claim-a", "claim-b", "claim-c", "1", "2", "3", "4", "5"))
    markdown = "\n".join(("\\수능합답3소사진5선지\\", *values))

    # When/Then: all three labels are matched to their ordered captionless panels.
    unit = ConversionUnit(3, markdown, assets)
    result = typeset_conversion(_request(tmp_path, unit), typesetter=_RecordingTypesetter())
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["figure_asset_hashes"]["3"] == [
        asset.metadata.asset_hash for asset in assets
    ]

    # Mutation proof: a wrong visible label cannot pass the typed contract.
    malformed = ConversionUnit(3, markdown.replace("(다)\nask?", "(라)\nask?"), assets)
    with pytest.raises(ManualReviewRequiredError, match="caption slot values"):
        typeset_conversion(_request(tmp_path, malformed), typesetter=_RecordingTypesetter())


def test_one_large_hapdap_item_uses_exact_photo_and_answer_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an active suneung palette and one prompt-photo hapdap item.
    data_root = tmp_path / "data"
    monkeypatch.setattr(palette_registry, "data_dir", lambda: data_root)
    palette_registry._save_registry({
        "schema_version": palette_registry.REGISTRY_SCHEMA,
        "active": {"suneung": "isolated-user-palette"},
        "packages": [],
    })
    base_asset = _figure_asset(tmp_path, 1)
    asset = FigureAsset(
        base_asset.image_path,
        base_asset.metadata.model_copy(update={"item_number": 1}),
    )
    values = (
        "1", "prompt", f"\\{asset.image_path.stem}\\", "ask",
        "claim-a", "claim-b", "claim-c", "1", "2", "3", "4", "5",
    )
    unit = ConversionUnit(
        item_number=1,
        palette_markdown="\n".join(("\\수능합답1대사진5선지\\", *values)),
        figure_assets=(asset,),
    )

    # When: production preflight resolves the compatibility-derived full template.
    result = typeset_conversion(_request(tmp_path, unit), typesetter=_RecordingTypesetter())

    # Then: the distinct prompt photo is hashed and all twelve values are accepted.
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["figure_asset_hashes"]["1"] == [asset.metadata.asset_hash]
