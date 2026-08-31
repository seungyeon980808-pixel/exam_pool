"""CLI shim for :mod:`app.endnote_qa_gate`."""

import sys
from pathlib import Path


# Permit ``python tools/endnote_qa_gate.py ...`` from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.endnote_qa_gate import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())


