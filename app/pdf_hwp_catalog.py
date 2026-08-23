"""Configurable question-type catalog for PDF→HWP review drafts.

The catalog is deliberately kept outside route code so schools can replace it
with a local settings file without changing the editor or serializer.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import logger


def _catalog_path() -> Path:
    configured = os.environ.get("EXAMPOOL_PDF_HWP_TYPE_CATALOG", "")
    return Path(configured) if configured else Path(__file__).with_name("pdf_hwp_type_catalog.json")


def load_catalog() -> dict[str, Any]:
    try:
        value = json.loads(_catalog_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("PDF-HWP type catalog load failed: %s", exc)
        return {"version": "fallback", "domains": []}
    return value if isinstance(value, dict) else {"version": "fallback", "domains": []}


def domains() -> list[dict[str, Any]]:
    return [domain for domain in load_catalog().get("domains", []) if isinstance(domain, dict)]


def find_type(type_id: str | None) -> dict[str, Any] | None:
    if not type_id:
        return None
    for domain in domains():
        for type_spec in domain.get("types", []):
            if isinstance(type_spec, dict) and type_spec.get("type_id") == type_id:
                return {**type_spec, "domain": domain.get("domain", ""), "domain_label": domain.get("label", "")}
    return None


def find_domain(domain_id: str | None) -> dict[str, Any] | None:
    return next((item for item in domains() if item.get("domain") == domain_id), None)


def compatible_type(domain_id: str | None, type_id: str | None) -> dict[str, Any] | None:
    domain = find_domain(domain_id)
    if not domain:
        return None
    return next((item for item in domain.get("types", []) if item.get("type_id") == type_id), None)
