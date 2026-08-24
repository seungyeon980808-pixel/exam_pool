"""Typeset representative recovered equations into one editable HWP proof."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.pdf_hwp_final_figure_contract import (  # noqa: E402
    FinalFigureContract, FinalFigureReview, reconcile_final_figure_contract,
)
from app.pdf_hwp_graphical_choices import draft_review_detail  # noqa: E402
from app.pdf_hwp_hwp_preflight import preflight_unit  # noqa: E402
from app.pdf_hwp_pipeline import (  # noqa: E402
    build_editable_draft, detect_items, typeset_conversion,
)
from app.pdf_hwp_pipeline_models import (  # noqa: E402
    ConversionRequest, ConversionUnit, FigureAsset, FigureAssetMetadata,
    GraphicalChoiceAsset, GraphicalChoiceAssetMetadata, LayoutStyle,
)


SOURCE_ROOT = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일"
EVIDENCE_BASE = PROJECT_ROOT / ".omo/evidence/pdf-hwp-generalization/equation-glyphs"
PROFILES = {
    "initial": (
        EVIDENCE_BASE / "actual-hwp",
        (
    ("p1_2022_09", 6, r"\frac{1}{\lambda_{a}}-\frac{1}{\lambda_{b}}=\frac{1}{\lambda_{c}}"),
    ("p1_2026_11", 15, r"I=4\sqrt{2}I_{0}"),
    ("p1_2024_11", 16, r"h\frac{y_{0}}{v_{0}}"),
    ("p1_2025_09", 9, r"\bar{S_{1}S_{2}}"),
    ("p1_2022_06", 20, r"\frac{12mgh}{d^{2}}"),
        ),
    ),
    "safe-main": (
        EVIDENCE_BASE / "actual-hwp-safe-main",
        (
            ("p1_2022_09", 6, r"\frac{1}{\lambda_{a}}-\frac{1}{\lambda_{b}}=\frac{1}{\lambda_{c}}"),
            ("p1_2025_09", 19, r"\sqrt{5}v_{0}"),
            ("p1_2024_11", 16, r"h\frac{y_{0}}{v_{0}}"),
        ),
    ),
    "safe-overbar": (
        EVIDENCE_BASE / "actual-hwp-safe-overbar",
        (("p1_2026_11", 6, r"\bar{PQ}"),),
    ),
    "safe-root": (
        EVIDENCE_BASE / "actual-hwp-safe-root",
        (("p1_2026_11", 15, r"I=4\sqrt{2}I_{0}"),),
    ),
}


class EquationProofBuildError(RuntimeError):
    """Raised when a representative equation cannot enter the proof HWP."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _figure_assets(artifacts: tuple) -> tuple[FigureAsset, ...]:
    return tuple(FigureAsset(
        artifact.image_path.resolve(),
        FigureAssetMetadata.model_validate_json(
            artifact.provenance_path.read_text(encoding="utf-8")
        ),
    ) for artifact in artifacts)


def _choice_assets(artifacts: tuple) -> tuple[GraphicalChoiceAsset, ...]:
    return tuple(GraphicalChoiceAsset(
        artifact.image_path.resolve(),
        GraphicalChoiceAssetMetadata.model_validate_json(
            artifact.provenance_path.read_text(encoding="utf-8")
        ),
    ) for artifact in artifacts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=tuple(PROFILES), default="initial")
    args = parser.parse_args()
    evidence_root, selected_cases = PROFILES[args.profile]
    evidence_root.mkdir(parents=True, exist_ok=True)
    units: list[ConversionUnit] = []
    cases: list[dict] = []
    for paper, item_number, required_formula in selected_cases:
        source = (SOURCE_ROOT / f"{paper}.pdf").resolve()
        item = next(
            value for value in detect_items(source).items
            if value.item_number == item_number
        )
        draft = build_editable_draft(
            source, item, evidence_root / "assets" / f"{paper}-q{item_number}",
        )
        detail = draft_review_detail(
            item_number, draft.palette_markdown, draft.graphical_choice_assets,
        )
        if detail is not None:
            raise EquationProofBuildError(f"{paper} q{item_number}: {detail}")
        contract = reconcile_final_figure_contract(
            item_number, draft.palette_markdown, draft.figure_assets,
        )
        if isinstance(contract, FinalFigureReview):
            raise EquationProofBuildError(f"{paper} q{item_number}: {contract.detail}")
        assert isinstance(contract, FinalFigureContract)
        normalized = contract.palette_markdown.replace(" ", "")
        if required_formula not in normalized:
            raise EquationProofBuildError(
                f"{paper} q{item_number}: required formula absent: {required_formula}"
            )
        unit = ConversionUnit(
            item_number, contract.palette_markdown,
            _figure_assets(draft.figure_assets),
            _choice_assets(draft.graphical_choice_assets),
        )
        preflight_unit(unit, LayoutStyle.SUNEUNG)
        units.append(unit)
        cases.append({
            "paper": paper, "item_number": item_number,
            "source_path": str(source), "source_sha256": _sha256(source),
            "required_formula": required_formula,
        })
    asset_dirs = tuple(sorted({
        asset.image_path.parent.resolve()
        for unit in units
        for asset in (*unit.figure_assets, *unit.graphical_choice_assets)
    }, key=lambda value: str(value).casefold()))
    generated = typeset_conversion(ConversionRequest(
        job_key=f"equation-recovery-proof-{args.profile}",
        units=tuple(units), output_dir=evidence_root / "output",
        layout_style=LayoutStyle.SUNEUNG, asset_dirs=asset_dirs,
    ))
    receipt = {
        "schema_version": 1, "cases": cases,
        "hwp_path": str(generated.hwp_path.resolve()),
        "hwp_sha256": _sha256(generated.hwp_path),
        "pdf_path": str(generated.pdf_path.resolve()),
        "pdf_sha256": _sha256(generated.pdf_path),
        "manifest_path": str(generated.manifest_path.resolve()),
        "rendered_pages": [str(path.resolve()) for path in generated.rendered_pages],
    }
    receipt_path = evidence_root / "receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=True, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
