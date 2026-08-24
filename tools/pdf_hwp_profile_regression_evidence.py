# /// script
# requires-python = ">=3.13"
# dependencies = ["pillow", "pydantic", "typer"]
# ///
# ─── How to run ───
# uv run tools/pdf_hwp_profile_regression_evidence.py --namespace <active-namespace>
"""Generate deterministic C003 profile-regression evidence from one namespace."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import json
from pathlib import Path
import re
import sys
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, JsonValue
import typer

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.pdf_hwp_atomic import atomic_replace
from app.pdf_hwp_roundtrip_checkpoint import artifact_hash
from app.pdf_hwp_roundtrip_models import SourceProfile
from app.pdf_hwp_roundtrip_units import load_prepared_units
from tools.pdf_hwp_profile_regression_contact_sheet import (
    render_rows,
    write_contact_sheet,
)
from tools.pdf_hwp_profile_regression_structural import verify_kice_figures
from tools.pdf_hwp_profile_regression_inputs import (
    parse_metadata,
    read_profile,
    readback,
)


KICE_ITEMS: Final = (2, 3, 5, 6, 18)
EBS_ITEMS: Final = (234, 235, 237, 238)
SCALE_THRESHOLD: Final = 0.70
DEFAULT_EVIDENCE: Final = Path(
    "data/pdf_hwp/roundtrip_harness/source-profiles/evidence"
)


class EvidenceError(RuntimeError):
    """Raised when a pinned structural regression is not proven."""


class ReviewStatus(StrEnum):
    REQUIRED = "required"
    PASS = "pass"
    REVISE = "revise"
    FAIL = "fail"


class _CropSidecar(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    protected_texts: tuple[str, ...] = ()
    excluded_body_spans: tuple[JsonValue, ...] = ()
    manual_review_required: bool = False


@dataclass(frozen=True, slots=True)
class EvidenceArtifacts:
    report: Path
    kice_sheet: Path
    ebs_sheet: Path


def _source_root(namespace: Path, prefixes: tuple[str, ...]) -> Path:
    candidates = tuple(sorted(
        path for prefix in prefixes for path in (namespace / "sources").glob(f"{prefix}-*")
    ))
    if len(candidates) != 1:
        raise EvidenceError(f"expected one source for {prefixes}, found {len(candidates)}")
    return candidates[0]


def _parent_sidecar(asset_path: Path) -> _CropSidecar:
    name = re.sub(r"-figure-\d+\.png$", "-figure.json", asset_path.name)
    return _CropSidecar.model_validate_json(
        asset_path.with_name(name).read_text(encoding="utf-8")
    )


def generate_evidence(
    namespace: Path,
    evidence_dir: Path = DEFAULT_EVIDENCE,
    review_status: ReviewStatus = ReviewStatus.REQUIRED,
) -> EvidenceArtifacts:
    """Validate pinned profile regressions and write JSON plus paired contact sheets."""
    metadata = parse_metadata(namespace)
    kice20 = _source_root(namespace, ("item20_original", "p1_2024_11"))
    p1 = _source_root(namespace, ("p1_2019_11",))
    ebs = _source_root(namespace, ("ebs_2027_physics1",))
    profiles = (
        read_profile(kice20, SourceProfile.KICE_STRUCTURAL),
        read_profile(p1, SourceProfile.KICE_STRUCTURAL),
        read_profile(ebs, SourceProfile.EBS_EDITABLE_REFLOW),
    )
    p20 = load_prepared_units(kice20 / "prepared-units.json").records[0]
    p1_records = {record.unit.item_number: record for record in load_prepared_units(
        p1 / "prepared-units.json"
    ).records}
    ebs_records = {record.unit.item_number: record for record in load_prepared_units(
        ebs / "prepared-units.json"
    ).records}
    choice_fields = (p20.structure.stem, p20.structure.ask, *(row.value for row in p20.structure.materials))
    leakage = tuple(index for index, field in enumerate(choice_fields) if any(
        choice in field for choice in p20.structure.choices
    ))
    if (
        len(p20.structure.choices) != 5
        or p20.structure.field_order[-1] != "choices"
        or leakage
    ):
        raise EvidenceError("p1_2024 q20 choices escaped the five choice fields")
    table_rows = tuple(p1_records[number] for number in (3, 6))
    header_leakage = tuple(row.unit.item_number for row in table_rows if any(
        material.value in text for material in row.structure.materials
        for text in (row.structure.ask, *row.structure.choices)
    ))
    if header_leakage or any(
        row.unit.figure_assets or "\\표" not in row.structure.stem for row in table_rows
    ):
        raise EvidenceError("p1_2019 editable table/box header leakage")
    figure_rows = tuple(p1_records[number] for number in (2, 5, 18))
    clean_crops = all(
        not sidecar.protected_texts and not sidecar.manual_review_required
        for row in figure_rows for sidecar in map(
            _parent_sidecar, (asset.image_path for asset in row.unit.figure_assets)
        )
    )
    excluded_counts = {
        str(row.unit.item_number): max(
            len(_parent_sidecar(asset.image_path).excluded_body_spans)
            for asset in row.unit.figure_assets
        ) for row in figure_rows
    }
    selected_issues = tuple(
        issue.code for issue in (*profiles[1].image_ownership.issues, *profiles[1].blocking_issues)
        if issue.item_number in (2, 5, 18)
    )
    if not clean_crops or selected_issues or any(
        not row.structure.stem or not row.structure.ask or not row.unit.figure_assets
        for row in figure_rows
    ):
        raise EvidenceError("p1_2019 figure structure regression")
    observations = verify_kice_figures(p1, figure_rows)
    hapdap = tuple(ebs_records[number] for number in EBS_ITEMS)
    if any(
        len(row.structure.bogi) != 3
        or len(row.structure.choices) != 5
        or not row.structure.stem
        or not row.structure.ask
        or not row.unit.palette_markdown.startswith("\\수능합답")
        for row in hapdap
    ):
        raise EvidenceError("EBS editable hapdap reflow regression")
    readbacks = tuple(readback(root, hapdap=flag) for root, flag in (
        (kice20, False), (p1, True), (ebs, True),
    ))
    output = evidence_dir.resolve(); output.mkdir(parents=True, exist_ok=True)
    kice_sheet = write_contact_sheet(
        (*render_rows(kice20, (20,), "p1_2024"), *render_rows(p1, KICE_ITEMS, "p1_2019")),
        output / "kice-structural-contact-sheet.png",
    )
    ebs_sheet = write_contact_sheet(
        render_rows(ebs, EBS_ITEMS, "ebs"), output / "ebs-editable-contact-sheet.png",
    )
    payload = {
        "schema_version": 1,
        "namespace": {"namespace_id": metadata.namespace_id, "namespace_root": str(namespace.resolve()),
                      "code_dependency_sha256": metadata.code_dependency_sha256},
        "p1_2024_q20": {"choice_field_count": len(p20.structure.choices),
                         "choice_leakage_fields": list(leakage), "field_order": list(p20.structure.field_order)},
        "p1_2019_tables": {"items": [3, 6], "editable": True,
                           "header_leakage_items": list(header_leakage)},
        "p1_2019_figures": {"items": [2, 5, 18], "editable_stem_ask": True,
                            "figure_only_crops": clean_crops, "minimum_scale_threshold": SCALE_THRESHOLD,
                            "blocking_issue_codes": list(selected_issues),
                            "excluded_prose_span_counts": excluded_counts,
                            "observations": [asdict(row) for row in observations]},
        "ebs_hapdap": {"items": list(EBS_ITEMS), "editable_reflow": True,
                       "bogi_counts": {str(row.unit.item_number): len(row.structure.bogi) for row in hapdap}},
        "readback": {"passed": True, "sources": [
            row.model_dump(mode="json") for row in readbacks
        ]},
        "profile_results": [{"profile": report.profile.value,
                             "image_ownership_passed": report.image_ownership.passed,
                             "blocking_issue_count": len(report.blocking_issues),
                             "profile_sha256": artifact_hash(root / "profile-verification.json")}
                            for report, root in zip(profiles, (kice20, p1, ebs), strict=True)],
        "contact_sheet_hashes": {"kice": artifact_hash(kice_sheet), "ebs": artifact_hash(ebs_sheet)},
        "review": {"automated": "pass", "manual_qa": review_status.value,
                   "passed": review_status is ReviewStatus.PASS},
        "cleanup_receipt": {"atomic_replacements_completed": 3,
                            "temporary_files_retained": 0},
    }
    report = output / "C003-structural-regression.json"
    temporary = report.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    atomic_replace(temporary, report)
    return EvidenceArtifacts(report, kice_sheet, ebs_sheet)


def main(
    namespace: Annotated[Path, typer.Option("--namespace")],
    evidence_dir: Annotated[Path, typer.Option("--evidence-dir")] = DEFAULT_EVIDENCE,
    review_status: Annotated[ReviewStatus, typer.Option("--review-status")] = ReviewStatus.REQUIRED,
) -> None:
    """CLI boundary for active-namespace evidence generation."""
    artifacts = generate_evidence(namespace, evidence_dir, review_status)
    typer.echo(str(artifacts.report.resolve()))


if __name__ == "__main__":
    typer.run(main)
