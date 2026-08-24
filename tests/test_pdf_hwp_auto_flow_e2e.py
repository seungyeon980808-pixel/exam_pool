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


def test_pdf_drop_waits_for_start_button_and_exposes_hwp_directly() -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    operation_reads = {"detect": 0, "typeset": 0}
    calls: list[tuple[str, str]] = []
    try:
        _wait_for(f"http://127.0.0.1:{port}/static/index.html")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})

            draft = {
                "id": "job1", "name": "dragged.pdf", "status": "draft", "revision": 1,
                "capabilities": {"review_items": True, "typeset_selected": True},
                "async_detection": True, "async_typeset": True, "items": [], "outputs": [],
            }
            uploaded = {**draft, "status": "uploaded", "revision": 2}
            review = {
                **uploaded, "status": "review", "revision": 4, "item_count": 1,
                "items": [{"id": "item1", "ord": 1, "source_page": 1, "selected": True,
                           "status": "ready", "confirmed": True,
                           "draft": {"palette_markdown": "\\수능AI실제직접형\\\n1\n발문"}}],
            }
            completed = {
                **review, "status": "completed", "revision": 7,
                "outputs": [{"id": "out1", "kind": "hwp", "status": "ready"}],
            }

            def api(route: Route) -> None:
                request = route.request
                url = request.url.split("/api/pdf-hwp", 1)[-1]
                method = request.method
                calls.append((method, url))
                if url == "/jobs" and method == "GET":
                    route.fulfill(json={"items": []})
                elif url == "/jobs" and method == "POST":
                    route.fulfill(json={"job": draft})
                elif url == "/jobs/job1/upload" and method == "POST":
                    route.fulfill(json={"job": uploaded})
                elif url == "/jobs/job1/detect/start" and method == "POST":
                    route.fulfill(json={"operation_id": "op-detect"})
                elif url == "/jobs/job1/typeset/start" and method == "POST":
                    route.fulfill(json={"operation_id": "op-typeset"})
                elif url == "/operations/op-detect":
                    operation_reads["detect"] += 1
                    route.fulfill(json={"id": "op-detect", "status": "completed" if operation_reads["detect"] > 1 else "running", "progress": 100 if operation_reads["detect"] > 1 else 42})
                elif url == "/operations/op-typeset":
                    operation_reads["typeset"] += 1
                    progress_values = (20, 60, 60, 60)
                    read_index = operation_reads["typeset"] - 1
                    route.fulfill(json={
                        "id": "op-typeset",
                        "status": "running" if read_index < len(progress_values) else "completed",
                        "progress": progress_values[read_index] if read_index < len(progress_values) else 100,
                    })
                elif url == "/jobs/job1" and method == "GET":
                    route.fulfill(json={"job": completed if operation_reads["typeset"] > 1 else review})
                elif url == "/catalog":
                    route.fulfill(json={"domains": []})
                else:
                    route.fulfill(json={})

            page.route("**/api/**", api)
            page.goto(f"http://127.0.0.1:{port}/static/index.html")
            page.get_by_role("button", name="PDF→HWP").click()
            page.evaluate(
                """() => {
                  window.progressTargets = [];
                  const bar = document.querySelector('#pdfHwpProgressBar');
                  new MutationObserver(() => {
                    const value = Number.parseFloat(bar.style.inlineSize);
                    if (Number.isFinite(value)) window.progressTargets.push(value);
                  }).observe(bar, {attributes: true, attributeFilter: ['style']});
                }""",
            )
            page.locator("#pdfHwpDropzone").evaluate(
                """(dropzone) => {
                  const transfer = new DataTransfer();
                  transfer.items.add(new File(['%PDF-1.7'], 'dragged.pdf', {type: 'application/pdf'}));
                  dropzone.dispatchEvent(new DragEvent('drop', {bubbles: true, dataTransfer: transfer}));
                }""",
            )
            assert ("POST", "/jobs") not in calls
            page.get_by_role("button", name="변환 시작").click()
            page.wait_for_selector("#pdfHwpOutput:not(.hidden)", timeout=10_000)
            assert page.locator("#pdfHwpOutput").get_by_text("dragged_converted.hwp", exact=True).is_visible()
            assert page.locator("#pdfHwpOutput").get_by_role("link", name="dragged_converted.hwp 받기").is_visible()
            assert page.locator("#pdfHwpTotalElapsed").inner_text().startswith("총 진행")
            assert "해당 파일" in page.locator("#pdfHwpElapsed").inner_text()
            assert "총 진행" in page.locator("#pdfHwpElapsed").inner_text()
            assert ("POST", "/jobs/job1/typeset/start") in calls
            progress_targets = page.evaluate("window.progressTargets")
            assert progress_targets == sorted(progress_targets), progress_targets
            assert progress_targets[-1] == 100
            assert page.locator("#pdfHwpAdvancedReview").get_attribute("open") is None
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_completed_output_card_lists_each_ready_hwp_for_download() -> None:
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
            first = {
                "id": 11, "name": "first.pdf", "status": "completed", "revision": 3,
                "source_filename": "first.pdf",
                "capabilities": {"review_items": True, "typeset_selected": True},
                "items": [], "outputs": [{"id": 101, "kind": "hwp", "status": "ready"}],
            }
            second = {
                "id": 12, "name": "second.pdf", "status": "completed", "revision": 3,
                "source_filename": "second.pdf",
                "capabilities": {"review_items": True, "typeset_selected": True},
                "items": [], "outputs": [{"id": 102, "kind": "hwp", "status": "ready"}],
            }

            def api(route: Route) -> None:
                url = route.request.url.split("/api/pdf-hwp", 1)[-1]
                if url == "/jobs" and route.request.method == "GET":
                    route.fulfill(json={"items": [second, first]})
                else:
                    route.fulfill(json={})

            page.route("**/api/**", api)
            page.goto(f"http://127.0.0.1:{port}/static/index.html")
            page.get_by_role("button", name="PDF→HWP").click()
            card = page.locator("#pdfHwpOutput")
            card.wait_for()
            assert card.get_by_text("변환 완료 파일 2개", exact=True).is_visible()
            assert card.get_by_text("first_converted.hwp", exact=True).is_visible()
            assert card.get_by_text("second_converted.hwp", exact=True).is_visible()
            first_link = card.get_by_role("link", name="first_converted.hwp 받기")
            second_link = card.get_by_role("link", name="second_converted.hwp 받기")
            assert first_link.get_attribute("href").endswith("/jobs/11/outputs/101")
            assert first_link.get_attribute("download") == "first_converted.hwp"
            assert second_link.get_attribute("href").endswith("/jobs/12/outputs/102")
            assert second_link.get_attribute("download") == "second_converted.hwp"
            assert card.get_by_role("checkbox", name="first_converted.hwp 선택").is_checked()
            card.get_by_role("checkbox", name="second_converted.hwp 선택").uncheck()
            assert card.get_by_role("button", name="선택한 파일 받기").is_enabled()
            card.get_by_role("button", name="선택 해제").click()
            assert card.get_by_role("button", name="선택한 파일 받기").is_disabled()
            card.get_by_role("button", name="전체 선택").click()
            assert card.get_by_role("button", name="선택한 2개 받기").is_enabled()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)
