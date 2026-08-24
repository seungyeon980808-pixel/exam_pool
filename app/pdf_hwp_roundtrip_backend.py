"""Real deterministic-folder adapter for the resumable round-trip runner."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz
from pydantic import ValidationError

from .pdf_hwp_pipeline import typeset_conversion
from .pdf_hwp_pipeline_models import (
    ConversionUnit,
    ConversionRequest,
    ConversionResourceLockedError,
    ConversionTypesetError,
    DocumentTypesetter,
    EmptyConversionError,
    InvalidSourcePdfError,
    LayoutStyle,
)
from .pdf_hwp_kice_profile_verifier import KiceStructuralImageOwnershipVerifier
from .pdf_hwp_roundtrip_backend_store import (
    ConversionPaths,
    VerificationItem,
    VerificationRecord,
    load_conversion_paths,
    write_conversion_paths,
    write_verification,
)
from .pdf_hwp_roundtrip_item_alignment import (
    ItemAlignmentRequest,
    align_and_compare_items,
)
from .pdf_hwp_roundtrip_models import SourceProfile, SourceRoute, WorkflowStage
from .pdf_hwp_roundtrip_pdf_contract import (
    generated_pdf_contract_request,
    inspect_generated_pdf_contract,
)
from .pdf_hwp_roundtrip_profile import (
    ImageOwnershipResult, ImageOwnershipVerifier, MetadataImageOwnershipVerifier,
    write_profile_verification,
)
from .pdf_hwp_roundtrip_profile_adapter import build_profile_verification
from .pdf_hwp_roundtrip_readback import (
    HwpExpectations,
    IssueCode,
    PdfExpectations,
    inspect_hwp,
    inspect_pdf,
)
from .pdf_hwp_roundtrip_router import route_source
from .pdf_hwp_roundtrip_runner import (
    BackendStageError,
    ExtractOutcome,
    HwpOutcome,
    PdfOutcome,
    RouteOutcome,
    SourceInput,
)
from .pdf_hwp_roundtrip_source import select_detected_items
from .pdf_hwp_roundtrip_units import load_prepared_units, prepare_units


def _hapdap_numbers(units: tuple[ConversionUnit, ...]) -> frozenset[int]:
    return frozenset(unit.item_number for unit in units if "합답" in unit.palette_markdown)


def _complete_bogi_table_count(tables: tuple[tuple[str, ...], ...]) -> int:
    complete = 0
    for cells in tables:
        text = "\n".join(cells)
        compact = "".join(text.split())
        if "<보기>" in compact and all(marker in text for marker in ("ㄱ", "ㄴ", "ㄷ")):
            complete += 1
    return complete


@dataclass(frozen=True, slots=True)
class BackendSourcePlan:
    original_path: Path
    pipeline_pdf: Path
    selected_numbers: tuple[int, ...] | None
    output_dir: Path
    header_subject: str
    profile: SourceProfile
    layout_style: LayoutStyle


@dataclass(frozen=True, slots=True)
class RealRoundTripBackend:
    """Bind runner stages to existing real preparation, conversion, and QA primitives."""

    plans: tuple[BackendSourcePlan, ...]
    typesetter: DocumentTypesetter | None = None
    image_ownership_verifier: ImageOwnershipVerifier = MetadataImageOwnershipVerifier()

    def _plan(self, source: SourceInput, stage: WorkflowStage) -> BackendSourcePlan:
        resolved = source.path.resolve()
        plan = next(
            (candidate for candidate in self.plans if candidate.original_path.resolve() == resolved), None,
        )
        if plan is None:
            raise BackendStageError(stage, "plan_not_found", str(resolved))
        return plan

    def route(self, source: SourceInput) -> RouteOutcome:
        self._plan(source, WorkflowStage.ROUTE)
        return RouteOutcome(route_source(source.facts))

    def extract(self, source: SourceInput, route: SourceRoute) -> ExtractOutcome:
        plan = self._plan(source, WorkflowStage.EXTRACT)
        try:
            selection = select_detected_items(plan.pipeline_pdf, plan.selected_numbers)
        except (InvalidSourcePdfError, OSError) as exc:
            raise BackendStageError(WorkflowStage.EXTRACT, "source_detection_failed", str(exc)) from exc
        if selection.issues:
            detail = ",".join(
                f"{issue.kind.value}:{issue.item_number}:{issue.occurrences}"
                for issue in selection.issues
            )
            raise BackendStageError(WorkflowStage.EXTRACT, "selection_failed", detail)
        try:
            prepared = prepare_units(
                plan.pipeline_pdf, selection.items, plan.output_dir, plan.layout_style,
                plan.profile,
            )
        except (InvalidSourcePdfError, ValidationError, OSError) as exc:
            raise BackendStageError(WorkflowStage.EXTRACT, "preparation_failed", str(exc)) from exc
        if not prepared.prepared_units:
            detail = ",".join(
                f"{failure.item_number}:{failure.code.value}" for failure in prepared.item_failures
            )
            raise BackendStageError(WorkflowStage.EXTRACT, "no_prepared_units", detail)
        return ExtractOutcome(prepared.manifest_path)

    def typeset(self, source: SourceInput, route: SourceRoute) -> HwpOutcome:
        plan = self._plan(source, WorkflowStage.HWP)
        try:
            prepared = load_prepared_units(plan.output_dir / "prepared-units.json")
            asset_dirs = tuple(sorted({
                asset.image_path.parent.resolve()
                for unit in prepared.prepared_units
                for asset in (*unit.figure_assets, *unit.graphical_choice_assets)
            }))
            result = typeset_conversion(
                ConversionRequest(
                    f"{plan.original_path.stem}-roundtrip",
                    prepared.prepared_units,
                    plan.output_dir / "conversion",
                    plan.layout_style,
                    asset_dirs,
                    plan.header_subject,
                ),
                typesetter=self.typesetter,
            )
        except (
            ConversionResourceLockedError,
            ConversionTypesetError,
            EmptyConversionError,
            ValidationError,
            OSError,
        ) as exc:
            raise BackendStageError(WorkflowStage.HWP, "typeset_failed", str(exc)) from exc
        conversion_manifest = write_conversion_paths(
            plan.output_dir / "backend-conversion.json",
            ConversionPaths(result.hwp_path, result.pdf_path, result.rendered_pages),
        )
        hapdap_count = len(_hapdap_numbers(prepared.prepared_units))
        readback = inspect_hwp(
            result.hwp_path, HwpExpectations(hapdap_count > 0, hapdap_count > 0, True, True),
        )
        if readback.issues:
            detail = ",".join(f"{issue.code.value}:{issue.detail}" for issue in readback.issues)
            raise BackendStageError(WorkflowStage.HWP, readback.issues[0].code.value, detail)
        if (
            readback.snapshot is not None
            and _complete_bogi_table_count(readback.snapshot.rhwp_table_cells) < hapdap_count
        ):
            observed = _complete_bogi_table_count(readback.snapshot.rhwp_table_cells)
            detail = f"complete_bogi_tables:{observed}<hapdap_units:{hapdap_count}"
            raise BackendStageError(WorkflowStage.HWP, IssueCode.MISSING_BOGI_BOX.value, detail)
        return HwpOutcome(result.hwp_path, (result.pdf_path, conversion_manifest))

    def verify(self, source: SourceInput, route: SourceRoute) -> PdfOutcome:
        plan = self._plan(source, WorkflowStage.PDF)
        try:
            prepared = load_prepared_units(plan.output_dir / "prepared-units.json")
            conversion = load_conversion_paths(plan.output_dir / "backend-conversion.json")
        except (ValidationError, OSError) as exc:
            raise BackendStageError(WorkflowStage.PDF, "verification_state_unreadable", str(exc)) from exc
        try:
            with fitz.open(conversion.pdf_path) as generated_document:
                generated_page_count = generated_document.page_count
        except (fitz.FileDataError, FileNotFoundError, OSError) as exc:
            raise BackendStageError(WorkflowStage.PDF, "unreadable_pdf", str(exc)) from exc
        if generated_page_count <= 0:
            raise BackendStageError(WorkflowStage.PDF, "unreadable_pdf", "generated PDF has zero pages")
        pdf_readback = inspect_pdf(conversion.pdf_path, PdfExpectations(generated_page_count))
        if pdf_readback.snapshot is None:
            detail = ",".join(issue.code.value for issue in pdf_readback.issues)
            raise BackendStageError(WorkflowStage.PDF, "unreadable_pdf", detail)
        selected_numbers = tuple(unit.item_number for unit in prepared.prepared_units)
        hapdap_numbers = _hapdap_numbers(prepared.prepared_units)
        contract = inspect_generated_pdf_contract(generated_pdf_contract_request(
            conversion.pdf_path, prepared.prepared_units,
        ))
        try:
            alignment = align_and_compare_items(ItemAlignmentRequest(
                plan.pipeline_pdf,
                conversion.pdf_path,
                selected_numbers,
                plan.output_dir / "verification-evidence",
                120,
            ))
        except (InvalidSourcePdfError, OSError) as exc:
            raise BackendStageError(WorkflowStage.PDF, "alignment_failed", str(exc)) from exc
        semantic_by_item = {
            item.item_number: tuple(issue.code.value for issue in item.issues)
            for item in contract.items
        }
        semantic_issues = tuple(issue.code.value for issue in contract.issues)
        document_issues = tuple(
            [issue.code.value for issue in pdf_readback.issues]
            + [issue.value for issue in alignment.issues]
            + list(semantic_issues)
        )
        record = VerificationRecord(
            conversion.pdf_path,
            document_issues,
            tuple(VerificationItem(
                item.item_number, item.pixel_mae, item.edge_mae,
                tuple(issue.value for issue in item.issues)
                + semantic_by_item.get(item.item_number, ()),
            ) for item in alignment.comparisons),
            prepared.item_failures,
        )
        verification_path = write_verification(plan.output_dir / "verification.json", record)
        image_ownership = self.image_ownership_verifier.verify(prepared.records)
        if plan.profile is SourceProfile.KICE_STRUCTURAL:
            generated_items = tuple(pair.generated for pair in alignment.pairs)
            structural = KiceStructuralImageOwnershipVerifier(
                conversion.pdf_path, generated_items,
            ).verify(prepared.records)
            combined = (*image_ownership.issues, *structural.issues)
            image_ownership = ImageOwnershipResult(
                not combined, combined, structural.verifier,
            )
        profile_record = build_profile_verification(
            prepared, pdf_readback, contract, alignment, plan.profile,
            image_ownership,
        )
        profile_path = write_profile_verification(
            plan.output_dir / "profile-verification.json", profile_record,
        )
        if profile_record.blocking_issues:
            issue = profile_record.blocking_issues[0]
            detail = ",".join(row.code for row in profile_record.blocking_issues)
            raise BackendStageError(WorkflowStage.PDF, issue.code, detail)
        return PdfOutcome(
            conversion.pdf_path, (verification_path, profile_path, alignment.contact_sheet),
        )
