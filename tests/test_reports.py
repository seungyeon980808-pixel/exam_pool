"""정답표 · 이원목적분류표 — 한글·DB 없이 도는 순수 함수."""
import unittest

from app import reports


def item(ord_answer, points=None, **qkw):
    q = {"qtype": "정답형", "difficulty": "중", "default_points": 3.0,
         "standard_code": "[9과10-01]", "intent": "굴절 개념 확인", "answer": ""}
    q.update(qkw)
    chs = [{"ord": i, "text": f"선지{i}", "is_answer": i == ord_answer} for i in range(1, 6)]
    return {"question": q, "choices": chs, "points": points}


class TestAnswerKey(unittest.TestCase):
    def test_rows_and_numbering(self):
        t = reports.answer_key([item(3), item(1, points=4.0)])
        self.assertEqual(t["columns"][0], "문항")
        self.assertEqual(t["rows"][0][0], "1")
        self.assertEqual(t["rows"][0][1], "③")
        self.assertEqual(t["rows"][1][1], "①")

    def test_points_use_set_override(self):
        t = reports.answer_key([item(1, points=4.0)])
        self.assertEqual(t["rows"][0][2], "4")       # 기본 3점이 아니라 세트 배점 4점

    def test_saved_answer_string_wins(self):
        t = reports.answer_key([item(2, answer="②③")])
        self.assertEqual(t["rows"][0][1], "②③")

    def test_total_points(self):
        t = reports.answer_key([item(1, points=2.5), item(2, points=3.0)])
        self.assertEqual(t["total_points"], "5.5")


class TestBlueprint(unittest.TestCase):
    def test_unit_and_standard_text_fill_in(self):
        t = reports.blueprint(
            [item(1)],
            unit_of_code={"[9과10-01]": "빛과 파동"},
            standard_texts={"[9과10-01]": "빛의 굴절을 설명할 수 있다."})
        row = t["rows"][0]
        self.assertEqual(row[1], "빛과 파동")
        self.assertIn("빛의 굴절", row[2])
        self.assertEqual(row[3], "굴절 개념 확인")

    def test_missing_maps_do_not_break(self):
        t = reports.blueprint([item(1)])
        self.assertEqual(t["rows"][0][1], "")
        self.assertEqual(len(t["rows"][0]), len(reports.BLUEPRINT_COLS))

    def test_behavior_summary(self):
        t = reports.blueprint([item(1, behavior="지식·이해"), item(2)])
        self.assertEqual(t["summary"]["behavior"]["지식·이해"], 1)
        self.assertEqual(t["summary"]["behavior"]["미지정"], 1)


class TestTsv(unittest.TestCase):
    def test_header_and_row_count(self):
        t = reports.answer_key([item(1), item(2)])
        lines = reports.to_tsv(t).split("\n")
        self.assertEqual(len(lines), 3)                     # 머리글 + 2행
        self.assertEqual(lines[0].split("\t")[0], "문항")

    def test_tabs_and_newlines_in_cells_are_flattened(self):
        t = reports.blueprint([item(1, intent="줄1\n줄2\t탭")])
        line = reports.to_tsv(t).split("\n")[1]
        self.assertEqual(len(line.split("\t")), len(reports.BLUEPRINT_COLS))


if __name__ == "__main__":
    unittest.main()
