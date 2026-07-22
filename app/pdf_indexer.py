"""PDF 폴더 인덱싱 + FTS5 근거 검색.

DocFinder 로직 재구현: 폴더 지정 → 하위 폴더 재귀 스캔 → PyMuPDF 로 페이지별
텍스트 추출 → SQLite FTS5 색인. 검색은 bm25() 랭킹.
2026-07-22 실측: 교과서326p+교육과정280p 인덱싱 7.9초, 검색 2ms.

한글 없이(실제 PDF 없이) 도는 부분은 tests/ 에서 검증한다.
"""
import os
import sqlite3

from .db import connect

MIN_CHARS = 10  # 페이지당 이 미만이면 스캔본 추정 → 건너뜀


def ensure_fts(conn: sqlite3.Connection) -> None:
    """FTS5 가상 테이블 준비. document_page 를 external-content 로 미러링."""
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS page_fts USING fts5("
        "  body, doc_title UNINDEXED, page_no UNINDEXED, document_id UNINDEXED,"
        "  tokenize='unicode61'"
        ")"
    )
    conn.commit()


def collect_pdfs(root: str) -> list[str]:
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.lower().endswith(".pdf"):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def _extract(path: str):
    """PDF 1개 → [(page_no, text)]. PyMuPDF 사용."""
    import fitz  # PyMuPDF — 여기서 import 해 실제 검색을 안 쓰면 로딩도 안 되게

    pages = []
    doc = fitz.open(path)
    try:
        for i, page in enumerate(doc):
            text = page.get_text()
            if len(text.strip()) >= MIN_CHARS:
                pages.append((i + 1, text))
    finally:
        doc.close()
    return pages


def index_folder(root: str, doc_type: str = "교과서", progress=None) -> dict:
    """폴더를 인덱싱한다. 이미 등록된 파일(path 동일)은 갱신한다."""
    conn = connect()
    try:
        ensure_fts(conn)
        pdfs = collect_pdfs(root)
        n_docs, n_pages, skipped = 0, 0, []
        for i, path in enumerate(pdfs):
            title = os.path.splitext(os.path.basename(path))[0]
            if progress:
                progress(i + 1, len(pdfs), title)
            try:
                pages = _extract(path)
            except Exception as e:  # 열기 실패 문서는 건너뛰고 기록
                skipped.append({"path": path, "reason": str(e)})
                continue
            if not pages:
                skipped.append({"path": path, "reason": "텍스트 없음(스캔본 추정)"})
                continue

            # 기존 문서 제거 후 재등록 (재인덱싱)
            old = conn.execute("SELECT id FROM document WHERE file_path = ?", (path,)).fetchone()
            if old:
                conn.execute("DELETE FROM page_fts WHERE document_id = ?", (old["id"],))
                conn.execute("DELETE FROM document WHERE id = ?", (old["id"],))

            cur = conn.execute(
                "INSERT INTO document (title, doc_type, file_path, indexed_at) "
                "VALUES (?, ?, ?, datetime('now','localtime'))",
                (title, doc_type, path),
            )
            doc_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO document_page (document_id, page_no, text) VALUES (?, ?, ?)",
                [(doc_id, p, t) for p, t in pages],
            )
            conn.executemany(
                "INSERT INTO page_fts (body, doc_title, page_no, document_id) VALUES (?, ?, ?, ?)",
                [(t, title, p, doc_id) for p, t in pages],
            )
            n_docs += 1
            n_pages += len(pages)
        conn.commit()
        return {"documents": n_docs, "pages": n_pages, "skipped": skipped}
    finally:
        conn.close()


def _fts_query(q: str) -> str:
    """공백으로 나눈 키워드를 모두 포함(AND)하는 FTS5 질의로. 특수문자는 제거.

    **각 키워드에 prefix(*) 를 붙이는 것이 한국어 검색의 핵심이다.**
    unicode61 토크나이저는 공백 기준으로 자르므로 '빛은'이 통째로 한 토큰이 된다.
    조사가 붙는 한국어에서 '빛'으로 검색하면 정확 일치가 안 되므로 '빛'* 로 찾는다.
    (trigram 토크나이저는 3글자 미만 검색어를 못 써서 '빛' 같은 질의가 불가능하다.)
    """
    terms = [t for t in "".join(c if c.isalnum() else " " for c in q).split() if t]
    return " AND ".join(f'"{t}"*' for t in terms)


def search(q: str, limit: int = 20) -> list[dict]:
    """근거 검색. bm25() 오름차순(작을수록 관련 높음)."""
    if not q.strip():
        return []
    conn = connect()
    try:
        ensure_fts(conn)
        match = _fts_query(q)
        if not match:
            return []
        rows = conn.execute(
            "SELECT doc_title, page_no, document_id, "
            "  snippet(page_fts, 0, '[', ']', ' … ', 18) AS snippet, "
            "  bm25(page_fts) AS score "
            "FROM page_fts WHERE page_fts MATCH ? ORDER BY score LIMIT ?",
            (match, limit),
        ).fetchall()
        result = []
        for r in rows:
            snip = " ".join((r["snippet"] or "").split())
            result.append({
                "doc_title": r["doc_title"],
                "page_no": r["page_no"],
                "document_id": r["document_id"],
                "source_label": f"{r['doc_title']} p.{r['page_no']}",
                "snippet": snip,
            })
        return result
    finally:
        conn.close()


def list_documents() -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT d.id, d.title, d.doc_type, d.file_path, d.indexed_at, "
            "  (SELECT COUNT(*) FROM document_page p WHERE p.document_id = d.id) AS pages "
            "FROM document d ORDER BY d.id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
