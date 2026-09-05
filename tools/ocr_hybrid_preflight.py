"""CLI for the OCR hybrid provenance gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Support both ``python -m tools.ocr_hybrid_preflight`` and the documented
# direct invocation from the repository root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.ocr_hybrid_policy import validate_ocr_provenance
from app.math_content_scope import load_scope_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate local OCR/ExamPool/anydoc provenance")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--scope-manifest", type=Path, required=True)
    parser.add_argument("--json", dest="json_path", type=Path)
    parser.add_argument("--source-is-copyrighted", action="store_true", default=False)
    parser.add_argument("--hosted-transfer-approved", action="store_true")
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = validate_ocr_provenance(
        manifest,
        scope_manifest=load_scope_manifest(args.scope_manifest),
        source_is_copyrighted=args.source_is_copyrighted,
        hosted_transfer_approved=args.hosted_transfer_approved,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.json_path:
        args.json_path.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
