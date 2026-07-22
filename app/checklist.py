"""문항·세트 자동 검토 체크리스트 (규칙 기반, AI 없음).

한글 없이 도는 순수 함수. 검토 단계 오류 불안 해소가 목적.
각 검사는 (level, code, message) 를 낸다. level: 'error' | 'warn'.
"""

VALID_QTYPES = ("정답형", "합답형")
VALID_DIFF = ("상", "중", "하")

# 발문이 부정형임을 알리는 말. '옳지 않은 것은?' 처럼 쓰인다.
NEGATIVE_WORDS = ("않은", "아닌", "틀린", "옳지 않", "적절하지 않")

# 정답 선지만 유독 길면 내용을 몰라도 답이 보인다. 평균 대비 이 배율을 넘으면 알린다.
LONG_ANSWER_RATIO = 1.4

# 한 번호에 정답이 이만큼 넘게 몰리면 알린다 (5문항 이상일 때만 본다).
ANSWER_BIAS_RATIO = 0.4


def check_question(q: dict, choices: list[dict]) -> list[dict]:
    """문항 1개 검사. choices 는 이 문항의 선지 목록."""
    issues = []

    def err(code, msg):
        issues.append({"level": "error", "code": code, "message": msg})

    def warn(code, msg):
        issues.append({"level": "warn", "code": code, "message": msg})

    # 발문
    if not (q.get("ask") or "").strip():
        err("no_ask", "발문(질문)이 비어 있습니다.")

    # 유형
    if q.get("qtype") not in VALID_QTYPES:
        err("bad_qtype", f"문항 유형이 올바르지 않습니다: {q.get('qtype')}")

    # 선지 수
    if len(choices) < 2:
        err("too_few_choices", f"선지가 {len(choices)}개뿐입니다. 최소 2개가 필요합니다.")

    # 정답 지정
    answers = [c for c in choices if c.get("is_answer")]
    if not answers:
        err("no_answer", "정답이 지정되지 않았습니다.")
    elif len(answers) > 1:
        warn("multi_answer", f"정답이 {len(answers)}개 지정됐습니다. 복수정답이 의도한 것인지 확인하세요.")

    # 근거 규칙: 모든 선지는 명제/변형/직접근거 중 하나를 가진다 (핵심 가치)
    for c in choices:
        has_evidence = (
            c.get("proposition_id") or c.get("variant_id")
            or (c.get("custom_evidence") or "").strip()
            or (c.get("combo") or "").strip()  # 합답형은 보기가 근거를 가짐
        )
        if not has_evidence:
            ord_ = c.get("ord", "?")
            err("choice_no_evidence", f"{ord_}번 선지에 근거가 없습니다. (명제·변형·직접 입력 중 하나 필요)")

    # 난이도·배점
    if q.get("difficulty") not in VALID_DIFF:
        warn("bad_difficulty", f"난이도가 올바르지 않습니다: {q.get('difficulty')}")
    if not q.get("default_points") or q["default_points"] <= 0:
        warn("bad_points", "배점이 0 이하입니다.")

    # 성취기준
    if not (q.get("standard_code") or "").strip():
        warn("no_standard", "성취기준이 연결되지 않았습니다.")

    issues += _check_negative_mark(q)
    issues += _check_answer_length(q, choices)
    return issues


# ===== 형식 너머의 검사 — 실제로 사고가 나는 지점 =====
def _check_negative_mark(q: dict) -> list[dict]:
    """부정 발문 표시와 실제 발문이 어긋나는지.

    '옳지 않은 것은?'인데 부정 표시가 없으면 출력물에서 강조(밑줄)가 빠지고,
    반대로 표시만 켜져 있고 발문이 긍정이면 학생이 헷갈린다.
    """
    ask = (q.get("ask") or "")
    looks_negative = any(w in ask for w in NEGATIVE_WORDS)
    marked = bool(q.get("is_negative"))
    if looks_negative and not marked:
        return [{"level": "warn", "code": "negative_unmarked",
                 "message": "발문이 부정형('옳지 않은' 등)인데 부정 문항 표시가 꺼져 있습니다."}]
    if marked and not looks_negative:
        return [{"level": "warn", "code": "negative_mismatch",
                 "message": "부정 문항으로 표시했는데 발문에 부정어가 보이지 않습니다."}]
    return []


def _check_answer_length(q: dict, choices: list[dict]) -> list[dict]:
    """정답 선지만 유독 긴 문항.

    합답형(ㄱㄴㄷ 조합)과 그림 선지는 길이가 의미 없으므로 건너뛴다.
    """
    if q.get("qtype") == "합답형" or q.get("image_choices"):
        return []
    texts = [(c, len((c.get("text") or "").strip())) for c in choices]
    texts = [(c, n) for c, n in texts if n]
    if len(texts) < 3:
        return []
    answers = [(c, n) for c, n in texts if c.get("is_answer")]
    if len(answers) != 1:
        return []
    ans_len = answers[0][1]
    others = [n for c, n in texts if not c.get("is_answer")]
    avg = sum(others) / len(others)
    if ans_len == max(n for _, n in texts) and avg and ans_len >= avg * LONG_ANSWER_RATIO:
        return [{"level": "warn", "code": "answer_longest",
                 "message": f"정답 선지가 가장 깁니다 ({ans_len}자 / 나머지 평균 {round(avg)}자). "
                            "길이만 보고 답을 고를 수 있습니다."}]
    return []


def _check_answer_spread(items: list[dict]) -> list[dict]:
    """세트 전체의 정답 번호 분포. 한 번호 쏠림·아예 안 쓰인 번호를 알린다."""
    if len(items) < 5:
        return []
    counts = {}
    total = 0
    max_ord = 0
    for it in items:
        chs = it["choices"]
        max_ord = max(max_ord, len(chs))
        ans = [c for c in chs if c.get("is_answer")]
        if len(ans) != 1:
            continue
        o = ans[0].get("ord")
        counts[o] = counts.get(o, 0) + 1
        total += 1
    if not total or max_ord < 2:
        return []

    out = []
    label = "①②③④⑤"
    top_ord, top_n = max(counts.items(), key=lambda kv: kv[1])
    if top_n / total > ANSWER_BIAS_RATIO:
        mark = label[top_ord - 1] if 1 <= top_ord <= 5 else str(top_ord)
        out.append({"level": "warn", "code": "answer_bias",
                    "message": f"정답이 {mark}번에 {top_n}/{total}문항 몰려 있습니다."})
    missing = [label[i - 1] if i <= 5 else str(i)
               for i in range(1, max_ord + 1) if not counts.get(i)]
    if missing:
        out.append({"level": "warn", "code": "answer_unused",
                    "message": f"정답으로 한 번도 쓰이지 않은 번호가 있습니다: {' '.join(missing)}"})
    return out


def _check_duplicate_props(items: list[dict]) -> list[dict]:
    """같은 명제가 여러 문항에 겹쳐 쓰였는지 — 한 내용을 두 번 묻게 된다."""
    where = {}
    for idx, it in enumerate(items, start=1):
        seen = set()
        for c in it["choices"]:
            pid = c.get("proposition_id")
            if pid and pid not in seen:
                seen.add(pid)
                where.setdefault(pid, []).append(idx)
        for b in _bogi_of(it["question"]):
            pid = b.get("proposition_id")
            if pid and pid not in seen:
                seen.add(pid)
                where.setdefault(pid, []).append(idx)
    dups = {pid: nums for pid, nums in where.items() if len(nums) > 1}
    if not dups:
        return []
    detail = ", ".join(f"{'·'.join(map(str, nums))}번 문항"
                       for nums in list(dups.values())[:4])
    return [{"level": "warn", "code": "duplicate_proposition",
             "message": f"같은 명제를 여러 문항에서 씁니다 ({detail}). 내용이 겹치지 않는지 확인하세요."}]


def _bogi_of(q: dict) -> list[dict]:
    """question.bogi_items 는 JSON 문자열일 수도, 이미 파싱된 목록일 수도 있다."""
    raw = q.get("bogi_items") or []
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw or "[]")
        except ValueError:
            return []
    return [b for b in raw if isinstance(b, dict)]


def check_set(set_row: dict, items: list[dict], target_total=None) -> list[dict]:
    """세트 전체 검사.

    items: [{question, choices, points}] — 세트 구성 + 각 문항 데이터.
    target_total 을 주지 않으면 세트에 저장된 만점(exam_set.total_points)을 쓴다.
    """
    if target_total is None:
        target_total = set_row.get("total_points") or 100.0
    issues = []

    def err(code, msg):
        issues.append({"level": "error", "code": code, "message": msg})

    def warn(code, msg):
        issues.append({"level": "warn", "code": code, "message": msg})

    if not items:
        err("empty_set", "세트에 문항이 없습니다.")
        return issues

    # 배점 합
    total = sum((it.get("points") or it["question"].get("default_points") or 0) for it in items)
    if abs(total - target_total) > 0.01:
        warn("points_mismatch", f"배점 합이 {round(total, 1)}점입니다 (목표 {target_total}점).")

    # 각 문항 검사 집계
    for idx, it in enumerate(items, start=1):
        q_issues = check_question(it["question"], it["choices"])
        for qi in q_issues:
            if qi["level"] == "error":
                err(qi["code"], f"{idx}번 문항: {qi['message']}")

    # 성취기준 커버리지 (참고용)
    covered = {it["question"].get("standard_code") for it in items if it["question"].get("standard_code")}
    if len(covered) < len(items) / 2:
        warn("low_coverage", f"성취기준 {len(covered)}종만 다룹니다. 편중되지 않았는지 확인하세요.")

    # 세트로 묶여야만 보이는 것들 — 정답 번호 쏠림, 내용 중복
    issues += _check_answer_spread(items)
    issues += _check_duplicate_props(items)
    return issues


def summarize(issues: list[dict]) -> dict:
    errors = [i for i in issues if i["level"] == "error"]
    warns = [i for i in issues if i["level"] == "warn"]
    return {"ok": not errors, "error_count": len(errors), "warn_count": len(warns), "issues": issues}
