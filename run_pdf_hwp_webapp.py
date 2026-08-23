"""Open the standalone PDF to HWP web app in the default browser."""
from __future__ import annotations

import threading
import webbrowser

import uvicorn


URL = "http://127.0.0.1:8633"


def main() -> None:
    threading.Timer(1.0, webbrowser.open, args=(URL,)).start()
    uvicorn.run("app.pdf_hwp_webapp:app", host="127.0.0.1", port=8633)


if __name__ == "__main__":
    main()
