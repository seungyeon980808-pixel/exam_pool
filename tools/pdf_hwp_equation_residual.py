"""Rerun only the 163 fresh equation/manual items after a code change."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from app.pdf_hwp_pipeline import build_editable_draft, detect_items
from app.pdf_hwp_pipeline_models import UnsupportedDraftLayoutError

from pdf_hwp_equation_corpus import (
    CorpusBaseline, contains_raw_pua, subject_counts,
)


_FORMULA_RE = re.compile(r"\[\[formula:(.*?)\]\]")


def _has_raw_pua(value: str) -> bool:
    return any(0xE000 <= ord(char) <= 0xF8FF for char in value)


def run_residual(
    baseline: CorpusBaseline, output: Path, attempt: int,
) -> dict[str, object]:
    if attempt not in {1, 2, 3}:
        raise ValueError("attempt must be 1, 2, or 3")
    detections: dict[Path, dict[int, object]] = {}
    results: list[dict[str, object]] = []
    with TemporaryDirectory(prefix=f"pdf-hwp-equation-attempt-{attempt}-") as scratch:
        scratch_root = Path(scratch)
        for index, residual in enumerate(baseline.residuals, 1):
            if residual.pdf_path not in detections:
                detections[residual.pdf_path] = {
                    item.item_number: item for item in detect_items(residual.pdf_path).items
                }
            item = detections[residual.pdf_path][residual.item_number]
            try:
                draft = build_editable_draft(
                    residual.pdf_path, item,
                    scratch_root / residual.paper / f"q{residual.item_number:02d}",
                )
                fields = (draft.source_text, *draft.choice_texts, draft.palette_markdown)
                raw_pua = any(_has_raw_pua(value) for value in fields)
                status = "failure" if raw_pua else "ready"
                detail = "raw PUA escaped into editable fields" if raw_pua else ""
                formulas = sorted(set(_FORMULA_RE.findall("\n".join(fields))))
            except UnsupportedDraftLayoutError as error:
                status, detail, formulas = "manual", error.detail, []
            except Exception as error:  # noqa: BLE001 - report hard failures verbatim
                status = "failure"
                detail = f"{type(error).__name__}: {error}"
                formulas = []
            results.append({
                "subject": residual.subject, "paper": residual.paper,
                "pdf_path": str(residual.pdf_path), "pdf_sha256": residual.pdf_sha256,
                "item_number": residual.item_number, "page_number": residual.page_number,
                "before_status": residual.status, "before_reason": residual.reason,
                "after_status": status, "after_reason": detail,
                "editable_formulas": formulas,
            })
            if index % 10 == 0 or index == len(baseline.residuals):
                print(f"run-residual attempt {attempt}: {index}/{len(baseline.residuals)}", flush=True)
    ready = sum(entry["after_status"] == "ready" for entry in results)
    manual = sum(entry["after_status"] == "manual" for entry in results)
    failure = sum(entry["after_status"] == "failure" for entry in results)
    after_subject = {
        subject: {
            "before_equation_manual": subject_counts(baseline.residuals)[subject],
            "resolved_ready": sum(
                entry["subject"] == subject and entry["after_status"] == "ready"
                for entry in results
            ),
            "remaining_manual": sum(
                entry["subject"] == subject and entry["after_status"] == "manual"
                for entry in results
            ),
            "failure": sum(
                entry["subject"] == subject and entry["after_status"] == "failure"
                for entry in results
            ),
        }
        for subject in ("p1", "c1", "c2", "b1", "b2", "e1", "e2")
    }
    report: dict[str, object] = {
        "schema_version": 1, "attempt": attempt,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_equation_manual_count": len(baseline.residuals),
        "ready_count": ready, "manual_count": manual, "failure_count": failure,
        "corpus_ready_before": baseline.ready_count,
        "corpus_ready_after": baseline.ready_count + ready,
        "corpus_manual_after": baseline.manual_count - ready - failure,
        "corpus_failure_after": baseline.failure_count + failure,
        "subject_counts": after_subject, "items": results,
    }
    if contains_raw_pua(report):
        raise ValueError("residual report contains raw PUA")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
