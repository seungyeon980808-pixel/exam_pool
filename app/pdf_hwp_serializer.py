"""Serialize a structured QuestionDraft at the last possible moment.

The review UI never exposes this representation by default. It is intentionally
small and deterministic so a fake HWP provider can exercise the same contract.
"""
from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def serialize_draft(item: Any) -> str:
    draft = item.draft if hasattr(item, "draft") else item
    existing = _text(draft.get("palette_markdown"))
    if existing:
        return existing
    number = getattr(item, "question_number", None) or getattr(item, "source_number", None) or getattr(item, "ord", 1)
    lines = ["\\direct\\", str(number)]
    for key in ("passage", "prompt"):
        value = _text(draft.get(key))
        if value:
            lines.append(value)
    materials = draft.get("materials") or []
    for index, material in enumerate(materials, 1):
        value = _text(material.get("caption") if isinstance(material, dict) else material)
        if value:
            lines.append(f"자료 {index}: {value}")
    bogi = draft.get("bogi") or []
    if bogi:
        lines.append("<보기>")
        labels = ("ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ")
        lines.extend(f"{labels[index] if index < len(labels) else index + 1}. {_text(value)}" for index, value in enumerate(bogi))
    choices = draft.get("choices") or []
    lines.extend(f"{index}. {_text(value)}" for index, value in enumerate(choices, 1) if _text(value))
    return "\n".join(lines).strip()
