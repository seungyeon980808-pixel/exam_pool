from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.pdf_hwp_pipeline import detect_items
from app.pdf_hwp_pipeline_models import LayoutStyle
from app.pdf_hwp_roundtrip_models import SourceProfile
from app.pdf_hwp_roundtrip_units import prepare_units


SOURCE = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일" / "p2_2013_11.pdf"
B1_SOURCE = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일" / "b1_2024_09.pdf"


def _prepare(tmp_path: Path, *numbers: int):
    if not SOURCE.is_file():
        pytest.skip("missing p2_2013_11.pdf")
    selected = tuple(
        item for item in detect_items(SOURCE).items if item.item_number in numbers
    )
    result = prepare_units(
        SOURCE, selected, tmp_path, LayoutStyle.SUNEUNG,
        SourceProfile.KICE_STRUCTURAL,
    )
    assert not result.item_failures
    return {record.unit.item_number: record for record in result.records}


def _parent_sidecar(tmp_path: Path, item_number: int) -> dict[str, object]:
    path = next((tmp_path / "assets" / f"item-{item_number:03d}").glob(
        f"page-*-item-{item_number}-figure.json"
    ))
    return json.loads(path.read_text(encoding="utf-8"))


def test_q2_recovers_radical_superscript_and_complete_graph(tmp_path: Path) -> None:
    record = _prepare(tmp_path, 2)[2]

    assert r"4\sqrt{2}" in record.structure.bogi[0].text
    assert r"m/s^{2}" in record.structure.bogi[1].text
    bbox = _parent_sidecar(tmp_path, 2)["bbox"]
    assert bbox[0] <= 243.0
    assert bbox[3] >= 914.0


def test_q10_keeps_three_claims_and_complete_circuit(tmp_path: Path) -> None:
    record = _prepare(tmp_path, 10)[10]

    assert tuple(claim.label for claim in record.structure.bogi) == ("ㄱ", "ㄴ", "ㄷ")
    assert all(claim.text != "-" for claim in record.structure.bogi)
    assert "2Ω" in record.structure.bogi[1].text
    bbox = _parent_sidecar(tmp_path, 10)["source_bbox"]
    assert bbox[3] >= 881.0


def test_q14_preserves_report_equations_and_comparison_choices(tmp_path: Path) -> None:
    record = _prepare(tmp_path, 14)[14]
    structure = record.structure

    assert r"\frac{1}{2}mv^{2}" in structure.stem
    assert "eE = (나)" in structure.stem
    assert r"\frac{e}{m}" in structure.stem
    assert r"\frac{E^{2}}{2VB^{2}}" in structure.stem
    assert "\\표1*1\\" in record.unit.palette_markdown
    assert "¤" not in record.unit.palette_markdown
    assert len(structure.choices) == 5
    assert all(choice and choice != "-" for choice in structure.choices)
    assert r"\frac{eV}{2}" in structure.choices[0]
    assert r"\frac{evB}{2}" in structure.choices[0]
    assert structure.choices[0].index("eV") < structure.choices[0].index("evB")


def test_b1_q11_keeps_meiosis_figure_beside_editable_table(tmp_path: Path) -> None:
    if not B1_SOURCE.is_file():
        pytest.skip("missing b1_2024_09.pdf")
    item = next(
        row for row in detect_items(B1_SOURCE).items if row.item_number == 11
    )
    result = prepare_units(
        B1_SOURCE, (item,), tmp_path, LayoutStyle.SUNEUNG,
        SourceProfile.KICE_STRUCTURAL,
    )

    assert not result.item_failures
    record = result.records[0]
    assert len(record.unit.figure_assets) == 1
    assert "\\표5*2\\" in record.unit.palette_markdown
    assert tuple(claim.label for claim in record.structure.bogi) == ("ㄱ", "ㄴ", "ㄷ")
    assert len(record.structure.choices) == 5
