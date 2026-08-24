"""Process commands shared by source and packaged HwpPalette execution."""
from __future__ import annotations

from pathlib import Path
import sys


def hwp_runner_command() -> list[str]:
    """Return the source or bundled command prefix for ExamPool HWP work."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--hwp-worker"]
    runner = Path(__file__).with_name("hwppalette_runner.py")
    return [sys.executable, str(runner)]
