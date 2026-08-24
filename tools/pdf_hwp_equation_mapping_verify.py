"""Re-verify inventory hashes, identity evidence, and semantic proof gates."""
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

from pdf_hwp_equation_corpus import contains_raw_pua


type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class MappingVerificationError(ValueError):
    """Raised when a verification report violates its serialization contract."""


def _file_sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_inventory(inventory_path: Path, output: Path) -> JsonObject:
    payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if contains_raw_pua(payload):
        errors.append("inventory contains raw PUA")
    if payload.get("paper_count") != 52 or payload.get("detected_item_count") != 1040:
        errors.append("inventory corpus dimensions are not 52 PDFs / 1,040 items")
    if payload.get("actual_equation_manual_count") != 163:
        errors.append("inventory equation-manual count is not 163")
    local_path = Path(LOCAL_FONT_PATH)
    if not local_path.is_file() or _file_sha(local_path) != LOCAL_FONT_SHA256:
        errors.append("local HYHWPEQ.TTF identity changed")
    seen_pdfs: dict[str, str] = {}
    rejected_occurrences = 0
    for item in payload.get("items", []):
        pdf_path = Path(item["pdf_path"])
        expected_pdf_sha = item["pdf_sha256"]
        pdf_key = str(pdf_path)
        if pdf_key not in seen_pdfs:
            seen_pdfs[pdf_key] = _file_sha(pdf_path)
        actual_pdf_sha = seen_pdfs[pdf_key]
        if actual_pdf_sha != expected_pdf_sha:
            errors.append(f"PDF hash mismatch: {pdf_path}")
        crop_path = Path(item["formula_region_crop_path"])
        if not crop_path.is_file() or _file_sha(crop_path) != item["formula_region_crop_sha256"]:
            errors.append(f"crop hash mismatch: {crop_path}")
        with fitz.open(pdf_path) as document:
            page = document[item["page_number"] - 1]
            recalculated = verify_equation_context(
                document, page, tuple(item["item_bbox"]),
            ).evidence
        if len(recalculated) != len(item["glyphs"]):
            errors.append(f"glyph occurrence count changed: {item['paper']} q{item['item_number']}")
        for glyph, current in zip(item["glyphs"], recalculated):
            expected = (
                glyph["codepoint"], glyph["pdf_font_xref"],
                glyph["embedded_font_sha256"], glyph["embedded_outline_sha256"],
                glyph["local_outline_sha256"], glyph["embedded_metrics"],
                glyph["local_metrics"], glyph["outline_and_metrics_verified"],
                glyph["verification_reason"],
            )
            observed = (
                f"U+{current.codepoint:04X}", current.font_xref,
                current.embedded_font_sha256, current.embedded_outline_sha256,
                current.local_outline_sha256,
                list(current.embedded_metrics) if current.embedded_metrics else None,
                list(current.local_metrics) if current.local_metrics else None,
                current.verified, current.reason,
            )
            if expected != observed:
                errors.append(
                    f"glyph evidence changed: {item['paper']} q{item['item_number']} "
                    f"{glyph['codepoint']}"
                )
            if not glyph["outline_and_metrics_verified"]:
                rejected_occurrences += 1
                ambiguous_resource = str(glyph["verification_reason"]).startswith(
                    "ambiguous-font-resource-xref:"
                )
                if (
                    not ambiguous_resource
                    and (glyph["pdf_font_xref"] is None or glyph["embedded_font_sha256"] is None)
                ):
                    errors.append(
                        f"rejected glyph lacks font identity: {item['paper']} "
                        f"q{item['item_number']} {glyph['codepoint']}"
                    )
                continue
            if glyph["embedded_outline_sha256"] != glyph["local_outline_sha256"]:
                errors.append(
                    f"outline mismatch marked verified: {item['paper']} q{item['item_number']} "
                    f"{glyph['codepoint']}"
                )
            if glyph["pdf_font_xref"] is None or glyph["embedded_font_sha256"] is None:
                errors.append(
                    f"verified glyph lacks font identity: {item['paper']} q{item['item_number']}"
                )
    mapping_results = []
    catalog = {**GLYPH_MAPPINGS, **STRUCTURAL_GLYPH_PROOFS}
    for entry in payload.get("mappings", []):
        label = entry["codepoint"]
        mapping = catalog.get(int(label[2:], 16))
        mapping_errors = []
        if mapping is None:
            mapping_errors.append("mapping absent from runtime catalog")
        else:
            if entry.get("mapping_source") != mapping.mapping_source:
                mapping_errors.append("mapping_source mismatch")
            if entry.get("proof") != list(mapping.proof):
                mapping_errors.append("proof manifest mismatch")
            if not entry.get("proof_satisfied"):
                mapping_errors.append("requires two papers or independent font/source evidence")
            if not entry.get("occurrences"):
                mapping_errors.append("no verified PDF occurrence")
        errors.extend(f"{label}: {message}" for message in mapping_errors)
        mapping_results.append({
            "codepoint": label, "formula": mapping.formula if mapping else None,
            "status": "verified" if not mapping_errors else "manual",
            "errors": mapping_errors,
        })
    report: JsonObject = {
        "schema_version": 1,
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "inventory_path": str(inventory_path.resolve()),
        "inventory_sha256": _file_sha(inventory_path),
        "mapping_count": len(mapping_results),
        "verified_mapping_count": sum(entry["status"] == "verified" for entry in mapping_results),
        "rejected_occurrence_count": rejected_occurrences,
        "errors": errors, "mappings": mapping_results,
        "passed": not errors,
    }
    if contains_raw_pua(report):
        raise MappingVerificationError("mapping verification report contains raw PUA")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
