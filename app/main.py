"""ExamPool — FastAPI 진입점.

로컬 단독 서버. 외부 통신 없음(완전 오프라인).
라우트는 기능별로 분리: routes_bank(명제 Pool) / routes_question(문항·세트) / routes_doc(근거 문서)
"""
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import (backup, db, routes_bank, routes_config, routes_doc, routes_lesson,
               routes_question)
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
    # 스키마가 준비된 뒤에 백업한다. 하루 1회만 뜨므로 켤 때마다 느려지지 않는다.
    backup.auto_backup_if_due()
    yield


app = FastAPI(title="ExamPool", lifespan=lifespan)


# 로컬 단독 서버라 캐시로 얻을 이득이 없다. 오히려 화면을 고쳐도 브라우저가 예전 js/css 를
# 계속 쓰는 사고가 잦아, 정적 파일은 매번 새로 받게 한다.
@app.middleware("http")
async def no_cache_static(request, call_next):
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.startswith(("/static/", "/assets/")):
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


app.include_router(routes_bank.router)
app.include_router(routes_question.router)
app.include_router(routes_doc.router)
app.include_router(routes_lesson.router)
app.include_router(routes_config.router)


# ===== 성취기준 트리 =====
@app.get("/api/standards")
def get_standards():
    """단원 평면 목록. 각 단원에 과목·교육과정 구분이 붙어 있어 화면에서 접어 볼 수 있다."""
    conn = db.connect()
    try:
        units = conn.execute(
            "SELECT u.unit_no, u.name, u.local_no, u.inquiry, u.consider, "
            "       s.name AS subject, s.track, s.grade_band, s.ord "
            "FROM unit u LEFT JOIN subject s ON s.id = u.subject_id "
            "ORDER BY COALESCE(s.ord, 0), u.unit_no").fetchall()
        stds = conn.execute(
            "SELECT code, unit_no, seq, text, explain FROM standard "
            "ORDER BY unit_no, seq").fetchall()
    finally:
        conn.close()
    by_unit = {}
    for s in stds:
        by_unit.setdefault(s["unit_no"], []).append(dict(s))
    out = []
    for u in units:
        d = dict(u)
        d["local_no"] = u["local_no"] or u["unit_no"]
        d["inquiry"] = json.loads(u["inquiry"] or "[]")
        d["consider"] = json.loads(u["consider"] or "[]")
        d["standards"] = by_unit.get(u["unit_no"], [])
        out.append(d)
    return out


@app.get("/api/subjects")
def get_subjects():
    """교육과정 구분 → 과목 목록. 환경설정의 드릴다운이 이 순서를 따른다."""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.track, s.grade_band, s.ord, "
            "       (SELECT COUNT(*) FROM standard st WHERE st.subject_id = s.id) AS standard_count, "
            "       (SELECT COUNT(*) FROM unit u WHERE u.subject_id = s.id) AS unit_count "
            "FROM subject s ORDER BY s.ord").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


@app.get("/api/subject")
def get_subject():
    """현재 적재된 교육과정 정보(확장성: 과목 교체 시 이 값이 바뀐다)."""
    conn = db.connect()
    try:
        meta = conn.execute("SELECT * FROM meta WHERE key = 'subject'").fetchone()
        n = conn.execute("SELECT COUNT(*) AS c FROM standard").fetchone()["c"]
        subj = conn.execute("SELECT COUNT(*) AS c FROM subject").fetchone()["c"]
    finally:
        conn.close()
    return {"subject": meta["value"] if meta else "중학교 과학 (2022 개정)",
            "standard_count": n, "subject_count": subj}


# ===== 정적 화면 =====
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/assets", StaticFiles(directory=str(BASE_DIR / "assets")), name="assets")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))
