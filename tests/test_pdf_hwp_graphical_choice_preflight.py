from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Final, TypedDict

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
    GraphicalChoiceAsset,
    GraphicalChoiceAssetMetadata,
    LayoutStyle,
    ManualReviewRequiredError,
    PanelMode,
)


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor" / "hwp_typesetter"))
LABEL: Final = "수능정답1대사진그림5선지"
SLOTS: Final = (
    "문항번호", "문두", "사진1", "발문",
    "선지사진1", "선지사진2", "선지사진3", "선지사진4", "선지사진5",
)


class _TemplateSpec(TypedDict):
    label: str
    slot_count: int
    slot_names: list[str]


class _RecordingTypesetter:
    """Minimal mutable fake that captures the HwpPalette input markdown."""

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


def _image(tmp_path: Path, name: str, color: tuple[int, int, int]) -> tuple[Path, str]:
    path = tmp_path / f"{name}.png"
    Image.new("RGB", (90, 70), color=color).save(path, format="PNG")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _prompt(tmp_path: Path) -> FigureAsset:
    path, digest = _image(tmp_path, "prompt", (40, 10, 20))
    return FigureAsset(
        image_path=path,
        metadata=FigureAssetMetadata(
            source_pdf=tmp_path / "source.pdf",
            page_number=1,
            item_number=20,
            image_bbox=(10.0, 20.0, 110.0, 90.0),
            caption_text="",
            caption_bbox=None,
            asset_count=1,
            panel_index=1,
            panel_mode=PanelMode.SINGLE,
            arrangement=FigureArrangement.HORIZONTAL,
            display_size=DisplaySize.SMALL,
            dpi=600,
            width_px=90,
            height_px=70,
            asset_hash=digest,
            confidence=1.0,
        ),
    )


def _choices(tmp_path: Path) -> tuple[GraphicalChoiceAsset, ...]:
    assets: list[GraphicalChoiceAsset] = []
    for index in range(1, 6):
        path, digest = _image(tmp_path, f"choice-{index}", (10, index * 35, 20))
        assets.append(GraphicalChoiceAsset(
            image_path=path,
            metadata=GraphicalChoiceAssetMetadata(
                source_pdf=tmp_path / "source.pdf",
                page_number=1,
                item_number=20,
                choice_index=index,
                dpi=600,
                width_px=90,
                height_px=70,
                asset_hash=digest,
                confidence=1.0,
            ),
        ))
    return tuple(assets)


def _template(label: str = LABEL) -> _TemplateSpec:
    return {"label": label, "slot_count": len(SLOTS), "slot_names": list(SLOTS)}


def _request(tmp_path: Path, unit: ConversionUnit) -> ConversionRequest:
    return ConversionRequest(
        job_key="graphical-choice-preflight",
        units=(unit,),
        output_dir=tmp_path / "output",
        layout_style=LayoutStyle.SUNEUNG,
        asset_dirs=(tmp_path,),
    )


def test_prompt_and_five_graphical_choices_map_to_distinct_ordered_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one prompt image plus five independently ordered graphical choices.
    monkeypatch.setattr(palette_registry, "active_template", lambda _style, _label: _template())
    prompt = _prompt(tmp_path)
    choices = _choices(tmp_path)
    tokens = [f"\\{asset.image_path.stem}\\" for asset in (prompt, *choices)]
    markdown = "\n".join((f"\\{LABEL}\\", "20", "prompt", tokens[0], "ask", *tokens[1:]))
    unit = ConversionUnit(20, markdown, (prompt,), choices)
    typesetter = _RecordingTypesetter()

    # When: registered-template preflight hands the unit to HwpPalette.
    result = typeset_conversion(_request(tmp_path, unit), typesetter=typesetter)

    # Then: the manifest and real parser plan retain prompt/choice order.
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["figure_asset_hashes"]["20"] == [
        prompt.metadata.asset_hash, *(asset.metadata.asset_hash for asset in choices),
    ]
    from hwp_palette.model import parser as hwp_parser

    lookup = {
        LABEL: ("템플릿", {"slot_count": len(SLOTS), "slot_names": SLOTS}),
        **{
            asset.image_path.stem: ("사진", {"path": str(asset.image_path)})
            for asset in (prompt, *choices)
        },
    }
    ops, warnings = hwp_parser.build_library_plan(typesetter.markdown, lookup)
    fills = next(operation[2] for operation in ops if operation[0] == "template")
    assert warnings == []
    assert [fills[index][0]["image"] for index in (2, 4, 5, 6, 7, 8)] == [
        str(prompt.image_path), *(str(asset.image_path) for asset in choices),
    ]


def test_text_choice_template_does_not_require_graphical_choice_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a normal five-text-choice item with one prompt image.
    text_slots = ["문항번호", "문두", "사진1", "발문", "1", "2", "3", "4", "5"]
    monkeypatch.setattr(
        palette_registry, "active_template",
        lambda _style, _label: {
            "label": "normal-text-choices", "slot_count": len(text_slots), "slot_names": text_slots,
        },
    )
    prompt = _prompt(tmp_path)
    markdown = "\n".join((
        "\\normal-text-choices\\", "20", "prompt", f"\\{prompt.image_path.stem}\\",
        "ask", "a", "b", "c", "d", "e",
    ))

    # When: preflight validates the ordinary item.
    result = typeset_conversion(
        _request(tmp_path, ConversionUnit(20, markdown, (prompt,))),
        typesetter=_RecordingTypesetter(),
    )

    # Then: its prompt remains the complete image contract.
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["figure_asset_hashes"]["20"] == [prompt.metadata.asset_hash]


def test_graphical_choice_order_mismatch_requires_manual_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the exact registered template but choice assets arrive out of order.
    monkeypatch.setattr(palette_registry, "active_template", lambda _style, _label: _template())
    choices = _choices(tmp_path)
    unit = ConversionUnit(
        20, f"\\{LABEL}\\", (_prompt(tmp_path),), (choices[1], choices[0], *choices[2:]),
    )

    # When/Then: preflight rejects the permutation before launching HWP.
    with pytest.raises(ManualReviewRequiredError, match="indices are not contiguous and ordered"):
        typeset_conversion(_request(tmp_path, unit), typesetter=_RecordingTypesetter())


def test_graphical_choices_reject_lookalike_unregistered_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a lookalike template with identical slot names but a different label.
    monkeypatch.setattr(
        palette_registry, "active_template", lambda _style, _label: _template("lookalike"),
    )
    unit = ConversionUnit(20, "\\lookalike\\", (_prompt(tmp_path),), _choices(tmp_path))

    # When/Then: the special six-image allowance remains label-scoped.
    with pytest.raises(ManualReviewRequiredError, match="registered five-choice template"):
        typeset_conversion(_request(tmp_path, unit), typesetter=_RecordingTypesetter())


def test_graphical_choice_template_rejects_missing_image_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the six-image template token but no persisted image assets.
    monkeypatch.setattr(palette_registry, "active_template", lambda _style, _label: _template())

    # When/Then: the special template cannot bypass preflight's asset contract.
    with pytest.raises(ManualReviewRequiredError, match="figure slot count"):
        typeset_conversion(
            _request(tmp_path, ConversionUnit(20, f"\\{LABEL}\\")),
            typesetter=_RecordingTypesetter(),
        )
