from __future__ import annotations

"""Build or statically audit native Hanword endnotes."""

import argparse
import json
from pathlib import Path

from app.pdf_hwp_native_endnote import (
    DEFAULT_MATH_ITEM_IDS,
    audit_native_endnotes,
    build_native_endnote_hwp,
    inspect_hwpx_native_endnotes,
    load_native_endnote_job,
)


def _build(job_path: Path, visible: bool) -> int:
    job = load_native_endnote_job(job_path)
    report = build_native_endnote_hwp(job, visible=visible)
    target = job.output_hwp.with_suffix(".build.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(target)}, ensure_ascii=False))
    return 0


def _audit(
    hwpx: Path,
    report_path: Path,
    expected_ids_path: Path | None,
    hwp_reopen: bool,
    hwpx_reopen: bool,
    copy_follows: bool,
    move_follows: bool,
) -> int:
    expected = DEFAULT_MATH_ITEM_IDS
    if expected_ids_path:
        expected = tuple(json.loads(expected_ids_path.read_text(encoding="utf-8"))["expected_item_ids"])
    inventory = inspect_hwpx_native_endnotes(hwpx, expected)
    report = audit_native_endnotes(
        inventory,
        expected,
        hwp_reopen=hwp_reopen,
        hwpx_reopen=hwpx_reopen,
        copy_follows=copy_follows,
        move_follows=move_follows,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "PASS" if report.passed else "FAIL", "report": str(report_path)}, ensure_ascii=False))
    return 0 if report.passed else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--job", type=Path, required=True)
    build_parser.add_argument("--visible", action="store_true")
    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--hwpx", type=Path, required=True)
    audit_parser.add_argument("--out", type=Path, required=True)
    audit_parser.add_argument("--expected-ids", type=Path)
    audit_parser.add_argument("--hwp-reopen", action="store_true")
    audit_parser.add_argument("--hwpx-reopen", action="store_true")
    audit_parser.add_argument("--copy-follows", action="store_true")
    audit_parser.add_argument("--move-follows", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "build":
        return _build(args.job, args.visible)
    return _audit(
        args.hwpx,
        args.out,
        args.expected_ids,
        args.hwp_reopen,
        args.hwpx_reopen,
        args.copy_follows,
        args.move_follows,
    )


if __name__ == "__main__":
    raise SystemExit(main())

