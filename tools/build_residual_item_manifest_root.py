from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".omo" / "evidence" / "pdf-hwp-generalization"
INPUTS = (
    ("p1", EVIDENCE / "corpus-c" / "p1-22-baseline.json"),
    ("c1-c2-b1", EVIDENCE / "multisubject-a" / "multisubject-a.json"),
    ("b2-e1-e2", EVIDENCE / "corpus-c" / "b2-e1-e2-latest-five.json"),
)
OUTPUT = EVIDENCE / "residual-manual-and-failure-items.json"


def main() -> None:
    residuals: list[dict[str, object]] = []
    input_summaries: list[dict[str, object]] = []
    for scope, path in INPUTS:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        input_summaries.append(
            {
                "scope": scope,
                "path": str(path),
                "paper_count": payload["paper_count"],
                "detected_count": payload["detected_count"],
                "ready_count": payload["ready_count"],
                "manual_count": payload["manual_count"],
                "failure_count": payload["failure_count"],
            }
        )
        for paper in payload["papers"]:
            logical_name = paper["logical_name"]
            subject = logical_name.split("_", 1)[0]
            if paper.get("paper_error"):
                residuals.append(
                    {
                        "scope": scope,
                        "subject": subject,
                        "paper": logical_name,
                        "source_path": paper["source_path"],
                        "source_sha256": paper["source_hash"],
                        "item_number": None,
                        "page_number": None,
                        "status": "failure",
                        "detail": paper["paper_error"],
                        "warning_count": None,
                        "draft_seconds": None,
                        "preflight_seconds": None,
                    }
                )
            for item in paper["items"]:
                if item["status"] == "ready":
                    continue
                residuals.append(
                    {
                        "scope": scope,
                        "subject": subject,
                        "paper": logical_name,
                        "source_path": paper["source_path"],
                        "source_sha256": paper["source_hash"],
                        "item_number": item["item_number"],
                        "page_number": item["page_number"],
                        "status": item["status"],
                        "detail": item["detail"],
                        "warning_count": item["warning_count"],
                        "draft_seconds": item["draft_seconds"],
                        "preflight_seconds": item["preflight_seconds"],
                    }
                )

    status_counts = Counter(str(item["status"]) for item in residuals)
    subject_counts: dict[str, dict[str, int]] = {}
    for item in residuals:
        counts = subject_counts.setdefault(str(item["subject"]), {"manual": 0, "failure": 0})
        counts[str(item["status"])] += 1
    expected_manual = sum(row["manual_count"] for row in input_summaries)
    expected_failures = sum(row["failure_count"] for row in input_summaries)
    if status_counts["manual"] != expected_manual or status_counts["failure"] != expected_failures:
        raise RuntimeError(
            "residual manifest does not reconcile with input aggregate counts: "
            f"manual {status_counts['manual']}/{expected_manual}, "
            f"failure {status_counts['failure']}/{expected_failures}"
        )
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": input_summaries,
        "summary": {
            "residual_count": len(residuals),
            "manual_count": status_counts["manual"],
            "failure_count": status_counts["failure"],
            "by_subject": subject_counts,
        },
        "items": residuals,
    }
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["summary"], ensure_ascii=False))
    print(OUTPUT)


if __name__ == "__main__":
    main()
