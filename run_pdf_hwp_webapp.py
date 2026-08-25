"""Open the standalone PDF to HWP desktop application."""
from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

from app.desktop_shell import run_desktop_app


def _configure_ocr_runtime() -> None:
    """Expose the separately bundled OCR packages to the frozen interpreter."""
    if not getattr(sys, "frozen", False):
        return
    runtime = Path(sys.executable).parent / "ocr_runtime"
    if runtime.is_dir() and str(runtime) not in sys.path:
        sys.path.insert(0, str(runtime))
    dll_directories = (
        runtime / "paddle" / "libs",
        runtime / "numpy.libs",
        runtime / "pandas.libs",
        runtime / "shapely.libs",
        runtime / "cv2",
    )
    existing_directories = tuple(str(path) for path in dll_directories if path.is_dir())
    if existing_directories:
        os.environ["PATH"] = os.pathsep.join((*existing_directories, os.environ.get("PATH", "")))


def main(argv: Sequence[str] | None = None) -> int:
    _configure_ocr_runtime()
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--hwp-worker"]:
        from app.integrations.hwppalette_runner import main as worker_main

        return worker_main(args[1:])
    from app.pdf_hwp_webapp import app

    return run_desktop_app(app)


if __name__ == "__main__":
    raise SystemExit(main())
