from __future__ import annotations

"""Apply STRICT/PRACTICAL editable-document gates to evidence manifests."""

import argparse
import json
from pathlib import Path

from app.pdf_hwp_workflow import EditableDocumentEvidence, audit_editable_workflow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--actual", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--coordinate-tolerance", type=float, default=0.02)
    parser.add_argument("--practical-page-delta-ratio", type=float, default=0.20)
    args = parser.parse_args(argv)
    expected = EditableDocumentEvidence.from_mapping(json.loads(args.expected.read_text(encoding="utf-8")))
    actual = EditableDocumentEvidence.from_mapping(json.loads(args.actual.read_text(encoding="utf-8")))
    report = audit_editable_workflow(
        expected,
        actual,
        coordinate_tolerance=args.coordinate_tolerance,
        practical_page_delta_ratio=args.practical_page_delta_ratio,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report.status.value, "out": str(args.out)}, ensure_ascii=False))
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

