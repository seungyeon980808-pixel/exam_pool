"""Run the all-gates PDF/HWPX acceptance check from JSON manifests.

Example (source PDFs and outputs are local, never committed)::

    python tools/pdf_hwp_strict_qa.py --source source.pdf --generated result.pdf \
      --hwpx result.hwpx --expected expected.json --actual actual.json \
      --figures figures.json --out qa
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.pdf_hwp_strict_qa import DocumentManifest, load_manifest, run_strict_qa


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--generated", type=Path, required=True)
    parser.add_argument("--hwpx", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--actual", type=Path)
    parser.add_argument("--figures", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dpi", type=int, default=300)
    args = parser.parse_args(argv)
    expected = load_manifest(args.expected)
    actual = load_manifest(args.actual) if args.actual else None
    figures = json.loads(args.figures.read_text(encoding="utf-8")) if args.figures else []
    report = run_strict_qa(args.source, args.generated, args.hwpx, expected,
                           output_dir=args.out, actual=actual, figure_manifest=figures, dpi=args.dpi)
    report_path = args.out / "strict-qa-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS" if report.passed else "FAIL", "report": str(report_path)}, ensure_ascii=False))
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
