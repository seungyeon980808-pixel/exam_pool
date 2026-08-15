"""Runtime KICE item-style and formula validation for the authoring workflow."""
from __future__ import annotations

import re
import json
from pathlib import Path

from ..formula_markup import validate_formula_markup


STYLE_SPEC_PATH = Path(__file__).resolve().parents[2] / "PRD" / "11_KICE_ITEM_TEXT_STYLE.md"
STYLE_DATA_PATH = STYLE_SPEC_PATH.with_name("11_KICE_ITEM_TEXT_STYLE_DATA.json")


def _attested_sources() -> dict[str, list[dict]]:
    try:
        data = json.loads(STYLE_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    intro = data.get("intro_sources") or {}
    ask = data.get("ask_sources") or {}
    def src(rows):
        return [{"paper": row.get("paper"), "page": row.get("page"), "item": row.get("item"),
                 "sample": row.get("sample", "")}
                for row in (rows or [])[:5]]
    return {
        "INTRO_FIGURE_SINGLE": src(intro.get("그림은")),
        "INTRO_FIGURE_PAIR": src(intro.get("그림_(가)")),
        "INTRO_FIGURE_AS_SHOWN": src(intro.get("그림과_같이")),
        "INTRO_NEXT_DESCRIPTION": src(intro.get("다음은")),
        "INTRO_NEXT_EXPERIMENT": src(intro.get("다음은")),
        "INTRO_NEXT_EXPERIMENT_RESULT": src(intro.get("다음은")),
        "INTRO_TABLE": src(intro.get("표는")),
        "INTRO_DIALOGUE": src(intro.get("그림은")),
        "ASK_BOGI_STANDARD": src(ask.get("보기_있는대로")),
        "ASK_BOGI_TARGET": src(ask.get("보기_있는대로")),
        "ASK_BOGI_STUDENT": src(ask.get("학생_있는대로")),
        "ASK_DIRECT_CORRECT": src(ask.get("직접_옳은것")),
        "ASK_DESCRIPTION_CORRECT": src(ask.get("직접_옳은것")),
        "ASK_GRAPH_BEST": src(ask.get("가장적절")),
        "ASK_COMPARE_CORRECT": src(ask.get("옳게비교")),
        "ASK_ORDER_CORRECT": src(ask.get("기타_것은")),
        "ASK_RATIO": src(ask.get("기타_의문")),
        "ASK_VALUE": src(ask.get("기타_의문")),
    }


FRAME_SOURCES = _attested_sources()

FORBIDDEN = (
    "이에 대한 설명으로 옳은 것만을 고른 것은?", "알맞은 답을 고르시오.",
    "모두 선택하시오.", "찾아보자.", "무엇일까요?", "확인해 보세요.",
    "위 그림을 참고하여", "주어진 정보를 바탕으로", "다음 질문에 답하시오.",
)


def _strip_score(text: str) -> str:
    return re.sub(r"\s*(?:\[|\()\s*\d+(?:\.\d+)?점\s*(?:\]|\))\s*$", "", text.strip())


def infer_intro_frame(passage: str) -> str | None:
    first = next((line.strip() for line in (passage or "").splitlines() if line.strip()), "")
    if not first:
        return None
    if re.match(r"^그림은 .+에 대해 학생 .+가 대화하는 모습을 나타낸 것이다\.$", first):
        return "INTRO_DIALOGUE"
    if re.match(r"^그림은 .+을 나타낸 것이다\.$|^그림은 .+를 나타낸 것이다\.$", first):
        return "INTRO_FIGURE_SINGLE"
    if re.match(r"^그림\s*\(가\)(?:와|과)\s*\(나\)는 각각 .+(?:와|과) .+(?:을|를) 나타낸 것이다\.$", first):
        return "INTRO_FIGURE_PAIR"
    if re.match(r"^그림과 같이 .+한다\.$", first):
        return "INTRO_FIGURE_AS_SHOWN"
    if re.match(r"^다음은 .+에 대한 실험 과정과 결과이다\.$", first):
        return "INTRO_NEXT_EXPERIMENT_RESULT"
    if re.match(r"^다음은 .+에 대한 실험 과정이다\.$", first):
        return "INTRO_NEXT_EXPERIMENT"
    if re.match(r"^다음은 .+에 대한 설명이다\.$", first):
        return "INTRO_NEXT_DESCRIPTION"
    if re.match(r"^표는 .+을 나타낸 것이다\.$|^표는 .+를 나타낸 것이다\.$", first):
        return "INTRO_TABLE"
    return None


def infer_ask_frame(ask: str, qtype: str = "정답형") -> str | None:
    value = _strip_score(ask)
    normalized = value.replace("〈보기〉", "<보기>")
    if normalized == "이에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은?":
        return "ASK_BOGI_STANDARD"
    if re.match(r"^.+에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은\?$", normalized):
        return "ASK_BOGI_TARGET"
    if normalized == "제시한 내용이 옳은 학생만을 있는 대로 고른 것은?":
        return "ASK_BOGI_STUDENT"
    if re.match(r"^.+를 나타낸 그래프로 가장 적절한 것은\?$|^.+을 나타낸 그래프로 가장 적절한 것은\?$", value):
        return "ASK_GRAPH_BEST"
    if re.match(r"^.+에 대한 설명으로 옳은 것은\?$", value):
        return "ASK_DESCRIPTION_CORRECT"
    if re.match(r"^.+를 옳게 비교한 것은\?$|^.+을 옳게 비교한 것은\?$", value):
        return "ASK_COMPARE_CORRECT"
    if re.match(r"^.+를 순서대로 옳게 나타낸 것은\?$|^.+을 순서대로 옳게 나타낸 것은\?$", value):
        return "ASK_ORDER_CORRECT"
    if re.match(r"^.+\s*:\s*.+는\?$", value):
        return "ASK_RATIO"
    if re.match(r"^.+(?:으로|로) 옳은 것은\?$", value):
        return "ASK_DIRECT_CORRECT"
    if qtype != "합답형" and re.match(r"^.+(?:은|는)\?$", value):
        return "ASK_VALUE"
    return None


def enrich_style_metadata(draft: dict) -> dict:
    meta = dict(draft.get("style_meta") or {})
    passage_frame = infer_intro_frame(str(draft.get("passage") or ""))
    ask_frame = infer_ask_frame(str(draft.get("ask") or ""), str(draft.get("qtype") or "정답형"))
    if passage_frame:
        meta["passage"] = {"frame_id": passage_frame, "sources": FRAME_SOURCES[passage_frame]}
    elif not str(draft.get("passage") or "").strip():
        meta.pop("passage", None)
    if ask_frame:
        meta["ask"] = {"frame_id": ask_frame, "sources": FRAME_SOURCES[ask_frame]}
    draft["style_meta"] = meta
    return draft


def validate_draft(draft: dict) -> list[dict]:
    issues: list[dict] = []
    def error(code: str, message: str, field: str = "") -> None:
        issues.append({"level": "error", "code": code, "message": message, "field": field})
    passage = str(draft.get("passage") or "")
    ask = str(draft.get("ask") or "")
    qtype = str(draft.get("qtype") or "정답형")
    raw_style_meta = draft.get("style_meta") or {}
    if isinstance(raw_style_meta, str):
        try:
            raw_style_meta = json.loads(raw_style_meta or "{}")
        except ValueError:
            raw_style_meta = {}
    reconstruction = (raw_style_meta if isinstance(raw_style_meta, dict) else {}).get("reconstruction") or {}
    is_reconstruction = bool(isinstance(reconstruction, dict) and reconstruction.get("enabled"))
    if not ask.strip():
        error("no_ask", "발문이 비어 있습니다.", "ask")
    if not is_reconstruction and passage.strip() and not infer_intro_frame(passage):
        error("style_frame_unattested", "제시문 시작 문장이 기존 평가원 문장 형식과 다릅니다. 생성은 계속할 수 있습니다.", "passage")
    if not is_reconstruction and qtype != "서술형" and ask.strip() and not infer_ask_frame(ask, qtype):
        error("style_frame_unattested", "발문 형식이 기존 평가원 문장 형식과 다릅니다. 생성은 계속할 수 있습니다.", "ask")
    combined = "\n".join([passage, ask] + [
        str(row.get("text") or "") for row in (draft.get("bogi_items") or []) if isinstance(row, dict)
    ])
    if not is_reconstruction:
        for phrase in FORBIDDEN:
            if phrase in combined:
                error("style_forbidden_phrase", f"자동 생성 금지 표현이 포함되어 있습니다: {phrase}")
    bogi = draft.get("bogi_items") or []
    mentions = "<보기>" in ask.replace("〈보기〉", "<보기>")
    if not is_reconstruction and qtype == "합답형" and bogi and not mentions:
        error("bogi_reference_missing", "합답형 발문에 <보기> 언급이 없습니다.", "ask")
    if not is_reconstruction and not bogi and mentions:
        error("bogi_reference_orphan", "<보기>가 없는데 발문에서 <보기>를 언급합니다.", "ask")
    if qtype == "합답형":
        for index, row in enumerate(bogi):
            text = str(row.get("text") or "") if isinstance(row, dict) else str(row)
            if not is_reconstruction and text and not text.endswith("다."):
                error("choice_ending", f"{index + 1}번째 보기는 현재형 평서문 '-다.'로 끝나야 합니다.", "bogi_items")
            if (not is_reconstruction and isinstance(row, dict)
                    and not str(row.get("evidence") or "").strip()):
                error("evidence_missing", f"{index + 1}번째 보기의 레퍼런스 근거가 없습니다.", "bogi_items")
    else:
        for index, row in enumerate(draft.get("choices") or []):
            if (not is_reconstruction and isinstance(row, dict)
                    and not row.get("proposition_id") and not row.get("variant_id")
                    and not str(row.get("custom_evidence") or "").strip()):
                error("evidence_missing", f"{index + 1}번 선지의 레퍼런스 근거가 없습니다.", "choices")
    for field in ("passage", "ask", "explanation"):
        for message in validate_formula_markup(str(draft.get(field) or "")):
            error("formula_invalid", message, field)
    return issues


def validate_evidence_links(draft: dict, reference_ids: set[int]) -> list[dict]:
    """Require every generated truth claim to point at an attached reference."""
    issues = []
    rows = ((draft.get("bogi_items") or []) if draft.get("qtype") == "합답형"
            else (draft.get("choices") or []))
    field = "evidence" if draft.get("qtype") == "합답형" else "custom_evidence"
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        evidence = str(row.get(field) or "")
        cited = {int(value) for value in re.findall(r"(?:reference_id\s*=|\[ref:)(\d+)", evidence)}
        if not cited:
            issues.append({"level": "error", "code": "evidence_reference_id_missing",
                           "message": f"{index + 1}번째 진술 근거에 reference_id를 기록하세요.",
                           "field": "bogi_items" if field == "evidence" else "choices"})
        elif not cited.issubset(reference_ids):
            invalid = ", ".join(map(str, sorted(cited - reference_ids)))
            issues.append({"level": "error", "code": "evidence_reference_unknown",
                           "message": f"{index + 1}번째 진술이 연결되지 않은 reference_id를 사용합니다: {invalid}",
                           "field": "bogi_items" if field == "evidence" else "choices"})
    return issues


def style_prompt_contract() -> str:
    return """문체 계약(PRD/11):
- 제시문 첫 문장은 다음 골격 중 하나만 사용한다: 그림은 [상황]을 나타낸 것이다. / 그림 (가)와 (나)는 각각 [A]와 [B]를 나타낸 것이다. / 그림과 같이 [행동]한다. / 다음은 [대상]에 대한 설명이다. / 다음은 [현상]에 대한 실험 과정이다. / 다음은 [현상]에 대한 실험 과정과 결과이다. / 표는 [결과]를 나타낸 것이다.
- 합답형 기본 발문은 '이에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은?'이다.
- 직접형 발문은 [대상]으로 옳은 것은?, [상황]에 대한 설명으로 옳은 것은?, [관계]를 나타낸 그래프로 가장 적절한 것은?, [A와 B]를 옳게 비교한 것은?, [값]은? 중 하나다.
- passage와 ask 제안의 value는 반드시 사람이 읽는 일반 문자열이어야 한다. 객체를 value 안에 넣지 않는다.
- passage와 ask의 frame_id와 style_sources는 value와 같은 제안 객체의 최상위 키에만 둔다. frame_id는 INTRO_* 또는 ASK_* 등록값이어야 한다.
- 합답형 보기 문장은 주장 하나만 담고 '-다.'로 끝낸다. 모든 보기·선지는 실제 연결 자료의 번호를 `reference_id=3: 출처와 근거 요약` 형식으로 evidence 또는 custom_evidence에 기록한다.
- 수식은 완성 유니코드로 쓰지 않고 [[formula:a = \\frac{\\Delta v}{\\Delta t}]] 형식으로 쓴다. 첨자는 v_0, 지수는 x^2를 쓴다.
- 학습지·존댓말·메타 표현과 '이에 대한 설명으로 옳은 것만을 고른 것은?'은 금지한다."""
