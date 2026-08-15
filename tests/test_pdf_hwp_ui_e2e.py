import json
import base64
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.request import urlopen

import pytest
from playwright.sync_api import Route, sync_playwright


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


def _job(status: str, *, palette_markdown: str = "", outputs: bool = False):
    return {
        "id": 17, "name": "11월 과학 20번", "layout_style": "school", "status": status,
        "source_filename": "exam.pdf", "source_path": "source.pdf", "source_sha256": "abc",
        "error": None, "revision": 2, "created_at": "2026-08-13T10:00:00",
        "updated_at": "2026-08-13T10:00:01",
        "capabilities": {
            "review_items": status in {"review", "partial_failure"},
            "typeset_selected": status in {"review", "partial_failure"} and bool(palette_markdown.strip()),
            "retry_failed": status in {"failed", "partial_failure"},
        },
        "items": [] if status in {"draft", "uploaded"} else [{
            "id": 31, "ord": 1, "source_page": 4, "source_number": 20,
            "bbox": [1, 2, 3, 4], "status": "ready", "selected": True,
            "draft": {"source_text": "20. 빛의 굴절", "palette_markdown": palette_markdown},
            "error": None, "revision": 1,
        }],
        "assets": [],
        "outputs": ([
            {"id": 81, "kind": "hwp", "status": "ready", "file_path": "converted.hwp", "sha256": "h", "size_bytes": 12, "error": None},
            {"id": 82, "kind": "pdf", "status": "ready", "file_path": "converted.pdf", "sha256": "p", "size_bytes": 12, "error": None},
        ] if outputs else []),
    }


def test_pdf_hwp_full_review_and_typeset_flow(tmp_path: Path) -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_server(f"http://127.0.0.1:{port}/static/index.html")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            listed_jobs = []
            scenario = {"upload_fails": False, "create_fails": False, "upload_calls": 0}

            def api(route: Route) -> None:
                request = route.request
                path = request.url.split("/api/pdf-hwp", 1)[1]
                if request.method == "GET" and path == "/jobs":
                    route.fulfill(json={"items": listed_jobs})
                elif request.method == "GET" and path == "/jobs/17":
                    route.fulfill(json=_job("detecting", palette_markdown="ready"))
                elif path == "/jobs" and request.method == "POST":
                    if scenario["create_fails"]:
                        route.fulfill(status=503, json={"detail": "create unavailable"})
                    else:
                        route.fulfill(status=201, json=_job("draft"))
                elif path.endswith("/upload"):
                    scenario["upload_calls"] += 1
                    if scenario["upload_fails"]:
                        route.fulfill(status=503, json={"detail": "upload unavailable"})
                    else:
                        route.fulfill(json=_job("uploaded"))
                elif path.endswith("/detect"):
                    route.fulfill(json=_job("review"))
                elif "/items/" in path:
                    route.fulfill(json=_job("review", palette_markdown="\\direct\\\n20\n빛의 굴절"))
                elif path.endswith("/typeset"):
                    route.fulfill(json=_job("completed", palette_markdown="ready", outputs=True))
                elif path.endswith("/retry"):
                    route.fulfill(json=_job("review", palette_markdown="ready"))
                else:
                    route.fulfill(status=404, json={"detail": "unexpected test route"})

            page.route("**/api/pdf-hwp/**", api)
            page.goto(f"http://127.0.0.1:{port}/static/index.html")
            page.emulate_media(reduced_motion="reduce")
            tab = page.get_by_role("button", name="PDF→HWP")
            tab.focus()
            tab.press("Enter")
            assert tab.get_attribute("aria-current") == "page"
            page.locator("#pdfHwpStatus").evaluate("el => el.dataset.tone = 'working'")
            assert page.locator(".ph-status-mark").evaluate("el => getComputedStyle(el).animationName") == "none"
            pdf = tmp_path / "exam.pdf"
            pdf.write_bytes(b"%PDF-1.7 test")
            page.locator("#pdfHwpFile").set_input_files(pdf)
            page.get_by_role("button", name="변환 시작").click()
            page.locator("#pdfHwpStatus").get_by_text("문항 검출 완료", exact=True).wait_for()
            assert page.get_by_role("button", name="검토한 문항으로 HWP 만들기").is_disabled()

            editor = page.get_by_label("20번 HWP 변환 내용")
            editor.fill("\\direct\\\n20\n빛의 굴절")
            page.get_by_role("button", name="문항 내용 저장").click()
            page.get_by_role("button", name="검토한 문항으로 HWP 만들기").click()

            page.locator("#pdfHwpStatus").get_by_text("변환 완료", exact=True).wait_for()
            assert page.get_by_role("link", name="편집용 HWP 받기").get_attribute("href").endswith("/outputs/81")
            assert page.get_by_role("link", name="확인용 PDF 받기").get_attribute("href").endswith("/outputs/82")

            listed_jobs.append(_job("partial_failure", palette_markdown="ready"))
            page.reload()
            page.get_by_role("button", name="PDF→HWP").press("Enter")
            page.locator("#pdfHwpStatus").get_by_text("일부 문항 변환 실패", exact=True).wait_for()
            assert page.locator("#pdfHwpStatus").get_attribute("role") == "alert"
            page.get_by_role("button", name="실패 단계 다시 시도").click()
            page.locator("#pdfHwpStatus").get_by_text("문항 검출 완료", exact=True).wait_for()
            assert page.locator(".ph-job-state").get_by_text("문항 검출 완료", exact=True).is_visible()

            successful_uploads = scenario["upload_calls"]
            scenario["upload_fails"] = True
            listed_jobs.clear()
            page.reload()
            page.get_by_role("button", name="PDF→HWP").press("Enter")
            page.locator("#pdfHwpFile").set_input_files(pdf)
            page.get_by_role("button", name="변환 시작").click()
            page.locator("#pdfHwpStatus").get_by_text("요청을 완료하지 못했습니다", exact=True).wait_for()
            assert page.get_by_role("button", name="실패 단계 다시 시도").is_visible()
            page.get_by_role("button", name="실패 단계 다시 시도").click()
            page.locator("#pdfHwpStatus").get_by_text("요청을 완료하지 못했습니다", exact=True).wait_for()
            assert page.get_by_role("button", name="실패 단계 다시 시도").is_visible()
            scenario["upload_fails"] = False
            page.get_by_role("button", name="실패 단계 다시 시도").click()
            page.locator("#pdfHwpStatus").get_by_text("문항 검출 완료", exact=True).wait_for()
            assert scenario["upload_calls"] == successful_uploads + 3

            listed_jobs.append(_job("partial_failure", palette_markdown="ready"))
            scenario["create_fails"] = True
            page.reload()
            page.get_by_role("button", name="PDF→HWP").press("Enter")
            page.locator("#pdfHwpFile").set_input_files(pdf)
            page.get_by_role("button", name="변환 시작").click()
            page.locator("#pdfHwpStatus").get_by_text("요청을 완료하지 못했습니다", exact=True).wait_for()
            assert page.get_by_role("button", name="실패 단계 다시 시도").is_hidden()
            assert scenario["upload_calls"] == successful_uploads + 3

            detail_calls = 0
            page.unroute("**/api/pdf-hwp/**", api)

            def unavailable(route: Route) -> None:
                nonlocal detail_calls
                path = route.request.url.split("/api/pdf-hwp", 1)[1]
                if path == "/jobs":
                    route.fulfill(json={"items": [_job("detecting", palette_markdown="ready")]})
                else:
                    detail_calls += 1
                    route.fulfill(status=503, json={"detail": "temporarily unavailable"})

            page.route("**/api/pdf-hwp/**", unavailable)
            page.reload()
            page.get_by_role("button", name="PDF→HWP").press("Enter")
            page.wait_for_timeout(1900)
            assert detail_calls == 1
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_stale_poll_error_does_not_overwrite_selected_job() -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        job_a = _job("detecting") | {"id": 1, "name": "작업 A"}
        job_b = _job("completed", palette_markdown="ready", outputs=True) | {"id": 2, "name": "작업 B"}
        jobs_json = json.dumps({"items": [job_a, job_b]}, ensure_ascii=False)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.add_init_script(f"""
                const nativeFetch = window.fetch.bind(window);
                window.fetch = (url, options) => {{
                  const path = String(url);
                  if (path.endsWith('/api/pdf-hwp/jobs')) {{
                    return Promise.resolve(new Response({json.dumps(jobs_json)}, {{status: 200, headers: {{'Content-Type': 'application/json'}}}}));
                  }}
                  if (path.endsWith('/api/pdf-hwp/jobs/1')) {{
                    return new Promise((resolve, reject) => setTimeout(() => reject(new TypeError('job A unavailable')), 500));
                  }}
                  return nativeFetch(url, options);
                }};
            """)
            page.goto(url)
            page.locator('[data-tab="pdf-hwp"]').click()
            page.wait_for_timeout(1600)
            page.locator(".ph-job-card", has_text="작업 B").get_by_role("button", name="상태 보기").click()
            page.wait_for_timeout(600)
            assert page.locator("#pdfHwpStatus").get_by_text("변환 완료", exact=True).is_visible()
            assert page.locator("#pdfHwpStatus").get_by_text("요청을 완료하지 못했습니다", exact=True).count() == 0
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_delayed_upload_recovery_stays_with_its_job(tmp_path: Path) -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        created = _job("draft") | {"id": 41, "name": "새 업로드"}
        older = _job("failed", palette_markdown="ready") | {
            "id": 22, "name": "기존 작업", "error": {"code": "FAILED", "message": "retry me"},
        }
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """([created, older]) => {
                  window.retryCalls = 0;
                  window.fetch = (url, options = {}) => {
                    const path = String(url);
                    if (path.endsWith('/api/pdf-hwp/jobs') && (!options.method || options.method === 'GET')) {
                      return Promise.resolve(new Response(JSON.stringify({items: [older]}), {status: 200, headers: {'Content-Type': 'application/json'}}));
                    }
                    if (path.endsWith('/api/pdf-hwp/jobs') && options.method === 'POST') {
                      return Promise.resolve(new Response(JSON.stringify(created), {status: 201, headers: {'Content-Type': 'application/json'}}));
                    }
                    if (path.endsWith('/api/pdf-hwp/jobs/41/upload')) {
                      return new Promise((resolve) => setTimeout(() => resolve(new Response(JSON.stringify({...created, status: 'uploaded'}), {status: 200, headers: {'Content-Type': 'application/json'}})), 600));
                    }
                    if (path.endsWith('/api/pdf-hwp/jobs/41/detect')) {
                      return Promise.resolve(new Response(JSON.stringify({...created, status: 'review'}), {status: 200, headers: {'Content-Type': 'application/json'}}));
                    }
                        if (path.endsWith('/api/pdf-hwp/jobs/22/retry')) {
                          window.retryCalls += 1;
                          return Promise.resolve(new Response(JSON.stringify({...older, status: 'completed', error: null, capabilities:{review_items:false, typeset_selected:false, retry_failed:false}}), {status: 200, headers: {'Content-Type': 'application/json'}}));
                    }
                    return Promise.resolve(new Response(JSON.stringify({detail: 'unexpected'}), {status: 404, headers: {'Content-Type': 'application/json'}}));
                  };
                }""",
                [created, older],
            )
            page.locator('[data-tab="pdf-hwp"]').click()
            pdf = tmp_path / "delayed.pdf"
            pdf.write_bytes(b"%PDF-1.7 delayed")
            page.locator("#pdfHwpFile").set_input_files(pdf)
            page.get_by_role("button", name="변환 시작").click()
            page.locator(".ph-job-card", has_text="기존 작업").get_by_role("button", name="상태 보기").click()
            page.locator("#pdfHwpRetry").click()
            page.wait_for_timeout(100)
            assert page.evaluate("window.retryCalls") == 1
            page.wait_for_timeout(800)
            assert page.locator("#pdfHwpStatus").get_by_text("변환 완료", exact=True).is_visible()
            assert page.get_by_role("button", name="실패 단계 다시 시도").is_hidden()
            page.locator(".ph-job-card", has_text="새 업로드").get_by_role("button", name="상태 보기").click()
            assert page.locator("#pdfHwpStatus").get_by_text("문항 검출 완료", exact=True).is_visible()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


@pytest.mark.parametrize("operation", ["item-save", "retry", "typeset"])
def test_delayed_operation_failure_does_not_overwrite_new_selection(operation: str) -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        status = "partial_failure" if operation == "retry" else "review"
        palette = "ready" if operation == "typeset" else ""
        job_a = _job(status, palette_markdown=palette) | {"id": 101, "name": "Job A"}
        job_a["items"] = [dict(item, id=131) for item in job_a["items"]]
        job_b = _job("completed", palette_markdown="ready", outputs=True) | {"id": 202, "name": "Job B"}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """([jobA, jobB]) => {
                  window.fetch = (url, options = {}) => {
                    const path = String(url);
                    if (path.endsWith('/api/pdf-hwp/jobs') && (!options.method || options.method === 'GET')) {
                      return Promise.resolve(new Response(JSON.stringify({items: [jobA, jobB]}), {status: 200, headers: {'Content-Type': 'application/json'}}));
                    }
                    if (path.includes('/api/pdf-hwp/jobs/101/')) {
                      return new Promise((resolve) => setTimeout(() => resolve(new Response(JSON.stringify({detail: 'Job A unavailable'}), {status: 503, headers: {'Content-Type': 'application/json'}})), 500));
                    }
                    return Promise.resolve(new Response(JSON.stringify({detail: 'unexpected'}), {status: 404, headers: {'Content-Type': 'application/json'}}));
                  };
                }""",
                [job_a, job_b],
            )
            page.locator('[data-tab="pdf-hwp"]').click()
            if operation == "item-save":
                page.locator(".ph-review-item textarea").fill("edited")
                page.locator(".ph-review-item button").click()
            elif operation == "retry":
                page.locator("#pdfHwpRetry").click()
            else:
                page.locator("#pdfHwpTypeset").click()
            page.locator(".ph-job-card", has_text="Job B").get_by_role("button").click()
            page.wait_for_timeout(700)
            assert page.locator("#pdfHwpStatus").get_attribute("data-tone") == "success"
            assert page.locator("#pdfHwpCurrent").get_by_text("202", exact=False).is_visible()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_retrying_selected_job_does_not_clear_another_jobs_recovery(tmp_path: Path) -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        created_a = _job("draft") | {"id": 41, "name": "Job A"}
        created_c = _job("draft") | {"id": 43, "name": "Job C"}
        failed_b = _job("failed", palette_markdown="ready") | {
            "id": 22, "name": "Job B", "error": {"code": "FAILED", "message": "retry B"},
        }
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """([createdA, createdC, failedB]) => {
                  window.createCalls = 0;
                  window.uploadACalls = 0;
                  window.fetch = (url, options = {}) => {
                    const path = String(url);
                    if (path.endsWith('/api/pdf-hwp/jobs') && (!options.method || options.method === 'GET')) return Promise.resolve(new Response(JSON.stringify({items: [failedB]}), {status: 200, headers: {'Content-Type': 'application/json'}}));
                    if (path.endsWith('/api/pdf-hwp/jobs') && options.method === 'POST') {
                      window.createCalls += 1;
                      return Promise.resolve(new Response(JSON.stringify(window.createCalls === 1 ? createdA : createdC), {status: 201, headers: {'Content-Type': 'application/json'}}));
                    }
                    if (path.endsWith('/api/pdf-hwp/jobs/41/upload')) {
                      window.uploadACalls += 1;
                      if (window.uploadACalls === 1) return Promise.resolve(new Response(JSON.stringify({detail: 'upload A failed'}), {status: 503, headers: {'Content-Type': 'application/json'}}));
                      return Promise.resolve(new Response(JSON.stringify({...createdA, status: 'uploaded'}), {status: 200, headers: {'Content-Type': 'application/json'}}));
                    }
                    if (path.endsWith('/api/pdf-hwp/jobs/41/detect')) return Promise.resolve(new Response(JSON.stringify({...createdA, status: 'review'}), {status: 200, headers: {'Content-Type': 'application/json'}}));
                    if (path.endsWith('/api/pdf-hwp/jobs/43/upload')) return Promise.resolve(new Response(JSON.stringify({detail: 'upload C failed'}), {status: 503, headers: {'Content-Type': 'application/json'}}));
                    if (path.endsWith('/api/pdf-hwp/jobs/22/retry')) return Promise.resolve(new Response(JSON.stringify({...failedB, status: 'completed', error: null}), {status: 200, headers: {'Content-Type': 'application/json'}}));
                    return Promise.resolve(new Response(JSON.stringify({detail: 'unexpected'}), {status: 404, headers: {'Content-Type': 'application/json'}}));
                  };
                }""",
                [created_a, created_c, failed_b],
            )
            page.locator('[data-tab="pdf-hwp"]').click()
            pdf = tmp_path / "recovery.pdf"
            pdf.write_bytes(b"%PDF-1.7 recovery")
            page.locator("#pdfHwpFile").set_input_files(pdf)
            page.get_by_role("button", name="변환 시작").click()
            page.locator("#pdfHwpRetry").wait_for(state="visible")
            page.get_by_role("button", name="변환 시작").click()
            page.locator("#pdfHwpStatus").get_by_text("요청을 완료하지 못했습니다", exact=True).wait_for()
            page.locator(".ph-job-card", has_text="Job B").get_by_role("button").click()
            page.locator("#pdfHwpRetry").click()
            page.locator("#pdfHwpStatus").get_by_text("변환 완료", exact=True).wait_for()
            page.locator(".ph-job-card", has_text="Job A").get_by_role("button").click()
            assert page.locator("#pdfHwpRetry").is_visible()
            page.locator("#pdfHwpRetry").click()
            page.locator("#pdfHwpStatus").get_by_text("문항 검출 완료", exact=True).wait_for()
            assert page.locator("#pdfHwpRetry").is_hidden()
            page.locator(".ph-job-card", has_text="Job C").get_by_role("button").click()
            assert page.locator("#pdfHwpRetry").is_visible()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


@pytest.mark.parametrize("action", ["retry", "typeset"])
def test_job_action_rejects_double_activation(action: str) -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        status = "failed" if action == "retry" else "review"
        current = _job(status, palette_markdown="ready") | {"id": 77, "name": "One job"}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """([current, action]) => {
                  window.actionCalls = 0;
                  window.fetch = (url, options = {}) => {
                    const path = String(url);
                    if (path.endsWith('/api/pdf-hwp/jobs') && (!options.method || options.method === 'GET')) return Promise.resolve(new Response(JSON.stringify({items: [current]}), {status: 200, headers: {'Content-Type': 'application/json'}}));
                    if (path.endsWith('/api/pdf-hwp/jobs/77/' + action)) {
                      window.actionCalls += 1;
                      return new Promise((resolve) => setTimeout(() => resolve(new Response(JSON.stringify({...current, status: action === 'retry' ? 'review' : 'completed', error: null}), {status: 200, headers: {'Content-Type': 'application/json'}})), 500));
                    }
                    return Promise.resolve(new Response(JSON.stringify({detail: 'unexpected'}), {status: 404, headers: {'Content-Type': 'application/json'}}));
                  };
                }""",
                [current, action],
            )
            page.locator('[data-tab="pdf-hwp"]').click()
            selector = "#pdfHwpRetry" if action == "retry" else "#pdfHwpTypeset"
            page.locator(selector).evaluate("button => { button.click(); button.click(); }")
            page.wait_for_timeout(100)
            assert page.evaluate("window.actionCalls") == 1
            assert page.locator(selector).is_disabled()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_failed_retry_and_refresh_keeps_retry_available() -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        failed = _job("failed", palette_markdown="ready") | {"id": 88, "name": "Retry job"}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """failed => {
                  window.fetch = (url, options = {}) => {
                    const path = String(url);
                    if (path.endsWith('/api/pdf-hwp/jobs') && (!options.method || options.method === 'GET')) return Promise.resolve(new Response(JSON.stringify({items: [failed]}), {status: 200, headers: {'Content-Type': 'application/json'}}));
                    if (path.endsWith('/api/pdf-hwp/jobs/88/retry')) return Promise.resolve(new Response(JSON.stringify({detail: 'retry failed'}), {status: 503, headers: {'Content-Type': 'application/json'}}));
                    if (path.endsWith('/api/pdf-hwp/jobs/88')) return Promise.resolve(new Response(JSON.stringify({detail: 'refresh failed'}), {status: 503, headers: {'Content-Type': 'application/json'}}));
                    return Promise.resolve(new Response(JSON.stringify({detail: 'unexpected'}), {status: 404, headers: {'Content-Type': 'application/json'}}));
                  };
                }""",
                failed,
            )
            page.locator('[data-tab="pdf-hwp"]').click()
            page.locator("#pdfHwpRetry").click()
            page.locator("#pdfHwpStatus").get_by_text("refresh failed", exact=True).wait_for()
            assert page.locator("#pdfHwpRetry").is_visible()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_real_server_upload_detect_select_and_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    import uvicorn

    from app import db, routes_pdf_hwp
    from app.pdf_hwp_pipeline import CropArtifact, DetectedItem, DetectionResult, DraftArtifact

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ui-real.db")
    monkeypatch.setattr(db, "_inited", False)
    monkeypatch.setattr(routes_pdf_hwp, "conversion_root", lambda: tmp_path / "pdf_hwp")
    db.init_db()
    detected = DetectedItem(1, 20, 0, (10.0, 20.0, 110.0, 180.0), "\\direct\\\n20\n빛의 굴절")
    detected_two = DetectedItem(1, 21, 0, (10.0, 190.0, 110.0, 340.0), "\\direct\\\n21\n빛의 반사")
    monkeypatch.setattr(routes_pdf_hwp.pipeline, "detect_items", lambda source: DetectionResult(
        source_pdf=source, source_hash="real-flow", page_count=1, items=(detected, detected_two),
    ))

    def crop(_source: Path, _item: DetectedItem, output_dir: Path, *, dpi: int = 300) -> CropArtifact:
        output_dir.mkdir(parents=True, exist_ok=True)
        image = output_dir / f"item-{_item.item_number}.png"
        image.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        ))
        provenance = output_dir / f"item-{_item.item_number}.json"
        provenance.write_text("{}", encoding="utf-8")
        return CropArtifact(image, provenance, 100, 160)

    monkeypatch.setattr(routes_pdf_hwp.pipeline, "crop_item", crop)
    monkeypatch.setattr(
        routes_pdf_hwp.pipeline,
        "build_editable_draft",
        lambda source, item, output_dir, **_kwargs: DraftArtifact(
            item.item_number,
            f"\\direct\\\n{item.item_number}\n{item.source_text.splitlines()[-1]}",
            item.source_text,
            (),
            crop(source, item, output_dir),
            None,
            (),
        ),
    )
    app = FastAPI()
    app.include_router(routes_pdf_hwp.router)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    app.get("/api/standards")(lambda: [])
    app.get("/api/subjects")(lambda: [])
    app.get("/api/subject")(lambda: {"standard_count": 0})
    app.get("/api/propositions")(lambda: [])
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        pdf = tmp_path / "real-flow.pdf"
        pdf.write_bytes(b"%PDF-1.7\nreal browser flow")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.locator('[data-tab="pdf-hwp"]').click()
            page.locator("#pdfHwpFile").set_input_files(pdf)
            page.get_by_role("button", name="변환 시작").click()
            page.locator("#pdfHwpStatus").get_by_text("문항 검출 완료", exact=True).wait_for()
            checkbox = page.get_by_role("checkbox", name="20번 문항 선택")
            assert checkbox.is_checked()
            assert page.get_by_role("checkbox", name="21번 문항 선택").is_checked()
            assert page.locator(".ph-selection-count").get_by_text("2개 문항 선택됨", exact=True).is_visible()
            assert page.get_by_role("img", name="20번 원문 자르기 미리보기").evaluate("img => img.complete && img.naturalWidth > 0")
            assert page.get_by_role("textbox", name="20번 HWP 변환 내용").input_value().startswith("\\direct\\")
            assert page.locator("#pdfHwpTypeset").is_enabled()
            page.get_by_role("button", name="선택 해제").click()
            page.locator(".ph-selection-count").get_by_text("0개 문항 선택됨", exact=True).wait_for()
            assert page.locator("#pdfHwpTypeset").is_disabled()
            page.get_by_role("button", name="전체 선택").click()
            page.locator(".ph-selection-count").get_by_text("2개 문항 선택됨", exact=True).wait_for()
            assert page.locator("#pdfHwpTypeset").is_enabled()
            checkbox.uncheck()
            page.locator(".ph-selection-count").get_by_text("1개 문항 선택됨", exact=True).wait_for()
            assert page.locator("#pdfHwpTypeset").is_enabled()
            page.get_by_role("checkbox", name="21번 문항 선택").uncheck()
            page.locator(".ph-selection-count").get_by_text("0개 문항 선택됨", exact=True).wait_for()
            assert page.locator("#pdfHwpTypeset").is_disabled()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.parametrize("completion_order", ["request-order", "response-order", "equal-revision"])
def test_out_of_order_item_selection_responses_keep_newest_revision(completion_order: str) -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        initial = _job("review", palette_markdown="ready") | {"id": 301, "name": "Race job", "revision": 5}
        first = dict(initial["items"][0], id=401, source_number=20, selected=True, revision=1)
        second = dict(initial["items"][0], id=402, source_number=21, selected=True, revision=1)
        initial["items"] = [first, second]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """([initial, order]) => {
                  window.fetch = (url, options = {}) => {
                    const path = String(url);
                    if (path.endsWith('/api/pdf-hwp/jobs') && (!options.method || options.method === 'GET')) return Promise.resolve(new Response(JSON.stringify({items: [initial]}), {status: 200, headers: {'Content-Type': 'application/json'}}));
                    if (path.endsWith('/items/401')) {
                      const revision = order === 'response-order' ? 7 : order === 'equal-revision' ? 5 : 6;
                      const items = order === 'response-order'
                        ? [{...initial.items[0], selected: false, revision: 2}, {...initial.items[1], selected: false, revision: 2}]
                        : [{...initial.items[0], selected: false, revision: 2}, initial.items[1]];
                      return new Promise(resolve => setTimeout(() => resolve(new Response(JSON.stringify({...initial, revision, items}), {status: 200, headers: {'Content-Type': 'application/json'}})), 600));
                    }
                    if (path.endsWith('/items/402')) {
                      const revision = order === 'request-order' ? 7 : order === 'equal-revision' ? 5 : 6;
                      const items = order === 'response-order'
                        ? [initial.items[0], {...initial.items[1], selected: false, revision: 2}]
                        : [{...initial.items[0], selected: false, revision: 2}, {...initial.items[1], selected: false, revision: 2}];
                      return new Promise(resolve => setTimeout(() => resolve(new Response(JSON.stringify({...initial, revision, items}), {status: 200, headers: {'Content-Type': 'application/json'}})), 100));
                    }
                    return Promise.resolve(new Response(JSON.stringify({detail: 'unexpected'}), {status: 404, headers: {'Content-Type': 'application/json'}}));
                  };
                }""",
                [initial, completion_order],
            )
            page.locator('[data-tab="pdf-hwp"]').click()
            page.get_by_role("checkbox", name="20번 문항 선택").uncheck()
            page.get_by_role("checkbox", name="21번 문항 선택").uncheck()
            page.wait_for_timeout(800)
            assert page.locator(".ph-selection-count").get_by_text("0개 문항 선택됨", exact=True).is_visible()
            assert page.get_by_role("checkbox", name="20번 문항 선택").is_checked() is False
            assert page.get_by_role("checkbox", name="21번 문항 선택").is_checked() is False
            assert page.locator("#pdfHwpTypeset").is_disabled()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


@pytest.mark.parametrize("mutation_case", ["batch-then-individual", "checkbox-then-save"])
def test_same_job_review_mutations_follow_user_intent_order(mutation_case: str) -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        initial = _job("review", palette_markdown="old") | {"id": 501, "name": "Queue job", "revision": 5}
        initial["items"] = [
            dict(initial["items"][0], id=601, source_number=20, selected=False, revision=1),
            dict(initial["items"][0], id=602, source_number=21, selected=False, revision=1),
        ]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """([initial, mutationCase]) => {
                  let revision = 5;
                  let state = structuredClone(initial);
                  window.patchBodies = [];
                  window.fetch = async (url, options = {}) => {
                    const path = String(url);
                    if (path.endsWith('/api/pdf-hwp/jobs') && (!options.method || options.method === 'GET')) return new Response(JSON.stringify({items: [state]}), {status: 200, headers: {'Content-Type': 'application/json'}});
                    if (path.includes('/items/')) {
                      const body = JSON.parse(options.body);
                      window.patchBodies.push(body);
                      const id = Number(path.split('/').pop());
                      if (mutationCase === 'batch-then-individual' && window.patchBodies.length === 1) await new Promise(resolve => setTimeout(resolve, 400));
                      if (mutationCase === 'checkbox-then-save' && window.patchBodies.length === 1) await new Promise(resolve => setTimeout(resolve, 400));
                      const index = state.items.findIndex(item => item.id === id);
                      const item = {...state.items[index], revision: state.items[index].revision + 1};
                      if ('selected' in body) item.selected = body.selected;
                      if ('palette_markdown' in body) item.draft = {...item.draft, palette_markdown: body.palette_markdown};
                      state.items[index] = item;
                      state = {...state, revision: ++revision};
                      return new Response(JSON.stringify(state), {status: 200, headers: {'Content-Type': 'application/json'}});
                    }
                    return new Response(JSON.stringify({detail: 'unexpected'}), {status: 404, headers: {'Content-Type': 'application/json'}});
                  };
                }""",
                [initial, mutation_case],
            )
            page.locator('[data-tab="pdf-hwp"]').click()
            if mutation_case == "batch-then-individual":
                page.get_by_role("button", name="전체 선택").click()
                page.get_by_role("checkbox", name="21번 문항 선택").check()
                page.get_by_role("checkbox", name="21번 문항 선택").uncheck()
                page.wait_for_timeout(1100)
                assert page.get_by_role("checkbox", name="21번 문항 선택").is_checked() is False
            else:
                editor = page.get_by_role("textbox", name="20번 HWP 변환 내용")
                page.get_by_role("checkbox", name="20번 문항 선택").check()
                editor.fill("new")
                page.locator(".ph-review-item", has=editor).get_by_role("button", name="문항 내용 저장").click()
                page.wait_for_timeout(1000)
                assert page.get_by_role("textbox", name="20번 HWP 변환 내용").input_value() == "new"
                assert "palette_markdown" not in page.evaluate("window.patchBodies[0]")
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


@pytest.mark.parametrize("list_result", ["stale-success", "stale-error"])
def test_delayed_job_list_cannot_overwrite_newer_item_mutation(list_result: str) -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        old = _job("review", palette_markdown="ready") | {"id": 701, "name": "List race", "revision": 5}
        old["items"] = [dict(old["items"][0], id=801, selected=True, revision=1)]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """([old, result]) => {
                  let listCalls = 0;
                  window.fetch = (url, options = {}) => {
                    const path = String(url);
                    if (path.endsWith('/api/pdf-hwp/jobs') && (!options.method || options.method === 'GET')) {
                      listCalls += 1;
                      if (listCalls === 1) return Promise.resolve(new Response(JSON.stringify({items:[old]}), {status:200, headers:{'Content-Type':'application/json'}}));
                      return new Promise(resolve => setTimeout(() => resolve(result === 'stale-error'
                        ? new Response(JSON.stringify({detail:'stale list failure'}), {status:503, headers:{'Content-Type':'application/json'}})
                        : new Response(JSON.stringify({items:[old]}), {status:200, headers:{'Content-Type':'application/json'}})), 600));
                    }
                    if (path.endsWith('/items/801')) {
                      const fresh = {...old, revision:6, items:[{...old.items[0], selected:false, revision:2}]};
                      return new Promise(resolve => setTimeout(() => resolve(new Response(JSON.stringify(fresh), {status:200, headers:{'Content-Type':'application/json'}})), 100));
                    }
                    return Promise.resolve(new Response(JSON.stringify({detail:'unexpected'}), {status:404, headers:{'Content-Type':'application/json'}}));
                  };
                }""",
                [old, list_result],
            )
            page.locator('[data-tab="pdf-hwp"]').click()
            page.locator("#pdfHwpRefresh").click()
            page.get_by_role("checkbox", name="20번 문항 선택").uncheck()
            page.wait_for_timeout(800)
            assert page.get_by_role("checkbox", name="20번 문항 선택").is_checked() is False
            assert page.locator("#pdfHwpStatus").get_attribute("role") == "status"
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_delayed_detail_error_cannot_overwrite_newer_same_job_success() -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        pending = _job("detecting", palette_markdown="ready") | {"id": 901, "name": "Detail race", "revision": 1}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """pending => {
                  window.fetch = (url, options = {}) => {
                    const path = String(url);
                    if (path.endsWith('/api/pdf-hwp/jobs') && (!options.method || options.method === 'GET')) return Promise.resolve(new Response(JSON.stringify({items:[pending]}), {status:200, headers:{'Content-Type':'application/json'}}));
                    if (path.endsWith('/api/pdf-hwp/jobs/901') && (!options.method || options.method === 'GET')) return new Promise(resolve => setTimeout(() => resolve(new Response(JSON.stringify({detail:'stale detail failure'}), {status:503, headers:{'Content-Type':'application/json'}})), 600));
                    if (path.endsWith('/api/pdf-hwp/jobs/901/retry')) return Promise.resolve(new Response(JSON.stringify({...pending, status:'review', error:null, revision:2}), {status:200, headers:{'Content-Type':'application/json'}}));
                    return Promise.resolve(new Response(JSON.stringify({detail:'unexpected'}), {status:404, headers:{'Content-Type':'application/json'}}));
                  };
                }""",
                pending,
            )
            page.locator('[data-tab="pdf-hwp"]').click()
            page.wait_for_timeout(1600)
            page.evaluate("window.EP.pdfHwpRetry()")
            page.wait_for_timeout(700)
            assert page.locator("#pdfHwpStatus").get_by_text("문항 검출 완료", exact=True).is_visible()
            assert page.locator("#pdfHwpStatus").get_attribute("role") == "status"
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


@pytest.mark.parametrize("mutation_kind", ["checkbox", "save"])
def test_typeset_waits_for_pending_review_mutation(mutation_kind: str) -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        initial = _job("review", palette_markdown="ready") | {"id": 1001, "revision": 5}
        initial["items"] = [
            dict(initial["items"][0], id=1101, source_number=20, revision=1),
            dict(initial["items"][0], id=1102, source_number=21, revision=1),
        ]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """initial => {
                  window.requestOrder = [];
                  window.fetch = async (url, options = {}) => {
                    const path = String(url);
                    if (path.endsWith('/api/pdf-hwp/jobs') && (!options.method || options.method === 'GET')) return new Response(JSON.stringify({items:[initial]}), {status:200, headers:{'Content-Type':'application/json'}});
                    if (path.endsWith('/items/1101')) {
                      window.requestOrder.push('patch-start');
                      await new Promise(resolve => setTimeout(resolve, 500));
                      window.requestOrder.push('patch-done');
                      const body = JSON.parse(options.body);
                      const item = {...initial.items[0], revision:2};
                      if ('selected' in body) item.selected = body.selected;
                      if ('palette_markdown' in body) item.draft = {...item.draft, palette_markdown:body.palette_markdown};
                      return new Response(JSON.stringify({...initial, revision:6, items:[item, initial.items[1]]}), {status:200, headers:{'Content-Type':'application/json'}});
                    }
                    if (path.endsWith('/typeset')) {
                      window.requestOrder.push('typeset');
                      return new Response(JSON.stringify({...initial, status:'typesetting', revision:7}), {status:200, headers:{'Content-Type':'application/json'}});
                    }
                    return new Response(JSON.stringify({detail:'unexpected'}), {status:404, headers:{'Content-Type':'application/json'}});
                  };
                }""",
                initial,
            )
            page.locator('[data-tab="pdf-hwp"]').click()
            if mutation_kind == "checkbox":
                page.get_by_role("checkbox", name="20번 문항 선택").uncheck()
            else:
                editor = page.get_by_role("textbox", name="20번 HWP 변환 내용")
                editor.fill("new draft")
                page.locator(".ph-review-item", has=editor).get_by_role("button", name="문항 내용 저장").click()
            page.wait_for_function("window.requestOrder.includes('patch-start')")
            assert page.locator("#pdfHwpTypeset").is_disabled()
            page.evaluate("window.EP.pdfHwpTypeset()")
            page.wait_for_function("window.requestOrder.includes('typeset')")
            assert page.evaluate("window.requestOrder") == ["patch-start", "patch-done", "typeset"]
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_failed_selection_patch_reverts_to_persisted_state() -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        initial = _job("review", palette_markdown="ready") | {"id": 1201, "revision": 5}
        initial["items"] = [dict(initial["items"][0], id=1301, selected=True)]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """initial => { window.fetch = (url, options = {}) => {
                  const path = String(url);
                  if (path.endsWith('/api/pdf-hwp/jobs') && (!options.method || options.method === 'GET')) return Promise.resolve(new Response(JSON.stringify({items:[initial]}), {status:200, headers:{'Content-Type':'application/json'}}));
                  if (path.endsWith('/items/1301')) return Promise.resolve(new Response(JSON.stringify({detail:'selection failed'}), {status:503, headers:{'Content-Type':'application/json'}}));
                  return Promise.resolve(new Response(JSON.stringify({detail:'unexpected'}), {status:404, headers:{'Content-Type':'application/json'}}));
                }; }""",
                initial,
            )
            page.locator('[data-tab="pdf-hwp"]').click()
            checkbox = page.get_by_role("checkbox", name="20번 문항 선택")
            checkbox.click()
            page.locator("#pdfHwpStatus").get_by_text("요청을 완료하지 못했습니다", exact=True).wait_for()
            assert checkbox.is_checked()
            assert page.locator(".ph-selection-count").get_by_text("1개 문항 선택됨", exact=True).is_visible()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_typeset_locks_new_review_mutations_until_response() -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        initial = _job("review", palette_markdown="ready") | {"id": 1401, "revision": 5}
        initial["items"] = [dict(initial["items"][0], id=1501)]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """initial => {
                  window.patchCalls = 0;
                  window.fetch = async (url, options = {}) => {
                    const path = String(url);
                    if (path.endsWith('/api/pdf-hwp/jobs') && (!options.method || options.method === 'GET')) return new Response(JSON.stringify({items:[initial]}), {status:200, headers:{'Content-Type':'application/json'}});
                    if (path.endsWith('/typeset')) { await new Promise(resolve => setTimeout(resolve, 600)); return new Response(JSON.stringify({...initial, status:'typesetting', revision:6}), {status:200, headers:{'Content-Type':'application/json'}}); }
                    if (path.includes('/items/')) { window.patchCalls += 1; return new Response(JSON.stringify(initial), {status:200, headers:{'Content-Type':'application/json'}}); }
                    return new Response(JSON.stringify({detail:'unexpected'}), {status:404, headers:{'Content-Type':'application/json'}});
                  };
                }""",
                initial,
            )
            page.locator('[data-tab="pdf-hwp"]').click()
            page.locator("#pdfHwpTypeset").click()
            assert page.get_by_role("checkbox", name="20번 문항 선택").is_disabled()
            assert page.get_by_role("textbox", name="20번 HWP 변환 내용").is_disabled()
            assert page.get_by_role("button", name="문항 내용 저장").is_disabled()
            assert page.get_by_role("button", name="전체 선택").is_disabled()
            page.wait_for_timeout(200)
            assert page.evaluate("window.patchCalls") == 0
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_broken_source_asset_falls_back_to_owned_pdf_link() -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        initial = _job("review", palette_markdown="ready") | {"id": 1601}
        initial["assets"] = [{"id": 1701, "item_id": 31, "role": "source_crop", "media_type": "image/png"}]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """initial => { window.fetch = (url, options = {}) => {
                  const path = String(url);
                  if (path.endsWith('/api/pdf-hwp/jobs') && (!options.method || options.method === 'GET')) return Promise.resolve(new Response(JSON.stringify({items:[initial]}), {status:200, headers:{'Content-Type':'application/json'}}));
                  return Promise.resolve(new Response(JSON.stringify({detail:'unexpected'}), {status:404, headers:{'Content-Type':'application/json'}}));
                }; }""",
                initial,
            )
            page.locator('[data-tab="pdf-hwp"]').click()
            fallback = page.get_by_role("link", name="PDF 4쪽 원문 열기")
            fallback.wait_for()
            assert fallback.get_attribute("href").endswith("/api/pdf-hwp/jobs/1601/source#page=4")
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_unsaved_or_failed_draft_edit_blocks_typeset() -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        initial = _job("review", palette_markdown="persisted") | {"id": 1801}
        initial["items"] = [dict(initial["items"][0], id=1901)]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """initial => { window.typesetCalls = 0; window.fetch = (url, options = {}) => {
                  const path = String(url);
                  if (path.endsWith('/api/pdf-hwp/jobs') && (!options.method || options.method === 'GET')) return Promise.resolve(new Response(JSON.stringify({items:[initial]}), {status:200, headers:{'Content-Type':'application/json'}}));
                  if (path.endsWith('/items/1901')) return Promise.resolve(new Response(JSON.stringify({detail:'save failed'}), {status:503, headers:{'Content-Type':'application/json'}}));
                  if (path.endsWith('/typeset')) { window.typesetCalls += 1; return Promise.resolve(new Response(JSON.stringify(initial), {status:200, headers:{'Content-Type':'application/json'}})); }
                  return Promise.resolve(new Response(JSON.stringify({detail:'unexpected'}), {status:404, headers:{'Content-Type':'application/json'}}));
                }; }""",
                initial,
            )
            page.locator('[data-tab="pdf-hwp"]').click()
            typeset = page.locator("#pdfHwpTypeset")
            assert typeset.is_enabled()
            editor = page.get_by_role("textbox", name="20번 HWP 변환 내용")
            editor.fill("unsaved")
            assert typeset.is_disabled()
            page.evaluate("window.EP.pdfHwpTypeset()")
            assert page.evaluate("window.typesetCalls") == 0
            page.get_by_role("button", name="문항 내용 저장").click()
            page.locator("#pdfHwpStatus").get_by_text("요청을 완료하지 못했습니다", exact=True).wait_for()
            assert editor.input_value() == "unsaved"
            assert typeset.is_disabled()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_selection_failure_reconciles_even_when_later_save_also_fails() -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        initial = _job("review", palette_markdown="ready") | {"id": 2001}
        initial["items"] = [dict(initial["items"][0], id=2101, selected=True)]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """initial => { window.fetch = async (url, options = {}) => {
                  const path = String(url);
                  if (path.endsWith('/api/pdf-hwp/jobs') && (!options.method || options.method === 'GET')) return new Response(JSON.stringify({items:[initial]}), {status:200, headers:{'Content-Type':'application/json'}});
                  if (path.endsWith('/items/2101')) { await new Promise(resolve => setTimeout(resolve, 100)); return new Response(JSON.stringify({detail:'queued failure'}), {status:503, headers:{'Content-Type':'application/json'}}); }
                  return new Response(JSON.stringify({detail:'unexpected'}), {status:404, headers:{'Content-Type':'application/json'}});
                }; }""",
                initial,
            )
            page.locator('[data-tab="pdf-hwp"]').click()
            page.get_by_role("checkbox", name="20번 문항 선택").click()
            page.get_by_role("button", name="문항 내용 저장").click()
            page.locator("#pdfHwpStatus").get_by_text("요청을 완료하지 못했습니다", exact=True).wait_for()
            page.wait_for_timeout(350)
            assert page.get_by_role("checkbox", name="20번 문항 선택").is_checked()
            assert page.locator(".ph-selection-count").get_by_text("1개 문항 선택됨", exact=True).is_visible()
            assert page.locator("#pdfHwpTypeset").is_enabled()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_partial_batch_failure_reconciles_even_when_later_save_fails() -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        initial = _job("review", palette_markdown="ready") | {"id": 2201, "revision": 5}
        initial["items"] = [
            dict(initial["items"][0], id=2301, source_number=20, selected=True),
            dict(initial["items"][0], id=2302, source_number=21, selected=True),
        ]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """initial => {
                  let persisted = structuredClone(initial);
                  window.fetch = async (url, options = {}) => {
                    const path = String(url);
                    if (path.endsWith('/api/pdf-hwp/jobs') && (!options.method || options.method === 'GET')) return new Response(JSON.stringify({items:[initial]}), {status:200, headers:{'Content-Type':'application/json'}});
                    if (path.endsWith('/api/pdf-hwp/jobs/2201') && (!options.method || options.method === 'GET')) return new Response(JSON.stringify(persisted), {status:200, headers:{'Content-Type':'application/json'}});
                    if (path.endsWith('/items/2301')) {
                      const body = JSON.parse(options.body);
                      if ('palette_markdown' in body) return new Response(JSON.stringify({detail:'save-latest'}), {status:503, headers:{'Content-Type':'application/json'}});
                      persisted = {...persisted, revision:6, items:[{...persisted.items[0], selected:false, revision:2}, persisted.items[1]]};
                      return new Response(JSON.stringify(persisted), {status:200, headers:{'Content-Type':'application/json'}});
                    }
                    if (path.endsWith('/items/2302')) { await new Promise(resolve => setTimeout(resolve, 400)); return new Response(JSON.stringify({detail:'batch-stale'}), {status:503, headers:{'Content-Type':'application/json'}}); }
                    return new Response(JSON.stringify({detail:'unexpected'}), {status:404, headers:{'Content-Type':'application/json'}});
                  };
                }""",
                initial,
            )
            page.locator('[data-tab="pdf-hwp"]').click()
            editor = page.get_by_role("textbox", name="20번 HWP 변환 내용")
            editor.fill("dirty newest")
            page.get_by_role("button", name="선택 해제").click()
            page.locator(".ph-review-item", has=editor).get_by_role("button", name="문항 내용 저장").click()
            page.locator("#pdfHwpStatus").get_by_text("save-latest", exact=True).wait_for()
            page.wait_for_timeout(300)
            assert page.get_by_role("checkbox", name="20번 문항 선택").is_checked() is False
            assert page.get_by_role("checkbox", name="21번 문항 선택").is_checked()
            assert page.locator(".ph-selection-count").get_by_text("1개 문항 선택됨", exact=True).is_visible()
            assert page.get_by_role("textbox", name="20번 HWP 변환 내용").input_value() == "dirty newest"
            assert page.locator("#pdfHwpStatus").get_by_text("save-latest", exact=True).is_visible()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_partial_failure_keeps_review_typeset_retry_and_outputs_together() -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/static/index.html"
        _wait_for_server(url)
        partial = _job("partial_failure", palette_markdown="ready", outputs=True) | {
            "id": 2401,
            "error": {"code": "crop_partial_failure", "message": "일부 문항 실패"},
            "capabilities": {"review_items": True, "typeset_selected": True, "retry_failed": True},
        }
        partial["items"].append(dict(
            partial["items"][0], id=2502, source_number=21, status="failed", selected=False,
            draft={"source_text": "21", "palette_markdown": ""},
            error={"code": "draft_extraction_failed", "message": "초안 실패"},
        ))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.evaluate(
                """partial => { window.fetch = (url, options = {}) => {
                  const path = String(url);
                  if (path.endsWith('/api/pdf-hwp/jobs') && (!options.method || options.method === 'GET')) return Promise.resolve(new Response(JSON.stringify({items:[partial]}), {status:200, headers:{'Content-Type':'application/json'}}));
                  return Promise.resolve(new Response(JSON.stringify({detail:'unexpected'}), {status:404, headers:{'Content-Type':'application/json'}}));
                }; }""",
                partial,
            )
            page.locator('[data-tab="pdf-hwp"]').click()
            assert page.locator("#pdfHwpStatus").get_attribute("role") == "alert"
            assert page.locator("#pdfHwpStatus").get_by_text(
                "일부 문항 제외 후 변환 가능", exact=True,
            ).is_visible()
            assert page.get_by_role("checkbox", name="20번 문항 선택").is_checked()
            assert page.get_by_role("textbox", name="20번 HWP 변환 내용").input_value() == "ready"
            current = page.locator("#pdfHwpCurrent")
            current.get_by_text("실패 문항 1개 자세히 보기", exact=True).click()
            assert current.get_by_text("21번 · PDF 4쪽", exact=True).is_visible()
            assert current.get_by_text("초안 실패", exact=True).is_visible()
            assert current.locator(".ph-failure-disclosure").get_by_role(
                "link", name="PDF 4쪽 원문 열기",
            ).is_visible()
            assert page.get_by_role("checkbox", name="21번 문항 선택").count() == 0
            assert page.locator("#pdfHwpTypeset").is_visible()
            assert page.locator("#pdfHwpTypeset").is_enabled()
            assert page.locator("#pdfHwpRetry").is_visible()
            assert page.get_by_role("link", name="편집용 HWP 받기").is_visible()
            assert page.get_by_role("link", name="확인용 PDF 받기").is_visible()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)
