"""Typed atomic state files owned by the real round-trip backend."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from .pdf_hwp_atomic import atomic_replace
from .pdf_hwp_roundtrip_unit_store import FailureCode, ItemFailure


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _ConversionPayload(_FrozenModel):
    hwp_path: Path
    pdf_path: Path
    rendered_pages: tuple[Path, ...]


class _FailurePayload(_FrozenModel):
    item_number: int
    code: FailureCode
    detail: str
    source_item_hash: str


class _ItemPayload(_FrozenModel):
    item_number: int
    pixel_mae: float
    edge_mae: float
    issues: tuple[str, ...]


class _VerificationPayload(_FrozenModel):
    pdf_path: Path
    document_issues: tuple[str, ...]
    items: tuple[_ItemPayload, ...]
    preparation_failures: tuple[_FailurePayload, ...]


@dataclass(frozen=True, slots=True)
class ConversionPaths:
    hwp_path: Path
    pdf_path: Path
    rendered_pages: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class VerificationItem:
    item_number: int
    pixel_mae: float
    edge_mae: float
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    pdf_path: Path
    document_issues: tuple[str, ...]
    items: tuple[VerificationItem, ...]
    preparation_failures: tuple[ItemFailure, ...]


def _atomic_write(path: Path, content: str) -> Path:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    atomic_replace(temporary, target)
    return target


def write_conversion_paths(path: Path, value: ConversionPaths) -> Path:
    """Persist generated document paths for a fresh verify-stage process."""
    payload = _ConversionPayload(
        hwp_path=value.hwp_path.resolve(),
        pdf_path=value.pdf_path.resolve(),
        rendered_pages=tuple(page.resolve() for page in value.rendered_pages),
    )
    return _atomic_write(path, payload.model_dump_json(indent=2) + "\n")


def load_conversion_paths(path: Path) -> ConversionPaths:
    """Parse persisted generated document paths."""
    payload = _ConversionPayload.model_validate_json(path.resolve().read_text(encoding="utf-8"))
    return ConversionPaths(payload.hwp_path, payload.pdf_path, payload.rendered_pages)


def write_verification(path: Path, value: VerificationRecord) -> Path:
    """Persist structural, visual, and preparation outcomes atomically."""
    payload = _VerificationPayload(
        pdf_path=value.pdf_path.resolve(),
        document_issues=value.document_issues,
        items=tuple(_ItemPayload(
            item_number=item.item_number,
            pixel_mae=item.pixel_mae,
            edge_mae=item.edge_mae,
            issues=item.issues,
        ) for item in value.items),
        preparation_failures=tuple(_FailurePayload(
            item_number=failure.item_number,
            code=failure.code,
            detail=failure.detail,
            source_item_hash=failure.source_item_hash,
        ) for failure in value.preparation_failures),
    )
    return _atomic_write(path, payload.model_dump_json(indent=2) + "\n")


def load_verification(path: Path) -> VerificationRecord:
    """Parse a verification record for reporting and resume checks."""
    payload = _VerificationPayload.model_validate_json(path.resolve().read_text(encoding="utf-8"))
    return VerificationRecord(
        payload.pdf_path,
        payload.document_issues,
        tuple(VerificationItem(item.item_number, item.pixel_mae, item.edge_mae, item.issues)
              for item in payload.items),
        tuple(ItemFailure(
            failure.item_number, failure.code, failure.detail, failure.source_item_hash,
        ) for failure in payload.preparation_failures),
    )
