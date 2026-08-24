"""Real source observations, image normalization, and item selection."""

from __future__ import annotations

import hashlib
from io import BytesIO
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final
from uuid import uuid4

import fitz
from PIL import Image

from .pdf_hwp_atomic import atomic_replace
from .pdf_hwp_pipeline import detect_items
from .pdf_hwp_pipeline_models import DetectedItem
from .pdf_hwp_roundtrip_models import SourceFacts, SourceIntegrity


_IMAGE_SUFFIXES: Final = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_IDENTITY_PAGES: Final = 4


@dataclass(frozen=True, slots=True)
class UnsupportedSourceError(ValueError):
    path: Path

    def __str__(self) -> str:
        return f"source must be PDF, PNG, JPEG, or WebP: {self.path}"


@dataclass(frozen=True, slots=True)
class NormalizedSource:
    original_path: Path
    pipeline_pdf: Path
    temporary: bool

    @property
    def cleanup_target(self) -> Path | None:
        """Return the durable run-owned PDF that a temporary caller may remove."""
        return self.pipeline_pdf if self.temporary else None


class SelectionIssueKind(StrEnum):
    DUPLICATE = "duplicate"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class SelectionIssue:
    kind: SelectionIssueKind
    item_number: int
    occurrences: int


@dataclass(frozen=True, slots=True)
class DetectedItemSelection:
    items: tuple[DetectedItem, ...]
    issues: tuple[SelectionIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


def derive_source_facts(source_pdf: Path, expected_filename: str) -> SourceFacts:
    """Observe a PDF once and derive the facts consumed by source routing."""
    source = source_pdf.resolve()
    try:
        document = fitz.open(source)
    except (fitz.FileDataError, FileNotFoundError, OSError):
        return SourceFacts(expected_filename, "", "", 0, 0, SourceIntegrity.MALFORMED)
    with document:
        page_count = document.page_count
        identity_text = " ".join(
            document[index].get_text()
            for index in range(min(_IDENTITY_PAGES, page_count))
        ).replace("[ ", "[")
        raster_page_count = sum(
            1 for page in document
            if not page.get_text().strip() and bool(page.get_images(full=True))
        )
    return SourceFacts(
        filename=expected_filename,
        identity_text=identity_text,
        source_text=identity_text,
        page_count=page_count,
        raster_page_count=raster_page_count,
        integrity=SourceIntegrity.VALID,
    )


def _is_nonempty_pdf(path: Path) -> bool:
    try:
        with fitz.open(path) as document:
            return document.is_pdf and document.page_count > 0
    except (
        fitz.EmptyFileError,
        fitz.FileDataError,
        fitz.FileNotFoundError,
        FileNotFoundError,
        OSError,
    ):
        return False


def normalize_source(source_path: Path, run_directory: Path) -> NormalizedSource:
    """Return a pipeline PDF, durably caching run-owned image normalization."""
    source = source_path.resolve()
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        return NormalizedSource(source, source, False)
    if suffix not in _IMAGE_SUFFIXES:
        raise UnsupportedSourceError(source)
    run_root = run_directory.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    target = run_root / f"{source.stem}-{digest}.normalized.pdf"
    if not _is_nonempty_pdf(target):
        temporary = run_root / f".{target.stem}.{uuid4().hex}.tmp.pdf"
        encoded = BytesIO()
        try:
            with Image.open(source) as raster:
                raster.save(encoded, format="PNG")
            with fitz.open("png", encoded.getvalue()) as image_document:
                payload = image_document.convert_to_pdf()
            with fitz.open("pdf", payload) as pdf_document:
                pdf_document.save(temporary)
            if not _is_nonempty_pdf(temporary):
                raise fitz.FileDataError("normalized PDF is empty or unreadable")
            atomic_replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return NormalizedSource(source, target, True)


def select_detected_items(
    source_pdf: Path, selected_numbers: tuple[int, ...] | None,
) -> DetectedItemSelection:
    """Select unambiguous detected items with deterministic issue ordering."""
    detection = detect_items(source_pdf)
    by_number: dict[int, list[DetectedItem]] = {}
    for item in detection.items:
        by_number.setdefault(item.item_number, []).append(item)
    wanted = set(by_number) if selected_numbers is None else set(selected_numbers)
    issues = [
        SelectionIssue(SelectionIssueKind.DUPLICATE, number, len(by_number[number]))
        for number in wanted
        if number in by_number and len(by_number[number]) > 1
    ]
    issues.extend(
        SelectionIssue(SelectionIssueKind.MISSING, number, 0)
        for number in wanted
        if number not in by_number
    )
    issues.sort(key=lambda issue: (issue.item_number, issue.kind.value))
    items = tuple(sorted(
        (
            by_number[number][0] for number in wanted
            if number in by_number and len(by_number[number]) == 1
        ),
        key=lambda item: (item.item_number, item.page_number, item.column, item.bbox),
    ))
    return DetectedItemSelection(items, tuple(issues))
