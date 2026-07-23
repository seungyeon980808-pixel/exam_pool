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

# 쪽마다 반복되는 머리말. 성취기준 문장 끝에 이게 붙어 들어오는 사고를 막는다.
#   '과학과 교육과정' / '공통 교육과정' / '선택 중심 교육과정 – 진로 선택 과목 -'
RUNNING_HEAD_RE = re.compile(r"^\s*(?:\[)?(?:[가-힣]+과\s+|공통\s*|선택\s*중심\s*)*교육과정"
                             r"(?:\s*[–—-]\s*[가-힣 ]+\s*[–—-]?)?(?:\])?\s*$")


def is_noise(s: str) -> bool:
    """페이지 머리말·쪽번호처럼 본문이 아닌 줄."""
    return not s or s in NOISE_SUFFIX or s.isdigit() or bool(RUNNING_HEAD_RE.match(s))


# 단원 하나가 끝날 때 붙는 세 덩어리. 편집 형식이 전 과목 동일하다.
#   <탐구 활동>              → 단원 단위
#   (가) 성취기준 해설        → 성취기준 단위 ("여기까지만 다룬다"). 출제 범위 판단에 직결
#   (나) 성취기준 적용 시 고려 사항 → 단원 단위 (타 학년군 연계·안전·평가 유의점)
INQUIRY_RE = re.compile(r"^\s*<\s*탐구\s*활동\s*>")
EXPLAIN_RE = re.compile(r"^\s*\(가\)\s*성취기준\s*해설")
CONSIDER_RE = re.compile(r"^\s*\(나\)\s*성취기준\s*적용")
BULLET_RE = re.compile(r"^\s*[•·⋅]\s*(.*)$")
SECTION_END_RE = re.compile(r"^\s*3\.\s*교수")   # '3. 교수⋅학습 및 평가' 부터는 성취기준이 아니다


def _explain_codes(bullet: str, prefix: str):
    """해설 불릿 앞머리의 코드를 뽑는다.

    문서에 나오는 형태:
        [9과11-02]                       하나
        [4과09-01∼02]                    괄호 안에서 범위
        [12물리01-01]∼[12물리01-03]      괄호 밖으로 범위
        [9과01-01], [9과01-02]           나열
    """
    p = re.escape(prefix)
    one = rf"\[{p}\d{{2}}-\d{{2}}(?:\s*[∼~]\s*\d{{2}})?\]"
    head = re.match(rf"^((?:\s*{one}(?:\s*[∼~]\s*{one})?\s*[,·]?)+)", bullet)
    if not head:
        return [], bullet
    codes, span = [], head.group(1)
    parts = re.findall(rf"\[{p}(\d{{2}})-(\d{{2}})(?:\s*[∼~]\s*(\d{{2}}))?\]", span)
    i = 0
    while i < len(parts):
        unit, a, b = parts[i]
        lo = hi = int(a)
        if b:
            hi = int(b)
        elif (i + 1 < len(parts) and parts[i + 1][0] == unit
              and re.search(rf"\]\s*[∼~]\s*\[{p}{unit}-{parts[i + 1][1]}", span)):
            hi = int(parts[i + 1][1])   # 괄호 밖으로 이어진 범위
            i += 1
        for seq in range(lo, max(lo, hi) + 1):
            codes.append(f"[{prefix}{int(unit):02d}-{seq:02d}]")
        i += 1
    # 뒤 공백은 살린다 — 줄이 단어 경계에서 끊겼다는 표시라 지우면 단어가 붙어버린다
    return codes, bullet[head.end():].lstrip()


def parse_standards(text: str, prefix: str, start_marker: str = "", end_marker: str = ""):
    """성취기준 + 단원 + 해설/유의사항/탐구활동 추출.

    prefix: 성취기준 코드 접두사 (예: '9과', '9영', '10통과1-')
    start_marker/end_marker: 해당 과목 구간만 자를 때 (없으면 전체)

    반환: (units, standards)
      units    {unit_no: {"name", "inquiry": [...], "consider": [...]}}
      standards[{"code","unit_no","seq","text","explain"}]
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

    units, standards = {}, []
    explains = {}          # code -> 해설
    current = None         # 누적 중인 성취기준
    mode = None            # None | 'inquiry' | 'explain' | 'consider'
    unit_no = None
    bullet = None          # 누적 중인 불릿 (mode 별)
    bullet_codes = []

    def flush_std():
        nonlocal current
        if current:
            current["text"] = re.sub(r"\s+", " ", current["text"]).strip()
            standards.append(current)
        current = None

    def flush_bullet():
        nonlocal bullet, bullet_codes
        if bullet is not None:
            txt = re.sub(r"\s+", " ", bullet).strip()
            if txt and unit_no in units:
                if mode == "explain":
                    for c in bullet_codes:
                        explains[c] = (explains.get(c, "") + " " + txt).strip()
                elif mode in ("inquiry", "consider"):
                    units[unit_no][mode].append(txt)
        bullet, bullet_codes = None, []

    def flush_all():
        flush_std()
        flush_bullet()

    for raw in lines:
        line = raw.replace("\x0c", "").rstrip("\r\n")
        s = line.strip()
        # PDF 는 줄이 단어 중간에서 끊기면 끝에 공백이 없고, 단어 경계에서 끊기면
        # 공백을 남긴다. 이 차이를 살려야 '추\n론할' 이 '추 론할' 로 붙지 않는다.
        chunk = s + (" " if line != line.rstrip() else "")
        if is_noise(s):
            continue  # 페이지 구분·머리말·쪽번호는 건너뛰되 누적은 유지

        if SECTION_END_RE.match(line):
            break

        m = unit_re.match(line)
        if m:
            flush_all()
            n, name = int(m.group(1)), m.group(2).strip()
            if 1 <= n <= 40 and n not in units:
                units[n] = {"name": name, "inquiry": [], "consider": []}
                unit_no, mode = n, None
            continue

        if INQUIRY_RE.match(line):
            flush_all(); mode = "inquiry"; continue
        if EXPLAIN_RE.match(line):
            flush_all(); mode = "explain"; continue
        if CONSIDER_RE.match(line):
            flush_all(); mode = "consider"; continue

        m = code_re.match(line)
        if m:
            flush_all()
            mode = "std"
            unit_no = int(m.group(1))
            units.setdefault(unit_no, {"name": f"({unit_no})", "inquiry": [], "consider": []})
            current = {"unit_no": unit_no, "seq": int(m.group(2)),
                       "code": f"[{prefix}{m.group(1)}-{m.group(2)}]", "text": m.group(3)}
            continue

        b = BULLET_RE.match(line)
        if b and mode in ("inquiry", "explain", "consider"):
            flush_bullet()
            body = b.group(1)
            if mode == "explain":
                bullet_codes, body = _explain_codes(body, prefix)
            bullet = body
            continue

        if b:                       # 성취기준 본문 중 불릿이면 문단이 끝난 것
            flush_std()
            continue

        if current is not None:
            current["text"] += chunk
        elif bullet is not None:
            bullet += chunk

    flush_all()
    for st in standards:
        st["explain"] = explains.get(st["code"], "")
    return units, standards


# 줄바꿈으로 쪼개진 단어 복구 (한 글자 토큰 + 다음 토큰 결합)
SPLIT_FIX = re.compile(r"(?<=[가-힣]) (?=[가-힣]{1,2}[을를이가은는의로과와에서])")


def report(units, standards) -> str:
    n_exp = sum(1 for s in standards if s.get("explain"))
    out = [f"단원 {len(units)}개, 성취기준 {len(standards)}개 (해설 {n_exp}개)"]
    for n in sorted(units):
        cnt = sum(1 for s in standards if s["unit_no"] == n)
        u = units[n]
        out.append(f"  ({n}) {u['name']} — {cnt}개 "
                   f"[탐구 {len(u['inquiry'])} · 유의 {len(u['consider'])}]")
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
        "units": [{"unit_no": n, "name": units[n]["name"],
                   "inquiry": units[n]["inquiry"], "consider": units[n]["consider"]}
                  for n in sorted(units)],
        "standards": [
            {"code": s["code"], "grade_band": args.grade_band,
             "unit_no": s["unit_no"], "seq": s["seq"], "text": s["text"],
             "explain": s.get("explain", "")}
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
