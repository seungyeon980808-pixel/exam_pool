"""SQLite 연결·스키마·성취기준 seed 적재.

스키마는 02_DATA_MODEL.md 와 1:1. Phase 1 에서 화면이 없는 테이블(Lesson)도
스키마는 미리 만들어 둔다 — 나중에 마이그레이션 없이 확장하기 위해.
"""
import json
import sqlite3
from pathlib import Path

from .paths import DB_PATH, SEED_DIR


# ===== 연결 =====
def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ===== 스키마 =====
SCHEMA = """
-- 앱 메타(과목·교육과정 출처 등). 다른 과목으로 교체할 때 여기 값이 바뀐다.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS standard (
    code        TEXT PRIMARY KEY,
    grade_band  TEXT NOT NULL,
    unit_no     INTEGER NOT NULL,
    seq         INTEGER NOT NULL,
    text        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unit (
    unit_no INTEGER PRIMARY KEY,
    name    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS objective (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    text          TEXT NOT NULL,
    standard_code TEXT NOT NULL REFERENCES standard(code)
);

CREATE TABLE IF NOT EXISTS proposition (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    text           TEXT NOT NULL,
    standard_code  TEXT NOT NULL REFERENCES standard(code),
    unit_no        INTEGER,
    objective_id   INTEGER REFERENCES objective(id),
    tags           TEXT NOT NULL DEFAULT '',
    class_verified INTEGER NOT NULL DEFAULT 0,
    note           TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS false_variant (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    proposition_id INTEGER NOT NULL REFERENCES proposition(id) ON DELETE CASCADE,
    text           TEXT NOT NULL,
    distortion     TEXT NOT NULL,
    note           TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS evidence (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    proposition_id   INTEGER NOT NULL REFERENCES proposition(id) ON DELETE CASCADE,
    source_type      TEXT NOT NULL,
    source_label     TEXT NOT NULL,
    quote            TEXT NOT NULL,
    document_page_id INTEGER,
    lesson_id        INTEGER
);

CREATE TABLE IF NOT EXISTS question (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT NOT NULL DEFAULT '',
    qtype          TEXT NOT NULL,
    is_negative    INTEGER NOT NULL DEFAULT 0,
    passage        TEXT NOT NULL DEFAULT '',
    material       TEXT NOT NULL DEFAULT '',
    ask            TEXT NOT NULL,
    bogi_items     TEXT NOT NULL DEFAULT '[]',
    answer         TEXT NOT NULL DEFAULT '',
    default_points REAL NOT NULL DEFAULT 3.0,
    difficulty     TEXT NOT NULL DEFAULT '중',
    standard_code  TEXT REFERENCES standard(code),
    intent         TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT '초안',
    review_note    TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS choice (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id    INTEGER NOT NULL REFERENCES question(id) ON DELETE CASCADE,
    ord            INTEGER NOT NULL,
    text           TEXT NOT NULL DEFAULT '',
    proposition_id INTEGER REFERENCES proposition(id),
    variant_id     INTEGER REFERENCES false_variant(id),
    combo          TEXT NOT NULL DEFAULT '',
    custom_evidence TEXT NOT NULL DEFAULT '',
    is_answer      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS exam_set (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT '설계중',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS set_item (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id      INTEGER NOT NULL REFERENCES exam_set(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES question(id),
    ord         INTEGER NOT NULL,
    points      REAL
);

CREATE TABLE IF NOT EXISTS document (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    doc_type   TEXT NOT NULL,
    file_path  TEXT NOT NULL,
    indexed_at TEXT
);

CREATE TABLE IF NOT EXISTS document_page (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    page_no     INTEGER NOT NULL,
    text        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lesson (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL,
    class_name TEXT NOT NULL DEFAULT '',
    transcript TEXT NOT NULL DEFAULT '',
    summary    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_prop_std ON proposition(standard_code);
CREATE INDEX IF NOT EXISTS idx_prop_unit ON proposition(unit_no);
"""


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
        _seed_standards(conn)
    finally:
        conn.close()


# ===== 기존 DB 보정 (컬럼 추가) =====
def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(question)")}
    if "title" not in cols:
        conn.execute("ALTER TABLE question ADD COLUMN title TEXT NOT NULL DEFAULT ''")


# ===== 성취기준 seed 적재 (첫 실행 시 1회) =====
def _seed_standards(conn: sqlite3.Connection) -> None:
    already = conn.execute("SELECT COUNT(*) AS c FROM standard").fetchone()["c"]
    if already:
        return
    seed_file = SEED_DIR / "standards.json"
    data = json.loads(Path(seed_file).read_text(encoding="utf-8"))

    conn.executemany(
        "INSERT INTO unit (unit_no, name) VALUES (?, ?)",
        [(u["unit_no"], u["name"]) for u in data["units"]],
    )
    conn.executemany(
        "INSERT INTO standard (code, grade_band, unit_no, seq, text) VALUES (?, ?, ?, ?, ?)",
        [
            (s["code"], s["grade_band"], s["unit_no"], s["seq"], s["text"])
            for s in data["standards"]
        ],
    )
    # 과목 메타 — 다른 과목 seed 로 교체하면 이 값이 따라 바뀐다 (확장성)
    conn.executemany(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        [
            ("subject", data.get("subject", "중학교 과학 (2022 개정)")),
            ("source", data.get("source", "")),
            ("extracted_at", data.get("extracted_at", "")),
        ],
    )
    conn.commit()
