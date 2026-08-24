# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Measure the fixed c1/c2/b1 recent-PDF set without invoking HWP/PDF output.

Usage:
    python tools/corpus_pdf_hwp_multisubject_a.py
    python tools/corpus_pdf_hwp_multisubject_a.py --limit-papers 1
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter
from typing import Final


PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.pdf_hwp_pipeline_models import LayoutStyle  # noqa: E402
from tools.corpus_pdf_hwp_baseline_c import (  # noqa: E402
    PaperSource,
    RunResult,
    measure_paper,
)


CORPUS_ROOT: Final = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일"
OUTPUT: Final = (
    PROJECT_ROOT
    / ".omo"
    / "evidence"
    / "pdf-hwp-generalization"
    / "multisubject-a"
    / "multisubject-a.json"
)
SUBJECTS: Final = ("c1", "c2", "b1")
SESSIONS: Final = ("2027_06", "2026_11", "2026_09", "2026_06", "2025_11")


def requested_papers() -> tuple[PaperSource, ...]:
    """Return the exact ordered requested-folder 15-paper set."""
    return tuple(
        PaperSource(
            path.stem.lower(),
            str(path.resolve(strict=True)),
            "requested_corpus_folder",
        )
        for subject in SUBJECTS
        for session in SESSIONS
        for path in (CORPUS_ROOT / f"{subject}_{session}.pdf",)
    )


def run(output: Path, limit_papers: int | None) -> RunResult:
    """Run C's production-seam measurement with an isolated temporary scratch root."""
    papers = requested_papers()
    if limit_papers is not None:
        papers = papers[:limit_papers]
    started_at = datetime.now(UTC)
    started = perf_counter()
    with tempfile.TemporaryDirectory(prefix="exampool-multisubject-a-") as temporary:
        scratch_root = Path(temporary)
        results = tuple(measure_paper(paper, scratch_root) for paper in papers)
    finished_at = datetime.now(UTC)
    result = RunResult(
        1,
        tuple(sys.argv),
        started_at.isoformat(),
        finished_at.isoformat(),
        LayoutStyle.SUNEUNG.value,
        "per-paper TemporaryDirectory under one system temporary root; deleted after measurement",
        False,
        len(results),
        sum(paper.detected_count for paper in results),
        sum(paper.ready_count for paper in results),
        sum(paper.manual_count for paper in results),
        sum(paper.failure_count for paper in results),
        perf_counter() - started,
        results,
    )
    payload = asdict(result)
    payload.update(
        corpus_root=str(CORPUS_ROOT.resolve(strict=True)),
        requested_subjects=SUBJECTS,
        requested_sessions=SESSIONS,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--limit-papers", type=int)
    args = parser.parse_args()
    result = run(args.output, args.limit_papers)
    print(json.dumps({
        "paper_count": result.paper_count,
        "detected_count": result.detected_count,
        "ready_count": result.ready_count,
        "manual_count": result.manual_count,
        "failure_count": result.failure_count,
        "elapsed_seconds": result.elapsed_seconds,
        "output": str(args.output.resolve()),
    }, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
