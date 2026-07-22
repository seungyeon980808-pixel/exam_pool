import unittest

from app import export_palette as ep


class TestExportPalette(unittest.TestCase):
    def test_answer_type_basic(self):
        q = {"qtype": "정답형", "passage": "다음은 빛의 굴절에 대한 설명이다.",
             "ask": "옳은 것은?", "is_negative": False, "bogi_items": "[]"}
        choices = [
            {"ord": 1, "text": "빛의 속력은 모든 매질에서 같다", "is_answer": False},
            {"ord": 2, "text": "굴절각은 입사각보다 작을 수 있다", "is_answer": True},
        ]
        out = ep.question_to_palette(q, choices, num=1)
        self.assertIn("번호: 1", out)
        self.assertIn("발문: 다음은 빛의 굴절", out)
        self.assertIn("질문: 옳은 것은?", out)
        self.assertIn("선지:", out)
        self.assertIn("빛의 속력은 모든 매질에서 같다", out)
        # 정답형은 보기 없음
        self.assertNotIn("보기:", out)

    def test_negative_ask_bolds_keyword(self):
        q = {"qtype": "정답형", "ask": "옳지 않은 것은?", "is_negative": True, "bogi_items": "[]"}
        out = ep.question_to_palette(q, [{"ord": 1, "text": "a"}], num=1)
        self.assertIn("질문: \\굵게{옳지 않은} 것은?", out)

    def test_hapdap_bogi_and_combo(self):
        q = {"qtype": "합답형", "ask": "옳은 것만을 고른 것은?", "is_negative": False,
             "bogi_items": '[{"label":"ㄱ","text":"빛의 속력은 매질마다 다르다"},'
                           '{"label":"ㄴ","text":"굴절각은 입사각보다 작다"},'
                           '{"label":"ㄷ","text":"평면거울 상은 좌우가 바뀐다"}]'}
        choices = [
            {"ord": 1, "combo": '["ㄱ"]'},
            {"ord": 3, "combo": '["ㄱ","ㄷ"]', "is_answer": True},
            {"ord": 5, "combo": '["ㄱ","ㄴ","ㄷ"]'},
        ]
        out = ep.question_to_palette(q, choices, num=2)
        self.assertIn("보기:", out)
        self.assertIn("빛의 속력은 매질마다 다르다", out)
        # combo 선지
        self.assertIn("ㄱ, ㄷ", out)
        self.assertIn("ㄱ, ㄴ, ㄷ", out)

    def test_photo_material(self):
        q = {"qtype": "정답형", "ask": "?", "material": "fig_refraction_01",
             "is_negative": False, "bogi_items": "[]"}
        out = ep.question_to_palette(q, [{"ord": 1, "text": "a"}])
        self.assertIn("사진자료: \\fig_refraction_01\\", out)

    def test_text_material(self):
        q = {"qtype": "정답형", "ask": "?", "material": "실험 결과 표는 다음과 같다.",
             "is_negative": False, "bogi_items": "[]"}
        out = ep.question_to_palette(q, [{"ord": 1, "text": "a"}])
        self.assertIn("자료: 실험 결과 표는 다음과 같다.", out)

    def test_set_markdown_numbers_sequentially(self):
        q = {"qtype": "정답형", "ask": "?", "is_negative": False, "bogi_items": "[]"}
        c = [{"ord": 1, "text": "a"}]
        md = ep.set_to_markdown([(q, c), (q, c)])
        self.assertIn("번호: 1", md)
        self.assertIn("번호: 2", md)


if __name__ == "__main__":
    unittest.main()
