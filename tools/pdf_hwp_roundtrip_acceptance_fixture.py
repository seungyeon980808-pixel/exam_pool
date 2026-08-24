"""Process-isolated deterministic fixture CLI for C002 acceptance evidence."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys

from pydantic import BaseModel, ConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.pdf_hwp_roundtrip_checkpoint import CheckpointStore
from app.pdf_hwp_roundtrip_models import SourceFacts, SourceIntegrity, SourceRoute
from app.pdf_hwp_roundtrip_router import route_source
from app.pdf_hwp_roundtrip_runner import (
    ExtractOutcome,
    HwpOutcome,
    PdfOutcome,
    RouteOutcome,
    RoundTripRunner,
    RunPolicy,
    SourceInput,
)


class FixturePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str
    route_kind: str | None
    completed_stages: tuple[str, ...]
    artifact_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FixtureInvocation:
    payload: FixturePayload
    command: tuple[str, ...]
    returncode: int


class FixtureProcessError(RuntimeError):
    detail: str

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

    def __str__(self) -> str:
        return self.detail


def run_fixture_process(root: Path, arguments: tuple[str, ...]) -> FixtureInvocation:
    """Execute this fixture CLI in a separate interpreter and parse its result."""
    script = Path(__file__).resolve()
    command = (sys.executable, str(script), "--root", str(root), *arguments)
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            timeout=30, check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise FixtureProcessError("C002 fixture CLI timed out") from error
    if completed.returncode != 0:
        raise FixtureProcessError(
            f"C002 fixture CLI exit {completed.returncode}: {completed.stderr.strip()}",
        )
    payload = FixturePayload.model_validate_json(completed.stdout)
    display = ("<PYTHON>", script.name, "--root", "<TEMP_ROOT>", *arguments)
    return FixtureInvocation(payload, display, completed.returncode)


@dataclass(frozen=True, slots=True)
class _FixtureBackend:
    root: Path

    def _record(self, stage: str, source: SourceInput) -> None:
        with (self.root / "calls.txt").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{stage}:{source.path.name}\n")

    def route(self, source: SourceInput) -> RouteOutcome:
        self._record("route", source)
        return RouteOutcome(route_source(source.facts))

    def extract(self, source: SourceInput, route: SourceRoute) -> ExtractOutcome:
        self._record("extract", source)
        target = self.root / "known-extract.json"
        target.write_text('{"items":[1]}\n', encoding="utf-8")
        return ExtractOutcome(target)

    def typeset(self, source: SourceInput, route: SourceRoute) -> HwpOutcome:
        self._record("typeset", source)
        target = self.root / "known.hwp"
        target.write_bytes(b"deterministic-hwp")
        return HwpOutcome(target)

    def verify(self, source: SourceInput, route: SourceRoute) -> PdfOutcome:
        self._record("verify", source)
        target = self.root / "known.pdf"
        target.write_bytes(b"%PDF-1.7\ndeterministic\n%%EOF")
        return PdfOutcome(target)


def _source(root: Path, malformed: bool) -> SourceInput:
    path = root / ("malformed.bin" if malformed else "p1_2024_11.pdf")
    path.write_bytes(b"malformed" if malformed else b"%PDF-1.7\nknown\n%%EOF")
    return SourceInput(path, SourceFacts(
        path.name,
        "" if malformed else "2026학년도 대학수학능력시험 문제지",
        "" if malformed else "1. fixture",
        0 if malformed else 1,
        0,
        SourceIntegrity.MALFORMED if malformed else SourceIntegrity.VALID,
    ))


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--malformed", action="store_true")
    parser.add_argument("--stop-after-completed-stages", type=int)
    arguments = parser.parse_args(argv)
    root = arguments.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    result = RoundTripRunner(
        _FixtureBackend(root),
        CheckpointStore(root / "checkpoints"),
        RunPolicy(arguments.stop_after_completed_stages),
    ).run((_source(root, arguments.malformed),))[0]
    print(json.dumps({
        "status": result.status.value,
        "route_kind": result.route_kind.value if result.route_kind else None,
        "completed_stages": [stage.value for stage in result.completed_stages],
        "artifact_hashes": [artifact.artifact_hash for artifact in result.artifacts],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
