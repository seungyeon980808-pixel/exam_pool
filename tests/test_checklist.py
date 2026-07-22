import unittest

from app import checklist as cl


def good_choices():
    return [
        {"ord": 1, "text": "a", "proposition_id": 1, "is_answer": False},
        {"ord": 2, "text": "b", "variant_id": 3, "is_answer": True},
    ]


class TestChecklist(unittest.TestCase):
    def test_clean_question_passes(self):
        q = {"qtype": "정답형", "ask": "옳은 것은?", "difficulty": "중",
             "default_points": 3.0, "standard_code": "[9과10-01]"}
        s = cl.summarize(cl.check_question(q, good_choices()))
        self.assertTrue(s["ok"], s)

    def test_missing_answer_is_error(self):
        q = {"qtype": "정답형", "ask": "?", "difficulty": "중", "default_points": 3.0}
        choices = [{"ord": 1, "text": "a", "proposition_id": 1, "is_answer": False}]
        codes = [i["code"] for i in cl.check_question(q, choices)]
        self.assertIn("no_answer", codes)

    def test_choice_without_evidence_is_error(self):
        q = {"qtype": "정답형", "ask": "?", "difficulty": "중", "default_points": 3.0}
        choices = [
            {"ord": 1, "text": "a", "is_answer": True},   # 근거 없음
            {"ord": 2, "text": "b", "proposition_id": 2},
        ]
        codes = [i["code"] for i in cl.check_question(q, choices)]
        self.assertIn("choice_no_evidence", codes)

    def test_empty_ask_is_error(self):
        q = {"qtype": "정답형", "ask": "  ", "difficulty": "중", "default_points": 3.0}
        codes = [i["code"] for i in cl.check_question(q, good_choices())]
        self.assertIn("no_ask", codes)

    def test_set_points_mismatch_warns(self):
        q = {"qtype": "정답형", "ask": "?", "difficulty": "중", "default_points": 3.0,
             "standard_code": "[9과10-01]"}
        items = [{"question": q, "choices": good_choices(), "points": 3.0}]
        codes = [i["code"] for i in cl.check_set({}, items, target_total=100.0)]
        self.assertIn("points_mismatch", codes)

    def test_empty_set_is_error(self):
        s = cl.summarize(cl.check_set({}, []))
        self.assertFalse(s["ok"])


if __name__ == "__main__":
    unittest.main()
