# /// script
# requires-python = ">=3.13"
# dependencies = ["pillow", "pymupdf", "pydantic"]
# ///
# ─── How to run ───
# Imported by pdf_hwp_profile_regression_evidence.py; it has no standalone CLI.
"""Generated-PDF ownership, placement, and scale evidence for pinned KICE items."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.pdf_hwp_kice_structural import (
    FigurePlacement,
    KiceFigureExpectation,
    KiceStructuralRequest,
    PreparedVisualKind,
    inspect_kice_figure_structure,
)
from app.pdf_hwp_roundtrip_generated_detection import detect_generated_items
from app.pdf_hwp_roundtrip_unit_store import PreparedUnitRecord


class _ConversionPaths(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    pdf_path: Path


class StructuralEvidenceError(RuntimeError):
    def __init__(self, details: tuple[str, ...]) -> None:
        self.details = details
        super().__init__(",".join(details))

    def __str__(self) -> str:
        return ",".join(self.details)


@dataclass(frozen=True, slots=True)
class FigureObservation:
    item_number: int
    expected_count: int
    observed_count: int
    placement: str
    minimum_scale: float


def verify_kice_figures(
    root: Path, records: tuple[PreparedUnitRecord, ...],
) -> tuple[FigureObservation, ...]:
    """Observe the three pinned figures directly in the generated PDF."""
    paths = _ConversionPaths.model_validate_json(
        (root / "backend-conversion.json").read_text(encoding="utf-8")
    )
    numbers = tuple(record.unit.item_number for record in records)
    generated = tuple(
        item for item in detect_generated_items(paths.pdf_path).items
        if item.item_number in numbers
    )
    source_placement = {5: FigurePlacement.SIDE_BY_SIDE}
    expectations = tuple(KiceFigureExpectation(
        record.unit.item_number,
        record.unit.figure_assets,
        PreparedVisualKind.FIGURE,
        source_placement.get(
            record.unit.item_number, FigurePlacement.BETWEEN_STEM_AND_ASK,
        ),
    ) for record in records)
    result = inspect_kice_figure_structure(KiceStructuralRequest(
        paths.pdf_path, generated, expectations, 0.70,
    ))
    if result.issues:
        raise StructuralEvidenceError(tuple(
            f"{issue.item_number}:{issue.code.value}" for issue in result.issues
        ))
    observations = tuple(FigureObservation(
        item.item_number,
        item.expected_count,
        item.observed_count,
        item.placement.value if item.placement is not None else "unobserved",
        item.minimum_scale if item.minimum_scale is not None else 0.0,
    ) for item in result.items if item.item_number in numbers)
    if len(observations) != len(records):
        raise StructuralEvidenceError(("missing pinned figure observation",))
    return observations
