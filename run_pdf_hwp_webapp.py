"""Open the standalone PDF to HWP web app in the default browser."""
from __future__ import annotations

import threading
import os
import sys
import webbrowser
from collections.abc import Sequence
from pathlib import Path

import uvicorn


URL = "http://127.0.0.1:8633"


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

    threading.Timer(1.0, webbrowser.open, args=(URL,)).start()
    uvicorn.run(app, host="127.0.0.1", port=8633)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
