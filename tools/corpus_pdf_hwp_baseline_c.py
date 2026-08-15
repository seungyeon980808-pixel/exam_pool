# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Measure ExamPool PDF detection, editable-draft, and HWP preflight baselines.

Usage:
    uv run tools/corpus_pdf_hwp_baseline_c.py discover-p1 --output evidence.json
    uv run tools/corpus_pdf_hwp_baseline_c.py run-p1 --output results.json \
        --scratch-root .omo/teams/team-2cdc6a66/artifacts/corpus-ui/scratch
    uv run tools/corpus_pdf_hwp_baseline_c.py run-subjects --subjects b2 e1 e2 \
        --output results.json --scratch-root .omo/evidence/pdf-hwp-generalization/corpus-c/scratch

The harness never invokes HWP or PDF generation. Draft crops live in one temporary
directory per paper and are removed after the paper is measured.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter
import traceback


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일")
LATEST_FIVE = ("2027_06", "2026_11", "2026_09", "2026_06", "2025_11")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.pdf_hwp_final_figure_contract import (  # noqa: E402
    FinalFigureContract,
    FinalFigureReview,
    reconcile_final_figure_contract,
)
from app.pdf_hwp_graphical_choices import draft_review_detail  # noqa: E402
from app.pdf_hwp_hwp_preflight import preflight_unit  # noqa: E402
from app.pdf_hwp_pipeline import build_editable_draft, detect_items  # noqa: E402
from app.pdf_hwp_pipeline_models import (  # noqa: E402
    ConversionUnit,
    CropArtifact,
    DetectedItem,
    DraftExtractionError,
    FigureAsset,
    FigureAssetMetadata,
    GraphicalChoiceAsset,
    GraphicalChoiceAssetMetadata,
    InvalidCropError,
    LayoutStyle,
    ManualReviewRequiredError,
    UnsupportedDraftLayoutError,
)


@dataclass(frozen=True, slots=True)
class PaperSource:
    logical_name: str
    source_path: str
    origin: str


@dataclass(frozen=True, slots=True)
class ItemResult:
    item_number: int
    page_number: int
    status: str
    detail: str
    warning_count: int
    draft_seconds: float
    preflight_seconds: float


@dataclass(frozen=True, slots=True)
class PaperResult:
    logical_name: str
    source_path: str
    origin: str
    source_hash: str
    page_count: int
    detected_count: int
    ready_count: int
    manual_count: int
    failure_count: int
    detection_seconds: float
    total_seconds: float
    paper_error: str
    items: tuple[ItemResult, ...]


@dataclass(frozen=True, slots=True)
class RunResult:
    schema_version: int
    command: tuple[str, ...]
    started_at_utc: str
    finished_at_utc: str
    layout_style: str
    output_isolation: str
    hwp_or_pdf_generation_invoked: bool
    paper_count: int
    detected_count: int
    ready_count: int
    manual_count: int
    failure_count: int
    elapsed_seconds: float
    papers: tuple[PaperResult, ...]


def _logical_name(path: Path) -> str:
    return path.stem.lower()


def discover_p1() -> tuple[PaperSource, ...]:
    """Return requested p1 sources, exclusively from the user's corpus folder."""
    candidates: dict[str, PaperSource] = {}
    for path in sorted(CORPUS_ROOT.glob("p1_*.pdf")):
        logical = _logical_name(path)
        if "p1_2020_06" <= logical <= "p1_2027_06":
            candidates[logical] = PaperSource(
                logical, str(path.resolve()), "requested_corpus_folder",
            )
    return tuple(candidates[key] for key in sorted(candidates))


def discover_subjects(subjects: tuple[str, ...]) -> tuple[PaperSource, ...]:
    """Return latest-five requested-folder sources for the selected subjects."""
    papers: list[PaperSource] = []
    for subject in subjects:
        for exam_id in LATEST_FIVE:
            logical = f"{subject}_{exam_id}"
            path = CORPUS_ROOT / f"{logical}.pdf"
            if not path.is_file():
                raise FileNotFoundError(f"required corpus source is missing: {path}")
            papers.append(PaperSource(
                logical, str(path.resolve()), "requested_corpus_folder",
            ))
    return tuple(papers)


def _figure_assets(draft_assets: tuple[CropArtifact, ...]) -> tuple[FigureAsset, ...]:
    return tuple(
        FigureAsset(
            artifact.image_path,
            FigureAssetMetadata.model_validate_json(
                artifact.provenance_path.read_text(encoding="utf-8")
            ),
        )
        for artifact in draft_assets
    )


def _choice_assets(
    draft_assets: tuple[CropArtifact, ...],
) -> tuple[GraphicalChoiceAsset, ...]:
    return tuple(
        GraphicalChoiceAsset(
            artifact.image_path,
            GraphicalChoiceAssetMetadata.model_validate_json(
                artifact.provenance_path.read_text(encoding="utf-8")
            ),
        )
        for artifact in draft_assets
    )


def _measure_item(source: Path, item: DetectedItem, output_dir: Path) -> ItemResult:
    draft_started = perf_counter()
    try:
        draft = build_editable_draft(source, item, output_dir)
    except UnsupportedDraftLayoutError as exc:
        return ItemResult(item.item_number, item.page_number, "manual", str(exc), 0,
                          perf_counter() - draft_started, 0.0)
    except (DraftExtractionError, InvalidCropError) as exc:
        return ItemResult(item.item_number, item.page_number, "failure", str(exc), 0,
                          perf_counter() - draft_started, 0.0)
    except Exception as exc:  # noqa: BLE001 - corpus boundary must keep measuring
        detail = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        return ItemResult(item.item_number, item.page_number, "failure", detail, 0,
                          perf_counter() - draft_started, 0.0)
    draft_seconds = perf_counter() - draft_started
    choice_detail = draft_review_detail(
        item.item_number, draft.palette_markdown, draft.graphical_choice_assets,
    )
    if choice_detail is not None:
        return ItemResult(item.item_number, item.page_number, "manual", choice_detail,
                          len(draft.warnings), draft_seconds, 0.0)
    figure_contract = reconcile_final_figure_contract(
        item.item_number, draft.palette_markdown, draft.figure_assets,
    )
    if isinstance(figure_contract, FinalFigureReview):
        return ItemResult(item.item_number, item.page_number, "manual", figure_contract.detail,
                          len(draft.warnings), draft_seconds, 0.0)
    assert isinstance(figure_contract, FinalFigureContract)
    preflight_started = perf_counter()
    try:
        unit = ConversionUnit(
            item.item_number,
            figure_contract.palette_markdown,
            _figure_assets(draft.figure_assets),
            _choice_assets(draft.graphical_choice_assets),
        )
        preflight_unit(unit, LayoutStyle.SUNEUNG)
    except ManualReviewRequiredError as exc:
        return ItemResult(item.item_number, item.page_number, "manual", str(exc),
                          len(draft.warnings), draft_seconds,
                          perf_counter() - preflight_started)
    except Exception as exc:  # noqa: BLE001 - corpus boundary must keep measuring
        detail = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        return ItemResult(item.item_number, item.page_number, "failure", detail,
                          len(draft.warnings), draft_seconds,
                          perf_counter() - preflight_started)
    return ItemResult(item.item_number, item.page_number, "ready", "",
                      len(draft.warnings), draft_seconds,
                      perf_counter() - preflight_started)


def measure_paper(paper: PaperSource, scratch_root: Path) -> PaperResult:
    started = perf_counter()
    source = Path(paper.source_path)
    detection_started = perf_counter()
    try:
        detection = detect_items(source)
    except Exception as exc:  # noqa: BLE001 - retain paper-level evidence
        detail = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        return PaperResult(paper.logical_name, paper.source_path, paper.origin, "", 0, 0,
                           0, 0, 1, perf_counter() - detection_started,
                           perf_counter() - started, detail, ())
    detection_seconds = perf_counter() - detection_started
    with tempfile.TemporaryDirectory(
        prefix=f"{paper.logical_name}-", dir=scratch_root,
    ) as temporary:
        root = Path(temporary)
        items = tuple(
            _measure_item(source, item, root / f"item-{item.item_number:02d}")
            for item in detection.items
        )
    return PaperResult(
        paper.logical_name, paper.source_path, paper.origin, detection.source_hash,
        detection.page_count, len(detection.items),
        sum(value.status == "ready" for value in items),
        sum(value.status == "manual" for value in items),
        sum(value.status == "failure" for value in items),
        detection_seconds, perf_counter() - started, "", items,
    )


def _write_json(path: Path, value: PaperSource | tuple[PaperSource, ...] | RunResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(value) if not isinstance(value, tuple)
                   else [asdict(item) for item in value],
                   ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )


def run_sources(
    papers: tuple[PaperSource, ...], output: Path, scratch_root: Path,
) -> RunResult:
    scratch_root.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)
    started = perf_counter()
    results = tuple(measure_paper(paper, scratch_root) for paper in papers)
    finished_at = datetime.now(UTC)
    result = RunResult(
        1, tuple(sys.argv), started_at.isoformat(), finished_at.isoformat(),
        LayoutStyle.SUNEUNG.value,
        "per-paper TemporaryDirectory under scratch-root; deleted after measurement",
        False, len(results), sum(value.detected_count for value in results),
        sum(value.ready_count for value in results),
        sum(value.manual_count for value in results),
        sum(value.failure_count for value in results), perf_counter() - started, results,
    )
    _write_json(output, result)
    return result


def run_p1(output: Path, scratch_root: Path, limit_papers: int | None) -> RunResult:
    papers = discover_p1()
    return run_sources(
        papers if limit_papers is None else papers[:limit_papers], output, scratch_root,
    )


def _print_summary(result: RunResult) -> None:
    print(json.dumps({
        "paper_count": result.paper_count,
        "detected_count": result.detected_count,
        "ready_count": result.ready_count,
        "manual_count": result.manual_count,
        "failure_count": result.failure_count,
        "elapsed_seconds": result.elapsed_seconds,
    }, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    discover = subparsers.add_parser("discover-p1")
    discover.add_argument("--output", type=Path, required=True)
    run = subparsers.add_parser("run-p1")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--scratch-root", type=Path, required=True)
    run.add_argument("--limit-papers", type=int)
    subject_run = subparsers.add_parser("run-subjects")
    subject_run.add_argument(
        "--subjects", nargs="+", choices=("b2", "e1", "e2"), required=True,
    )
    subject_run.add_argument("--output", type=Path, required=True)
    subject_run.add_argument("--scratch-root", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "discover-p1":
        papers = discover_p1()
        _write_json(args.output, papers)
        print(json.dumps({"paper_count": len(papers)}, ensure_ascii=False))
        return 0
    if args.command == "run-p1":
        result = run_p1(args.output, args.scratch_root, args.limit_papers)
    else:
        result = run_sources(
            discover_subjects(tuple(args.subjects)), args.output, args.scratch_root,
        )
    _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
