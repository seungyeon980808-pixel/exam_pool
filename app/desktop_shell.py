"""Windows desktop shell for the standalone PDF-to-HWP application."""
from __future__ import annotations

import socket
import threading
from pathlib import Path
from types import TracebackType

import uvicorn
from fastapi import FastAPI

from .paths import data_dir


HOST = "127.0.0.1"
STARTUP_TIMEOUT_SECONDS = 15.0
SHUTDOWN_TIMEOUT_SECONDS = 15.0
GRACEFUL_SHUTDOWN_SECONDS = 3


class DesktopStartupError(RuntimeError):
    """The local application server could not become ready."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class _ReadyServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, ready: threading.Event) -> None:
        super().__init__(config)
        self._ready = ready

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        try:
            await super().startup(sockets)
        finally:
            self._ready.set()


class LoopbackServer:
    """Own the mutable lifecycle of one loopback-only Uvicorn server."""

    def __init__(self, application: FastAPI) -> None:
        self._ready = threading.Event()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((HOST, 0))
        self._socket.listen(2048)
        port = int(self._socket.getsockname()[1])
        self.url = f"http://{HOST}:{port}"
        config = uvicorn.Config(
            application,
            host=HOST,
            port=port,
            access_log=False,
            log_level="warning",
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
            timeout_keep_alive=1,
        )
        self._server = _ReadyServer(config, self._ready)
        self._thread = threading.Thread(
            target=self._serve,
            name="exampool-hwp-server",
            daemon=True,
        )

    def _serve(self) -> None:
        self._server.run(sockets=[self._socket])

    @property
    def is_running(self) -> bool:
        return self._server.started and self._thread.is_alive()

    def __enter__(self) -> LoopbackServer:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(STARTUP_TIMEOUT_SECONDS) or not self._server.started:
            self.stop()
            raise DesktopStartupError("로컬 변환 서버를 시작하지 못했습니다.")

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(SHUTDOWN_TIMEOUT_SECONDS)
        if self._thread.is_alive():
            self._server.force_exit = True
            self._thread.join(SHUTDOWN_TIMEOUT_SECONDS)
        if not self._thread.is_alive():
            self._socket.close()


def run_desktop_app(application: FastAPI) -> int:
    """Run the local service inside a dedicated Edge WebView2 window."""
    import webview

    with LoopbackServer(application) as server:
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.create_window(
            "ExamPool HWP 변환기",
            server.url,
            width=1440,
            height=900,
            min_size=(960, 640),
            background_color="#f4f2ed",
        )
        storage_path = Path(data_dir()) / "webview"
        storage_path.mkdir(parents=True, exist_ok=True)
        webview.start(
            gui="edgechromium",
            debug=False,
            private_mode=False,
            storage_path=str(storage_path),
        )
    return 0
