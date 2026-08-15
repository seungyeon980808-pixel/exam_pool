"""Build the complete, bounded reference packet supplied to item generation."""
from __future__ import annotations

import json

from .item_rules import FRAME_SOURCES


def _loads(value, fallback=None):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback if fallback is not None else {}


def build_reference_bundle(conn, draft: dict, references: list[dict], user_instruction: str,
                           purpose_mode: str = "create") -> dict:
    standard = None
    code = str(draft.get("standard_code") or "").strip()
    if code:
        row = conn.execute(
            "SELECT s.code,s.grade_band,s.text,s.explain,u.name AS unit_name "
            "FROM standard s LEFT JOIN unit u ON u.unit_no=s.unit_no WHERE s.code=?", (code,)
        ).fetchone()
        if row:
            standard = dict(row)

    items = []
    for raw in references:
        meta = _loads(raw.get("source_meta_json"), {})
        item = {
            "reference_id": raw.get("id"), "type": "참고 자료",
            "source_label": raw.get("source_label", ""),
            "selected_text": raw.get("source_text", ""),
            "usage": raw.get("usage", "both"), "source_meta": meta,
        }
        document_id = meta.get("document_id")
        page_no = meta.get("page_no")
        if isinstance(document_id, int) and document_id > 0:
            page = conn.execute(
                "SELECT d.doc_type,d.title,p.text FROM document d JOIN document_page p "
                "ON p.document_id=d.id WHERE d.id=? AND p.page_no=?", (document_id, page_no)
            ).fetchone()
            if page:
                item.update({"type": page["doc_type"], "document_title": page["title"],
                             "page_no": page_no, "page_text": (page["text"] or "")[:6000]})
        items.append(item)
    style_examples = {
        frame: [{**{key: row.get(key) for key in ("paper", "page", "item")},
                 "sample": str(row.get("sample") or "")[:320]}
                for row in rows[:3]]
        for frame, rows in FRAME_SOURCES.items() if rows
    }
    reconstruct = purpose_mode == "reconstruct"
    return {
        "curriculum": standard,
        "references": items,
        "style_examples": style_examples,
        "user_instruction": str(user_instruction or "").strip(),
        "purpose_mode": "reconstruct" if reconstruct else "create",
        "rules": {
            "use_only_supported_facts": True,
            "do_not_copy_exam_wording_except_registered_frames": not reconstruct,
            "allow_selected_source_exact_wording": reconstruct,
            "allow_selected_source_figure_layout": reconstruct,
            "preserve_selected_source_order_and_labels": reconstruct,
            "cite_reference_id_in_evidence": True,
        },
    }
