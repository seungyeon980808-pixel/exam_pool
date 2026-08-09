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
        """학교합답1사진5선지 — 번호·지문·사진·발문(점수 포함)·보기·선지."""
        out = ep.question_to_palette(hapdap(), combos(), num=7).split("\n")
        self.assertEqual(out[0], "\\학교합답1사진5선지\\")
        self.assertEqual(len(out) - 1, 12, "빈칸이 12개여야 한다")
        self.assertEqual(out[1], "7")                         # 번호
        self.assertIn("빛의 굴절", out[2])                     # 지문
        self.assertEqual(out[3], "\\굴절_그림01\\")            # 사진 — \라벨\ 이라야 그림이 된다
        self.assertIn("고른 것은?", out[4])                    # 발문
        self.assertIn("(3점)", out[4], "점수 칸이 없는 템플릿은 발문에 붙인다")
        self.assertEqual(out[5], "빛의 속력은 매질마다 다르다")   # ㄱ
        self.assertEqual(out[7], "평면거울 상은 좌우가 바뀐다")   # ㄷ
        self.assertEqual(out[8], "ㄱ")                        # ①
        self.assertEqual(out[10], "ㄱ, ㄷ")                    # ③
        self.assertEqual(out[12], "ㄱ, ㄴ, ㄷ")                 # ⑤

    def test_passage_and_ask_do_not_repeat(self):
        """지문 칸·발문 칸이 따로 있으면 서로의 내용을 겹쳐 넣지 않는다."""
        out = ep.question_to_palette(hapdap(), combos()).split("\n")
        self.assertIn("빛의 굴절", out[2])
        self.assertNotIn("고른 것은?", out[2], "발문이 지문 칸에 또 들어가면 안 된다")
        self.assertIn("고른 것은?", out[4])
        self.assertNotIn("빛의 굴절", out[4], "지문이 발문 칸에 또 들어가면 안 된다")

    def test_ask_slot_used_when_no_passage(self):
        """지문이 없으면 지문 칸은 비우고(-) 발문은 발문 칸에 그대로 간다."""
        out = ep.question_to_palette(hapdap(passage=""), combos()).split("\n")
        self.assertEqual(out[2], "-")
        self.assertIn("고른 것은?", out[4])

    def test_two_photos_template(self):
        """학교합답2사진5선지 — 그림 2개 칸 + 발문·점수 칸이 따로 있다."""
        out = ep.question_to_palette(hapdap(material="그림가.png, 그림나.png"), combos()).split("\n")
        self.assertEqual(out[0], "\\학교합답2사진5선지\\")
        self.assertEqual(len(out) - 1, 14)
        self.assertEqual(out[3], "\\그림가\\")
        self.assertEqual(out[4], "\\그림나\\")
        self.assertIn("고른 것은?", out[5])                    # 발문
        self.assertEqual(out[6], "3")                         # 점수

    def test_no_photo_template(self):
        """사진 없는 합답형 — 실험 문구 없는 학교합답 템플릿으로 (2026-08-03 등록)."""
        out = ep.question_to_palette(hapdap(material=""), combos()).split("\n")
        self.assertEqual(out[0], "\\학교합답0사진5선지\\")
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
        self.assertEqual(ep.pick_template(hapdap()), "학교합답1사진5선지")
        self.assertEqual(ep.pick_template(hapdap(material="")), "학교합답0사진5선지")
        self.assertEqual(ep.pick_template(hapdap(qtype="정답형", material="")), "학교정답0사진1선지")
        self.assertEqual(ep.pick_template(hapdap(qtype="정답형")), "정답형1사진")
        self.assertEqual(ep.pick_template(hapdap(qtype="서술형", material="")), "서술형")


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

    def test_one_photo_uses_photo_template(self):
        """사진 1장 정답형은 사진 칸이 있는 템플릿으로 (2026-08-03 등록)."""
        out = ep.question_to_palette(
            jungdap(material="검전기01"), five("가", "나", "다", "라", "마")).split("\n")
        self.assertEqual(out[0], "\\정답형1사진\\")
        self.assertEqual(len(out) - 1, 9)
        self.assertEqual(out[3], "3")             # 점수
        self.assertEqual(out[4], "\\검전기01\\")    # 사진 칸 — \라벨\ 이라야 그림이 된다
        self.assertEqual(out[5], "가")             # ①

    def test_two_photos_go_into_block(self):
        """사진 2장 정답형 템플릿은 없다 — 발문 블록 안에 라벨로 넣는다."""
        out = ep.question_to_palette(
            jungdap(material="검전기01, 검전기02"), five("가", "나", "다", "라", "마"))
        self.assertIn("\\학교정답0사진1선지\\", out)
        self.assertIn("\\검전기01\\", out)
        self.assertIn("\\검전기02\\", out)

    def test_missing_choices_use_dash(self):
        out = ep.question_to_palette(jungdap(), five("가", "나")).split("\n")
        self.assertEqual(out[6], "-")
        self.assertEqual(out[8], "-")


# ===== 서술형 =====
class TestEssay(unittest.TestCase):
    """2026-08-03 '서술형' 템플릿 등록 — 번호·발문·점수 3칸 + 답란 상자는 조각에 내장."""

    def test_template_slots(self):
        q = jungdap(qtype="서술형", ask="까닭을 서술하시오.", default_points=5)
        out = ep.question_to_palette(q, [], num=12).split("\n")
        self.assertEqual(out[0], "\\서술형\\")
        self.assertEqual(len(out) - 1, 3)
        self.assertEqual(out[1], "12")                        # 번호
        self.assertEqual(out[2], "까닭을 서술하시오.")           # 발문
        self.assertEqual(out[3], "5")                         # 점수

    def test_passage_kept(self):
        q = jungdap(qtype="서술형", passage="그림은 전동기이다.", ask="원리를 쓰시오.")
        out = ep.question_to_palette(q, [])
        self.assertIn("그림은 전동기이다.", out)

    def test_photo_goes_into_ask_block(self):
        """서술형 템플릿엔 사진 칸이 없다 — 발문 블록 안에 라벨로 넣는다."""
        q = jungdap(qtype="서술형", material="전동기01", ask="원리를 쓰시오.")
        out = ep.question_to_palette(q, [])
        self.assertIn("\\전동기01\\", out)


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


class TestSuneungPalette(unittest.TestCase):
    def test_direct_question_uses_csat_pack(self):
        q = jungdap(material="그래프.png", default_points=3)
        out = ep.question_to_palette(
            q, five("가", "나", "다", "라", "마"), layout_style="suneung"
        ).split("\n")
        self.assertEqual(out[0], "\\수능AI실제직접형\\")
        self.assertEqual(len(out) - 1, 9)
        self.assertIn("\\그래프\\", out[2])
        self.assertEqual(out[4], "3")

    def test_hapdap_question_puts_score_in_question(self):
        q = hapdap(material="", default_points=3)
        out = ep.question_to_palette(q, combos(), layout_style="suneung").split("\n")
        self.assertEqual(out[0], "\\수능AI실제합답형\\")
        self.assertEqual(len(out) - 1, 11)
        self.assertTrue(out[3].endswith("[3점]"), out[3])

    def test_set_style_is_forwarded_to_each_question(self):
        md = ep.set_to_markdown([(jungdap(), five("가", "나", "다", "라", "마"))],
                                layout_style="suneung")
        self.assertTrue(md.startswith("\\수능AI실제직접형\\"))


if __name__ == "__main__":
    unittest.main()
