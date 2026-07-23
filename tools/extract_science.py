# -*- coding: utf-8 -*-
"""과학과 교육과정 PDF → 중·고 전 과목 seed(JSON) 추출기.

extract_standards.py 는 과목 하나를 뽑는 범용 도구다. 이 파일은 그 위에서
'과학과 교육과정'(교육부 고시 제2022-33호 [별책 9]) 한 권에 들어 있는 과목
19개를 한 번에 뽑아 app/seed/standards.json 을 만든다.

    python tools/extract_science.py "2022개정교육과정_과학과.pdf" --out app/seed/standards.json

초등(4과·6과)은 출제 대상이 아니라 뺀다. 중학교 '과학'은 공통 교육과정 안에
초3~중3이 한 과목으로 묶여 있으므로, 코드 접두사 9과 로 중학교 구간만 집는다.

단원 번호는 과목마다 (1)부터 다시 시작한다. DB 는 unit_no 하나로 단원을
구분하므로 과목별 번호대를 100 씩 띄워 전역 유일하게 만든다.
중학교 과학은 1~23 을 그대로 써서 기존 명제 데이터가 어긋나지 않게 한다.
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_standards import extract_text, parse_standards  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


# (코드 접두사, 과목명, 교육과정 구분, 학년군, 단원 번호대)
# 순서는 PDF 수록 순서. base 0 = 중학교(1~23 유지), 나머지는 100 단위.
SUBJECTS = [
    ("9과",      "과학",             "공통",     "중학교 1~3학년", 0),
    ("10통과1-", "통합과학1",        "공통과목", "고등학교 1학년", 100),
    ("10통과2-", "통합과학2",        "공통과목", "고등학교 1학년", 200),
    ("10과탐1-", "과학탐구실험1",    "공통과목", "고등학교 1학년", 300),
    ("10과탐2-", "과학탐구실험2",    "공통과목", "고등학교 1학년", 400),
    ("12물리",   "물리학",           "일반선택", "고등학교",       500),
    ("12화학",   "화학",             "일반선택", "고등학교",       600),
    ("12생과",   "생명과학",         "일반선택", "고등학교",       700),
    ("12지구",   "지구과학",         "일반선택", "고등학교",       800),
    ("12역학",   "역학과 에너지",    "진로선택", "고등학교",       900),
    ("12전자",   "전자기와 양자",    "진로선택", "고등학교",      1000),
    ("12물에",   "물질과 에너지",    "진로선택", "고등학교",      1100),
    ("12반응",   "화학 반응의 세계", "진로선택", "고등학교",      1200),
    ("12세포",   "세포와 물질대사",  "진로선택", "고등학교",      1300),
    ("12유전",   "생물의 유전",      "진로선택", "고등학교",      1400),
    ("12지시",   "지구시스템과학",   "진로선택", "고등학교",      1500),
    ("12행우",   "행성우주과학",     "진로선택", "고등학교",      1600),
    ("12과사",   "과학의 역사와 문화", "융합선택", "고등학교",    1700),
    ("12기환",   "기후변화와 환경생태", "융합선택", "고등학교",   1800),
    ("12융탐",   "융합과학 탐구",    "융합선택", "고등학교",      1900),
]

UNIT_HEAD_RE = re.compile(r"^\s*\(\d{1,2}\)\s*\S")


def subject_regions(lines):
    """과목별 구간 [시작줄, 끝줄) 을 잡는다.

    성취기준 코드가 처음 나오는 줄을 기준으로 하되, 그 앞의 단원 헤더
    '(1) 힘과 에너지' 를 놓치지 않도록 몇 줄 거슬러 올라간다.
    """
    starts = []
    for prefix, *_ in SUBJECTS:
        pat = re.compile(rf"^\s*\[{re.escape(prefix)}\d{{2}}-\d{{2}}\]")
        idx = next((i for i, l in enumerate(lines) if pat.match(l)), None)
        if idx is None:
            raise SystemExit(f"성취기준 코드를 찾지 못했습니다: {prefix}")
        for j in range(idx - 1, max(0, idx - 12), -1):   # 직전 단원 헤더까지 포함
            if UNIT_HEAD_RE.match(lines[j]):
                idx = j
                break
        starts.append(idx)

    if starts != sorted(starts):
        raise SystemExit("과목 순서가 PDF 수록 순서와 다릅니다. SUBJECTS 표를 확인하세요.")
    return [(starts[i], starts[i + 1] if i + 1 < len(starts) else len(lines))
            for i in range(len(starts))]


def main():
    ap = argparse.ArgumentParser(description="과학과 교육과정 PDF → 중·고 전 과목 seed JSON")
    ap.add_argument("pdf")
    ap.add_argument("--out", default="", help="저장 경로 (미지정 시 미리보기만)")
    args = ap.parse_args()

    lines = extract_text(args.pdf).split("\n")
    regions = subject_regions(lines)

    subjects, units, standards = [], [], []
    for (prefix, name, track, band, base), (a, b) in zip(SUBJECTS, regions):
        u, st = parse_standards("\n".join(lines[a:b]), prefix)
        subjects.append({"name": name, "track": track, "grade_band": band,
                         "code_prefix": prefix, "unit_base": base,
                         "unit_count": len(u), "standard_count": len(st)})
        for n in sorted(u):
            units.append({"unit_no": base + n, "subject": name, "local_no": n,
                          "name": u[n]["name"], "inquiry": u[n]["inquiry"],
                          "consider": u[n]["consider"]})
        for s in sorted(st, key=lambda x: (x["unit_no"], x["seq"])):
            standards.append({"code": s["code"], "subject": name, "grade_band": band,
                              "unit_no": base + s["unit_no"], "seq": s["seq"],
                              "text": s["text"], "explain": s.get("explain", "")})
        print(f"{track:6s} {name:16s} 단원 {len(u):2d} · 성취기준 {len(st):3d} · "
              f"해설 {sum(1 for x in st if x['explain']):3d}")

    print(f"\n합계: 과목 {len(subjects)} · 단원 {len(units)} · 성취기준 {len(standards)}")
    odd = [s for s in standards if not s["text"].rstrip().endswith(("다.", "다"))]
    if odd:
        print(f"※ 종결이 이상한 항목 {len(odd)}개 — 검수 필요")
        for s in odd[:10]:
            print(f"   {s['code']} {s['text'][:70]}")

    data = {
        "subject": "과학 (2022 개정)",
        "source": "교육부 고시 제2022-33호 [별책 9] 과학과 교육과정 — 중학교·고등학교",
        "extracted_at": date.today().isoformat(),
        "subjects": subjects,
        "units": units,
        "standards": standards,
    }
    if args.out:
        Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장: {args.out}  (자동 추출은 초안이다 — 눈으로 검수하라)")
    else:
        print("\n--out 을 주면 파일로 저장합니다.")


if __name__ == "__main__":
    main()
