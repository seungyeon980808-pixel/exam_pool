from __future__ import annotations

from pathlib import Path

import pytest

import tools.pdf_hwp_roundtrip_harness_contract as subject
from app.integrations.hwppalette import HwpPaletteProvider
from app.pdf_hwp_roundtrip_manifest import load_manifest
from tools.pdf_hwp_roundtrip_harness_contract import (
    _production_dependency_paths,
    create_run_namespace,
    manifest_candidates,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools" / "pdf_hwp_roundtrip_approved_first_run.json"
SELECTION = ("source|kice|digest|1|수능형|claim|",)


def test_namespace_is_stable_for_unchanged_production_tree(tmp_path: Path) -> None:
    first, first_metadata = create_run_namespace(tmp_path, MANIFEST, SELECTION)
    second, second_metadata = create_run_namespace(tmp_path, MANIFEST, SELECTION)

    assert second.namespace_id == first.namespace_id
    assert second.code_dependency_sha256 == first.code_dependency_sha256
    assert second_metadata == first_metadata
    assert len(first.root.name) == 20


def test_production_dependencies_are_sorted_and_cover_conversion_seams() -> None:
    relative = tuple(
        path.relative_to(ROOT).as_posix()
        for path in _production_dependency_paths(ROOT)
    )

    assert relative == tuple(sorted(set(relative)))
    assert {
        "app/pdf_hwp_question_structure.py",
        "app/pdf_hwp_roundtrip_crop_audit.py",
        "app/pdf_hwp_roundtrip_generated_detection.py",
        "app/pdf_hwp_roundtrip_item_alignment.py",
        "app/pdf_hwp_roundtrip_readback.py",
        "app/integrations/hwppalette.py",
        "tools/pdf_hwp_roundtrip_harness_support.py",
        "vendor/hwp_typesetter/hwp_palette/hwp/hwp_engine.py",
    } <= set(relative)


def test_namespace_changes_when_previously_omitted_module_bytes_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline, _ = create_run_namespace(tmp_path, MANIFEST, SELECTION)
    target = (ROOT / "app" / "pdf_hwp_roundtrip_generated_detection.py").resolve()
    read_bytes = Path.read_bytes

    def with_generated_detector_edit(path: Path) -> bytes:
        content = read_bytes(path)
        return content + b"\n# simulated edit\n" if path.resolve() == target else content

    monkeypatch.setattr(Path, "read_bytes", with_generated_detector_edit)

    changed, _ = create_run_namespace(tmp_path, MANIFEST, SELECTION)

    assert changed.code_dependency_sha256 != baseline.code_dependency_sha256
    assert changed.namespace_id != baseline.namespace_id


def test_namespace_changes_when_selected_external_runtime_bytes_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = (tmp_path / "external-hwppalette").resolve()
    target = runtime / "hwp_palette" / "hwp" / "engine_library.py"
    target.parent.mkdir(parents=True)
    target.write_text("RUNTIME = 'baseline'\n", encoding="utf-8")
    monkeypatch.setenv("EXAMPOOL_HWPPAL_ROOT", str(runtime))
    provider = HwpPaletteProvider()
    assert provider.root == runtime
    assert target.is_file()
    baseline, _ = create_run_namespace(tmp_path, MANIFEST, SELECTION)
    read_bytes = Path.read_bytes

    def with_external_runtime_edit(path: Path) -> bytes:
        content = read_bytes(path)
        return content + b"\n# simulated external edit\n" if path.resolve() == target else content

    monkeypatch.setattr(Path, "read_bytes", with_external_runtime_edit)

    changed, _ = create_run_namespace(tmp_path, MANIFEST, SELECTION)

    assert changed.code_dependency_sha256 != baseline.code_dependency_sha256
    assert changed.namespace_id != baseline.namespace_id


def test_provider_selects_dev_sibling_runtime_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sibling = (ROOT.parent / "31_hwp_palette").resolve()
    if not (ROOT / ".git").exists() or not (sibling / "hwp_palette" / "cli.py").is_file():
        pytest.skip("development sibling runtime is not available")
    monkeypatch.delenv("EXAMPOOL_HWPPAL_ROOT", raising=False)

    assert HwpPaletteProvider().root == sibling


def test_external_runtime_hash_is_independent_of_absolute_root(tmp_path: Path) -> None:
    left = tmp_path / "machine-a" / "runtime"
    right = tmp_path / "machine-b" / "runtime"
    for runtime in (left, right):
        source = runtime / "hwp_palette" / "hwp" / "engine_library.py"
        layout = runtime / "typesetting_packs" / "csat_science" / "pack.json"
        source.parent.mkdir(parents=True)
        layout.parent.mkdir(parents=True)
        source.write_text("RUNTIME = 'same'\n", encoding="utf-8")
        layout.write_text('{"name":"same"}\n', encoding="utf-8")

    assert subject._code_dependency_sha256(ROOT, left) == subject._code_dependency_sha256(
        ROOT, right,
    )


def test_embedded_runtime_dependencies_are_hashed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = (ROOT / "vendor" / "hwp_typesetter").resolve()
    target = (runtime / "hwp_palette" / "hwp" / "engine_library.py").resolve()
    read_bytes = Path.read_bytes
    target_reads = 0

    def count_target_reads(path: Path) -> bytes:
        nonlocal target_reads
        if path.resolve() == target:
            target_reads += 1
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", count_target_reads)

    subject._code_dependency_sha256(ROOT, runtime)

    assert target_reads == 1


def test_approved_candidates_have_explicit_source_profiles() -> None:
    # Given
    candidates = manifest_candidates(load_manifest(MANIFEST))

    # When
    profiles = {candidate.group.value: candidate.profile.value for candidate in candidates}

    # Then
    assert profiles == {
        "ebs": "ebs_editable_reflow",
        "kice": "kice_structural",
        "raster": "kice_structural",
    }


def test_profile_changes_selection_contract_and_namespace(tmp_path: Path) -> None:
    # Given
    candidates = manifest_candidates(load_manifest(MANIFEST))
    kice = next(candidate for candidate in candidates if candidate.group.value == "kice")
    ebs = next(candidate for candidate in candidates if candidate.group.value == "ebs")

    # When
    kice_contract = subject.candidate_selection_contract((kice,))
    ebs_profile_contract = subject.candidate_selection_contract((
        subject.Candidate(
            kice.source_id,
            kice.group,
            kice.path,
            kice.sha256,
            kice.selected_numbers,
            kice.header_subject,
            ebs.profile,
            kice.regression_claims,
            kice.companion_pdf_path,
            kice.companion_pdf_sha256,
        ),
    ))
    kice_namespace, _ = create_run_namespace(tmp_path, MANIFEST, kice_contract)
    ebs_profile_namespace, _ = create_run_namespace(
        tmp_path, MANIFEST, ebs_profile_contract,
    )

    # Then
    assert "|kice_structural|suneung|" in kice_contract[0]
    assert "|ebs_editable_reflow|suneung|" in ebs_profile_contract[0]
    assert ebs_profile_namespace.namespace_id != kice_namespace.namespace_id
