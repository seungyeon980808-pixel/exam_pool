"""Codex App Server JSONL client used only by the trusted local backend.

The browser receives account summaries and login URLs, never Codex credentials.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Iterator

from ..paths import BASE_DIR


class CodexAppServerError(RuntimeError):
    pass


class CodexAppServerClient:
    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._start_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._pending: dict[int, queue.Queue] = {}
        self._subscribers: set[queue.Queue] = set()
        self._next_id = 1
        self._stderr: deque[str] = deque(maxlen=30)
        self._model: str | None = None
        self._models: list[dict] | None = None
        self._active_turns: dict[str, str] = {}
        self._active_turns_lock = threading.Lock()

    @staticmethod
    def _command() -> list[str]:
        configured = os.environ.get("EXAMPOOL_CODEX_COMMAND")
        if configured:
            return [configured, "app-server"]
        names = ["codex.cmd", "codex.exe", "codex"] if os.name == "nt" else ["codex"]
        for name in names:
            found = shutil.which(name)
            if found:
                return [found, "app-server"]
        raise CodexAppServerError("Codex CLI를 찾을 수 없습니다. Codex를 먼저 설치하세요.")

    def _ensure_started(self) -> None:
        with self._start_lock:
            if self._process and self._process.poll() is None:
                return
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                self._process = subprocess.Popen(
                    self._command(), cwd=str(BASE_DIR), stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=creationflags,
                )
            except (OSError, ValueError) as exc:
                raise CodexAppServerError(f"Codex App Server를 시작하지 못했습니다: {exc}") from exc
            threading.Thread(target=self._read_stdout, daemon=True, name="codex-appserver-out").start()
            threading.Thread(target=self._read_stderr, daemon=True, name="codex-appserver-err").start()
            self._request_started("initialize", {
                "clientInfo": {"name": "exampool", "title": "ExamPool", "version": "0.2.0"}
            }, timeout=15)
            self.notify("initialized", {})

    def _read_stdout(self) -> None:
        process = self._process
        if not process or not process.stdout:
            return
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                message_id = message.get("id")
                if message_id is not None and message_id in self._pending:
                    self._pending[message_id].put(message)
                elif message.get("method"):
                    for target in list(self._subscribers):
                        target.put(message)
        finally:
            error = CodexAppServerError(self._error_message("Codex App Server 연결이 종료되었습니다."))
            for target in list(self._pending.values()) + list(self._subscribers):
                target.put(error)

    def _read_stderr(self) -> None:
        process = self._process
        if not process or not process.stderr:
            return
        for line in process.stderr:
            if line.strip():
                self._stderr.append(line.strip())

    def _error_message(self, base: str) -> str:
        return f"{base} ({self._stderr[-1]})" if self._stderr else base

    def _request_started(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        with self._write_lock:
            if not self._process or self._process.poll() is not None or not self._process.stdin:
                raise CodexAppServerError(self._error_message("Codex App Server가 실행 중이 아닙니다."))
            request_id = self._next_id
            self._next_id += 1
            target: queue.Queue = queue.Queue(maxsize=1)
            self._pending[request_id] = target
            payload = {"method": method, "id": request_id}
            if params is not None:
                payload["params"] = params
            try:
                self._process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self._process.stdin.flush()
            except OSError as exc:
                self._pending.pop(request_id, None)
                raise CodexAppServerError(f"Codex 요청을 전송하지 못했습니다: {exc}") from exc
        try:
            response = target.get(timeout=timeout)
        except queue.Empty as exc:
            raise CodexAppServerError(f"Codex 응답 시간이 초과되었습니다: {method}") from exc
        finally:
            self._pending.pop(request_id, None)
        if isinstance(response, Exception):
            raise response
        if response.get("error"):
            error = response["error"]
            raise CodexAppServerError(error.get("message") or str(error))
        return response.get("result") or {}

    def request(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        self._ensure_started()
        return self._request_started(method, params, timeout)

    def notify(self, method: str, params: dict | None = None) -> None:
        with self._write_lock:
            if not self._process or not self._process.stdin:
                raise CodexAppServerError("Codex App Server가 실행 중이 아닙니다.")
            payload = {"method": method}
            if params is not None:
                payload["params"] = params
            self._process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._process.stdin.flush()

    def account_state(self) -> dict:
        account_result = self.request("account/read", {"refreshToken": False})
        account = account_result.get("account")
        result = {
            "service_available": True,
            "signed_in": bool(account),
            "requires_openai_auth": bool(account_result.get("requiresOpenaiAuth")),
            "account": account,
            "rate_limits": None,
            "usage": None,
            "model": self.default_model(), "models": self.list_models(),
            "capabilities": self.capabilities(),
        }
        if account and account.get("type") == "chatgpt":
            try:
                result["rate_limits"] = self.request("account/rateLimits/read", timeout=15)
            except CodexAppServerError:
                pass
            try:
                result["usage"] = self.request("account/usage/read", timeout=15)
            except CodexAppServerError:
                pass
        return result

    def capabilities(self) -> dict:
        """Capabilities advertised for the signed-in model provider."""
        try:
            return self.request("modelProvider/capabilities/read", {}, timeout=15)
        except CodexAppServerError:
            return {}

    def generate_image(self, prompt: str, timeout: float = 600,
                       reference_paths: list[str] | None = None, progress=None) -> dict:
        """Generate one image through the user's ChatGPT-managed Codex session."""
        account = self.account_state().get("account")
        if not account or account.get("type") != "chatgpt":
            raise CodexAppServerError("ChatGPT 계정 로그인이 필요합니다.")
        if not self.capabilities().get("imageGeneration"):
            raise CodexAppServerError("현재 Codex 로그인에서는 이미지 생성 기능을 사용할 수 없습니다.")

        notifications: queue.Queue = queue.Queue()
        self._subscribers.add(notifications)
        try:
            if progress:
                progress(35, "ChatGPT 이미지 생성 세션을 준비하는 중")
            started = self.request("thread/start", {
                "cwd": str(Path(BASE_DIR).resolve()),
                "sandbox": "read-only", "approvalPolicy": "never", "ephemeral": True,
                "developerInstructions": (
                    "You are ExamPool's dedicated image renderer. For every request, call the "
                    "image generation capability exactly once. Do not answer with instructions, "
                    "ASCII art, SVG, or code. Return the generated image itself."
                ),
            }, timeout=30)
            thread_id = started["thread"]["id"]
            turn_input = [{"type": "text", "text": prompt}]
            for raw_path in reference_paths or []:
                path = Path(raw_path).resolve()
                if path.is_file():
                    turn_input.append({"type": "localImage", "path": str(path), "detail": "high"})
            turn = self.request("turn/start", {
                "threadId": thread_id,
                "input": turn_input,
                "cwd": str(Path(BASE_DIR).resolve()), "approvalPolicy": "never",
            }, timeout=45).get("turn") or {}
            turn_id = turn.get("id")
            if progress:
                progress(50, "레퍼런스와 장면 설명을 바탕으로 이미지 생성 중")
            image_item = None
            while True:
                try:
                    event = notifications.get(timeout=timeout)
                except queue.Empty as exc:
                    raise CodexAppServerError("이미지 생성 대기 시간이 초과되었습니다.") from exc
                if isinstance(event, Exception):
                    raise event
                params = event.get("params") or {}
                if params.get("threadId") != thread_id:
                    continue
                if turn_id and params.get("turnId") not in (None, turn_id):
                    continue
                method = event.get("method")
                item = params.get("item") or {}
                if method == "item/started" and item.get("type") == "imageGeneration" and progress:
                    progress(60, "AI가 도판을 렌더링하는 중")
                if method == "item/completed" and item.get("type") == "imageGeneration":
                    image_item = item
                    if progress:
                        progress(88, "생성된 이미지 파일을 확인하는 중")
                if method == "turn/completed":
                    completed = params.get("turn") or {}
                    if completed.get("status") == "failed":
                        error = completed.get("error") or {}
                        raise CodexAppServerError(error.get("message") or "이미지 생성에 실패했습니다.")
                    break
            if not image_item or not image_item.get("savedPath"):
                raise CodexAppServerError("생성된 이미지 파일 경로를 받지 못했습니다.")
            return image_item
        except (KeyError, TypeError) as exc:
            raise CodexAppServerError("이미지 생성 스레드를 시작하지 못했습니다.") from exc
        finally:
            self._subscribers.discard(notifications)

    def plan_image_panels(self, question_context: str) -> list[dict]:
        """Split a question into independently printable figure panels without UI steps."""
        instructions = (
            "You split Korean middle-school science questions into independently printable figures. "
            "Return only a JSON array. Each item must have id, summary, and image_prompt. "
            "Use one item for one coherent figure; use multiple items when the question compares "
            "distinct states, times, cases, experiments, or diagrams that must be inserted separately. "
            "Do not merge distinct figures into one canvas. Maximum six items."
        )
        _, deltas = self.stream_turn(
            None,
            "Analyze this question and return its figure panels:\n" + question_context,
            instructions,
            model="gpt-5.6-luna", reasoning_effort="low",
        )
        text = "".join(deltas).strip()
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            raise CodexAppServerError("그림 장면 분리 결과를 해석하지 못했습니다.")
        try:
            value = json.loads(text[start:end + 1])
        except (json.JSONDecodeError, TypeError) as exc:
            raise CodexAppServerError("그림 장면 분리 결과가 올바른 JSON이 아닙니다.") from exc
        panels = []
        for index, item in enumerate(value[:6] if isinstance(value, list) else []):
            if not isinstance(item, dict):
                continue
            prompt = str(item.get("image_prompt") or item.get("summary") or "").strip()
            if not prompt:
                continue
            panels.append({
                "id": str(item.get("id") or f"panel-{index + 1}"),
                "summary": str(item.get("summary") or prompt),
                "image_prompt": prompt,
            })
        if not panels:
            raise CodexAppServerError("생성할 그림 장면을 찾지 못했습니다.")
        return panels

    def default_model(self) -> str | None:
        """Use the app-server advertised default instead of a possibly newer config override."""
        if self._model:
            return self._model
        try:
            models = self.list_models()
        except CodexAppServerError:
            return None
        selected = next((m for m in models if m.get("isDefault")), None)
        if not selected and models:
            selected = models[0]
        if selected:
            self._model = selected.get("id") or selected.get("model")
        return self._model

    def list_models(self) -> list[dict]:
        """Return the trusted app-server model catalog in a UI-safe shape."""
        if self._models is not None:
            return self._models
        result = self.request("model/list", {"includeHidden": False}, timeout=15)
        raw_models = result.get("data") or result.get("models") or []
        models = []
        for item in raw_models:
            model_id = item.get("id") or item.get("model")
            if not model_id or item.get("hidden"):
                continue
            efforts = []
            for option in item.get("supportedReasoningEfforts") or []:
                effort = option.get("reasoningEffort") if isinstance(option, dict) else option
                if effort:
                    efforts.append(str(effort))
            models.append({
                "id": model_id,
                "display_name": item.get("displayName") or model_id,
                "description": item.get("description") or "",
                "is_default": bool(item.get("isDefault")),
                "default_reasoning_effort": item.get("defaultReasoningEffort") or "",
                "supported_reasoning_efforts": efforts,
            })
        self._models = models
        return models

    def start_login(self) -> dict:
        return self.request("account/login/start", {"type": "chatgpt"}, timeout=30)

    def _start_or_resume_thread(self, thread_id: str | None, developer_instructions: str,
                                model: str | None = None,
                                reasoning_effort: str | None = None) -> str:
        model = model or self.default_model()
        if thread_id:
            try:
                params = {
                    "threadId": thread_id, "sandbox": "read-only",
                    "approvalPolicy": "never", "cwd": str(Path(BASE_DIR).resolve()),
                }
                if model:
                    params["model"] = model
                if reasoning_effort:
                    params["effort"] = reasoning_effort
                result = self.request("thread/resume", params, timeout=30)
                return result["thread"]["id"]
            except (CodexAppServerError, KeyError, TypeError):
                pass
        params = {
            "cwd": str(Path(BASE_DIR).resolve()), "sandbox": "read-only",
            "approvalPolicy": "never", "developerInstructions": developer_instructions,
            "ephemeral": False,
        }
        if model:
            params["model"] = model
        if reasoning_effort:
            params["effort"] = reasoning_effort
        result = self.request("thread/start", params, timeout=30)
        try:
            return result["thread"]["id"]
        except (KeyError, TypeError) as exc:
            raise CodexAppServerError("Codex 스레드 ID를 받지 못했습니다.") from exc

    def stream_turn(self, thread_id: str | None, message: str,
                    developer_instructions: str, model: str | None = None,
                    reasoning_effort: str | None = None) -> tuple[str, Iterator[str]]:
        account = self.account_state().get("account")
        if not account:
            raise CodexAppServerError("ChatGPT 로그인이 필요합니다.")
        if account.get("type") != "chatgpt":
            raise CodexAppServerError("ExamPool은 API 키 로그인을 사용하지 않습니다. ChatGPT 계정으로 로그인하세요.")
        active_thread_id = self._start_or_resume_thread(
            thread_id, developer_instructions, model=model,
            reasoning_effort=reasoning_effort)
        notifications: queue.Queue = queue.Queue()
        self._subscribers.add(notifications)
        try:
            turn_params = {
                "threadId": active_thread_id,
                "input": [{"type": "text", "text": message}],
                "cwd": str(Path(BASE_DIR).resolve()),
                "approvalPolicy": "never",
            }
            model = model or self.default_model()
            if model:
                turn_params["model"] = model
            if reasoning_effort:
                turn_params["effort"] = reasoning_effort
            turn = self.request("turn/start", turn_params, timeout=45).get("turn") or {}
            turn_id = turn.get("id")
            if turn_id:
                with self._active_turns_lock:
                    self._active_turns[active_thread_id] = turn_id
        except Exception:
            self._subscribers.discard(notifications)
            raise

        def deltas() -> Iterator[str]:
            try:
                while True:
                    try:
                        event = notifications.get(timeout=300)
                    except queue.Empty as exc:
                        raise CodexAppServerError("Codex 응답 대기 시간이 초과되었습니다.") from exc
                    if isinstance(event, Exception):
                        raise event
                    params = event.get("params") or {}
                    if params.get("threadId") != active_thread_id:
                        continue
                    if turn_id and params.get("turnId") not in (None, turn_id):
                        continue
                    method = event.get("method")
                    if method == "item/agentMessage/delta":
                        yield params.get("delta") or ""
                    elif method == "turn/completed":
                        completed = params.get("turn") or {}
                        if completed.get("status") == "failed":
                            error = completed.get("error") or {}
                            raise CodexAppServerError(error.get("message") or "Codex 응답 생성에 실패했습니다.")
                        return
            finally:
                self._subscribers.discard(notifications)
                with self._active_turns_lock:
                    if self._active_turns.get(active_thread_id) == turn_id:
                        self._active_turns.pop(active_thread_id, None)

        return active_thread_id, deltas()

    def interrupt_turn(self, thread_id: str) -> bool:
        with self._active_turns_lock:
            turn_id = self._active_turns.get(thread_id)
        if not turn_id:
            return False
        self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=15)
        return True

    def close(self) -> None:
        with self._start_lock:
            if self._process and self._process.poll() is None:
                self._process.terminate()
            self._process = None
            self._model = None
            self._models = None
            with self._active_turns_lock:
                self._active_turns.clear()

    def restart(self) -> None:
        """Restart the local app-server so it reloads Codex CLI login state."""
        self.close()
        self._ensure_started()


codex_app_server = CodexAppServerClient()
