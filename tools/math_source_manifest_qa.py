"""CLI for the reviewed-PDF source manifest gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.math_source_manifest import load_and_validate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a reviewed PDF math source manifest before HWP/HWPX build")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--json", dest="json_path", type=Path, help="write the full report to this path")
    args = parser.parse_args(argv)
    report = load_and_validate(args.manifest)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_path:
        args.json_path.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report.get("status") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
