"""수업 기록 — 교과서와 같은 인덱스에 얹는다 (Phase 2).

출제 조건 2번 "수업에서 언급했는가"를 기록으로 답한다.
별도 검색 엔진을 만들지 않고 **PDF 근거 검색과 같은 page_fts 인덱스**를 쓴다.
근거 검색창 한 곳에서 교과서·교육과정·기출·수업이 함께 나오는 것이 요점이다.

같은 테이블에 섞이므로 구분 규칙을 둔다:
  document_id = -(lesson.id)   음수면 수업 기록
  page_no     = 조각 번호(1부터)
  doc_title   = "수업 2026-03-12 3반"

음수 id 를 쓰는 이유: document 테이블의 실제 id 와 절대 겹치지 않으면서,
검색 결과를 받는 쪽이 부호만 보고 "이건 PDF 페이지가 아니다"를 알 수 있다.
학생 발언이 섞일 수 있으므로 저장·검색 모두 로컬에서만 일어난다(외부 전송 없음).
"""
import sqlite3

# 한 조각의 목표 길이. 너무 길면 스니펫이 뭉개지고, 너무 짧으면 문맥이 끊긴다.
CHUNK_CHARS = 700


def doc_id_of(lesson_id: int) -> int:
    return -lesson_id


def lesson_id_of(doc_id: int) -> int | None:
    return -doc_id if doc_id < 0 else None


def is_lesson(doc_id: int) -> bool:
    return doc_id < 0


def title_of(row) -> str:
    cls = (row["class_name"] or "").strip()
    return f"수업 {row['date']}" + (f" {cls}" if cls else "")


def split_chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """줄 단위로 모아 size 근처에서 끊는다. 문장 중간을 자르지 않는다."""
    chunks, buf = [], ""
    for line in (text or "").splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        if buf and len(buf) + len(line) + 1 > size:
            chunks.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        chunks.append(buf)
    return chunks


def unindex(conn: sqlite3.Connection, lesson_id: int) -> None:
    conn.execute("DELETE FROM page_fts WHERE document_id = ?", (doc_id_of(lesson_id),))


def index_lesson(conn: sqlite3.Connection, lesson_id: int) -> int:
    """수업 기록 하나를 색인한다(기존 색인은 지우고 다시). 조각 수를 낸다."""
    row = conn.execute("SELECT * FROM lesson WHERE id = ?", (lesson_id,)).fetchone()
    if not row:
        return 0
    unindex(conn, lesson_id)
    chunks = split_chunks(row["transcript"])
    if not chunks:
        conn.execute("UPDATE lesson SET indexed_at = '' WHERE id = ?", (lesson_id,))
        return 0
    title = title_of(row)
    conn.executemany(
        "INSERT INTO page_fts (body, doc_title, page_no, document_id) VALUES (?,?,?,?)",
        [(c, title, i + 1, doc_id_of(lesson_id)) for i, c in enumerate(chunks)],
    )
    conn.execute("UPDATE lesson SET indexed_at = datetime('now','localtime') WHERE id = ?",
                 (lesson_id,))
    return len(chunks)


def reindex_all(conn: sqlite3.Connection) -> int:
    ids = [r["id"] for r in conn.execute("SELECT id FROM lesson").fetchall()]
    return sum(index_lesson(conn, i) for i in ids)


def chunk_text(conn: sqlite3.Connection, lesson_id: int, chunk_no: int) -> dict | None:
    """검색 결과에서 '원문 보기'를 눌렀을 때 보여줄 조각."""
    row = conn.execute("SELECT * FROM lesson WHERE id = ?", (lesson_id,)).fetchone()
    if not row:
        return None
    chunks = split_chunks(row["transcript"])
    if not chunks:
        return None
    idx = max(1, min(chunk_no, len(chunks)))
    return {"title": title_of(row), "date": row["date"], "class_name": row["class_name"],
            "chunk_no": idx, "last_chunk": len(chunks), "text": chunks[idx - 1]}
