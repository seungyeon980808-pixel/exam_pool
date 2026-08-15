"""Loose-coupled hwpPalette paths, photo folders, and CLI launcher."""
from __future__ import annotations

import json
import hashlib
import os
import re
import signal
import subprocess
import sys
import threading
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from ..paths import BASE_DIR, data_dir
from . import palette_registry


class HwpPaletteError(RuntimeError):
    pass


@contextmanager
def _thread_preview_lock(lock: threading.Lock, timeout_seconds: int):
    acquired = lock.acquire(timeout=max(1, timeout_seconds))
    if not acquired:
        raise HwpPaletteError(
            "다른 문항을 정밀 조판하는 중입니다. 현재 탭은 최신 내용으로 자동 재시도합니다."
        )
    try:
        yield
    finally:
        lock.release()


@contextmanager
def _system_preview_lock(timeout_seconds: int):
    """Serialize HWP automation across duplicate/restarting ExamPool servers."""
    if os.name != "nt":
        yield
        return

    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.ReleaseMutex.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.CreateMutexW(None, False, "Local\\ExamPool_HwpPalette_Preview_v1")
    if not handle:
        raise HwpPaletteError("한글 조판 잠금을 만들지 못했습니다.")
    wait = kernel32.WaitForSingleObject(handle, max(1, timeout_seconds) * 1000)
    # WAIT_OBJECT_0 and WAIT_ABANDONED both grant ownership.
    if wait not in (0x00000000, 0x00000080):
        kernel32.CloseHandle(handle)
        raise HwpPaletteError(
            "이전 한글 조판을 정리하는 중입니다. 잠시 후 최신 내용으로 다시 시도합니다."
        )
    try:
        yield
    finally:
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


def _terminate_process_id(pid: int) -> None:
    """Terminate one previously recorded automation process without broad matching."""
    if pid <= 0:
        return
    try:
        os.kill(pid, signal.SIGTERM)
        return
    except (OSError, ProcessLookupError):
        pass
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"], capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


class _TimedOutProcess(Protocol):
    pid: int

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


def _cleanup_timed_out_process(
    process: _TimedOutProcess, hwp_pid_path: Path, *, is_windows: bool,
) -> None:
    """Clean up one timed-out runner and its recorded Windows HWP child."""
    if is_windows:
        try:
            hwp_pid = int(hwp_pid_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            hwp_pid = 0
        _terminate_process_id(hwp_pid)
        _terminate_process_id(process.pid)
    else:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


class HwpPaletteProvider:
    name = "hwppalette_local"

    def __init__(self, root: Path | None = None):
        configured = os.environ.get("EXAMPOOL_HWPPAL_ROOT")
        self._explicit_root = bool(root or configured)
        embedded = (BASE_DIR / "vendor" / "hwp_typesetter").resolve()
        sibling = (BASE_DIR.parent / "31_hwp_palette").resolve()
        if root or configured:
            selected = Path(root or configured)
        elif (BASE_DIR / ".git").exists() and (sibling / "hwp_palette" / "cli.py").is_file():
            # 소스 개발에서는 HwpPalette 최신 작업본을 즉시 통합 시험한다.
            selected = sibling
        else:
            # 배포본은 외부 설치를 요구하지 않고 검증된 내장 스냅샷을 쓴다.
            selected = embedded
        self.root = selected.resolve()
        self.embedded = self.root == embedded
        self._processes: dict[int, tuple[subprocess.Popen, int]] = {}
        self._preview_lock = threading.Lock()
        self._authoritative_photo_dirs: tuple[Path, ...] = ()

    def available(self) -> bool:
        return (self.root / "hwp_palette" / "cli.py").is_file()

    def _config(self) -> dict:
        path = self._data_root() / "config.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _data_root(self) -> Path:
        # 명시적으로 지정한 외부 HwpPalette는 그 설치의 데이터까지 쓰겠다는 뜻이다.
        # 테스트용/고급 사용자 경로의 기존 계약을 보존한다.
        if self._explicit_root and not self.embedded:
            return self.root / "data"
        # 실행 코드는 개발 중인 형제 HwpPalette를 쓸 수 있지만, 라이브러리는 언제나
        # ExamPool 전용 사본을 쓴다. 그래야 .hwpal 등록이 사용자의 HwpPalette 창고를
        # 덮어쓰지 않고 개발/배포 환경에서 똑같이 동작한다.
        target = data_dir() / "hwp_typesetter"
        marker = target / "UPSTREAM.json"
        embedded = (BASE_DIR / "vendor" / "hwp_typesetter").resolve()
        upstream_path = embedded / "UPSTREAM.json"
        seed = embedded / "seed_data"
        try:
            upstream = json.loads(upstream_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            upstream = {}
        current = {}
        try:
            current = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        identity_keys = ("commit", "packs", "schema_version", "synced_at")
        needs_seed = (
            not (target / "library.json").is_file()
            or not (target / "fragments").is_dir()
            or any(current.get(key) != upstream.get(key) for key in identity_keys)
        )
        if needs_seed:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(seed / "library.json", target / "library.json")
            fragments = target / "fragments"
            if fragments.exists():
                shutil.rmtree(fragments)
            shutil.copytree(seed / "fragments", fragments)
            if not (target / "config.json").exists():
                shutil.copy2(seed / "config.json", target / "config.json")
            marker.write_text(json.dumps(upstream, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
        palette_registry.materialize_active(target, seed, force=needs_seed)
        return target

    def _child_env(self) -> dict:
        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"
        child_env["HWPPAL_DATA_DIR"] = str(self._data_root())
        old_pythonpath = child_env.get("PYTHONPATH", "")
        child_env["PYTHONPATH"] = str(self.root) + (os.pathsep + old_pythonpath if old_pythonpath else "")
        return child_env

    def photo_root(self) -> Path:
        configured = os.environ.get("EXAMPOOL_PHOTO_ROOT")
        if configured:
            return Path(configured).resolve()
        config = self._config()
        raw = config.get("photo_dir") or next(iter(config.get("photo_dirs") or []), "")
        if raw:
            return Path(raw).resolve()
        return (data_dir() / "figures").resolve()

    def photo_dir(self, short_code: str = "") -> Path:
        root = self.photo_root()
        folder = root / short_code if short_code else root
        folder.mkdir(parents=True, exist_ok=True)
        return folder.resolve()

    def photo_dirs(self) -> list[Path]:
        """Return all configured photo folders in hwpPalette lookup order."""
        config = self._config()
        raw_dirs = [config.get("photo_dir"), *(config.get("photo_dirs") or [])]
        raw_dirs.append(data_dir() / "figures")
        result: list[Path] = []
        seen: set[str] = set()
        for raw in raw_dirs:
            if not raw:
                continue
            path = Path(raw).resolve()
            key = str(path).lower()
            if key not in seen:
                seen.add(key)
                result.append(path)
        return result

    def resolve_photo(self, material: str) -> Path | None:
        """Resolve one unambiguous image, preferring the current conversion dirs."""
        name = str(material or "").split(",", 1)[0].strip().strip("\\/")
        if not name or Path(name).name != name:
            return None
        candidate = Path(name)
        names = [name] if candidate.suffix else [
            f"{name}.png", f"{name}.jpg", f"{name}.jpeg", f"{name}.webp",
        ]
        authoritative_matches: list[Path] = []
        for folder in self._authoritative_photo_dirs:
            for filename in names:
                path = folder / filename
                if path.is_file():
                    authoritative_matches.append(path.resolve())
        if len(authoritative_matches) == 1:
            return authoritative_matches[0]
        if len(authoritative_matches) > 1:
            return None
        authoritative_keys = {
            str(path).casefold() for path in self._authoritative_photo_dirs
        }
        fallback_matches: list[Path] = []
        for folder in self.photo_dirs():
            if str(folder).casefold() in authoritative_keys:
                continue
            for filename in names:
                path = folder / filename
                if path.is_file():
                    fallback_matches.append(path.resolve())
        return fallback_matches[0] if len(fallback_matches) == 1 else None

    def validate_slot_contract(self, expected: dict[str, int]) -> dict:
        """Compare ExamPool's active template contract with hwpPalette metadata."""
        library_path = self._data_root() / "library.json"
        try:
            payload = json.loads(library_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return {"ok": False, "path": str(library_path), "templates": [],
                    "error": f"템플릿 라이브러리를 읽지 못했습니다: {exc}"}

        registered = {}
        for value in payload.values():
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict):
                    continue
                for key in (item.get("label"), item.get("name")):
                    if key:
                        registered[key] = item

        rows = []
        for label, expected_count in sorted(expected.items()):
            item = registered.get(label)
            actual = item.get("slot_count") if item else None
            rows.append({
                "label": label,
                "expected": expected_count,
                "actual": actual,
                "registered": item is not None,
                "ok": actual == expected_count,
            })
        return {
            "ok": all(row["ok"] for row in rows),
            "path": str(library_path),
            "templates": rows,
        }

    def register_photo_dir(self, folder: Path) -> None:
        self.register_photo_dirs((folder,))

    def register_photo_dirs(self, folders: tuple[Path, ...]) -> None:
        """Make current conversion folders authoritative for the next render."""
        if not self.available():
            raise HwpPaletteError("hwpPalette 설치 위치를 찾지 못했습니다.")
        current: list[Path] = []
        seen: set[str] = set()
        for folder in folders:
            path = folder.resolve()
            key = str(path).casefold()
            if key not in seen:
                seen.add(key)
                current.append(path)
        fallback = [
            path for path in self.photo_dirs()
            if str(path).casefold() not in seen
        ]
        config = self._config()
        ordered = [str(path) for path in (*current, *fallback)]
        config["photo_dirs"] = ordered
        config["photo_dir"] = ordered[0] if ordered else ""
        config_path = self._data_root() / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = config_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        temporary.replace(config_path)
        self._authoritative_photo_dirs = tuple(current)

    def open_template_editor(self, path: Path) -> None:
        """Open an isolated HWP revision in the user's associated Hancom editor."""
        target = path.resolve()
        if not target.is_file() or target.suffix.lower() != ".hwp":
            raise HwpPaletteError("수정할 HWP 템플릿 파일을 찾을 수 없습니다.")
        if os.name != "nt" or not hasattr(os, "startfile"):
            raise HwpPaletteError("템플릿 직접 편집은 Windows 한글 환경에서 사용할 수 있습니다.")
        try:
            os.startfile(str(target))
        except OSError as exc:
            raise HwpPaletteError(f"한글 편집기를 열지 못했습니다: {exc}") from exc

    def launch(self, markdown_path: Path, exam_page: bool = True,
               layout_style: str = "school") -> subprocess.Popen:
        if not self.available():
            raise HwpPaletteError("hwpPalette CLI를 찾지 못했습니다.")
        layout_style = "suneung" if layout_style == "suneung" else "school"
        if layout_style == "suneung":
            runner = Path(__file__).with_name("hwppalette_runner.py")
            args = [sys.executable, str(runner), "--markdown-file", str(markdown_path),
                    "--layout-style", layout_style]
        else:
            args = [sys.executable, "-m", "hwp_palette.cli", "--markdown-file", str(markdown_path)]
            if exam_page:
                args.append("--exam-page")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        log_dir = data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "hwppalette-cli.log"
        log_start = log_path.stat().st_size if log_path.exists() else 0
        log = None
        try:
            log = open(log_path, "a", encoding="utf-8")
            child_env = self._child_env()
            process = subprocess.Popen(
                args, cwd=str(self.root), stdout=log, stderr=subprocess.STDOUT,
                creationflags=flags, env=child_env,
            )
            self._processes[process.pid] = (process, log_start)
            return process
        finally:
            if log is not None:
                log.close()

    def _preview_token(self, markdown: str, scope: str, exam_page: bool,
                       layout_style: str) -> str:
        """Key previews by input and the hwpPalette files that affect layout."""
        dependencies = []
        paths = [
            self._data_root() / "library.json",
            self._data_root() / "config.json",
            self.root / "hwp_palette" / "cli.py",
            self.root / "hwp_palette" / "hwp" / "engine_library.py",
            self.root / "UPSTREAM.json",
            Path(__file__).with_name("hwppalette_runner.py"),
        ]
        for path in paths:
            relative = str(path)
            try:
                stat = path.stat()
                dependencies.append((relative, stat.st_size, stat.st_mtime_ns))
            except OSError:
                dependencies.append((relative, None, None))
        # A stable photo call name is deliberately overwritten when a figure is
        # regenerated. Include the current image file in the cache fingerprint;
        # otherwise precise preview keeps serving the old drawing indefinitely.
        photo_paths: set[Path] = set()
        for label in re.findall(r"\\([^\\\r\n]+)\\", markdown):
            resolved = self.resolve_photo(label)
            if resolved:
                photo_paths.add(resolved)
        for path in sorted(photo_paths, key=lambda item: str(item).casefold()):
            try:
                stat = path.stat()
                dependencies.append((str(path), stat.st_size, stat.st_mtime_ns))
            except OSError:
                dependencies.append((str(path), None, None))
        payload = json.dumps({
            "markdown": markdown,
            "scope": scope,
            "exam_page": exam_page,
            "layout_style": layout_style,
            "dependencies": dependencies,
            # v5: palette edit revisions now stay in their declared layout
            # family, so stale previews made from the pre-edit package must not
            # be reused.
            "preview_version": 7,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _preview_dir(self, token: str) -> Path:
        return data_dir() / "previews" / token

    @staticmethod
    def _content_clip(page):
        """Return only the question bounds, excluding the exam-page furniture."""
        import fitz

        text_boxes = []
        question_tops = []
        for block in page.get_text("blocks"):
            text = str(block[4] or "").strip() if len(block) >= 5 else ""
            if not text:
                continue
            box = fitz.Rect(block[:4])
            text_boxes.append(box)
            # The single-question preview always starts with an Arabic question
            # number.  The CSAT page header also contains a page number, but no
            # trailing dot, so it must not expand the crop to the whole sheet.
            if re.match(r"^\s*\d{1,3}\s*\.\s*\S", text):
                question_tops.append(box.y0)

        question_top = min(question_tops) if question_tops else None
        body_floor = page.rect.height * 0.20
        boxes = [
            box for box in text_boxes
            if (question_top is not None and box.y1 >= question_top - 1)
            or (question_top is None and box.y0 >= body_floor)
        ]
        for image in page.get_image_info():
            bbox = image.get("bbox")
            if bbox:
                box = fitz.Rect(bbox)
                if (question_top is not None and box.y1 >= question_top - 1) or (
                    question_top is None and box.y0 >= body_floor
                ):
                    boxes.append(box)
        if not boxes:
            return None
        content = boxes[0]
        for box in boxes[1:]:
            content |= box
        side_padding = 14
        top_padding = 6
        return fitz.Rect(
            max(page.rect.x0, content.x0 - side_padding),
            max(page.rect.y0, content.y0 - top_padding),
            min(page.rect.x1, content.x1 + side_padding),
            min(page.rect.y1, content.y1 + side_padding),
        )

    @staticmethod
    def _render_pdf(pdf_path: Path, target_dir: Path, *, crop_content: bool = False) -> list[dict]:
        try:
            import fitz
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise HwpPaletteError("PDF 미리보기 렌더러(PyMuPDF)가 설치되어 있지 않습니다.") from exc

        pages = []
        document = None
        try:
            document = fitz.open(pdf_path)
        except Exception as exc:
            raise HwpPaletteError(f"PDF를 열 수 없습니다: {pdf_path} — {exc}")
        try:
            if document.page_count < 1:
                raise HwpPaletteError("hwpPalette가 빈 PDF를 만들었습니다.")
            matrix = fitz.Matrix(1.65, 1.65)
            for index, page in enumerate(document):
                clip = HwpPaletteProvider._content_clip(page) if crop_content else None
                if crop_content and clip is None:
                    continue
                pixmap = page.get_pixmap(matrix=matrix, alpha=False, clip=clip)
                filename = f"page-{index + 1}.png"
                pixmap.save(target_dir / filename)
                pages.append({
                    "page_no": index + 1,
                    "filename": filename,
                    "width": pixmap.width,
                    "height": pixmap.height,
                })
        finally:
            document.close()
        return pages

    def _cached_preview(self, token: str) -> dict | None:
        folder = self._preview_dir(token)
        metadata_path = folder / "preview.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        required = [folder / "preview.hwp", folder / "preview.pdf"]
        required.extend(folder / page["filename"] for page in metadata.get("pages", []))
        if not metadata.get("pages") or not all(path.is_file() for path in required):
            return None
        metadata["cached"] = True
        return self._public_preview(metadata)

    @staticmethod
    def _public_preview(metadata: dict) -> dict:
        token = metadata["token"]
        result = dict(metadata)
        result["pages"] = [
            {**page, "image_url": f"/api/previews/{token}/pages/{page['page_no']}"}
            for page in metadata["pages"]
        ]
        result["hwp_url"] = f"/api/previews/{token}/hwp"
        result["pdf_url"] = f"/api/previews/{token}/pdf"
        return result

    def render_preview(
        self, markdown: str, *, scope: str = "question", exam_page: bool = True,
        layout_style: str = "school",
        timeout: int = 30,
    ) -> dict:
        """Typeset markdown in an isolated hidden HWP process and cache its pages."""
        if not self.available():
            raise HwpPaletteError("hwpPalette CLI를 찾을 수 없습니다.")
        if not markdown.strip():
            raise HwpPaletteError("미리 볼 문항 내용이 없습니다.")
        layout_style = "suneung" if layout_style == "suneung" else "school"
        token = self._preview_token(markdown, scope, exam_page, layout_style)

        # Do not queue stale editor snapshots behind a slow HWP job.  The browser
        # already retains an instant preview and can retry the newest fingerprint.
        with _thread_preview_lock(self._preview_lock, min(2, timeout)), \
             _system_preview_lock(min(2, timeout)):
            cached = self._cached_preview(token)
            if cached:
                return cached

            folder = self._preview_dir(token)
            folder.mkdir(parents=True, exist_ok=True)
            markdown_path = folder / "source.md"
            hwp_path = folder / "preview.hwp"
            pdf_path = folder / "preview.pdf"
            markdown_path.write_text(markdown, encoding="utf-8")

            runner = Path(__file__).with_name("hwppalette_runner.py")
            hwp_pid_path = folder / "hwp.pid"
            hwp_pid_path.unlink(missing_ok=True)
            args = [
                sys.executable, str(runner), "--markdown-file", str(markdown_path),
                "--layout-style", layout_style, "--output-hwp", str(hwp_path),
                "--output-pdf", str(pdf_path), "--hwp-pid-file", str(hwp_pid_path),
                "--hidden",
            ]
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            child_env = self._child_env()
            process = subprocess.Popen(
                args, cwd=str(self.root), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", creationflags=flags, env=child_env,
            )
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                _cleanup_timed_out_process(
                    process, hwp_pid_path, is_windows=os.name == "nt",
                )
                raise HwpPaletteError(
                    f"한글 정밀 조판이 {timeout}초 안에 끝나지 않아 해당 작업을 정리했습니다. "
                    "빠른 미리보기는 계속 사용할 수 있습니다."
                ) from exc
            result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
            if result.returncode:
                detail = (result.stderr or result.stdout or "알 수 없는 오류").strip()[-1800:]
                raise HwpPaletteError(f"HWP 미리보기 조판에 실패했습니다.\n{detail}")
            if not hwp_path.is_file() or not pdf_path.is_file():
                raise HwpPaletteError("hwpPalette가 미리보기 파일을 만들지 못했습니다.")

            pages = self._render_pdf(pdf_path, folder, crop_content=scope == "question")
            metadata = {
                "ok": True,
                "token": token,
                "scope": scope,
                "layout_style": layout_style,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "cached": False,
                "pages": pages,
            }
            temporary = folder / "preview.json.tmp"
            temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(folder / "preview.json")
            return self._public_preview(metadata)

    def preview_asset(self, token: str, kind: str, page_no: int | None = None) -> Path | None:
        if not re.fullmatch(r"[0-9a-f]{64}", token):
            return None
        folder = self._preview_dir(token)
        if kind == "page" and page_no and page_no > 0:
            path = folder / f"page-{page_no}.png"
        elif kind in {"hwp", "pdf"}:
            path = folder / f"preview.{kind}"
        else:
            return None
        return path.resolve() if path.is_file() else None

    def process_status(self, pid: int) -> dict:
        tracked = self._processes.get(pid)
        if not tracked:
            raise HwpPaletteError("추적 중인 hwpPalette 조판 작업이 아닙니다.")
        process, log_start = tracked
        returncode = process.poll()
        output = ""
        if returncode is not None:
            path = data_dir() / "logs" / "hwppalette-cli.log"
            try:
                with path.open("rb") as stream:
                    stream.seek(log_start)
                    output = stream.read().decode("utf-8", "replace").strip()
            except OSError:
                output = ""
        return {
            "pid": pid,
            "running": returncode is None,
            "returncode": returncode,
            "ok": returncode == 0 if returncode is not None else None,
            "output": output,
        }


hwppalette_provider = HwpPaletteProvider()
