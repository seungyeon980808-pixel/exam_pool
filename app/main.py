"""ExamPool — FastAPI 진입점.

로컬 단독 서버. 외부 통신 없음(완전 오프라인).
라우트는 기능별로 분리: routes_bank(명제 은행) / routes_question(문항·세트) / routes_doc(근거 문서)
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, routes_bank, routes_doc, routes_question
from .paths import BASE_DIR, STATIC_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    conn = db.connect()
    try:
        from . import pdf_indexer
        pdf_indexer.ensure_fts(conn)
    finally:
        conn.close()
    yield


app = FastAPI(title="ExamPool", lifespan=lifespan)

app.include_router(routes_bank.router)
app.include_router(routes_question.router)
app.include_router(routes_doc.router)


# ===== 성취기준 트리 =====
@app.get("/api/standards")
def get_standards():
    conn = db.connect()
    try:
        units = conn.execute("SELECT unit_no, name FROM unit ORDER BY unit_no").fetchall()
        stds = conn.execute(
            "SELECT code, unit_no, seq, text FROM standard ORDER BY unit_no, seq").fetchall()
    finally:
        conn.close()
    by_unit = {}
    for s in stds:
        by_unit.setdefault(s["unit_no"], []).append(dict(s))
    return [{"unit_no": u["unit_no"], "name": u["name"],
             "standards": by_unit.get(u["unit_no"], [])} for u in units]


@app.get("/api/subject")
def get_subject():
    """현재 적재된 교육과정 정보(확장성: 과목 교체 시 이 값이 바뀐다)."""
    conn = db.connect()
    try:
        meta = conn.execute("SELECT * FROM meta WHERE key = 'subject'").fetchone()
        n = conn.execute("SELECT COUNT(*) AS c FROM standard").fetchone()["c"]
    finally:
        conn.close()
    return {"subject": meta["value"] if meta else "중학교 과학 (2022 개정)", "standard_count": n}


# ===== 정적 화면 =====
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/assets", StaticFiles(directory=str(BASE_DIR / "assets")), name="assets")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))
