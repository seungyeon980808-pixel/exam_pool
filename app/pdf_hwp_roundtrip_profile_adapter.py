"""Adapt backend verification primitives to the source-profile contract."""
from __future__ import annotations

from .pdf_hwp_roundtrip_item_alignment import ItemAlignmentResult
from .pdf_hwp_roundtrip_pdf_contract import GeneratedPdfContractResult
from .pdf_hwp_roundtrip_profile import (
    ImageOwnershipResult,
    ObservedProfileIssue,
    ProfileVerificationRecord,
    ProfileVerificationRequest,
    VisualMetric,
    classify_profile,
)
from .pdf_hwp_roundtrip_readback import PdfReadbackReport
from .pdf_hwp_roundtrip_models import SourceProfile
from .pdf_hwp_roundtrip_unit_store import PreparationResult


def build_profile_verification(
    prepared: PreparationResult,
    pdf_readback: PdfReadbackReport,
    contract: GeneratedPdfContractResult,
    alignment: ItemAlignmentResult,
    profile: SourceProfile,
    image_ownership: ImageOwnershipResult,
) -> ProfileVerificationRecord:
    """Preserve located structural evidence while treating visual metrics diagnostically."""
    return classify_profile(ProfileVerificationRequest(
        profile=profile,
        records=prepared.records,
        preparation_failures=prepared.item_failures,
        generated_count=len(contract.items),
        editability_issues=(),
        hwp_issues=(),
        pdf_issues=tuple(ObservedProfileIssue(
            issue.code.value, None, issue.detail,
        ) for issue in pdf_readback.issues),
        semantic_issues=tuple(ObservedProfileIssue(
            issue.code.value, getattr(issue, "item_number", None),
            getattr(issue, "detail", issue.code.value),
        ) for issue in contract.issues),
        alignment_issues=tuple(ObservedProfileIssue(
            issue.value, None, issue.value,
        ) for issue in alignment.issues) + (() if prepared.profile is profile else (
            ObservedProfileIssue(
                "source_profile_mismatch", None,
                f"prepared {prepared.profile.value} != planned {profile.value}",
            ),
        )),
        visual_metrics=tuple(VisualMetric(
            item.item_number, item.pixel_mae, item.edge_mae,
        ) for item in alignment.comparisons),
        image_ownership=image_ownership,
    ))


__all__ = ["build_profile_verification"]
