"""문항 설계 · 세트 관리 · 출력 API."""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import checklist, db, export_palette

router = APIRouter(prefix="/api")

# 합답형 선지 프리셋 (5개년 수능 물리학Ⅰ 74블록 실측 — 2개가 90%)
COMBO_PRESETS = {
    "A": [["ㄱ"], ["ㄴ"], ["ㄱ", "ㄷ"], ["ㄴ", "ㄷ"], ["ㄱ", "ㄴ", "ㄷ"]],
    "B": [["ㄱ"], ["ㄷ"], ["ㄱ", "ㄴ"], ["ㄴ", "ㄷ"], ["ㄱ", "ㄴ", "ㄷ"]],
    "C": [["ㄴ"], ["ㄷ"], ["ㄱ", "ㄴ"], ["ㄱ", "ㄷ"], ["ㄱ", "ㄴ", "ㄷ"]],
}


@router.get("/combo-presets")
def get_combo_presets():
    return COMBO_PRESETS


# ===== 문항 =====
class ChoiceIn(BaseModel):
    ord: int
    text: str = ""
    proposition_id: int | None = None
    variant_id: int | None = None
    combo: list[str] | None = None
    custom_evidence: str = ""
    is_answer: bool = False


class QuestionIn(BaseModel):
    qtype: str = "정답형"
    is_negative: bool = False
    passage: str = ""
    material: str = ""
    ask: str
    bogi_items: list[dict] = []
    answer: str = ""
    default_points: float = 3.0
    difficulty: str = "중"
    standard_code: str | None = None
    intent: str = ""
    choices: list[ChoiceIn] = []


def _load_question(conn, qid):
    q = conn.execute("SELECT * FROM question WHERE id = ?", (qid,)).fetchone()
    if not q:
        return None, None
    ch = conn.execute("SELECT * FROM choice WHERE question_id = ? ORDER BY ord", (qid,)).fetchall()
    return dict(q), [dict(c) for c in ch]


@router.get("/questions")
def list_questions(standard: str = "", q: str = ""):
    sql = """
        SELECT q.*, (SELECT COUNT(*) FROM choice c WHERE c.question_id = q.id) AS choice_count
        FROM question q WHERE 1=1
    """
    args = []
    if standard:
        sql += " AND q.standard_code = ?"; args.append(standard)
    if q:
        sql += " AND (q.ask LIKE ? OR q.passage LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY q.id DESC"
    conn = db.connect()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


@router.get("/questions/{qid}")
def get_question(qid: int):
    conn = db.connect()
    try:
        q, ch = _load_question(conn, qid)
        if not q:
            raise HTTPException(404, "문항을 찾을 수 없습니다.")
        q["bogi_items"] = json.loads(q["bogi_items"] or "[]")
        for c in ch:
            c["combo"] = json.loads(c["combo"]) if c["combo"] else None
        return {"question": q, "choices": ch}
    finally:
        conn.close()


@router.post("/questions")
def create_question(qin: QuestionIn):
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO question (qtype, is_negative, passage, material, ask, bogi_items, "
            " answer, default_points, difficulty, standard_code, intent) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (qin.qtype, int(qin.is_negative), qin.passage.strip(), qin.material.strip(),
             qin.ask.strip(), json.dumps(qin.bogi_items, ensure_ascii=False), qin.answer,
             qin.default_points, qin.difficulty, qin.standard_code, qin.intent.strip()))
        qid = cur.lastrowid
        _save_choices(conn, qid, qin.choices)
        conn.commit()
        return {"id": qid}
    finally:
        conn.close()


@router.put("/questions/{qid}")
def update_question(qid: int, qin: QuestionIn):
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE question SET qtype=?, is_negative=?, passage=?, material=?, ask=?, "
            " bogi_items=?, answer=?, default_points=?, difficulty=?, standard_code=?, intent=? "
            "WHERE id=?",
            (qin.qtype, int(qin.is_negative), qin.passage.strip(), qin.material.strip(),
             qin.ask.strip(), json.dumps(qin.bogi_items, ensure_ascii=False), qin.answer,
             qin.default_points, qin.difficulty, qin.standard_code, qin.intent.strip(), qid))
        conn.execute("DELETE FROM choice WHERE question_id = ?", (qid,))
        _save_choices(conn, qid, qin.choices)
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def _save_choices(conn, qid, choices):
    for c in choices:
        conn.execute(
            "INSERT INTO choice (question_id, ord, text, proposition_id, variant_id, combo, "
            " custom_evidence, is_answer) VALUES (?,?,?,?,?,?,?,?)",
            (qid, c.ord, c.text.strip(), c.proposition_id, c.variant_id,
             json.dumps(c.combo, ensure_ascii=False) if c.combo else "",
             c.custom_evidence.strip(), int(c.is_answer)))


@router.delete("/questions/{qid}")
def delete_question(qid: int):
    conn = db.connect()
    try:
        conn.execute("DELETE FROM question WHERE id = ?", (qid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.get("/questions/{qid}/check")
def check_question_api(qid: int):
    conn = db.connect()
    try:
        q, ch = _load_question(conn, qid)
        if not q:
            raise HTTPException(404, "문항을 찾을 수 없습니다.")
        return checklist.summarize(checklist.check_question(q, ch))
    finally:
        conn.close()


# ===== 세트 =====
class SetIn(BaseModel):
    name: str


class SetItemIn(BaseModel):
    question_id: int
    points: float | None = None


class ReorderIn(BaseModel):
    question_ids: list[int]


@router.get("/sets")
def list_sets():
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM set_item i WHERE i.set_id = s.id) AS item_count "
            "FROM exam_set s ORDER BY s.id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/sets")
def create_set(s: SetIn):
    conn = db.connect()
    try:
        cur = conn.execute("INSERT INTO exam_set (name) VALUES (?)", (s.name.strip(),))
        conn.commit()
        return {"id": cur.lastrowid}
    finally:
        conn.close()


@router.delete("/sets/{sid}")
def delete_set(sid: int):
    conn = db.connect()
    try:
        conn.execute("DELETE FROM exam_set WHERE id = ?", (sid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def _set_items(conn, sid):
    rows = conn.execute(
        "SELECT i.id AS item_id, i.ord, i.points, q.* FROM set_item i "
        "JOIN question q ON q.id = i.question_id WHERE i.set_id = ? ORDER BY i.ord",
        (sid,)).fetchall()
    items = []
    for r in rows:
        q = dict(r)
        item_id, ord_, points = q.pop("item_id"), q.pop("ord"), q.pop("points")
        ch = conn.execute("SELECT * FROM choice WHERE question_id = ? ORDER BY ord",
                          (q["id"],)).fetchall()
        items.append({"item_id": item_id, "ord": ord_, "points": points,
                      "question": q, "choices": [dict(c) for c in ch]})
    return items


@router.get("/sets/{sid}")
def get_set(sid: int):
    conn = db.connect()
    try:
        s = conn.execute("SELECT * FROM exam_set WHERE id = ?", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, "세트를 찾을 수 없습니다.")
        items = _set_items(conn, sid)
        # 대시보드 집계 (항상 파생)
        total = sum((it["points"] if it["points"] is not None
                     else it["question"]["default_points"]) for it in items)
        diff = {"상": 0, "중": 0, "하": 0}
        for it in items:
            diff[it["question"].get("difficulty", "중")] = diff.get(
                it["question"].get("difficulty", "중"), 0) + 1
        covered = sorted({it["question"]["standard_code"] for it in items
                          if it["question"]["standard_code"]})
        return {"set": dict(s), "items": items,
                "dashboard": {"total_points": round(total, 1), "difficulty": diff,
                              "standards": covered, "count": len(items)}}
    finally:
        conn.close()


@router.post("/sets/{sid}/items")
def add_set_item(sid: int, item: SetItemIn):
    conn = db.connect()
    try:
        row = conn.execute("SELECT COALESCE(MAX(ord),0) AS m FROM set_item WHERE set_id = ?",
                           (sid,)).fetchone()
        conn.execute("INSERT INTO set_item (set_id, question_id, ord, points) VALUES (?,?,?,?)",
                     (sid, item.question_id, row["m"] + 1, item.points))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/sets/{sid}/items/{item_id}")
def remove_set_item(sid: int, item_id: int):
    conn = db.connect()
    try:
        conn.execute("DELETE FROM set_item WHERE id = ? AND set_id = ?", (item_id, sid))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.put("/sets/{sid}/order")
def reorder_set(sid: int, body: ReorderIn):
    """드래그 배열 결과 저장 — question_ids 순서대로 ord 재부여."""
    conn = db.connect()
    try:
        for i, qid in enumerate(body.question_ids, start=1):
            conn.execute("UPDATE set_item SET ord = ? WHERE set_id = ? AND question_id = ?",
                         (i, sid, qid))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.get("/sets/{sid}/check")
def check_set_api(sid: int, target: float = 100.0):
    conn = db.connect()
    try:
        s = conn.execute("SELECT * FROM exam_set WHERE id = ?", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, "세트를 찾을 수 없습니다.")
        items = _set_items(conn, sid)
        return checklist.summarize(checklist.check_set(dict(s), items, target_total=target))
    finally:
        conn.close()


@router.get("/sets/{sid}/export")
def export_set(sid: int):
    """세트 → hwppalette 시험문제 문법 마크다운."""
    conn = db.connect()
    try:
        items = _set_items(conn, sid)
        if not items:
            return {"markdown": "", "count": 0}
        pairs = [(it["question"], it["choices"]) for it in items]
        return {"markdown": export_palette.set_to_markdown(pairs), "count": len(pairs)}
    finally:
        conn.close()


@router.get("/questions/{qid}/export")
def export_question(qid: int):
    conn = db.connect()
    try:
        q, ch = _load_question(conn, qid)
        if not q:
            raise HTTPException(404, "문항을 찾을 수 없습니다.")
        return {"markdown": export_palette.question_to_palette(q, ch, num=1)}
    finally:
        conn.close()
