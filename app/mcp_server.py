"""ExamPool MCP 서버 — Claude Code(·Desktop)에서 명제·근거·문항을 다룬다.

**왜 MCP 인가**: 앱 화면에 AI 버튼을 다는 대신, 이미 매일 쓰는 Claude Code 가
ExamPool 의 데이터를 도구로 호출하게 한다. 그래서 이 앱에는 LLM 도 API 키도 들어가지
않는다 — 모델은 클라이언트(Claude Code)가 댄다.

**설계**: FastAPI 서버(uvicorn)와 같은 `data/exam_pool.db` 를 공유한다. 앱이 떠 있지
않아도 되고, 로직은 기존 라우트 함수를 그대로 재사용한다(두 벌로 갈라지지 않게).

**목표 두 가지**:
  - 참 명제 생성: get_standard → search_evidence 로 교과서 근거를 깔고 초안 →
    create_proposition + add_evidence 로 되쓴다(인용 근거까지 연결).
  - 문항 검토: get_question / get_set 로 조립된 문항을 읽고 의미 문제를 짚는다.

실행: `python -m app.mcp_server` (stdio). Claude Code 등록은 README_MCP.md 참고.
"""
from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP

from . import db
from .routes_bank import (EvidenceIn, PropIn, VariantIn, create_evidence,
                          create_proposition, create_variant, get_proposition,
                          list_propositions)
from .routes_question import (ChoiceIn, QuestionIn, check_question_api,
                              create_question as create_question_route,
                              get_question, get_set, list_questions, list_sets,
                              update_question as update_question_route)
from . import pdf_indexer

mcp = FastMCP(
    "ExamPool",
    instructions=(
        "중학교 과학 시험 출제 도구 ExamPool 의 데이터에 접근한다. 두 가지에 쓴다.\n"
        "1) 참 명제 생성 — 성취기준을 고르면(get_standard), search_evidence 로 교과서\n"
        "   원문을 찾아 **근거에 실제로 있는** 참 명제만 만든다. 지어내지 말 것. 만든 명제는\n"
        "   create_proposition 으로 저장하고, 근거 문장을 add_evidence 로 붙인다.\n"
        "   '참'이라는 판단은 교사가 확인하므로, 인용 근거(source_label·quote)를 반드시 남긴다.\n"
        "2) 문항 검토 — get_question/get_set 으로 문항을 읽고, 보기 중복·발문 모호·정답\n"
        "   복수 성립·성취기준 불일치 같은 의미 문제를 짚는다. check_question 으로 규칙\n"
        "   검토 결과도 함께 참고한다."
    ),
)


def _clean(fn, *a, **k):
    """라우트 함수의 예외를 MCP 가 이해하는 오류로 바꾼다."""
    try:
        return fn(*a, **k)
    except HTTPException as e:
        raise ValueError(e.detail)
    except Exception as e:
        raise ValueError(f"{type(e).__name__}: {e}")


# ===== 성취기준 =====
@mcp.tool()
def list_standards(unit_no: int = 0, query: str = "") -> list:
    """성취기준 목록. unit_no(단원 번호)나 query(본문 부분일치)로 좁힐 수 있다.

    반환: [{code, unit_no, unit_name, seq, text, explain}]
    """
    sql = ("SELECT s.code, s.unit_no, s.seq, s.text, s.explain, u.name AS unit_name "
           "FROM standard s LEFT JOIN unit u ON u.unit_no = s.unit_no WHERE 1=1")
    args = []
    if unit_no:
        sql += " AND s.unit_no = ?"; args.append(unit_no)
    if query:
        sql += " AND s.text LIKE ?"; args.append(f"%{query}%")
    sql += " ORDER BY s.unit_no, s.seq"
    conn = db.connect()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


@mcp.tool()
def get_standard(code: str) -> dict:
    """성취기준 하나 + 해설 + 그 단원의 유의사항·탐구활동. 참 명제를 만들 때의 바탕이다.

    반환: {code, unit_no, unit_name, text, explain, consider:[...], inquiry:[...]}
    """
    import json

    conn = db.connect()
    try:
        r = conn.execute(
            "SELECT s.code, s.unit_no, s.seq, s.text, s.explain, "
            "  u.name AS unit_name, u.consider, u.inquiry "
            "FROM standard s LEFT JOIN unit u ON u.unit_no = s.unit_no "
            "WHERE s.code = ?", (code,)).fetchone()
    finally:
        conn.close()
    if not r:
        raise ValueError(f"성취기준을 찾을 수 없습니다: {code}")
    d = dict(r)
    d["consider"] = json.loads(d.pop("consider") or "[]")
    d["inquiry"] = json.loads(d.pop("inquiry") or "[]")
    return d


# ===== 근거 검색 (교과서·교육과정·기출·수업) =====
@mcp.tool()
def search_evidence(query: str, doc_type: str = "", limit: int = 20) -> dict:
    """색인된 문서에서 근거를 찾는다(SQLite FTS5). 참 명제의 사실 근거로 쓴다.

    query: 띄어쓰기로 여러 키워드(모두 포함). 예 "빛 굴절".
    doc_type: "교과서" | "교육과정" | "기출" | "수업" | "" (빈 값이면 전체).
    반환: {total, terms, items:[{doc_title, page_no, document_id, source_label,
           snippet, match_pct, kind}]}. snippet 은 일치 부위 발췌다.
    """
    return pdf_indexer.search(query, limit=limit, doc_type=doc_type)


# ===== 명제 Pool =====
@mcp.tool()
def list_pool(standard: str = "", unit: int = 0, query: str = "") -> list:
    """명제 Pool 조회. 이미 있는 명제와 중복을 피하려면 먼저 여기를 본다.

    반환: [{id, text, standard_code, unit_no, tags, ev_count, var_count, ...}]
    """
    return list_propositions(standard=standard, unit=unit, q=query)


@mcp.tool()
def get_pool_item(proposition_id: int) -> dict:
    """명제 하나 + 붙은 거짓 변형(오답)·근거를 함께 본다."""
    return _clean(get_proposition, proposition_id)


@mcp.tool()
def create_pool_item(text: str, standard_code: str, tags: str = "", note: str = "") -> dict:
    """참 명제를 Pool 에 저장한다. **교과서 근거로 확인된 참**만 넣을 것.

    text: 참인 명제 한 문장. standard_code: 연결할 성취기준(예 "9과10-02").
    저장 후 add_evidence 로 근거를 꼭 붙인다. 반환: {id}
    """
    return _clean(create_proposition,
                  PropIn(text=text, standard_code=standard_code, tags=tags, note=note))


@mcp.tool()
def add_evidence(proposition_id: int, source_label: str, quote: str,
                 source_type: str = "교과서", document_page_id: int = 0) -> dict:
    """명제에 근거를 붙인다. source_label·quote 는 search_evidence 결과에서 가져온다.

    source_label: "교과서명 p.83" 같은 출처 표기.
    quote: 근거가 되는 원문 문장(search_evidence 의 snippet/본문).
    document_page_id: 아는 경우만(모르면 0). 반환: {id}
    """
    return _clean(create_evidence, EvidenceIn(
        proposition_id=proposition_id, source_type=source_type,
        source_label=source_label, quote=quote,
        document_page_id=document_page_id if document_page_id else None))


@mcp.tool()
def add_false_variant(proposition_id: int, text: str, distortion: str, note: str = "") -> dict:
    """참 명제를 비틀어 만든 거짓 변형(오답 재료)을 붙인다.

    distortion 유형: 수치·정도 변경 / 주체 바꿈 / 인과 역전 / 조건 삭제 / 개념 혼동.
    반환: {id}
    """
    return _clean(create_variant, VariantIn(
        proposition_id=proposition_id, text=text, distortion=distortion, note=note))


# ===== 문항 검토 =====
@mcp.tool()
def list_question_bank(standard: str = "", query: str = "", status: str = "") -> list:
    """문항 목록. 검토할 문항을 고를 때 쓴다."""
    return list_questions(standard=standard, q=query, status=status)


@mcp.tool()
def get_question_detail(question_id: int) -> dict:
    """문항 하나 + 선지 전체. 의미 검토(보기 중복·발문 모호·정답 복수 등)의 입력이다."""
    return _clean(get_question, question_id)


@mcp.tool()
def check_question_rules(question_id: int) -> dict:
    """규칙 기반 자동 검토 결과. AI 의미 검토와 나란히 참고한다."""
    return _clean(check_question_api, question_id)


# ===== 문항 조립 (ExamMaker — 청사진 슬롯을 채운다) =====
# 쓰기 툴이지만 원칙은 읽기 툴과 같다: 근거 없는 내용을 지어내지 않는다.
# 선지는 가급적 proposition_id/variant_id 로 Pool 의 명제·변형을 연결해,
# 교사가 검토 화면에서 근거를 따라갈 수 있게 한다.

@mcp.tool()
def create_question(qtype: str, ask: str, passage: str = "", material: str = "",
                    bogi_items: list[dict] | None = None, is_negative: bool = False,
                    answer: str = "", default_points: float = 3.0, difficulty: str = "중",
                    standard_code: str = "", intent: str = "", explanation: str = "", behavior: str = "",
                    choices: list[dict] | None = None, origin_note: str = "") -> dict:
    """문항을 만든다. origin 은 'AI초안'으로 고정 저장된다(첫 줄을 쓴 주체가 AI라는 사실).

    qtype: "정답형" | "합답형" | "서술형".
    material: 그림 파일명 (규약 {세트약칭}_{번호2자리}, 예 "26-1기말_03"). 그림 없으면 "".
    bogi_items: 합답형 <보기> — [{"label":"ㄱ","text":"..."}] 형식.
    choices: [{"ord":1,"text":"...","proposition_id":?, "variant_id":?, "combo":["ㄱ","ㄴ"],
              "is_answer":false}] — Pool 명제와 연결하면 검토 화면에서 근거 추적이 된다.
    standard_code: 대괄호 없이 저장돼 있으면 그대로, 화면 표기는 대괄호 포함.
    반환: {id}
    """
    try:
        choice_models = [ChoiceIn(**c) for c in (choices or [])]
    except Exception as e:
        raise ValueError(f"선지(choices) 형식이 잘못되었습니다: {e}")
    return _clean(create_question_route, QuestionIn(
        qtype=qtype, ask=ask, passage=passage, material=material,
        bogi_items=bogi_items or [], is_negative=is_negative, answer=answer,
        default_points=default_points, difficulty=difficulty,
        standard_code=standard_code or None, intent=intent, explanation=explanation, behavior=behavior,
        origin="AI초안", origin_note=origin_note,
        choices=choice_models))


@mcp.tool()
def update_question(question_id: int, ask: str = "", passage: str = "",
                    material: str = "", answer: str = "", intent: str = "", explanation: str = "",
                    bogi_items: list[dict] | None = None,
                    choices: list[dict] | None = None) -> dict:
    """문항을 부분 수정한다. 넘긴 필드만 바뀌고 나머지는 유지된다(빈 문자열/None = 유지).

    material 을 지우려면 "-" 를 넘긴다. status 가 '완성'인 문항은 수정하지 않는다.
    반환: {ok}
    """
    cur = _clean(get_question, question_id)
    q, ch = cur["question"], cur["choices"]
    if q["status"] == "완성":
        raise ValueError("status 가 '완성'인 문항은 MCP 로 수정하지 않는다 — 화면에서 직접.")
    try:
        if choices is not None:
            choice_models = [ChoiceIn(**c) for c in choices]
        else:
            choice_models = [
                ChoiceIn(ord=c["ord"], text=c["text"], proposition_id=c["proposition_id"],
                         variant_id=c["variant_id"], combo=c["combo"],
                         custom_evidence=c["custom_evidence"], is_answer=bool(c["is_answer"]))
                for c in ch]
    except Exception as e:
        raise ValueError(f"선지(choices) 형식이 잘못되었습니다: {e}")
    merged = QuestionIn(
        title=q["title"], qtype=q["qtype"], image_choices=bool(q["image_choices"]),
        status=q["status"], review_note=q["review_note"],
        is_negative=bool(q["is_negative"]),
        passage=passage or q["passage"],
        material=("" if material == "-" else (material or q["material"])),
        ask=ask or q["ask"],
        bogi_items=bogi_items if bogi_items is not None else q["bogi_items"],
        answer=answer or q["answer"], default_points=q["default_points"],
        difficulty=q["difficulty"], standard_code=q["standard_code"],
        intent=intent or q["intent"], explanation=explanation or q.get("explanation", ""), behavior=q["behavior"],
        origin=q["origin"], origin_note=q["origin_note"],
        choices=choice_models)
    return _clean(update_question_route, question_id, merged)


@mcp.tool()
def get_blueprint(set_id: int) -> dict:
    """세트의 청사진 — 슬롯별 계획(plan_*)과 채워진 문항 id 를 함께 본다.

    출제 지시문과 대조하며 작업 순서를 잡을 때 먼저 호출한다.
    반환: {set: {id, name, short_code, status, total_points},
           slots: [{item_id, ord, points, plan_qtype, plan_standard_code, plan_topic,
                    plan_is_negative, plan_needs_figure, plan_figure_hint,
                    plan_situation, slot_status, question_id, figure_name}]}
    figure_name 은 파일명 규약({short_code}_{ord 2자리})으로 계산된 값 — 그림을
    그릴 때 5E 페이지 이름과 저장 파일명을 이 값으로 맞춘다.
    """
    from .routes_blueprint import blueprint
    conn = db.connect()
    try:
        return _clean(blueprint, conn, set_id)
    finally:
        conn.close()


@mcp.tool()
def attach_to_set(set_id: int, item_id: int, question_id: int) -> dict:
    """생성한 문항을 청사진 슬롯에 끼운다. slot_status 는 'generated' 가 된다.

    item_id: get_blueprint 가 준 슬롯 id. 이미 문항이 있는 슬롯이면 거부한다
    (교사가 검토한 문항을 덮어쓰지 않기 위해 — 바꾸려면 화면에서).
    반환: {ok}
    """
    conn = db.connect()
    try:
        r = conn.execute("SELECT set_id, question_id FROM set_item WHERE id = ?",
                         (item_id,)).fetchone()
        if not r or r["set_id"] != set_id:
            raise ValueError(f"세트 {set_id} 에 슬롯 {item_id} 이 없습니다.")
        if r["question_id"]:
            raise ValueError("슬롯에 이미 문항이 있습니다. 교체는 화면에서 직접.")
        if not conn.execute("SELECT 1 FROM question WHERE id = ?", (question_id,)).fetchone():
            raise ValueError(f"문항을 찾을 수 없습니다: {question_id}")
        conn.execute("UPDATE set_item SET question_id = ?, slot_status = 'generated' "
                     "WHERE id = ?", (question_id, item_id))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@mcp.tool()
def list_exam_sets() -> list:
    """시험 세트 목록."""
    return list_sets()


@mcp.tool()
def get_exam_set(set_id: int) -> dict:
    """세트 하나 + 담긴 문항·선지 + 대시보드(배점 합·난이도 분포·커버리지). 세트 검토용."""
    return _clean(get_set, set_id)


if __name__ == "__main__":
    mcp.run()   # 기본 stdio 전송 — Claude Code 가 하위 프로세스로 띄운다
