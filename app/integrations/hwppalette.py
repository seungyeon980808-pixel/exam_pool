"""Loose-coupled hwpPalette paths, photo folders, and CLI launcher."""
from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..paths import BASE_DIR, data_dir


class HwpPaletteError(RuntimeError):
    pass


class HwpPaletteProvider:
    name = "hwppalette_local"

    def __init__(self, root: Path | None = None):
        configured = os.environ.get("EXAMPOOL_HWPPAL_ROOT")
        self.root = Path(root or configured or (BASE_DIR.parent / "31_hwp_palette")).resolve()
        self._processes: dict[int, tuple[subprocess.Popen, int]] = {}
        self._preview_lock = threading.Lock()

    def available(self) -> bool:
        return (self.root / "hwp_palette" / "cli.py").is_file()

    def _config(self) -> dict:
        path = self.root / "data" / "config.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

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
        """Resolve the first legacy question material to an existing image."""
        name = str(material or "").split(",", 1)[0].strip().strip("\\/")
        if not name or Path(name).name != name:
            return None
        candidate = Path(name)
        names = [name] if candidate.suffix else [
            f"{name}.png", f"{name}.jpg", f"{name}.jpeg", f"{name}.webp",
        ]
        for folder in self.photo_dirs():
            for filename in names:
                path = folder / filename
                if path.is_file():
                    return path.resolve()
        return None

    def validate_slot_contract(self, expected: dict[str, int]) -> dict:
        """Compare ExamPool's active template contract with hwpPalette metadata."""
        library_path = self.root / "data" / "library.json"
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
        if not self.available():
            raise HwpPaletteError("hwpPalette 설치 위치를 찾지 못했습니다.")
        code = (
            "from hwp_palette.core import settings; settings.add_photo_dir("
            + json.dumps(str(folder), ensure_ascii=False) + ")"
        )
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=str(self.root), capture_output=True,
            text=True, encoding="utf-8", timeout=15, creationflags=flags,
        )
        if result.returncode:
            raise HwpPaletteError(result.stderr.strip() or "사진 폴더 등록에 실패했습니다.")

    def launch(self, markdown_path: Path, exam_page: bool = True) -> subprocess.Popen:
        if not self.available():
            raise HwpPaletteError("hwpPalette CLI를 찾지 못했습니다.")
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
            child_env = os.environ.copy()
            child_env["PYTHONIOENCODING"] = "utf-8"
            child_env["PYTHONUTF8"] = "1"
            process = subprocess.Popen(
                args, cwd=str(self.root), stdout=log, stderr=subprocess.STDOUT,
                creationflags=flags, env=child_env,
            )
            self._processes[process.pid] = (process, log_start)
            return process
        finally:
            if log is not None:
                log.close()

    def _preview_token(self, markdown: str, scope: str, exam_page: bool) -> str:
        """Key previews by input and the hwpPalette files that affect layout."""
        dependencies = []
        for relative in ("data/library.json", "data/config.json", "hwp_palette/cli.py"):
            path = self.root / relative
            try:
                stat = path.stat()
                dependencies.append((relative, stat.st_size, stat.st_mtime_ns))
            except OSError:
                dependencies.append((relative, None, None))
        payload = json.dumps({
            "markdown": markdown,
            "scope": scope,
            "exam_page": exam_page,
            "dependencies": dependencies,
            "preview_version": 1,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _preview_dir(self, token: str) -> Path:
        return data_dir() / "previews" / token

    @staticmethod
    def _render_pdf(pdf_path: Path, target_dir: Path) -> list[dict]:
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
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
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
        timeout: int = 150,
    ) -> dict:
        """Typeset markdown in an isolated hidden HWP process and cache its pages."""
        if not self.available():
            raise HwpPaletteError("hwpPalette CLI를 찾을 수 없습니다.")
        if not markdown.strip():
            raise HwpPaletteError("미리 볼 문항 내용이 없습니다.")
        token = self._preview_token(markdown, scope, exam_page)

        with self._preview_lock:
            cached = self._cached_preview(token)
            if cached:
                return cached

            folder = self._preview_dir(token)
            folder.mkdir(parents=True, exist_ok=True)
            markdown_path = folder / "source.md"
            hwp_path = folder / "preview.hwp"
            pdf_path = folder / "preview.pdf"
            markdown_path.write_text(markdown, encoding="utf-8")

            args = [
                sys.executable, "-m", "hwp_palette.cli",
                "--markdown-file", str(markdown_path),
                "--output-hwp", str(hwp_path),
                "--output-pdf", str(pdf_path),
                "--hidden",
            ]
            if exam_page:
                args.append("--exam-page")
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            child_env = os.environ.copy()
            child_env["PYTHONIOENCODING"] = "utf-8"
            child_env["PYTHONUTF8"] = "1"
            try:
                result = subprocess.run(
                    args, cwd=str(self.root), capture_output=True, text=True,
                    encoding="utf-8", timeout=timeout, creationflags=flags, env=child_env,
                )
            except subprocess.TimeoutExpired as exc:
                raise HwpPaletteError("HWP 미리보기 조판 시간이 초과되었습니다.") from exc
            if result.returncode:
                detail = (result.stderr or result.stdout or "알 수 없는 오류").strip()[-1800:]
                raise HwpPaletteError(f"HWP 미리보기 조판에 실패했습니다.\n{detail}")
            if not hwp_path.is_file() or not pdf_path.is_file():
                raise HwpPaletteError("hwpPalette가 미리보기 파일을 만들지 못했습니다.")

            pages = self._render_pdf(pdf_path, folder)
            metadata = {
                "ok": True,
                "token": token,
                "scope": scope,
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
