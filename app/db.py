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

-- 교육과정 안의 과목. 중학교 '과학' 하나가 아니라 고등학교 선택 과목까지 들어온다.
--   track: 공통 / 공통과목 / 일반선택 / 진로선택 / 융합선택
CREATE TABLE IF NOT EXISTS subject (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    track       TEXT NOT NULL,
    grade_band  TEXT NOT NULL,
    code_prefix TEXT NOT NULL,
    unit_base   INTEGER NOT NULL,
    ord         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS standard (
    code        TEXT PRIMARY KEY,
    grade_band  TEXT NOT NULL,
    unit_no     INTEGER NOT NULL,
    seq         INTEGER NOT NULL,
    text        TEXT NOT NULL
);

-- unit_no 는 과목이 여럿이어도 전역 유일하다(과목별로 100 번대씩 띄움).
-- 화면에는 local_no(그 과목 안에서의 (1),(2)...)를 보여준다.
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
    image_choices  INTEGER NOT NULL DEFAULT 0,
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
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT '설계중',
    -- 지필 만점. 100 이 아닌 시험(예: 지필 70 + 수행 30)이 흔해 세트마다 따로 갖는다.
    total_points REAL NOT NULL DEFAULT 100.0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 세트 안의 문항 자리. 청사진(ExamMaker) 도입 후에는 "아직 문항이 없는 슬롯"도
-- 이 테이블의 행이다 — question_id 가 NULL 이면 계획만 있는 슬롯, 채워지면 완성.
-- 계획→생성→확정이 별도 테이블 이동 없이 한 행의 생애주기로 흐른다.
CREATE TABLE IF NOT EXISTS set_item (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id      INTEGER NOT NULL REFERENCES exam_set(id) ON DELETE CASCADE,
    question_id INTEGER REFERENCES question(id),
    ord         INTEGER NOT NULL,
    points      REAL,
    -- 청사진 슬롯 필드 (plan_*) — 문항 생성 전의 '주문서'
    plan_qtype         TEXT NOT NULL DEFAULT '',
    plan_standard_code TEXT NOT NULL DEFAULT '',
    plan_topic         TEXT NOT NULL DEFAULT '',
    plan_is_negative   INTEGER NOT NULL DEFAULT 0,
    plan_needs_figure  INTEGER NOT NULL DEFAULT 0,
    plan_figure_hint   TEXT NOT NULL DEFAULT '',
    plan_situation     TEXT NOT NULL DEFAULT '',
    slot_status        TEXT NOT NULL DEFAULT ''
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
    summary    TEXT NOT NULL DEFAULT '',
    indexed_at TEXT NOT NULL DEFAULT ''
);

-- 참고한 기출 문항 스크랩 (문항 단위 북마크 + 메모)
CREATE TABLE IF NOT EXISTS exam_ref (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    doc_title   TEXT NOT NULL,
    page_no     INTEGER NOT NULL,
    item_num    INTEGER NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '',
    question_id INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ref_uniq ON exam_ref(document_id, page_no, item_num);

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
def _add_column(conn: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _migrate(conn: sqlite3.Connection) -> None:
    # 교육과정 확장 — 과목·단원 유의사항·성취기준 해설
    _add_column(conn, "unit", "subject_id", "INTEGER")
    _add_column(conn, "unit", "local_no", "INTEGER")
    _add_column(conn, "unit", "inquiry", "TEXT NOT NULL DEFAULT '[]'")    # <탐구 활동>
    _add_column(conn, "unit", "consider", "TEXT NOT NULL DEFAULT '[]'")   # 성취기준 적용 시 고려 사항
    _add_column(conn, "standard", "subject_id", "INTEGER")
    _add_column(conn, "standard", "explain", "TEXT NOT NULL DEFAULT ''")  # 성취기준 해설

    _add_column(conn, "question", "title", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "question", "image_choices", "INTEGER NOT NULL DEFAULT 0")
    # 이원목적분류표의 행동영역 (2022 개정: 지식·이해 / 과정·기능 / 가치·태도)
    _add_column(conn, "question", "behavior", "TEXT NOT NULL DEFAULT ''")

    # 출처 — '누가 처음 썼나'라는 바뀌지 않는 사실. status(초안/검토중/완성)와는 다른 축이다.
    #   status 에 'AI' 를 값으로 섞으면 AI 초안을 검토해 완성한 순간 출처가 지워진다.
    #   경계는 '첫 줄을 누가 썼나'로 고정하고, 애매하면 AI초안 쪽으로 남긴다(보수적으로).
    _add_column(conn, "question", "origin", "TEXT NOT NULL DEFAULT ''")
    # 출처 메모 — 기출 출처, 적재 스크립트 이름 등. 제목과 달리 자주 고치지 않는 칸이라
    # 스크립트가 자기가 넣은 문항을 다시 찾는 열쇠로도 쓴다 (seed:... 형식).
    _add_column(conn, "question", "origin_note", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "exam_set", "total_points", "REAL NOT NULL DEFAULT 100.0")
    _add_column(conn, "lesson", "indexed_at", "TEXT NOT NULL DEFAULT ''")

    # ExamMaker 청사진 — 세트 약칭(그림 파일명 규약 {short_code}_{번호2자리}의 앞부분)
    _add_column(conn, "exam_set", "short_code", "TEXT NOT NULL DEFAULT ''")
    # 슬롯 계획 필드. question_id 를 NULL 허용으로 바꿔야 해서(SQLite 는 제약 변경 불가)
    # 한 번은 테이블 재구성이 필요하다 — 데이터는 그대로 복사한다.
    _migrate_set_item_slots(conn)


def _migrate_set_item_slots(conn: sqlite3.Connection) -> None:
    info = {r["name"]: r for r in conn.execute("PRAGMA table_info(set_item)")}
    plan_cols = [
        ("plan_qtype", "TEXT NOT NULL DEFAULT ''"),
        ("plan_standard_code", "TEXT NOT NULL DEFAULT ''"),
        ("plan_topic", "TEXT NOT NULL DEFAULT ''"),
        ("plan_is_negative", "INTEGER NOT NULL DEFAULT 0"),
        ("plan_needs_figure", "INTEGER NOT NULL DEFAULT 0"),
        ("plan_figure_hint", "TEXT NOT NULL DEFAULT ''"),
        ("plan_situation", "TEXT NOT NULL DEFAULT ''"),
        ("slot_status", "TEXT NOT NULL DEFAULT ''"),
    ]
    for col, decl in plan_cols:
        _add_column(conn, "set_item", col, decl)

    # question_id NOT NULL 이면 재구성 (구버전 DB 한정, 한 번만 실행됨)
    if info.get("question_id") is not None and info["question_id"]["notnull"]:
        old_cols = "id, set_id, question_id, ord, points, " + ", ".join(c for c, _ in plan_cols)
        conn.execute("ALTER TABLE set_item RENAME TO set_item_old")
        conn.execute("""
            CREATE TABLE set_item (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                set_id      INTEGER NOT NULL REFERENCES exam_set(id) ON DELETE CASCADE,
                question_id INTEGER REFERENCES question(id),
                ord         INTEGER NOT NULL,
                points      REAL,
                plan_qtype         TEXT NOT NULL DEFAULT '',
                plan_standard_code TEXT NOT NULL DEFAULT '',
                plan_topic         TEXT NOT NULL DEFAULT '',
                plan_is_negative   INTEGER NOT NULL DEFAULT 0,
                plan_needs_figure  INTEGER NOT NULL DEFAULT 0,
                plan_figure_hint   TEXT NOT NULL DEFAULT '',
                plan_situation     TEXT NOT NULL DEFAULT '',
                slot_status        TEXT NOT NULL DEFAULT ''
            )""")
        conn.execute(f"INSERT INTO set_item ({old_cols}) SELECT {old_cols} FROM set_item_old")
        conn.execute("DROP TABLE set_item_old")


# ===== 성취기준 seed 적재 =====
# 교육과정은 참고 자료라 seed 파일이 원본이다. 첫 실행뿐 아니라 seed 가 늘어났을 때도
# (중학교만 있다가 고등학교 과목이 추가되는 식) 맞춰 넣는다. 선생님이 만든 명제·문항은
# 건드리지 않으므로, 없어진 성취기준을 지우지는 않는다.
def _seed_standards(conn: sqlite3.Connection) -> None:
    seed_file = SEED_DIR / "standards.json"
    data = json.loads(Path(seed_file).read_text(encoding="utf-8"))

    sid = {}   # 과목명 → subject.id
    for i, s in enumerate(data.get("subjects", [])):
        conn.execute(
            "INSERT INTO subject (name, track, grade_band, code_prefix, unit_base, ord) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
            "track=excluded.track, grade_band=excluded.grade_band, "
            "code_prefix=excluded.code_prefix, unit_base=excluded.unit_base, ord=excluded.ord",
            (s["name"], s["track"], s["grade_band"], s["code_prefix"], s["unit_base"], i))
    for r in conn.execute("SELECT id, name FROM subject"):
        sid[r["name"]] = r["id"]

    for u in data["units"]:
        conn.execute(
            "INSERT INTO unit (unit_no, name, subject_id, local_no, inquiry, consider) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(unit_no) DO UPDATE SET "
            "name=excluded.name, subject_id=excluded.subject_id, local_no=excluded.local_no, "
            "inquiry=excluded.inquiry, consider=excluded.consider",
            (u["unit_no"], u["name"], sid.get(u.get("subject")), u.get("local_no", u["unit_no"]),
             json.dumps(u.get("inquiry", []), ensure_ascii=False),
             json.dumps(u.get("consider", []), ensure_ascii=False)))

    for s in data["standards"]:
        conn.execute(
            "INSERT INTO standard (code, grade_band, unit_no, seq, text, subject_id, explain) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET "
            "grade_band=excluded.grade_band, unit_no=excluded.unit_no, seq=excluded.seq, "
            "text=excluded.text, subject_id=excluded.subject_id, explain=excluded.explain",
            (s["code"], s["grade_band"], s["unit_no"], s["seq"], s["text"],
             sid.get(s.get("subject")), s.get("explain", "")))
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
