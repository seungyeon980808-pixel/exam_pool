# -*- coding: utf-8 -*-
"""ExamMaker 쓰기 MCP 툴 테스트 — create_question / update_question / 청사진 슬롯."""
import unittest

from app import db
from app import mcp_server as m
from app import routes_question

MARK = "MCP쓰기테스트"


class TestMcpWrite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def setUp(self):
        self._cleanup()
        conn = db.connect()
        try:
            self.std = conn.execute(
                "SELECT code FROM standard ORDER BY code LIMIT 1").fetchone()["code"]
            self.set_id = conn.execute(
                "INSERT INTO exam_set (name, short_code) VALUES (?, ?)",
                (MARK, "T-기말")).lastrowid
            self.slot_id = conn.execute(
                "INSERT INTO set_item (set_id, question_id, ord, points, plan_qtype, "
                " plan_standard_code, plan_needs_figure, slot_status) "
                "VALUES (?, NULL, 3, 4.0, '합답형', ?, 1, 'empty')",
                (self.set_id, self.std)).lastrowid
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        conn = db.connect()
        try:
            conn.execute(
                "DELETE FROM set_item WHERE set_id IN "
                "(SELECT id FROM exam_set WHERE name = ?)", (MARK,))
            conn.execute("DELETE FROM exam_set WHERE name = ?", (MARK,))
            conn.execute(
                "DELETE FROM choice WHERE question_id IN "
                "(SELECT id FROM question WHERE title = ?)", (MARK,))
            conn.execute("DELETE FROM question WHERE title = ?", (MARK,))
            conn.commit()
        finally:
            conn.close()

    def _create(self):
        r = m.create_question(
            qtype="합답형", ask="옳은 것을 고른 것은?", material="T-기말_03",
            bogi_items=[{"label": "ㄱ", "text": "빛은 직진한다"}],
            choices=[{"ord": 1, "text": "ㄱ", "is_answer": True},
                     {"ord": 2, "text": "ㄴ"}],
            standard_code=self.std)
        qid = r["id"]
        conn = db.connect()
        try:
            conn.execute("UPDATE question SET title = ? WHERE id = ?", (MARK, qid))
            conn.commit()
        finally:
            conn.close()
        return qid

    def test_create_question_sets_origin_ai(self):
        qid = self._create()
        conn = db.connect()
        try:
            q = conn.execute("SELECT * FROM question WHERE id = ?", (qid,)).fetchone()
            n = conn.execute("SELECT COUNT(*) FROM choice WHERE question_id = ?",
                             (qid,)).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(q["origin"], "AI초안")
        self.assertEqual(q["material"], "T-기말_03")
        self.assertEqual(n, 2)

    def test_update_question_partial_merge(self):
        qid = self._create()
        m.update_question(qid, ask="옳지 않은 것을 고른 것은?")
        conn = db.connect()
        try:
            q = conn.execute("SELECT * FROM question WHERE id = ?", (qid,)).fetchone()
            n = conn.execute("SELECT COUNT(*) FROM choice WHERE question_id = ?",
                             (qid,)).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(q["ask"], "옳지 않은 것을 고른 것은?")
        self.assertEqual(q["material"], "T-기말_03")   # 안 넘긴 필드 유지
        self.assertEqual(n, 2)                          # 선지 유지
        # material 지우기는 "-"
        m.update_question(qid, material="-")
        conn = db.connect()
        try:
            q = conn.execute("SELECT material FROM question WHERE id = ?", (qid,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(q["material"], "")

    def test_update_refuses_completed(self):
        qid = self._create()
        conn = db.connect()
        try:
            conn.execute("UPDATE question SET status = '완성' WHERE id = ?", (qid,))
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(ValueError):
            m.update_question(qid, ask="바꿔치기")

    def test_question_pool_sort_and_updated_at(self):
        older_id = self._create()
        newer_id = self._create()
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE question SET created_at=?, updated_at=? WHERE id=?",
                ("2025-01-01 09:00:00", "2025-01-01 09:00:00", older_id),
            )
            conn.execute(
                "UPDATE question SET created_at=?, updated_at=? WHERE id=?",
                ("2025-02-01 09:00:00", "2025-02-01 09:00:00", newer_id),
            )
            conn.commit()
        finally:
            conn.close()

        newest_first = routes_question.list_questions(q=MARK, sort="created", direction="desc")
        oldest_first = routes_question.list_questions(q=MARK, sort="created", direction="asc")
        self.assertEqual([r["id"] for r in newest_first], [newer_id, older_id])
        self.assertEqual([r["id"] for r in oldest_first], [older_id, newer_id])
        self.assertIn("review_error_count", newest_first[0])
        self.assertIn("usage_count", newest_first[0])

        m.update_question(older_id, ask="수정 시각 확인용 발문")
        conn = db.connect()
        try:
            updated_at = conn.execute(
                "SELECT updated_at FROM question WHERE id=?", (older_id,)
            ).fetchone()["updated_at"]
        finally:
            conn.close()
        self.assertGreater(updated_at, "2025-01-01 09:00:00")

    def test_blueprint_and_attach(self):
        bp = m.get_blueprint(self.set_id)
        self.assertEqual(bp["set"]["short_code"], "T-기말")
        slot = bp["slots"][0]
        self.assertIsNone(slot["question_id"])
        self.assertEqual(slot["figure_name"], "T-기말_03")   # 파일명 규약

        qid = self._create()
        m.attach_to_set(self.set_id, slot["item_id"], qid)
        bp2 = m.get_blueprint(self.set_id)
        self.assertEqual(bp2["slots"][0]["question_id"], qid)
        self.assertEqual(bp2["slots"][0]["slot_status"], "generated")

        # 이미 찬 슬롯은 덮어쓰기 거부
        with self.assertRaises(ValueError):
            m.attach_to_set(self.set_id, slot["item_id"], qid)
