"""Exhaustive routing from parsed PDF facts to conversion policy."""
from __future__ import annotations

import re
from typing import Final, assert_never

from .pdf_hwp_roundtrip_models import (
    EbsRoute,
    KiceRoute,
    QuarantineReason,
    RasterRoute,
    SourceFacts,
    SourceIntegrity,
    SourceRoute,
    UnknownRoute,
    WorkflowStage,
)


_KICE_FILENAME: Final = re.compile(r"^[a-z]\d?_(?:19|20)\d{2}_(?:06|09|11)\.pdf$", re.IGNORECASE)
_EBS_SOURCE_ID: Final = re.compile(r"\[26023-\d{4}\]")


def route_source(facts: SourceFacts) -> SourceRoute:
    """Choose one typed route only when its current source evidence is proven."""
    match facts.integrity:
        case SourceIntegrity.MALFORMED:
            return UnknownRoute(QuarantineReason.MALFORMED)
        case SourceIntegrity.VALID:
            if "EBS 수능특강" in facts.identity_text and _EBS_SOURCE_ID.search(facts.source_text):
                return EbsRoute()
            if _KICE_FILENAME.fullmatch(facts.filename) and "대학수학능력시험" in facts.identity_text:
                return KiceRoute()
            if facts.page_count > 0 and facts.raster_page_count == facts.page_count:
                return RasterRoute()
            return UnknownRoute(QuarantineReason.UNRECOGNIZED)
        case unreachable:
            assert_never(unreachable)


def scheduled_stages(route: SourceRoute) -> tuple[WorkflowStage, ...]:
    """Return post-routing work; quarantined sources cannot reach HWP."""
    match route:
        case KiceRoute() | EbsRoute() | RasterRoute():
            return (WorkflowStage.EXTRACT, WorkflowStage.HWP, WorkflowStage.PDF)
        case UnknownRoute():
            return (WorkflowStage.QUARANTINE,)
        case unreachable:
            assert_never(unreachable)
