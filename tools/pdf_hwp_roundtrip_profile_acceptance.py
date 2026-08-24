"""Emit G002 C002 profile-fault and policy-aware resume evidence."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from tempfile import gettempdir, TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.pdf_hwp_roundtrip_models import SourceProfile
from app.pdf_hwp_atomic import atomic_replace
from app.pdf_hwp_roundtrip_profile import (
    ImageOwnershipResult,
    ObservedProfileIssue,
    ProfileVerificationRequest,
    classify_profile,
)
from app.pdf_hwp_roundtrip_structure import (
    AssetRole,
    PreparedAssetRef,
    PreparedItemStructure,
)
from app.pdf_hwp_roundtrip_unit_store import (
    FailureCode,
    ItemFailure,
    PreparedUnitRecord,
)
from app.pdf_hwp_pipeline_models import ConversionUnit
from tools.pdf_hwp_roundtrip_acceptance_evidence import generate_c002
from tools.pdf_hwp_roundtrip_harness_contract import (
    Candidate,
    SourceGroup,
    candidate_selection_contract,
    create_run_namespace,
)


@dataclass(frozen=True, slots=True)
class ProfileC002Evidence:
    transcript: Path
    proof: Path


def _atomic_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    atomic_replace(temporary, path)
    return path


def _record(*, wrong_owner: bool) -> PreparedUnitRecord:
    item_number = 7
    structure = PreparedItemStructure(
        number=item_number,
        source_page=1,
        item_bbox=(10.0, 20.0, 300.0, 500.0),
        stem="완전한 편집 지문이다.",
        materials=(),
        ask="이에 대한 설명으로 옳은 것은?",
        bogi=(),
        choices=("선지 1", "선지 2", "선지 3", "선지 4", "선지 5"),
        asset_refs=(PreparedAssetRef(
            role=AssetRole.MATERIAL,
            asset_path=Path("figure.png"),
            slot_name="사진1",
            owner_item_number=8 if wrong_owner else item_number,
            source_page=1,
            asset_hash="a" * 64,
            order=1,
        ),),
    )
    unit = ConversionUnit(item_number, "fixture")
    return PreparedUnitRecord(unit, "b" * 64, structure)


def _classified(profile: SourceProfile) -> dict[str, object]:
    record = _record(wrong_owner=True)
    request = ProfileVerificationRequest(
        profile=profile,
        records=(record,),
        preparation_failures=(ItemFailure(
            8, FailureCode.MERGED_FIELDS,
            "fixture stem and ask occupy one field", "c" * 64,
        ),),
        generated_count=1,
        editability_issues=(
            ObservedProfileIssue("whole_item_rasterized", 9, "image-only item"),
            ObservedProfileIssue("missing_bogi_box", 10, "claims are outside a table"),
            ObservedProfileIssue("missing_bogi_claims", 10, "ㄱ/ㄴ/ㄷ are incomplete"),
        ),
        pdf_issues=(),
        semantic_issues=(
            ObservedProfileIssue("fifth_choice_wrapped", 11, "⑤ tail moved to next line"),
            ObservedProfileIssue("item_boundary_spill", 12, "next leader entered item"),
        ),
        alignment_issues=(
            ObservedProfileIssue("visual_mismatch", 7, "font differs"),
            ObservedProfileIssue("geometry_delta", 7, "margin differs"),
        ),
        visual_metrics=(),
        image_ownership=ImageOwnershipResult.from_records((record,)),
    )
    result = classify_profile(request)
    return {
        "passed": not result.blocking_issues,
        "blocking_codes": sorted({issue.code for issue in result.blocking_issues}),
        "blocking_items": sorted({
            issue.item_number for issue in result.blocking_issues
            if issue.item_number is not None
        }),
        "diagnostic_codes": sorted({issue.code for issue in result.diagnostics}),
    }


def _harmless(profile: SourceProfile) -> bool:
    record = _record(wrong_owner=False)
    result = classify_profile(ProfileVerificationRequest(
        profile=profile,
        records=(record,),
        preparation_failures=(),
        generated_count=1,
        editability_issues=(),
        pdf_issues=(),
        semantic_issues=(),
        alignment_issues=(
            ObservedProfileIssue("visual_mismatch", 7, "font differs"),
            ObservedProfileIssue("geometry_delta", 7, "margin differs"),
        ),
        visual_metrics=(),
        image_ownership=ImageOwnershipResult.from_records((record,)),
    ))
    return not result.blocking_issues and len(result.diagnostics) == 2


def _selection_isolation(root: Path) -> tuple[bool, dict[str, str]]:
    manifest = root / "manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    digest = "d" * 64
    base = dict(
        source_id="same-source",
        group=SourceGroup.KICE,
        path=root / "same.pdf",
        sha256=digest,
        selected_numbers=(1,),
        header_subject="물리학Ⅰ",
    )
    ebs = Candidate(**base, profile=SourceProfile.EBS_EDITABLE_REFLOW)
    kice = Candidate(**base, profile=SourceProfile.KICE_STRUCTURAL)
    ebs_namespace, ebs_meta = create_run_namespace(
        root / "run", manifest, candidate_selection_contract((ebs,)),
    )
    kice_namespace, kice_meta = create_run_namespace(
        root / "run", manifest, candidate_selection_contract((kice,)),
    )
    hashes = {
        "ebs": str(ebs_meta["selection_sha256"]),
        "kice": str(kice_meta["selection_sha256"]),
    }
    return ebs_namespace.root != kice_namespace.root and hashes["ebs"] != hashes["kice"], hashes


def generate_profile_c002(evidence_dir: Path) -> ProfileC002Evidence:
    output = evidence_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    legacy = generate_c002(output)
    resume = json.loads(legacy.proof.read_text(encoding="utf-8"))
    with TemporaryDirectory(prefix=".profile-c002-") as temporary:
        temporary_root = Path(temporary).resolve()
        contained = temporary_root.is_relative_to(Path(gettempdir()).resolve())
        isolated, selection_hashes = _selection_isolation(temporary_root)
    exists_after = temporary_root.exists()
    payload = {
        "schema_version": 1,
        "profiles": {
            profile.value: _classified(profile) for profile in SourceProfile
        },
        "harmless_geometry_accepted": all(_harmless(profile) for profile in SourceProfile),
        "profile_namespace_isolated": isolated,
        "profile_selection_sha256": selection_hashes,
        "resume": resume,
        "cleanup_receipt": {
            "temporary_root_contained": contained,
            "removed": not exists_after,
            "exists_after": exists_after,
        },
    }
    if not payload["harmless_geometry_accepted"] or not isolated or exists_after:
        raise RuntimeError("profile C002 acceptance invariant failed")
    proof = _atomic_text(
        output / "adversarial-proof.json",
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    transcript = _atomic_text(
        output / "C002-profile-adversarial.txt",
        "\n".join((
            "G002 C002 PROFILE ADVERSARIAL PROOF",
            *(f"{name} blocking={','.join(row['blocking_codes'])} diagnostics={','.join(row['diagnostic_codes'])}"
              for name, row in payload["profiles"].items()),
            f"harmless_geometry_accepted={str(payload['harmless_geometry_accepted']).lower()}",
            f"profile_namespace_isolated={str(isolated).lower()}",
            f"resume_reused_extract={str(resume['resume_reused_extract']).lower()}",
            f"cleanup={payload['cleanup_receipt']}",
            "",
        )),
    )
    return ProfileC002Evidence(transcript, proof)


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        generate_profile_c002(arguments.evidence_dir)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"profile C002 evidence failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
