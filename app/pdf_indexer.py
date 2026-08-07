"""문서 폴더 인덱싱 + FTS5 근거 검색.

DocFinder 로직 재구현: 폴더 지정 → 하위 폴더 재귀 스캔 → PyMuPDF(·anydoc) 로
페이지별 텍스트 추출 → SQLite FTS5 색인. 검색은 bm25() 랭킹.

지원 형식:
  - PDF: PyMuPDF (fitz) — 기존 경로, 하이라이트 렌더링 포함
  - DOCX·PPTX·XLSX·ODT·ODS·ODP·RTF·EPUB·CSV: anydoc (firecrawl-anydoc)
2026-07-22 실측: 교과서326p+교육과정280p 인덱싱 7.9초, 검색 2ms.
"""
import os
import sqlite3
import threading
from collections import OrderedDict

from .db import connect

MIN_CHARS = 10  # 페이지당 이 미만이면 의미 없는 페이지 → 건너뜀

# anydoc 지원 확장자 (PDF 제외)
_ANYDOC_EXTS = {".docx", ".pptx", ".xlsx", ".odt", ".ods", ".odp", ".rtf", ".epub", ".csv",
                ".doc", ".ppt", ".xls", ".docm", ".pptm", ".xlsm", ".xlsb", ".ppsx", ".ppsm"}
_PDF_EXTS = {".pdf"}
_ALL_EXTS = _PDF_EXTS | _ANYDOC_EXTS


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


def collect_docs(root: str) -> list[tuple[str, str]]:
    """폴더에서 모든 지원 문서를 수집. 반환: [(경로, 형식명)]. pdf는 제외."""
    out = []
    ext_map = {
        ".docx": "Word", ".doc": "Word", ".docm": "Word",
        ".pptx": "PPT", ".ppt": "PPT", ".pptm": "PPT", ".ppsx": "PPT", ".ppsm": "PPT",
        ".xlsx": "Excel", ".xls": "Excel", ".xlsm": "Excel", ".xlsb": "Excel",
        ".odt": "ODT", ".ods": "ODS", ".odp": "ODP",
        ".rtf": "RTF", ".epub": "EPUB", ".csv": "CSV",
    }
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            ext = os.path.splitext(name.lower())[1]
            fmt = ext_map.get(ext)
            if fmt:
                out.append((os.path.join(dirpath, name), fmt))
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


def _extract_anydoc(path: str) -> list[tuple[int, str]]:
    """anydoc 으로 비PDF 문서 텍스트 추출. 반환: [(page_no, text)].

    anydoc 은 whole-document Markdown 을 반환하므로, 빈 줄 기준으로 페이지를 나눈다.
    실제 페이지가 아니라 의미 단락이지만 FTS5 검색용으로는 충분하다.
    """
    import anydoc
    md = anydoc.to_markdown(path)
    chunks = [b.strip() for b in md.split("\n\n") if len(b.strip()) >= MIN_CHARS]
    if not chunks:
        return []
    return [(i + 1, c) for i, c in enumerate(chunks)]


def _index_doc(conn, path, title, doc_type, pages, n_docs, n_pages, skipped):
    """문서 하나를 인덱싱. 기존 등록된 파일이면 갱신."""
    old = conn.execute("SELECT id FROM document WHERE file_path = ?", (path,)).fetchone()
    if old:
        conn.execute("SAVEPOINT idx_doc")
        try:
            conn.execute("DELETE FROM page_fts WHERE document_id = ?", (old["id"],))
            conn.execute("DELETE FROM document WHERE id = ?", (old["id"],))
        except Exception:
            conn.execute("ROLLBACK TO idx_doc")
            raise
        else:
            conn.execute("RELEASE idx_doc")

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
    return n_docs + 1, n_pages + len(pages)


def index_folder(root: str, doc_type: str = "교과서", progress=None) -> dict:
    """폴더를 인덱싱한다. PDF(PyMuPDF) + anydoc(Word·PPT·Excel 등) 모두 처리."""
    close_all_docs()
    conn = connect()
    try:
        ensure_fts(conn)
        n_docs, n_pages, skipped, total = 0, 0, [], 0

        # PDF — PyMuPDF
        pdfs = collect_pdfs(root)
        total += len(pdfs)
        for i, path in enumerate(pdfs):
            title = os.path.splitext(os.path.basename(path))[0]
            if progress:
                progress(i + 1, total, title)
            try:
                pages = _extract(path)
            except Exception as e:
                skipped.append({"path": path, "reason": str(e)})
                continue
            if not pages:
                skipped.append({"path": path, "reason": "텍스트 없음(스캔본 추정)"})
                continue
            n_docs, n_pages = _index_doc(conn, path, title, doc_type, pages, n_docs, n_pages, skipped)

        # 비PDF — anydoc
        docs = collect_docs(root)
        total += len(docs)
        for i, (path, fmt) in enumerate(docs):
            title = os.path.splitext(os.path.basename(path))[0]
            if progress:
                progress(len(pdfs) + i + 1, total, f"{title} ({fmt})")
            try:
                pages = _extract_anydoc(path)
            except Exception as e:
                skipped.append({"path": path, "reason": f"anydoc({fmt}): {e}"})
                continue
            if not pages:
                continue
            n_docs, n_pages = _index_doc(conn, path, title, doc_type, pages, n_docs, n_pages, skipped)

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


def search(q: str, limit: int = 60, doc_id: int | None = None,
           doc_type: str = "") -> dict:
    """근거 검색.

    반환: {"total": 전체 일치 수, "terms": [키워드], "items": [...]}
    각 item 에는 일치율(match_pct)이 붙는다 — 최고점을 100%로 한 상대 환산(DocFinder 방식).

    doc_type 은 반드시 여기(SQL)에서 걸러야 한다. 상위 limit 건만 받아다 화면에서
    추리면, 교육과정처럼 페이지가 많은 문서가 상위를 다 차지해 기출이 한 건도
    안 남는다 ('에너지' 검색 시 기출 32쪽이 있는데도 0건으로 보이던 문제).
    """
    terms = terms_of(q)
    if not terms:
        return {"total": 0, "terms": [], "items": []}
    conn = connect()
    try:
        ensure_fts(conn)
        match = " AND ".join(f'"{t}"*' for t in terms)
        where, args = "page_fts MATCH ?", [match]
        if doc_id:
            where += " AND document_id = ?"
            args.append(doc_id)
        if doc_type == "수업":
            where += " AND document_id < 0"       # 수업 기록은 document_id 가 음수다
        elif doc_type:
            where += " AND document_id IN (SELECT id FROM document WHERE doc_type = ?)"
            args.append(doc_type)

        rows = conn.execute(
            "SELECT doc_title, page_no, document_id, "
            "  snippet(page_fts, 0, '[', ']', ' … ', 20) AS snippet, "
            "  bm25(page_fts) AS score "
            f"FROM page_fts WHERE {where} ORDER BY score LIMIT ?", args + [limit]).fetchall()

        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM page_fts WHERE {where}", args).fetchone()["c"]

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

    **글자 단위로 찾는다.**
    `get_text("words")` 는 공백으로만 잘리는데, 한글 PDF 의 텍스트 층은 띄어쓰기가
    통째로 빠진 경우가 많다('에너지증가량과같다'가 한 단어). 단어 단위로 칠하면
    검색어가 아닌 줄 전체가 형광이 된다. 그래서 `rawdict` 의 글자별 bbox 를 모아
    줄 문자열을 만들고, 검색어에 해당하는 **글자들만** 묶어서 상자를 만든다.
    공백을 빼고 맞히므로 '징 계 기 준' 처럼 쪼개진 경우도 같이 잡힌다
    (예전 _line_fallback 이 하던 일).

    `search_for()` 를 쓰지 않는 이유는 색인(FTS5)과 탐색 경로가 갈려
    '일치율 100%인데 형광펜 0개'가 나기 때문이다(06 보고서 2-2절).

    반환: ({검색어: [Rect]}, 못 찾은 검색어 목록)
    """
    lines = _char_lines(page)
    hits: dict[str, list] = {}
    misses: list[str] = []
    for t in terms:
        target = t.lower().replace(" ", "")
        rects = []
        if target:
            for text, rs in lines:
                start = text.find(target)
                while start >= 0:
                    span = rs[start:start + len(target)]
                    rect = span[0]
                    for r in span[1:]:
                        rect = rect | r
                    rects.append(rect)
                    start = text.find(target, start + 1)
        if rects:
            hits[t] = rects
        else:
            misses.append(t)
    return hits, misses


def _char_lines(page) -> list[tuple[str, list]]:
    """페이지를 줄 단위로 → [(공백 제거한 소문자 줄 문자열, [글자별 Rect])].

    문자열의 i 번째 글자와 Rect 목록의 i 번째가 1:1 로 맞는다 — 그래야 검색어에
    걸린 글자만 정확히 골라낼 수 있다.
    """
    import fitz

    out = []
    for block in page.get_text("rawdict").get("blocks", []):
        if block.get("type") != 0:          # 0 = 텍스트 (이미지 블록은 건너뜀)
            continue
        for line in block.get("lines", []):
            chars, rects = [], []
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    c = ch.get("c", "")
                    if not c.strip():       # 공백은 문자열에서도 뺀다
                        continue
                    chars.append(c.lower())
                    rects.append(fitz.Rect(ch["bbox"]))
            if chars:
                out.append(("".join(chars), rects))
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

    반환: {"items":[{num, x,y,w,h, hits:{단어:개수}, has_hit,
                     boxes:[{term,color_idx,x,y,w,h}]}], "page_w","page_h"}
    문항을 못 찾으면 items 가 빈 목록 → 호출 쪽이 페이지 전체를 쓰면 된다.

    boxes 는 **문항 상자를 1로 본 비율(0~1)**이다. 문항 이미지는 페이지와 다른 dpi 로
    잘라 렌더링하므로(render_item_png 기본 120), 픽셀 좌표를 주면 화면에서 어긋난다.
    비율로 주면 이미지가 어떤 크기로 나와도 그대로 겹칠 수 있다.
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
        iw = max(it["x1"] - it["x0"], 1e-6)
        ih = max(it["y1"] - it["y0"], 1e-6)
        hits, boxes = {}, []
        for t, rects in hit_map.items():
            inside = [r for r in rects
                      if it["x0"] <= r.x0 <= it["x1"] and it["y0"] <= r.y0 <= it["y1"]]
            if not inside:
                continue
            hits[t] = len(inside)
            ci = terms.index(t) % len(HL_PALETTE) if t in terms else 0
            for r in inside:
                boxes.append({
                    "term": t, "color_idx": ci,
                    "x": round((r.x0 - it["x0"]) / iw, 4),
                    "y": round((r.y0 - it["y0"]) / ih, 4),
                    "w": round((r.x1 - r.x0) / iw, 4),
                    "h": round((r.y1 - r.y0) / ih, 4),
                })
        boxes.sort(key=lambda b: (b["y"], b["x"]))
        out.append({
            "num": it["num"],
            "x": round(it["x0"] * zoom, 1), "y": round(it["y0"] * zoom, 1),
            "w": round((it["x1"] - it["x0"]) * zoom, 1),
            "h": round((it["y1"] - it["y0"]) * zoom, 1),
            "hits": hits, "has_hit": bool(hits), "boxes": boxes,
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
