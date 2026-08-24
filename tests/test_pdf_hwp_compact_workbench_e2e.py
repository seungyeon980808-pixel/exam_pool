from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import Route, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for(url: str) -> None:
    for _ in range(40):
        try:
            with urlopen(url, timeout=1):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError("local UI server did not start")


def test_pdf_hwp_dropzone_and_collapsed_source_panel() -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for(f"http://127.0.0.1:{port}/static/index.html")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})

            def api(route: Route) -> None:
                url = route.request.url
                if "/api/pdf-hwp/jobs" in url and route.request.method == "GET":
                    route.fulfill(json={"items": []})
                elif url.endswith("/api/standards") or url.endswith("/api/subjects") or "/api/propositions" in url:
                    route.fulfill(json=[])
                elif url.endswith("/api/subject"):
                    route.fulfill(json={"standard_count": 0})
                else:
                    route.fulfill(json={})

            page.route("**/api/**", api)
            page.goto(f"http://127.0.0.1:{port}/static/index.html")
            page.get_by_role("button", name="PDF→HWP").click()

            advanced = page.locator("#pdfHwpAdvancedReview")
            assert not advanced.get_attribute("open")
            page.get_by_text("변환 상세 보기", exact=True).click()
            source = page.locator("#pdfHwpSourceDetails")
            assert not source.get_attribute("open")
            assert page.get_by_text("PDF 원문 보기", exact=True).is_visible()

            result = page.locator("#pdfHwpDropzone").evaluate(
                """(dropzone) => {
                  const transfer = new DataTransfer();
                  transfer.items.add(new File(['%PDF-1.7'], 'dragged.pdf', {type: 'application/pdf'}));
                  transfer.items.add(new File(['%PDF-1.7'], 'second.pdf', {type: 'application/pdf'}));
                  dropzone.dispatchEvent(new DragEvent('drop', {bubbles: true, dataTransfer: transfer}));
                  const input = document.querySelector('#pdfHwpFile');
                  return {count: input.files.length, names: [...input.files].map((file) => file.name), label: document.querySelector('#pdfHwpFileName').textContent};
                }""",
            )
            assert result == {
                "count": 2,
                "names": ["dragged.pdf", "second.pdf"],
                "label": "2개 파일 선택됨",
            }

            page.get_by_text("PDF 원문 보기", exact=True).click()
            assert source.get_attribute("open") == ""
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)
