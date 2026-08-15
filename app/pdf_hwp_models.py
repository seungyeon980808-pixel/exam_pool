"""Typed HTTP and persistence contracts for PDF-to-HWP conversion jobs."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


JobStatus = Literal[
    "draft", "uploaded", "detecting", "review", "typesetting",
    "partial_failure", "completed", "failed", "cancelled",
]
ItemStatus = Literal["detected", "ready", "processing", "completed", "failed"]
OutputStatus = Literal["pending", "processing", "ready", "failed"]
LayoutStyle = Literal["school", "suneung"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class JobCreate(FrozenModel):
    name: str = Field(default="PDF-HWP conversion", min_length=1, max_length=200)
    layout_style: LayoutStyle = "suneung"


class ItemPatch(FrozenModel):
    palette_markdown: str | None = Field(default=None, min_length=1)
    selected: bool | None = None


class ErrorRead(FrozenModel):
    code: str
    message: str


class ItemRead(FrozenModel):
    id: int
    ord: int
    source_page: int | None
    source_number: int | None
    bbox: tuple[float, float, float, float]
    status: ItemStatus
    selected: bool
    draft: dict[str, JsonValue]
    error: ErrorRead | None
    revision: int


class AssetRead(FrozenModel):
    id: int
    item_id: int | None
    role: str
    file_path: str
    sha256: str
    media_type: str
    metadata: dict[str, JsonValue]


class OutputRead(FrozenModel):
    id: int
    kind: str
    status: OutputStatus
    file_path: str
    sha256: str
    size_bytes: int
    error: ErrorRead | None


class JobCapabilities(FrozenModel):
    review_items: bool
    typeset_selected: bool
    retry_failed: bool


class JobRead(FrozenModel):
    id: int
    name: str
    layout_style: LayoutStyle
    status: JobStatus
    source_filename: str
    source_path: str
    source_sha256: str
    error: ErrorRead | None
    revision: int
    created_at: str
    updated_at: str
    capabilities: JobCapabilities
    items: list[ItemRead]
    assets: list[AssetRead]
    outputs: list[OutputRead]


class JobList(FrozenModel):
    items: list[JobRead]
