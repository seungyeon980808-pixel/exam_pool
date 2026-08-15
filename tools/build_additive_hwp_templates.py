#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "pyhwpx==1.7.2",
# ]
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run tools/build_additive_hwp_templates.py
# 3. Or make executable and run:
#      chmod +x tools/build_additive_hwp_templates.py && ./tools/build_additive_hwp_templates.py
# 4. ExamPool's current Windows runtime already has pyhwpx, so when uv is unavailable:
#      python tools/build_additive_hwp_templates.py
# ──────────────────

"""Build additive no-photo HWP templates without opening protected sources."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

from pyhwpx import Hwp


REPOSITORY = Path(__file__).resolve().parents[1]
TEMPLATE_DIRECTORY = REPOSITORY / "assets" / "hwp_templates"
EVIDENCE_DIRECTORY = (
    REPOSITORY / ".omo" / "evidence" / "pdf-hwp-generalization" / "additive-templates"
)
VENDORED_HWPPALETTE = REPOSITORY / "vendor" / "hwp_typesetter"
PHOTO_MARKER = "\\사진1\\"
sys.path.insert(0, str(REPOSITORY))


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    """Immutable source/target contract for one additive template."""

    label: str
    source_name: str
    source_sha256: str
    target_name: str
    slot_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BuildResult:
    """Auditable result for one promoted HWP template."""

    label: str
    source: str
    source_sha256: str
    target: str
    target_sha256: str
    target_size: int
    slot_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StagedTemplate:
    """One fully built candidate that has not reached its final path."""

    spec: TemplateSpec
    candidate: Path
    result: BuildResult


class TemplateBuildError(RuntimeError):
    """Raised when an additive HWP cannot be built without contract drift."""


SPECS = (
    TemplateSpec(
        label="수능정답0사진5선지",
        source_name="csat_direct_one_small.hwp",
        source_sha256="302559EA4D0164E92F77F250AA31159FBBBF9902CC51FD785C02A85E6FB6E777",
        target_name="csat_direct_no_photo.hwp",
        slot_names=("문항번호", "문두", "발문", "1", "2", "3", "4", "5"),
    ),
    TemplateSpec(
        label="수능합답0사진5선지",
        source_name="csat_hapdap_one_small.hwp",
        source_sha256="6953AA7B658E32DE5530630AD65A57D8A4DFCE1796A18075F5BBBCBA90ECED93",
        target_name="csat_hapdap_no_photo.hwp",
        slot_names=("문항번호", "문두", "발문", "ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5"),
    ),
    TemplateSpec(
        label="수능정답0사진그림5선지",
        source_name="csat_direct_one_large_graphical_choices.hwp",
        source_sha256="7C708263A1D0099CCBF470006A713CA035919E1EDB26132D8D61AB2833D180AD",
        target_name="csat_direct_no_prompt_graphical_choices.hwp",
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


def validate_inputs() -> None:
    """Refuse to build if a protected source changed or a target exists."""
    for spec in SPECS:
        source = TEMPLATE_DIRECTORY / spec.source_name
        target = TEMPLATE_DIRECTORY / spec.target_name
        if not source.is_file():
            raise FileNotFoundError(source)
        observed = file_sha256(source)
        if observed != spec.source_sha256:
            raise TemplateBuildError(
                f"protected source hash mismatch: {source}: {observed}"
            )
        if target.exists():
            raise FileExistsError(target)


def build_one(spec: TemplateSpec, staging_root: Path) -> StagedTemplate:
    """Delete the unique prompt-photo table from a protected-source clone."""
    source = TEMPLATE_DIRECTORY / spec.source_name
    target = TEMPLATE_DIRECTORY / spec.target_name
    staged_source = staging_root / f"source-{spec.target_name}"
    candidate = staging_root / f"candidate-{spec.target_name}"
    shutil.copy2(source, staged_source)

    sys.path.insert(0, str(VENDORED_HWPPALETTE))
    from hwp_palette.hwp import engine_library, hwp_engine

    hwp = Hwp(new=True, visible=False, register_module=True, on_quit=False)
    hwp_engine.hwp = hwp
    try:
        if not hwp.open(str(staged_source.resolve())):
            raise TemplateBuildError(f"HWP failed to open staging copy: {staged_source}")
        if not engine_library.delete_table_containing_text(PHOTO_MARKER):
            raise TemplateBuildError(
                f"unique photo table was not deleted: {staged_source}"
            )
        if not hwp.save_as(str(candidate.resolve()), format="HWP"):
            raise TemplateBuildError(f"HWP failed to save candidate: {candidate}")
    finally:
        hwp_engine.hwp = None
        hwp.quit(save=False)

    if not candidate.is_file() or candidate.stat().st_size == 0:
        raise TemplateBuildError(f"HWP candidate is absent or empty: {candidate}")
    observed_source = file_sha256(source)
    if observed_source != spec.source_sha256:
        raise TemplateBuildError(f"protected source changed after build: {source}")
    return StagedTemplate(
        spec=spec,
        candidate=candidate,
        result=BuildResult(
            label=spec.label,
            source=str(source),
            source_sha256=observed_source,
            target=str(target),
            target_sha256=file_sha256(candidate),
            target_size=candidate.stat().st_size,
            slot_names=spec.slot_names,
        ),
    )


def validate_staged(staged: tuple[StagedTemplate, ...]) -> None:
    """Recheck every source and candidate before any final path is visible."""
    if len(staged) != len(SPECS):
        raise TemplateBuildError(
            f"expected {len(SPECS)} staged templates, got {len(staged)}"
        )
    for item in staged:
        source = TEMPLATE_DIRECTORY / item.spec.source_name
        target = TEMPLATE_DIRECTORY / item.spec.target_name
        if file_sha256(source) != item.spec.source_sha256:
            raise TemplateBuildError(f"protected source changed before commit: {source}")
        if target.exists():
            raise FileExistsError(target)
        if (
            not item.candidate.is_file()
            or item.candidate.stat().st_size != item.result.target_size
            or file_sha256(item.candidate) != item.result.target_sha256
        ):
            raise TemplateBuildError(f"staged candidate changed: {item.candidate}")


def commit_all(staged: tuple[StagedTemplate, ...], receipt_path: Path) -> None:
    """Promote all candidates and the receipt, rolling back on any failure."""
    promoted: list[Path] = []
    receipt_staging = receipt_path.with_suffix(receipt_path.suffix + ".tmp")
    if receipt_staging.exists():
        raise FileExistsError(receipt_staging)
    receipt = {
        "schema_version": 1,
        "operation": "delete_unique_prompt_photo_table_from_isolated_clone",
        "photo_marker": PHOTO_MARKER,
        "results": [asdict(item.result) for item in staged],
    }
    try:
        for item in staged:
            target = Path(item.result.target)
            if target.exists():
                raise FileExistsError(target)
            item.candidate.rename(target)
            promoted.append(target)
            if file_sha256(target) != item.result.target_sha256:
                raise TemplateBuildError(f"promoted target hash mismatch: {target}")
        receipt_staging.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        receipt_staging.replace(receipt_path)
    except (OSError, TemplateBuildError):
        receipt_staging.unlink(missing_ok=True)
        for target in reversed(promoted):
            if target.parent.resolve() != TEMPLATE_DIRECTORY.resolve():
                raise TemplateBuildError(f"refusing unsafe rollback: {target}")
            target.unlink(missing_ok=True)
        raise
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


def main() -> None:
    """Build all three templates and write a machine-readable receipt."""
    from app.integrations.hwp_security import registration_valid

    if not registration_valid():
        raise TemplateBuildError(
            "HWP FilePathCheckerModule registration is not valid"
        )
    validate_inputs()
    EVIDENCE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="build-", dir=EVIDENCE_DIRECTORY) as raw_staging:
        staging_root = Path(raw_staging)
        staged = tuple(build_one(spec, staging_root) for spec in SPECS)
        validate_staged(staged)
        commit_all(staged, EVIDENCE_DIRECTORY / "build-receipt.json")


if __name__ == "__main__":
    main()
