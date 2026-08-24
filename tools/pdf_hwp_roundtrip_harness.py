"""Resumable approved-first-run PDF-to-HWP command-line harness."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import json
from pathlib import Path
import sys

from pydantic import ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.pdf_hwp_pipeline_models import LayoutStyle
from app.pdf_hwp_roundtrip_backend import BackendSourcePlan, RealRoundTripBackend
from app.pdf_hwp_roundtrip_checkpoint import CheckpointStore, artifact_hash
from app.pdf_hwp_roundtrip_manifest import SourceCheck, load_manifest, verify_manifest_sources
from app.pdf_hwp_roundtrip_models import ArtifactHash, WorkflowStage
from app.pdf_hwp_roundtrip_reports import ReportPaths, write_reports
from app.pdf_hwp_roundtrip_runner import (
    RoundTripRunner,
    RunFailure,
    RunPolicy,
    RunStatus,
    SourceInput,
    SourceRunResult,
)
from app.pdf_hwp_roundtrip_source import (
    UnsupportedSourceError,
    derive_source_facts,
    normalize_source,
)
from app.pdf_hwp_roundtrip_units import load_prepared_units
from tools.pdf_hwp_roundtrip_harness_support import (
    ReportSource,
    finalize_reports,
    persist_run_metadata,
)
from tools.pdf_hwp_roundtrip_harness_contract import (
    Candidate,
    SourceGroup,
    candidate_selection_contract,
    create_run_namespace,
    manifest_candidates,
    selected_candidates,
)
from tools.pdf_hwp_roundtrip_live_status import live_sources, run_with_live_status


DEFAULT_MANIFEST = Path(__file__).with_name("pdf_hwp_roundtrip_approved_first_run.json")


@dataclass(frozen=True, slots=True)
class HarnessOptions:
    manifest_path: Path
    run_root: Path
    stop_after_completed_stages: int | None = None
    verify_sources: bool = False
    groups: tuple[SourceGroup, ...] = ()
    source_ids: tuple[str, ...] = ()
    max_sources: int | None = None

    def with_stop(self, value: int | None) -> "HarnessOptions":
        return replace(self, stop_after_completed_stages=value)


@dataclass(frozen=True, slots=True)
class HarnessResult:
    reports: ReportPaths
    contact_sheet: Path
    results: tuple[SourceRunResult, ...]
    expected_failures: tuple[SourceRunResult, ...] = ()
    unexpected_failures: tuple[SourceRunResult, ...] = ()


def approved_exit_code(result: HarnessResult) -> int:
    if result.unexpected_failures:
        return 1
    if not result.reports.summary.is_file():
        return 0
    try:
        summary = json.loads(result.reports.summary.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 1
    return int(summary.get("unexpected_preparation_verification_failure_count", 0) > 0)


def _failed_source(candidate: Candidate, stage: WorkflowStage, code: str, detail: str) -> SourceRunResult:
    digest = artifact_hash(candidate.path) if candidate.path.is_file() else ArtifactHash(candidate.sha256)
    failure = RunFailure(digest, candidate.path.resolve(), stage, code, detail)
    return SourceRunResult(
        candidate.path.resolve(), digest, None, RunStatus.FAILED, (), (), (failure,),
    )


def _prepare_runtime(
    candidates: tuple[Candidate, ...], options: HarnessOptions, namespace_root: Path,
    checks: dict[str, SourceCheck],
) -> tuple[
    tuple[BackendSourcePlan, ...],
    tuple[SourceInput, ...],
    tuple[tuple[Candidate, Path, SourceRunResult], ...],
]:
    plans: list[BackendSourcePlan] = []
    inputs: list[SourceInput] = []
    rejected: list[tuple[Candidate, Path, SourceRunResult]] = []
    for candidate in candidates:
        source_root = namespace_root / "rejected" / candidate.source_id
        required_checks = [checks.get(candidate.source_id)]
        if candidate.companion_pdf_path is not None:
            required_checks.append(checks.get(f"{candidate.source_id}:companion"))
        missing_check = any(check is None for check in required_checks)
        failed_check = next(
            (check for check in required_checks if check is not None and not check.ok), None,
        )
        if missing_check or failed_check is not None:
            rejected.append((candidate, source_root, _failed_source(
                candidate, WorkflowStage.ROUTE, "source_verification_failed",
                "missing verification result" if missing_check else (
                    f"source_id={failed_check.source_id},exists={failed_check.exists},"
                    f"hash_matches={failed_check.hash_matches},"
                    f"pages={failed_check.page_count_matches},"
                    f"items={failed_check.item_count_matches}"
                ),
            )))
            continue
        actual_digest = artifact_hash(candidate.path)
        source_root = namespace_root / "sources" / f"{candidate.source_id}-{actual_digest[:12]}"
        try:
            normalized = normalize_source(candidate.path, source_root / "normalized")
        except (UnsupportedSourceError, OSError) as error:
            rejected.append((candidate, source_root, _failed_source(
                candidate, WorkflowStage.ROUTE, "source_normalization_failed", str(error),
            )))
            continue
        facts = derive_source_facts(normalized.pipeline_pdf, candidate.path.name)
        plans.append(BackendSourcePlan(
            candidate.path.resolve(),
            (
                candidate.companion_pdf_path.resolve()
                if candidate.companion_pdf_path is not None
                else normalized.pipeline_pdf
            ),
            candidate.selected_numbers,
            source_root,
            candidate.header_subject,
            candidate.profile,
            LayoutStyle.SUNEUNG,
        ))
        inputs.append(SourceInput(candidate.path.resolve(), facts))
    return tuple(plans), tuple(inputs), tuple(rejected)


def _is_expected_regression_failure(
    candidate: Candidate, output_dir: Path, result: SourceRunResult,
) -> bool:
    if result.status is not RunStatus.FAILED or not candidate.regression_claims:
        return False
    if len(result.failures) != 1:
        return False
    failure = result.failures[0]
    if failure.stage is not WorkflowStage.EXTRACT or failure.code != "no_prepared_units":
        return False
    expected = {
        (int(item), code)
        for claim in candidate.regression_claims
        for _, item, code in (claim.rsplit(":", 2),)
    }
    if set(candidate.selected_numbers or ()) != {item for item, _ in expected}:
        return False
    try:
        prepared = load_prepared_units(output_dir / "prepared-units.json")
    except (OSError, ValidationError):
        return False
    observed = {
        (item.item_number, item.code.value) for item in prepared.item_failures
    }
    return observed == expected and not prepared.prepared_units


def run_harness(options: HarnessOptions) -> HarnessResult:
    """Run a bounded approved selection and atomically refresh aggregate evidence."""
    manifest = load_manifest(options.manifest_path)
    candidates = selected_candidates(
        manifest, options.groups, options.source_ids, options.max_sources,
    )
    verification = verify_manifest_sources(manifest, check_expectations=True)
    checks = {check.source_id: check for check in verification.checks}
    selection_contract = candidate_selection_contract(candidates)
    namespace, metadata = create_run_namespace(
        options.run_root, options.manifest_path, selection_contract,
    )
    persist_run_metadata(options.run_root, metadata)
    plans, inputs, rejected = _prepare_runtime(candidates, options, namespace.root, checks)
    runner = RoundTripRunner(
        RealRoundTripBackend(plans),
        CheckpointStore(namespace.root / "checkpoints"),
        RunPolicy(options.stop_after_completed_stages),
    )
    output_roots = {
        plan.original_path.resolve(): plan.output_dir.resolve() for plan in plans
    }
    output_roots.update({candidate.path.resolve(): root for candidate, root, _ in rejected})
    executed = run_with_live_status(
        runner, inputs, live_sources(candidates, output_roots),
        tuple(result for _, _, result in rejected),
        options.run_root, namespace.namespace_id,
    )
    by_path = {result.source_path: result for result in executed}
    results = tuple(by_path[candidate.path.resolve()] for candidate in candidates)
    report_sources = tuple(ReportSource(
        candidate.source_id, output_roots[candidate.path.resolve()], result,
        candidate.profile, candidate.regression_claims,
    ) for candidate, result in zip(candidates, results, strict=True))
    reports = write_reports(results, options.run_root)
    sample_groups = {
        group.value: {
            "source_ids": [candidate.source_id for candidate in manifest_candidates(manifest)
                           if candidate.group is group and candidate.source_id != "e2_2024_09"],
            "selected_source_ids": [candidate.source_id for candidate in candidates
                                    if candidate.group is group and candidate.source_id != "e2_2024_09"],
        }
        for group in SourceGroup
    }
    regression_claims = tuple(
        f"{case.case_id}:{case.item}:{case.expected}"
        for case in manifest.fixed_regressions
    )
    contact_sheet = finalize_reports(
        reports, report_sources, options.run_root, sample_groups, regression_claims,
    )
    expected_failures = tuple(
        result for candidate, source, result in zip(candidates, report_sources, results, strict=True)
        if _is_expected_regression_failure(candidate, source.output_dir, result)
    )
    expected_hashes = {result.artifact_hash for result in expected_failures}
    unexpected_failures = tuple(
        result for result in results
        if result.status is RunStatus.FAILED and result.artifact_hash not in expected_hashes
    )
    return HarnessResult(
        reports, contact_sheet, results, expected_failures, unexpected_failures,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--stop-after-completed-stages", type=int)
    parser.add_argument("--verify-sources", action="store_true")
    parser.add_argument("--group", action="append", choices=tuple(SourceGroup), default=[])
    parser.add_argument("--source-id", action="append", default=[])
    parser.add_argument("--max-sources", type=int)
    return parser


def main(argv: tuple[str, ...] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    options = HarnessOptions(
        arguments.manifest,
        arguments.run_root,
        arguments.stop_after_completed_stages,
        arguments.verify_sources,
        tuple(SourceGroup(value) for value in arguments.group),
        tuple(arguments.source_id),
        arguments.max_sources,
    )
    result = run_harness(options)
    succeeded = sum(run.status is RunStatus.SUCCEEDED for run in result.results)
    paused = sum(run.status is RunStatus.PAUSED for run in result.results)
    print(
        f"sources={len(result.results)} succeeded={succeeded} paused={paused} "
        f"expected_failed={len(result.expected_failures)} "
        f"unexpected_failed={len(result.unexpected_failures)}"
    )
    print(f"summary={result.reports.summary.resolve()}")
    print(f"failures={result.reports.failures.resolve()}")
    print(f"report={result.reports.markdown.resolve()}")
    print(f"contact_sheet={result.contact_sheet.resolve()}")
    return approved_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
