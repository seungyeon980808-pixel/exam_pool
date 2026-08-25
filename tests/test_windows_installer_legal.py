import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_project_declares_agpl_when_distributed_with_pymupdf() -> None:
    with (ROOT / "pyproject.toml").open("rb") as project_file:
        project = tomllib.load(project_file)

    assert project["project"]["license"] == "AGPL-3.0-only"
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in (ROOT / "LICENSE").read_text(encoding="utf-8")


def test_public_distribution_contains_required_legal_notices() -> None:
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    assert "PyMuPDF" in notices
    assert "PaddleOCR" in notices
    assert "PaddlePaddle" in notices
    assert "PyInstaller" in notices
    assert "Hancom" in notices


def test_installer_bundles_legal_files_and_shows_the_project_license() -> None:
    spec = (ROOT / "packaging/windows/ExamPoolHwpConverter.spec").read_text(encoding="utf-8")
    installer = (ROOT / "packaging/windows/ExamPoolHwpConverter.iss").read_text(encoding="utf-8")
    builder = (ROOT / "packaging/windows/build_installer.ps1").read_text(encoding="utf-8")

    assert 'ROOT / "LICENSE"' in spec
    assert 'ROOT / "THIRD_PARTY_NOTICES.md"' in spec
    assert "include_py_files=False" in spec
    assert '"**/tests/**"' in spec
    assert '"**/include/**"' in spec
    assert "LicenseFile=" in installer
    assert "collect_licenses.ps1" in builder
    assert "LOCALAPPDATA" in builder


def test_standalone_ui_links_to_the_versioned_public_source() -> None:
    page = (ROOT / "static/pdf-hwp.html").read_text(encoding="utf-8")
    script = (ROOT / "static/js/pdf-hwp.js").read_text(encoding="utf-8")

    assert "github.com/seungyeon980808-pixel/exam_pool/tree/hwp-converter-v0.1.0" in page
    assert 'rel="noopener noreferrer"' in page
    assert 'typesetPhrase.className = "ph-keep"' in script
    assert 'statusDescription.replaceChildren(' in script


def test_vendored_typesetter_metadata_does_not_publish_local_machine_state() -> None:
    upstream = json.loads((ROOT / "vendor/hwp_typesetter/UPSTREAM.json").read_text(encoding="utf-8"))

    assert "source" not in upstream
    assert "dirty" not in upstream


def test_installer_workflow_syncs_dependencies_without_packaging_the_flat_repository() -> None:
    workflow = (ROOT / ".github/workflows/windows-installer.yml").read_text(encoding="utf-8")

    assert "uv sync --extra installer --no-install-project" in workflow


def test_installer_builds_a_windowed_webview_application() -> None:
    with (ROOT / "pyproject.toml").open("rb") as project_file:
        project = tomllib.load(project_file)
    spec = (ROOT / "packaging/windows/ExamPoolHwpConverter.spec").read_text(encoding="utf-8")

    assert "pywebview==6.2.1" in project["project"]["optional-dependencies"]["installer"]
    assert 'WINDOWED = os.environ.get("EXAMPOOL_WINDOWED", "1") != "0"' in spec
    assert "console=not WINDOWED" in spec


def test_installer_freezes_ocr_modules_instead_of_copying_the_target_folder() -> None:
    spec = (ROOT / "packaging/windows/ExamPoolHwpConverter.spec").read_text(encoding="utf-8")

    assert "collect_runtime_datas" not in spec
    assert "collect_all" in spec
    assert 'OCR_PACKAGES = ("paddleocr", "paddlex")' in spec
    assert '"opencv-contrib-python"' in spec
    assert '"pypdfium2"' in spec
    assert '"paddle-libs.marker"' in spec
    assert '"mklml.dll"' in spec
    assert '"tkinter"' in spec
    assert '"tzdata"' in spec
