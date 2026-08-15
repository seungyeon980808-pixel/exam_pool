"""ExamPool-specific hwpPalette runner with an optional registered base form.

hwpPalette's public CLI intentionally rejects form + template plans. ExamPool needs one
controlled exception: open the registered ``수능AI첫면틀`` first, then append question
templates to its body. This module stays in ExamPool so the upstream CLI contract is not
silently changed for other users.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import threading
import time


def _prefer_exam_pool_runtime() -> Path:
    """Put ExamPool's patched HwpPalette runtime before development siblings."""
    embedded = Path(__file__).resolve().parents[2] / "vendor" / "hwp_typesetter"
    sys.path.insert(0, str(embedded))
    return embedded


def _needs_empty_bogi_collapse(markdown: str) -> bool:
    """Recognize the isolated direct-question large-photo repair contract."""
    lines = markdown.splitlines()
    large_photo_label = "\\\uc218\ub2a5\ud569\ub2f51\ub300\uc0ac\uc9c45\uc120\uc9c0\\"
    return (
        len(lines) >= 8
        and lines[0] == "\\수능합답1대사진5선지\\"
        and lines.count(large_photo_label) == 1
        and lines[5:8] == ["-", "-", "-"]
    )


def _configure_source_crop_frame(layout: dict[str, float]) -> None:
    """Apply the measured KICE item frame used by direct source-crop figures."""
    layout["figure_frame_width_mm"] = 114.3
    layout["figure_target_ratio"] = 0.845


def _source_reconstruction_paragraph_settings() -> dict[str, int]:
    """Return the measured paragraph settings for the KICE source item."""
    return {
        "break_non_latin_word": 0,
        "condense_percent": 20,
        "character_ratio_percent": 85,
    }


def _ensure_file_path_checker_registry() -> bool:
    """Verify startup registration, with a best-effort repair for direct runs."""
    # The runner's working directory is the bundled HwpPalette runtime, while
    # Python always places this script's own directory on sys.path.
    from hwp_security import ensure_registration, registration_valid

    if registration_valid():
        return True
    ok, _ = ensure_registration()
    return ok


def _hwp_process_ids() -> set[int]:
    """Return active HWP process ids without making psutil a hard dependency."""
    try:
        import psutil
        return {
            process.pid for process in psutil.process_iter(("name",))
            if str(process.info.get("name") or "").lower() == "hwp.exe"
        }
    except Exception:
        return set()


def _start_hwp_pid_watcher(before: set[int], target: Path | None):
    """Record the COM-launched HWP even if its constructor later blocks."""
    stop = threading.Event()
    if target is None:
        return stop, None

    def watch():
        deadline = time.monotonic() + 10
        while not stop.is_set() and time.monotonic() < deadline:
            created = sorted(_hwp_process_ids() - before)
            if created:
                target.write_text(str(created[-1]), encoding="ascii")
                return
            stop.wait(0.05)

    worker = threading.Thread(target=watch, name="hwp-pid-watcher", daemon=True)
    worker.start()
    return stop, worker


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown-file", required=True)
    parser.add_argument("--layout-style", choices=("school", "suneung"), default="school")
    parser.add_argument("--output-hwp")
    parser.add_argument("--output-pdf")
    parser.add_argument("--hwp-pid-file")
    parser.add_argument("--hidden", action="store_true")
    args = parser.parse_args(argv)

    source = Path(args.markdown_file)
    if not source.is_file():
        print(f"파일이 없습니다: {source}", file=sys.stderr)
        return 2
    _prefer_exam_pool_runtime()
    from hwp_palette.model import library
    from hwp_palette.model import parser as md_parser
    from hwp_palette.hwp import engine_library, hwp_engine

    markdown = source.read_text(encoding="utf-8")
    if _needs_empty_bogi_collapse(markdown):
        _configure_source_crop_frame(hwp_engine.S["layout"])
    lookup = library.label_lookup()
    if args.layout_style == "suneung":
        if "수능원안지" in lookup:
            # 새 사용자 양식은 완성된 원안지라 채울 슬롯이 없다.
            markdown = "\\수능원안지\\\n" + markdown
        else:
            # 내장 수능 팩과 이전에 만든 팔레트의 계약을 보존한다.
            markdown = (
                "\\수능AI첫면틀\\\n"
                "대학수학능력시험 문제지\n"
                "과학탐구 영역\n" + markdown
            )

    ops, warnings = md_parser.build_library_plan(markdown, lookup)
    if args.layout_style == "suneung" and not ops:
        print("등록된 수능양식을 찾지 못했습니다.", file=sys.stderr)
        return 2

    automated = bool(args.output_hwp or args.output_pdf or args.hidden)
    isolated = None
    pid_watcher_stop = None
    try:
        if automated:
            # Keep pyhwpx's original startup path.  HWP 2022 can return False
            # from a direct RegisterModule call even while its own registration
            # sequence works; treating that value as a hard error regressed the
            # previously working precise-preview flow.
            before_hwp = _hwp_process_ids() if os.name == "nt" else set()
            pid_watcher_stop, _ = _start_hwp_pid_watcher(
                before_hwp, Path(args.hwp_pid_file) if args.hwp_pid_file else None,
            )
            if not _ensure_file_path_checker_registry():
                print("한글 파일 경로 승인 모듈을 등록하지 못했습니다.", file=sys.stderr)
                return 2
            from pyhwpx import Hwp
            isolated = Hwp(new=True, visible=not args.hidden, register_module=True, on_quit=False)
            hwp_engine.hwp = isolated
        else:
            hwp_engine.connect()
            if args.layout_style == "school":
                hwp_engine.new_document()

        if args.layout_style == "school" and not engine_library.apply_exam_page():
            print("주의: 학교 시험지 2단 판형 적용에 실패했습니다.", file=sys.stderr)

        result = engine_library.execute_library_plan(
            ops, library.template_path, form_path_fn=library.template_path,
        )
        if result.get("error"):
            print(f"조판 실패: {result['error']}", file=sys.stderr)
            return 1
        if _needs_empty_bogi_collapse(source.read_text(encoding="utf-8")):
            if not engine_library.delete_table_containing_text("<보 기>"):
                print("직접형 대사진 문항의 빈 <보기> 표를 제거하지 못했습니다.", file=sys.stderr)
                return 1
            if not engine_library.set_paragraph_word_boundary_wrap("가만히 놓았더니"):
                print("직접형 문항의 어절 단위 줄 나눔을 적용하지 못했습니다.", file=sys.stderr)
                return 1
            if not engine_library.set_paragraph_word_boundary_wrap(
                    "물체의 크기,", character_ratio=90,
            ):
                print("직접형 발문의 어절 단위 줄 나눔을 적용하지 못했습니다.", file=sys.stderr)
                return 1
            if not engine_library.delete_trailing_csat_form_page():
                print("사용하지 않는 수능 양식 뒷쪽을 제거하지 못했습니다.", file=sys.stderr)
                return 1
        elif args.layout_style == "suneung":
            # Full conversion sets can retain one header-only continuation page
            # after the last question. It is safe to remove only when the last
            # page has none of the item-content sentinels checked by the engine.
            engine_library.delete_trailing_csat_form_page()
        if args.layout_style == "suneung" and "수능원안지" in lookup:
            # The registered full-page form has no fill contract, but can retain raw
            # backslash placeholders used while the form was authored (for example
            # around the examinee-number boxes).  Manual HwpPalette application strips
            # these marks before use; automated output must do the same.
            engine_library.strip_marks()
        for raw, fmt in ((args.output_hwp, "HWP"), (args.output_pdf, "PDF")):
            if raw and not hwp_engine.hwp.save_as(str(Path(raw).resolve()), format=fmt):
                print(f"{fmt} 저장에 실패했습니다: {raw}", file=sys.stderr)
                return 1
    finally:
        if pid_watcher_stop is not None:
            pid_watcher_stop.set()
        if isolated is not None:
            try:
                isolated.quit()
            finally:
                hwp_engine.hwp = None

    for warning in warnings:
        print(f"주의: {warning}")
    print(f"조판 완료: {args.layout_style}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
