"""ExamPool — FastAPI 진입점.

로컬 단독 서버. /api/* 는 JSON API, 나머지는 static 화면.
Phase 1: 명제 은행(조회·등록·변형·근거)부터 동작시킨다.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .paths import BASE_DIR, STATIC_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="ExamPool", lifespan=lifespan)


# ===== 성취기준 트리 =====
@app.get("/api/standards")
def get_standards():
    conn = db.connect()
    try:
        units = conn.execute("SELECT unit_no, name FROM unit ORDER BY unit_no").fetchall()
        stds = conn.execute(
            "SELECT code, unit_no, seq, text FROM standard ORDER BY unit_no, seq"
        ).fetchall()
    finally:
        conn.close()
    by_unit = {}
    for s in stds:
        by_unit.setdefault(s["unit_no"], []).append(dict(s))
    return [
        {"unit_no": u["unit_no"], "name": u["name"], "standards": by_unit.get(u["unit_no"], [])}
        for u in units
    ]


# ===== 명제 조회 =====
@app.get("/api/propositions")
def list_propositions(standard: str = "", unit: int = 0, q: str = ""):
    sql = """
        SELECT p.id, p.text, p.standard_code, p.unit_no, p.tags,
               p.class_verified, p.note, p.created_at,
               u.name AS unit_name,
               (SELECT COUNT(*) FROM evidence e WHERE e.proposition_id = p.id) AS ev_count,
               (SELECT COUNT(*) FROM false_variant v WHERE v.proposition_id = p.id) AS var_count
        FROM proposition p
        LEFT JOIN unit u ON u.unit_no = p.unit_no
        WHERE 1=1
    """
    args = []
    if standard:
        sql += " AND p.standard_code = ?"
        args.append(standard)
    if unit:
        sql += " AND p.unit_no = ?"
        args.append(unit)
    if q:
        sql += " AND p.text LIKE ?"
        args.append(f"%{q}%")
    sql += " ORDER BY p.id DESC"
    conn = db.connect()
    try:
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ===== 명제 등록 =====
class PropIn(BaseModel):
    text: str
    standard_code: str
    unit_no: int | None = None
    tags: str = ""
    note: str = ""


@app.post("/api/propositions")
def create_proposition(p: PropIn):
    conn = db.connect()
    try:
        unit_no = p.unit_no
        if unit_no is None:
            row = conn.execute(
                "SELECT unit_no FROM standard WHERE code = ?", (p.standard_code,)
            ).fetchone()
            unit_no = row["unit_no"] if row else None
        cur = conn.execute(
            "INSERT INTO proposition (text, standard_code, unit_no, tags, note) "
            "VALUES (?, ?, ?, ?, ?)",
            (p.text.strip(), p.standard_code, unit_no, p.tags.strip(), p.note.strip()),
        )
        conn.commit()
        return {"id": cur.lastrowid}
    finally:
        conn.close()


@app.delete("/api/propositions/{prop_id}")
def delete_proposition(prop_id: int):
    conn = db.connect()
    try:
        conn.execute("DELETE FROM proposition WHERE id = ?", (prop_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ===== 정적 화면 =====
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/assets", StaticFiles(directory=str(BASE_DIR / "assets")), name="assets")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))
