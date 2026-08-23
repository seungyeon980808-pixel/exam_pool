"""Bounded recovery for transient Hancom COM server faults."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .pdf_hwp_pipeline_models import (
    ConversionTypesetError,
    DocumentTypesetter,
    GeneratedDocument,
    LayoutStyle,
)


_TRANSIENT_COM_CODES: Final = (
    "-2147417851",  # RPC_E_SERVERFAULT
    "-2147418111",  # RPC_E_CALL_REJECTED
    "-2147417846",  # RPC_E_SERVERCALL_RETRYLATER
)


@dataclass(frozen=True, slots=True)
class TypesetInvocation:
    """Immutable arguments needed to restart one HWP automation process."""

    markdown: str
    output_dir: Path
    layout_style: LayoutStyle
    asset_dirs: tuple[Path, ...]


def typeset_with_transient_restart(
    typesetter: DocumentTypesetter,
    invocation: TypesetInvocation,
) -> GeneratedDocument:
    """Restart HWP once when its COM server reports a known transient fault."""
    try:
        return typesetter.typeset(
            invocation.markdown,
            invocation.output_dir,
            invocation.layout_style,
            invocation.asset_dirs,
        )
    except ConversionTypesetError as exc:
        if not any(code in exc.detail for code in _TRANSIENT_COM_CODES):
            raise
        return typesetter.typeset(
            invocation.markdown,
            invocation.output_dir,
            invocation.layout_style,
            invocation.asset_dirs,
        )
