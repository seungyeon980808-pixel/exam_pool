# -*- coding: utf-8 -*-
"""hwppalette 템플릿 출력 검증.

hwppalette 는 `\\라벨\\` 다음 줄부터 빈칸을 순서대로 채운다.

빈칸 순서는 31_hwp_palette/fragments/*.hwp 조각 파일을 직접 열어 확인한 것이다
(2026-07-27). 합답형 템플릿은 본문에 "… 고른 것은? (\\점)" 줄이 〈보 기〉 상자보다
앞에 있으므로 순서가 이렇게 된다:

    번호 → 발문 → 사진 → **점수** → ㄱㄴㄷ → 선지①~⑤
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
        self.assertEqual(out[4], "3")                         # 점수 — 〈보기〉 바로 앞
        self.assertEqual(out[5], "빛의 속력은 매질마다 다르다")   # ㄱ
        self.assertEqual(out[7], "평면거울 상은 좌우가 바뀐다")   # ㄷ
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
        self.assertEqual(out[3], "3")                         # 사진 칸 없이 바로 점수
        self.assertEqual(out[4], "빛의 속력은 매질마다 다르다")   # 그다음이 ㄱ

    def test_empty_slot_uses_dash(self):
        """보기가 2개뿐이면 남는 칸은 '-' 로 비운다."""
        q = hapdap(bogi_items='[{"label":"ㄱ","text":"가"},{"label":"ㄴ","text":"나"}]')
        out = ep.question_to_palette(q, combos()).split("\n")
        self.assertEqual(out[7], "-")                          # ㄷ 칸

    def test_multiline_passage_stays_one_slot(self):
        """여러 줄 지문은 { } 로 묶어 한 칸에 넣는다.

        묶지 않으면 줄 수만큼 빈칸을 먹어 점수·보기·선지가 한 칸씩 밀린다.
        """
        q = hapdap(material="", passage="(가) 물을 붓는다.\n(나) 빛을 비춘다.")
        out = ep.question_to_palette(q, combos()).split("\n")
        self.assertEqual(out[2], "{(가) 물을 붓는다.")
        self.assertTrue(out[4].endswith("}"), "블록이 닫혀야 한다")
        self.assertEqual(out[5], "3", "블록 다음 칸은 점수여야 한다")
        self.assertEqual(out[6], "빛의 속력은 매질마다 다르다", "그다음이 ㄱ")

    def test_negative_ask_bolds(self):
        out = ep.question_to_palette(hapdap(ask="옳지 않은 것은?", is_negative=True), combos())
        self.assertIn("\\굵게{옳지 않은}", out)

    def test_no_colon_syntax_anywhere(self):
        """콜론 문법(발문:/질문:)은 쓰지 않는다 — 사용자는 \\ 와 {} 만 쓴다."""
        out = ep.question_to_palette(hapdap(), combos())
        for bad in ("발문:", "질문:", "보기:", "선지:", "번호:", "자료:", "사진자료:"):
            self.assertNotIn(bad, out, f"콜론 문법 '{bad}' 이 남아 있다")

    def test_fallback_when_template_missing(self):
        """등록되지 않은 유형은 사람이 읽을 수 있는 평문으로 떨어진다."""
        q = hapdap(qtype="단답형", material="")
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
        self.assertEqual(ep.pick_template(hapdap(qtype="정답형")), "학교정답0사진1선지")


# ===== 학교 지필용 정답형 =====
def jungdap(**kw):
    q = {"qtype": "정답형", "is_negative": False, "passage": "", "material": "",
         "ask": "마찰에 대한 설명으로 옳은 것은?", "default_points": 3, "bogi_items": "[]"}
    q.update(kw)
    return q


def five(*texts):
    return [{"ord": i + 1, "text": t} for i, t in enumerate(texts)]


class TestCorrectAnswerTemplate(unittest.TestCase):
    """빈칸 순서(fragments/f7fbdd7a….hwp 확인): 번호 → 발문 → 점수 → 선지①~⑤"""

    def test_slot_order(self):
        out = ep.question_to_palette(
            jungdap(), five("가", "나", "다", "라", "마"), num=4).split("\n")
        self.assertEqual(out[0], "\\학교정답0사진1선지\\")
        self.assertEqual(len(out) - 1, 8, "빈칸이 8개여야 한다")
        self.assertEqual(out[1], "4")                      # 번호
        self.assertIn("마찰", out[2])                       # 발문
        self.assertEqual(out[3], "3")                      # 점수
        self.assertEqual(out[4], "가")                      # ①
        self.assertEqual(out[8], "마")                      # ⑤

    def test_passage_becomes_block(self):
        """지문 칸이 따로 없으므로 지문+발문을 { } 블록 한 칸에 넣는다."""
        out = ep.question_to_palette(
            jungdap(passage="그림은 검전기를 나타낸 것이다."),
            five("가", "나", "다", "라", "마")).split("\n")
        self.assertTrue(out[2].startswith("{") and out[2].endswith("}") is False
                        or "\n" not in out[2])
        joined = "\n".join(out)
        self.assertIn("{그림은 검전기를 나타낸 것이다.", joined)
        self.assertIn("마찰에 대한 설명으로 옳은 것은?}", joined)

    def test_photo_goes_into_block(self):
        """정답형 템플릿엔 사진 칸이 없다 — 발문 블록 안에 라벨로 넣는다."""
        out = ep.question_to_palette(
            jungdap(material="검전기01"), five("가", "나", "다", "라", "마"))
        self.assertIn("\\검전기01\\", out)

    def test_missing_choices_use_dash(self):
        out = ep.question_to_palette(jungdap(), five("가", "나")).split("\n")
        self.assertEqual(out[6], "-")
        self.assertEqual(out[8], "-")


# ===== 서술형 =====
class TestEssay(unittest.TestCase):
    def test_answer_box_and_points(self):
        q = jungdap(qtype="서술형", ask="까닭을 서술하시오.", default_points=5)
        out = ep.question_to_palette(q, [], num=12).split("\n")
        self.assertIn("12. 까닭을 서술하시오. (5점)", out)
        self.assertIn("\\표1*1\\", out)
        self.assertEqual(out[-1], "-", "표 다음 줄의 '-' 가 빈 답란이 된다")

    def test_passage_kept(self):
        q = jungdap(qtype="서술형", passage="그림은 전동기이다.", ask="원리를 쓰시오.")
        out = ep.question_to_palette(q, [])
        self.assertIn("그림은 전동기이다.", out)


# ===== 레거시 문법 충돌 방지 =====
class TestLegacyGuard(unittest.TestCase):
    def test_bold_braces_survive_escaping(self):
        """원문의 '}' 는 escape 하되 \\굵게{...} 의 닫는 괄호는 건드리면 안 된다."""
        q = jungdap(passage="그림은 검전기이다.", ask="옳지 않은 것은?", is_negative=True)
        out = ep.question_to_palette(q, five("가", "나", "다", "라", "마"))
        self.assertIn("\\굵게{옳지 않은}", out)
        self.assertNotIn("\\굵게{옳지 않은\\}", out)

    def test_literal_brace_in_text_is_escaped(self):
        q = jungdap(passage="집합 {1, 2} 를 생각하자.")
        out = ep.question_to_palette(q, five("가", "나", "다", "라", "마"))
        self.assertIn("{1, 2\\}", out)

    def test_leading_keyword_is_defused(self):
        """지문 줄이 '자료:' 로 시작하면 hwppalette 가 템플릿 경로를 건너뛴다."""
        q = jungdap(passage="자료: 표는 소비 전력을 나타낸 것이다.")
        out = ep.question_to_palette(q, five("가", "나", "다", "라", "마"))
        self.assertNotIn("\n자료:", out)
        self.assertIn("자료 :", out)


if __name__ == "__main__":
    unittest.main()
