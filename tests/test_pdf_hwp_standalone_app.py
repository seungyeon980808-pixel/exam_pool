import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.pdf_hwp_webapp import app
from app.integrations import palette_registry


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

    assert launcher.is_file()
    assert "app.pdf_hwp_webapp:app" in entrypoint
    assert "127.0.0.1" in entrypoint


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
