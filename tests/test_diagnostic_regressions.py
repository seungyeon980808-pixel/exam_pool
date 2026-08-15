from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import db, routes_doc, routes_question
from app.authoring.figures import FiveELocalProvider, close_figure_providers


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Given an empty database, isolate every regression scenario from user data."""
    database = tmp_path / "diagnostic.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(db, "_inited", False)
    db.init_db()
    return database


def _question(conn: sqlite3.Connection, ask: str) -> int:
    cursor = conn.execute(
        "INSERT INTO question (ask, qtype, status) VALUES (?, ?, ?)",
        (ask, "객관식", "초안"),
    )
    return int(cursor.lastrowid)


def _set(conn: sqlite3.Connection) -> int:
    cursor = conn.execute("INSERT INTO exam_set (name) VALUES (?)", ("회귀 세트",))
    return int(cursor.lastrowid)


def _session(conn: sqlite3.Connection) -> int:
    cursor = conn.execute("INSERT INTO authoring_session DEFAULT VALUES")
    return int(cursor.lastrowid)


def test_draft_references_are_isolated_by_authoring_session(isolated_db: Path) -> None:
    """Given two drafts, when they attach one source, then each owns a separate reference."""
    with db.transaction() as conn:
        first_session = _session(conn)
        second_session = _session(conn)

    first = routes_doc.create_ref(routes_doc.RefIn(
        document_id=7,
        doc_title="기출",
        page_no=3,
        item_num=2,
        authoring_session_id=first_session,
    ))
    second = routes_doc.create_ref(routes_doc.RefIn(
        document_id=7,
        doc_title="기출",
        page_no=3,
        item_num=2,
        authoring_session_id=second_session,
    ))

    assert first["id"] != second["id"]
    assert [row["id"] for row in routes_doc.list_refs(authoring_session_id=first_session)] == [first["id"]]
    assert [row["id"] for row in routes_doc.list_refs(authoring_session_id=second_session)] == [second["id"]]


def test_question_delete_removes_its_references(isolated_db: Path) -> None:
    """Given a saved question reference, when the question is deleted, then no orphan remains."""
    with db.transaction() as conn:
        question_id = _question(conn, "삭제할 문항")
        conn.execute(
            "INSERT INTO exam_ref (document_id, doc_title, page_no, item_num, question_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (9, "기출", 1, 1, question_id),
        )

    routes_question.delete_question(question_id)

    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM exam_ref WHERE question_id = ?",
            (question_id,),
        ).fetchone()["count"]
    assert count == 0


def test_set_rejects_duplicate_question(isolated_db: Path) -> None:
    """Given one set item, when it is added again, then the API rejects the duplicate."""
    with db.transaction() as conn:
        question_id = _question(conn, "중복 방지")
        set_id = _set(conn)

    routes_question.add_set_item(set_id, routes_question.SetItemIn(question_id=question_id))

    with pytest.raises(HTTPException) as error:
        routes_question.add_set_item(set_id, routes_question.SetItemIn(question_id=question_id))
    assert error.value.status_code == 409


def test_set_reorder_requires_exact_membership(isolated_db: Path) -> None:
    """Given three set items, when reorder omits one, then ordering remains unchanged."""
    with db.transaction() as conn:
        question_ids = [_question(conn, f"문항 {index}") for index in range(3)]
        set_id = _set(conn)
    for question_id in question_ids:
        routes_question.add_set_item(set_id, routes_question.SetItemIn(question_id=question_id))

    with pytest.raises(HTTPException) as error:
        routes_question.reorder_set(
            set_id,
            routes_question.ReorderIn(question_ids=list(reversed(question_ids[1:]))),
        )
    assert error.value.status_code == 409

    with db.connect() as conn:
        actual = [
            row["question_id"]
            for row in conn.execute(
                "SELECT question_id FROM set_item WHERE set_id = ? ORDER BY ord",
                (set_id,),
            )
        ]
    assert actual == question_ids


def test_close_figure_providers_stops_owned_static_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given a provider-owned server, when the app shuts down, then the listener is terminated."""
    class Process:
        terminated = False

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: int) -> int:
            assert timeout == 3
            return 0

    process = Process()
    monkeypatch.setattr(FiveELocalProvider, "_server_process", process)
    monkeypatch.setattr(FiveELocalProvider, "_mcp_client", None)

    close_figure_providers()

    assert process.terminated is True
    assert FiveELocalProvider._server_process is None


def test_fivee_launch_claims_bridge_and_uses_identifiable_server() -> None:
    """Given ExamPool launches 5E, then ownership and runtime provenance are explicit."""
    root = Path(__file__).resolve().parents[1]
    authoring = (root / "static" / "js" / "authoring.js").read_text(encoding="utf-8")
    launcher = (root / "launch_all.bat").read_text(encoding="utf-8")
    fivee_root = root.parent / "51_5E" / "5E_main"
    bridge = (fivee_root / "js" / "mcp-bridge.js").read_text(encoding="utf-8")
    server = (fivee_root / "tools" / "serve.py").read_text(encoding="utf-8")

    assert 'searchParams.set("claim", "1")' in authoring
    assert "connect(port, claim)" in bridge
    assert '"/__5e_health"' in server
    assert "tools\\serve.py 8611" in launcher


def test_legacy_database_repairs_before_creating_owner_and_set_indexes(isolated_db: Path) -> None:
    """Given a legacy schema and duplicate set rows, startup migrates without failing."""
    with db.transaction() as conn:
        conn.execute("DROP INDEX idx_ref_owner_uniq")
        conn.execute("ALTER TABLE exam_ref RENAME TO exam_ref_new")
        conn.execute(
            "CREATE TABLE exam_ref (id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL, "
            "doc_title TEXT NOT NULL, page_no INTEGER NOT NULL, item_num INTEGER NOT NULL, "
            "note TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '', question_id INTEGER, "
            "created_at TEXT NOT NULL DEFAULT '')"
        )
        conn.execute("DROP TABLE exam_ref_new")
        conn.execute("DROP INDEX idx_set_item_question")
        conn.execute("DROP INDEX idx_set_item_ord")
        question_id = _question(conn, "legacy duplicate")
        set_id = _set(conn)
        conn.execute(
            "INSERT INTO set_item (set_id, question_id, ord) VALUES (?, ?, 1), (?, ?, 1)",
            (set_id, question_id, set_id, question_id),
        )

    db._inited = False
    db.init_db()

    with db.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(exam_ref)")}
        rows = conn.execute(
            "SELECT question_id, ord FROM set_item WHERE set_id = ? ORDER BY ord",
            (set_id,),
        ).fetchall()
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(set_item)")}
    assert "authoring_session_id" in columns
    assert [(row["question_id"], row["ord"]) for row in rows] == [(question_id, 1)]
    assert {"idx_set_item_question", "idx_set_item_ord"} <= indexes
