#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run tools/build_additive_hwppalette_root.py
# 3. Or make executable and run:
#      chmod +x tools/build_additive_hwppalette_root.py && ./tools/build_additive_hwppalette_root.py
# 4. ExamPool's current Windows runtime has no uv, so the dependency-free fallback is:
#      python tools/build_additive_hwppalette_root.py
# ──────────────────

"""Build a new environment-selected HwpPalette root for additive templates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory


REPOSITORY = Path(__file__).resolve().parents[1]
VENDOR_ROOT = REPOSITORY / "vendor" / "hwp_typesetter"
SEED_DATA = VENDOR_ROOT / "seed_data"
TEMPLATE_DIRECTORY = REPOSITORY / "assets" / "hwp_templates"
OUTPUT_ROOT = REPOSITORY / "data" / "hwppalette_additive_root"
EVIDENCE_DIRECTORY = (
    REPOSITORY
    / ".omo"
    / "evidence"
    / "pdf-hwp-generalization"
    / "additive-templates"
    / "external-root"
)


@dataclass(frozen=True, slots=True)
class OverlayTemplate:
    """One new runtime-library record and its immutable HWP fragment."""

    identifier: str
    label: str
    filename: str
    sha256: str
    slot_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OverlayReceiptRow:
    """Machine-readable receipt for one copied overlay fragment."""

    label: str
    fragment: str
    sha256: str
    slot_count: int


class OverlayBuildError(RuntimeError):
    """Raised when an isolated runtime root cannot be built safely."""


TEMPLATES = (
    OverlayTemplate(
        identifier="additive:csat_direct_no_photo",
        label="수능정답0사진5선지",
        filename="csat_direct_no_photo.hwp",
        sha256="F06CD29D18BD83A1A1D5ACF180A3B03A026E45C0CE3E6DD1EA459772373995F4",
        slot_names=("문항번호", "문두", "발문", "1", "2", "3", "4", "5"),
    ),
    OverlayTemplate(
        identifier="additive:csat_hapdap_no_photo",
        label="수능합답0사진5선지",
        filename="csat_hapdap_no_photo.hwp",
        sha256="8ADA531F4D3A5B4AEC672BBEC99C9AC17EFF59AA5BC3A5DBF970472E575580C4",
        slot_names=("문항번호", "문두", "발문", "ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5"),
    ),
    OverlayTemplate(
        identifier="additive:csat_direct_no_prompt_graphical_choices",
        label="수능정답0사진그림5선지",
        filename="csat_direct_no_prompt_graphical_choices.hwp",
        sha256="D7BB78A1922E7628337D5316FACFAE0CB402B7297F3B49EC59EEE3AE2EB07C13",
        slot_names=(
            "문항번호",
            "문두",
            "발문",
            "선지사진1",
            "선지사진2",
            "선지사진3",
            "선지사진4",
            "선지사진5",
        ),
    ),
)


def file_sha256(path: Path) -> str:
    """Return an uppercase SHA-256 digest for one file."""
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def copy_runtime(destination_root: Path) -> None:
    """Copy only runtime code and seed data into a wholly new root."""
    shutil.copytree(
        VENDOR_ROOT / "hwp_palette",
        destination_root / "hwp_palette",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(SEED_DATA, destination_root / "data")


def install_templates(destination_root: Path) -> tuple[OverlayReceiptRow, ...]:
    """Append new label records and copy their verified fragments."""
    data_root = destination_root / "data"
    library_path = data_root / "library.json"
    library = json.loads(library_path.read_text(encoding="utf-8"))
    records = library.get("템플릿")
    if not isinstance(records, list):
        raise OverlayBuildError("seed library has no template list")
    existing_labels = {str(record.get("label", "")) for record in records}
    fragments = data_root / "fragments"
    receipt_rows: list[OverlayReceiptRow] = []
    for template in TEMPLATES:
        if template.label in existing_labels:
            raise OverlayBuildError(f"duplicate additive label: {template.label}")
        source = TEMPLATE_DIRECTORY / template.filename
        observed = file_sha256(source)
        if observed != template.sha256:
            raise OverlayBuildError(
                f"additive template hash mismatch: {source}: {observed}"
            )
        target = fragments / template.filename
        if target.exists():
            raise FileExistsError(target)
        shutil.copy2(source, target)
        staged_hash = file_sha256(target)
        if staged_hash != template.sha256:
            raise OverlayBuildError(
                f"staged fragment hash mismatch: {target}: {staged_hash}"
            )
        source_after_copy = file_sha256(source)
        if source_after_copy != template.sha256:
            raise OverlayBuildError(
                f"additive source changed during copy: {source}: {source_after_copy}"
            )
        records.append(
            {
                "id": template.identifier,
                "name": template.label,
                "label": template.label,
                "file": template.filename,
                "slot_count": len(template.slot_names),
                "slot_names": list(template.slot_names),
                "tags": ["무사진"],
                "subcat": "csat_science",
                "category": "템플릿",
            }
        )
        receipt_rows.append(
            OverlayReceiptRow(
                label=template.label,
                fragment=str(OUTPUT_ROOT / "data" / "fragments" / template.filename),
                sha256=staged_hash,
                slot_count=len(template.slot_names),
            )
        )
    library_path.write_text(
        json.dumps(library, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return tuple(receipt_rows)


def validate_staged_root(
    staging_root: Path,
    rows: tuple[OverlayReceiptRow, ...],
) -> None:
    """Authenticate every source and staged fragment before root promotion."""
    if len(rows) != len(TEMPLATES):
        raise OverlayBuildError(
            f"expected {len(TEMPLATES)} staged fragments, got {len(rows)}"
        )
    for template, row in zip(TEMPLATES, rows, strict=True):
        source = TEMPLATE_DIRECTORY / template.filename
        target = staging_root / "data" / "fragments" / template.filename
        if file_sha256(source) != template.sha256:
            raise OverlayBuildError(f"additive source changed before commit: {source}")
        if not target.is_file() or file_sha256(target) != template.sha256:
            raise OverlayBuildError(f"staged fragment changed before commit: {target}")
        if row.sha256 != template.sha256:
            raise OverlayBuildError(f"receipt hash drift: {template.label}")
    library_path = staging_root / "data" / "library.json"
    library = json.loads(library_path.read_text(encoding="utf-8"))
    labels = {
        str(record.get("label", ""))
        for record in library.get("템플릿", ())
        if isinstance(record, dict)
    }
    missing = sorted(template.label for template in TEMPLATES if template.label not in labels)
    if missing:
        raise OverlayBuildError(f"staged library is missing labels: {missing}")


def main() -> None:
    """Create the additive runtime root and its machine-readable receipt."""
    if OUTPUT_ROOT.exists():
        raise FileExistsError(OUTPUT_ROOT)
    with TemporaryDirectory(
        prefix=".hwppalette-additive-build-",
        dir=OUTPUT_ROOT.parent,
    ) as raw_staging:
        staging_root = Path(raw_staging) / "root"
        copy_runtime(staging_root)
        rows = install_templates(staging_root)
        validate_staged_root(staging_root, rows)
        staging_root.rename(OUTPUT_ROOT)
    receipt = {
        "schema_version": 1,
        "root": str(OUTPUT_ROOT),
        "activation": "set EXAMPOOL_HWPPAL_ROOT before ExamPool process start",
        "library": str(OUTPUT_ROOT / "data" / "library.json"),
        "templates": [asdict(row) for row in rows],
    }
    EVIDENCE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    receipt_path = EVIDENCE_DIRECTORY / "build-receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
