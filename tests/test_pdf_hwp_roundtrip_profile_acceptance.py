from __future__ import annotations

import json
from pathlib import Path

from tools.pdf_hwp_roundtrip_profile_acceptance import generate_profile_c002


def test_profile_c002_classifies_faults_and_proves_policy_aware_resume(
    tmp_path: Path,
) -> None:
    result = generate_profile_c002(tmp_path / "evidence")

    proof = json.loads(result.proof.read_text(encoding="utf-8"))
    assert result.transcript.stat().st_size > 0
    expected = {
        "merged_fields", "fifth_choice_wrapped", "missing_bogi_box",
        "missing_bogi_claims", "wrong_image_ownership",
        "item_boundary_spill", "whole_item_rasterized",
    }
    for profile in ("ebs_editable_reflow", "kice_structural"):
        row = proof["profiles"][profile]
        assert set(row["blocking_codes"]) >= expected
        assert row["diagnostic_codes"] == ["geometry_delta", "visual_mismatch"]
        assert row["passed"] is False
    assert proof["harmless_geometry_accepted"] is True
    assert proof["resume"]["resume_reused_extract"] is True
    assert proof["resume"]["prior_checkpoint_unchanged"] is True
    assert proof["resume"]["cleanup_receipt"]["exists_after"] is False
    assert proof["profile_namespace_isolated"] is True
    assert proof["profile_selection_sha256"]["ebs"] != proof["profile_selection_sha256"]["kice"]
    assert proof["cleanup_receipt"] == {
        "exists_after": False,
        "removed": True,
        "temporary_root_contained": True,
    }
