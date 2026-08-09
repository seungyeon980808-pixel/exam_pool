"""Figure provider boundary and the external 5E adapter.

5E remains a separate application.  The adapter only creates a compatible
project file, starts its local static server, and returns a launch URL.
"""
from __future__ import annotations

import json
import os
import base64
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Protocol

from ..integrations.hwppalette import HwpPaletteError, hwppalette_provider
from ..paths import BASE_DIR, data_dir
from .codex_app_server import CodexAppServerError, codex_app_server
from .fivee_mcp import FiveEMcpClient, FiveEMcpError


class FigureProviderError(RuntimeError):
    pass


class FigureProvider(Protocol):
    name: str

    def create(self, session_id: int, draft: dict, current: dict) -> dict: ...

    def edit(self, session_id: int, draft: dict, current: dict) -> dict: ...

    def activate(self, session_id: int, draft: dict, current: dict) -> dict: ...

    def sync(self, session_id: int, draft: dict, current: dict) -> dict: ...

    def revert(self, session_id: int, draft: dict, current: dict) -> dict: ...

    def confirm(self, session_id: int, draft: dict, current: dict) -> dict: ...


class StubFigureProvider:
    name = "stub"

    @staticmethod
    def _paths(current: dict) -> dict:
        return {
            "scene_spec_path": current.get("scene_spec_path") or "",
            "fivee_project_path": current.get("fivee_project_path") or "",
            "rendered_image_path": current.get("rendered_image_path") or "",
        }

    def create(self, session_id: int, draft: dict, current: dict) -> dict:
        return {"provider": self.name, "status": "draft", **self._paths(current)}

    def edit(self, session_id: int, draft: dict, current: dict) -> dict:
        return {"provider": self.name, "status": "editing", **self._paths(current)}

    def activate(self, session_id: int, draft: dict, current: dict) -> dict:
        return self.edit(session_id, draft, current)

    def sync(self, session_id: int, draft: dict, current: dict) -> dict:
        return self.edit(session_id, draft, current)

    def revert(self, session_id: int, draft: dict, current: dict) -> dict:
        return self.edit(session_id, draft, current)

    def confirm(self, session_id: int, draft: dict, current: dict) -> dict:
        return {"provider": self.name, "status": "confirmed", **self._paths(current)}


class FiveELocalProvider:
    """Launch the separately installed 5E app and track one project per session."""

    name = "fivee_local"
    schema_version = "0.17"
    _server_process: subprocess.Popen | None = None
    _mcp_client: FiveEMcpClient | None = None

    def __init__(self, root: Path | None = None, port: int | None = None):
        configured = os.environ.get("EXAMPOOL_5E_ROOT")
        sibling = BASE_DIR.parent / "51_5E" / "5E_main"
        self.root = Path(root or configured or sibling).resolve()
        # 8190은 5E 개별 브랜치 서버들이 오래 사용해 충돌이 잦다. 통합 런처의
        # 전용 포트 8611을 기본으로 써 기존 작업 창을 건드리지 않는다.
        self.port = int(port or os.environ.get("EXAMPOOL_5E_PORT", "8611"))

    @property
    def launch_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?from=exampool&t={int(time.time())}"

    def _check_installation(self) -> None:
        required = (
            self.root / "index.html", self.root / "tools" / "serve.py",
            self.root / "tools" / "mcp-5e" / "server.js",
        )
        if not all(path.is_file() for path in required):
            raise FigureProviderError(
                "5E 설치 위치를 찾지 못했습니다. EXAMPOOL_5E_ROOT에 5E_main 경로를 지정하세요."
            )

    def _project_path(self, session_id: int, current: dict) -> Path:
        existing = current.get("fivee_project_path")
        if existing:
            return Path(existing).resolve()
        folder = data_dir() / "authoring_figures" / f"session_{session_id}"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / "figure.5e.json"

    @staticmethod
    def _new_project(session_id: int, draft: dict) -> dict:
        stamp = int(time.time() * 1000)
        page_id = f"page_exampool_{session_id}_{stamp}"
        title = str(draft.get("title") or draft.get("ask") or f"문항 {session_id}").strip()
        return {
            "version": "0.17",
            "pages": [{
                "id": page_id,
                "name": title[:40] or f"문항 {session_id}",
                "meta": {"number": "", "points": ""},
                "objects": [],
                "guides": [],
                "layers": [
                    {"id": 1, "name": "레이어 1", "visible": True},
                    {"id": 2, "name": "레이어 2", "visible": True},
                    {"id": 3, "name": "레이어 3", "visible": True},
                ],
                "artboard": {"w": 90, "h": 60},
            }],
            "activePageId": page_id,
        }

    def _server_is_ready(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=0.25):
                return True
        except OSError:
            return False

    def _ensure_server(self) -> None:
        self._check_installation()
        if self._server_is_ready():
            return
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        self.__class__._server_process = subprocess.Popen(
            [sys.executable, str(self.root / "tools" / "serve.py"), str(self.port), str(self.root)],
            cwd=str(self.root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self._server_is_ready():
                return
            if self._server_process.poll() is not None:
                break
            time.sleep(0.1)
        raise FigureProviderError(f"5E 로컬 서버를 {self.port}번 포트에서 시작하지 못했습니다.")

    def _result(
        self, path: Path, status: str, scene_spec_path: str = "",
        rendered_image_path: str = "", material: str = "",
    ) -> dict:
        return {
            "provider": self.name,
            "status": status,
            "scene_spec_path": scene_spec_path,
            "fivee_project_path": str(path),
            "rendered_image_path": rendered_image_path,
            **({"material": material} if material else {}),
            "launch_url": self.launch_url,
            "instructions": "5E의 파일 → 열기에서 이 프로젝트를 연 뒤, 같은 파일에 저장하세요.",
        }

    def _mcp(self) -> FiveEMcpClient:
        self._check_installation()
        if self.__class__._mcp_client is None:
            self.__class__._mcp_client = FiveEMcpClient(
                self.root / "tools" / "mcp-5e" / "server.js"
            )
        self.__class__._mcp_client.start()
        return self.__class__._mcp_client

    @staticmethod
    def _figure_name(session_id: int, draft: dict, current: dict) -> str:
        raw = current.get("figure_name") or draft.get("material") or f"draft_{session_id}"
        raw = str(raw).split(",", 1)[0].strip()
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in raw)
        return safe.strip("._") or f"draft_{session_id}"

    @staticmethod
    def _project_object_count(path: Path) -> int:
        try:
            project = json.loads(path.read_text(encoding="utf-8"))
            return sum(len(page.get("objects") or []) for page in project.get("pages") or [])
        except (OSError, TypeError, ValueError):
            return 0

    @staticmethod
    def _same_plan(scene_path: Path, plan: dict) -> bool:
        try:
            return json.loads(scene_path.read_text(encoding="utf-8")) == plan
        except (OSError, ValueError):
            return False

    def _materialize_project(
        self, client: FiveEMcpClient, path: Path, scene_path: Path,
        session_id: int, draft: dict, plan: dict,
    ) -> None:
        """Build into a staging file so a failed add never leaves a blank project."""
        if self._project_object_count(path) and self._same_plan(scene_path, plan):
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(path.stem + ".pending" + path.suffix)
        artboard = plan.get("artboard") if isinstance(plan.get("artboard"), dict) else {"w": 90, "h": 60}
        title = str(draft.get("title") or draft.get("ask") or f"문항 {session_id}").strip()[:40]
        try:
            client.call("create_project", {
                "path": str(staging), "artboard": artboard,
                "pageNames": [title or f"문항 {session_id}"], "overwrite": True,
            })
            client.call("add_objects", {
                "path": str(staging), "objects": plan["objects"], "group": True,
            })
            if not self._project_object_count(staging):
                raise FigureProviderError("5E가 빈 프로젝트를 반환했습니다. 그림 객체를 다시 확인해 주세요.")
            os.replace(staging, path)
            scene_path.write_text(
                json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except FiveEMcpError as exc:
            raise FigureProviderError(f"5E 그림 생성 실패: {exc}") from exc
        finally:
            if staging.exists():
                staging.unlink()

    def _render_preview(
        self, client: FiveEMcpClient, session_id: int, draft: dict,
        current: dict, path: Path, plan: dict,
    ) -> tuple[Path, str]:
        """Render through a private headless 5E tab; the editor stays optional."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise FigureProviderError(
                "자동 그림 렌더러가 설치되지 않았습니다. `pip install playwright` 후 다시 실행해 주세요."
            ) from exc

        name = self._figure_name(session_id, draft, current)
        output_dir = hwppalette_provider.photo_dir(str(current.get("short_code") or "").strip())
        hwppalette_provider.register_photo_dir(output_dir)
        token = uuid.uuid4().hex
        url = f"http://127.0.0.1:{self.port}/?from=exampool&mcp=1&render={token}"
        browser = None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    if token in str(client.call("app_status")):
                        break
                    time.sleep(0.25)
                else:
                    raise FigureProviderError("백그라운드 5E 렌더러에 연결하지 못했습니다.")

                client.call("set_page", {"page": name, "create": True})
                client.call("clear_app")
                client.call("set_artboard", plan.get("artboard") or {"w": 90, "h": 60})
                client.call("add_objects", {"objects": plan["objects"], "group": True})
                client.call("read_app")
                client.call("fit_artboard", {"margin": 4, "recenter": True})
                client.call("read_app")
                client.call("export_image", {"widthPx": 1200})
                client.call("save_image", {
                    "dir": str(output_dir), "name": name, "dpi": 300,
                })
                serialized = page.evaluate("""async () => {
                    const [{ state }, { serialize }] = await Promise.all([
                      import('./js/state.js?v=1.4.0'),
                      import('./js/project-io.js?v=1.4.0')
                    ]);
                    return serialize(state.get());
                }""")
                path.write_text(
                    json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                browser.close()
                browser = None
        except FigureProviderError:
            raise
        except (FiveEMcpError, HwpPaletteError, OSError, ValueError) as exc:
            raise FigureProviderError(f"5E 자동 그림 생성 실패: {exc}") from exc
        except Exception as exc:
            raise FigureProviderError(f"5E 백그라운드 렌더링 실패: {exc}") from exc
        finally:
            if browser:
                browser.close()

        image_path = output_dir / f"{name}.png"
        if not image_path.is_file():
            raise FigureProviderError("5E 렌더링은 끝났지만 PNG 파일이 생성되지 않았습니다.")
        return image_path, name

    def _render_separate_previews(
        self, client: FiveEMcpClient, session_id: int, draft: dict,
        current: dict, path: Path, panels: list[dict],
    ) -> list[dict]:
        """Render every panel as a page tab in one editable 5E project."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise FigureProviderError(
                "자동 그림 렌더러가 설치되지 않았습니다. `pip install playwright` 후 다시 실행해 주세요."
            ) from exc
        output_dir = hwppalette_provider.photo_dir(str(current.get("short_code") or "").strip())
        hwppalette_provider.register_photo_dir(output_dir)
        base_name = self._figure_name(session_id, draft, current)
        token = uuid.uuid4().hex
        url = f"http://127.0.0.1:{self.port}/?from=exampool&mcp=1&render={token}"
        browser = None
        assets = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    if token in str(client.call("app_status")):
                        break
                    time.sleep(0.25)
                else:
                    raise FigureProviderError("백그라운드 5E 렌더러에 연결하지 못했습니다.")

                for index, panel in enumerate(panels):
                    name = f"{base_name}_{index + 1:02d}"
                    client.call("set_page", {"page": name, "create": True})
                    client.call("clear_app")
                    client.call("set_artboard", panel.get("artboard") or {"w": 90, "h": 60})
                    client.call("add_objects", {"objects": panel["objects"], "group": True})
                    client.call("read_app")
                    client.call("fit_artboard", {"margin": 4, "recenter": True})
                    fitted = json.loads(str(client.call("read_app")))
                    client.call("export_image", {"widthPx": 1200})
                    client.call("save_image", {"dir": str(output_dir), "name": name, "dpi": 300})
                    panel_dir = path.parent / f"panel_{index + 1:02d}"
                    panel_dir.mkdir(parents=True, exist_ok=True)
                    scene_path = panel_dir / "figure.scene.json"
                    scene_path.write_text(
                        json.dumps(panel, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    image_path = output_dir / f"{name}.png"
                    if not image_path.is_file():
                        raise FigureProviderError(f"{index + 1}번째 5E PNG가 생성되지 않았습니다.")
                    assets.append({
                        "panel_id": str(panel.get("id") or f"panel-{index + 1}"),
                        "ord": index + 1, "provider": "fivee_assets", "status": "draft",
                        "scene_spec_path": str(scene_path), "fivee_project_path": str(path),
                        "source_image_path": "", "rendered_image_path": str(image_path),
                        "material": name, "artboard": fitted.get("artboard") or {},
                        "page_name": name,
                    })
                serialized = page.evaluate("""async () => {
                    const [{ state }, { serialize }] = await Promise.all([
                      import('./js/state.js?v=1.4.0'),
                      import('./js/project-io.js?v=1.4.0')
                    ]);
                    return serialize(state.get());
                }""")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8")
                browser.close()
                browser = None
        except FigureProviderError:
            raise
        except (FiveEMcpError, HwpPaletteError, OSError, ValueError) as exc:
            raise FigureProviderError(f"5E 다중 탭 생성 실패: {exc}") from exc
        except Exception as exc:
            raise FigureProviderError(f"5E 다중 탭 렌더링 실패: {exc}") from exc
        finally:
            if browser:
                browser.close()
        return assets

    def create(self, session_id: int, draft: dict, current: dict) -> dict:
        progress = current.get("progress_callback")
        if callable(progress):
            progress(18, "5E 의미 객체 설계안을 확인하는 중")
        self._ensure_server()
        client = self._mcp()
        path = self._project_path(session_id, current)
        plan = draft.get("figure_plan")
        if not isinstance(plan, dict):
            raise FigureProviderError("채팅에서 그림 설계안을 요청한 뒤 ‘설계안 반영’을 먼저 누르세요.")
        if plan.get("blocked_reason"):
            raise FigureProviderError(f"5E 전용 부품이 필요합니다: {plan['blocked_reason']}")
        options = {
            "provider": "fivee_assets", "include_text": False, "composition": "combined",
            **(current.get("options") or plan.get("options") or {}),
        }
        panels = plan.get("panels")
        if not isinstance(panels, list):
            panels = [{
                "id": "main", "summary": plan.get("summary", ""),
                "artboard": plan.get("artboard") or {"w": 90, "h": 60},
                "objects": plan.get("objects") or [],
            }]
        if options["composition"] == "combined" and len(panels) > 1:
            raise FigureProviderError("한 도판에 그리기 설계안은 하나의 통합 패널이어야 합니다. 설계안을 다시 요청하세요.")

        assets = []
        base_name = self._figure_name(session_id, draft, current)
        normalized_panels = []
        for index, raw_panel in enumerate(panels):
            if not isinstance(raw_panel, dict):
                continue
            panel = dict(raw_panel)
            objects = panel.get("objects") or []
            if not isinstance(objects, list) or not objects:
                raise FigureProviderError(f"{index + 1}번째 패널에 생성할 5E 객체가 없습니다.")
            if not options["include_text"]:
                objects = [obj for obj in objects if obj.get("type") not in {"text", "formula"}]
                if not objects:
                    raise FigureProviderError("글자 제거 후 남은 그림 객체가 없습니다. 그림 설계안을 다시 요청하세요.")
            panel["objects"] = objects
            normalized_panels.append(panel)

        if options["composition"] in {"auto", "separate"}:
            if callable(progress):
                progress(45, f"5E에서 {len(normalized_panels)}개 그림을 배치하는 중")
            assets = self._render_separate_previews(
                client, session_id, draft, current, path, normalized_panels
            )
        else:
            if callable(progress):
                progress(45, "5E에서 도판 객체를 배치하는 중")
            panel = normalized_panels[0]
            panel_id = str(panel.get("id") or "main")
            panel_path = path
            panel_current = current
            scene_path = panel_path.with_name("figure.scene.json")
            self._materialize_project(client, panel_path, scene_path, session_id, draft, panel)
            image_path, name = self._render_preview(
                client, session_id, draft, panel_current, panel_path, panel
            )
            assets.append({
                "panel_id": panel_id, "ord": index + 1, "provider": "fivee_assets",
                "status": "draft", "scene_spec_path": str(scene_path),
                "fivee_project_path": str(panel_path), "source_image_path": "",
                "rendered_image_path": str(image_path), "material": name,
            })
        if not assets:
            raise FigureProviderError("생성할 그림 패널이 없습니다.")
        primary = assets[0]
        if callable(progress):
            progress(92, "5E 미리보기와 프로젝트를 저장하는 중")
        result = self._result(
            Path(primary["fivee_project_path"]), "draft", primary["scene_spec_path"],
            primary["rendered_image_path"], ",".join(a["material"] for a in assets),
        )
        result["assets"] = assets
        result["instructions"] = "그림을 생성해 미리보기에 연결했습니다. 필요한 경우에만 5E에서 편집하세요."
        return result

    def edit(self, session_id: int, draft: dict, current: dict) -> dict:
        self._ensure_server()
        self._mcp()
        path = self._project_path(session_id, current)
        if not path.is_file():
            raise FigureProviderError("연결된 5E 프로젝트가 없습니다. 먼저 그림 생성을 누르세요.")
        return self._result(
            path, "editing", current.get("scene_spec_path") or "",
            current.get("rendered_image_path") or "",
        )

    def activate(self, session_id: int, draft: dict, current: dict) -> dict:
        client = self._mcp()
        try:
            client.wait_for_app(href_token=str(current.get("activation_token") or ""))
            project_path = self._project_path(session_id, current)
            if project_path.is_file():
                client.call("load_project", {"path": str(project_path)})
            client.call("set_page", {
                "page": self._figure_name(session_id, draft, current), "create": True,
            })
            # 새 5E 창의 문항 페이지가 비어 있으면 저장된 설계안을 즉시 복원한다.
            # 이미 객체가 있으면 사용자의 편집 내용을 보존한다.
            state = json.loads(str(client.call("read_app")))
            if not (state.get("objects") or []):
                scene_path = Path(current.get("scene_spec_path") or "")
                if scene_path.is_file():
                    plan = json.loads(scene_path.read_text(encoding="utf-8"))
                else:
                    plan = draft.get("figure_plan")
                if isinstance(plan, dict) and not plan.get("objects") and plan.get("panels"):
                    plan = plan["panels"][0]
                if not isinstance(plan, dict) or not plan.get("objects"):
                    raise FigureProviderError("5E에 불러올 그림 설계안을 찾을 수 없습니다.")
                objects = plan.get("objects") or []
                client.call("clear_app")
                client.call("set_artboard", plan.get("artboard") or {"w": 90, "h": 60})
                client.call("add_objects", {"objects": objects, "group": True})
                client.call("read_app")
        except FiveEMcpError as exc:
            raise FigureProviderError(str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise FigureProviderError(f"5E 그림 설계안을 읽을 수 없습니다: {exc}") from exc
        result = self._result(
            self._project_path(session_id, current), "editing",
            current.get("scene_spec_path") or "",
            current.get("rendered_image_path") or "",
        )
        result["instructions"] = "5E 문항 전용 페이지에 초안을 표시했습니다. 바로 위치·크기·속성을 수정하세요."
        return result

    def sync(self, session_id: int, draft: dict, current: dict) -> dict:
        """Pull the visible 5E page back into the editable project and PNG preview."""
        path = self._project_path(session_id, current)
        if not path.is_file():
            raise FigureProviderError("동기화할 5E 프로젝트가 없습니다.")
        name = self._figure_name(session_id, draft, current)
        short_code = str(current.get("short_code") or "").strip()
        output_dir = hwppalette_provider.photo_dir(short_code)
        image_path = output_dir / f"{name}.png"
        previous_project = path.with_name("figure.previous.5e.json")
        previous_image = path.with_name("figure.previous.png")
        try:
            if path.is_file():
                shutil.copy2(path, previous_project)
            if image_path.is_file():
                shutil.copy2(image_path, previous_image)
            hwppalette_provider.register_photo_dir(output_dir)
            client = self._mcp()
            client.wait_for_app()
            client.call("set_page", {"page": name, "create": False})
            state = json.loads(str(client.call("read_app")))
            if not (state.get("objects") or []):
                raise FigureProviderError("현재 5E 문항 페이지가 비어 있어 가져오지 않았습니다.")
            client.call("fit_artboard", {"margin": 4, "recenter": True})
            client.call("read_app")
            client.call("save_project", {"path": str(path)})
            client.call("export_image", {"widthPx": 1200})
            client.call("save_image", {"dir": str(output_dir), "name": name, "dpi": 300})
        except (FiveEMcpError, HwpPaletteError, OSError, ValueError) as exc:
            raise FigureProviderError(f"5E 편집 내용 가져오기 실패: {exc}") from exc
        result = self._result(
            path, "draft", current.get("scene_spec_path") or "",
            str(image_path), name,
        )
        result["previous_image_path"] = str(previous_image) if previous_image.is_file() else ""
        result["instructions"] = "5E 수정본과 PNG를 ExamPool에 다시 반영했습니다."
        return result

    def revert(self, session_id: int, draft: dict, current: dict) -> dict:
        path = self._project_path(session_id, current)
        previous_project = path.with_name("figure.previous.5e.json")
        previous_image = Path(current.get("previous_image_path") or path.with_name("figure.previous.png"))
        current_image = Path(current.get("rendered_image_path") or "")
        if not previous_project.is_file() and not previous_image.is_file():
            raise FigureProviderError("되돌릴 이전 5E 편집본이 없습니다.")
        if previous_project.is_file():
            shutil.copy2(previous_project, path)
        if previous_image.is_file() and current_image.name:
            current_image.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(previous_image, current_image)
        result = self._result(
            path, "draft", current.get("scene_spec_path") or "",
            str(current_image) if current_image.is_file() else "",
        )
        result["instructions"] = "직전 5E 편집 반영 전 상태로 되돌렸습니다."
        return result

    def confirm(self, session_id: int, draft: dict, current: dict) -> dict:
        path = self._project_path(session_id, current)
        if not path.is_file():
            raise FigureProviderError("확정할 5E 프로젝트 파일이 없습니다.")
        try:
            project = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise FigureProviderError(f"5E 프로젝트 파일을 읽을 수 없습니다: {exc}") from exc
        if project.get("version") != self.schema_version or not project.get("pages"):
            raise FigureProviderError("5E 프로젝트 형식이 올바르지 않습니다(pages[]/0.17 필요).")
        name = self._figure_name(session_id, draft, current)
        short_code = str(current.get("short_code") or "").strip()
        existing_image = Path(current.get("rendered_image_path") or "")
        if existing_image.is_file():
            asset_names = [Path(asset.get("rendered_image_path") or "").stem
                           for asset in current.get("assets") or []
                           if Path(asset.get("rendered_image_path") or "").is_file()]
            result = self._result(
                path, "confirmed", current.get("scene_spec_path") or "",
                str(existing_image), ",".join(asset_names) or existing_image.stem,
            )
            result["instructions"] = "현재 미리보기 그림을 문항 자료에 연결했습니다."
            return result
        try:
            output_dir = hwppalette_provider.photo_dir(short_code)
            hwppalette_provider.register_photo_dir(output_dir)
            client = self._mcp()
            client.wait_for_app()
            client.call("set_page", {"page": name, "create": False})
            client.call("export_image", {"widthPx": 1200})
            client.call("save_image", {"dir": str(output_dir), "name": name, "dpi": 300})
        except (FiveEMcpError, HwpPaletteError) as exc:
            raise FigureProviderError(str(exc)) from exc
        result = self._result(
            path, "confirmed", current.get("scene_spec_path") or "",
            str(output_dir / f"{name}.png"), name,
        )
        result["instructions"] = "5E 그림을 300dpi PNG로 저장하고 문항 자료에 연결했습니다."
        return result


class RasterImageProvider:
    """ChatGPT-managed image generation through the local Codex App Server."""

    name = "raster_image"

    @staticmethod
    def _folder(session_id: int) -> Path:
        folder = data_dir() / "authoring_figures" / f"session_{session_id}" / "raster"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    @staticmethod
    def _build_fivee_project(folder: Path, assets: list[dict]) -> Path:
        """Embed generated rasters as one auto-sized 5E page tab per image."""
        import fitz
        pages = []
        for index, asset in enumerate(assets):
            source = Path(asset["source_image_path"])
            pixmap = fitz.Pixmap(str(source))
            scale = min(90 / max(1, pixmap.width), 60 / max(1, pixmap.height))
            width = round(pixmap.width * scale, 2)
            height = round(pixmap.height * scale, 2)
            mime = "image/png" if source.suffix.lower() == ".png" else "image/jpeg"
            src = f"data:{mime};base64," + base64.b64encode(source.read_bytes()).decode("ascii")
            page_id = f"page_raster_{index + 1}_{uuid.uuid4().hex[:8]}"
            page_name = Path(asset["rendered_image_path"]).stem
            pages.append({
                "id": page_id, "name": page_name, "meta": {"number": "", "points": ""},
                "objects": [{
                    "id": f"image_{uuid.uuid4().hex[:12]}", "type": "image",
                    "x": -width / 2, "y": -height / 2, "w": width, "h": height,
                    "src": src, "rotation": 0, "mode": "edit", "opacity": 1,
                    "aspectLocked": True, "exportable": True, "cutouts": [],
                    "recognized": False, "layerId": 1, "order": 0,
                }],
                "guides": [],
                "layers": [
                    {"id": 1, "name": "레이어 1", "visible": True},
                    {"id": 2, "name": "레이어 2", "visible": True},
                    {"id": 3, "name": "레이어 3", "visible": True},
                ],
                "artboard": {"w": round(width + 8, 2), "h": round(height + 8, 2)},
            })
            asset["fivee_project_path"] = str(folder / "figure.5e.json")
            asset["page_name"] = page_name
            asset["artboard"] = pages[-1]["artboard"]
        project_path = folder / "figure.5e.json"
        project_path.write_text(json.dumps({
            "version": "0.17", "pages": pages,
            "activePageId": pages[0]["id"] if pages else None,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return project_path

    def create(self, session_id: int, draft: dict, current: dict) -> dict:
        progress = current.get("progress_callback")
        if callable(progress):
            progress(15, "그림 장면과 생성 옵션을 분석하는 중")
        plan = draft.get("figure_plan")
        if not isinstance(plan, dict):
            context_text = "\n".join(filter(None, [
                str(draft.get("passage") or ""), str(draft.get("material") or ""),
                str(draft.get("ask") or ""),
                " ".join(str(item.get("text") or item) for item in draft.get("bogi_items") or []),
            ]))
            plan = {"version": 2, "summary": "현재 문항에 필요한 과학 평가 도판", "panels": [{
                "id": "main", "summary": context_text, "image_prompt": context_text,
            }]}
        panels = plan.get("panels") or [{
            "id": "main", "summary": plan.get("summary", ""),
            "image_prompt": plan.get("summary", ""),
        }]
        options = current.get("options") or {}
        if options.get("composition") in {"auto", "separate"} and len(panels) <= 1:
            question_context = "\n".join(filter(None, [
                str(draft.get("passage") or ""), str(draft.get("material") or ""),
                str(draft.get("ask") or ""),
                "\n".join(str(item.get("text") or item) for item in draft.get("bogi_items") or []),
                str(plan.get("summary") or ""),
            ]))
            try:
                if callable(progress):
                    progress(25, "문항에 필요한 그림 장면 수를 판단하는 중")
                panels = codex_app_server.plan_image_panels(question_context)
            except CodexAppServerError as exc:
                raise FigureProviderError(f"그림 장면 자동 분리 실패: {exc}") from exc
        folder = self._folder(session_id)
        scene_path = folder / "image-prompts.json"
        scene_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        previous_image = folder / "figure.previous.png"
        current_image = Path(str(current.get("rendered_image_path") or ""))
        if current_image.is_file():
            shutil.copy2(current_image, previous_image)
        output_dir = hwppalette_provider.photo_dir(str(current.get("short_code") or ""))
        hwppalette_provider.register_photo_dir(output_dir)
        base_name = str(current.get("figure_name") or f"draft_{session_id}")
        include_text = bool(options.get("include_text", False))
        text_rule = (
            "Only include labels, letters, and numerical values that are explicitly necessary."
            if include_text else
            "Do not draw any letters, words, numbers, plus/minus signs, charge marks, arrows, "
            "motion marks, symbols, captions, panel labels, legends, or watermarks. Express the "
            "scientific comparison only through object positions, distances, shapes, and leaf spread."
        )
        assets = []
        references = [
            str(row.get("image_path") or "") for row in current.get("references") or []
            if row.get("usage", "both") in {"image", "both"}
        ]
        for index, panel in enumerate(panels):
            panel_prompt = str(
                panel.get("image_prompt") or panel.get("summary") or plan.get("summary") or ""
            ).strip()
            prompt = (
                "Create a newly interpreted Korean KICE-style middle-school science exam figure. "
                "Clean monochrome black line art on a pure white background, consistent thin strokes, "
                "simple flat geometry, high legibility when printed, no color, no gradients, no shadows, "
                "no photorealism, no decorative border. Do not reproduce a source page or screenshot. "
                f"{text_rule}\nScene description (semantic reference only): {panel_prompt}\n"
                "The style and no-annotation rules above override any conflicting wording in the scene description."
            )
            if references:
                prompt += (
                    "\nAttached images are visual references only. Preserve the scientifically important "
                    "objects and relationships, but reinterpret the composition as a new KICE-style diagram; "
                    "do not trace, copy text, or reproduce the source layout."
                )
            try:
                if callable(progress):
                    base = 35 + int((index / max(1, len(panels))) * 50)
                    progress(base, f"그림 {index + 1}/{len(panels)} 생성 요청 중")
                generated = codex_app_server.generate_image(
                    prompt, reference_paths=references, progress=progress,
                )
                source = Path(str(generated.get("savedPath") or ""))
                if not source.is_file():
                    raise FigureProviderError("Codex가 반환한 이미지 파일을 찾을 수 없습니다.")
                source_copy = folder / f"generated_{index + 1:02d}{source.suffix or '.png'}"
                shutil.copy2(source, source_copy)
                image_name = base_name if len(panels) == 1 else f"{base_name}_{index + 1:02d}"
                rendered = output_dir / f"{image_name}.png"
                if source.suffix.lower() == ".png":
                    shutil.copy2(source, rendered)
                else:
                    import fitz
                    fitz.Pixmap(str(source)).save(str(rendered))
            except (CodexAppServerError, HwpPaletteError, OSError, ValueError) as exc:
                raise FigureProviderError(f"이미지 자동 생성 실패: {exc}") from exc
            assets.append({
                "panel_id": str(panel.get("id") or f"panel-{index + 1}"), "ord": index + 1,
                "provider": self.name, "status": "draft",
                "scene_spec_path": str(scene_path), "fivee_project_path": "",
                "source_image_path": str(source_copy), "rendered_image_path": str(rendered),
                "image_prompt": prompt,
            })
            if callable(progress):
                progress(85 + int(((index + 1) / max(1, len(panels))) * 10),
                         f"그림 {index + 1}/{len(panels)} 저장 완료")
        project_path = self._build_fivee_project(folder, assets)
        if callable(progress):
            progress(98, "5E 편집 프로젝트와 문항 그림을 연결하는 중")
        return {
            "provider": self.name, "status": "draft",
            "scene_spec_path": str(scene_path), "fivee_project_path": str(project_path),
            "rendered_image_path": assets[0]["rendered_image_path"], "assets": assets,
            "previous_image_path": str(previous_image) if previous_image.is_file() else "",
            "instructions": "ChatGPT-managed 이미지 생성을 완료하고 문항 미리보기에 연결했습니다.",
        }

    def edit(self, session_id: int, draft: dict, current: dict) -> dict:
        result = FiveELocalProvider().edit(session_id, draft, current)
        result["provider"] = self.name
        return result

    def activate(self, session_id: int, draft: dict, current: dict) -> dict:
        result = FiveELocalProvider().activate(session_id, draft, current)
        result["provider"] = self.name
        return result

    def sync(self, session_id: int, draft: dict, current: dict) -> dict:
        result = FiveELocalProvider().sync(session_id, draft, current)
        result["provider"] = self.name
        return result

    def revert(self, session_id: int, draft: dict, current: dict) -> dict:
        previous = Path(current.get("previous_image_path") or "")
        rendered = Path(current.get("rendered_image_path") or "")
        if not previous.is_file() or not rendered.name:
            raise FigureProviderError("되돌릴 이전 이미지가 없습니다.")
        shutil.copy2(previous, rendered)
        return {
            "provider": self.name, "status": "draft", "scene_spec_path": current.get("scene_spec_path") or "",
            "fivee_project_path": "", "rendered_image_path": str(rendered),
        }

    def confirm(self, session_id: int, draft: dict, current: dict) -> dict:
        rendered = Path(current.get("rendered_image_path") or "")
        assets = current.get("assets") or []
        ready_assets = [
            asset for asset in assets
            if Path(asset.get("rendered_image_path") or "").is_file()
        ]
        if assets and len(ready_assets) != len(assets):
            raise FigureProviderError("분리된 모든 그림을 생성해 가져온 뒤 확정하세요.")
        if not rendered.is_file():
            raise FigureProviderError("먼저 생성된 이미지를 가져오세요.")
        material = ",".join(
            Path(asset["rendered_image_path"]).stem for asset in ready_assets
        ) or rendered.stem
        return {
            "provider": self.name, "status": "confirmed",
            "scene_spec_path": current.get("scene_spec_path") or "",
            "fivee_project_path": "", "rendered_image_path": str(rendered),
            "material": material, "instructions": "이미지 그림을 문항 자료에 연결했습니다.",
        }


_FIGURE_PROVIDERS: dict[str, FigureProvider] = {
    "stub": StubFigureProvider(),
    "fivee_local": FiveELocalProvider(),
    "raster_image": RasterImageProvider(),
}


def get_figure_provider(name: str = "stub") -> FigureProvider:
    return _FIGURE_PROVIDERS.get(name) or _FIGURE_PROVIDERS["stub"]


def close_figure_providers() -> None:
    client = FiveELocalProvider._mcp_client
    FiveELocalProvider._mcp_client = None
    if client:
        client.close()
