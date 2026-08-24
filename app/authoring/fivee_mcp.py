"""Small persistent JSON-RPC client for the separate 5E MCP process."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path


class FiveEMcpError(RuntimeError):
    pass


class FiveEMcpClient:
    def __init__(self, server_path: Path, timeout: float = 15):
        self.server_path = Path(server_path)
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._responses: queue.Queue[dict] = queue.Queue()
        self._lock = threading.RLock()
        self._id = 0

    def _reader(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            try:
                value = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                self._responses.put(value)

    def start(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return
            if not self.server_path.is_file():
                raise FiveEMcpError(f"5E MCP 서버가 없습니다: {self.server_path}")
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self._proc = subprocess.Popen(
                ["node", str(self.server_path)],
                cwd=str(self.server_path.parent), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", bufsize=1, creationflags=flags,
            )
            threading.Thread(target=self._reader, daemon=True).start()
            self._rpc("initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "exampool", "version": "1"},
            })

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        if not self._proc or self._proc.poll() is not None or not self._proc.stdin:
            raise FiveEMcpError("5E MCP 서버가 실행 중이 아닙니다.")
        self._id += 1
        request_id = self._id
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()
        deadline = time.monotonic() + self.timeout
        skipped: list[dict] = []
        try:
            while time.monotonic() < deadline:
                try:
                    response = self._responses.get(timeout=max(0.05, deadline - time.monotonic()))
                except queue.Empty as exc:
                    raise FiveEMcpError(f"5E MCP {method} 응답 시간이 초과되었습니다.") from exc
                if response.get("id") != request_id:
                    skipped.append(response)
                    continue
                if response.get("error"):
                    raise FiveEMcpError(str(response["error"].get("message") or response["error"]))
                return response.get("result") or {}
        finally:
            for response in skipped:
                self._responses.put(response)
        raise FiveEMcpError(f"5E MCP {method} 응답 시간이 초과되었습니다.")

    def call(self, name: str, arguments: dict | None = None):
        with self._lock:
            self.start()
            result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
            if result.get("isError"):
                message = "".join(c.get("text", "") for c in result.get("content", []))
                raise FiveEMcpError(message or f"5E {name} 실행 실패")
            content = result.get("content", [])
            if len(content) == 1 and content[0].get("type") == "text":
                return content[0].get("text", "")
            return content

    def wait_for_app(self, seconds: float = 12, href_token: str = "") -> str:
        deadline = time.monotonic() + seconds
        last = ""
        while time.monotonic() < deadline:
            last = str(self.call("app_status"))
            if last.startswith("✅") and (not href_token or href_token in last):
                return last
            time.sleep(0.5)
        if href_token and last.startswith("✅"):
            raise FiveEMcpError(
                "새로 연 5E 편집기 대신 다른 5E 창이 연결되어 있습니다. "
                "새 창을 새로고침한 뒤 다시 시도하세요.\n" + last
            )
        raise FiveEMcpError("5E 편집기가 MCP에 연결되지 않았습니다. 5E 창을 새로고침하세요.\n" + last)

    def close(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
            if not proc:
                return
            try:
                if proc.stdin:
                    proc.stdin.close()
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
