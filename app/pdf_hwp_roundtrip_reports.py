"""Deterministic atomic JSON and Markdown reports for round-trip runs."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Final
from uuid import uuid4

from .pdf_hwp_atomic import atomic_replace
from .pdf_hwp_roundtrip_runner import RunStatus, SourceRunResult


_STATUS_ORDER: Final = tuple(RunStatus)


@dataclass(frozen=True, slots=True)
class ReportPaths:
    summary: Path
    failures: Path
    markdown: Path


def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    with temporary.open("x", encoding="utf-8", newline="\n") as destination:
        destination.write(content)
        destination.flush()
        os.fsync(destination.fileno())
    atomic_replace(temporary, target)


def _source_payload(result: SourceRunResult) -> dict[str, str | list[str] | list[dict[str, str]]]:
    return {
        "source_path": str(result.source_path),
        "artifact_hash": result.artifact_hash,
        "route_kind": "" if result.route_kind is None else result.route_kind.value,
        "status": result.status.value,
        "completed_stages": [stage.value for stage in result.completed_stages],
        "artifacts": [
            {
                "stage": artifact.stage.value,
                "path": str(artifact.path),
                "artifact_hash": artifact.artifact_hash,
            }
            for artifact in result.artifacts
        ],
    }


def write_reports(results: tuple[SourceRunResult, ...], output_dir: Path) -> ReportPaths:
    """Write stable aggregate, failure, and human-readable reports atomically."""
    ordered = tuple(sorted(results, key=lambda result: (result.artifact_hash, str(result.source_path))))
    counts = Counter(result.status for result in ordered)
    groups = [
        {"status": status.value, "count": counts[status]}
        for status in _STATUS_ORDER
        if counts[status] > 0
    ]
    summary = {
        "schema_version": 1,
        "source_count": len(ordered),
        "groups": groups,
        "sources": [_source_payload(result) for result in ordered],
    }
    ordered_failures = sorted(
        (failure for result in ordered for failure in result.failures),
        key=lambda failure: (failure.artifact_hash, failure.stage.value, failure.code),
    )
    failures = {
        "schema_version": 1,
        "failures": [
            {
                "artifact_hash": failure.artifact_hash,
                "source_path": str(failure.source_path),
                "stage": failure.stage.value,
                "code": failure.code,
                "detail": failure.detail,
            }
            for failure in ordered_failures
        ],
    }
    lines = ["# PDF-HWP Round-Trip Report", "", f"Sources: {len(ordered)}", "", "## Groups", ""]
    lines.extend(f"- {group['status']}: {group['count']}" for group in groups)
    lines.extend(("", "## Sources", "", "| Artifact hash | Status | Source |", "|---|---|---|"))
    lines.extend(
        f"| `{result.artifact_hash}` | {result.status.value} | {result.source_path} |"
        for result in ordered
    )
    target = output_dir.resolve()
    paths = ReportPaths(target / "summary.json", target / "failures.json", target / "run-report.md")
    _atomic_write(paths.summary, json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _atomic_write(paths.failures, json.dumps(failures, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _atomic_write(paths.markdown, "\n".join(lines) + "\n")
    return paths
