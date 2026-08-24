"""Explain every attempt-3 manual equation with coordinates and evidence needs."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

import fitz


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.pdf_hwp_equation_decode import (  # noqa: E402
    bind_leading_superscripts, split_structural_words,
)
from app.pdf_hwp_equation_types import FORMULA_PREFIX_RE, page_words  # noqa: E402
from app.pdf_hwp_pipeline import detect_items  # noqa: E402


EVIDENCE = ROOT / ".omo/evidence/pdf-hwp-generalization/equation-glyphs"
RESIDUAL = EVIDENCE / "residual-attempt-3.json"
INVENTORY = EVIDENCE / "inventory.json"
FINAL_ROOT = EVIDENCE / "followup-final-sequential"
FINAL_REPORTS = ("p1.json", "multisubject-a.json", "subjects-b2-e1-e2.json")
OUTPUT = EVIDENCE / "followup-remaining-manual-audit.json"
CODEPOINT_RE = re.compile(r"U\+[0-9A-F]{4,6}")


class ManualAuditError(ValueError):
    """Raised when stabilized manual evidence violates the audit contract."""


def _leading_geometry(row: dict) -> list[dict]:
    source = Path(row["pdf_path"])
    item = next(
        value for value in detect_items(source).items
        if value.item_number == row["item_number"]
    )
    with fitz.open(source) as document:
        words = bind_leading_superscripts(split_structural_words(
            page_words(document[row["page_number"] - 1], item.bbox)
        ))
    findings = []
    for word in words:
        run = 0
        indices = frozenset(word.superscript_indices)
        while run in indices:
            run += 1
        unresolved_super = bool(
            run and FORMULA_PREFIX_RE.match(word.raw[run:]) is None
        )
        if 0 not in word.subscript_indices and not unresolved_super:
            continue
        pua = sorted({
            f"U+{ord(char):04X}" for char in word.raw
            if 0xE000 <= ord(char) <= 0xF8FF
        })
        findings.append({
            "kind": "leading-subscript" if 0 in word.subscript_indices else "leading-superscript",
            "word_bbox": list(word.bbox),
            "codepoints": pua or ["non-PUA-script-geometry"],
            "subscript_indices": list(word.subscript_indices),
            "superscript_indices": list(word.superscript_indices),
        })
    return findings


def _causes(reason: str, rejected: list[str], paper: str, item_number: int) -> tuple[list[str], list[str]]:
    causes, needed = [], []
    if "U+E06D" in reason:
        causes.append(
            "verified HyhwpEQ structural bar could not be joined to unique numerator/denominator or overbar geometry"
        )
        needed.append(
            "independent source or coordinate evidence selecting exactly one fraction/overbar grouping"
        )
    if "ambiguous-radical" in reason:
        causes.append("radical sign, overline, and radicand candidates are not geometrically unique")
        needed.append("a unique radical extent and radicand boundary from the source layout")
    if "ambiguous-leading-subscript" in reason:
        causes.append("a leading raised/lowered run cannot be attached to one adjacent formula base safely")
        needed.append("a unique base-association rule confirmed on another independent PDF context")
    if rejected:
        causes.append("legacy non-HyhwpEQ font emits a PUA ToUnicode value; occurrence is not an equation glyph")
        needed.append("none for mapping; preserve the occurrence as manual/blank unless source text proves otherwise")
    if (paper, item_number) in {("c1_2026_06", 7), ("c2_2026_06", 13)}:
        causes.append("PUA remained outside the normalized editable-formula segment and was stopped by the final raw-PUA gate")
        needed.append("typed segment ownership evidence proving the glyph belongs to one editable formula field")
    return causes, needed


def _within_leading(glyph: dict, findings: list[dict]) -> bool:
    box = glyph["char_bbox"]
    center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    return any(
        glyph["codepoint"] in finding["codepoints"]
        and finding["word_bbox"][0] <= center[0] <= finding["word_bbox"][2]
        and finding["word_bbox"][1] <= center[1] <= finding["word_bbox"][3]
        for finding in findings
    )


def main() -> int:
    residual = json.loads(RESIDUAL.read_text(encoding="utf-8"))
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    final_items = {}
    for report_name in FINAL_REPORTS:
        report = json.loads((FINAL_ROOT / report_name).read_text(encoding="utf-8"))
        for paper in report["papers"]:
            for item in paper["items"]:
                final_items[(paper["logical_name"], item["item_number"])] = item
    indexed = {
        (row["paper"], row["item_number"]): row for row in inventory["items"]
    }
    results = []
    for row in residual["items"]:
        if row["after_status"] != "manual":
            continue
        evidence = indexed[(row["paper"], row["item_number"])]
        final = final_items[(row["paper"], row["item_number"])]
        if final["status"] != "manual":
            raise ManualAuditError(
                f"attempt-3 manual is no longer manual: {row['paper']} q{row['item_number']}"
            )
        reason = row["after_reason"]
        explicit = set(CODEPOINT_RE.findall(reason))
        mentioned = set(explicit)
        leading = _leading_geometry(row) if "ambiguous-leading-subscript" in reason else []
        mentioned.update(
            codepoint for finding in leading for codepoint in finding["codepoints"]
            if codepoint.startswith("U+")
        )
        glyphs = [
            {
                "codepoint": glyph["codepoint"],
                "font_name": glyph["font_name_decoded"],
                "pdf_font_xref": glyph["pdf_font_xref"],
                "embedded_font_sha256": glyph["embedded_font_sha256"],
                "glyph_id": glyph["glyph_id"], "char_bbox": glyph["char_bbox"],
                "origin": glyph["origin"], "font_size": glyph["font_size"],
                "verification_reason": glyph["verification_reason"],
            }
            for glyph in evidence["glyphs"]
            if glyph["codepoint"] in explicit or _within_leading(glyph, leading)
        ]
        causes, needed = _causes(
            reason, evidence["font_verification_rejections"],
            row["paper"], row["item_number"],
        )
        results.append({
            "subject": row["subject"], "paper": row["paper"],
            "item_number": row["item_number"], "page_number": row["page_number"],
            "pdf_path": row["pdf_path"], "pdf_sha256": row["pdf_sha256"],
            "item_bbox": evidence["item_bbox"],
            "codepoints": sorted(mentioned) or ["coordinate-only"],
            "glyph_evidence": glyphs, "leading_script_geometry": leading,
            "exact_causes": causes, "additional_evidence_needed": needed,
            "manual_review_reason": reason,
            "stabilized_full_manual_reason": final["detail"],
            "manual_preserved": True,
        })
    payload = {
        "schema_version": 1,
        "residual_attempt": 3,
        "residual_source": str(RESIDUAL.resolve()),
        "stabilized_full_reports": [
            str((FINAL_ROOT / name).resolve()) for name in FINAL_REPORTS
        ],
        "manual_count": len(results), "items": results,
    }
    if any(0xE000 <= ord(char) <= 0xF8FF for char in json.dumps(payload)):
        raise ManualAuditError("manual audit contains raw PUA")
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({"output": str(OUTPUT.resolve()), "manual_count": len(results)}))
    return 0 if len(results) == 20 else 1


if __name__ == "__main__":
    raise SystemExit(main())
