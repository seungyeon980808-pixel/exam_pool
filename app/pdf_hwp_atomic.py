"""Bounded same-directory atomic file publication for Windows contention."""
from __future__ import annotations

import os
from pathlib import Path
from time import sleep
from typing import Final


_REPLACE_ATTEMPTS: Final = 3
_REPLACE_RETRY_SECONDS: Final = 0.01


def atomic_replace(temporary: Path, target: Path) -> None:
    """Publish a prepared sibling temp file, retrying only transient access denial."""
    try:
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(temporary, target)
                return
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                sleep(_REPLACE_RETRY_SECONDS)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = ["atomic_replace"]
