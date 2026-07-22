# -*- coding: utf-8 -*-
"""기출 스크랩(참고 문항 + 메모) 저장 로직 테스트."""
import unittest

from app import db


class TestExamRef(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def setUp(self):
        self.conn = db.connect()
        self.conn.execute("DELETE FROM exam_ref WHERE doc_title = '테스트기출'")
        self.conn.commit()

    def tearDown(self):
        self.conn.execute("DELETE FROM exam_ref WHERE doc_title = '테스트기출'")
        self.conn.commit()
        self.conn.close()

    def _add(self, num, note=""):
        return self.conn.execute(
            "INSERT INTO exam_ref (document_id, doc_title, page_no, item_num, note) "
            "VALUES (?,?,?,?,?)", (99, "테스트기출", 3, num, note)).lastrowid

    def test_save_and_read(self):
        rid = self._add(13, "굴절률 참고")
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM exam_ref WHERE id=?", (rid,)).fetchone()
        self.assertEqual(row["item_num"], 13)
        self.assertEqual(row["note"], "굴절률 참고")

    def test_same_item_is_unique(self):
        """같은 문항을 두 번 담으면 안 된다 (UNIQUE 인덱스)."""
        self._add(13)
        self.conn.commit()
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self._add(13)
            self.conn.commit()
        self.conn.rollback()

    def test_different_items_ok(self):
        self._add(13); self._add(14)
        self.conn.commit()
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM exam_ref WHERE doc_title='테스트기출'").fetchone()["c"]
        self.assertEqual(n, 2)

    def test_update_note_and_tags(self):
        rid = self._add(13)
        self.conn.execute("UPDATE exam_ref SET note=?, tags=? WHERE id=?",
                          ("변형 아이디어", "굴절,임계각", rid))
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM exam_ref WHERE id=?", (rid,)).fetchone()
        self.assertEqual(row["note"], "변형 아이디어")
        self.assertEqual(row["tags"], "굴절,임계각")

    def test_search_by_note(self):
        self._add(13, "굴절률 정의")
        self._add(14, "전기회로")
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT * FROM exam_ref WHERE doc_title='테스트기출' AND note LIKE ?", ("%굴절%",)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["item_num"], 13)


if __name__ == "__main__":
    unittest.main()
