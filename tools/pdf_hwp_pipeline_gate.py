from __future__ import annotations

"""Evaluate saved stage reports and refuse downstream work after a failure."""

import argparse
import json
from pathlib import Path

from app.pdf_hwp_pipeline_gate import PipelineStage, StageResult, evaluate_pipeline_stages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = json.loads(args.state.read_text(encoding="utf-8"))
    results = tuple(StageResult.from_mapping(item, base_dir=args.state.parent) for item in payload.get("stages", ()))
    report = evaluate_pipeline_stages(results)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "PASS" if report.passed else "BLOCKED", "out": str(args.out)}, ensure_ascii=False))
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

