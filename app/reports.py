"""제출 서류 — 정답표 · 이원목적분류표.

문항을 다 만들고 나면 손으로 다시 만들던 두 장이다. 성취기준·난이도·배점·정답이
이미 DB 에 있으므로 조회해서 표로 세우기만 하면 된다.

한글 없이 도는 순수 함수 — 세트 데이터(dict)를 받아 행 목록을 낸다.
붙여넣기는 TSV 로 준다. 한글 표에 붙이면 칸이 그대로 맞고, 엑셀도 같은 형식을 읽는다.
"""

CIRCLED = "①②③④⑤"

ANSWER_KEY_COLS = ["문항", "정답", "배점", "유형", "난이도", "성취기준"]

BLUEPRINT_COLS = ["문항", "단원", "성취기준", "평가 내용(출제 의도)",
                  "행동영역", "난이도", "배점", "정답", "유형"]

# 2022 개정 교육과정의 세 범주. 문항에 저장된 값이 없으면 빈칸으로 두고 교사가 채운다.
BEHAVIORS = ["지식·이해", "과정·기능", "가치·태도"]
ORIGINS = ["직접", "AI초안", "기출변형"]


def _answer_of(question: dict, choices: list[dict]) -> str:
    """정답 표기. 저장된 answer 문자열이 있으면 그대로, 없으면 선지에서 찾는다."""
    saved = (question.get("answer") or "").strip()
    if saved:
        return saved
    marked = [c for c in choices if c.get("is_answer")]
    if not marked:
        return ""
    nums = sorted(c.get("ord") or 0 for c in marked)
    return " ".join(CIRCLED[n - 1] if 1 <= n <= 5 else str(n) for n in nums)


def _points_of(item: dict) -> float:
    p = item.get("points")
    if p is None:
        p = item["question"].get("default_points") or 0
    return round(float(p), 1)


def _num(p: float) -> str:
    """3.0 은 '3' 으로, 2.5 는 '2.5' 로 — 표에 소수점이 지저분하게 깔리지 않게."""
    return str(int(p)) if float(p).is_integer() else str(p)


def answer_key(items: list[dict]) -> dict:
    """정답표. 채점·이의신청 대응 때 가장 먼저 펴는 표."""
    rows = []
    for i, it in enumerate(items, start=1):
        q = it["question"]
        rows.append([
            str(i),
            _answer_of(q, it["choices"]),
            _num(_points_of(it)),
            q.get("qtype") or "",
            q.get("difficulty") or "",
            q.get("standard_code") or "",
        ])
    return {"columns": ANSWER_KEY_COLS, "rows": rows,
            "total_points": _num(sum(_points_of(it) for it in items))}


def blueprint(items: list[dict], unit_of_code: dict | None = None,
              standard_texts: dict | None = None) -> dict:
    """이원목적분류표.

    unit_of_code: {성취기준코드: 단원명}, standard_texts: {성취기준코드: 전문}
    둘 다 없으면 코드만 적힌 표가 나온다(값이 없다고 표가 깨지지는 않는다).
    """
    unit_of_code = unit_of_code or {}
    standard_texts = standard_texts or {}
    rows = []
    for i, it in enumerate(items, start=1):
        q = it["question"]
        code = q.get("standard_code") or ""
        unit = unit_of_code.get(code, "")
        rows.append([
            str(i),
            unit,
            f"{code} {standard_texts.get(code, '')}".strip(),
            (q.get("intent") or "").strip(),
            q.get("behavior") or "",
            q.get("difficulty") or "",
            _num(_points_of(it)),
            _answer_of(q, it["choices"]),
            q.get("qtype") or "",
        ])
    return {"columns": BLUEPRINT_COLS, "rows": rows,
            "summary": _summary(items)}


def _summary(items: list[dict]) -> dict:
    """표 아래 붙는 집계 — 난이도·행동영역·출처 분포와 배점 합.

    출처(origin)는 내부 관리용이다. 이원목적분류표는 학교에 내는 공식 서식이므로
    대외 제출본에 넣을지는 학교 방침을 확인하고 정한다 — 기본은 화면에서만 본다.
    """
    diff = {"상": 0, "중": 0, "하": 0}
    beh = {b: 0 for b in BEHAVIORS}
    beh["미지정"] = 0
    org = {o: 0 for o in ORIGINS}
    org["미지정"] = 0
    for it in items:
        q = it["question"]
        d = q.get("difficulty") or "중"
        diff[d] = diff.get(d, 0) + 1
        b = q.get("behavior") or ""
        beh[b if b in BEHAVIORS else "미지정"] += 1
        o = (q.get("origin") or "").strip()
        org[o if o in ORIGINS else "미지정"] += 1
    return {"count": len(items), "difficulty": diff, "behavior": beh, "origin": org,
            "total_points": _num(sum(_points_of(it) for it in items))}


def to_tsv(table: dict) -> str:
    """표 → 탭 구분 텍스트. 한글 표·엑셀에 그대로 붙는다."""
    lines = ["\t".join(table["columns"])]
    lines += ["\t".join(cell.replace("\t", " ").replace("\n", " ") for cell in row)
              for row in table["rows"]]
    return "\n".join(lines)
