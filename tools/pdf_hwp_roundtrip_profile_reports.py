"""Atomic aggregate reports for source-profile acceptance evidence."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import ValidationError

from app.pdf_hwp_atomic import atomic_replace
from app.pdf_hwp_roundtrip_models import SourceProfile
from app.pdf_hwp_roundtrip_profile import load_profile_verification
from app.pdf_hwp_roundtrip_runner import SourceRunResult


type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


class ProfileReportSource(Protocol):
    source_id: str
    output_dir: Path
    result: SourceRunResult
    profile: SourceProfile


@dataclass(frozen=True, slots=True)
class ProfileReportPaths:
    summary: Path
    failures: Path


def _atomic_json(path: Path, payload: JsonValue) -> Path:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    atomic_replace(temporary, target)
    return target


def write_profile_reports(
    sources: tuple[ProfileReportSource, ...], output_dir: Path,
) -> ProfileReportPaths:
    """Aggregate blocking profile failures while retaining diagnostics in the summary."""
    rows: list[dict[str, JsonValue]] = []
    failures: list[dict[str, JsonValue]] = []
    for source in sources:
        verification_path = source.output_dir / "profile-verification.json"
        try:
            record = load_profile_verification(verification_path) if verification_path.is_file() else None
        except (OSError, ValidationError) as error:
            record = None
            failures.append({
                "source_id": source.source_id, "profile": source.profile.value,
                "code": "profile_verification_unreadable", "detail": str(error),
            })
        if record is not None:
            if record.profile is not source.profile:
                failures.append({
                    "source_id": source.source_id, "profile": source.profile.value,
                    "code": "source_profile_mismatch",
                    "detail": f"record {record.profile.value} != source {source.profile.value}",
                })
            rows.append({
                "source_id": source.source_id, "profile": source.profile.value,
                "prepared_count": record.prepared_count,
                "generated_count": record.generated_count,
                "typed_structure_complete_count": record.structural_coverage.complete_count,
                "typed_structure_total_count": record.structural_coverage.total_count,
                "editability_passed": record.editability.passed,
                "hwp_passed": record.hwp.passed,
                "pdf_semantic_passed": record.pdf_semantic.passed,
                "image_ownership_passed": record.image_ownership.passed,
                "blocking_count": len(record.blocking_issues),
                "diagnostic_count": len(record.diagnostics),
                "diagnostics": [issue.code for issue in record.diagnostics],
            })
            failures.extend({
                "source_id": source.source_id, "profile": record.profile.value,
                "code": issue.code, "detail": issue.detail,
                **({"item_number": issue.item_number} if issue.item_number is not None else {}),
            } for issue in record.blocking_issues)
            continue
        rows.append({
            "source_id": source.source_id, "profile": source.profile.value,
            "verification_available": False, "blocking_count": len(source.result.failures),
            "diagnostic_count": 0,
        })
        failures.extend({
            "source_id": source.source_id, "profile": source.profile.value,
            "code": failure.code, "detail": failure.detail,
        } for failure in source.result.failures)
    rows.sort(key=lambda row: str(row["source_id"]))
    failures.sort(key=lambda row: (str(row["source_id"]), str(row["code"])))
    root = output_dir.resolve()
    return ProfileReportPaths(
        _atomic_json(root / "profile-summary.json", {
            "schema_version": 1, "source_count": len(rows), "sources": rows,
            "profile_failure_count": len(failures),
        }),
        _atomic_json(root / "profile-failures.json", {
            "schema_version": 1, "failures": failures,
        }),
    )


__all__ = ["ProfileReportPaths", "write_profile_reports"]
