from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

from PIL import Image
import pytest

from tools.pdf_hwp_profile_regression_evidence import generate_evidence
from tools.pdf_hwp_profile_regression_inputs import ProfileInputError, parse_metadata


SNAPSHOT_NAMESPACE = Path(
    "data/pdf_hwp/roundtrip_harness/source-profiles/namespaces/"
    "ff3b9036fcb999488b3d"
)


@pytest.fixture
def coherent_namespace(tmp_path: Path) -> Path:
    namespace = tmp_path / "run" / "namespaces" / "fixture-namespace"
    sources = namespace / "sources"
    sources.mkdir(parents=True)
    for prefix in ("item20_original", "p1_2019_11", "ebs_2027_physics1"):
        source = next((SNAPSHOT_NAMESPACE / "sources").glob(f"{prefix}-*"))
        shutil.copytree(source, sources / source.name, copy_function=os.link)
    metadata = {
        "namespace_id": namespace.name,
        "namespace_root": str(namespace.resolve()),
        "code_dependency_sha256": "0" * 64,
    }
    (namespace.parent.parent / "run-metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    return namespace


def test_profile_regression_evidence_is_deterministic_for_coherent_snapshot(
    tmp_path: Path,
    coherent_namespace: Path,
) -> None:
    # Given: the active namespace has paired renders and typed prepared records.
    first = tmp_path / "first"
    second = tmp_path / "second"

    # When: C003 evidence is generated twice from the same immutable namespace.
    left = generate_evidence(coherent_namespace, first)
    right = generate_evidence(coherent_namespace, second)

    # Then: field-placement, profile, readback, hashes, and PNG bytes are stable.
    payload = json.loads(left.report.read_text(encoding="utf-8"))
    assert payload["namespace"]["namespace_id"] == coherent_namespace.name
    assert payload["p1_2024_q20"]["choice_field_count"] == 5
    assert payload["p1_2024_q20"]["choice_leakage_fields"] == []
    assert payload["p1_2019_tables"]["items"] == [3, 6]
    assert payload["p1_2019_figures"]["items"] == [2, 5, 18]
    assert payload["p1_2019_figures"]["minimum_scale_threshold"] == 0.70
    assert payload["p1_2019_figures"]["blocking_issue_codes"] == []
    assert [row["item_number"] for row in payload["p1_2019_figures"]["observations"]] == [2, 5, 18]
    placements = {
        row["item_number"]: row["placement"]
        for row in payload["p1_2019_figures"]["observations"]
    }
    assert placements == {
        2: "between_stem_and_ask",
        5: "side_by_side",
        18: "between_stem_and_ask",
    }
    assert all(
        row["minimum_scale"] >= 0.70
        for row in payload["p1_2019_figures"]["observations"]
    )
    assert payload["ebs_hapdap"]["items"] == [234, 235, 237, 238]
    assert payload["ebs_hapdap"]["bogi_counts"] == {
        str(number): 3 for number in (234, 235, 237, 238)
    }
    assert payload["readback"]["passed"] is True
    assert payload["cleanup_receipt"] == {
        "atomic_replacements_completed": 3,
        "temporary_files_retained": 0,
    }
    assert payload["review"]["automated"] == "pass"
    assert left.kice_sheet.read_bytes() == right.kice_sheet.read_bytes()
    assert left.ebs_sheet.read_bytes() == right.ebs_sheet.read_bytes()
    with Image.open(left.kice_sheet) as image:
        assert image.width > image.height
    with Image.open(left.ebs_sheet) as image:
        assert image.width > image.height


def test_profile_regression_evidence_rejects_non_namespace_root(tmp_path: Path) -> None:
    # Given: an arbitrary directory is not an approved namespace boundary.
    invalid = tmp_path / "not-a-namespace"
    invalid.mkdir()

    # When / Then: boundary parsing rejects it before writing evidence.
    try:
        generate_evidence(invalid, tmp_path / "evidence")
    except RuntimeError as error:
        assert "namespace" in str(error)
    else:
        raise AssertionError("invalid namespace was accepted")


def test_profile_input_error_allows_interpreter_traceback_assignment(tmp_path: Path) -> None:
    invalid = tmp_path / "not-a-namespace"
    invalid.mkdir()

    with pytest.raises(ProfileInputError, match="direct child") as raised:
        parse_metadata(invalid)
    raised.value.__traceback__ = None


def test_profile_metadata_rejects_stale_namespace(tmp_path: Path) -> None:
    stale = tmp_path / "run" / "namespaces" / "stale"
    active = stale.parent / "active"
    stale.mkdir(parents=True)
    metadata = {
        "namespace_id": active.name,
        "namespace_root": str(active.resolve()),
        "code_dependency_sha256": "0" * 64,
    }
    (stale.parent.parent / "run-metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )

    with pytest.raises(ProfileInputError, match="does not match active"):
        parse_metadata(stale)
