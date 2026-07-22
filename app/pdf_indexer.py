"""PDF 폴더 인덱싱 + FTS5 근거 검색.

DocFinder 로직 재구현: 폴더 지정 → 하위 폴더 재귀 스캔 → PyMuPDF 로 페이지별
텍스트 추출 → SQLite FTS5 색인. 검색은 bm25() 랭킹.
2026-07-22 실측: 교과서326p+교육과정280p 인덱싱 7.9초, 검색 2ms.

한글 없이(실제 PDF 없이) 도는 부분은 tests/ 에서 검증한다.
"""
import os
import sqlite3
import threading
from collections import OrderedDict

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
    close_all_docs()   # 캐시가 잡고 있는 파일 잠금을 먼저 푼다
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
            # document_id 가 음수면 수업 기록이다 (lessons.py 참고).
            # PDF 페이지가 아니므로 이미지 렌더링 대신 본문을 보여줘야 한다.
            lesson = r["document_id"] < 0
            items.append({
                "doc_title": r["doc_title"],
                "page_no": r["page_no"],
                "document_id": r["document_id"],
                "kind": "수업" if lesson else "문서",
                "source_label": (f"{r['doc_title']} #{r['page_no']}" if lesson
                                 else f"{r['doc_title']} p.{r['page_no']}"),
                "snippet": snip,
                "match_pct": max(1, min(100, pct)),
            })
        return {"total": total, "terms": terms, "items": items}
    finally:
        conn.close()


# ===== 문서 핸들 캐시 (렌더링 속도) =====
# 페이지를 넘길 때마다 fitz.open() 을 반복하면 큰 PDF 는 여는 것만으로 수백 ms 가 든다.
# 열어둔 문서를 재사용한다. PyMuPDF 문서 객체는 스레드 안전이 아니므로 락으로 보호한다.
_doc_lock = threading.RLock()
_doc_cache: "OrderedDict[str, tuple[float, object]]" = OrderedDict()
_DOC_CACHE_MAX = 4

_png_lock = threading.Lock()
_png_cache: "OrderedDict[tuple, bytes]" = OrderedDict()
_PNG_CACHE_MAX = 40

# 키워드별 형광 색 (여러 태그를 구분해 보여준다)
HL_PALETTE = [
    (1.00, 0.85, 0.30),   # 노랑
    (0.55, 0.85, 1.00),   # 하늘
    (0.65, 1.00, 0.65),   # 연두
    (1.00, 0.72, 0.86),   # 분홍
    (0.82, 0.76, 1.00),   # 보라
]


def _get_doc(path: str):
    """열어둔 PDF 를 재사용한다. 파일이 바뀌면 다시 연다."""
    import fitz

    mtime = os.path.getmtime(path)
    with _doc_lock:
        cached = _doc_cache.get(path)
        if cached and cached[0] == mtime:
            _doc_cache.move_to_end(path)
            return cached[1]
        if cached:
            try:
                cached[1].close()
            except Exception:
                pass
            _doc_cache.pop(path, None)
        while len(_doc_cache) >= _DOC_CACHE_MAX:
            _, (_, old) = _doc_cache.popitem(last=False)
            try:
                old.close()
            except Exception:
                pass
        doc = fitz.open(path)
        _doc_cache[path] = (mtime, doc)
        return doc


def close_all_docs() -> None:
    """캐시된 문서를 모두 닫는다 (재색인 전에 파일 잠금을 푼다)."""
    with _doc_lock:
        for _, (_, doc) in _doc_cache.items():
            try:
                doc.close()
            except Exception:
                pass
        _doc_cache.clear()
    with _png_lock:
        _png_cache.clear()


# ===== 하이라이트: 색인과 같은 규칙으로 글자를 찾는다 =====
def highlight_rects(page, terms: list[str]) -> tuple[dict, list[str]]:
    """검색어가 실제로 어느 글자에 있는지 좌표를 찾는다.

    **색인(FTS5)과 같은 규칙을 쓰는 것이 핵심이다.**
    FTS5 는 unicode61 로 공백 분리 후 prefix 로 맞히므로, 여기서도 `get_text("words")`
    (같은 공백 분리)에서 부분일치로 찾는다. `search_for()` 는 글리프 단위 탐색이라
    색인과 경로가 갈려 '일치율 100%인데 형광펜 0개'가 날 수 있었다(06 보고서 2-2절).

    한 단어가 여러 조각으로 쪼개진 경우(한글 PDF 에서 흔함)를 위해 줄 단위 폴백을 둔다.
    반환: ({검색어: [Rect]}, 못 찾은 검색어 목록)
    """
    import fitz

    words = page.get_text("words")  # (x0,y0,x1,y1,word,block,line,word_no)
    hits: dict[str, list] = {}
    misses: list[str] = []
    for t in terms:
        tl = t.lower()
        rects = [fitz.Rect(w[:4]) for w in words if tl in w[4].lower()]
        if not rects:
            rects = _line_fallback(words, tl)   # 쪼개진 단어 구제
        if rects:
            hits[t] = rects
        else:
            misses.append(t)
    return hits, misses


def _line_fallback(words, term_lower: str) -> list:
    """줄 안의 조각을 이어붙여 찾는다. '징 계 기 준' 처럼 쪼개진 단어 대응."""
    import fitz
    from collections import defaultdict

    lines = defaultdict(list)
    for w in words:
        lines[(w[5], w[6])].append(w)

    target = term_lower.replace(" ", "")
    out = []
    for ws in lines.values():
        ws.sort(key=lambda w: w[7])
        joined = "".join(w[4] for w in ws).lower()
        start = joined.find(target)
        if start < 0:
            continue
        end = start + len(target)
        pos, rect = 0, None
        for w in ws:
            wlen = len(w[4])
            if pos < end and pos + wlen > start:      # 이 조각이 검색어에 걸친다
                r = fitz.Rect(w[:4])
                rect = r if rect is None else (rect | r)
            pos += wlen
        if rect is not None:
            out.append(rect)
    return out


def _doc_path(doc_id: int) -> str:
    conn = connect()
    try:
        row = conn.execute("SELECT file_path FROM document WHERE id = ?", (doc_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise FileNotFoundError("문서를 찾을 수 없습니다.")
    return row["file_path"]


def render_page_png(doc_id: int, page_no: int, dpi: int = 110) -> bytes:
    """PDF 페이지를 PNG 로 렌더링한다 (하이라이트 없는 순수 지면).

    **형광 표시를 여기서 그리지 않는 이유**: `draw_rect` 는 페이지 콘텐츠를 실제로 바꾸므로
    캐시된 문서를 재사용하면 표시가 눌어붙는다. 좌표만 따로 내보내 화면에서 겹치면
    원본이 안전하고, 검색어가 달라도 같은 이미지를 재사용해 훨씬 빠르다.
    """
    key = (doc_id, page_no, dpi)
    with _png_lock:
        cached = _png_cache.get(key)
        if cached is not None:
            _png_cache.move_to_end(key)
            return cached

    path = _doc_path(doc_id)
    with _doc_lock:                       # 문서 객체는 스레드 안전이 아니다
        doc = _get_doc(path)
        if page_no < 1 or page_no > doc.page_count:
            raise FileNotFoundError("페이지 범위를 벗어났습니다.")
        png = doc[page_no - 1].get_pixmap(dpi=dpi).tobytes("png")

    with _png_lock:
        _png_cache[key] = png
        while len(_png_cache) > _PNG_CACHE_MAX:
            _png_cache.popitem(last=False)
    return png


def page_highlights(doc_id: int, page_no: int, terms: list[str], dpi: int = 110) -> dict:
    """검색어가 있는 위치를 화면 픽셀 좌표로 돌려준다.

    반환: {"boxes": [{term, color_idx, x, y, w, h}], "misses": [...],
           "hits": {검색어: 개수}, "page_w": .., "page_h": ..}
    """
    path = _doc_path(doc_id)
    zoom = dpi / 72.0
    with _doc_lock:
        doc = _get_doc(path)
        if page_no < 1 or page_no > doc.page_count:
            raise FileNotFoundError("페이지 범위를 벗어났습니다.")
        page = doc[page_no - 1]
        hits, misses = highlight_rects(page, terms)
        pw, ph = page.rect.width * zoom, page.rect.height * zoom

    boxes = []
    for i, t in enumerate(terms):
        for r in hits.get(t, []):
            boxes.append({
                "term": t, "color_idx": i % len(HL_PALETTE),
                "x": round(r.x0 * zoom, 1), "y": round(r.y0 * zoom, 1),
                "w": round((r.x1 - r.x0) * zoom, 1), "h": round((r.y1 - r.y0) * zoom, 1),
            })
    boxes.sort(key=lambda b: (b["y"], b["x"]))
    return {"boxes": boxes, "misses": misses,
            "hits": {t: len(hits.get(t, [])) for t in terms},
            "page_w": round(pw), "page_h": round(ph)}


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


# ===== 기출: 문항 단위로 잘라 보기 =====
def page_items(doc_id: int, page_no: int, terms: list[str] | None = None,
               dpi: int = 110) -> dict:
    """페이지의 문항 목록. 검색어가 있으면 어느 문항에 들어있는지도 표시한다.

    반환: {"items":[{num, x,y,w,h, hits:{단어:개수}, has_hit}], "page_w","page_h"}
    문항을 못 찾으면 items 가 빈 목록 → 호출 쪽이 페이지 전체를 쓰면 된다.
    """
    from . import exam_items

    path = _doc_path(doc_id)
    zoom = dpi / 72.0
    terms = terms or []
    with _doc_lock:
        doc = _get_doc(path)
        if page_no < 1 or page_no > doc.page_count:
            raise FileNotFoundError("페이지 범위를 벗어났습니다.")
        page = doc[page_no - 1]
        found = exam_items.detect_items(page)
        hit_map, _ = highlight_rects(page, terms) if terms else ({}, [])
        pw, ph = page.rect.width * zoom, page.rect.height * zoom

    out = []
    for it in found:
        hits = {}
        for t, rects in hit_map.items():
            n = sum(1 for r in rects
                    if it["x0"] <= r.x0 <= it["x1"] and it["y0"] <= r.y0 <= it["y1"])
            if n:
                hits[t] = n
        out.append({
            "num": it["num"],
            "x": round(it["x0"] * zoom, 1), "y": round(it["y0"] * zoom, 1),
            "w": round((it["x1"] - it["x0"]) * zoom, 1),
            "h": round((it["y1"] - it["y0"]) * zoom, 1),
            "hits": hits, "has_hit": bool(hits),
        })
    return {"items": out, "page_w": round(pw), "page_h": round(ph)}


def render_item_png(doc_id: int, page_no: int, num: int, dpi: int = 120) -> bytes:
    """문항 하나만 잘라 PNG 로. (문제 하나가 화면에 딱 들어가게)"""
    import fitz

    from . import exam_items

    key = (doc_id, page_no, "item", num, dpi)
    with _png_lock:
        c = _png_cache.get(key)
        if c is not None:
            _png_cache.move_to_end(key)
            return c

    path = _doc_path(doc_id)
    with _doc_lock:
        doc = _get_doc(path)
        if page_no < 1 or page_no > doc.page_count:
            raise FileNotFoundError("페이지 범위를 벗어났습니다.")
        page = doc[page_no - 1]
        found = [it for it in exam_items.detect_items(page) if it["num"] == num]
        if not found:
            raise FileNotFoundError("문항을 찾을 수 없습니다.")
        it = found[0]
        clip = fitz.Rect(it["x0"], it["y0"], it["x1"], it["y1"])
        png = page.get_pixmap(clip=clip, dpi=dpi).tobytes("png")

    with _png_lock:
        _png_cache[key] = png
        while len(_png_cache) > _PNG_CACHE_MAX:
            _png_cache.popitem(last=False)
    return png
