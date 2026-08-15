"""Safety boundary for ordered graphical answer-choice assets."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from .pdf_hwp_models import AssetRead
from .pdf_hwp_pipeline_models import (
    CropArtifact,
    GraphicalChoiceAsset,
    GraphicalChoiceAssetMetadata,
    ManualReviewRequiredError,
)


GRAPHICAL_CHOICE_TEMPLATE: Final = "수능정답1대사진그림5선지"
GRAPHICAL_CHOICE_COUNT: Final = 5


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _template_detail(markdown: str, assets: tuple[CropArtifact, ...]) -> str | None:
    lines = tuple(line.strip() for line in markdown.splitlines() if line.strip())
    if not lines or lines[0] != f"\\{GRAPHICAL_CHOICE_TEMPLATE}\\":
        return "graphical-choice template label is incomplete"
    expected = tuple(f"\\{asset.image_path.stem}\\" for asset in assets)
    if len(lines) < GRAPHICAL_CHOICE_COUNT or lines[-GRAPHICAL_CHOICE_COUNT:] != expected:
        return "graphical-choice template slots do not match ordered assets"
    return None


def draft_review_detail(
    item_number: int,
    markdown: str,
    assets: tuple[CropArtifact, ...],
) -> str | None:
    """Return a manual-review reason when a draft's choice contract is unsafe."""
    has_graphical_template = markdown.lstrip().startswith(f"\\{GRAPHICAL_CHOICE_TEMPLATE}\\")
    if not assets:
        return "graphical-choice assets are missing" if has_graphical_template else None
    if len(assets) != GRAPHICAL_CHOICE_COUNT:
        return "graphical-choice asset count must be exactly five"
    try:
        metadata = tuple(
            GraphicalChoiceAssetMetadata.model_validate_json(
                asset.provenance_path.read_text(encoding="utf-8")
            )
            for asset in assets
        )
    except (OSError, ValidationError):
        return "invalid graphical-choice asset metadata"
    if tuple(value.choice_index for value in metadata) != tuple(range(1, 6)):
        return "graphical-choice assets must be ordered 1 through 5"
    if any(value.item_number != item_number for value in metadata):
        return "graphical-choice item identity mismatch"
    if any(value.manual_review_required for value in metadata):
        reasons = tuple(
            reason for value in metadata for reason in value.review_reasons if reason.strip()
        )
        return "; ".join(dict.fromkeys(reasons)) or "graphical choices require manual review"
    if any(_file_hash(asset.image_path) != value.asset_hash for asset, value in zip(assets, metadata, strict=True)):
        return "graphical-choice asset hash mismatch"
    return _template_detail(markdown, assets)


def selected_assets(
    item_number: int,
    markdown: str,
    assets: list[AssetRead],
) -> tuple[GraphicalChoiceAsset, ...]:
    """Parse persisted choices into a safe ordered typeset contract."""
    candidates = [asset for asset in assets if asset.role == "graphical_choice"]
    if not candidates:
        if markdown.lstrip().startswith(f"\\{GRAPHICAL_CHOICE_TEMPLATE}\\"):
            raise ManualReviewRequiredError(item_number, "graphical-choice assets are missing")
        return ()
    try:
        parsed = tuple(
            GraphicalChoiceAsset(
                Path(asset.file_path),
                GraphicalChoiceAssetMetadata.model_validate(asset.metadata),
            )
            for asset in candidates
        )
    except ValidationError as exc:
        raise ManualReviewRequiredError(
            item_number, "invalid persisted graphical-choice asset metadata",
        ) from exc
    detail = _selected_detail(item_number, markdown, candidates, parsed)
    if detail is not None:
        raise ManualReviewRequiredError(item_number, detail)
    return parsed


def _selected_detail(
    item_number: int,
    markdown: str,
    rows: list[AssetRead],
    assets: tuple[GraphicalChoiceAsset, ...],
) -> str | None:
    if len(assets) != GRAPHICAL_CHOICE_COUNT:
        return "graphical-choice asset count must be exactly five"
    if tuple(asset.metadata.choice_index for asset in assets) != tuple(range(1, 6)):
        return "graphical-choice assets must be ordered 1 through 5"
    if any(asset.metadata.item_number != item_number for asset in assets):
        return "graphical-choice item identity mismatch"
    if any(asset.metadata.manual_review_required for asset in assets):
        return "graphical choices require manual review"
    if any(
        _file_hash(asset.image_path) != asset.metadata.asset_hash
        or row.sha256 != asset.metadata.asset_hash
        for row, asset in zip(rows, assets, strict=True)
    ):
        return "graphical-choice asset hash mismatch"
    crops = tuple(
        CropArtifact(asset.image_path, Path(), asset.metadata.width_px, asset.metadata.height_px)
        for asset in assets
    )
    return _template_detail(markdown, crops)
