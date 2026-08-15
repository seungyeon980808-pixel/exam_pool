"""Capture the running PDF-to-HWP UI at required viewports without mutations."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path

from playwright.sync_api import ConsoleMessage, Page, sync_playwright


VIEWPORTS = ((375, 900), (768, 1024), (1280, 900))


def inspect_page(page: Page, width: int, height: int, output_dir: Path) -> dict:
    console_errors: list[str] = []
    page_errors: list[str] = []

    def on_console(message: ConsoleMessage) -> None:
        if message.type == "error":
            console_errors.append(message.text)

    page.on("console", on_console)
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.goto("http://127.0.0.1:8632/", wait_until="domcontentloaded", timeout=30_000)
    page.emulate_media(reduced_motion="reduce")
    tab = page.get_by_role("button", name="PDF→HWP")
    tab.focus()
    tab.press("Enter")
    page.locator("#tab-pdf-hwp").wait_for(state="visible", timeout=10_000)
    try:
        page.locator(".ph-job-card").first.wait_for(state="visible", timeout=10_000)
    except Exception:  # The empty-state UI is still valid capture evidence.
        pass
    page.wait_for_timeout(750)
    page.evaluate(
        """async () => {
          const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
          const step = Math.max(300, Math.floor(window.innerHeight * 0.8));
          for (let y = 0; y < document.documentElement.scrollHeight; y += step) {
            window.scrollTo(0, y);
            await pause(120);
          }
          window.scrollTo(0, document.documentElement.scrollHeight);
          await pause(300);
          window.scrollTo(0, 0);
          await pause(300);
        }"""
    )
    screenshot = output_dir / f"pdf-hwp-{width}x{height}.png"
    page.screenshot(path=str(screenshot), full_page=True)
    metrics = page.evaluate(
        """() => {
          const root = document.documentElement;
          const cards = [...document.querySelectorAll('.ph-job-card')];
          const failures = [...document.querySelectorAll('.ph-failure-disclosure')];
          const buttons = [...document.querySelectorAll('button')].filter(el =>
            !el.hidden && el.getClientRects().length > 0);
          const images = [...document.images];
          const smallTargets = buttons.filter(el => {
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && (r.width < 44 || r.height < 44);
          }).map(el => ({text: el.textContent.trim(), width: el.getBoundingClientRect().width,
                         height: el.getBoundingClientRect().height}));
          return {
            title: document.title,
            active_tab: document.querySelector('[aria-current="page"]')?.textContent?.trim() || '',
            viewport_width: root.clientWidth,
            document_scroll_width: root.scrollWidth,
            horizontal_overflow_px: Math.max(0, root.scrollWidth - root.clientWidth),
            job_card_count: cards.length,
            failure_disclosure_count: failures.length,
            visible_button_count: buttons.length,
            image_count: images.length,
            incomplete_image_count: images.filter(el => !el.complete).length,
            zero_natural_size_image_count: images.filter(el => el.complete &&
              (el.naturalWidth === 0 || el.naturalHeight === 0)).length,
            zero_natural_size_images: images.filter(el => el.complete &&
              (el.naturalWidth === 0 || el.naturalHeight === 0)).map(el => ({
                alt: el.alt,
                src: el.getAttribute('src') || '',
                context: el.closest('.ph-review-item, .ph-job-card')?.innerText?.trim().slice(0, 240) || '',
              })),
            small_targets: smallTargets,
            status_text: document.querySelector('#pdfHwpStatus')?.innerText?.trim() || '',
            current_text: document.querySelector('#pdfHwpCurrent')?.innerText?.trim() || '',
          };
        }"""
    )
    return {
        "viewport": {"width": width, "height": height},
        "screenshot": str(screenshot.resolve()),
        "metrics": metrics,
        "keyboard_enter_selected_tab": metrics["active_tab"] == "PDF→HWP",
        "lazy_scroll_primed": True,
        "console_errors": console_errors,
        "page_errors": page_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for width, height in VIEWPORTS:
                page = browser.new_page(viewport={"width": width, "height": height})
                try:
                    results.append(inspect_page(page, width, height, args.output_dir))
                finally:
                    page.close()
        finally:
            browser.close()
    receipt = {
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "target": "http://127.0.0.1:8632/",
        "read_only": True,
        "results": results,
    }
    receipt_path = args.output_dir / "ui-capture.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "receipt": str(receipt_path.resolve()),
        "captures": len(results),
        "horizontal_overflow_px": [item["metrics"]["horizontal_overflow_px"] for item in results],
        "console_error_count": sum(len(item["console_errors"]) for item in results),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
