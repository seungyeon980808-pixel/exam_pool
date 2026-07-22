# -*- coding: utf-8 -*-
"""교육과정 PDF → 성취기준 seed(JSON) 추출기.

**다른 과목 선생님이 ExamPool 을 쓰려면 이 도구로 자기 과목 seed 를 만든다.**
2022 개정 교육과정 문서는 과목이 달라도 편집 형식이 같아서(성취기준 코드 + 단원
헤더), 코드 접두사만 바꾸면 대체로 동작한다.

사용:
    python tools/extract_standards.py <교육과정.pdf> --prefix 9과 --subject "중학교 과학 (2022 개정)"
    python tools/extract_standards.py <교육과정.pdf> --prefix 9영 --subject "중학교 영어 (2022 개정)"

결과를 app/seed/standards.json 에 저장하면(--out) 첫 실행 시 DB 에 적재된다.
※ 자동 추출은 초안이다. 반드시 눈으로 검수하라(줄바꿈으로 단어가 쪼개지는 일이 있다).
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Windows 콘솔(cp949)에서 '—' 같은 문자가 깨져 죽는 것을 막는다
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


def extract_text(pdf_path: str) -> str:
    """PyMuPDF 로 전체 텍스트. (poppler 는 한글 CID 폰트에서 실패하는 경우가 있다)"""
    import fitz

    doc = fitz.open(pdf_path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


NOISE_SUFFIX = ("교육과정", "공통 교육과정", "선택 중심 교육과정")


def parse_standards(text: str, prefix: str, start_marker: str = "", end_marker: str = ""):
    """성취기준 + 단원 추출.

    prefix: 성취기준 코드 접두사 (예: '9과', '9영', '9수')
    start_marker/end_marker: 해당 과목 구간만 자를 때 (없으면 전체)
    """
    lines = text.split("\n")
    if start_marker:
        for i, l in enumerate(lines):
            if start_marker in l:
                lines = lines[i:]
                break
    if end_marker:
        for i, l in enumerate(lines):
            if i > 0 and l.strip() == end_marker:
                lines = lines[:i]
                break

    unit_re = re.compile(r"^\s*\((\d{1,2})\)\s*(\S.*\S|\S)\s*$")
    code_re = re.compile(rf"^\s*\[{re.escape(prefix)}(\d{{2}})-(\d{{2}})\]\s*(.*)$")
    bullet_code_re = re.compile(rf"^\s*[•·]\s*\[{re.escape(prefix)}")

    units, standards, current = {}, [], None

    def flush():
        nonlocal current
        if current:
            current["text"] = re.sub(r"\s+", " ", current["text"]).strip()
            standards.append(current)
        current = None

    for raw in lines:
        line = raw.replace("\x0c", "").rstrip()
        s = line.strip()
        if not s or s in NOISE_SUFFIX or s.isdigit():
            continue  # 페이지 구분·머리말·쪽번호는 건너뛰되 누적은 유지

        m = unit_re.match(line)
        if m:
            flush()
            n, name = int(m.group(1)), m.group(2).strip()
            if 1 <= n <= 40 and n not in units:
                units[n] = name
            continue

        if s.startswith("<") or bullet_code_re.match(line):
            flush()
            continue

        m = code_re.match(line)
        if m:
            flush()
            current = {"unit_no": int(m.group(1)), "seq": int(m.group(2)),
                       "code": f"[{prefix}{m.group(1)}-{m.group(2)}]", "text": m.group(3)}
            continue

        if current is not None:
            current["text"] += " " + s

    flush()
    return units, standards


# 줄바꿈으로 쪼개진 단어 복구 (한 글자 토큰 + 다음 토큰 결합)
SPLIT_FIX = re.compile(r"(?<=[가-힣]) (?=[가-힣]{1,2}[을를이가은는의로과와에서])")


def report(units, standards) -> str:
    out = [f"단원 {len(units)}개, 성취기준 {len(standards)}개"]
    for n in sorted(units):
        cnt = sum(1 for s in standards if s["unit_no"] == n)
        out.append(f"  ({n}) {units[n]} — {cnt}개")
    odd = [s for s in standards if not s["text"].endswith(("다.", "다"))]
    if odd:
        out.append(f"※ 종결이 이상한 항목 {len(odd)}개 — 검수 필요")
        for s in odd[:5]:
            out.append(f"   {s['code']} {s['text'][:60]}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="교육과정 PDF → 성취기준 seed JSON")
    ap.add_argument("pdf")
    ap.add_argument("--prefix", required=True, help="성취기준 코드 접두사 (예: 9과, 9영)")
    ap.add_argument("--subject", required=True, help='과목명 (예: "중학교 과학 (2022 개정)")')
    ap.add_argument("--grade-band", default="중학교 1~3학년")
    ap.add_argument("--start", default="", help="구간 시작 표지 (예: [중학교 1~3학년])")
    ap.add_argument("--end", default="", help="구간 끝 표지 (예: 선택 중심 교육과정)")
    ap.add_argument("--out", default="", help="저장 경로 (미지정 시 미리보기만)")
    args = ap.parse_args()

    text = extract_text(args.pdf)
    units, standards = parse_standards(text, args.prefix, args.start, args.end)
    print(report(units, standards))

    if not standards:
        print("\n성취기준을 찾지 못했습니다. --prefix 를 확인하세요 "
              "(문서에서 [9과01-01] 같은 코드의 접두사).")
        sys.exit(1)

    data = {
        "subject": args.subject,
        "source": Path(args.pdf).name,
        "extracted_at": "",
        "units": [{"unit_no": n, "name": units[n]} for n in sorted(units)],
        "standards": [
            {"code": s["code"], "grade_band": args.grade_band,
             "unit_no": s["unit_no"], "seq": s["seq"], "text": s["text"]}
            for s in sorted(standards, key=lambda x: (x["unit_no"], x["seq"]))
        ],
    }
    if args.out:
        Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장: {args.out}  (반드시 눈으로 검수하세요)")
    else:
        print("\n--out 을 주면 파일로 저장합니다.")


if __name__ == "__main__":
    main()
