"""문항·세트 → hwppalette 시험문제 문법 변환기.

한글 없이 도는 순수 함수 (tests/ 에서 단독 검증).
용어 매핑(04_PROJECT_SPEC): 지문→발문:, 자료→자료:/사진자료:, 발문→질문:, 보기→보기:, 선지→선지:
hwppalette 문법 근거: 31_hwp_palette/parser.py
"""
import json


def _negatize(ask: str, is_negative: bool) -> str:
    """부정 발문이면 '옳지 않은'을 굵게 강조한다(hwppalette 서식 문법)."""
    if not is_negative:
        return ask
    for kw in ("옳지 않은", "옳지 않는", "적절하지 않은", "틀린"):
        if kw in ask:
            return ask.replace(kw, "\\굵게{" + kw + "}", 1)
    return ask


def question_to_palette(q: dict, choices: list[dict], num=None) -> str:
    """문항 1개 → hwppalette 마크다운 블록.

    q: question 행(dict). choices: choice 행 목록(ord 순).
    """
    lines = []
    if num is not None:
        lines.append(f"번호: {num}")

    if q.get("passage"):
        lines.append(f"발문: {q['passage']}")

    material = q.get("material") or ""
    if material:
        # 이미지 파일명이면 사진자료(\파일이름\), 아니면 자료
        if _looks_like_filename(material):
            lines.append(f"사진자료: \\{material}\\")
        else:
            lines.append(f"자료: {material}")

    ask = _negatize(q.get("ask", ""), bool(q.get("is_negative")))
    lines.append(f"질문: {ask}")

    # 보기 (합답형)
    bogi = q.get("bogi_items")
    if isinstance(bogi, str):
        bogi = json.loads(bogi or "[]")
    if bogi:
        lines.append("보기:")
        for b in bogi:
            lines.append(b["text"] if isinstance(b, dict) else str(b))

    # 선지
    lines.append("선지:")
    for c in sorted(choices, key=lambda x: x.get("ord", 0)):
        lines.append(_choice_text(c))

    return "\n".join(lines)


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
    return c.get("text", "")


def _looks_like_filename(s: str) -> bool:
    s = s.strip()
    if "\n" in s or len(s) > 60:
        return False
    return ("." in s and " " not in s) or s.replace("_", "").isalnum()


def set_to_markdown(questions: list[tuple[dict, list[dict]]]) -> str:
    """세트 전체 → hwppalette 마크다운.

    questions: [(question_dict, choices_list), ...] — 세트 배열 순서.
    문항 사이는 빈 줄로 구분한다.
    """
    blocks = []
    for i, (q, choices) in enumerate(questions, start=1):
        blocks.append(question_to_palette(q, choices, num=i))
    return "\n\n".join(blocks) + "\n"
