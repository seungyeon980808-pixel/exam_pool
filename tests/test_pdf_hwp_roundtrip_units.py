from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.pdf_hwp_roundtrip_units as subject
from app.pdf_hwp_final_figure_contract import FinalFigureContract
from app.pdf_hwp_pipeline import detect_items
from app.pdf_hwp_pipeline_models import (
    DetectedItem,
    DraftArtifact,
    LayoutStyle,
)
from app.pdf_hwp_roundtrip_units import FailureCode, load_prepared_units, prepare_units
from app.pdf_hwp_roundtrip_models import SourceProfile
from app.pdf_hwp_kice_source_crop import observe_source_crop
from app.pdf_hwp_roundtrip_unit_store import PreparationPayload, write_prepared_units


EBS = Path.home() / "Desktop" / "project" / "31_hwp_palette" / "2027 수능특강 물리학 I 원본.pdf"
KICE = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일" / "e2_2024_09.pdf"
P1 = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일" / "p1_2019_11.pdf"
P2 = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일" / "p2_2026_11.pdf"
P2_2013 = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일" / "p2_2013_11.pdf"
B1_2024 = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일" / "b1_2024_09.pdf"
B2_2022 = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일" / "b2_2022_06.pdf"
C1_2024 = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일" / "c1_2024_06.pdf"
C2_2023 = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일" / "c2_2023_06.pdf"
E1_2024 = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일" / "e1_2024_06.pdf"
E2_2023 = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일" / "e2_2023_11.pdf"
E2_2025 = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일" / "e2_2025_09.pdf"


def test_prepared_manifest_writes_profiled_schema_v2(tmp_path: Path) -> None:
    # Given
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    payload = PreparationPayload(
        source, "a" * 64, LayoutStyle.SUNEUNG, (), (),
        SourceProfile.EBS_EDITABLE_REFLOW,
    )

    # When
    result = write_prepared_units(tmp_path / "prepared-units.json", payload)

    # Then
    persisted = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == 2
    assert persisted["profile"] == "ebs_editable_reflow"


def _draft(item: DetectedItem) -> DraftArtifact:
    return DraftArtifact(
        item.item_number,
        f"\\수능정답1대사진5선지\\\n{item.item_number}\n지문\n-\n발문\n①\n②\n③\n④\n⑤",
        item.source_text,
        ("①", "②", "③", "④", "⑤"),
        subject.CropArtifact(Path("source.png"), Path("source.json"), 10, 10),
        None,
        (),
    )


def test_prepare_persists_units_and_load_reconstructs_restart_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = (
        DetectedItem(1, 2, 0, (0, 0, 100, 100), "two"),
        DetectedItem(1, 1, 0, (0, 100, 100, 200), "one"),
    )
    monkeypatch.setattr(subject, "build_editable_draft", lambda source, item, root, **options: _draft(item))
    monkeypatch.setattr(subject, "draft_review_detail", lambda item, markdown, assets: None)
    monkeypatch.setattr(
        subject,
        "reconcile_final_figure_contract",
        lambda item, markdown, assets, **_kwargs: FinalFigureContract(markdown, None),
    )
    monkeypatch.setattr(subject, "preflight_unit", lambda unit, style: unit)

    prepared = prepare_units(KICE, items, tmp_path, LayoutStyle.SUNEUNG)
    loaded = load_prepared_units(prepared.manifest_path)

    assert tuple(unit.item_number for unit in prepared.prepared_units) == (1, 2)
    assert loaded.prepared_units == prepared.prepared_units
    assert loaded.profile is SourceProfile.KICE_STRUCTURAL
    assert tuple(record.structure.number for record in loaded.records) == (1, 2)
    assert tuple(record.structure.source_page for record in loaded.records) == (1, 1)
    assert loaded.records[0].structure.item_bbox == (0.0, 100.0, 100.0, 200.0)
    assert loaded.item_failures == ()
    assert loaded.manifest_path == prepared.manifest_path


def test_real_seven_regressions_gate_bad_crops_and_prepare_hapdap(
    tmp_path: Path,
) -> None:
    ebs_items = tuple(
        item for item in detect_items(EBS).items
        if item.item_number in {35, 37, 234, 235, 237, 238}
    )
    kice_items = tuple(
        item for item in detect_items(KICE).items if item.item_number == 1
    )

    ebs = prepare_units(EBS, ebs_items, tmp_path / "ebs", LayoutStyle.SUNEUNG)
    kice = prepare_units(KICE, kice_items, tmp_path / "kice", LayoutStyle.SUNEUNG)

    failures = {failure.item_number: failure.code for failure in ebs.item_failures}
    assert failures == {
        35: FailureCode.CROP_CONTAMINATION,
        37: FailureCode.CROP_CONTAMINATION,
    }
    assert tuple(unit.item_number for unit in ebs.prepared_units) == (234, 235, 237, 238)
    for unit in ebs.prepared_units:
        lines = unit.palette_markdown.splitlines()
        assert lines[0] in {
            "\\수능합답1대사진5선지\\",
            "\\수능합답1소사진5선지\\",
            "\\수능AI실제합답형\\",
        }
        assert len(lines[5:8]) == 3
        assert all(slot.strip() for slot in lines[5:8])
    assert tuple(failure.code for failure in kice.item_failures) == (
        FailureCode.CROP_CLIPPING,
    )
    assert kice.prepared_units == ()


def test_real_ebs_student_dialogue_is_editable_not_a_mixed_crop(
    tmp_path: Path,
) -> None:
    selected = tuple(
        item for item in detect_items(EBS).items
        if item.item_number in {35, 37, 192}
    )

    result = prepare_units(
        EBS, selected, tmp_path, LayoutStyle.SUNEUNG,
        SourceProfile.EBS_EDITABLE_REFLOW,
    )

    assert tuple(unit.item_number for unit in result.prepared_units) == (192,)
    assert {failure.item_number: failure.code for failure in result.item_failures} == {
        35: FailureCode.CROP_CONTAMINATION,
        37: FailureCode.CROP_CONTAMINATION,
    }
    record = result.records[0]
    assert record.unit.figure_assets == ()
    assert "학생 A: 강자성체는 외부 자기장을 제거하여도 자성을 오래 유지해." in record.structure.stem
    assert "학생 B: 상자성체에 자석을 가까이 하면 서로 당기는 방향의 자기력이 작용해." in record.structure.stem
    assert "학생 C: 반자성체는 외부 자기장과 반대 방향으로 자기화돼." in record.structure.stem
    assert record.structure.ask == "제시한 내용이 옳은 학생만을 있는 대로 고른 것은?"
    assert tuple(record.structure.choices) == (
        "\\수식{A}", "\\수식{C}", "\\수식{A}, \\수식{B}",
        "\\수식{B}, \\수식{C}", "\\수식{A}, \\수식{B}, \\수식{C}",
    )


def test_real_semantic_figure_and_split_panels_do_not_false_positive(
    tmp_path: Path,
) -> None:
    p1_items = tuple(
        item for item in detect_items(P1).items if item.item_number in {2, 9}
    )
    p2_items = tuple(
        item for item in detect_items(P2).items if item.item_number == 8
    )
    ebs_items = tuple(
        item for item in detect_items(EBS).items if item.item_number in {1, 24}
    )

    p1 = prepare_units(P1, p1_items, tmp_path / "p1", LayoutStyle.SUNEUNG)
    p2 = prepare_units(P2, p2_items, tmp_path / "p2", LayoutStyle.SUNEUNG)
    ebs = prepare_units(EBS, ebs_items, tmp_path / "ebs", LayoutStyle.SUNEUNG)

    assert tuple(unit.item_number for unit in p1.prepared_units) == (2, 9)
    assert p1.item_failures == ()
    assert len(p1.records[0].structure.stem) > 4
    assert tuple(unit.item_number for unit in p2.prepared_units) == (8,)
    assert p2.item_failures == ()
    assert tuple(unit.item_number for unit in ebs.prepared_units) == (1, 24)
    assert ebs.item_failures == ()


def test_real_p1_experiment_and_hapdap_prepare_with_typed_structure(
    tmp_path: Path,
) -> None:
    selected = tuple(
        item for item in detect_items(P1).items if item.item_number in {4, 13}
    )

    result = prepare_units(P1, selected, tmp_path, LayoutStyle.SUNEUNG)

    assert tuple(unit.item_number for unit in result.prepared_units) == (4, 13)
    assert result.item_failures == ()
    structures = {record.unit.item_number: record.structure for record in result.records}
    assert len(structures[4].bogi) == len(structures[13].bogi) == 3
    assert len(structures[4].choices) == len(structures[13].choices) == 5


def test_real_p2_2013_mixed_vector_prompts_keep_editable_structure(
    tmp_path: Path,
) -> None:
    numbers = {3, 4, 5, 8, 10, 11, 13}
    selected = tuple(
        item for item in detect_items(P2_2013).items if item.item_number in numbers
    )

    result = prepare_units(P2_2013, selected, tmp_path, LayoutStyle.SUNEUNG)

    assert tuple(unit.item_number for unit in result.prepared_units) == tuple(sorted(numbers))
    assert result.item_failures == ()
    assert all(record.structure.stem for record in result.records)
    assert all(len(record.structure.choices) == 5 for record in result.records)
    structures = {record.unit.item_number: record.structure for record in result.records}
    assert all(len(structures[number].bogi) == 3 for number in {3, 5, 8, 10})
    assert all(not structures[number].bogi for number in {4, 11, 13})
    assert structures[4].ask.endswith("A, B의크기는무시한다.)")
    assert structures[11].ask.endswith("A의전하량은?")


@pytest.mark.parametrize(("source", "numbers"), (
    (B1_2024, (4, 13)),
    (C1_2024, (19,)),
    (C2_2023, (1, 6)),
    (E1_2024, (1,)),
    (P2_2013, (17,)),
))
def test_real_extract_audit_blockers_have_registered_editable_structure(
    tmp_path: Path, source: Path, numbers: tuple[int, ...],
) -> None:
    if not source.is_file():
        pytest.skip(f"missing source PDF {source.name}")
    detected = detect_items(source)
    selected = tuple(item for item in detected.items if item.item_number in numbers)

    result = prepare_units(
        source, selected, tmp_path / source.stem,
        LayoutStyle.SUNEUNG, SourceProfile.KICE_STRUCTURAL,
    )

    assert tuple(unit.item_number for unit in result.prepared_units) == numbers
    assert not result.item_failures
    if source == B1_2024:
        assert result.records[0].unit.palette_markdown.startswith("\\수능AI실제합답형\\")
        assert tuple(claim.label for claim in result.records[0].structure.bogi) == ("ㄱ", "ㄴ", "ㄷ")
    if source == P2_2013:
        structure = result.records[0].structure
        assert structure.stem.startswith("탄소동위원소")
        assert "{}_{6}^{14}C" in structure.stem
        assert "{}_{7}^{14}N" in structure.stem
        assert not structure.asset_refs


@pytest.mark.parametrize(("source", "numbers", "figure_numbers"), (
    (B1_2024, {7, 12, 14, 18}, {7, 12, 18}),
    (B2_2022, {14, 19}, set()),
    (P2_2013, {14}, {14}),
))
def test_real_kice_contamination_cohort_prepares_without_prose_in_crop(
    tmp_path: Path,
    source: Path,
    numbers: set[int],
    figure_numbers: set[int],
) -> None:
    selected = tuple(
        item for item in detect_items(source).items if item.item_number in numbers
    )

    result = prepare_units(
        source, selected, tmp_path, LayoutStyle.SUNEUNG,
        SourceProfile.KICE_STRUCTURAL,
    )

    assert {unit.item_number for unit in result.prepared_units} == numbers
    assert result.item_failures == ()
    for unit in result.prepared_units:
        if unit.item_number not in figure_numbers:
            assert unit.figure_assets == ()
            assert unit.palette_markdown.startswith("\\수능AI실제")
            continue
        assert unit.figure_assets
        parent = next((tmp_path / "assets" / f"item-{unit.item_number:03d}").glob(
            f"page-*-item-{unit.item_number}-figure.json"
        ))
        payload = json.loads(parent.read_text(encoding="utf-8"))
        assert payload["manual_review_required"] is False
        assert payload["excluded_body_spans"] == []
        if source == B1_2024 and unit.item_number in {12, 18}:
            assert len(payload["component_bboxes"]) >= 2
        if source == P2_2013:
            assert payload["asset_mode"] == "pdf_exact_vector_region_crop_hd"
            assert payload["protected_texts"] == []


@pytest.mark.parametrize(("source", "numbers"), (
    (C2_2023, {8}),
    (E2_2023, {5, 10}),
    (E2_2025, {3}),
    (EBS, {168, 246}),
))
def test_real_unexpected_crop_blockers_prepare_complete_prose_free_figures(
    tmp_path: Path, source: Path, numbers: set[int],
) -> None:
    selected = tuple(
        item for item in detect_items(source).items if item.item_number in numbers
    )

    result = prepare_units(
        source, selected, tmp_path / source.stem, LayoutStyle.SUNEUNG,
        SourceProfile.KICE_STRUCTURAL,
    )

    assert {unit.item_number for unit in result.prepared_units} == numbers
    assert result.item_failures == ()
    assert all(record.structure.stem and record.structure.ask for record in result.records)
    for unit in result.prepared_units:
        assert unit.figure_assets
        assert all(
            observe_source_crop(asset).contaminated_bboxes == ()
            for asset in unit.figure_assets
        )


def test_real_known_unsafe_crops_remain_quarantined(tmp_path: Path) -> None:
    cases = (
        (EBS, {35, 37}, FailureCode.CROP_CONTAMINATION),
        (KICE, {1}, FailureCode.CROP_CLIPPING),
    )
    for source, numbers, code in cases:
        selected = tuple(
            item for item in detect_items(source).items if item.item_number in numbers
        )
        result = prepare_units(
            source, selected, tmp_path / source.stem, LayoutStyle.SUNEUNG,
            SourceProfile.KICE_STRUCTURAL,
        )
        assert result.prepared_units == ()
        assert {failure.item_number for failure in result.item_failures} == numbers
        assert all(failure.code is code for failure in result.item_failures)
