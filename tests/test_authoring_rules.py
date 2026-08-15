import unittest

from app.authoring.item_rules import enrich_style_metadata, validate_draft, validate_evidence_links
from app.formula_markup import (
    formula_to_plain, normalize_reconstruction_draft_formulas,
    to_hwppalette_markup, validate_formula_markup,
)


class FormulaMarkupTest(unittest.TestCase):
    def test_canonical_formula_is_valid_and_preserved_for_hwppalette(self):
        text = "변화량은 [[formula:a = \\frac{\\Delta v}{\\Delta t}]]이다."
        self.assertEqual(validate_formula_markup(text), [])
        self.assertIn(r"\수식{a = \frac{\Delta v}{\Delta t}}", to_hwppalette_markup(text))

    def test_unicode_subscript_is_rejected(self):
        errors = validate_formula_markup("[[formula:v₀ = 2]]")
        self.assertTrue(any("유니코드 첨자" in message for message in errors))

    def test_raw_latex_outside_formula_marker_is_rejected(self):
        errors = validate_formula_markup(r"속력은 \frac{s}{t}이다.")
        self.assertTrue(any("수식 명령" in message for message in errors))

    def test_plain_fallback_is_readable(self):
        self.assertEqual(formula_to_plain(r"a = \frac{\Delta v}{\Delta t}"), "a = (Δv)/(Δt)")

    def test_reconstruction_promotes_physical_quantities_and_stacked_fractions(self):
        draft = {
            "passage": (
                "질량이 m인 물체 A를 높이 9h인 지점에 놓는다. "
                "질량이 2m인 물체 B는 높이 7/2h인 지점에서 정지한다."
            ),
            "ask": "H는?",
            "choices": [{"ord": 1, "text": "5/17h"}],
        }

        normalized = normalize_reconstruction_draft_formulas(draft)

        self.assertEqual(
            normalized["passage"],
            "질량이 [[formula:m]]인 물체 A를 높이 [[formula:9h]]인 지점에 놓는다. "
            "질량이 [[formula:2m]]인 물체 B는 높이 [[formula:\\frac{7}{2}h]]인 지점에서 정지한다.",
        )
        self.assertEqual(normalized["ask"], "[[formula:H]]는?")
        self.assertEqual(normalized["choices"][0]["text"], "[[formula:\\frac{5}{17}h]]")

    def test_reconstruction_formula_normalization_is_idempotent(self):
        draft = {"passage": "높이 [[formula:\\frac{7}{2}h]]인 지점"}

        once = normalize_reconstruction_draft_formulas(draft)
        twice = normalize_reconstruction_draft_formulas(once)

        self.assertEqual(twice, once)


class ItemStyleRuleTest(unittest.TestCase):
    def _draft(self):
        return {
            "qtype": "정답형",
            "passage": "다음은 물체 A의 운동에 대한 설명이다.\nA는 2초 동안 10 m 이동하였다.",
            "ask": "A의 속력은?",
            "choices": [
                {"ord": i + 1, "text": f"{i + 1} m/s", "custom_evidence": "reference_id=1 계산"}
                for i in range(5)
            ],
        }

    def test_attested_frames_pass(self):
        draft = enrich_style_metadata(self._draft())
        self.assertEqual(validate_draft(draft), [])
        self.assertEqual(draft["style_meta"]["ask"]["frame_id"], "ASK_VALUE")

    def test_pair_figure_frame_accepts_object_particle_variation(self):
        draft = self._draft()
        draft["passage"] = (
            "그림 (가)와 (나)는 각각 대전되지 않은 검전기와 대전체를 가까이 가져간 검전기를 나타낸 것이다."
        )
        draft = enrich_style_metadata(draft)
        self.assertEqual(draft["style_meta"]["passage"]["frame_id"], "INTRO_FIGURE_PAIR")
        self.assertFalse(any(item["field"] == "passage" for item in validate_draft(draft)))

    def test_forbidden_workbook_phrase_is_blocked(self):
        draft = self._draft()
        draft["ask"] = "알맞은 답을 고르시오."
        codes = {item["code"] for item in validate_draft(draft)}
        self.assertIn("style_forbidden_phrase", codes)
        self.assertIn("style_frame_unattested", codes)

    def test_registered_source_reconstruction_keeps_original_wording(self):
        draft = self._draft()
        draft["passage"] = "기출 원문의 고유한 도입 문장이다."
        draft["ask"] = "기출 원문에 적힌 물음은 무엇인가?"
        draft["choices"] = [{"ord": 1, "text": "원문 선지", "custom_evidence": ""}]
        draft["style_meta"] = {
            "reconstruction": {"enabled": True, "reference_id": 7, "source_label": "기출 19번"}
        }
        codes = {item["code"] for item in validate_draft(draft)}
        self.assertNotIn("style_frame_unattested", codes)
        self.assertNotIn("evidence_missing", codes)

    def test_evidence_must_name_an_attached_reference(self):
        draft = self._draft()
        draft["choices"][0]["custom_evidence"] = "교과서 내용"
        draft["choices"][1]["custom_evidence"] = "reference_id=99: 다른 자료"
        for row in draft["choices"][2:]:
            row["custom_evidence"] = "reference_id=7: 선택 자료"
        codes = [item["code"] for item in validate_evidence_links(draft, {7})]
        self.assertIn("evidence_reference_id_missing", codes)
        self.assertIn("evidence_reference_unknown", codes)


if __name__ == "__main__":
    unittest.main()
