import io
import json
import os
import sys
import zipfile
from pathlib import Path
from urllib.request import urlopen
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.pdf_hwp_webapp import app
from app.desktop_shell import LoopbackServer
from app.integrations import hwppalette, palette_registry
import run_pdf_hwp_webapp


ROOT = Path(__file__).resolve().parents[1]


def _hwpal_bytes(name: str = "내 수능 양식") -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("chip.json", json.dumps({"chip_version": 1, "name": name}, ensure_ascii=False))
        archive.writestr("exam.json", json.dumps({
            "schema_version": 1, "kind": "exam_palette", "layout_style": "suneung",
        }))
        archive.writestr("library.json", json.dumps({"version": 1, "items": [{
            "category": "템플릿", "name": "직접형", "label": "수능AI실제직접형",
            "file": "direct.hwp", "slot_count": 2, "slot_names": ["문항번호", "발문"],
        }]}, ensure_ascii=False))
        archive.writestr("fragments/direct.hwp", b"hwp-template")
    return stream.getvalue()


def test_standalone_root_serves_only_the_pdf_hwp_product_shell() -> None:
    response = TestClient(app).get("/")

    assert response.status_code == 200
    html = response.text
    assert 'id="pdfHwpFile"' in html
    assert 'id="pdfHwpStatus"' in html
    assert 'id="pdfHwpCurrent"' in html
    assert '/static/js/pdf-hwp-shell.js' in html
    assert 'id="pdfHwpPalette"' in html
    assert 'id="pdfHwpPaletteFile"' in html
    assert '/static/js/pdf-hwp-palette.js' in html
    assert '/static/js/pdf-hwp.js' in html
    assert 'data-tab="bank"' not in html
    assert 'id="tab-config"' not in html
    assert 'id="tab-authoring"' not in html


def test_standalone_app_exposes_pdf_hwp_api_and_not_the_exam_pool_banks() -> None:
    client = TestClient(app)

    assert client.get("/api/pdf-hwp/jobs").status_code == 200
    assert client.get("/api/questions").status_code == 404
    assert client.get("/api/props").status_code == 404


def test_standalone_has_a_double_click_windows_launcher() -> None:
    launcher = ROOT / "PDF-HWP 웹앱 실행.bat"
    entrypoint = (ROOT / "run_pdf_hwp_webapp.py").read_text(encoding="utf-8")
    desktop_shell = (ROOT / "app/desktop_shell.py").read_text(encoding="utf-8")

    assert launcher.is_file()
    assert "from app.pdf_hwp_webapp import app" in entrypoint
    assert "run_desktop_app(app)" in entrypoint
    assert 'HOST = "127.0.0.1"' in desktop_shell


def test_windows_bundle_includes_editable_hwp_templates() -> None:
    # Given: editable CSAT typesetting depends on the repository's HWP template set.
    spec = (ROOT / "packaging/windows/ExamPoolHwpConverter.spec").read_text(encoding="utf-8")

    # When: the PyInstaller data manifest is inspected.
    packaged_template_directory = (
        'str(ROOT / "assets" / "hwp_templates"), "assets/hwp_templates"'
    )

    # Then: the complete template directory is copied into the frozen application.
    assert packaged_template_directory in spec


def test_windows_bundle_includes_hwp_path_checker() -> None:
    # Given: a standalone install cannot depend on the development virtual environment.
    spec = (ROOT / "packaging/windows/ExamPoolHwpConverter.spec").read_text(encoding="utf-8")

    # When: the PyInstaller binary manifest is inspected.
    packaged_checker = 'collect_dynamic_libs("pyhwpx", destdir="pyhwpx")'

    # Then: Hancom's automation path checker is shipped beside the frozen pyhwpx package.
    assert packaged_checker in spec


def test_frozen_standalone_reuses_the_executable_for_hwp_work() -> None:
    with (
        patch.object(sys, "frozen", True, create=True),
        patch.object(sys, "executable", r"C:\Program Files\ExamPool\ExamPool HWP Converter.exe"),
    ):
        command = hwppalette._hwp_runner_command()

    assert command == [
        r"C:\Program Files\ExamPool\ExamPool HWP Converter.exe",
        "--hwp-worker",
    ]


def test_source_standalone_uses_the_python_hwp_worker() -> None:
    with patch.object(sys, "frozen", False, create=True):
        command = hwppalette._hwp_runner_command()

    assert command[0] == sys.executable
    assert Path(command[1]).name == "hwppalette_runner.py"


def test_standalone_dispatches_hwp_worker_arguments() -> None:
    with patch("app.integrations.hwppalette_runner.main", return_value=7) as worker:
        exit_code = run_pdf_hwp_webapp.main([
            "--hwp-worker", "--markdown-file", "question.md", "--hidden",
        ])

    assert exit_code == 7
    worker.assert_called_once_with(["--markdown-file", "question.md", "--hidden"])


def test_standalone_opens_the_native_desktop_shell() -> None:
    # Given: the desktop shell boundary is isolated from its Windows renderer.
    with patch.object(run_pdf_hwp_webapp, "run_desktop_app", return_value=0) as desktop:
        # When: a user launches the normal application entry point.
        exit_code = run_pdf_hwp_webapp.main([])

    # Then: only the native shell owns the visible application lifecycle.
    assert exit_code == 0
    desktop.assert_called_once()


def test_desktop_shell_owns_the_loopback_server_lifecycle() -> None:
    # Given: the real standalone FastAPI service is assigned an ephemeral port.
    server = LoopbackServer(app)

    # When: the desktop window lifecycle owns the service context.
    with server:
        response = urlopen(f"{server.url}/health", timeout=2)

        # Then: the service is reachable only while the desktop context is active.
        assert response.status == 200
        assert server.is_running

    assert not server.is_running


def test_frozen_standalone_loads_the_bundled_ocr_runtime(tmp_path) -> None:
    executable = tmp_path / "ExamPoolHwpConverter.exe"
    runtime = tmp_path / "ocr_runtime"
    runtime.mkdir()
    paddle_libraries = runtime / "paddle" / "libs"
    paddle_libraries.mkdir(parents=True)
    with (
        patch.object(sys, "frozen", True, create=True),
        patch.object(sys, "executable", str(executable)),
        patch.object(sys, "path", ["bundled-python"]),
        patch.dict(os.environ, {"PATH": "windows-path"}),
    ):
        run_pdf_hwp_webapp._configure_ocr_runtime()

        assert sys.path[0] == str(runtime)
        assert os.environ["PATH"].startswith(f"{paddle_libraries}{os.pathsep}")


def test_standalone_can_download_and_replace_the_active_full_palette(tmp_path, monkeypatch) -> None:
    # Given: one active CSAT palette in an isolated registry.
    monkeypatch.setattr(palette_registry, "data_dir", lambda: tmp_path / "data")
    original = _hwpal_bytes("첫 양식")
    palette_registry.install_hwpal(original, "첫양식.hwpal", "suneung")
    client = TestClient(app)

    # When: the user inspects and downloads the palette from the standalone app.
    current = client.get("/api/pdf-hwp/palette")
    download = client.get("/api/pdf-hwp/palette/download")

    # Then: the complete original package is returned, not an individual fragment.
    assert current.status_code == 200
    assert current.json()["name"] == "첫 양식"
    assert download.status_code == 200
    assert download.content == original
    assert download.headers["content-disposition"].endswith('filename*=utf-8\'\'%EC%B2%AB%EC%96%91%EC%8B%9D.hwpal')

    # When: an edited full package is registered.
    edited = _hwpal_bytes("수정 양식")
    uploaded = client.post(
        "/api/pdf-hwp/palette",
        files={"file": ("수정양식.hwpal", edited, "application/octet-stream")},
    )

    # Then: it becomes the active palette immediately.
    assert uploaded.status_code == 201
    assert uploaded.json()["name"] == "수정 양식"
    assert "suneung" in uploaded.json()["active_for"]
    assert client.get("/api/pdf-hwp/palette/download").content == edited
