"""Prepare and typeset one recent requested-folder paper per adjacent science subject."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
import traceback


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.pdf_hwp_final_figure_contract import (  # noqa: E402
    FinalFigureContract,
    FinalFigureReview,
    reconcile_final_figure_contract,
)
from app.pdf_hwp_graphical_choices import draft_review_detail  # noqa: E402
from app.pdf_hwp_hwp_preflight import preflight_unit  # noqa: E402
from app.pdf_hwp_pipeline import (  # noqa: E402
    build_editable_draft,
    detect_items,
    typeset_conversion,
)
from app.pdf_hwp_pipeline_models import (  # noqa: E402
    ConversionRequest,
    ConversionUnit,
    FigureAsset,
    FigureAssetMetadata,
    GraphicalChoiceAsset,
    GraphicalChoiceAssetMetadata,
    LayoutStyle,
    ManualReviewRequiredError,
)


SOURCE_ROOT = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일"
SUBJECTS = ("c1", "c2", "b1", "b2", "e1", "e2")
REPRESENTATIVE_SUFFIX = "2027_06"


@dataclass(frozen=True, slots=True)
class PreparedItem:
    item_number: int
    palette_markdown: str
    figure_assets: tuple[dict, ...]
    graphical_choice_assets: tuple[dict, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _figure_assets(artifacts: tuple) -> tuple[FigureAsset, ...]:
    return tuple(
        FigureAsset(
            artifact.image_path.resolve(),
            FigureAssetMetadata.model_validate_json(
                artifact.provenance_path.read_text(encoding="utf-8")
            ),
        )
        for artifact in artifacts
    )


def _choice_assets(artifacts: tuple) -> tuple[GraphicalChoiceAsset, ...]:
    return tuple(
        GraphicalChoiceAsset(
            artifact.image_path.resolve(),
            GraphicalChoiceAssetMetadata.model_validate_json(
                artifact.provenance_path.read_text(encoding="utf-8")
            ),
        )
        for artifact in artifacts
    )


def _asset_payload(asset: FigureAsset | GraphicalChoiceAsset) -> dict:
    return {
        "image_path": str(asset.image_path.resolve()),
        "metadata": asset.metadata.model_dump(mode="json"),
    }


def _prepare_subject(subject: str, root: Path) -> dict:
    source = (SOURCE_ROOT / f"{subject}_{REPRESENTATIVE_SUFFIX}.pdf").resolve()
    subject_root = root / subject
    assets_root = subject_root / "assets"
    detection = detect_items(source)
    prepared: list[PreparedItem] = []
    skipped: list[dict] = []
    started = perf_counter()
    for item in detection.items:
        item_root = assets_root / f"item-{item.item_number:02d}"
        try:
            draft = build_editable_draft(source, item, item_root)
            detail = draft_review_detail(
                item.item_number,
                draft.palette_markdown,
                draft.graphical_choice_assets,
            )
            if detail is not None:
                raise ManualReviewRequiredError(item.item_number, detail)
            contract = reconcile_final_figure_contract(
                item.item_number,
                draft.palette_markdown,
                draft.figure_assets,
            )
            if isinstance(contract, FinalFigureReview):
                raise ManualReviewRequiredError(item.item_number, contract.detail)
            assert isinstance(contract, FinalFigureContract)
            unit = ConversionUnit(
                item.item_number,
                contract.palette_markdown,
                _figure_assets(draft.figure_assets),
                _choice_assets(draft.graphical_choice_assets),
            )
            preflight_unit(unit, LayoutStyle.SUNEUNG)
        except Exception as exc:  # Keep safe/manual items out of real output.
            skipped.append({
                "item_number": item.item_number,
                "reason": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })
            continue
        prepared.append(PreparedItem(
            item_number=unit.item_number,
            palette_markdown=unit.palette_markdown,
            figure_assets=tuple(_asset_payload(asset) for asset in unit.figure_assets),
            graphical_choice_assets=tuple(
                _asset_payload(asset) for asset in unit.graphical_choice_assets
            ),
        ))
    if not prepared:
        raise RuntimeError(f"{subject}: no preflight-safe items in {source.name}")
    return {
        "subject": subject,
        "source_path": str(source),
        "source_sha256": _sha256(source),
        "page_count": detection.page_count,
        "detected_count": len(detection.items),
        "prepared_count": len(prepared),
        "skipped_count": len(skipped),
        "prepare_seconds": perf_counter() - started,
        "items": [asdict(item) for item in prepared],
        "skipped": skipped,
        "output_dir": str((subject_root / "output").resolve()),
    }


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def prepare(root: Path, manifest: Path) -> int:
    started = perf_counter()
    payload = {
        "schema_version": 1,
        "prepared_at_utc": datetime.now(UTC).isoformat(),
        "hwp_or_pdf_generation_invoked": False,
        "source_root": str(SOURCE_ROOT),
        "representative_suffix": REPRESENTATIVE_SUFFIX,
        "subjects": [],
    }
    for subject in SUBJECTS:
        payload["subjects"].append(_prepare_subject(subject, root))
    if tuple(row["subject"] for row in payload["subjects"]) != SUBJECTS:
        raise RuntimeError("prepared manifest must contain each required subject exactly once")
    payload["prepare_elapsed_seconds"] = perf_counter() - started
    _write_json(manifest, payload)
    print(json.dumps({
        "manifest": str(manifest.resolve()),
        "subject_count": len(payload["subjects"]),
        "prepared_counts": {
            value["subject"]: value["prepared_count"] for value in payload["subjects"]
        },
        "hwp_or_pdf_generation_invoked": False,
    }, ensure_ascii=False))
    return 0


def _restore_figure(payload: dict) -> FigureAsset:
    return FigureAsset(
        Path(payload["image_path"]),
        FigureAssetMetadata.model_validate(payload["metadata"]),
    )


def _restore_choice(payload: dict) -> GraphicalChoiceAsset:
    return GraphicalChoiceAsset(
        Path(payload["image_path"]),
        GraphicalChoiceAssetMetadata.model_validate(payload["metadata"]),
    )


def _validate_prepared_manifest(prepared: dict) -> None:
    if prepared.get("schema_version") != 1:
        raise RuntimeError("unsupported prepared manifest schema")
    if prepared.get("hwp_or_pdf_generation_invoked") is not False:
        raise RuntimeError("prepared manifest generation flag must be false")
    subjects = prepared.get("subjects")
    if not isinstance(subjects, list):
        raise RuntimeError("prepared manifest subjects must be a list")
    names = tuple(row.get("subject") for row in subjects)
    if names != SUBJECTS:
        raise RuntimeError(
            f"prepared manifest must contain exactly {SUBJECTS}, observed {names}"
        )
    source_root = SOURCE_ROOT.resolve()
    for row in subjects:
        subject = row["subject"]
        items = row.get("items")
        if not isinstance(items, list) or not items:
            raise RuntimeError(f"{subject}: prepared manifest has no safe items")
        if row.get("prepared_count") != len(items):
            raise RuntimeError(f"{subject}: prepared item count does not match items")
        source = Path(row["source_path"]).resolve()
        if source.parent != source_root or source.name != f"{subject}_{REPRESENTATIVE_SUFFIX}.pdf":
            raise RuntimeError(f"{subject}: source escaped the requested representative set")
        if not source.is_file() or _sha256(source) != row.get("source_sha256"):
            raise RuntimeError(f"{subject}: source file is missing or changed")


def typeset(manifest: Path, receipt: Path) -> int:
    prepared = json.loads(manifest.read_text(encoding="utf-8"))
    _validate_prepared_manifest(prepared)
    result = {
        "schema_version": 1,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "artifact_marker_expected_pdf_count": len(SUBJECTS),
        "subjects": [],
    }
    for subject in prepared["subjects"]:
        units = tuple(
            ConversionUnit(
                item["item_number"],
                item["palette_markdown"],
                tuple(_restore_figure(value) for value in item["figure_assets"]),
                tuple(_restore_choice(value) for value in item["graphical_choice_assets"]),
            )
            for item in subject["items"]
        )
        asset_dirs = tuple(sorted({
            asset.image_path.parent.resolve()
            for unit in units
            for asset in (*unit.figure_assets, *unit.graphical_choice_assets)
        }, key=lambda value: str(value).casefold()))
        started = perf_counter()
        row = {
            "subject": subject["subject"],
            "source_path": subject["source_path"],
            "source_sha256": subject["source_sha256"],
            "item_numbers": [unit.item_number for unit in units],
            "item_count": len(units),
        }
        try:
            from app.integrations.hwppalette_runner import subject_header_from_source
            generated = typeset_conversion(ConversionRequest(
                job_key=f"adjacent-{subject['subject']}-{REPRESENTATIVE_SUFFIX}",
                units=units,
                output_dir=Path(subject["output_dir"]),
                layout_style=LayoutStyle.SUNEUNG,
                asset_dirs=asset_dirs,
                header_subject=subject_header_from_source(subject["source_path"]) or "",
            ))
        except Exception as exc:
            row.update({
                "status": "failed",
                "seconds": perf_counter() - started,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })
        else:
            row.update({
                "status": "ready",
                "seconds": perf_counter() - started,
                "hwp_path": str(generated.hwp_path.resolve()),
                "pdf_path": str(generated.pdf_path.resolve()),
                "manifest_path": str(generated.manifest_path.resolve()),
                "hwp_sha256": _sha256(generated.hwp_path),
                "pdf_sha256": _sha256(generated.pdf_path),
                "rendered_pages": [str(path.resolve()) for path in generated.rendered_pages],
            })
        result["subjects"].append(row)
        _write_json(receipt, result)
    result["finished_at_utc"] = datetime.now(UTC).isoformat()
    result["ready_count"] = sum(row["status"] == "ready" for row in result["subjects"])
    result["failed_count"] = sum(row["status"] == "failed" for row in result["subjects"])
    result["subject_count"] = len(result["subjects"])
    _write_json(receipt, result)
    print(json.dumps({
        "receipt": str(receipt.resolve()),
        "ready_count": result["ready_count"],
        "failed_count": result["failed_count"],
    }, ensure_ascii=False))
    complete = (
        result["subject_count"] == len(SUBJECTS)
        and result["ready_count"] == len(SUBJECTS)
        and result["failed_count"] == 0
    )
    return 0 if complete else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--root", type=Path, required=True)
    prepare_parser.add_argument("--manifest", type=Path, required=True)
    typeset_parser = subparsers.add_parser("typeset")
    typeset_parser.add_argument("--manifest", type=Path, required=True)
    typeset_parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        return prepare(args.root.resolve(), args.manifest.resolve())
    return typeset(args.manifest.resolve(), args.receipt.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
