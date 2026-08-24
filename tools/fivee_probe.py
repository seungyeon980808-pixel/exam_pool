"""Attach a temporary ExamPool client to the local 5E editor and print its state."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.authoring.fivee_mcp import FiveEMcpClient


SERVER = (
    Path.home() / "Desktop" / "project" / "51_5E" / "5E_main"
    / "tools" / "mcp-5e" / "server.js"
)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    client = FiveEMcpClient(SERVER, timeout=45)
    try:
        print("WAITING_FOR_5E", flush=True)
        print(client.wait_for_app(90), flush=True)
        print(client.call("read_app"), flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
