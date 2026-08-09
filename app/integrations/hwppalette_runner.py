"""ExamPool-specific hwpPalette runner with an optional registered base form.

hwpPalette's public CLI intentionally rejects form + template plans. ExamPool needs one
controlled exception: open the registered ``수능AI첫면틀`` first, then append question
templates to its body. This module stays in ExamPool so the upstream CLI contract is not
silently changed for other users.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown-file", required=True)
    parser.add_argument("--layout-style", choices=("school", "suneung"), default="school")
    parser.add_argument("--output-hwp")
    parser.add_argument("--output-pdf")
    parser.add_argument("--hidden", action="store_true")
    args = parser.parse_args(argv)

    source = Path(args.markdown_file)
    if not source.is_file():
        print(f"파일이 없습니다: {source}", file=sys.stderr)
        return 2
    markdown = source.read_text(encoding="utf-8")
    if args.layout_style == "suneung":
        markdown = (
            "\\수능AI첫면틀\\\n"
            "대학수학능력시험 문제지\n"
            "과학탐구 영역\n" + markdown
        )

    from hwp_palette.model import library
    from hwp_palette.model import parser as md_parser
    from hwp_palette.hwp import engine_library, hwp_engine

    ops, warnings = md_parser.build_library_plan(markdown, library.label_lookup())
    if args.layout_style == "suneung" and not ops:
        print("등록된 수능양식을 찾지 못했습니다.", file=sys.stderr)
        return 2

    automated = bool(args.output_hwp or args.output_pdf or args.hidden)
    isolated = None
    try:
        if automated:
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
        for raw, fmt in ((args.output_hwp, "HWP"), (args.output_pdf, "PDF")):
            if raw and not hwp_engine.hwp.save_as(str(Path(raw).resolve()), format=fmt):
                print(f"{fmt} 저장에 실패했습니다: {raw}", file=sys.stderr)
                return 1
    finally:
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
