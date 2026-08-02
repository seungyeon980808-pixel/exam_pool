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


# ===== 서술형 — 선지·정답번호가 없다 =====
def essay(**kw):
    q = {"qtype": "서술형", "ask": "까닭을 서술하시오.", "difficulty": "상",
         "default_points": 4.0, "standard_code": "[9과14-01]", "origin": "직접",
         "answer": "전자가 이동하기 때문이다."}
    q.update(kw)
    return q


class TestEssayQuestion(unittest.TestCase):
    def test_essay_without_choices_is_clean(self):
        """선지가 없다고 오류가 나면 안 된다 — 서술형에는 선지가 없다."""
        s = cl.summarize(cl.check_question(essay(), []))
        self.assertTrue(s["ok"], s)

    def test_essay_needs_model_answer(self):
        codes = [i["code"] for i in cl.check_question(essay(answer=""), [])]
        self.assertIn("no_model_answer", codes)

    def test_essay_skips_choice_rules(self):
        codes = [i["code"] for i in cl.check_question(essay(), [])]
        for c in ("too_few_choices", "no_answer", "choice_no_evidence", "bad_qtype"):
            self.assertNotIn(c, codes)


# ===== 출처 (origin) — status 와 다른 축 =====
def q_with(origin, status="검토중"):
    return {"qtype": "정답형", "ask": "옳은 것은?", "difficulty": "중",
            "default_points": 3.0, "standard_code": "[9과10-01]",
            "origin": origin, "status": status}


class TestOrigin(unittest.TestCase):
    def test_missing_origin_warns_but_not_error(self):
        issues = cl.check_question(q_with(""), good_choices())
        self.assertIn("no_origin", [i["code"] for i in issues])
        self.assertTrue(cl.summarize(issues)["ok"], "출처 미지정은 경고지 오류가 아니다")

    def test_bad_origin_warns(self):
        codes = [i["code"] for i in cl.check_question(q_with("몰라"), good_choices())]
        self.assertIn("bad_origin", codes)

    def test_ai_draft_not_finished_is_set_error(self):
        """AI 초안이 검토 안 된 채 시험지로 나가는 것을 막는다."""
        items = [{"question": q_with("AI초안", "검토중"), "choices": good_choices(), "points": 3.0}]
        s = cl.summarize(cl.check_set({"total_points": 3.0}, items))
        self.assertIn("ai_draft_unreviewed", [i["code"] for i in s["issues"]])
        self.assertFalse(s["ok"])

    def test_ai_draft_finished_passes(self):
        """검토를 마쳤으면 AI 초안이어도 통과한다 — 출처는 남고 상태만 바뀐다."""
        items = [{"question": q_with("AI초안", "완성"), "choices": good_choices(), "points": 3.0}]
        codes = [i["code"] for i in cl.check_set({"total_points": 3.0}, items)]
        self.assertNotIn("ai_draft_unreviewed", codes)

    def test_self_written_draft_is_not_flagged(self):
        items = [{"question": q_with("직접", "검토중"), "choices": good_choices(), "points": 3.0}]
        codes = [i["code"] for i in cl.check_set({"total_points": 3.0}, items)]
        self.assertNotIn("ai_draft_unreviewed", codes)


if __name__ == "__main__":
    unittest.main()
