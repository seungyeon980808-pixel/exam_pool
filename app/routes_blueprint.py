"""세트 청사진(ExamMaker) — 문항이 생기기 전의 '주문서' 슬롯을 다룬다.

슬롯 = question_id 가 비어 있는 set_item 행. 계획(plan_*) → 생성(Claude가 채움)
→ 검토가 별도 테이블 없이 한 행의 생애주기로 흐른다 (PRD/exammaker/02).
기존 세트 화면(_set_items)은 INNER JOIN 이라 빈 슬롯을 안 보여준다 —
그래서 청사진은 여기서 따로 읽는다.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import db
from . import prompt_builder

router = APIRouter()

QTYPES = ("정답형", "합답형", "서술형")


class SlotIn(BaseModel):
    plan_qtype: str = "정답형"
    plan_standard_code: str = ""
    plan_topic: str = ""
    plan_is_negative: bool = False
    plan_needs_figure: bool = False
    plan_figure_hint: str = ""
    plan_situation: str = ""
    points: float | None = None


class SlotPatch(BaseModel):
    plan_qtype: str | None = None
    plan_standard_code: str | None = None
    plan_topic: str | None = None
    plan_is_negative: bool | None = None
    plan_needs_figure: bool | None = None
    plan_figure_hint: str | None = None
    plan_situation: str | None = None
    points: float | None = None


class ShortCodeIn(BaseModel):
    short_code: str


def blueprint(conn, sid: int) -> dict:
    """세트 + 슬롯 전체(빈 슬롯 포함). MCP get_blueprint 와 화면이 같이 쓴다."""
    s = conn.execute("SELECT * FROM exam_set WHERE id = ?", (sid,)).fetchone()
    if not s:
        raise HTTPException(404, "세트를 찾을 수 없습니다.")
    rows = conn.execute(
        "SELECT i.id AS item_id, i.ord, i.points, i.question_id, "
        " i.plan_qtype, i.plan_standard_code, i.plan_topic, i.plan_is_negative, "
        " i.plan_needs_figure, i.plan_figure_hint, i.plan_situation, i.slot_status, "
        " q.ask AS q_ask, q.status AS q_status "
        "FROM set_item i LEFT JOIN question q ON q.id = i.question_id "
        "WHERE i.set_id = ? ORDER BY i.ord", (sid,)).fetchall()
    short = s["short_code"] or ""
    slots = []
    for r in rows:
        d = dict(r)
        d["figure_name"] = (f"{short}_{d['ord']:02d}" if short and d["plan_needs_figure"] else "")
        slots.append(d)
    return {"set": dict(s), "slots": slots}


@router.get("/api/sets/{sid}/blueprint")
def get_blueprint_api(sid: int):
    conn = db.connect()
    try:
        return blueprint(conn, sid)
    finally:
        conn.close()


@router.post("/api/sets/{sid}/slots")
def create_slot(sid: int, s: SlotIn):
    if s.plan_qtype not in QTYPES:
        raise HTTPException(400, f"유형은 {'/'.join(QTYPES)} 중 하나여야 합니다.")
    conn = db.connect()
    try:
        if not conn.execute("SELECT 1 FROM exam_set WHERE id = ?", (sid,)).fetchone():
            raise HTTPException(404, "세트를 찾을 수 없습니다.")
        m = conn.execute("SELECT COALESCE(MAX(ord),0) AS m FROM set_item WHERE set_id = ?",
                         (sid,)).fetchone()["m"]
        cur = conn.execute(
            "INSERT INTO set_item (set_id, question_id, ord, points, plan_qtype, "
            " plan_standard_code, plan_topic, plan_is_negative, plan_needs_figure, "
            " plan_figure_hint, plan_situation, slot_status) "
            "VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'empty')",
            (sid, m + 1, s.points, s.plan_qtype, s.plan_standard_code.strip(),
             s.plan_topic.strip(), int(s.plan_is_negative), int(s.plan_needs_figure),
             s.plan_figure_hint.strip(), s.plan_situation.strip()))
        conn.commit()
        return {"id": cur.lastrowid}
    finally:
        conn.close()


@router.patch("/api/sets/{sid}/slots/{item_id}")
def update_slot(sid: int, item_id: int, p: SlotPatch):
    if p.plan_qtype is not None and p.plan_qtype not in QTYPES:
        raise HTTPException(400, f"유형은 {'/'.join(QTYPES)} 중 하나여야 합니다.")
    fields = {k: v for k, v in p.model_dump().items() if v is not None}
    conn = db.connect()
    try:
        row = conn.execute("SELECT set_id FROM set_item WHERE id = ?", (item_id,)).fetchone()
        if not row or row["set_id"] != sid:
            raise HTTPException(404, "슬롯을 찾을 수 없습니다.")
        for k, v in fields.items():
            if isinstance(v, bool):
                v = int(v)
            elif isinstance(v, str):
                v = v.strip()
            conn.execute(f"UPDATE set_item SET {k} = ? WHERE id = ?", (v, item_id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.put("/api/sets/{sid}/short-code")
def set_short_code(sid: int, body: ShortCodeIn):
    """세트 약칭 — 그림 파일명 규약 {short_code}_{번호2자리} 의 앞부분.

    hwpPalette 문법 기호(\\ { } &)와 파일명 금지 문자는 받지 않는다.
    """
    code = body.short_code.strip()
    if any(c in code for c in '\\{}&/:*?"<>| '):
        raise HTTPException(400, "약칭에는 공백과 \\ { } & / : * ? \" < > | 를 쓸 수 없습니다.")
    conn = db.connect()
    try:
        conn.execute("UPDATE exam_set SET short_code = ? WHERE id = ?", (code, sid))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.get("/api/sets/{sid}/prompt")
def get_prompt(sid: int):
    """청사진 → Claude Code 에 붙여넣을 출제 지시문."""
    conn = db.connect()
    try:
        bp = blueprint(conn, sid)
    finally:
        conn.close()
    empty = [s for s in bp["slots"] if not s["question_id"]]
    if not empty:
        raise HTTPException(400, "빈 슬롯이 없습니다 — 계획 탭에서 슬롯을 먼저 추가하세요.")
    if not bp["set"]["short_code"]:
        raise HTTPException(400, "세트 약칭(short_code)을 먼저 정하세요 — 그림 파일명에 필요합니다.")
    return {"prompt": prompt_builder.build(bp), "slot_count": len(empty)}
