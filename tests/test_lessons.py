"""수업 기록 — 조각내기와 문서/수업 구분 규칙."""
import sqlite3
import unittest

from app import lessons


class TestIdMapping(unittest.TestCase):
    def test_lesson_ids_are_negative(self):
        self.assertEqual(lessons.doc_id_of(7), -7)
        self.assertTrue(lessons.is_lesson(-7))
        self.assertFalse(lessons.is_lesson(7))
        self.assertEqual(lessons.lesson_id_of(-7), 7)
        self.assertIsNone(lessons.lesson_id_of(7))


class TestChunking(unittest.TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(lessons.split_chunks("한 줄짜리 기록"), ["한 줄짜리 기록"])

    def test_blank_lines_dropped(self):
        self.assertEqual(lessons.split_chunks("가\n\n\n나"), ["가\n나"])

    def test_empty_text_gives_no_chunks(self):
        self.assertEqual(lessons.split_chunks(""), [])
        self.assertEqual(lessons.split_chunks("   \n  "), [])

    def test_long_text_splits_on_line_boundaries(self):
        text = "\n".join(f"{i}번째 줄입니다" for i in range(200))
        chunks = lessons.split_chunks(text, size=100)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertNotIn("\n\n", c)
        # 줄이 잘리지 않고 그대로 보존된다
        self.assertEqual("\n".join(chunks), text)


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE lesson (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT,
            class_name TEXT DEFAULT '', transcript TEXT DEFAULT '',
            summary TEXT DEFAULT '', indexed_at TEXT DEFAULT '');
        CREATE VIRTUAL TABLE page_fts USING fts5(
            body, doc_title UNINDEXED, page_no UNINDEXED, document_id UNINDEXED,
            tokenize='unicode61');
    """)
    return conn


class TestIndexing(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()
        self.conn.execute(
            "INSERT INTO lesson (date, class_name, transcript) VALUES (?,?,?)",
            ("2026-03-12", "3반", "빛이 물속으로 들어가면 굴절합니다\n각도가 달라져요"))

    def tearDown(self):
        self.conn.close()

    def test_indexed_rows_use_negative_document_id(self):
        n = lessons.index_lesson(self.conn, 1)
        self.assertEqual(n, 1)
        row = self.conn.execute("SELECT * FROM page_fts").fetchone()
        self.assertEqual(row["document_id"], -1)
        self.assertEqual(row["doc_title"], "수업 2026-03-12 3반")

    def test_searchable_with_same_index(self):
        lessons.index_lesson(self.conn, 1)
        hits = self.conn.execute(
            'SELECT COUNT(*) FROM page_fts WHERE page_fts MATCH ?', ('"굴절"*',)).fetchone()[0]
        self.assertEqual(hits, 1)

    def test_reindex_replaces_not_duplicates(self):
        lessons.index_lesson(self.conn, 1)
        lessons.index_lesson(self.conn, 1)
        n = self.conn.execute("SELECT COUNT(*) FROM page_fts").fetchone()[0]
        self.assertEqual(n, 1)

    def test_unindex_removes(self):
        lessons.index_lesson(self.conn, 1)
        lessons.unindex(self.conn, 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM page_fts").fetchone()[0], 0)

    def test_chunk_text_returns_body(self):
        out = lessons.chunk_text(self.conn, 1, 1)
        self.assertIn("굴절", out["text"])
        self.assertEqual(out["last_chunk"], 1)

    def test_indexed_at_stamped(self):
        lessons.index_lesson(self.conn, 1)
        at = self.conn.execute("SELECT indexed_at FROM lesson WHERE id=1").fetchone()[0]
        self.assertTrue(at)


if __name__ == "__main__":
    unittest.main()
