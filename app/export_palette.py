"""문항·세트 → hwppalette 출력 변환기.

**hwppalette 는 등록해 둔 템플릿의 빈칸을 순서대로 채우는 방식이다.**
`\\라벨\\` 을 한 줄에 쓰고, 다음 줄부터 빈칸 개수만큼 값을 차례로 놓는다.
비울 칸에는 `-` 한 줄을 둔다(parser.SKIP_MARK).

    \\합답형1사진3선지\\
    1                    ← 번호
    빛의 굴절에 대한 설명이다   ← 발문(지문)
    굴절_그림01            ← 사진
    ㄱ 내용 / ㄴ 내용 / ㄷ 내용
    3                    ← 점수 (선지 바로 위)
    ㄱ / ㄴ / ㄱ,ㄷ / ㄴ,ㄷ / ㄱㄴㄷ   ← 선지 5개

한글 없이 도는 순수 함수 (tests/ 에서 단독 검증).
"""
import json

SKIP = "-"          # 이 빈칸은 비운다 (hwppalette parser.SKIP_MARK)

# 등록된 템플릿과 빈칸 순서 (2026-07-22 사용자 확인)
#   slots 는 채울 값의 종류를 순서대로 적은 것이다.
#   photo1/photo2 = 사진 칸, bogi = ㄱㄴㄷ, points = 점수, choice = 선지
TEMPLATES = {
    "합답형1사진3선지": {
        "slot_count": 12,
        "slots": ["num", "passage", "photo1", "bogi1", "bogi2", "bogi3",
                  "points", "c1", "c2", "c3", "c4", "c5"],
    },
    "합답형2사진3선지": {
        "slot_count": 13,
        "slots": ["num", "passage", "photo1", "photo2", "bogi1", "bogi2", "bogi3",
                  "points", "c1", "c2", "c3", "c4", "c5"],
    },
    "합답형실험3선지": {
        "slot_count": 11,
        "slots": ["num", "passage", "bogi1", "bogi2", "bogi3",
                  "points", "c1", "c2", "c3", "c4", "c5"],
    },
}

# 문항 → 템플릿 고르기. 정답형 템플릿은 아직 등록 전이라 비어 있다
# (hwppalette 에 만들고 여기 라벨을 적으면 그대로 출력된다).
TEMPLATE_FOR = {
    ("합답형", 1): "합답형1사진3선지",
    ("합답형", 2): "합답형2사진3선지",
    ("합답형", 0): "합답형실험3선지",
    ("정답형", 0): "",      # 미등록
    ("정답형", 1): "",
    ("정답형", 2): "",
}


def _photos(material: str) -> list[str]:
    """자료 칸에서 사진 파일명을 뽑는다. 쉼표로 2개까지."""
    m = (material or "").strip()
    if not m:
        return []
    parts = [p.strip() for p in m.split(",") if p.strip()]
    return [p for p in parts if _looks_like_filename(p)][:2]


def _looks_like_filename(s: str) -> bool:
    s = s.strip()
    if not s or "\n" in s or len(s) > 60 or " " in s:
        return False
    return "." in s or s.replace("_", "").replace("-", "").isalnum()


def _negatize(ask: str, is_negative: bool) -> str:
    """부정 발문이면 '옳지 않은'을 굵게 강조한다(hwppalette 서식 문법)."""
    if not is_negative:
        return ask
    for kw in ("옳지 않은", "옳지 않는", "적절하지 않은", "틀린"):
        if kw in ask:
            return ask.replace(kw, "\\굵게{" + kw + "}", 1)
    return ask


def pick_template(q: dict) -> str:
    """이 문항에 쓸 템플릿 라벨. 없으면 빈 문자열."""
    photos = _photos(q.get("material", ""))
    return TEMPLATE_FOR.get((q.get("qtype", "합답형"), len(photos)), "")


def question_to_palette(q: dict, choices: list[dict], num=1) -> str:
    """문항 1개 → hwppalette 템플릿 호출 + 빈칸 값들."""
    label = pick_template(q)
    if not label:
        return _fallback_text(q, choices, num)

    spec = TEMPLATES[label]
    photos = _photos(q.get("material", ""))
    bogi = q.get("bogi_items")
    if isinstance(bogi, str):
        bogi = json.loads(bogi or "[]")
    bogi = bogi or []
    ordered = sorted(choices, key=lambda c: c.get("ord", 0))

    def value(slot: str) -> str:
        if slot == "num":
            return str(num)
        if slot == "passage":
            # 지문과 발문을 한 칸에 넣는다 (템플릿은 '\. \' 한 칸만 준다)
            parts = [p for p in (q.get("passage", "").strip(),
                                 _negatize(q.get("ask", "").strip(), q.get("is_negative"))) if p]
            return " ".join(parts) or SKIP
        if slot == "photo1":
            return photos[0] if len(photos) >= 1 else SKIP
        if slot == "photo2":
            return photos[1] if len(photos) >= 2 else SKIP
        if slot.startswith("bogi"):
            i = int(slot[-1]) - 1
            if i < len(bogi):
                b = bogi[i]
                return (b.get("text") if isinstance(b, dict) else str(b)) or SKIP
            return SKIP
        if slot == "points":
            pt = q.get("default_points")
            return str(int(pt)) if pt and float(pt).is_integer() else (str(pt) if pt else SKIP)
        if slot.startswith("c"):
            i = int(slot[1:]) - 1
            return _choice_text(ordered[i]) if i < len(ordered) else SKIP
        return SKIP

    lines = ["\\%s\\" % label]
    lines += [value(s) for s in spec["slots"]]
    return "\n".join(lines)


def _fallback_text(q: dict, choices: list[dict], num: int) -> str:
    """템플릿이 없는 유형(예: 정답형 미등록) — 사람이 읽고 손으로 넣을 수 있게 평문으로."""
    bogi = q.get("bogi_items")
    if isinstance(bogi, str):
        bogi = json.loads(bogi or "[]")
    out = [f"[{num}번 · {q.get('qtype', '')} — hwppalette 템플릿 미등록]"]
    if q.get("passage"):
        out.append(q["passage"])
    if q.get("material"):
        out.append(f"(자료: {q['material']})")
    out.append(_negatize(q.get("ask", ""), q.get("is_negative")))
    for b in (bogi or []):
        out.append(f"  {b.get('label', '')}. {b.get('text', '')}" if isinstance(b, dict) else f"  {b}")
    for i, c in enumerate(sorted(choices, key=lambda x: x.get("ord", 0))):
        out.append(f"  {'①②③④⑤'[i] if i < 5 else i + 1} {_choice_text(c)}")
    out.append(f"  (배점 {q.get('default_points', '')}점)")
    return "\n".join(out)


def _choice_text(c: dict) -> str:
    """정답형은 text, 합답형은 combo(['ㄱ','ㄷ']) → 'ㄱ, ㄷ'."""
    combo = c.get("combo")
    if combo:
        if isinstance(combo, str):
            try:
                combo = json.loads(combo)
            except (ValueError, TypeError):
                combo = [x.strip() for x in combo.split(",") if x.strip()]
        if combo:
            return ", ".join(combo)
    return c.get("text", "") or SKIP


def set_to_markdown(questions: list[tuple[dict, list[dict]]]) -> str:
    """세트 전체 → hwppalette 입력. 문항 사이는 빈 줄로 구분한다."""
    blocks = []
    for i, (q, choices) in enumerate(questions, start=1):
        blocks.append(question_to_palette(q, choices, num=i))
    return "\n\n".join(blocks) + "\n"
