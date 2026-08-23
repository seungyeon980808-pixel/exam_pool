from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.pdf_hwp_pipeline_models import (
    ConversionUnit,
    FigureAsset,
    FigureAssetMetadata,
    GraphicalChoiceAsset,
    GraphicalChoiceAssetMetadata,
    LayoutStyle,
)
from app.pdf_hwp_roundtrip_models import SourceProfile
from app.pdf_hwp_roundtrip_structure import (
    PreparedStructureError,
    PreparedStructureIssue,
    parse_prepared_structure,
)
from app.pdf_hwp_roundtrip_unit_store import (
    PreparationPayload,
    PreparedUnitRecord,
    load_prepared_units,
    write_prepared_units,
)


ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = (
    ROOT / "data" / "pdf_hwp" / "roundtrip_harness" / "approved-first-run"
    / "namespaces" / "5c2ea3441d9aa4338fc9" / "sources"
)


def _real_unit(source: str, item_number: int) -> ConversionUnit:
    payload = json.loads((NAMESPACE / source / "prepared-units.json").read_text(encoding="utf-8"))
    row = next(unit for unit in payload["units"] if unit["item_number"] == item_number)
    return ConversionUnit(
        row["item_number"],
        row["palette_markdown"],
        tuple(FigureAsset(
            Path(asset["image_path"]), FigureAssetMetadata.model_validate(asset["metadata"]),
        ) for asset in row["figure_assets"]),
        tuple(GraphicalChoiceAsset(
            Path(asset["image_path"]),
            GraphicalChoiceAssetMetadata.model_validate(asset["metadata"]),
        ) for asset in row["graphical_choice_assets"]),
    )


@pytest.mark.parametrize(
    ("source", "item_number", "has_bogi"),
    [
        ("p1_2019_11-402e92ce979e", 1, False),
        ("p1_2019_11-402e92ce979e", 10, True),
        ("ebs_2027_physics1-dcd657d3f125", 12, False),
    ],
)
def test_real_prepared_markdown_exposes_ordered_typed_structure(
    source: str, item_number: int, has_bogi: bool,
) -> None:
    # Given
    unit = _real_unit(source, item_number)
    page = unit.figure_assets[0].metadata.page_number

    # When
    structure = parse_prepared_structure(
        unit, page, (10.0, 20.0, 200.0, 400.0), LayoutStyle.SUNEUNG,
    )

    # Then
    assert structure.number == item_number
    assert structure.stem.strip()
    assert structure.ask.strip()
    assert len(structure.choices) == 5
    assert bool(structure.bogi) is has_bogi
    assert structure.field_order == (
        "number", "stem", "materials", "ask", "bogi", "choices",
    )


@pytest.mark.parametrize("item_number", (2, 5, 18))
def test_real_fragment_only_stems_are_not_structurally_complete(item_number: int) -> None:
    # Given
    unit = _real_unit("p1_2019_11-402e92ce979e", item_number)

    # When / Then
    with pytest.raises(PreparedStructureError) as failure:
        parse_prepared_structure(
            unit, unit.figure_assets[0].metadata.page_number,
            (10.0, 20.0, 200.0, 400.0), LayoutStyle.SUNEUNG,
        )
    assert failure.value.code is PreparedStructureIssue.MISSING_STEM


def test_real_ask_must_not_repeat_trailing_choice_values() -> None:
    # Given
    unit = _real_unit("p1_2019_11-402e92ce979e", 20)

    # When / Then
    with pytest.raises(PreparedStructureError) as failure:
        parse_prepared_structure(
            unit, unit.figure_assets[0].metadata.page_number,
            (10.0, 20.0, 200.0, 400.0), LayoutStyle.SUNEUNG,
        )
    assert failure.value.code is PreparedStructureIssue.ASK_CHOICE_OVERLAP


def test_multiline_blocks_remain_single_typed_slots() -> None:
    # Given
    unit = ConversionUnit(
        7,
        "\\수능정답1대사진5선지\\\n7\n{첫째 줄\n둘째 줄}\n-\n{무엇을 묻는가?\n조건을 포함한다.}\n①\n②\n③\n④\n⑤",
    )

    # When
    structure = parse_prepared_structure(
        unit, 3, (1.0, 2.0, 30.0, 40.0), LayoutStyle.SUNEUNG,
    )

    # Then
    assert structure.stem == "첫째 줄\n둘째 줄"
    assert structure.ask == "무엇을 묻는가?\n조건을 포함한다."
    assert structure.materials[0].value == "-"
    assert structure.choices == ("①", "②", "③", "④", "⑤")


def test_graphical_choices_expose_five_ordered_owned_asset_refs(tmp_path: Path) -> None:
    # Given
    assets: list[GraphicalChoiceAsset] = []
    tokens: list[str] = []
    for choice_index in range(1, 6):
        path = tmp_path / f"choice-{choice_index}.png"
        path.write_bytes(f"choice-{choice_index}".encode())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assets.append(GraphicalChoiceAsset(path, GraphicalChoiceAssetMetadata(
            source_pdf=tmp_path / "source.pdf", page_number=3, item_number=7,
            choice_index=choice_index, dpi=300, width_px=200, height_px=100,
            asset_hash=digest, confidence=1.0,
        )))
        tokens.append(f"\\{path.stem}\\")
    unit = ConversionUnit(
        7,
        "\n".join(("\\수능정답0사진그림5선지\\", "7", "지문", "발문", *tokens)),
        graphical_choice_assets=tuple(assets),
    )

    # When
    structure = parse_prepared_structure(
        unit, 3, (1.0, 2.0, 30.0, 40.0), LayoutStyle.SUNEUNG,
    )

    # Then
    assert structure.choices == tuple(tokens)
    assert tuple(reference.role for reference in structure.asset_refs) == (
        "graphical_choice", "graphical_choice", "graphical_choice",
        "graphical_choice", "graphical_choice",
    )
    assert tuple(reference.owner_item_number for reference in structure.asset_refs) == (7,) * 5
    assert tuple(reference.order for reference in structure.asset_refs) == (1, 2, 3, 4, 5)


def test_v2_roundtrip_exposes_records_and_rejects_v1(tmp_path: Path) -> None:
    # Given
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    unit = ConversionUnit(
        7,
        "\\수능정답1대사진5선지\\\n7\n지문\n-\n발문?\n①\n②\n③\n④\n⑤",
    )
    structure = parse_prepared_structure(
        unit, 3, (1.0, 2.0, 30.0, 40.0), LayoutStyle.SUNEUNG,
    )
    target = tmp_path / "prepared-units.json"
    write_prepared_units(target, PreparationPayload(
        source, hashlib.sha256(b"source").hexdigest(), LayoutStyle.SUNEUNG,
        (PreparedUnitRecord(unit, "b" * 64, structure),), (),
        SourceProfile.EBS_EDITABLE_REFLOW,
    ))

    # When
    loaded = load_prepared_units(target)

    # Then
    assert loaded.prepared_units == (unit,)
    assert loaded.records[0].structure == structure
    assert loaded.profile is SourceProfile.EBS_EDITABLE_REFLOW
    legacy = json.loads(target.read_text(encoding="utf-8"))
    legacy["schema_version"] = 1
    target.write_text(json.dumps(legacy), encoding="utf-8")
    with pytest.raises(ValidationError, match="schema_version"):
        load_prepared_units(target)
