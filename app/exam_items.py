"""기출 시험지에서 문항을 하나씩 잘라낸다.

수능·모의고사 시험지는 편집이 규칙적이다(2026-07-22 실측, 물리학Ⅰ 2024):
  · 2단 편집 — 좌측 x≈88~400, 우측 x≈427~743
  · 문항 번호(`14.`)가 각 단의 맨 왼쪽 같은 x 에 정렬된다
그래서 번호 토큰의 좌표만 찾으면 "이 문항은 여기서 여기까지"를 계산할 수 있다.

문항을 못 찾으면(형식이 다른 옛 시험지 등) 빈 목록을 돌려주고,
호출한 쪽이 페이지 전체를 쓰도록 한다 — 조용히 잘못 자르지 않는다.
"""
import re

NUM_RE = re.compile(r"^(\d{1,2})\.$")
MARGIN = 8          # 크롭할 때 둘레 여백
MIN_ITEM_H = 60     # 이보다 짧으면 문항으로 보지 않는다(머리말 숫자 등)


COL_TOL = 30        # 문항 번호 x 가 이만큼 안에 있으면 같은 단
BOTTOM_PAD = 30     # 쪽번호 영역은 뺀다


def _column_starts(marks: list[dict]) -> list[float]:
    """문항 번호의 x 를 묶어 각 단의 왼쪽 시작점을 구한다.

    번호는 항상 단 맨 왼쪽에 정렬되므로, 본문 단어의 폭(표·그림이 옆 단까지 걸치는 경우)에
    영향을 받지 않는다. 2010년 시험지에서 좌측 그림이 우측 단까지 뻗어 크롭이 밀렸던 문제를
    이 방식으로 없앴다.
    """
    xs = sorted(m["x"] for m in marks)
    starts = []
    for x in xs:
        if not starts or x - starts[-1][-1] > COL_TOL:
            starts.append([x])
        else:
            starts[-1].append(x)
    return [min(g) for g in starts]


def detect_items(page) -> list[dict]:
    """페이지에서 문항 영역을 찾는다.

    반환: [{"num": 14, "col": 0..n, "x0","y0","x1","y1"}] — PDF 좌표계
    """
    words = page.get_text("words")   # (x0,y0,x1,y1,word,block,line,word_no)
    if not words:
        return []

    W, H = page.rect.width, page.rect.height

    raw = []
    for w in words:
        m = NUM_RE.match(w[4])
        if m:
            raw.append({"num": int(m.group(1)), "x": w[0], "y": w[1]})
    if not raw:
        return []

    starts = _column_starts(raw)
    if not starts:
        return []

    def col_of(x):
        for i in range(len(starts) - 1, -1, -1):
            if x >= starts[i] - COL_TOL / 2:
                return i
        return 0

    # 단 왼쪽에 정렬된 번호만 문항으로 인정 (본문 속 '1.' 같은 것은 제외)
    marks = []
    for r in raw:
        c = col_of(r["x"])
        if abs(r["x"] - starts[c]) <= COL_TOL / 2:
            marks.append({**r, "col": c})
    if not marks:
        return []

    items = []
    for c, sx in enumerate(starts):
        right = (starts[c + 1] - MARGIN) if c + 1 < len(starts) else (W - MARGIN)
        col_marks = sorted([m for m in marks if m["col"] == c], key=lambda m: m["y"])
        for i, mk in enumerate(col_marks):
            y0 = mk["y"] - MARGIN
            y1 = (col_marks[i + 1]["y"] - MARGIN) if i + 1 < len(col_marks) else (H - BOTTOM_PAD)
            if y1 - y0 < MIN_ITEM_H:
                continue
            items.append({
                "num": mk["num"], "col": c,
                "x0": max(0, sx - MARGIN), "y0": max(0, y0),
                "x1": min(W, right), "y1": min(H, y1),
            })
    items.sort(key=lambda it: (it["col"], it["y0"]))
    return items


def item_at(items: list[dict], x: float, y: float) -> dict | None:
    """좌표가 어느 문항 안에 있는지 (검색어 위치 → 문항 번호)."""
    for it in items:
        if it["x0"] <= x <= it["x1"] and it["y0"] <= y <= it["y1"]:
            return it
    # 같은 단에서 y 만 맞는 것 (가장 가까운 위쪽 문항)
    cand = [it for it in items if it["y0"] <= y <= it["y1"]]
    return cand[0] if cand else None


def summarize(items: list[dict]) -> str:
    """'7~10번' 같은 요약."""
    if not items:
        return ""
    nums = sorted({it["num"] for it in items})
    if len(nums) == 1:
        return f"{nums[0]}번"
    return f"{nums[0]}~{nums[-1]}번"
