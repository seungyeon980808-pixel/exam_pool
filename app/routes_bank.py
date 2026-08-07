"""명제 Pool API — 명제·거짓변형·근거."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import db

router = APIRouter(prefix="/api")


# ===== 명제 =====
class PropIn(BaseModel):
    text: str
    standard_code: str
    unit_no: int | None = None
    tags: str = ""
    note: str = ""


@router.get("/propositions")
def list_propositions(standard: str = "", unit: int | None = None, q: str = ""):
    sql = """
        SELECT p.id, p.text, p.standard_code, p.unit_no, p.tags,
               p.class_verified, p.note, p.created_at, u.name AS unit_name,
               (SELECT COUNT(*) FROM evidence e WHERE e.proposition_id = p.id) AS ev_count,
               (SELECT COUNT(*) FROM false_variant v WHERE v.proposition_id = p.id) AS var_count
        FROM proposition p LEFT JOIN unit u ON u.unit_no = p.unit_no
        WHERE 1=1
    """
    args = []
    if standard:
        sql += " AND p.standard_code = ?"; args.append(standard)
    if unit:
        sql += " AND p.unit_no = ?"; args.append(unit)
    if q:
        sql += " AND p.text LIKE ?"; args.append(f"%{q}%")
    sql += " ORDER BY p.id DESC"
    conn = db.connect()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


@router.post("/propositions")
def create_proposition(p: PropIn):
    conn = db.connect()
    try:
        unit_no = p.unit_no
        if unit_no is None:
            row = conn.execute("SELECT unit_no FROM standard WHERE code = ?",
                               (p.standard_code,)).fetchone()
            unit_no = row["unit_no"] if row else None
        cur = conn.execute(
            "INSERT INTO proposition (text, standard_code, unit_no, tags, note) VALUES (?,?,?,?,?)",
            (p.text.strip(), p.standard_code, unit_no, p.tags.strip(), p.note.strip()))
        conn.commit()
        return {"id": cur.lastrowid}
    finally:
        conn.close()


@router.delete("/propositions/{prop_id}")
def delete_proposition(prop_id: int):
    conn = db.connect()
    try:
        exists = conn.execute("SELECT 1 FROM proposition WHERE id = ?", (prop_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "명제를 찾을 수 없습니다.")
        conn.execute("DELETE FROM proposition WHERE id = ?", (prop_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.get("/propositions/{prop_id}")
def get_proposition(prop_id: int):
    conn = db.connect()
    try:
        p = conn.execute(
            "SELECT p.*, u.name AS unit_name, s.text AS standard_text "
            "FROM proposition p LEFT JOIN unit u ON u.unit_no = p.unit_no "
            "LEFT JOIN standard s ON s.code = p.standard_code WHERE p.id = ?",
            (prop_id,)).fetchone()
        if not p:
            raise HTTPException(404, "명제를 찾을 수 없습니다.")
        variants = conn.execute(
            "SELECT * FROM false_variant WHERE proposition_id = ? ORDER BY id", (prop_id,)).fetchall()
        evidence = conn.execute(
            "SELECT * FROM evidence WHERE proposition_id = ? ORDER BY id", (prop_id,)).fetchall()
        return {"proposition": dict(p),
                "variants": [dict(v) for v in variants],
                "evidence": [dict(e) for e in evidence]}
    finally:
        conn.close()


@router.patch("/propositions/{prop_id}/class-verified")
def set_class_verified(prop_id: int, value: bool = True):
    conn = db.connect()
    try:
        conn.execute("UPDATE proposition SET class_verified = ? WHERE id = ?",
                     (1 if value else 0, prop_id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ===== 거짓 변형 (오답) =====
DISTORTIONS = ["수치·정도 변경", "주체 바꿈", "인과 역전", "조건 삭제", "개념 혼동"]


class VariantIn(BaseModel):
    proposition_id: int
    text: str
    distortion: str
    note: str = ""


@router.get("/distortions")
def get_distortions():
    return DISTORTIONS


@router.post("/variants")
def create_variant(v: VariantIn):
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO false_variant (proposition_id, text, distortion, note) VALUES (?,?,?,?)",
            (v.proposition_id, v.text.strip(), v.distortion, v.note.strip()))
        conn.commit()
        return {"id": cur.lastrowid}
    finally:
        conn.close()


@router.delete("/variants/{vid}")
def delete_variant(vid: int):
    conn = db.connect()
    try:
        exists = conn.execute("SELECT 1 FROM false_variant WHERE id = ?", (vid,)).fetchone()
        if not exists:
            raise HTTPException(404, "오답 변형을 찾을 수 없습니다.")
        conn.execute("DELETE FROM false_variant WHERE id = ?", (vid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ===== 근거 =====
class EvidenceIn(BaseModel):
    proposition_id: int
    source_type: str = "교과서"
    source_label: str
    quote: str
    document_page_id: int | None = None


@router.post("/evidence")
def create_evidence(e: EvidenceIn):
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO evidence (proposition_id, source_type, source_label, quote, document_page_id) "
            "VALUES (?,?,?,?,?)",
            (e.proposition_id, e.source_type, e.source_label.strip(), e.quote.strip(),
             e.document_page_id))
        conn.commit()
        return {"id": cur.lastrowid}
    finally:
        conn.close()


@router.delete("/evidence/{eid}")
def delete_evidence(eid: int):
    conn = db.connect()
    try:
        exists = conn.execute("SELECT 1 FROM evidence WHERE id = ?", (eid,)).fetchone()
        if not exists:
            raise HTTPException(404, "근거를 찾을 수 없습니다.")
        conn.execute("DELETE FROM evidence WHERE id = ?", (eid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
