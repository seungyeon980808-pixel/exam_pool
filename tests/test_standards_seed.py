# -*- coding: utf-8 -*-
"""교육과정 seed 와 추출기 파서 검사.

성취기준은 손으로 고칠 데이터가 아니라 PDF 에서 뽑아온 참고 자료다.
추출이 조용히 어긋나면(단어가 붙거나 머리말이 섞이거나) 출제 화면 전체가 오염되므로
seed 파일 자체를 검사한다.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from extract_standards import _explain_codes, is_noise, parse_standards  # noqa: E402

SEED = json.loads((ROOT / "app" / "seed" / "standards.json").read_text(encoding="utf-8"))


class SeedShape(unittest.TestCase):
    def test_subjects_present(self):
        names = [s["name"] for s in SEED["subjects"]]
        self.assertIn("과학", names)              # 중학교 공통
        self.assertIn("물리학", names)            # 고등 일반 선택
        self.assertIn("융합과학 탐구", names)     # 고등 융합 선택
        self.assertEqual(len(names), len(set(names)))

    def test_unit_no_is_globally_unique(self):
        nos = [u["unit_no"] for u in SEED["units"]]
        self.assertEqual(len(nos), len(set(nos)))

    def test_middle_school_units_keep_1_to_23(self):
        """기존 명제가 unit_no 로 단원을 가리키므로 중학교 번호는 바뀌면 안 된다."""
        mid = sorted(u["unit_no"] for u in SEED["units"] if u["subject"] == "과학")
        self.assertEqual(mid, list(range(1, 24)))

    def test_every_standard_has_a_unit(self):
        units = {u["unit_no"] for u in SEED["units"]}
        for s in SEED["standards"]:
            self.assertIn(s["unit_no"], units, s["code"])

    def test_standards_end_like_sentences(self):
        """머리말이 문장 끝에 섞여 들어오면 여기서 걸린다."""
        odd = [s["code"] for s in SEED["standards"] if not s["text"].endswith("다.")]
        self.assertEqual(odd, [])

    def test_notes_are_lists(self):
        for u in SEED["units"]:
            self.assertIsInstance(u["inquiry"], list)
            self.assertIsInstance(u["consider"], list)


class ParserBits(unittest.TestCase):
    def test_running_head_is_noise(self):
        for s in ["과학과 교육과정", "공통 교육과정", "선택 중심 교육과정 – 진로 선택 과목 -", "51"]:
            self.assertTrue(is_noise(s), s)
        self.assertFalse(is_noise("교육과정 설계의 개요"))
        self.assertFalse(is_noise("원소와 화합물의 정의를 알고"))

    def test_explain_code_range_expands(self):
        codes, rest = _explain_codes("[4과09-01∼02] 자기장의 개념은 도입하지 않는다.", "4과")
        self.assertEqual(codes, ["[4과09-01]", "[4과09-02]"])
        self.assertTrue(rest.startswith("자기장"))

    def test_line_broken_word_is_not_split(self):
        """PDF 는 단어 중간에서 끊기면 줄 끝에 공백을 남기지 않는다."""
        text = ("(2) 생물의 구성과 다양성\n"
                "[9과02-01] 세포의 구조와 기능의 관계를 추\n"
                "론할 수 있다.\n")
        _, stds = parse_standards(text, "9과")
        self.assertEqual(stds[0]["text"], "세포의 구조와 기능의 관계를 추론할 수 있다.")

    def test_notes_attach_to_unit_and_code(self):
        text = ("(11) 물질의 구성\n"
                "[9과11-01] 원소와 화합물을 화학식으로 표현할 수 있다.\n"
                "<탐구 활동>\n"
                "• 같은 족 원소들의 유사성 탐구하기\n"
                "(가) 성취기준 해설\n"
                "• [9과11-01] 질량수나 동위 원소는 다루지 않는다.\n"
                "(나) 성취기준 적용 시 고려 사항\n"
                "• 고등학교 ‘통합과학1’과 연계된다.\n")
        units, stds = parse_standards(text, "9과")
        self.assertEqual(units[11]["inquiry"], ["같은 족 원소들의 유사성 탐구하기"])
        self.assertEqual(units[11]["consider"], ["고등학교 ‘통합과학1’과 연계된다."])
        self.assertEqual(stds[0]["explain"], "질량수나 동위 원소는 다루지 않는다.")


if __name__ == "__main__":
    unittest.main()
