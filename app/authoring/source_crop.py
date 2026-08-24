"""Typed provenance contract for exact source-PDF figure crops."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceCropMetadata(BaseModel):
    """Metadata required to route an original crop without regeneration."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    asset_mode: Literal["source_crop_hd"]
    source_pdf: str = Field(min_length=1)
    page_no: int = Field(gt=0)
    bbox: tuple[float, float, float, float]
    dpi: int = Field(gt=0)
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    aspect_ratio: float = Field(gt=0)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def parse_source_crop(source_meta: dict) -> SourceCropMetadata | None:
    """Parse metadata only when the source-crop routing discriminator is present."""
    if source_meta.get("asset_mode") != "source_crop_hd":
        return None
    return SourceCropMetadata.model_validate(source_meta)
