r"""마크다운 파일 → 한글 조판 CLI (ExamMaker 파이프라인의 마지막 구간).

    python -m hwp_palette.cli --markdown-file 세트.md [--append]

ExamPool 이 내보낸 세트 마크다운(\템플릿\ + 빈칸 줄 문법)을 통째로 받아,
한글을 열고(없으면 실행) 새 문서에 전체를 조판한다. GUI 의 Ctrl+Alt+T 와
같은 엔진(build_library_plan → execute_library_plan)을 쓰되, '선택 영역'
대신 파일이 입력이라는 점만 다르다.

**저장은 하지 않는다** — 조판 결과를 사용자가 한글에서 확인하고 직접 저장한다
(5E 의 "저장은 사용자 손으로" 원칙과 같다).
"""
import argparse
import pathlib
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m hwp_palette.cli",
        description="마크다운 파일을 한글 새 문서에 조판한다 (저장은 사용자가).")
    ap.add_argument("--markdown-file", required=True,
                    help="조판할 마크다운 파일 경로 (UTF-8)")
    ap.add_argument("--append", action="store_true",
                    help="새 문서를 만들지 않고 지금 활성 문서의 커서 위치에 조판")
    ap.add_argument("--exam-page", action="store_true",
                    help="시험지 판형(2단)으로 쪽을 설정한 뒤 조판한다 (--append 와 함께 쓰지 않음)")
    ap.add_argument("--output-hwp",
                    help="조판 결과를 지정한 HWP로 저장한다 (미리보기·자동화용)")
    ap.add_argument("--output-pdf",
                    help="조판 결과를 지정한 PDF로 저장한다 (미리보기·자동화용)")
    ap.add_argument("--hidden", action="store_true",
                    help="독립된 숨은 한글 인스턴스에서 조판한다 (출력 경로 필요)")
    args = ap.parse_args(argv)

    automated = bool(args.output_hwp or args.output_pdf or args.hidden)
    if args.append and automated:
        print("--append 는 --output-hwp/--output-pdf/--hidden 과 함께 쓸 수 없습니다.",
              file=sys.stderr)
        return 2
    if args.hidden and not (args.output_hwp or args.output_pdf):
        print("--hidden 은 --output-hwp 또는 --output-pdf와 함께 써야 합니다.",
              file=sys.stderr)
        return 2

    src = pathlib.Path(args.markdown_file)
    if not src.is_file():
        print(f"파일이 없습니다: {src}", file=sys.stderr)
        return 2
    text = src.read_text(encoding="utf-8")
    if not text.strip():
        print("파일이 비어 있습니다.", file=sys.stderr)
        return 2

    # 무거운 것(COM·라이브러리)은 인자 검증이 끝난 뒤에야 불러온다 —
    # --help 만 치는 사람이 한글 연결을 기다리게 하지 않는다.
    from hwp_palette.core import applog
    from hwp_palette.model import library
    from hwp_palette.model import parser as md_parser
    from hwp_palette.hwp import engine_library, hwp_engine

    if not md_parser.has_library_tokens(text):
        print("라이브러리 문법(\\라벨\\)이 없습니다 — ExamPool 의 세트 export 결과를 "
              "넣어 주세요. 시험문제 문법(번호:/발문: …)은 GUI 의 Ctrl+Alt+T 로.",
              file=sys.stderr)
        return 2

    lookup = library.label_lookup()
    ops, warns = md_parser.build_library_plan(text, lookup)
    has_form = any(op[0] == "form" for op in ops)

    isolated_hwp = None
    try:
        try:
            if automated:
                # 미리보기는 사용자가 작업 중인 한글 문서에 붙지 않는다. DispatchEx를
                # 사용하는 pyhwpx의 new=True로 독립 인스턴스를 만들고 이 실행이 끝날
                # 때만 닫는다.
                from pyhwpx import Hwp
                isolated_hwp = Hwp(new=True, visible=not args.hidden,
                                   register_module=True, on_quit=False)
                hwp_engine.hwp = isolated_hwp
            else:
                hwp_engine.connect()
        except Exception as e:
            applog.exc("CLI: 한글 연결 실패", e)
            print(f"한글에 연결하지 못했습니다: {e}", file=sys.stderr)
            return 1

        # 양식은 자신이 용지·여백·단을 가진 새 문서이므로 빈 문서를 먼저 만들거나
        # --exam-page로 덮어쓰지 않는다. execute_library_plan이 양식을 연 뒤 그 안의
        # \\본문\\ 위치에 템플릿들을 이어 넣는다.
        if not args.append and not has_form:
            if not automated:
                hwp_engine.new_document()
            if args.exam_page and not engine_library.apply_exam_page():
                print("주의: 시험지 판형(2단) 적용에 실패해 기본 판형으로 조판합니다.",
                      file=sys.stderr)

        result = engine_library.execute_library_plan(
            ops, library.template_path, form_path_fn=library.template_path)
        if result.get("error"):
            print(f"조판 실패: {result['error']}", file=sys.stderr)
            return 1

        for raw, fmt in ((args.output_hwp, "HWP"), (args.output_pdf, "PDF")):
            if not raw:
                continue
            dest = pathlib.Path(raw).resolve()
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not hwp_engine.hwp.save_as(str(dest), format=fmt):
                print(f"{fmt} 저장에 실패했습니다: {dest}", file=sys.stderr)
                return 1
    finally:
        if isolated_hwp is not None:
            try:
                isolated_hwp.quit()
            except Exception as e:
                applog.exc("CLI: 미리보기 한글 인스턴스 닫기 실패", e)
            finally:
                hwp_engine.hwp = None

    n_tpl = sum(1 for op in ops if op[0] == "template")
    n_line = len(ops) - n_tpl
    print(f"조판 완료: 템플릿 {n_tpl}개 + 줄 {n_line}개 ({src.name})")
    for w in warns:
        print(f"  주의: {w}")
    if automated:
        if args.output_hwp:
            print(f"HWP 저장: {pathlib.Path(args.output_hwp).resolve()}")
        if args.output_pdf:
            print(f"PDF 저장: {pathlib.Path(args.output_pdf).resolve()}")
    else:
        print("한글에서 결과를 확인하고 직접 저장하세요 — CLI 는 저장하지 않습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
