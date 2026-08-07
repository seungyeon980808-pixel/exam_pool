"""수업 기록 API (Phase 2).

등록하면 곧바로 근거 검색 인덱스에 들어간다 — 교과서와 한 화면에서 함께 검색된다.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import db, lessons, pdf_indexer

router = APIRouter(prefix="/api")


class LessonIn(BaseModel):
    date: str
    class_name: str = ""
    transcript: str = ""
    summary: str = ""


@router.get("/lessons")
def list_lessons(q: str = ""):
    conn = db.connect()
    try:
        sql = ("SELECT id, date, class_name, summary, indexed_at, "
               "  LENGTH(transcript) AS chars FROM lesson WHERE 1=1")
        args = []
        if q:
            sql += " AND (transcript LIKE ? OR summary LIKE ? OR class_name LIKE ?)"
            args += [f"%{q}%"] * 3
        sql += " ORDER BY date DESC, id DESC"
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


@router.get("/lessons/{lid}")
def get_lesson(lid: int):
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM lesson WHERE id = ?", (lid,)).fetchone()
        if not row:
            raise HTTPException(404, "수업 기록을 찾을 수 없습니다.")
        return dict(row)
    finally:
        conn.close()


@router.post("/lessons")
def create_lesson(body: LessonIn):
    if not body.date.strip():
        raise HTTPException(400, "수업 날짜를 입력하세요.")
    conn = db.connect()
    try:
        pdf_indexer.ensure_fts(conn)
        cur = conn.execute(
            "INSERT INTO lesson (date, class_name, transcript, summary) VALUES (?,?,?,?)",
            (body.date.strip(), body.class_name.strip(), body.transcript, body.summary.strip()))
        lid = cur.lastrowid
        chunks = lessons.index_lesson(conn, lid)
        conn.commit()
        return {"id": lid, "chunks": chunks}
    finally:
        conn.close()


@router.put("/lessons/{lid}")
def update_lesson(lid: int, body: LessonIn):
    conn = db.connect()
    try:
        exists = conn.execute("SELECT 1 FROM lesson WHERE id = ?", (lid,)).fetchone()
        if not exists:
            raise HTTPException(404, "수업 기록을 찾을 수 없습니다.")
        pdf_indexer.ensure_fts(conn)
        conn.execute("UPDATE lesson SET date=?, class_name=?, transcript=?, summary=? WHERE id=?",
                     (body.date.strip(), body.class_name.strip(), body.transcript,
                      body.summary.strip(), lid))
        chunks = lessons.index_lesson(conn, lid)
        conn.commit()
        return {"ok": True, "chunks": chunks}
    finally:
        conn.close()


@router.delete("/lessons/{lid}")
def delete_lesson(lid: int):
    conn = db.connect()
    try:
        exists = conn.execute("SELECT 1 FROM lesson WHERE id = ?", (lid,)).fetchone()
        if not exists:
            raise HTTPException(404, "수업 기록을 찾을 수 없습니다.")
        pdf_indexer.ensure_fts(conn)
        lessons.unindex(conn, lid)
        conn.execute("DELETE FROM lesson WHERE id = ?", (lid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.post("/lessons/reindex")
def reindex_lessons():
    conn = db.connect()
    try:
        pdf_indexer.ensure_fts(conn)
        n = lessons.reindex_all(conn)
        conn.commit()
        return {"chunks": n}
    finally:
        conn.close()


@router.get("/lesson-chunk/{lid}/{chunk_no}")
def get_chunk(lid: int, chunk_no: int):
    """검색 결과에서 수업 기록 원문 조각을 펴 볼 때."""
    conn = db.connect()
    try:
        out = lessons.chunk_text(conn, lid, chunk_no)
        if not out:
            raise HTTPException(404, "수업 기록을 찾을 수 없습니다.")
        return out
    finally:
        conn.close()
