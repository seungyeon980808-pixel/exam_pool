"""근거 문서 API — PDF 폴더 인덱싱·검색·문서 관리."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import db, pdf_indexer

router = APIRouter(prefix="/api")


class IndexIn(BaseModel):
    folder: str
    doc_type: str = "교과서"


@router.get("/documents")
def list_documents():
    return pdf_indexer.list_documents()


@router.post("/documents/index")
def index_folder(body: IndexIn):
    import os
    if not os.path.isdir(body.folder):
        raise HTTPException(400, f"폴더를 찾을 수 없습니다: {body.folder}")
    result = pdf_indexer.index_folder(body.folder, doc_type=body.doc_type)
    return result


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: int):
    """근거 검색에서 빼고 싶은 문서를 제거한다(원본 파일은 그대로)."""
    conn = db.connect()
    try:
        pdf_indexer.ensure_fts(conn)
        conn.execute("DELETE FROM page_fts WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM document WHERE id = ?", (doc_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.get("/evidence/search")
def search_evidence(q: str = "", limit: int = 20):
    """근거 검색 — 명제·문항 작성 화면에서 호출."""
    return pdf_indexer.search(q, limit=limit)


@router.get("/documents/{doc_id}/page/{page_no}")
def get_page_text(doc_id: int, page_no: int):
    """검색 결과에서 원문 전체를 확인할 때."""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT p.text, d.title FROM document_page p JOIN document d ON d.id = p.document_id "
            "WHERE p.document_id = ? AND p.page_no = ?", (doc_id, page_no)).fetchone()
        if not row:
            raise HTTPException(404, "페이지를 찾을 수 없습니다.")
        return {"title": row["title"], "page_no": page_no, "text": row["text"]}
    finally:
        conn.close()
