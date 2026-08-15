#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run tools/probe_additive_hwppalette_root.py
# 3. Or make executable and run:
#      chmod +x tools/probe_additive_hwppalette_root.py && ./tools/probe_additive_hwppalette_root.py
# 4. ExamPool's current Windows runtime has no uv, so the dependency-free fallback is:
#      python tools/probe_additive_hwppalette_root.py
# ──────────────────

"""Probe new-label runtime lookup and the exact immutable preflight boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import sys


REPOSITORY = Path(__file__).resolve().parents[1]
OVERLAY_ROOT = REPOSITORY / "data" / "hwppalette_additive_root"
EVIDENCE_DIRECTORY = (
    REPOSITORY
    / ".omo"
    / "evidence"
    / "pdf-hwp-generalization"
    / "additive-templates"
    / "external-root"
)


@dataclass(frozen=True, slots=True)
class LookupResult:
    """Observed HwpPalette parser selection for one new label."""

    label: str
    slot_count: int
    fragment: str
    fragment_sha256: str
    operation_count: int
    first_operation: str
    warning_count: int
    protected_registry_hit: bool
    no_asset_preflight: str


class ProbeError(RuntimeError):
    """Raised when the immutable runtime-boundary probe drifts."""


LABELS = (
    "수능정답0사진5선지",
    "수능합답0사진5선지",
    "수능정답0사진그림5선지",
)
GRAPHICAL_LABEL = "수능정답0사진그림5선지"


def file_sha256(path: Path) -> str:
    """Return an uppercase SHA-256 digest for one file."""
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def main() -> None:
    """Run parser, no-asset preflight, and figure-bearing boundary probes."""
    if not (OVERLAY_ROOT / "hwp_palette" / "cli.py").is_file():
        raise FileNotFoundError(OVERLAY_ROOT)
    os.environ["EXAMPOOL_HWPPAL_ROOT"] = str(OVERLAY_ROOT)
    sys.path.insert(0, str(REPOSITORY))

    from app.integrations import palette_registry
    from app.integrations.hwppalette import HwpPaletteProvider
    from app.pdf_hwp_hwp_preflight import preflight_unit
    from app.pdf_hwp_pipeline_models import (
        ConversionUnit,
        GraphicalChoiceAsset,
        GraphicalChoiceAssetMetadata,
        LayoutStyle,
        ManualReviewRequiredError,
    )

    provider = HwpPaletteProvider()
    if provider.root != OVERLAY_ROOT.resolve() or not provider.available():
        raise ProbeError(f"provider did not select overlay root: {provider.root}")
    child_env = provider._child_env()
    os.environ["HWPPAL_DATA_DIR"] = child_env["HWPPAL_DATA_DIR"]
    sys.path.insert(0, str(provider.root))

    from hwp_palette.model import library, parser

    lookup = library.label_lookup()
    results: list[LookupResult] = []
    markdown_by_label: dict[str, str] = {}
    for label in LABELS:
        entry = lookup.get(label)
        if entry is None:
            raise ProbeError(f"overlay label is unavailable: {label}")
        item = entry[1]
        slot_count = int(item["slot_count"])
        markdown = f"\\{label}\\\n" + "\n".join("-" for _ in range(slot_count))
        markdown_by_label[label] = markdown
        operations, warnings = parser.build_library_plan(markdown, lookup)
        if not operations or operations[0][0] != "template":
            raise ProbeError(f"overlay parser did not select template: {label}")
        fragment = library.template_path(item).resolve()
        if not fragment.is_file():
            raise ProbeError(f"overlay fragment is missing: {fragment}")
        registry_hit = palette_registry.active_template("suneung", label) is not None
        if registry_hit:
            raise ProbeError(f"protected registry unexpectedly contains: {label}")
        no_asset = preflight_unit(
            ConversionUnit(item_number=1, palette_markdown=markdown),
            LayoutStyle.SUNEUNG,
        )
        if no_asset.palette_markdown != markdown:
            raise ProbeError(f"no-asset preflight changed markdown: {label}")
        results.append(
            LookupResult(
                label=label,
                slot_count=slot_count,
                fragment=str(fragment),
                fragment_sha256=file_sha256(fragment),
                operation_count=len(operations),
                first_operation=str(operations[0][0]),
                warning_count=len(warnings),
                protected_registry_hit=registry_hit,
                no_asset_preflight="passed",
            )
        )

    graphical_assets = tuple(
        GraphicalChoiceAsset(
            image_path=REPOSITORY / f"intentionally-absent-choice-{index}.png",
            metadata=GraphicalChoiceAssetMetadata(
                source_pdf=REPOSITORY / "intentionally-absent-source.pdf",
                page_number=1,
                item_number=1,
                choice_index=index,
                dpi=300,
                width_px=100,
                height_px=100,
                asset_hash="0" * 64,
                confidence=1.0,
            ),
        )
        for index in range(1, 6)
    )
    try:
        preflight_unit(
            ConversionUnit(
                item_number=1,
                palette_markdown=markdown_by_label[GRAPHICAL_LABEL],
                graphical_choice_assets=graphical_assets,
            ),
            LayoutStyle.SUNEUNG,
        )
    except ManualReviewRequiredError as error:
        graphical_preflight = error.detail
    else:
        raise ProbeError("figure-bearing graphical preflight unexpectedly passed")

    expected = f"registered template is unavailable: {GRAPHICAL_LABEL}"
    if graphical_preflight != expected:
        raise ProbeError(
            f"unexpected figure-bearing boundary: {graphical_preflight}"
        )
    receipt = {
        "schema_version": 1,
        "status": "pass_with_boundary",
        "external_root": str(provider.root),
        "child_data_root": child_env["HWPPAL_DATA_DIR"],
        "lookup_results": [asdict(result) for result in results],
        "figure_bearing_graphical_preflight": {
            "status": "blocked_as_expected",
            "reason": graphical_preflight,
            "required_change": "authorize an existing registry/preflight discovery seam",
        },
        "live_process_restart_required": True,
    }
    EVIDENCE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    receipt_path = EVIDENCE_DIRECTORY / "probe-receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
