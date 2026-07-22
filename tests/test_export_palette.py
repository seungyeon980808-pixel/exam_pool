# -*- coding: utf-8 -*-
"""hwppalette 템플릿 출력 검증.

hwppalette 는 `\\라벨\\` 다음 줄부터 빈칸을 순서대로 채운다.
빈칸 순서(2026-07-22 사용자 확인): 번호 → 발문 → 사진 → ㄱㄴㄷ → 점수 → 선지①~⑤
"""
import unittest

from app import export_palette as ep


def hapdap(**kw):
    q = {"qtype": "합답형", "is_negative": False, "passage": "다음은 빛의 굴절에 대한 설명이다.",
         "ask": "옳은 것만을 〈보기〉에서 있는 대로 고른 것은?", "material": "굴절_그림01",
         "default_points": 3,
         "bogi_items": '[{"label":"ㄱ","text":"빛의 속력은 매질마다 다르다"},'
                       '{"label":"ㄴ","text":"굴절각은 입사각보다 작다"},'
                       '{"label":"ㄷ","text":"평면거울 상은 좌우가 바뀐다"}]'}
    q.update(kw)
    return q


def combos():
    return [{"ord": 1, "combo": '["ㄱ"]'}, {"ord": 2, "combo": '["ㄴ"]'},
            {"ord": 3, "combo": '["ㄱ","ㄷ"]'}, {"ord": 4, "combo": '["ㄴ","ㄷ"]'},
            {"ord": 5, "combo": '["ㄱ","ㄴ","ㄷ"]'}]


class TestTemplateOutput(unittest.TestCase):
    def test_photo1_template_and_slot_order(self):
        out = ep.question_to_palette(hapdap(), combos(), num=7).split("\n")
        self.assertEqual(out[0], "\\합답형1사진3선지\\")
        self.assertEqual(len(out) - 1, 12, "빈칸이 12개여야 한다")
        self.assertEqual(out[1], "7")                         # 번호
        self.assertIn("빛의 굴절", out[2])                     # 발문(지문+질문)
        self.assertEqual(out[3], "굴절_그림01")                # 사진
        self.assertEqual(out[4], "빛의 속력은 매질마다 다르다")   # ㄱ
        self.assertEqual(out[6], "평면거울 상은 좌우가 바뀐다")   # ㄷ
        self.assertEqual(out[7], "3")                         # 점수 — 선지 바로 위
        self.assertEqual(out[8], "ㄱ")                        # ①
        self.assertEqual(out[10], "ㄱ, ㄷ")                    # ③
        self.assertEqual(out[12], "ㄱ, ㄴ, ㄷ")                 # ⑤

    def test_two_photos_template(self):
        out = ep.question_to_palette(hapdap(material="그림가.png, 그림나.png"), combos()).split("\n")
        self.assertEqual(out[0], "\\합답형2사진3선지\\")
        self.assertEqual(len(out) - 1, 13)
        self.assertEqual(out[3], "그림가.png")
        self.assertEqual(out[4], "그림나.png")

    def test_no_photo_template(self):
        out = ep.question_to_palette(hapdap(material=""), combos()).split("\n")
        self.assertEqual(out[0], "\\합답형실험3선지\\")
        self.assertEqual(len(out) - 1, 11)
        self.assertEqual(out[3], "빛의 속력은 매질마다 다르다")   # 사진 칸 없이 바로 ㄱ

    def test_empty_slot_uses_dash(self):
        """보기가 2개뿐이면 남는 칸은 '-' 로 비운다."""
        q = hapdap(bogi_items='[{"label":"ㄱ","text":"가"},{"label":"ㄴ","text":"나"}]')
        out = ep.question_to_palette(q, combos()).split("\n")
        self.assertEqual(out[6], "-")

    def test_negative_ask_bolds(self):
        out = ep.question_to_palette(hapdap(ask="옳지 않은 것은?", is_negative=True), combos())
        self.assertIn("\\굵게{옳지 않은}", out)

    def test_no_colon_syntax_anywhere(self):
        """콜론 문법(발문:/질문:)은 쓰지 않는다 — 사용자는 \\ 와 {} 만 쓴다."""
        out = ep.question_to_palette(hapdap(), combos())
        for bad in ("발문:", "질문:", "보기:", "선지:", "번호:", "자료:", "사진자료:"):
            self.assertNotIn(bad, out, f"콜론 문법 '{bad}' 이 남아 있다")

    def test_fallback_when_template_missing(self):
        """정답형은 템플릿 미등록 — 사람이 읽을 수 있는 평문으로 떨어진다."""
        q = hapdap(qtype="정답형", material="")
        out = ep.question_to_palette(q, [{"ord": 1, "text": "선지 하나"}])
        self.assertIn("템플릿 미등록", out)
        self.assertIn("선지 하나", out)

    def test_set_numbers_sequentially(self):
        md = ep.set_to_markdown([(hapdap(), combos()), (hapdap(), combos())])
        blocks = md.strip().split("\n\n")
        self.assertEqual(blocks[0].split("\n")[1], "1")
        self.assertEqual(blocks[1].split("\n")[1], "2")

    def test_pick_template(self):
        self.assertEqual(ep.pick_template(hapdap()), "합답형1사진3선지")
        self.assertEqual(ep.pick_template(hapdap(material="")), "합답형실험3선지")
        self.assertEqual(ep.pick_template(hapdap(qtype="정답형")), "")


if __name__ == "__main__":
    unittest.main()
