"""Audit one exam PDF with the same detect + preflight gates as the web app."""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import pdf_hwp_graphical_choices, pdf_hwp_pipeline as pipeline
from app.pdf_hwp_final_figure_contract import FinalFigureContract, FinalFigureReview
from app.pdf_hwp_final_figure_contract import reconcile_final_figure_contract
from app.pdf_hwp_hwp_preflight import preflight_unit
from app.formula_markup import validate_formula_markup
from app.pdf_hwp_pipeline_models import (
    ConversionUnit,
    CropArtifact,
    FigureArrangement,
    FigureAsset,
    FigureAssetMetadata,
    GraphicalChoiceAsset,
    GraphicalChoiceAssetMetadata,
    LayoutStyle,
    ManualReviewRequiredError,
    PanelMode,
)
from pydantic import ValidationError


_EBS_SOURCE_ID = re.compile(r"\[26023-\d{4}\]")
_EBS_COMPACT_FRACTION = re.compile(r";\d+[!@#$%^&*()];")


def _editable_text_error(item: pipeline.DetectedItem, draft: pipeline.DraftArtifact) -> str | None:
    if _EBS_SOURCE_ID.search(item.source_text) is None:
        return None
    semantic = "\n".join((draft.source_text, *draft.choice_texts))
    combined = "\n".join((semantic, draft.palette_markdown))
    if draft.palette_markdown.startswith("\\수능원문1대사진\\"):
        return "whole-item image fallback is not editable"
    if not draft.source_text.strip():
        return "editable passage/ask text is empty"
    if len(draft.choice_texts) != 5 and len(draft.graphical_choice_assets) != 5:
        return "five editable or graphical choices were not recovered"
    if _EBS_SOURCE_ID.search(combined):
        return "source identifier leaked into editable text"
    if _EBS_COMPACT_FRACTION.search(combined):
        return "legacy compact-fraction code remains"
    if "`" in combined:
        return "legacy equation spacing/subscript code remains"
    unsafe = sorted({
        f"U+{ord(char):04X}"
        for char in combined
        if (
            0xE000 <= ord(char) <= 0xF8FF
            or (0x80 <= ord(char) <= 0xFF and char not in {"°", "±", "×", "·"})
            or unicodedata.category(char) == "Cc" and char not in {"\n", "\t"}
        )
    })
    if unsafe:
        return "legacy or control characters remain: " + ", ".join(unsafe)
    formula_errors = validate_formula_markup(semantic)
    if formula_errors:
        return "; ".join(dict.fromkeys(formula_errors))
    return None


def _figure_review_error(assets: tuple[pipeline.CropArtifact, ...]) -> str | None:
    try:
        metadata = tuple(
            FigureAssetMetadata.model_validate_json(asset.provenance_path.read_text(encoding="utf-8"))
            for asset in assets
        )
    except ValidationError:
        return "invalid figure asset metadata"
    unsafe = tuple(asset for asset in metadata if asset.manual_review_required)
    if not unsafe:
        return None
    if (
        len(metadata) == 1
        and metadata[0].panel_mode in {PanelMode.SINGLE, PanelMode.COMPOSITE}
        and metadata[0].arrangement is FigureArrangement.COMPOSITE
    ):
        return None
    reasons = tuple(dict.fromkeys(
        reason for asset in unsafe for reason in asset.review_reasons if reason.strip()
    ))
    return "; ".join(reasons) or "figure separation requires manual review"


def audit_pdf(source: Path, output_dir: Path | None = None) -> dict:
    source = source.resolve()
    detected = pipeline.detect_items(source)
    owned = output_dir or Path(tempfile.mkdtemp(prefix=f"pdf-hwp-audit-{source.stem}-"))
    owned.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    for item in detected.items:
        crop_dir = owned / f"item-{item.item_number}"
        crop_dir.mkdir(parents=True, exist_ok=True)
        try:
            draft = pipeline.build_editable_draft(
                source, item, crop_dir, layout_style=LayoutStyle.SUNEUNG,
            )
        except pipeline.UnsupportedDraftLayoutError as exc:
            items.append({"item": item.item_number, "page": item.page_number, "ok": False, "stage": "draft", "error": str(exc)})
            continue
        except (pipeline.DraftExtractionError, pipeline.InvalidCropError) as exc:
            items.append({"item": item.item_number, "page": item.page_number, "ok": False, "stage": "crop", "error": str(exc)})
            continue
        except Exception as exc:
            items.append({
                "item": item.item_number,
                "page": item.page_number,
                "ok": False,
                "stage": "crash",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        (crop_dir / "draft.txt").write_text(draft.palette_markdown, encoding="utf-8")
        text_error = _editable_text_error(item, draft)
        if text_error:
            items.append({"item": item.item_number, "page": item.page_number, "ok": False, "stage": "editable-text", "error": text_error})
            continue
        choice_detail = pdf_hwp_graphical_choices.draft_review_detail(
            item.item_number, draft.palette_markdown, draft.graphical_choice_assets,
        )
        final_contract = reconcile_final_figure_contract(
            item.item_number, draft.palette_markdown, draft.figure_assets,
        )
        match final_contract:
            case FinalFigureContract(palette_markdown=markdown):
                figure_detail = None
            case FinalFigureReview(detail=figure_detail):
                markdown = draft.palette_markdown
        review = choice_detail or figure_detail or _figure_review_error(draft.figure_assets)
        if review:
            items.append({"item": item.item_number, "page": item.page_number, "ok": False, "stage": "review", "error": review})
            continue
        try:
            figure_assets = tuple(
                FigureAsset(
                    artifact.image_path,
                    FigureAssetMetadata.model_validate_json(
                        artifact.provenance_path.read_text(encoding="utf-8")
                    ),
                )
                for artifact in draft.figure_assets
            )
            choice_assets = tuple(
                GraphicalChoiceAsset(
                    artifact.image_path,
                    GraphicalChoiceAssetMetadata.model_validate_json(
                        artifact.provenance_path.read_text(encoding="utf-8")
                    ),
                )
                for artifact in draft.graphical_choice_assets
            )
        except ValidationError as exc:
            items.append({"item": item.item_number, "page": item.page_number, "ok": False, "stage": "review", "error": f"invalid persisted asset metadata: {exc}"})
            continue
        unit = ConversionUnit(
            item_number=item.item_number,
            palette_markdown=markdown,
            figure_assets=figure_assets,
            graphical_choice_assets=choice_assets,
        )
        try:
            preflight_unit(unit, LayoutStyle.SUNEUNG)
        except ManualReviewRequiredError as exc:
            items.append({"item": item.item_number, "page": item.page_number, "ok": False, "stage": "preflight", "error": exc.detail})
            continue
        items.append({"item": item.item_number, "page": item.page_number, "ok": True, "stage": "ready", "error": ""})
    failed = [row for row in items if not row["ok"]]
    return {
        "source": str(source),
        "paper": source.stem,
        "total": len(items),
        "ready": len(items) - len(failed),
        "failed": len(failed),
        "items": items,
        "failures": failed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    if args.output_dir is not None and len(args.pdfs) != 1:
        parser.error("--output-dir requires exactly one PDF")
    reports = [
        audit_pdf(path, args.output_dir if len(args.pdfs) == 1 else None)
        for path in args.pdfs
    ]
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(reports, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
        return 0
    for report in reports:
        print(f"{report['paper']}: {report['ready']}/{report['total']} ready, {report['failed']} failed")
        for row in report["failures"]:
            print(f"  q{row['item']} p{row['page']} [{row['stage']}] {row['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
