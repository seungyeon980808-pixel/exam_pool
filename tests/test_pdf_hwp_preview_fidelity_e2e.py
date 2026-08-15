from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import socket
import threading
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from PIL import Image
from playwright.sync_api import sync_playwright
import pytest
import uvicorn

from app import db, routes_pdf_hwp


ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextmanager
def _running_app(app: FastAPI) -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, name="pdf-hwp-preview-e2e", daemon=True)
    thread.start()
    started = threading.Event()
    for _ in range(100):
        if server.started:
            started.set()
            break
        thread.join(0.02)
    assert started.is_set()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _png(path: Path, color: str) -> str:
    Image.new("RGB", (96, 64), color=color).save(path, format="PNG")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_preview_fetches_the_same_captionless_png_hash_owned_by_hwp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a ready item whose source crop differs from its final captionless figure.
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "preview.db")
    monkeypatch.setattr(db, "_inited", False)
    db.init_db()
    source_crop = tmp_path / "source-crop.png"
    figure = tmp_path / "captionless-figure.png"
    source_hash = _png(source_crop, "white")
    figure_hash = _png(figure, "black")
    with db.transaction() as connection:
        job_id = connection.execute(
            "INSERT INTO conversion_job(name,layout_style,status) VALUES(?,?,'review')",
            ("preview fidelity", "suneung"),
        ).lastrowid
        item_id = connection.execute(
            "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json,status,"
            "selected,draft_json) VALUES(?,1,1,20,'[1,2,3,4]','ready',1,?)",
            (job_id, json.dumps({"palette_markdown": "\\template\\\n20\nquestion"})),
        ).lastrowid
        source_id = connection.execute(
            "INSERT INTO conversion_asset(job_id,item_id,role,file_path,sha256,media_type) "
            "VALUES(?,?,?,?,?,'image/png')",
            (job_id, item_id, "source_crop", str(source_crop), source_hash),
        ).lastrowid
        figure_id = connection.execute(
            "INSERT INTO conversion_asset(job_id,item_id,role,file_path,sha256,media_type,metadata_json) "
            "VALUES(?,?,?,?,?,'image/png',?)",
            (
                job_id,
                item_id,
                "figure",
                str(figure),
                figure_hash,
                json.dumps({
                    "asset_hash": figure_hash,
                    "asset_count": 1,
                    "panel_index": 1,
                    "caption_in_image": False,
                    "caption_text": "",
                    "manual_review_required": False,
                }),
            ),
        ).lastrowid

    app = FastAPI()
    app.include_router(routes_pdf_hwp.router)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    # When: the real browser renders the job and fetches its preview through the real API.
    with _running_app(app) as origin, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{origin}/static/index.html")
        page.locator('[data-tab="pdf-hwp"]').click()
        image = page.locator(".ph-source-preview img")
        image.wait_for()
        asset_url = image.get_attribute("src") or ""
        rendered_hash = page.evaluate(
            """async (url) => {
              const response = await fetch(url);
              const digest = await crypto.subtle.digest('SHA-256', await response.arrayBuffer());
              return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('');
            }""",
            asset_url,
        )
        browser.close()

    # Then: source_crop stays a fallback and the preview bytes match the HWP-owned asset hash.
    assert source_id != figure_id
    assert source_hash != figure_hash
    assert asset_url.endswith(f"/api/pdf-hwp/jobs/{job_id}/assets/{figure_id}")
    assert rendered_hash == figure_hash
