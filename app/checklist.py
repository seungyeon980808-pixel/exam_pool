"""문항·세트 자동 검토 체크리스트 (규칙 기반, AI 없음).

한글 없이 도는 순수 함수. 검토 단계 오류 불안 해소가 목적.
각 검사는 (level, code, message) 를 낸다. level: 'error' | 'warn'.
"""

VALID_QTYPES = ("정답형", "합답형")
VALID_DIFF = ("상", "중", "하")


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

    return issues


def check_set(set_row: dict, items: list[dict], target_total=100.0) -> list[dict]:
    """세트 전체 검사.

    items: [{question, choices, points}] — 세트 구성 + 각 문항 데이터.
    """
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

    return issues


def summarize(issues: list[dict]) -> dict:
    errors = [i for i in issues if i["level"] == "error"]
    warns = [i for i in issues if i["level"] == "warn"]
    return {"ok": not errors, "error_count": len(errors), "warn_count": len(warns), "issues": issues}
