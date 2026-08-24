"""Inventory, rerun, and verify evidence for equation/manual residuals."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from pdf_hwp_equation_corpus import EVIDENCE_ROOT, load_fresh_baseline  # noqa: E402
from pdf_hwp_equation_inventory import build_inventory  # noqa: E402
from pdf_hwp_equation_mapping_verify import verify_inventory  # noqa: E402
from pdf_hwp_equation_residual import run_residual  # noqa: E402


DEFAULT_INVENTORY = EVIDENCE_ROOT / "inventory.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evidence-backed HyhwpEQ residual equation harness",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory = subparsers.add_parser("inventory", help="build exact 163-item glyph inventory")
    inventory.add_argument("--output", type=Path, default=DEFAULT_INVENTORY)
    residual = subparsers.add_parser("run-residual", help="rerun only equation/manual items")
    residual.add_argument("--attempt", type=int, choices=(1, 2, 3), default=1)
    residual.add_argument("--output", type=Path)
    mapping = subparsers.add_parser("verify-mapping", help="verify hashes and proof gates")
    mapping.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    mapping.add_argument(
        "--output", type=Path, default=EVIDENCE_ROOT / "mapping-verification.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "inventory":
        report = build_inventory(load_fresh_baseline(), args.output)
        summary = {
            "command": "inventory", "output": str(args.output.resolve()),
            "paper_count": report["paper_count"],
            "detected_item_count": report["detected_item_count"],
            "equation_manual_count": report["actual_equation_manual_count"],
            "count_difference": report["count_difference"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.command == "run-residual":
        output = args.output or EVIDENCE_ROOT / f"residual-attempt-{args.attempt}.json"
        report = run_residual(load_fresh_baseline(), output, args.attempt)
        summary = {
            "command": "run-residual", "output": str(output.resolve()),
            "attempt": args.attempt, "ready_count": report["ready_count"],
            "manual_count": report["manual_count"], "failure_count": report["failure_count"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return int(report["failure_count"] != 0)
    report = verify_inventory(args.inventory, args.output)
    summary = {
        "command": "verify-mapping", "output": str(args.output.resolve()),
        "mapping_count": report["mapping_count"],
        "verified_mapping_count": report["verified_mapping_count"],
        "rejected_occurrence_count": report["rejected_occurrence_count"],
        "passed": report["passed"], "errors": report["errors"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
