"""Build auditable glyph occurrence and mapping-proof inventory JSON."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import fitz

from app.pdf_hwp_equation_font import verify_equation_context
from app.pdf_hwp_equation_glyphs import (
    GLYPH_MAPPINGS, LOCAL_FONT_PATH, LOCAL_FONT_SHA256, STRUCTURAL_GLYPH_PROOFS,
)
from app.pdf_hwp_pipeline import detect_items

from pdf_hwp_equation_corpus import (
    CorpusBaseline, contains_raw_pua, subject_counts,
)


def _file_sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_char(codepoint: int) -> str:
    if 0xE000 <= codepoint <= 0xF8FF:
        return f"U+{codepoint:04X}"
    char = chr(codepoint)
    return "<space>" if char.isspace() else char


def _decoded_font_name(value: str) -> str:
    try:
        return value.encode("latin1").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def _trace_chars(
    page: fitz.Page, clip: tuple[float, float, float, float],
) -> list[tuple[int, tuple[float, float, float, float]]]:
    bounds = fitz.Rect(clip)
    result = []
    for trace in page.get_texttrace():
        for codepoint, _glyph_id, _origin, bbox in trace.get("chars", ()):
            box = tuple(float(value) for value in bbox)
            center = fitz.Point((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            if bounds.contains(center):
                result.append((int(codepoint), box))
    return result


def _adjacent(
    chars: list[tuple[int, tuple[float, float, float, float]]],
    codepoint: int,
    bbox: tuple[float, float, float, float],
) -> dict[str, str | None]:
    matches = [
        index for index, value in enumerate(chars)
        if value[0] == codepoint and all(abs(a - b) <= 0.05 for a, b in zip(value[1], bbox))
    ]
    if len(matches) != 1:
        return {"before": None, "after": None}
    index = matches[0]
    return {
        "before": _safe_char(chars[index - 1][0]) if index else None,
        "after": _safe_char(chars[index + 1][0]) if index + 1 < len(chars) else None,
    }


def _crop_formula_region(
    page: fitz.Page,
    boxes: tuple[tuple[float, float, float, float], ...],
    output: Path,
) -> tuple[tuple[float, float, float, float], str]:
    union = fitz.Rect(boxes[0])
    for bbox in boxes[1:]:
        union.include_rect(fitz.Rect(bbox))
    union = fitz.Rect(
        max(0, union.x0 - 8), max(0, union.y0 - 8),
        min(page.rect.width, union.x1 + 8), min(page.rect.height, union.y1 + 8),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), clip=union, alpha=False).save(output)
    return tuple(float(value) for value in union), _file_sha(output)


def _glyph_record(entry, adjacent: dict[str, str | None], crop_sha: str) -> dict[str, object]:
    mapping = GLYPH_MAPPINGS.get(entry.codepoint) or STRUCTURAL_GLYPH_PROOFS.get(
        entry.codepoint
    )
    return {
        "codepoint": f"U+{entry.codepoint:04X}",
        "font_name": entry.font_name,
        "font_name_decoded": _decoded_font_name(entry.font_name),
        "pdf_font_xref": entry.font_xref,
        "embedded_font_sha256": entry.embedded_font_sha256,
        "local_hyhwpeq_sha256": LOCAL_FONT_SHA256,
        "glyph_id": entry.glyph_id,
        "local_glyph_id": entry.local_glyph_id,
        "glyph_identifier": f"texttrace-gid-{entry.glyph_id}",
        "embedded_outline_sha256": entry.embedded_outline_sha256,
        "local_outline_sha256": entry.local_outline_sha256,
        "embedded_metrics": entry.embedded_metrics,
        "local_metrics": entry.local_metrics,
        "outline_and_metrics_verified": entry.verified,
        "char_bbox": list(entry.bbox),
        "origin": list(entry.origin),
        "font_size": entry.font_size,
        "adjacent_characters": adjacent,
        "formula_region_crop_sha256": crop_sha,
        "mapping_status": "verified" if entry.verified and mapping is not None else "manual",
        "mapping_source": mapping.mapping_source if mapping is not None else None,
        "proof": list(mapping.proof) if mapping is not None else [],
        "verification_reason": entry.reason,
    }


def _mapping_proofs(items: list[dict[str, object]]) -> list[dict[str, object]]:
    by_codepoint: dict[str, list[dict[str, object]]] = {}
    for item in items:
        for glyph in item["glyphs"]:
            if glyph["mapping_source"] is not None and glyph["outline_and_metrics_verified"]:
                by_codepoint.setdefault(glyph["codepoint"], []).append({
                    "paper": item["paper"], "item_number": item["item_number"],
                    "page_number": item["page_number"],
                    "pdf_sha256": item["pdf_sha256"],
                    "crop_sha256": item["formula_region_crop_sha256"],
                })
    result = []
    for codepoint, occurrences in sorted(by_codepoint.items()):
        numeric = int(codepoint[2:], 16)
        mapping = GLYPH_MAPPINGS.get(numeric) or STRUCTURAL_GLYPH_PROOFS[numeric]
        unique = {(entry["paper"], entry["item_number"]) for entry in occurrences}
        papers = sorted({entry["paper"] for entry in occurrences})
        independent = any(proof.startswith("local-HyhwpEQ") for proof in mapping.proof)
        result.append({
            "codepoint": codepoint,
            "formula": mapping.formula,
            "mapping_source": mapping.mapping_source,
            "proof": list(mapping.proof),
            "distinct_papers": papers,
            "distinct_item_count": len(unique),
            "independent_font_sample": independent,
            "proof_satisfied": len(papers) >= 2 or independent,
            "occurrences": occurrences[:8],
        })
    return result


def build_inventory(baseline: CorpusBaseline, output: Path) -> dict[str, object]:
    crop_root = output.parent / "crops"
    items: list[dict[str, object]] = []
    detections: dict[Path, dict[int, object]] = {}
    for residual in baseline.residuals:
        if residual.pdf_path not in detections:
            detections[residual.pdf_path] = {
                item.item_number: item for item in detect_items(residual.pdf_path).items
            }
        item = detections[residual.pdf_path][residual.item_number]
        with fitz.open(residual.pdf_path) as document:
            page = document[residual.page_number - 1]
            context = verify_equation_context(document, page, item.bbox)
            if not context.evidence:
                raise ValueError(f"equation residual has no PUA evidence: {residual.paper} q{residual.item_number}")
            crop_path = crop_root / f"{residual.paper}-q{residual.item_number:02d}.png"
            crop_bbox, crop_sha = _crop_formula_region(
                page, tuple(entry.bbox for entry in context.evidence), crop_path,
            )
            chars = _trace_chars(page, item.bbox)
            glyphs = [
                _glyph_record(entry, _adjacent(chars, entry.codepoint, entry.bbox), crop_sha)
                for entry in context.evidence
            ]
        items.append({
            "pdf_path": str(residual.pdf_path), "pdf_sha256": residual.pdf_sha256,
            "subject": residual.subject, "paper": residual.paper,
            "item_number": residual.item_number, "page_number": residual.page_number,
            "item_bbox": list(item.bbox), "formula_region_bbox": list(crop_bbox),
            "formula_region_crop_path": str(crop_path.resolve()),
            "formula_region_crop_sha256": crop_sha,
            "current_status": residual.status,
            "manual_review_reason": residual.reason,
            "font_verification_rejections": list(context.rejections),
            "glyphs": glyphs,
        })
    payload: dict[str, object] = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "corpus_root": str(Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일"),
        "paper_count": 52, "detected_item_count": baseline.detected_count,
        "expected_equation_manual_count": 163,
        "actual_equation_manual_count": len(items),
        "count_difference": len(items) - 163,
        "subject_counts": subject_counts(baseline.residuals),
        "local_hyhwpeq_path": LOCAL_FONT_PATH,
        "local_hyhwpeq_sha256": LOCAL_FONT_SHA256,
        "mappings": _mapping_proofs(items), "items": items,
    }
    if contains_raw_pua(payload):
        raise ValueError("inventory serialization contains a raw PUA character")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload
