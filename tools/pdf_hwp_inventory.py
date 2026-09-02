from __future__ import annotations

"""Create content-neutral PDF pair inventory JSON."""

import argparse
import json
from pathlib import Path

from app.pdf_hwp_workflow import identify_pdf_pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    pairs = identify_pdf_pairs(args.pdf)
    payload = {
        "status": "PASS",
        "pairs": [
            {
                "pair_key": pair.pair_key,
                "problem": {
                    "path": str(pair.problem.path),
                    "sha256": pair.problem.sha256,
                    "page_count": pair.problem.page_count,
                    "page_size_pt": pair.problem.page_size_pt,
                    "source_kind": pair.problem.source_kind.value,
                },
                "solution": {
                    "path": str(pair.solution.path),
                    "sha256": pair.solution.sha256,
                    "page_count": pair.solution.page_count,
                    "page_size_pt": pair.solution.page_size_pt,
                    "source_kind": pair.solution.source_kind.value,
                },
            }
            for pair in pairs
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "PASS", "pairs": len(pairs), "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

