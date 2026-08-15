from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright
from pydantic import JsonValue


ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _wait_for_server(url: str) -> None:
    for _ in range(40):
        try:
            with urlopen(url, timeout=1):  # noqa: S310 - local test server only
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError("local UI server did not start")


def _failed_job() -> dict[str, JsonValue]:
    items = [
        {
            "id": number,
            "ord": number,
            "source_page": 1 if number <= 6 else 2,
            "source_number": number,
            "bbox": [1, 2, 3, 4],
            "status": "failed",
            "selected": False,
            "draft": {},
            "error": {
                "code": "manual_review_required",
                "message": "graphical answer choices require manual review",
            },
            "revision": 1,
        }
        for number in range(1, 21)
    ]
    return {
        "id": 20,
        "name": "p1_2027_06.pdf",
        "layout_style": "suneung",
        "status": "failed",
        "source_filename": "p1_2027_06.pdf",
        "source_path": "source.pdf",
        "source_sha256": "abc",
        "error": {
            "code": "crop_all_failed",
            "message": "변환 가능한 문항이 없습니다. 실패한 20개 문항의 상세 원인을 확인하세요.",
        },
        "revision": 2,
        "created_at": "2026-08-15 13:06:37",
        "updated_at": "2026-08-15 13:07:04",
        "capabilities": {
            "review_items": False,
            "typeset_selected": False,
            "retry_failed": True,
        },
        "items": items,
        "assets": [],
        "outputs": [],
    }


def _partial_job() -> dict[str, JsonValue]:
    job = _failed_job()
    ready = dict(job["items"][0])
    ready.update({
        "status": "ready",
        "selected": True,
        "draft": {"palette_markdown": "\\direct\\\n1\nready"},
        "error": None,
    })
    job.update({
        "id": 21,
        "name": "부분 변환.pdf",
        "status": "partial_failure",
        "error": {"code": "crop_partial_failure", "message": "1 item failed"},
        "capabilities": {
            "review_items": True,
            "typeset_selected": True,
            "retry_failed": True,
        },
        "items": [ready, job["items"][1]],
    })
    return job


def test_failed_job_exposes_item_errors_in_native_disclosures() -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        failed = _failed_job()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """failed => { window.fetch = () => Promise.resolve(new Response(
                  JSON.stringify({items:[failed]}),
                  {status:200, headers:{'Content-Type':'application/json'}}
                )); }""",
                failed,
            )
            page.locator('[data-tab="pdf-hwp"]').click()

            current = page.locator("#pdfHwpCurrent")
            assert current.get_by_text(
                "작업 20 · 성공 0개 · 실패 20개 · 결과 0개", exact=True,
            ).is_visible()
            current.get_by_text("실패 문항 20개 자세히 보기", exact=True).click()
            assert current.get_by_text("1번 · PDF 1쪽", exact=True).is_visible()
            assert current.get_by_text(
                "PDF 문자·그림 선지 인식에 실패했습니다.", exact=True,
            ).first.is_visible()

            history = page.locator("#pdfHwpJobs .ph-job-card").first
            assert history.get_by_text("성공 0개 · 실패 20개 · 결과 0개", exact=True).is_visible()
            history.get_by_text("실패 문항 20개 자세히 보기", exact=True).click()
            assert history.get_by_text("20번 · PDF 2쪽", exact=True).is_visible()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_partial_failure_typesets_ready_items_and_keeps_failure_details() -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        partial = _partial_job()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """partial => {
                  let state = partial;
                  window.typesetCalls = 0;
                  window.fetch = (url, options = {}) => {
                    const path = String(url);
                    if (path.endsWith('/typeset')) {
                      window.typesetCalls += 1;
                      state = {...state, revision: 3, outputs: [
                        {id:81, kind:'hwp', status:'ready', file_path:'partial.hwp', sha256:'h', size_bytes:12, error:null},
                        {id:82, kind:'pdf', status:'ready', file_path:'partial.pdf', sha256:'p', size_bytes:12, error:null},
                      ]};
                      return Promise.resolve(new Response(JSON.stringify(state), {status:200, headers:{'Content-Type':'application/json'}}));
                    }
                    return Promise.resolve(new Response(JSON.stringify({items:[state]}), {status:200, headers:{'Content-Type':'application/json'}}));
                  };
                }""",
                partial,
            )
            page.locator('[data-tab="pdf-hwp"]').click()

            assert page.get_by_role("textbox", name="1번 HWP 변환 내용").is_visible()
            assert page.get_by_text("실패 문항 1개 자세히 보기", exact=True).first.is_visible()
            button = page.get_by_role("button", name="성공한 1개 문항으로 HWP 만들기")
            assert button.is_enabled()
            button.click()
            page.get_by_role("link", name="편집용 HWP 받기").first.wait_for()

            assert page.evaluate("window.typesetCalls") == 1
            assert page.get_by_role("link", name="확인용 PDF 받기").first.is_visible()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)
