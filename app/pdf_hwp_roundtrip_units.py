from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from .pdf_hwp_final_figure_contract import (
    FinalFigureContract,
    FinalFigureReview,
    reconcile_final_figure_contract,
)
from .pdf_hwp_graphical_choices import draft_review_detail
from .pdf_hwp_hwp_preflight import preflight_unit
from .pdf_hwp_object_segmentation import EmptyFigureSelectionError
from .pdf_hwp_pipeline import build_editable_draft
from .pdf_hwp_pipeline_models import (
    ConversionUnit,
    CropArtifact,
    DetectedItem,
    DraftExtractionError,
    FigureAsset,
    FigureAssetMetadata,
    GraphicalChoiceAsset,
    GraphicalChoiceAssetMetadata,
    InvalidCropError,
    LayoutStyle,
    ManualReviewRequiredError,
    UnsupportedDraftLayoutError,
)
from .pdf_hwp_roundtrip_crop_audit import (
    BBox,
    CropAuditIssue,
    CropSourceRequest,
    audit_crop_geometry,
    read_crop_geometry,
)
from .pdf_hwp_roundtrip_models import SourceProfile
from .pdf_hwp_roundtrip_structure import PreparedStructureError, parse_prepared_structure
from .pdf_hwp_roundtrip_unit_store import (
    FailureCode,
    ItemFailure,
    PreparationPayload,
    PreparationResult,
    PreparedUnitRecord,
    load_prepared_units,
    write_prepared_units,
)


@dataclass(frozen=True, slots=True)
class CropGateError(RuntimeError):
    item_number: int
    issue: CropAuditIssue

    def __str__(self) -> str:
        return f"item {self.item_number} failed crop gate: {self.issue.value}"


class _CropSidecar(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    page_number: int = Field(ge=1)
    item_number: int = Field(ge=1)
    crop_bbox: BBox = Field(
        validation_alias=AliasChoices("image_bbox", "bbox", "source_bbox")
    )
    component_bboxes: tuple[BBox, ...] = ()


@dataclass(frozen=True, slots=True)
class _PreparationContext:
    source_pdf: Path
    output_root: Path
    layout_style: LayoutStyle
    profile: SourceProfile


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _item_hash(item: DetectedItem) -> str:
    payload = json.dumps(
        [item.page_number, item.item_number, item.column, item.bbox, item.source_text],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _figure_assets(
    artifacts: tuple[CropArtifact, ...],
) -> tuple[FigureAsset, ...]:
    return tuple(FigureAsset(
        artifact.image_path.resolve(),
        FigureAssetMetadata.model_validate_json(
            artifact.provenance_path.read_text(encoding="utf-8")
        ),
    ) for artifact in artifacts)


def _audit_sidecars(
    source_pdf: Path,
    item: DetectedItem,
    editable_text: str,
    semantic_selection: bool,
    sidecars: tuple[Path, ...],
) -> None:
    for sidecar_path in sidecars:
        sidecar = _CropSidecar.model_validate_json(
            sidecar_path.read_text(encoding="utf-8")
        )
        component_bboxes = sidecar.component_bboxes
        if not component_bboxes:
            parent_name = re.sub(
                r"-figure-\d+\.json$", "-figure.json", sidecar_path.name,
            )
            parent_sidecar = sidecar_path.with_name(parent_name)
            if parent_sidecar != sidecar_path and parent_sidecar.is_file():
                try:
                    component_bboxes = _CropSidecar.model_validate_json(
                        parent_sidecar.read_text(encoding="utf-8")
                    ).component_bboxes
                except (OSError, ValidationError):
                    component_bboxes = ()
        crop_bboxes = component_bboxes or (sidecar.crop_bbox,)
        for crop_bbox in crop_bboxes:
            geometry = read_crop_geometry(CropSourceRequest(
                source_pdf,
                sidecar.page_number,
                item.item_number,
                item.bbox,
                crop_bbox,
                editable_text,
                semantic_selection,
            ))
            audit = audit_crop_geometry(geometry)
            if audit.issues:
                raise CropGateError(item.item_number, audit.issues[0])


def _choice_assets(artifacts: tuple[CropArtifact, ...]) -> tuple[GraphicalChoiceAsset, ...]:
    return tuple(GraphicalChoiceAsset(
        artifact.image_path.resolve(),
        GraphicalChoiceAssetMetadata.model_validate_json(
            artifact.provenance_path.read_text(encoding="utf-8")
        ),
    ) for artifact in artifacts)


def _prepare_one(context: _PreparationContext, item: DetectedItem) -> PreparedUnitRecord | ItemFailure:
    item_hash = _item_hash(item)
    item_root = context.output_root / "assets" / f"item-{item.item_number:03d}"
    try:
        draft = build_editable_draft(
            context.source_pdf,
            item,
            item_root,
            layout_style=context.layout_style,
        )
        detail = draft_review_detail(item.item_number, draft.palette_markdown, draft.graphical_choice_assets)
        if detail is not None:
            raise ManualReviewRequiredError(item.item_number, detail)
        contract = reconcile_final_figure_contract(
            item.item_number, draft.palette_markdown, draft.figure_assets,
            source_item=item if context.profile is SourceProfile.KICE_STRUCTURAL else None,
        )
        match contract:
            case FinalFigureReview(detail=review_detail):
                raise ManualReviewRequiredError(item.item_number, review_detail)
            case FinalFigureContract(palette_markdown=markdown):
                if draft.figure_assets:
                    sidecars = tuple(
                        artifact.provenance_path for artifact in draft.figure_assets
                    )
                elif draft.figure_asset is not None:
                    sidecars = (draft.figure_asset.provenance_path,)
                else:
                    sidecars = tuple(sorted(item_root.glob(
                        f"page-*-item-{item.item_number}-figure.json"
                    )))
                _audit_sidecars(
                    context.source_pdf,
                    item,
                    markdown,
                    bool(draft.figure_assets),
                    sidecars,
                )
                unit = ConversionUnit(
                    item.item_number,
                    markdown,
                    _figure_assets(draft.figure_assets),
                    _choice_assets(draft.graphical_choice_assets),
                )
            case unreachable:
                assert_never(unreachable)
        preflight_unit(unit, context.layout_style)
        structure = parse_prepared_structure(
            unit, item.page_number, item.bbox, context.layout_style,
        )
        return PreparedUnitRecord(unit, item_hash, structure)
    except CropGateError as error:
        return ItemFailure(
            item.item_number, FailureCode(error.issue.value), str(error), item_hash,
        )
    except PreparedStructureError as error:
        return ItemFailure(
            item.item_number, FailureCode(error.code.value), str(error), item_hash,
        )
    except (
        DraftExtractionError,
        UnsupportedDraftLayoutError,
        InvalidCropError,
        EmptyFigureSelectionError,
        ManualReviewRequiredError,
        ValidationError,
        OSError,
    ) as error:
        return ItemFailure(
            item.item_number,
            FailureCode.PREPARATION_ERROR,
            f"{type(error).__name__}: {error}",
            item_hash,
        )


def prepare_units(
    source_pdf: Path,
    selected: tuple[DetectedItem, ...],
    output_root: Path,
    layout_style: LayoutStyle,
    profile: SourceProfile = SourceProfile.KICE_STRUCTURAL,
) -> PreparationResult:
    """Prepare selected items independently and atomically persist all outcomes."""
    context = _PreparationContext(
        source_pdf.resolve(), output_root.resolve(), layout_style, profile,
    )
    outcomes = tuple(
        _prepare_one(context, item)
        for item in sorted(selected, key=lambda value: (value.item_number, value.page_number))
    )
    unit_records: list[PreparedUnitRecord] = []
    failure_records: list[ItemFailure] = []
    for outcome in outcomes:
        match outcome:
            case PreparedUnitRecord():
                unit_records.append(outcome)
            case ItemFailure():
                failure_records.append(outcome)
            case unreachable:
                assert_never(unreachable)
    return write_prepared_units(
        context.output_root / "prepared-units.json",
        PreparationPayload(
            context.source_pdf,
            _sha256(context.source_pdf),
            layout_style,
            tuple(unit_records),
            tuple(failure_records),
            context.profile,
        ),
    )


__all__ = [
    "FailureCode",
    "ItemFailure",
    "PreparationResult",
    "load_prepared_units",
    "prepare_units",
]
