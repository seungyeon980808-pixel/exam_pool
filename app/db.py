"""SQLite 연결·스키마·성취기준 seed 적재.

스키마는 02_DATA_MODEL.md 와 1:1. Phase 1 에서 화면이 없는 테이블(Lesson)도
스키마는 미리 만들어 둔다 — 나중에 마이그레이션 없이 확장하기 위해.
"""
import contextlib
import json
import sqlite3
from pathlib import Path

from .paths import DB_PATH, SEED_DIR

_inited = False

# ===== 연결 =====
def connect() -> sqlite3.Connection:
    global _inited
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    if not _inited:
        _inited = True
        try:
            conn.executescript(SCHEMA)
            _migrate(conn)
            conn.commit()
            _seed_standards(conn)
        except Exception:
            _inited = False
            raise
    return conn


@contextlib.contextmanager
def transaction():
    """트랜잭션 컨텍스트 매니저. 예외 발생 시 자동 롤백."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
    explanation    TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT '초안',
    review_note    TEXT NOT NULL DEFAULT '{}',
    style_meta     TEXT NOT NULL DEFAULT '{}',
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
    layout_style TEXT NOT NULL DEFAULT 'school',
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
    question_id INTEGER REFERENCES question(id) ON DELETE CASCADE,
    authoring_session_id INTEGER REFERENCES authoring_session(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 대화형 문항 제작 세션. 기존 question 은 확정·저장된 문항이고, 이 테이블은
-- 저장 전 초안과 Codex 대화를 별도로 보관한다. Codex thread 가 사라져도 대화는 남는다.
CREATE TABLE IF NOT EXISTS authoring_session (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id        INTEGER UNIQUE REFERENCES question(id) ON DELETE SET NULL,
    source_question_id INTEGER REFERENCES question(id) ON DELETE SET NULL,
    provider           TEXT NOT NULL DEFAULT 'mock',
    model              TEXT NOT NULL DEFAULT '',
    reasoning_effort   TEXT NOT NULL DEFAULT '',
    authoring_mode     TEXT NOT NULL DEFAULT 'quick',
    workflow_mode      TEXT NOT NULL DEFAULT 'auto',
    purpose_mode       TEXT NOT NULL DEFAULT 'create',
    provider_thread_id TEXT NOT NULL DEFAULT '',
    provider_protocol  TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'text_drafting',
    draft_json         TEXT NOT NULL DEFAULT '{}',
    confirmed_json     TEXT NOT NULL DEFAULT '{}',
    review_flags       TEXT NOT NULL DEFAULT '[]',
    revision           INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS authoring_message (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL REFERENCES authoring_session(id) ON DELETE CASCADE,
    role           TEXT NOT NULL,
    content        TEXT NOT NULL DEFAULT '',
    proposals_json TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS authoring_revision (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES authoring_session(id) ON DELETE CASCADE,
    message_id  INTEGER REFERENCES authoring_message(id) ON DELETE SET NULL,
    before_json TEXT NOT NULL,
    after_json  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS authoring_figure (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER NOT NULL UNIQUE REFERENCES authoring_session(id) ON DELETE CASCADE,
    provider            TEXT NOT NULL DEFAULT 'stub',
    status              TEXT NOT NULL DEFAULT 'none',
    scene_spec_path     TEXT NOT NULL DEFAULT '',
    fivee_project_path  TEXT NOT NULL DEFAULT '',
    rendered_image_path TEXT NOT NULL DEFAULT '',
    options_json        TEXT NOT NULL DEFAULT '{"provider":"fivee_assets","include_text":false,"composition":"combined"}',
    revision            INTEGER NOT NULL DEFAULT 0,
    previous_image_path TEXT NOT NULL DEFAULT '',
    updated_at          TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS authoring_figure_asset (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER NOT NULL REFERENCES authoring_session(id) ON DELETE CASCADE,
    panel_id            TEXT NOT NULL DEFAULT 'main',
    ord                 INTEGER NOT NULL DEFAULT 1,
    provider            TEXT NOT NULL DEFAULT 'fivee_assets',
    prompt              TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'none',
    scene_spec_path     TEXT NOT NULL DEFAULT '',
    fivee_project_path  TEXT NOT NULL DEFAULT '',
    source_image_path   TEXT NOT NULL DEFAULT '',
    rendered_image_path TEXT NOT NULL DEFAULT '',
    asset_mode          TEXT NOT NULL DEFAULT '',
    asset_role          TEXT NOT NULL DEFAULT 'editable',
    source_pdf          TEXT NOT NULL DEFAULT '',
    page_no             INTEGER NOT NULL DEFAULT 0,
    bbox_json           TEXT NOT NULL DEFAULT '[]',
    dpi                 INTEGER NOT NULL DEFAULT 0,
    width_px            INTEGER NOT NULL DEFAULT 0,
    height_px           INTEGER NOT NULL DEFAULT 0,
    aspect_ratio        REAL NOT NULL DEFAULT 0,
    source_hash         TEXT NOT NULL DEFAULT '',
    revision            INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(session_id, panel_id)
);

CREATE TABLE IF NOT EXISTS authoring_figure_reference (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES authoring_session(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL DEFAULT '',
    image_path  TEXT NOT NULL,
    source_label TEXT NOT NULL DEFAULT '',
    source_text TEXT NOT NULL DEFAULT '',
    usage       TEXT NOT NULL DEFAULT 'both',
    source_meta_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_authoring_message_session
    ON authoring_message(session_id, id);
CREATE INDEX IF NOT EXISTS idx_authoring_revision_session
    ON authoring_revision(session_id, id);
CREATE INDEX IF NOT EXISTS idx_authoring_figure_asset_session
    ON authoring_figure_asset(session_id, ord);
CREATE INDEX IF NOT EXISTS idx_authoring_figure_reference_session
    ON authoring_figure_reference(session_id, id);

-- PDF-to-HWP conversion owns durable jobs independently of question authoring.
CREATE TABLE IF NOT EXISTS conversion_job (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL DEFAULT '',
    layout_style    TEXT NOT NULL DEFAULT 'suneung',
    status          TEXT NOT NULL DEFAULT 'draft',
    source_filename TEXT NOT NULL DEFAULT '',
    source_path     TEXT NOT NULL DEFAULT '',
    source_sha256   TEXT NOT NULL DEFAULT '',
    error_code      TEXT NOT NULL DEFAULT '',
    error_message   TEXT NOT NULL DEFAULT '',
    detection_progress INTEGER NOT NULL DEFAULT 0,
    generation_progress INTEGER NOT NULL DEFAULT 0,
    current_item_number INTEGER,
    selection_snapshot_json TEXT NOT NULL DEFAULT '[]',
    selection_snapshot_at TEXT NOT NULL DEFAULT '',
    revision        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS conversion_item (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        INTEGER NOT NULL REFERENCES conversion_job(id) ON DELETE CASCADE,
    ord           INTEGER NOT NULL,
    source_page   INTEGER NOT NULL,
    source_number INTEGER,
    bbox_json     TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'detected',
    selected      INTEGER NOT NULL DEFAULT 1,
    draft_json    TEXT NOT NULL DEFAULT '{}',
    error_code    TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    question_number INTEGER,
    domain        TEXT NOT NULL DEFAULT '',
    type_id       TEXT NOT NULL DEFAULT '',
    type_version  TEXT NOT NULL DEFAULT '1.0',
    response_type TEXT NOT NULL DEFAULT 'matching',
    asset_count   INTEGER NOT NULL DEFAULT 0,
    detection_status TEXT NOT NULL DEFAULT 'detected',
    conversion_status TEXT NOT NULL DEFAULT 'pending',
    confirmed     INTEGER NOT NULL DEFAULT 0,
    confirmed_at  TEXT NOT NULL DEFAULT '',
    manual_blocks_json TEXT NOT NULL DEFAULT '[]',
    unplaced_materials_json TEXT NOT NULL DEFAULT '[]',
    revision      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(job_id, ord)
);

CREATE TABLE IF NOT EXISTS conversion_asset (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       INTEGER NOT NULL REFERENCES conversion_job(id) ON DELETE CASCADE,
    item_id      INTEGER REFERENCES conversion_item(id) ON DELETE CASCADE,
    role         TEXT NOT NULL,
    file_path    TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    media_type   TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS conversion_output (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       INTEGER NOT NULL REFERENCES conversion_job(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    file_path    TEXT NOT NULL DEFAULT '',
    sha256       TEXT NOT NULL DEFAULT '',
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    error_code   TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(job_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_conversion_item_job ON conversion_item(job_id, ord);
CREATE INDEX IF NOT EXISTS idx_conversion_asset_job ON conversion_asset(job_id, item_id);
CREATE INDEX IF NOT EXISTS idx_conversion_output_job ON conversion_output(job_id, id);

CREATE TABLE IF NOT EXISTS conversion_operation (
    id              TEXT PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES conversion_job(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',
    progress        INTEGER NOT NULL DEFAULT 0,
    current_item_number INTEGER,
    selection_snapshot_json TEXT NOT NULL DEFAULT '[]',
    error_code      TEXT NOT NULL DEFAULT '',
    error_message   TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_conversion_operation_job ON conversion_operation(job_id, created_at);

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


def _unwrap_visible_text_envelopes(payload) -> tuple[object, bool]:
    """Repair the short-lived metadata-envelope format without losing its provenance."""
    if not isinstance(payload, dict):
        return payload, False
    value = dict(payload)
    changed = False
    raw_style_meta = value.get("style_meta")
    style_meta = dict(raw_style_meta) if isinstance(raw_style_meta, dict) else {}
    for field in ("passage", "ask"):
        raw = value.get(field)
        if isinstance(raw, dict):
            value[field] = str(raw.get("text") or "")
            frame_id = raw.get("frame_id")
            if frame_id:
                style_meta[field] = {
                    "frame_id": frame_id,
                    "sources": raw.get("style_sources") or raw.get("sources") or [],
                }
            changed = True
        elif isinstance(raw, str) and raw.strip().lower() == "[object object]":
            value[field] = ""
            changed = True
    if style_meta != (raw_style_meta or {}):
        value["style_meta"] = style_meta
        changed = True
    return value, changed


def _repair_authoring_text_envelopes(conn: sqlite3.Connection) -> None:
    """One-way repair for drafts, proposals and undo snapshots saved by protocol v11."""
    for row in conn.execute("SELECT id,draft_json,confirmed_json FROM authoring_session").fetchall():
        updates = {}
        for column in ("draft_json", "confirmed_json"):
            try:
                payload = json.loads(row[column] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            repaired, changed = _unwrap_visible_text_envelopes(payload)
            if changed:
                updates[column] = json.dumps(repaired, ensure_ascii=False)
        if updates:
            assignments = ",".join(f"{column}=?" for column in updates)
            conn.execute(
                f"UPDATE authoring_session SET {assignments} WHERE id=?",
                (*updates.values(), row["id"]),
            )

    for row in conn.execute("SELECT id,proposals_json FROM authoring_message").fetchall():
        try:
            proposals = json.loads(row["proposals_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            continue
        changed = False
        if isinstance(proposals, list):
            for proposal in proposals:
                if not isinstance(proposal, dict) or proposal.get("field") not in {"passage", "ask"}:
                    continue
                raw = proposal.get("value")
                if not isinstance(raw, dict):
                    continue
                proposal["value"] = str(raw.get("text") or "")
                if raw.get("frame_id") and not proposal.get("frame_id"):
                    proposal["frame_id"] = raw["frame_id"]
                if raw.get("style_sources") and not proposal.get("style_sources"):
                    proposal["style_sources"] = raw["style_sources"]
                changed = True
        if changed:
            conn.execute(
                "UPDATE authoring_message SET proposals_json=? WHERE id=?",
                (json.dumps(proposals, ensure_ascii=False), row["id"]),
            )

    for row in conn.execute("SELECT id,before_json,after_json FROM authoring_revision").fetchall():
        updates = {}
        for column in ("before_json", "after_json"):
            try:
                payload = json.loads(row[column] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            repaired, changed = _unwrap_visible_text_envelopes(payload)
            if changed:
                updates[column] = json.dumps(repaired, ensure_ascii=False)
        if updates:
            assignments = ",".join(f"{column}=?" for column in updates)
            conn.execute(
                f"UPDATE authoring_revision SET {assignments} WHERE id=?",
                (*updates.values(), row["id"]),
            )


def _migrate(conn: sqlite3.Connection) -> None:
    # 교육과정 확장 — 과목·단원 유의사항·성취기준 해설
    _add_column(conn, "unit", "subject_id", "INTEGER")
    _add_column(conn, "unit", "local_no", "INTEGER")
    _add_column(conn, "unit", "inquiry", "TEXT NOT NULL DEFAULT '[]'")    # <탐구 활동>
    _add_column(conn, "unit", "consider", "TEXT NOT NULL DEFAULT '[]'")   # 성취기준 적용 시 고려 사항
    _add_column(conn, "standard", "subject_id", "INTEGER")
    _add_column(conn, "standard", "explain", "TEXT NOT NULL DEFAULT ''")  # 성취기준 해설

    _add_column(conn, "question", "title", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "conversion_item", "selected", "INTEGER NOT NULL DEFAULT 1")
    # PDF→HWP structured review fields. Existing palette-only drafts remain valid
    # and are upgraded lazily by the item store when first read or edited.
    _add_column(conn, "conversion_job", "detection_progress", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "conversion_job", "generation_progress", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "conversion_job", "current_item_number", "INTEGER")
    _add_column(conn, "conversion_job", "selection_snapshot_json", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "conversion_job", "selection_snapshot_at", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "conversion_item", "question_number", "INTEGER")
    _add_column(conn, "conversion_item", "domain", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "conversion_item", "type_id", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "conversion_item", "type_version", "TEXT NOT NULL DEFAULT '1.0'")
    _add_column(conn, "conversion_item", "response_type", "TEXT NOT NULL DEFAULT 'matching'")
    _add_column(conn, "conversion_item", "asset_count", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "conversion_item", "detection_status", "TEXT NOT NULL DEFAULT 'detected'")
    _add_column(conn, "conversion_item", "conversion_status", "TEXT NOT NULL DEFAULT 'pending'")
    _add_column(conn, "conversion_item", "confirmed", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "conversion_item", "confirmed_at", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "conversion_item", "manual_blocks_json", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "conversion_item", "unplaced_materials_json", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "question", "image_choices", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "question", "updated_at", "TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "UPDATE question SET updated_at = created_at "
        "WHERE updated_at IS NULL OR updated_at = ''"
    )
    # 이원목적분류표의 행동영역 (2022 개정: 지식·이해 / 과정·기능 / 가치·태도)
    _add_column(conn, "question", "behavior", "TEXT NOT NULL DEFAULT ''")

    # 출처 — '누가 처음 썼나'라는 바뀌지 않는 사실. status(초안/검토중/완성)와는 다른 축이다.
    #   status 에 'AI' 를 값으로 섞으면 AI 초안을 검토해 완성한 순간 출처가 지워진다.
    #   경계는 '첫 줄을 누가 썼나'로 고정하고, 애매하면 AI초안 쪽으로 남긴다(보수적으로).
    _add_column(conn, "question", "origin", "TEXT NOT NULL DEFAULT ''")
    # 출처 메모 — 기출 출처, 적재 스크립트 이름 등. 제목과 달리 자주 고치지 않는 칸이라
    # 스크립트가 자기가 넣은 문항을 다시 찾는 열쇠로도 쓴다 (seed:... 형식).
    _add_column(conn, "question", "origin_note", "TEXT NOT NULL DEFAULT ''")
    # 대화형 제작 화면에서 사용하는 해설. 기존 문항은 빈 문자열로 안전하게 시작한다.
    _add_column(conn, "question", "explanation", "TEXT NOT NULL DEFAULT ''")
    # 기출 문체 frame_id와 실제 시험지 출처. 작성 세션이 끝난 뒤에도 문항과 함께 보존한다.
    _add_column(conn, "question", "style_meta", "TEXT NOT NULL DEFAULT '{}'")
    # 대화 모델 설정은 문항별 작성 세션에만 보관한다. 인증 정보와 토큰은 저장하지 않는다.
    _add_column(conn, "authoring_session", "model", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "authoring_session", "reasoning_effort", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "authoring_session", "authoring_mode", "TEXT NOT NULL DEFAULT 'quick'")
    _add_column(conn, "authoring_session", "workflow_mode", "TEXT NOT NULL DEFAULT 'auto'")
    # 새 문항 출제와 선택 기출의 충실한 복원을 세션(문항 탭)별로 분리한다.
    _add_column(conn, "authoring_session", "purpose_mode", "TEXT NOT NULL DEFAULT 'create'")
    # 대화 기능 명세가 바뀌면 이전 Codex 스레드를 안전하게 교체하기 위한 버전 표식.
    _add_column(conn, "authoring_session", "provider_protocol", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "authoring_session", "source_question_id", "INTEGER REFERENCES question(id) ON DELETE SET NULL")
    _add_column(conn, "authoring_figure", "options_json", "TEXT NOT NULL DEFAULT '{\"provider\":\"fivee_assets\",\"include_text\":false,\"composition\":\"combined\"}'")
    _add_column(conn, "authoring_figure", "revision", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "authoring_figure", "previous_image_path", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "authoring_figure_asset", "prompt", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "authoring_figure_asset", "asset_mode", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "authoring_figure_asset", "asset_role", "TEXT NOT NULL DEFAULT 'editable'")
    _add_column(conn, "authoring_figure_asset", "source_pdf", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "authoring_figure_asset", "page_no", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "authoring_figure_asset", "bbox_json", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "authoring_figure_asset", "dpi", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "authoring_figure_asset", "width_px", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "authoring_figure_asset", "height_px", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "authoring_figure_asset", "aspect_ratio", "REAL NOT NULL DEFAULT 0")
    _add_column(conn, "authoring_figure_asset", "source_hash", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "authoring_figure_reference", "source_label", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "authoring_figure_reference", "source_text", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "authoring_figure_reference", "usage", "TEXT NOT NULL DEFAULT 'both'")
    _add_column(conn, "authoring_figure_reference", "source_meta_json", "TEXT NOT NULL DEFAULT '{}'")
    _add_column(conn, "exam_set", "total_points", "REAL NOT NULL DEFAULT 100.0")
    _add_column(conn, "exam_set", "layout_style", "TEXT NOT NULL DEFAULT 'school'")
    _add_column(conn, "lesson", "indexed_at", "TEXT NOT NULL DEFAULT ''")
    # Reconstruct legacy set_item tables before repairing their invariants.
    _migrate_set_item_slots(conn)
    _add_column(conn, "exam_ref", "authoring_session_id", "INTEGER")
    conn.execute("DROP INDEX IF EXISTS idx_ref_uniq")
    conn.execute("DROP INDEX IF EXISTS idx_ref_owner_uniq")
    conn.execute(
        "CREATE UNIQUE INDEX idx_ref_owner_uniq ON exam_ref("
        "document_id, page_no, item_num, COALESCE(question_id, -1), "
        "COALESCE(authoring_session_id, -1))"
    )
    conn.execute("DROP INDEX IF EXISTS idx_set_item_question")
    conn.execute("DROP INDEX IF EXISTS idx_set_item_ord")
    conn.execute(
        "DELETE FROM set_item WHERE question_id IS NOT NULL AND id NOT IN ("
        "SELECT MIN(id) FROM set_item WHERE question_id IS NOT NULL "
        "GROUP BY set_id, question_id)"
    )
    for set_row in conn.execute("SELECT DISTINCT set_id FROM set_item").fetchall():
        item_rows = conn.execute(
            "SELECT id FROM set_item WHERE set_id = ? ORDER BY ord, id",
            (set_row["set_id"],),
        ).fetchall()
        for index, item_row in enumerate(item_rows, start=1):
            conn.execute("UPDATE set_item SET ord = ? WHERE id = ?", (-index, item_row["id"]))
        conn.execute("UPDATE set_item SET ord = -ord WHERE set_id = ?", (set_row["set_id"],))
    conn.execute(
        "CREATE UNIQUE INDEX idx_set_item_question "
        "ON set_item(set_id, question_id) WHERE question_id IS NOT NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX idx_set_item_ord ON set_item(set_id, ord)"
    )

    # ExamMaker 청사진 — 세트 약칭(그림 파일명 규약 {short_code}_{번호2자리}의 앞부분)
    _add_column(conn, "exam_set", "short_code", "TEXT NOT NULL DEFAULT ''")
    _repair_authoring_text_envelopes(conn)
    # 슬롯 계획 필드. question_id 를 NULL 허용으로 바꿔야 해서(SQLite 는 제약 변경 불가)
    # 한 번은 테이블 재구성이 필요하다 — 데이터는 그대로 복사한다.
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
        try:
            conn.execute(f"INSERT INTO set_item ({old_cols}) SELECT {old_cols} FROM set_item_old")
        except Exception:
            conn.execute("DROP TABLE set_item")
            conn.execute("ALTER TABLE set_item_old RENAME TO set_item")
            raise
        conn.execute("DROP TABLE set_item_old")


# ===== 성취기준 seed 적재 =====
# 교육과정은 참고 자료라 seed 파일이 원본이다. 첫 실행뿐 아니라 seed 가 늘어났을 때도
# (중학교만 있다가 고등학교 과목이 추가되는 식) 맞춰 넣는다. 선생님이 만든 명제·문항은
# 건드리지 않으므로, 없어진 성취기준을 지우지는 않는다.
def _seed_standards(conn: sqlite3.Connection) -> None:
    seed_file = SEED_DIR / "standards.json"
    try:
        data = json.loads(Path(seed_file).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"[db] standards seed 파일 로드 실패: {e} — seed 적재를 건너뜁니다.")
        return

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
