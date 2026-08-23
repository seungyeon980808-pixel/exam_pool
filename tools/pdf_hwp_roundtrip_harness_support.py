"""Deterministic aggregate failure and contact-sheet reporting for the harness."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Final
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont
from pydantic import ValidationError

from app.pdf_hwp_roundtrip_backend_store import load_verification
from app.pdf_hwp_atomic import atomic_replace
from app.pdf_hwp_roundtrip_models import SourceProfile
from app.pdf_hwp_roundtrip_reports import ReportPaths
from app.pdf_hwp_roundtrip_runner import SourceRunResult
from app.pdf_hwp_roundtrip_units import load_prepared_units
from tools.pdf_hwp_roundtrip_profile_reports import write_profile_reports


_OVERVIEW_WIDTH: Final = 900
_OVERVIEW_COLUMNS: Final = 2
_OVERVIEW_MARGIN: Final = 20
_OVERVIEW_GAP: Final = 20
_OVERVIEW_CARD_WIDTH: Final = 420
_OVERVIEW_PREVIEW_HEIGHT: Final = 160
_OVERVIEW_SOURCE_CROP_HEIGHT: Final = 320
_OVERVIEW_CARD_HEIGHT, _OVERVIEW_TOP, _OVERVIEW_MAX_CARDS = 196, 44, 14


@dataclass(frozen=True, slots=True)
class ReportSource:
    source_id: str
    output_dir: Path
    result: SourceRunResult
    profile: SourceProfile = SourceProfile.KICE_STRUCTURAL
    regression_claims: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtraFailure:
    artifact_hash: str
    source_path: Path
    stage: str
    code: str
    detail: str
    item_number: int | None = None


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    atomic_replace(temporary, path)


def persist_run_metadata(
    run_root: Path, metadata: dict[str, str | int | tuple[str, ...]],
) -> None:
    """Atomically expose the active deterministic namespace."""
    _atomic_write(
        run_root.resolve() / "run-metadata.json",
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _state_failures(source: ReportSource) -> tuple[ExtraFailure, ...]:
    failures: list[ExtraFailure] = []
    prepared_path = source.output_dir / "prepared-units.json"
    if prepared_path.is_file():
        try:
            prepared = load_prepared_units(prepared_path)
        except (OSError, ValidationError) as error:
            failures.append(ExtraFailure(
                source.result.artifact_hash,
                source.result.source_path,
                "extract",
                "prepared_state_unreadable",
                str(error),
            ))
        else:
            failures.extend(ExtraFailure(
                source.result.artifact_hash,
                source.result.source_path,
                "extract",
                failure.code.value,
                failure.detail,
                failure.item_number,
            ) for failure in prepared.item_failures)
    verification_path = source.output_dir / "verification.json"
    if verification_path.is_file():
        try:
            verification = load_verification(verification_path)
        except (OSError, ValidationError) as error:
            failures.append(ExtraFailure(
                source.result.artifact_hash,
                source.result.source_path,
                "pdf",
                "verification_state_unreadable",
                str(error),
            ))
        else:
            failures.extend(ExtraFailure(
                source.result.artifact_hash,
                source.result.source_path,
                "pdf",
                issue,
                issue,
            ) for issue in verification.document_issues if issue not in {"visual_mismatch", "visual_metric"})
            failures.extend(
                ExtraFailure(
                    source.result.artifact_hash,
                    source.result.source_path,
                    "pdf",
                    issue,
                    f"item {item.item_number}: {issue}",
                    item.item_number,
                )
                for item in verification.items
                for issue in item.issues
                if issue not in {"visual_mismatch", "visual_metric"}
            )
    return tuple(failures)


def _failure_payload(failure: ExtraFailure) -> dict[str, str | int]:
    payload: dict[str, str | int] = {
        "artifact_hash": failure.artifact_hash,
        "source_path": str(failure.source_path),
        "stage": failure.stage,
        "code": failure.code,
        "detail": failure.detail,
    }
    if failure.item_number is not None:
        payload["item_number"] = failure.item_number
    return payload


def _is_pinned_failure(source: ReportSource, failure: ExtraFailure) -> bool:
    if failure.stage != "extract" or failure.item_number is None:
        return False
    pinned = {
        (int(item), code)
        for claim in source.regression_claims
        for _, item, code in (claim.rsplit(":", 2),)
    }
    return (failure.item_number, failure.code) in pinned


def _write_contact_sheet(
    sources: tuple[ReportSource, ...], failures: tuple[ExtraFailure, ...], target: Path,
) -> None:
    image_entries: list[tuple[ReportSource, Path, tuple[ExtraFailure, ...]]] = []
    text_entries: list[tuple[ReportSource, Path | None, tuple[ExtraFailure, ...]]] = []
    for source in sources:
        evidence_root = source.output_dir / "verification-evidence"
        failure_sheet = evidence_root / "item-failures.png"
        contact_sheet = evidence_root / "contact-sheet.png"
        related = tuple(
            failure for failure in failures
            if failure.artifact_hash == source.result.artifact_hash
        )
        if failure_sheet.is_file():
            image_entries.append((source, failure_sheet, related))
        elif contact_sheet.is_file():
            image_entries.append((source, contact_sheet, related))
        elif related:
            text_entries.append((source, None, related))
    entries = [*image_entries, *text_entries]
    if not entries:
        entries = [(source, None, ()) for source in sources]
    if len(entries) > _OVERVIEW_MAX_CARDS:
        entries = entries[:_OVERVIEW_MAX_CARDS]
    rows = max(1, (len(entries) + _OVERVIEW_COLUMNS - 1) // _OVERVIEW_COLUMNS)
    height = _OVERVIEW_TOP + rows * _OVERVIEW_CARD_HEIGHT + _OVERVIEW_MARGIN
    canvas = Image.new("RGB", (_OVERVIEW_WIDTH, height), "#f1f3f5")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    label = f"FAILURES {len(failures)}" if failures else f"PASS / PAUSED {len(sources)}"
    draw.text((20, 18), label, fill="#111111", font=font)
    for index, (source, path, related) in enumerate(entries):
        row, column = divmod(index, _OVERVIEW_COLUMNS)
        x = _OVERVIEW_MARGIN + column * (_OVERVIEW_CARD_WIDTH + _OVERVIEW_GAP)
        y = _OVERVIEW_TOP + row * _OVERVIEW_CARD_HEIGHT
        draw.text(
            (x, y), f"{source.source_id} | {source.result.status.value}",
            fill="#8b1e1e" if related else "#14532d", font=font,
        )
        if path is not None:
            with Image.open(path) as opened:
                preview = opened.convert("RGB").crop((
                    0, 0, opened.width, min(opened.height, _OVERVIEW_SOURCE_CROP_HEIGHT),
                ))
            preview.thumbnail(
                (_OVERVIEW_CARD_WIDTH, _OVERVIEW_PREVIEW_HEIGHT), Image.Resampling.LANCZOS,
            )
            canvas.paste(preview, (x, y + 16))
            preview.close()
            continue
        for line, failure in enumerate(related[:5]):
            text = f"{failure.code} | {failure.detail}"[:68]
            draw.text((x, y + 24 + line * 18), text, fill="#8b1e1e", font=font)
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target)
    canvas.close()


def finalize_reports(
    reports: ReportPaths,
    sources: tuple[ReportSource, ...],
    output_dir: Path,
    sample_groups: dict[str, dict[str, list[str]]],
    regression_claims: tuple[str, ...],
) -> Path:
    """Merge item-level failures and always create one top-level contact sheet."""
    located_by_failure = {
        failure: (source, failure)
        for source in sources for failure in _state_failures(source)
    }
    located = tuple(located_by_failure.values())
    extras = tuple(sorted(
        located_by_failure,
        key=lambda value: (
            value.artifact_hash, value.stage, value.code, value.item_number or 0,
        ),
    ))
    payload = json.loads(reports.failures.read_text(encoding="utf-8"))
    payload["failures"].extend(_failure_payload(failure) for failure in extras)
    payload["failures"].sort(key=lambda row: (
        row["artifact_hash"], row["stage"], row["code"], row.get("item_number", 0),
    ))
    _atomic_write(
        reports.failures,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    summary = json.loads(reports.summary.read_text(encoding="utf-8"))
    summary["failure_count"] = len(payload["failures"])
    summary["preparation_verification_failure_count"] = len(extras)
    summary["expected_preparation_verification_failure_count"] = sum(
        _is_pinned_failure(source, failure) for source, failure in located
    )
    summary["unexpected_preparation_verification_failure_count"] = sum(
        not _is_pinned_failure(source, failure) for source, failure in located
    )
    summary["sample_groups"] = {
        name: {
            "count": len(group["source_ids"]),
            "source_ids": group["source_ids"],
            "selected_count": len(group["selected_source_ids"]),
            "selected_source_ids": group["selected_source_ids"],
        }
        for name, group in sorted(sample_groups.items())
    }
    summary["artifact_hashes"] = {
        source.source_id: source.result.artifact_hash for source in sources
    }
    summary["fixed_regression_claims"] = sorted(regression_claims)
    summary["contact_sheet"] = str(output_dir.resolve() / "contact-sheet.png")
    _atomic_write(
        reports.summary,
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    markdown = reports.markdown.read_text(encoding="utf-8")
    if regression_claims:
        lines = ["", "## Fixed regression claims", ""]
        lines.extend(f"- `{claim}`" for claim in sorted(regression_claims))
        markdown = markdown.rstrip() + "\n" + "\n".join(lines) + "\n"
    if extras:
        lines = ["", "## Preparation and verification failures", ""]
        lines.extend(
            f"- `{failure.code}` item {failure.item_number or '-'}: {failure.source_path}"
            for failure in extras
        )
        markdown = markdown.rstrip() + "\n" + "\n".join(lines) + "\n"
    _atomic_write(reports.markdown, markdown)
    contact_sheet = output_dir.resolve() / "contact-sheet.png"
    base_failures = tuple(
        ExtraFailure(
            failure.artifact_hash,
            failure.source_path,
            failure.stage.value,
            failure.code,
            failure.detail,
        )
        for source in sources
        for failure in source.result.failures
    )
    _write_contact_sheet(sources, (*base_failures, *extras), contact_sheet)
    write_profile_reports(sources, output_dir)
    return contact_sheet
