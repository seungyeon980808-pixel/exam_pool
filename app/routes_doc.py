"""근거 문서 API — PDF 폴더 인덱싱·검색·페이지 미리보기."""
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
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
    if not os.path.isdir(body.folder):
        raise HTTPException(400, f"폴더를 찾을 수 없습니다: {body.folder}")
    return pdf_indexer.index_folder(body.folder, doc_type=body.doc_type)


@router.post("/pick-folder")
def pick_folder():
    """OS 폴더 선택 대화상자를 띄운다 (로컬 전용 앱이라 가능).

    브라우저는 보안상 폴더 경로를 알려주지 않으므로, 서버(내 PC)에서 창을 연다.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title="근거 문서(PDF) 폴더 선택")
        root.destroy()
        return {"folder": path or ""}
    except Exception as e:
        raise HTTPException(500, f"폴더 선택 창을 열 수 없습니다: {e}")


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: int):
    """근거 검색에서 제외한다(원본 파일은 그대로)."""
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
def search_evidence(q: str = "", limit: int = 60, doc_id: int = 0):
    """근거 검색. q 는 공백 또는 #해시태그로 여러 키워드(AND)."""
    return pdf_indexer.search(q, limit=limit, doc_id=doc_id or None)


@router.get("/documents/{doc_id}/page/{page_no}/image")
def page_image(doc_id: int, page_no: int, dpi: int = 110):
    """PDF 페이지 미리보기 PNG (하이라이트 없는 순수 지면).

    검색어와 무관하므로 같은 페이지는 항상 캐시가 맞는다 → 페이지 넘김이 빠르다.
    형광 표시는 /highlights 좌표를 화면에서 겹쳐 그린다.
    """
    try:
        png = pdf_indexer.render_page_png(doc_id, page_no, dpi=dpi)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"페이지를 그릴 수 없습니다: {e}")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


@router.get("/documents/{doc_id}/page/{page_no}/highlights")
def page_highlights(doc_id: int, page_no: int, q: str = "", dpi: int = 110):
    """검색어 위치 좌표. 못 찾은 검색어(misses)를 함께 알려 조용한 실패를 막는다.

    (06 보고서 2-2절: '일치율 100%인데 형광펜 0개'를 UI 가 표시할 수 있게)
    """
    try:
        return pdf_indexer.page_highlights(doc_id, page_no, pdf_indexer.terms_of(q), dpi=dpi)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"좌표를 계산할 수 없습니다: {e}")


@router.get("/documents/{doc_id}/page/{page_no}")
def get_page_text(doc_id: int, page_no: int):
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT p.text, d.title, d.file_path, "
            "  (SELECT MAX(page_no) FROM document_page WHERE document_id = ?) AS last_page "
            "FROM document_page p JOIN document d ON d.id = p.document_id "
            "WHERE p.document_id = ? AND p.page_no = ?", (doc_id, doc_id, page_no)).fetchone()
        if not row:
            raise HTTPException(404, "페이지를 찾을 수 없습니다.")
        return {"title": row["title"], "page_no": page_no, "text": row["text"],
                "last_page": row["last_page"], "file_path": row["file_path"]}
    finally:
        conn.close()


@router.post("/documents/{doc_id}/open")
def open_original(doc_id: int, page_no: int = 1):
    """원본 PDF 를 기본 뷰어로 연다 (DocFinder 의 '원본 열기')."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT file_path FROM document WHERE id = ?", (doc_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    try:
        os.startfile(row["file_path"])  # Windows
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"원본을 열 수 없습니다: {e}")


@router.get("/documents/{doc_id}/page/{page_no}/items")
def page_items(doc_id: int, page_no: int, q: str = "", dpi: int = 110):
    """기출 페이지의 문항 목록 (+ 검색어가 어느 문항에 있는지)."""
    try:
        return pdf_indexer.page_items(doc_id, page_no, pdf_indexer.terms_of(q), dpi=dpi)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"문항을 찾을 수 없습니다: {e}")


@router.get("/documents/{doc_id}/page/{page_no}/item/{num}/image")
def item_image(doc_id: int, page_no: int, num: int, dpi: int = 120):
    """문항 하나만 잘라낸 PNG — 문제 하나가 화면에 딱 들어간다."""
    try:
        png = pdf_indexer.render_item_png(doc_id, page_no, num, dpi=dpi)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"문항을 그릴 수 없습니다: {e}")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


# ===== 기출 스크랩 (참고 문항 + 메모) =====
class RefIn(BaseModel):
    document_id: int
    doc_title: str = ""
    page_no: int
    item_num: int
    note: str = ""
    tags: str = ""
    question_id: int | None = None


class RefPatch(BaseModel):
    note: str | None = None
    tags: str | None = None


@router.get("/exam-refs")
def list_refs(q: str = ""):
    conn = db.connect()
    try:
        sql = "SELECT * FROM exam_ref WHERE 1=1"
        args = []
        if q:
            sql += " AND (doc_title LIKE ? OR note LIKE ? OR tags LIKE ?)"
            args += [f"%{q}%"] * 3
        sql += " ORDER BY id DESC"
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


@router.post("/exam-refs")
def create_ref(r: RefIn):
    """이미 담은 문항이면 메모만 갱신한다(중복 방지)."""
    conn = db.connect()
    try:
        old = conn.execute(
            "SELECT id FROM exam_ref WHERE document_id=? AND page_no=? AND item_num=?",
            (r.document_id, r.page_no, r.item_num)).fetchone()
        if old:
            if r.note.strip():
                conn.execute("UPDATE exam_ref SET note=? WHERE id=?", (r.note.strip(), old["id"]))
                conn.commit()
            return {"id": old["id"], "existed": True}
        cur = conn.execute(
            "INSERT INTO exam_ref (document_id, doc_title, page_no, item_num, note, tags, question_id) "
            "VALUES (?,?,?,?,?,?,?)",
            (r.document_id, r.doc_title.strip(), r.page_no, r.item_num,
             r.note.strip(), r.tags.strip(), r.question_id))
        conn.commit()
        return {"id": cur.lastrowid, "existed": False}
    finally:
        conn.close()


@router.patch("/exam-refs/{ref_id}")
def update_ref(ref_id: int, p: RefPatch):
    conn = db.connect()
    try:
        if p.note is not None:
            conn.execute("UPDATE exam_ref SET note=? WHERE id=?", (p.note.strip(), ref_id))
        if p.tags is not None:
            conn.execute("UPDATE exam_ref SET tags=? WHERE id=?", (p.tags.strip(), ref_id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/exam-refs/{ref_id}")
def delete_ref(ref_id: int):
    conn = db.connect()
    try:
        conn.execute("DELETE FROM exam_ref WHERE id=?", (ref_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
