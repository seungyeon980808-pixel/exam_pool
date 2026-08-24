"""Generate the audited EBS set as an editable HWP without a PDF side export."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.integrations.hwppalette import hwppalette_provider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    audit_path = args.audit.resolve()
    assets_root = args.assets.resolve()
    output_dir = args.output_dir.resolve()
    audit_payload = json.loads(audit_path.read_text(encoding="utf-8"))
    audit = audit_payload[0] if isinstance(audit_payload, list) and len(audit_payload) == 1 else audit_payload
    if audit.get("ready") != audit.get("total") or audit.get("failed") != 0:
        raise RuntimeError("refusing to typeset an audit with failures")
    item_numbers = [int(row["item"]) for row in audit["items"]]
    if item_numbers != list(range(1, 279)):
        raise RuntimeError(f"expected audited items 1..278, observed {item_numbers[:3]}..{item_numbers[-3:]}")

    item_dirs = tuple((assets_root / f"item-{number}").resolve() for number in item_numbers)
    drafts = []
    for number, folder in zip(item_numbers, item_dirs, strict=True):
        draft = folder / "draft.txt"
        if not draft.is_file():
            raise RuntimeError(f"missing audited draft for item {number}: {draft}")
        drafts.append(draft.read_text(encoding="utf-8").strip())

    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / "audited-source.md"
    output_hwp = output_dir / "2027_수능특강_물리학I_편집가능_복원.hwp"
    markdown_path.write_text(
        "\\수능과목머리말\\\n물리Ⅰ\n" + "\n\n".join(drafts) + "\n",
        encoding="utf-8",
    )
    hwppalette_provider.register_photo_dirs(item_dirs)
    output_hwp.unlink(missing_ok=True)

    runner = ROOT / "app" / "integrations" / "hwppalette_runner.py"
    pid_file = output_dir / "hwp.pid"
    command = [
        sys.executable, str(runner),
        "--markdown-file", str(markdown_path),
        "--layout-style", "suneung",
        "--output-hwp", str(output_hwp),
        "--hwp-pid-file", str(pid_file),
        "--hidden",
    ]
    completed = subprocess.run(
        command,
        cwd=hwppalette_provider.root,
        env=hwppalette_provider._child_env(),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        check=False,
    )
    if (
        completed.returncode != 0
        or not output_hwp.is_file()
        or output_hwp.stat().st_size < 1_000_000
    ):
        return completed.returncode or 1
    print(json.dumps({
        "hwp": str(output_hwp.resolve()),
        "bytes": output_hwp.stat().st_size,
        "items": len(item_numbers),
        "audit": str(audit_path),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
