"""Overnight multi-subject audit loop. Measures only. Does not edit templates."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent
for path in (ROOT, TOOLS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from pdf_hwp_accuracy_audit import audit_pdf  # noqa: E402


PDF_DIR = ROOT / "PDF"
CORPUS_DIR = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일"
STATUS_DIR = ROOT / "data" / "pdf_hwp"
STATUS_JSON = STATUS_DIR / "overnight_status.json"
STATUS_MD = STATUS_DIR / "overnight_status.md"
LOCK = ("p1_2020_06", "p1_2021_11", "p1_2022_11", "p1_2024_11")
SUBJECTS = {
    "p1": "물리학Ⅰ",
    "c1": "화학Ⅰ",
    "c2": "화학Ⅱ",
    "b1": "생명과학Ⅰ",
    "b2": "생명과학Ⅱ",
    "e1": "지구과학Ⅰ",
    "e2": "지구과학Ⅱ",
}
LATEST_SESSIONS = ("2027_06", "2026_11", "2026_09", "2026_06", "2025_11")
PHYSICS_SESSION_GLOB = "20[2-9][0-9]_*"


def _bucket(error: str) -> str:
    text = error.lower()
    if "ambiguous-leading-subscript" in text or "ambiguous-radical" in text:
        return "equation_closed"
    if "u+" in text or "equation" in text:
        return "equation"
    if "graphical" in text or "choice" in text:
        return "graphical_choice"
    if "caption" in text:
        return "caption"
    if "slot" in text or "figure" in text or "panel" in text:
        return "figure"
    if "stopiteration" in text or "crash" in text or "zerodivision" in text:
        return "crash"
    if "empty image" in text:
        return "empty_image"
    return "other"


def _resolve(name: str) -> Path | None:
    for folder in (CORPUS_DIR, PDF_DIR):
        path = folder / f"{name}.pdf"
        if path.is_file():
            return path
    return None


def _papers(subjects: tuple[str, ...], physics_full: bool) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for subject in subjects:
        if subject == "p1" and physics_full:
            for folder in (PDF_DIR, CORPUS_DIR):
                for path in sorted(folder.glob(f"p1_{PHYSICS_SESSION_GLOB}.pdf")):
                    if path.stem not in seen:
                        found.append(path)
                        seen.add(path.stem)
            continue
        for session in LATEST_SESSIONS:
            path = _resolve(f"{subject}_{session}")
            if path is not None and path.stem not in seen:
                found.append(path)
                seen.add(path.stem)
    return found


def run_once(subjects: tuple[str, ...], physics_full: bool) -> dict:
    reports = []
    failures = []
    buckets: dict[str, int] = {}
    by_subject: dict[str, dict[str, int]] = defaultdict(lambda: {"ready": 0, "total": 0, "failed": 0, "papers": 0})
    for source in _papers(subjects, physics_full):
        subject = source.stem.split("_", 1)[0]
        print(f"audit {source.name}", flush=True)
        report = audit_pdf(source)
        reports.append({
            "paper": report["paper"],
            "subject": subject,
            "ready": report["ready"],
            "total": report["total"],
            "failed": report["failed"],
        })
        print(f"  {report['ready']}/{report['total']}", flush=True)
        stats = by_subject[subject]
        stats["ready"] += report["ready"]
        stats["total"] += report["total"]
        stats["failed"] += report["failed"]
        stats["papers"] += 1
        for row in report["failures"]:
            bucket = _bucket(str(row["error"]))
            buckets[bucket] = buckets.get(bucket, 0) + 1
            failures.append({
                "paper": report["paper"],
                "subject": subject,
                "item": row["item"],
                "page": row["page"],
                "stage": row["stage"],
                "bucket": bucket,
                "error": row["error"],
            })
            print(f"    q{row['item']} [{bucket}] {row['error'][:140]}", flush=True)
    ready = sum(item["ready"] for item in reports)
    total = sum(item["total"] for item in reports)
    lock_ok = all(
        any(item["paper"] == name and item["ready"] == item["total"] == 20 for item in reports)
        for name in LOCK
    ) if "p1" in subjects else True
    subject_rows = {
        key: {
            **value,
            "label": SUBJECTS.get(key, key),
            "rate": f"{(value['ready'] / value['total']):.1%}" if value["total"] else "0%",
        }
        for key, value in by_subject.items()
    }
    payload = {
        "updated": datetime.now().isoformat(timespec="seconds"),
        "paper_count": len(reports),
        "ready": ready,
        "total": total,
        "failed": len(failures),
        "rate": f"{(ready / total):.1%}" if total else "0%",
        "lock_20_20_ok": lock_ok,
        "buckets": buckets,
        "subjects": subject_rows,
        "papers": reports,
        "failures": failures,
    }
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# PDF→HWP overnight status",
        "",
        f"- updated: {payload['updated']}",
        f"- ready: **{ready}/{total}** ({payload['rate']})",
        f"- lock 20/20: {'OK' if lock_ok else 'BROKEN'}",
        f"- remaining: {len(failures)}",
        "",
        "## subjects",
        "",
    ]
    for key, value in subject_rows.items():
        lines.append(
            f"- {value['label']} ({key}): {value['ready']}/{value['total']} "
            f"({value['rate']}, {value['papers']} papers)"
        )
    lines.extend(("", "## buckets", ""))
    for key, count in sorted(buckets.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {key}: {count}")
    lines.extend(("", "## remaining", ""))
    for row in failures:
        lines.append(
            f"- {row['paper']} q{row['item']}: {row['bucket']} — {row['error'][:160]}"
        )
    STATUS_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "ready": ready, "total": total, "failed": len(failures),
        "rate": payload["rate"], "lock_ok": lock_ok, "buckets": buckets,
        "subjects": {key: value["rate"] for key, value in subject_rows.items()},
    }, ensure_ascii=False), flush=True)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-min", type=int, default=30)
    parser.add_argument("--until", default="")
    parser.add_argument(
        "--subjects", default="p1,c1,c2,b1,b2,e1,e2",
        help="comma-separated prefixes: p1,c1,c2,b1,b2,e1,e2",
    )
    parser.add_argument("--physics-full", action="store_true", default=True)
    parser.add_argument("--no-physics-full", action="store_true")
    args = parser.parse_args()
    subjects = tuple(item.strip() for item in args.subjects.split(",") if item.strip())
    physics_full = bool(args.physics_full) and not args.no_physics_full
    stop = None
    if args.until:
        stop = datetime.fromisoformat(args.until)
    elif not args.once:
        now = datetime.now()
        stop = datetime(now.year, now.month, now.day, 8, 0, 0) + timedelta(days=1)
    cycle = 0
    while True:
        cycle += 1
        print(f"=== overnight cycle {cycle} {datetime.now().isoformat(timespec='seconds')} ===", flush=True)
        run_once(subjects, physics_full)
        if args.once or (stop is not None and datetime.now() >= stop):
            return 0
        print(f"sleep {args.interval_min} min", flush=True)
        time.sleep(max(60, args.interval_min * 60))


if __name__ == "__main__":
    raise SystemExit(main())
