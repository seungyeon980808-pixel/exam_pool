"""보강한 검토 규칙 — 형식이 아니라 실제로 사고가 나는 지점을 잡는지."""
import unittest

from app import checklist as cl


def q_base(**kw):
    q = {"qtype": "정답형", "ask": "옳은 것은?", "difficulty": "중",
         "default_points": 3.0, "standard_code": "[9과10-01]"}
    q.update(kw)
    return q


def choices(texts, answer_idx, **extra):
    out = []
    for i, t in enumerate(texts):
        c = {"ord": i + 1, "text": t, "proposition_id": i + 1,
             "is_answer": i == answer_idx}
        c.update(extra)
        out.append(c)
    return out


class TestNegativeMark(unittest.TestCase):
    def test_negative_ask_without_flag_warns(self):
        q = q_base(ask="이에 대한 설명으로 옳지 않은 것은?", is_negative=False)
        codes = [i["code"] for i in cl.check_question(q, choices(["a", "b"], 0))]
        self.assertIn("negative_unmarked", codes)

    def test_flag_without_negative_ask_warns(self):
        q = q_base(ask="이에 대한 설명으로 옳은 것은?", is_negative=True)
        codes = [i["code"] for i in cl.check_question(q, choices(["a", "b"], 0))]
        self.assertIn("negative_mismatch", codes)

    def test_matching_pair_is_quiet(self):
        q = q_base(ask="옳지 않은 것은?", is_negative=True)
        codes = [i["code"] for i in cl.check_question(q, choices(["a", "b"], 0))]
        self.assertNotIn("negative_unmarked", codes)
        self.assertNotIn("negative_mismatch", codes)


class TestAnswerLength(unittest.TestCase):
    def test_long_answer_warns(self):
        q = q_base()
        texts = ["짧다", "짧다", "짧다", "이 선지는 다른 선지들보다 훨씬 길게 쓰여 있어서 눈에 띈다"]
        codes = [i["code"] for i in cl.check_question(q, choices(texts, 3))]
        self.assertIn("answer_longest", codes)

    def test_even_lengths_are_quiet(self):
        q = q_base()
        texts = ["가나다라마", "가나다라마", "가나다라마", "가나다라바"]
        codes = [i["code"] for i in cl.check_question(q, choices(texts, 1))]
        self.assertNotIn("answer_longest", codes)

    def test_combo_type_is_skipped(self):
        """합답형은 선지가 'ㄱ, ㄴ' 조합이라 길이가 뜻을 갖지 않는다."""
        q = q_base(qtype="합답형")
        texts = ["ㄱ", "ㄴ", "ㄷ", "ㄱ, ㄴ, ㄷ 를 모두 포함하는 긴 선지처럼 보이는 것"]
        codes = [i["code"] for i in cl.check_question(q, choices(texts, 3))]
        self.assertNotIn("answer_longest", codes)


def item(answer_ord, n_choices=5, prop_ids=None, points=4.0):
    chs = []
    for i in range(n_choices):
        chs.append({"ord": i + 1, "text": f"선지{i + 1}",
                    "proposition_id": (prop_ids or [None] * n_choices)[i] or (100 + i),
                    "is_answer": (i + 1) == answer_ord})
    return {"question": q_base(), "choices": chs, "points": points}


class TestAnswerSpread(unittest.TestCase):
    def test_bias_warns(self):
        items = [item(3) for _ in range(5)]      # 전부 ③
        codes = [i["code"] for i in cl.check_set({}, items, target_total=20.0)]
        self.assertIn("answer_bias", codes)
        self.assertIn("answer_unused", codes)

    def test_even_spread_is_quiet(self):
        items = [item(o) for o in (1, 2, 3, 4, 5)]
        codes = [i["code"] for i in cl.check_set({}, items, target_total=20.0)]
        self.assertNotIn("answer_bias", codes)
        self.assertNotIn("answer_unused", codes)

    def test_short_set_is_skipped(self):
        """4문항짜리는 분포를 논할 표본이 아니다."""
        items = [item(1) for _ in range(4)]
        codes = [i["code"] for i in cl.check_set({}, items, target_total=16.0)]
        self.assertNotIn("answer_bias", codes)


class TestDuplicateProps(unittest.TestCase):
    def test_same_proposition_twice_warns(self):
        a = item(1, prop_ids=[7, 2, 3, 4, 5])
        b = item(2, prop_ids=[7, 12, 13, 14, 15])
        codes = [i["code"] for i in cl.check_set({}, [a, b], target_total=8.0)]
        self.assertIn("duplicate_proposition", codes)

    def test_distinct_propositions_are_quiet(self):
        a = item(1, prop_ids=[1, 2, 3, 4, 5])
        b = item(2, prop_ids=[11, 12, 13, 14, 15])
        codes = [i["code"] for i in cl.check_set({}, [a, b], target_total=8.0)]
        self.assertNotIn("duplicate_proposition", codes)

    def test_bogi_items_count_too(self):
        """합답형은 근거가 선지가 아니라 보기에 있다."""
        a = item(1, prop_ids=[1, 2, 3, 4, 5])
        a["question"] = q_base(qtype="합답형", bogi_items=[{"proposition_id": 99}])
        b = item(2, prop_ids=[11, 12, 13, 14, 15])
        b["question"] = q_base(bogi_items='[{"proposition_id": 99}]')   # JSON 문자열도 받는다
        codes = [i["code"] for i in cl.check_set({}, [a, b], target_total=8.0)]
        self.assertIn("duplicate_proposition", codes)


class TestSetTotalPoints(unittest.TestCase):
    def test_uses_set_total_points_when_target_omitted(self):
        """세트에 저장된 만점(70점)을 쓰면 배점 합 70은 경고가 아니다."""
        items = [item(o, points=10.0) for o in (1, 2, 3, 4, 5, 1, 2)]
        codes = [i["code"] for i in cl.check_set({"total_points": 70.0}, items)]
        self.assertNotIn("points_mismatch", codes)

    def test_falls_back_to_100(self):
        items = [item(1, points=10.0)]
        codes = [i["code"] for i in cl.check_set({}, items)]
        self.assertIn("points_mismatch", codes)


if __name__ == "__main__":
    unittest.main()
