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


def terms_of(q: str) -> list[str]:
    """검색어(공백/해시태그 구분) → 키워드 목록. '#빛 #굴절' 도 받는다."""
    cleaned = q.replace("#", " ")
    return [t for t in "".join(c if c.isalnum() else " " for c in cleaned).split() if t]


def search(q: str, limit: int = 60, doc_id: int | None = None) -> dict:
    """근거 검색.

    반환: {"total": 전체 일치 수, "terms": [키워드], "items": [...]}
    각 item 에는 일치율(match_pct)이 붙는다 — 최고점을 100%로 한 상대 환산(DocFinder 방식).
    """
    terms = terms_of(q)
    if not terms:
        return {"total": 0, "terms": [], "items": []}
    conn = connect()
    try:
        ensure_fts(conn)
        match = " AND ".join(f'"{t}"*' for t in terms)
        sql = (
            "SELECT doc_title, page_no, document_id, "
            "  snippet(page_fts, 0, '[', ']', ' … ', 20) AS snippet, "
            "  bm25(page_fts) AS score "
            "FROM page_fts WHERE page_fts MATCH ?"
        )
        args: list = [match]
        if doc_id:
            sql += " AND document_id = ?"
            args.append(doc_id)
        sql += " ORDER BY score LIMIT ?"
        args.append(limit)
        rows = conn.execute(sql, args).fetchall()

        total = conn.execute(
            "SELECT COUNT(*) AS c FROM page_fts WHERE page_fts MATCH ?" +
            (" AND document_id = ?" if doc_id else ""),
            [match] + ([doc_id] if doc_id else []),
        ).fetchone()["c"]

        best = rows[0]["score"] if rows else -1
        items = []
        for r in rows:
            snip = " ".join((r["snippet"] or "").split())
            pct = round((r["score"] / best) * 100) if best else 100
            items.append({
                "doc_title": r["doc_title"],
                "page_no": r["page_no"],
                "document_id": r["document_id"],
                "source_label": f"{r['doc_title']} p.{r['page_no']}",
                "snippet": snip,
                "match_pct": max(1, min(100, pct)),
            })
        return {"total": total, "terms": terms, "items": items}
    finally:
        conn.close()


def render_page_png(doc_id: int, page_no: int, terms: list[str] | None = None,
                    dpi: int = 110) -> bytes:
    """PDF 페이지를 PNG 로 렌더링한다. 검색어가 있으면 형광 표시를 그린다.

    원본 파일은 건드리지 않는다(메모리에서만 그리고 저장하지 않음).
    """
    import fitz

    conn = connect()
    try:
        row = conn.execute("SELECT file_path FROM document WHERE id = ?", (doc_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise FileNotFoundError("문서를 찾을 수 없습니다.")

    doc = fitz.open(row["file_path"])
    try:
        page = doc[page_no - 1]
        for t in (terms or []):
            for rect in page.search_for(t):
                page.draw_rect(rect, color=None, fill=(1, 0.85, 0.3), fill_opacity=0.42,
                               overlay=True)
        return page.get_pixmap(dpi=dpi).tobytes("png")
    finally:
        doc.close()


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
