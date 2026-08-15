"""문항 설계 · 세트 관리 · 출력 API."""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from . import checklist, db, export_palette, reports

router = APIRouter(prefix="/api")

QUESTION_SORTS = {
    "created", "updated", "status", "review", "usage", "standard",
    "difficulty", "qtype", "origin", "points",
}

# 합답형 선지 프리셋 (2022~2026 수능 물리학Ⅰ 74블록 실측 — 상위 2개가 90%)
#
# 세 프리셋 모두 ㄱ·ㄴ·ㄷ 이 각각 3번씩 나와 노출은 균등하다.
# 차이는 **어느 보기가 '단독 선지'로 나오는가** 뿐이라 이름을 거기서 딴다.
COMBO_PRESETS = {
    "A": {
        "name": "ㄱ·ㄴ 단독형",
        "desc": "수능 최빈 (34/74)",
        "combos": [["ㄱ"], ["ㄴ"], ["ㄱ", "ㄷ"], ["ㄴ", "ㄷ"], ["ㄱ", "ㄴ", "ㄷ"]],
    },
    "B": {
        "name": "ㄱ·ㄷ 단독형",
        "desc": "수능 빈출 (33/74)",
        "combos": [["ㄱ"], ["ㄷ"], ["ㄱ", "ㄴ"], ["ㄴ", "ㄷ"], ["ㄱ", "ㄴ", "ㄷ"]],
    },
    "C": {
        "name": "ㄴ·ㄷ 단독형",
        "desc": "변형 (드묾)",
        "combos": [["ㄴ"], ["ㄷ"], ["ㄱ", "ㄴ"], ["ㄱ", "ㄷ"], ["ㄱ", "ㄴ", "ㄷ"]],
    },
}


@router.get("/combo-presets")
def get_combo_presets():
    """각 프리셋의 이름·설명·선지 조합. 화면에서 무엇이 들어가는지 보이도록 미리보기 문자열도 준다."""
    out = {}
    for k, v in COMBO_PRESETS.items():
        out[k] = {
            **v,
            "preview": " / ".join("".join(c) for c in v["combos"]),
        }
    return out


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
    title: str = ""
    qtype: str = "정답형"
    image_choices: bool = False
    status: str = "초안"
    review_note: str = "{}"
    style_meta: dict = {}
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
    explanation: str = ""
    behavior: str = ""          # 이원목적분류표의 행동영역
    origin: str = ""            # 직접 / AI초안 / 기출변형 — 처음 쓴 주체(사실)
    origin_note: str = ""       # 출처 메모 (기출 출처, 적재 스크립트 이름 등)
    choices: list[ChoiceIn] = []
    layout_style: str = "school"  # 미리보기 전용; 문항은행 데이터에는 저장하지 않는다.

    @field_validator("title", "passage", "material", "ask", "answer", "intent", "explanation", "origin_note")
    @classmethod
    def reject_javascript_object_literal(cls, value: str) -> str:
        if value.strip().lower() == "[object object]":
            raise ValueError("내부 객체 문자열은 문항 내용으로 사용할 수 없습니다.")
        return value


def _validate_complete(qin: QuestionIn) -> None:
    if qin.status != "완성":
        return
    q = qin.model_dump(exclude={"choices", "layout_style"})
    choices = [choice.model_dump() for choice in qin.choices]
    errors = [item for item in checklist.check_question(q, choices) if item["level"] == "error"]
    if errors:
        raise HTTPException(409, {"message": "완성 문항 검사를 통과하지 못했습니다.", "issues": errors})


def _load_question(conn, qid):
    q = conn.execute("SELECT * FROM question WHERE id = ?", (qid,)).fetchone()
    if not q:
        return None, None
    ch = conn.execute("SELECT * FROM choice WHERE question_id = ? ORDER BY ord", (qid,)).fetchall()
    return dict(q), [dict(c) for c in ch]


@router.get("/questions")
def list_questions(standard: str = "", q: str = "", status: str = "",
                   sort: str = "created", direction: str = "desc"):
    sql = """
        SELECT q.*,
               (SELECT COUNT(*) FROM choice c WHERE c.question_id = q.id) AS choice_count,
               (SELECT COUNT(*) FROM set_item si WHERE si.question_id = q.id) AS usage_count
        FROM question q WHERE 1=1
    """
    args = []
    if standard:
        sql += " AND q.standard_code = ?"; args.append(standard)
    if q:
        like = f"%{q}%"
        sql += (" AND (CAST(q.id AS TEXT) LIKE ? OR q.ask LIKE ? OR q.passage LIKE ? "
                "OR q.title LIKE ? OR q.standard_code LIKE ? OR q.origin_note LIKE ?)")
        args += [like] * 6
    if status:
        sql += " AND q.status = ?"; args.append(status)
    conn = db.connect()
    try:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        ids = [row["id"] for row in rows]
        choices_by_question = {qid: [] for qid in ids}
        if ids:
            marks = ",".join("?" for _ in ids)
            for choice in conn.execute(
                    f"SELECT * FROM choice WHERE question_id IN ({marks}) ORDER BY ord", ids):
                choices_by_question[choice["question_id"]].append(dict(choice))
        for row in rows:
            review = checklist.summarize(
                checklist.check_question(row, choices_by_question[row["id"]])
            )
            row["review_error_count"] = review["error_count"]
            row["review_warn_count"] = review["warn_count"]

        status_rank = {"초안": 0, "검토중": 1, "완성": 2}
        difficulty_rank = {"하": 0, "중": 1, "상": 2}
        key_name = sort if sort in QUESTION_SORTS else "created"
        key_funcs = {
            "created": lambda row: row.get("created_at") or "",
            "updated": lambda row: row.get("updated_at") or row.get("created_at") or "",
            "status": lambda row: status_rank.get(row.get("status"), -1),
            "review": lambda row: row["review_error_count"] * 100 + row["review_warn_count"],
            "usage": lambda row: row["usage_count"],
            "standard": lambda row: row.get("standard_code") or "",
            "difficulty": lambda row: difficulty_rank.get(row.get("difficulty"), -1),
            "qtype": lambda row: row.get("qtype") or "",
            "origin": lambda row: row.get("origin") or "",
            "points": lambda row: row.get("default_points") or 0,
        }
        reverse = direction.lower() != "asc"
        rows.sort(key=lambda row: (key_funcs[key_name](row), row["id"]), reverse=reverse)
        return rows
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
        q["style_meta"] = json.loads(q.get("style_meta") or "{}")
        for c in ch:
            c["combo"] = json.loads(c["combo"]) if c["combo"] else None
        return {"question": q, "choices": ch}
    finally:
        conn.close()


@router.post("/questions")
def create_question(qin: QuestionIn):
    _validate_complete(qin)
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO question (title, qtype, image_choices, status, review_note, style_meta, is_negative, "
            " passage, material, ask, bogi_items, answer, default_points, difficulty, standard_code, "
            " intent, explanation, behavior, origin, origin_note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (qin.title.strip(), qin.qtype, int(qin.image_choices), qin.status, qin.review_note,
             json.dumps(qin.style_meta, ensure_ascii=False), int(qin.is_negative), qin.passage.strip(), qin.material.strip(),
             qin.ask.strip(), json.dumps(qin.bogi_items, ensure_ascii=False), qin.answer,
             qin.default_points, qin.difficulty, qin.standard_code, qin.intent.strip(), qin.explanation.strip(),
             qin.behavior.strip(), qin.origin.strip(), qin.origin_note.strip()))
        qid = cur.lastrowid
        _save_choices(conn, qid, qin.choices)
        return {"id": qid}


@router.put("/questions/{qid}")
def update_question(qid: int, qin: QuestionIn):
    _validate_complete(qin)
    with db.transaction() as conn:
        exists = conn.execute("SELECT 1 FROM question WHERE id = ?", (qid,)).fetchone()
        if not exists:
            raise HTTPException(404, "문항을 찾을 수 없습니다.")
        conn.execute(
            "UPDATE question SET title=?, qtype=?, image_choices=?, status=?, review_note=?, style_meta=?, is_negative=?, "
            " passage=?, material=?, ask=?, bogi_items=?, answer=?, default_points=?, difficulty=?, "
            " standard_code=?, intent=?, explanation=?, behavior=?, origin=?, origin_note=?, "
            " updated_at=datetime('now','localtime') WHERE id=?",
            (qin.title.strip(), qin.qtype, int(qin.image_choices), qin.status, qin.review_note,
             json.dumps(qin.style_meta, ensure_ascii=False), int(qin.is_negative), qin.passage.strip(), qin.material.strip(),
             qin.ask.strip(), json.dumps(qin.bogi_items, ensure_ascii=False), qin.answer,
             qin.default_points, qin.difficulty, qin.standard_code, qin.intent.strip(), qin.explanation.strip(),
             qin.behavior.strip(), qin.origin.strip(), qin.origin_note.strip(), qid))
        conn.execute("DELETE FROM choice WHERE question_id = ?", (qid,))
        _save_choices(conn, qid, qin.choices)
        return {"ok": True}


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
    with db.transaction() as conn:
        exists = conn.execute("SELECT 1 FROM question WHERE id = ?", (qid,)).fetchone()
        if not exists:
            raise HTTPException(404, "문항을 찾을 수 없습니다.")
        conn.execute("DELETE FROM exam_ref WHERE question_id = ?", (qid,))
        conn.execute("DELETE FROM question WHERE id = ?", (qid,))
        return {"ok": True}


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
    total_points: float = 100.0
    layout_style: str = "school"


class SetPatch(BaseModel):
    name: str | None = None
    total_points: float | None = None
    status: str | None = None
    layout_style: str | None = None


class SetItemIn(BaseModel):
    question_id: int
    points: float | None = None


class ItemPointsIn(BaseModel):
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
    with db.transaction() as conn:
        layout_style = "suneung" if s.layout_style == "suneung" else "school"
        cur = conn.execute("INSERT INTO exam_set (name, total_points, layout_style) VALUES (?,?,?)",
                           (s.name.strip(), s.total_points, layout_style))
        return {"id": cur.lastrowid}


@router.patch("/sets/{sid}")
def update_set(sid: int, p: SetPatch):
    """세트 이름·만점·상태 수정. 만점은 지필 70점처럼 100이 아닌 시험 때문에 필요하다."""
    with db.transaction() as conn:
        if p.name is not None:
            conn.execute("UPDATE exam_set SET name=? WHERE id=?", (p.name.strip(), sid))
        if p.total_points is not None:
            conn.execute("UPDATE exam_set SET total_points=? WHERE id=?", (p.total_points, sid))
        if p.status is not None:
            conn.execute("UPDATE exam_set SET status=? WHERE id=?", (p.status.strip(), sid))
        if p.layout_style is not None:
            layout_style = "suneung" if p.layout_style == "suneung" else "school"
            conn.execute("UPDATE exam_set SET layout_style=? WHERE id=?", (layout_style, sid))
        return {"ok": True}


@router.delete("/sets/{sid}")
def delete_set(sid: int):
    with db.transaction() as conn:
        conn.execute("DELETE FROM exam_set WHERE id = ?", (sid,))
        return {"ok": True}


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
        target = dict(s).get("total_points") or 100.0
        return {"set": dict(s), "items": items,
                "dashboard": {"total_points": round(total, 1), "difficulty": diff,
                              "standards": covered, "count": len(items),
                              "target_points": round(target, 1),
                              "gap": round(total - target, 1)}}
    finally:
        conn.close()


@router.post("/sets/{sid}/items")
def add_set_item(sid: int, item: SetItemIn):
    with db.transaction() as conn:
        if conn.execute(
            "SELECT 1 FROM set_item WHERE set_id = ? AND question_id = ?",
            (sid, item.question_id),
        ).fetchone():
            raise HTTPException(409, "이미 이 세트에 들어 있는 문항입니다.")
        row = conn.execute("SELECT COALESCE(MAX(ord),0) AS m FROM set_item WHERE set_id = ?",
                           (sid,)).fetchone()
        conn.execute("INSERT INTO set_item (set_id, question_id, ord, points) VALUES (?,?,?,?)",
                     (sid, item.question_id, row["m"] + 1, item.points))
        return {"ok": True}


@router.delete("/sets/{sid}/items/{item_id}")
def remove_set_item(sid: int, item_id: int):
    with db.transaction() as conn:
        conn.execute("DELETE FROM set_item WHERE id = ? AND set_id = ?", (item_id, sid))
        rows = conn.execute(
            "SELECT id FROM set_item WHERE set_id = ? ORDER BY ord, id",
            (sid,),
        ).fetchall()
        for index, row in enumerate(rows, start=1):
            conn.execute("UPDATE set_item SET ord = ? WHERE id = ?", (-index, row["id"]))
        conn.execute("UPDATE set_item SET ord = -ord WHERE set_id = ?", (sid,))
        return {"ok": True}


@router.patch("/sets/{sid}/items/{item_id}")
def set_item_points(sid: int, item_id: int, body: ItemPointsIn):
    """세트 안에서만 배점을 바꾼다(문항 자체의 기본 배점은 그대로).

    같은 문항이 중간고사에선 3점, 기말에선 4점일 수 있다.
    """
    with db.transaction() as conn:
        conn.execute("UPDATE set_item SET points=? WHERE id=? AND set_id=?",
                     (body.points, item_id, sid))
        return {"ok": True}


@router.put("/sets/{sid}/order")
def reorder_set(sid: int, body: ReorderIn):
    """드래그 배열 결과 저장 — question_ids 순서대로 ord 재부여."""
    with db.transaction() as conn:
        current = [
            row["question_id"]
            for row in conn.execute(
                "SELECT question_id FROM set_item WHERE set_id = ? ORDER BY ord, id",
                (sid,),
            ).fetchall()
        ]
        requested = body.question_ids
        if len(requested) != len(set(requested)) or set(requested) != set(current):
            raise HTTPException(409, "세트의 모든 문항을 중복 없이 한 번씩 보내야 합니다.")
        conn.execute("UPDATE set_item SET ord = -ord WHERE set_id = ?", (sid,))
        for i, qid in enumerate(body.question_ids, start=1):
            conn.execute("UPDATE set_item SET ord = ? WHERE set_id = ? AND question_id = ?",
                         (i, sid, qid))
        return {"ok": True}


@router.get("/sets/{sid}/check")
def check_set_api(sid: int, target: float | None = None):
    """세트 검토. target 을 주지 않으면 세트에 저장된 만점을 쓴다."""
    conn = db.connect()
    try:
        s = conn.execute("SELECT * FROM exam_set WHERE id = ?", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, "세트를 찾을 수 없습니다.")
        items = _set_items(conn, sid)
        return checklist.summarize(checklist.check_set(dict(s), items, target_total=target))
    finally:
        conn.close()


# ===== 제출 서류 =====
@router.get("/sets/{sid}/reports")
def set_reports(sid: int):
    """정답표 + 이원목적분류표. 화면 표시용 행 목록과 붙여넣기용 TSV 를 함께 낸다."""
    conn = db.connect()
    try:
        s = conn.execute("SELECT * FROM exam_set WHERE id = ?", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, "세트를 찾을 수 없습니다.")
        items = _set_items(conn, sid)
        rows = conn.execute(
            "SELECT s.code, s.text, u.name AS unit_name FROM standard s "
            "LEFT JOIN unit u ON u.unit_no = s.unit_no").fetchall()
        unit_of_code = {r["code"]: (r["unit_name"] or "") for r in rows}
        std_text = {r["code"]: r["text"] for r in rows}

        key = reports.answer_key(items)
        bp = reports.blueprint(items, unit_of_code=unit_of_code, standard_texts=std_text)
        return {
            "set": dict(s),
            "answer_key": {**key, "tsv": reports.to_tsv(key)},
            "blueprint": {**bp, "tsv": reports.to_tsv(bp)},
            "behaviors": reports.BEHAVIORS,
            "origins": reports.ORIGINS,
        }
    finally:
        conn.close()


@router.get("/sets/{sid}/export")
def export_set(sid: int):
    """세트 → hwppalette 시험문제 문법 마크다운."""
    conn = db.connect()
    try:
        set_row = conn.execute("SELECT layout_style FROM exam_set WHERE id=?", (sid,)).fetchone()
        items = _set_items(conn, sid)
        if not items:
            return {"markdown": "", "count": 0}
        pairs = [(it["question"], it["choices"]) for it in items]
        layout_style = dict(set_row).get("layout_style", "school") if set_row else "school"
        return {"markdown": export_palette.set_to_markdown(
            pairs, layout_style=layout_style), "count": len(pairs)}
    finally:
        conn.close()


@router.get("/questions/{qid}/export")
def export_question(qid: int, layout_style: str = "school"):
    conn = db.connect()
    try:
        q, ch = _load_question(conn, qid)
        if not q:
            raise HTTPException(404, "문항을 찾을 수 없습니다.")
        return {"markdown": export_palette.question_to_palette(
            q, ch, num=1, layout_style=layout_style)}
    finally:
        conn.close()
