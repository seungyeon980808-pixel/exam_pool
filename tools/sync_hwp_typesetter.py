# -*- coding: utf-8 -*-
"""검증된 HwpPalette 조판 런타임/양식팩을 ExamPool 배포 자산으로 동기화한다."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys


EXAMPOOL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = EXAMPOOL_ROOT.parent / "31_hwp_palette"
DEFAULT_TARGET = EXAMPOOL_ROOT / "vendor" / "hwp_typesetter"
PACK_NAMES = ("csat_science", "school_exam")


def _git(source: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source), *args], capture_output=True,
        text=True, encoding="utf-8", check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _safe_target(target: Path) -> Path:
    target = target.resolve()
    vendor = (EXAMPOOL_ROOT / "vendor").resolve()
    if vendor not in target.parents:
        raise ValueError(f"동기화 대상은 ExamPool vendor 아래여야 합니다: {target}")
    return target


def sync(source: Path, target: Path) -> dict:
    source = source.resolve()
    target = _safe_target(target)
    package = source / "hwp_palette"
    pack_dirs = [source / "typesetting_packs" / name for name in PACK_NAMES]
    if (not (package / "cli.py").is_file()
            or not all((pack_dir / "pack.json").is_file() for pack_dir in pack_dirs)):
        raise FileNotFoundError(f"HwpPalette 조판 소스/팩을 찾지 못했습니다: {source}")

    sys.path.insert(0, str(source))
    try:
        from hwp_palette.typesetter_pack import library_payload, load_pack
        manifests = [load_pack(pack_dir) for pack_dir in pack_dirs]
        libraries = [library_payload(manifest) for manifest in manifests]
    finally:
        sys.path.pop(0)

    target.mkdir(parents=True, exist_ok=True)
    runtime = target / "hwp_palette"
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.mkdir()
    for filename in ("__init__.py", "cli.py", "typesetter_pack.py"):
        shutil.copy2(package / filename, runtime / filename)
    for dirname in ("core", "model", "hwp"):
        shutil.copytree(
            package / dirname, runtime / dirname,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    seed = target / "seed_data"
    fragments = seed / "fragments"
    if fragments.exists():
        shutil.rmtree(fragments)
    fragments.mkdir(parents=True, exist_ok=True)
    library = {"서식": [], "문자": [], "템플릿": [], "양식": []}
    for pack_dir, manifest, pack_library in zip(pack_dirs, manifests, libraries):
        for category in ("양식", "템플릿"):
            entries = ([manifest["form"]] if manifest.get("form") else []) \
                      if category == "양식" else manifest["templates"]
            for entry, item in zip(entries, pack_library[category]):
                source_file = (pack_dir / entry["file"]).resolve()
                destination = fragments / f"{manifest['name']}_{Path(entry['file']).name}"
                shutil.copy2(source_file, destination)
                item["file"] = destination.name
                library[category].append(item)
    (seed / "library.json").write_text(
        json.dumps(library, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not (seed / "config.json").exists():
        (seed / "config.json").write_text("{}\n", encoding="utf-8")

    for pack_dir, manifest in zip(pack_dirs, manifests):
        pack_copy = target / "packs" / manifest["name"]
        pack_copy.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pack_dir / "pack.json", pack_copy / "pack.json")

    status = _git(source, "status", "--porcelain")
    upstream = {
        "repository": "HwpPalette",
        "source": str(source),
        "commit": _git(source, "rev-parse", "HEAD") or "unknown",
        "branch": _git(source, "branch", "--show-current") or "unknown",
        "dirty": bool(status),
        "packs": {manifest["name"]: manifest["version"] for manifest in manifests},
        "schema_version": max(manifest["schema_version"] for manifest in manifests),
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
    (target / "UPSTREAM.json").write_text(
        json.dumps(upstream, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return upstream


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args(argv)
    result = sync(args.source, args.target)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
