"""Atomic persistence for restartable prepared conversion units."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .pdf_hwp_pipeline_models import (
    ConversionUnit,
    FigureAsset,
    FigureAssetMetadata,
    GraphicalChoiceAsset,
    GraphicalChoiceAssetMetadata,
    LayoutStyle,
)
from .pdf_hwp_roundtrip_models import SourceProfile
from .pdf_hwp_roundtrip_structure import PreparedItemStructure


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class FailureCode(StrEnum):
    CROP_CONTAMINATION = "crop_contamination"
    CROP_CLIPPING = "crop_clipping"
    SOURCE_BOUNDARY_SPILL = "source_boundary_spill"
    PREPARATION_ERROR = "preparation_error"
    INVALID_STRUCTURE = "invalid_structure"
    MERGED_FIELDS = "merged_fields"
    MISSING_STEM = "missing_stem"
    MISSING_BOGI = "missing_bogi"
    MISSING_CHOICES = "missing_choices"
    ASK_CHOICE_OVERLAP = "ask_choice_overlap"


@dataclass(frozen=True, slots=True)
class ItemFailure:
    item_number: int
    code: FailureCode
    detail: str
    source_item_hash: str


@dataclass(frozen=True, slots=True)
class PreparedUnitRecord:
    unit: ConversionUnit
    source_item_hash: str
    structure: PreparedItemStructure


@dataclass(frozen=True, slots=True)
class PreparationResult:
    manifest_path: Path
    prepared_units: tuple[ConversionUnit, ...]
    item_failures: tuple[ItemFailure, ...]
    records: tuple[PreparedUnitRecord, ...]
    profile: SourceProfile


@dataclass(frozen=True, slots=True)
class PreparationPayload:
    source_pdf: Path
    source_sha256: str
    layout_style: LayoutStyle
    units: tuple[PreparedUnitRecord, ...]
    failures: tuple[ItemFailure, ...]
    profile: SourceProfile = SourceProfile.KICE_STRUCTURAL


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _StoredFigure(_FrozenModel):
    image_path: Path
    metadata: FigureAssetMetadata


class _StoredChoice(_FrozenModel):
    image_path: Path
    metadata: GraphicalChoiceAssetMetadata


class _StoredUnit(_FrozenModel):
    item_number: int = Field(ge=1)
    source_item_hash: Sha256
    palette_markdown: str
    figure_assets: tuple[_StoredFigure, ...]
    graphical_choice_assets: tuple[_StoredChoice, ...]
    structure: PreparedItemStructure

    @model_validator(mode="after")
    def matching_structure_identity(self) -> _StoredUnit:
        if self.structure.number != self.item_number:
            msg = "stored structure number must match item_number"
            raise ValueError(msg)
        return self


class _StoredFailure(_FrozenModel):
    item_number: int = Field(ge=1)
    source_item_hash: Sha256
    code: FailureCode
    detail: str


class _PreparedManifest(_FrozenModel):
    schema_version: Literal[2]
    source_pdf: Path
    source_sha256: Sha256
    layout_style: LayoutStyle
    profile: SourceProfile
    units: tuple[_StoredUnit, ...]
    failures: tuple[_StoredFailure, ...]

    @model_validator(mode="after")
    def unique_item_outcomes(self) -> "_PreparedManifest":
        numbers = tuple(row.item_number for row in (*self.units, *self.failures))
        if len(numbers) != len(set(numbers)):
            raise DuplicatePreparedItemError(numbers)
        return self


@dataclass(frozen=True, slots=True)
class DuplicatePreparedItemError(ValueError):
    item_numbers: tuple[int, ...]

    def __str__(self) -> str:
        return f"prepared manifest contains duplicate item outcomes: {self.item_numbers}"


def _stored_unit(record: PreparedUnitRecord) -> _StoredUnit:
    unit = record.unit
    return _StoredUnit(
        item_number=unit.item_number,
        source_item_hash=record.source_item_hash,
        palette_markdown=unit.palette_markdown,
        figure_assets=tuple(
            _StoredFigure(image_path=asset.image_path, metadata=asset.metadata)
            for asset in unit.figure_assets
        ),
        graphical_choice_assets=tuple(
            _StoredChoice(image_path=asset.image_path, metadata=asset.metadata)
            for asset in unit.graphical_choice_assets
        ),
        structure=record.structure,
    )


def write_prepared_units(path: Path, payload: PreparationPayload) -> PreparationResult:
    """Atomically persist prepared units and return their public outcome."""
    manifest = _PreparedManifest(
        schema_version=2,
        source_pdf=payload.source_pdf,
        source_sha256=payload.source_sha256,
        layout_style=payload.layout_style,
        profile=payload.profile,
        units=tuple(_stored_unit(record) for record in payload.units),
        failures=tuple(_StoredFailure(
            item_number=failure.item_number,
            source_item_hash=failure.source_item_hash,
            code=failure.code,
            detail=failure.detail,
        ) for failure in payload.failures),
    )
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return PreparationResult(
        target, tuple(record.unit for record in payload.units), payload.failures,
        payload.units, payload.profile,
    )


def load_prepared_units(manifest_path: Path) -> PreparationResult:
    """Parse persisted paths and metadata back into conversion units."""
    target = manifest_path.resolve()
    manifest = _PreparedManifest.model_validate_json(target.read_text(encoding="utf-8"))
    records = tuple(PreparedUnitRecord(
        ConversionUnit(
            row.item_number,
            row.palette_markdown,
            tuple(FigureAsset(asset.image_path, asset.metadata) for asset in row.figure_assets),
            tuple(GraphicalChoiceAsset(asset.image_path, asset.metadata) for asset in row.graphical_choice_assets),
        ),
        row.source_item_hash,
        row.structure,
    ) for row in manifest.units)
    failures = tuple(ItemFailure(
        row.item_number, row.code, row.detail, row.source_item_hash,
    ) for row in manifest.failures)
    return PreparationResult(
        target, tuple(record.unit for record in records), failures, records, manifest.profile,
    )
