"""Typed HTTP and persistence contracts for PDF-to-HWP conversion jobs."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


JobStatus = Literal[
    "draft", "uploaded", "detecting", "review", "typesetting",
    "partial_failure", "completed", "failed", "cancelled",
    "cleanup_pending",
]
ItemStatus = Literal[
    "detected", "ready", "processing", "completed", "failed",
    "manual_required", "manual_editing", "manual_ready", "confirmed", "conversion_failed",
]
OutputStatus = Literal["pending", "processing", "ready", "failed"]
LayoutStyle = Literal["school", "suneung"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class JobCreate(FrozenModel):
    name: str = Field(default="PDF-HWP conversion", min_length=1, max_length=200)
    layout_style: LayoutStyle = "suneung"


class ItemPatch(FrozenModel):
    # An empty value intentionally clears stale generated markdown after a
    # manual recovery so the serializer rebuilds the draft from edited text.
    palette_markdown: str | None = None
    selected: bool | None = None
    question_number: int | None = Field(default=None, ge=1, le=999)
    domain: str | None = None
    type_id: str | None = None
    response_type: Literal["matching", "combined"] | None = None
    asset_count: int | None = Field(default=None, ge=0, le=20)
    passage: str | None = None
    prompt: str | None = None
    materials: list[JsonValue] | None = None
    bogi: list[JsonValue] | None = None
    choices: list[JsonValue] | None = None
    source_text: str | None = None
    manual_blocks: list[JsonValue] | None = None
    whole_source_text: bool | None = None


class QuestionDraft(FrozenModel):
    """Structured editor payload persisted inside a conversion item draft."""

    item_id: int | None = None
    question_number: int | None = Field(default=None, ge=1, le=999)
    domain: str = ""
    type_id: str = ""
    type_version: str = "1.0"
    response_type: Literal["matching", "combined"] = "matching"
    asset_count: int = Field(default=0, ge=0, le=20)
    passage: str = ""
    materials: list[JsonValue] = Field(default_factory=list)
    prompt: str = ""
    bogi: list[JsonValue] = Field(default_factory=list)
    choices: list[JsonValue] = Field(default_factory=list)
    source_text: str = ""
    source_bbox: tuple[float, float, float, float] | None = None
    manual_blocks: list[JsonValue] = Field(default_factory=list)
    detection_status: str = "detected"
    conversion_status: str = "pending"
    confirmed: bool = False
    updated_at: str = ""
    unplaced_materials: list[JsonValue] = Field(default_factory=list)


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
    question_number: int | None = None
    domain: str = ""
    type_id: str = ""
    type_version: str = "1.0"
    response_type: Literal["matching", "combined"] = "matching"
    asset_count: int = 0
    detection_status: str = "detected"
    conversion_status: str = "pending"
    confirmed: bool = False
    confirmed_at: str | None = None
    source_text: str = ""
    manual_blocks: list[JsonValue] = Field(default_factory=list)
    unplaced_materials: list[JsonValue] = Field(default_factory=list)


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
    detection_progress: int = 0
    generation_progress: int = 0
    current_item_number: int | None = None
    selection_snapshot: list[int] = Field(default_factory=list)
    selection_snapshot_at: str | None = None
    warnings: list[str] = Field(default_factory=list)
    async_typeset: bool = True
    async_detection: bool = True


class JobList(FrozenModel):
    items: list[JobRead]
