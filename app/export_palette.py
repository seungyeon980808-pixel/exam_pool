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

# 등록된 템플릿과 빈칸 순서.
#   slots 는 채울 값의 종류를 순서대로 적은 것이다.
#   photo1/photo2 = 사진 칸, bogi = ㄱㄴㄷ, points = 점수, choice = 선지
#
# **빈칸 순서는 추측하지 말고 31_hwp_palette/fragments/*.hwp 를 직접 열어 확인한다.**
# hwppalette 는 조각 파일 안의 역슬래시를 문서 순서대로 세어 채우므로, 표 안의
# 셀 순서가 곧 빈칸 순서다. 아래는 2026-07-27 에 조각 파일을 열어 확인한 것이다.
#
#   합답형* 세 템플릿은 모두 본문에 아래 줄이 박혀 있고, 여기의 (\점) 이
#   〈보 기〉 상자보다 **앞**에 온다:
#       이에 대한 설명으로 옳은 것을 <보기>에서 모두 고른 것은? (\점)
#   따라서 points 는 bogi 보다 먼저다. (2026-07-22 메모는 순서가 반대로 적혀 있었다)
TEMPLATES = {
    # 54c6380dd2d545efb68214a5bfa5a5c7.hwp
    #   ask_builtin: 조각 본문에 "이에 대한 설명으로 옳은 것을 <보기>에서 모두
    #   고른 것은?" 이 이미 박혀 있다 → 발문을 지문 칸에 또 넣으면 두 번 나온다.
    "합답형1사진3선지": {
        "slot_count": 12, "ask_builtin": True,
        "slots": ["num", "passage", "photo1", "points",
                  "bogi1", "bogi2", "bogi3", "c1", "c2", "c3", "c4", "c5"],
    },
    # 1d64d6825a3742f7ac62946b980d9205.hwp
    "합답형2사진3선지": {
        "slot_count": 13, "ask_builtin": True,
        "slots": ["num", "passage", "photo1", "photo2", "points",
                  "bogi1", "bogi2", "bogi3", "c1", "c2", "c3", "c4", "c5"],
    },
    # 7e0d6662e63340089369f353124761bd.hwp
    "합답형실험3선지": {
        "slot_count": 11, "ask_builtin": True,
        "slots": ["num", "passage", "points",
                  "bogi1", "bogi2", "bogi3", "c1", "c2", "c3", "c4", "c5"],
    },
    # 학교 지필용 정답형(5지선다). 사진 칸이 없고 지문 칸도 따로 없다 —
    # 지문이 있으면 발문 칸에 { } 블록으로 함께 넣는다.
    #   fragments/f7fbdd7ad2684f14b3e4a4618fdc4547.hwp 를 열어 확인 (2026-07-27)
    #   \.  /  \ (\점)  /  ① \  ② \  ③ \  ④ \  ⑤ \
    "학교정답0사진1선지": {
        "slot_count": 8,
        "slots": ["num", "ask", "points", "c1", "c2", "c3", "c4", "c5"],
    },
    # ── 학교 지필 양식 (조각에 발문이 박혀 있지 않아 부정발문을 쓸 수 있고,
    #    선지가 5칸 가로 배치다). 칸 순서는 2026-08-03 에 S1..Sn 을 채워 PDF 로
    #    떠서 눈으로 확인했다(tools/slot_dump 방식). ──
    #   \.  \        ← 번호, 지문
    #   ┌ 자료 상자 ┐ ← 사진
    #   \            ← 발문 (점수는 발문 문장에 붙인다)
    #   〈보 기〉 ㄱㄴㄷ / ① ~ ⑤
    "학교합답1사진5선지": {
        "slot_count": 12,
        "slots": ["num", "passage", "photo1", "ask_points",
                  "bogi1", "bogi2", "bogi3", "c1", "c2", "c3", "c4", "c5"],
    },
    #   \.  \ / 그림 \ , \ + (가)(나) 캡션 내장 / \ (\점) / 보기 / 선지
    "학교합답2사진5선지": {
        "slot_count": 14,
        "slots": ["num", "passage", "photo1", "photo2", "ask", "points",
                  "bogi1", "bogi2", "bogi3", "c1", "c2", "c3", "c4", "c5"],
    },
    # ── 2026-08-03 tools/make_exam_fragments.py 로 생성·등록한 3종 ──
    # 순서는 등록 직후 insert_template_filled 에 S1..Sn 을 채워 실측 확인했다.
    # 실험 문구 없는 합답형 — [실험 과정]/[실험 결과] 가 안 박혀 있다 (갭 7 해소)
    "학교합답0사진5선지": {
        "slot_count": 11,
        "slots": ["num", "passage", "points",
                  "bogi1", "bogi2", "bogi3", "c1", "c2", "c3", "c4", "c5"],
    },
    # 정답형 + 사진 1장 (사진 칸이 발문과 선지 사이 가운데 정렬)
    "정답형1사진": {
        "slot_count": 9,
        "slots": ["num", "ask", "points", "photo1", "c1", "c2", "c3", "c4", "c5"],
    },
    # 서술형 — 번호·발문·점수 한 줄 + 답란 상자(45mm). 지문·사진은 발문 블록에 함께.
    "서술형": {
        "slot_count": 3,
        "slots": ["num", "ask", "points"],
    },
}

# 문항 → 템플릿 고르기. (유형, 사진 개수) 로 찾는다.
# 값이 빈 문자열이면 hwppalette 에 아직 등록되지 않은 조합이라 평문으로 떨어진다.
TEMPLATE_FOR = {
    # 합답형은 전부 '학교합답*' 계열을 쓴다. 옛 '합답형*3선지' 는 조각에 발문이
    # 박혀 있어 부정발문을 표현할 수 없다(지문 칸에 또 넣으면 발문이 두 번 찍히고,
    # 안 넣으면 '옳지 않은'이 통째로 사라진다) — 2026-08-03 실측.
    ("합답형", 0): "학교합답0사진5선지",
    ("합답형", 1): "학교합답1사진5선지",
    ("합답형", 2): "학교합답2사진5선지",
    ("정답형", 0): "학교정답0사진1선지",
    ("정답형", 1): "정답형1사진",
    # 사진 2장 정답형 템플릿은 없다 — 0사진 템플릿을 쓰고 사진은 발문 블록 안에
    # \파일이름\ 으로 넣는다(사진 라벨은 슬롯을 끊지 않는다).
    ("정답형", 2): "학교정답0사진1선지",
    ("서술형", 0): "서술형",
    ("서술형", 1): "서술형",
    ("서술형", 2): "서술형",
}

# hwppalette 는 줄 첫머리의 이 낱말들을 '시험문제 문법'으로 먼저 알아채고
# \라벨\ 템플릿 경로를 건너뛴다. 지문에 우연히 들어가면 출력이 통째로 깨지므로
# 콜론 앞에 공백을 넣어 무력화한다 (parser.parse 의 키 목록과 같다).
LEGACY_KEYS = ("번호:", "발문:", "문:", "자료:", "사진자료:", "실험자료:",
               "질문:", "보기:", "선지:", "선지1:", "선지3:", "선지5:")


def _photos(material: str) -> list[str]:
    """자료 칸에서 사진 파일명을 뽑는다. 쉼표로 2개까지."""
    m = (material or "").strip()
    if not m:
        return []
    parts = [p.strip() for p in m.split(",") if p.strip()]
    return [p for p in parts if _looks_like_filename(p)][:2]


def _looks_like_filename(s: str) -> bool:
    s = s.strip()
    if not s or "\n" in s or len(s) > 60:
        return False
    return "." in s or s.replace("_", "").replace("-", "").replace(" ", "").isalnum()


def _negatize(ask: str, is_negative: bool) -> str:
    """부정 발문이면 '옳지 않은'을 굵게 강조한다(hwppalette 서식 문법)."""
    if not is_negative:
        return ask
    for kw in ("옳지 않은", "옳지 않는", "적절하지 않은", "틀린"):
        if kw in ask:
            return ask.replace(kw, "\\굵게{" + kw + "}", 1)
    return ask


def _guard(text: str) -> str:
    """줄 첫머리가 hwppalette 의 '시험문제 문법' 키워드면 콜론 앞에 공백을 넣어 무력화한다.

    이게 없으면 지문에 '자료:' 한 줄만 있어도 파서가 템플릿 경로 대신
    레거시 경로로 빠져 시험지 전체가 엉뚱하게 조판된다.
    """
    out = []
    for line in (text or "").split("\n"):
        s = line.lstrip()
        for k in LEGACY_KEYS:
            if s.startswith(k):
                line = line.replace(k, k[:-1] + " :", 1)
                break
        out.append(line)
    return "\n".join(out)


def _esc(text: str) -> str:
    """원문에 들어 있는 '}' 를 escape 한다.

    **반드시 _negatize 보다 먼저** 부를 것. 순서가 바뀌면 `\\굵게{...}` 의 닫는
    괄호까지 escape 되어 굵게 서식이 통째로 깨진다.
    """
    return (text or "").replace("}", "\\}")


def _block(text: str) -> str:
    """여러 줄이면 { } 블록으로 감싼다 — hwppalette 는 한 칸에 한 줄이 원칙이라
    지문처럼 줄이 여럿인 값은 블록으로 묶어야 한 칸에 들어간다."""
    t = _guard((text or "").strip())
    if not t:
        return SKIP
    if "\n" not in t:
        return t
    return "{" + t + "}"


def _bogi_list(q: dict) -> list:
    b = q.get("bogi_items")
    if isinstance(b, str):
        b = json.loads(b or "[]")
    return b or []


def _bogi_text(b) -> str:
    return (b.get("text") if isinstance(b, dict) else str(b)) or ""


def _ask_cell(q: dict, photos: list[str], with_bogi: bool,
              with_passage: bool = True, suffix: str = "") -> str:
    """발문 칸 하나에 들어갈 내용. 템플릿에 없는 요소만 여기에 합친다.

    학교 정답형 템플릿에는 지문·사진 칸이 따로 없어서 묶어 넣지만, 지문 칸이
    따로 있는 템플릿에서는 지문을 빼야 한다 — 안 그러면 지문이 두 번 나온다.
    """
    parts = []
    if with_passage and q.get("passage", "").strip():
        parts.append(_esc(q["passage"].strip()))
    for p in photos:                    # 사진 라벨은 블록 안에서도 그림으로 들어간다
        parts.append("\\%s\\" % p)
    parts.append(_negatize(_esc(q.get("ask", "").strip()), q.get("is_negative")) + suffix)

    if with_bogi:
        bogi = _bogi_list(q)
        if bogi:
            parts.append("〈보 기〉")
            labels = ["ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ"]
            for i, b in enumerate(bogi):
                lab = (b.get("label") if isinstance(b, dict) else "") or \
                      (labels[i] if i < len(labels) else str(i + 1))
                parts.append(f"{lab}. {_esc(_bogi_text(b))}")

    return _block("\n".join([p for p in parts if p]))


def pick_template(q: dict) -> str:
    """이 문항에 쓸 템플릿 라벨. 없으면 빈 문자열."""
    photos = _photos(q.get("material", ""))
    return TEMPLATE_FOR.get((q.get("qtype", "합답형"), len(photos)), "")


def question_to_palette(q: dict, choices: list[dict], num=1) -> str:
    """문항 1개 → hwppalette 템플릿 호출 + 빈칸 값들."""
    label = pick_template(q)
    if q.get("qtype") == "서술형" and not label:
        return _essay_to_palette(q, num)   # 템플릿 미등록 환경 폴백
    if not label:
        return _fallback_text(q, choices, num)

    spec = TEMPLATES[label]
    photos = _photos(q.get("material", ""))
    bogi = _bogi_list(q)
    ordered = sorted(choices, key=lambda c: c.get("ord", 0))
    # 이 템플릿에 보기 칸이 따로 있는지 — 없으면 발문 블록 안에 〈보기〉를 넣는다.
    has_bogi_slot = any(s.startswith("bogi") for s in spec["slots"])

    def value(slot: str) -> str:
        if slot == "num":
            return str(num)
        if slot in ("ask", "ask_points"):
            # 템플릿이 안 주는 것만 이 칸에 묶는다. ask_points 는 점수 칸이 따로
            # 없는 템플릿용 — 발문 문장 끝에 "(N점)"을 붙인다.
            pts = _points_text(q)
            suffix = (" (%s점)" % pts) if (slot == "ask_points" and pts != SKIP) else ""
            return _ask_cell(q, photos if not _has_photo_slot(spec) else [],
                             with_bogi=not has_bogi_slot,
                             with_passage="passage" not in spec["slots"],
                             suffix=suffix)
        if slot == "passage":
            # 지문과 발문을 한 칸에 넣는다 (템플릿은 '\. \' 한 칸만 준다).
            # 지문이 여러 줄이면 { } 블록으로 묶어야 한다 — 안 그러면 줄 수만큼
            # 빈칸을 잡아먹어 뒤의 점수·보기·선지가 통째로 한 칸씩 밀린다.
            #
            # 단, 발문이 따로 갈 곳이 있으면(발문 칸이 있거나 조각에 이미 박혀
            # 있으면) 지문만 넣는다 — 안 그러면 같은 발문이 두 번 찍힌다
            # (2026-08-03 실측). 지문이 없으면 그 칸이 비므로 발문이라도 넣는다.
            psg = _esc(q.get("passage", "").strip())
            ak = _negatize(_esc(q.get("ask", "").strip()), q.get("is_negative"))
            if any(s in ("ask", "ask_points") for s in spec["slots"]):
                ak = ""                 # 발문은 제 칸으로 간다
            elif spec.get("ask_builtin"):
                ak = "" if psg else ak  # 조각에 박혀 있다 — 지문이 없을 때만 채운다
            parts = [p for p in (psg, ak) if p]
            if not parts:
                return SKIP
            if "\n" in psg:
                return _block("\n".join(parts))
            return _guard(" ".join(parts)) or SKIP
        # 사진 칸도 \파일이름\ 으로 감싼다. hwppalette 는 `\라벨\` 토큰만 그림으로
        # 바꾸므로(parser._slot_value → build_segments), 맨 파일명을 넣으면 글자
        # "EM26_01" 이 그대로 시험지에 박힌다 — 2026-08-03 실측으로 확인.
        if slot == "photo1":
            return "\\%s\\" % photos[0] if len(photos) >= 1 else SKIP
        if slot == "photo2":
            return "\\%s\\" % photos[1] if len(photos) >= 2 else SKIP
        if slot.startswith("bogi"):
            i = int(slot[-1]) - 1
            return _guard(_esc(_bogi_text(bogi[i]))) or SKIP if i < len(bogi) else SKIP
        if slot == "points":
            return _points_text(q)
        if slot.startswith("c"):
            i = int(slot[1:]) - 1
            return _choice_text(ordered[i]) if i < len(ordered) else SKIP
        return SKIP

    lines = ["\\%s\\" % label]
    lines += [value(s) for s in spec["slots"]]
    return "\n".join(lines)


def _has_photo_slot(spec: dict) -> bool:
    return any(s.startswith("photo") for s in spec["slots"])


def _points_text(q: dict) -> str:
    pt = q.get("default_points")
    if not pt:
        return SKIP
    return str(int(pt)) if float(pt).is_integer() else str(pt)


def _essay_to_palette(q: dict, num: int) -> str:
    """서술형 — hwppalette 에 서술형 템플릿이 없어서 평문 + 답란 표로 만든다.

    `\\표1*1\\` 다음 줄의 `-` 는 빈 칸 하나짜리 표가 되어 답란 역할을 한다.
    (템플릿을 등록하면 TEMPLATE_FOR 의 ("서술형", 0) 에 라벨만 적으면 된다.)
    """
    photos = _photos(q.get("material", ""))
    head = [f"{num}. " + _negatize(_esc(q.get("ask", "").strip()), q.get("is_negative")) +
            (f" ({_points_text(q)}점)" if q.get("default_points") else "")]

    body = []
    if q.get("passage", "").strip():
        body.append(_esc(q["passage"].strip()))
    for p in photos:
        body.append("\\%s\\" % p)

    lines = []
    if body:
        lines.append(_guard("\n".join(body)))
    lines += [_guard(head[0]), "\\표1*1\\", SKIP]
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
