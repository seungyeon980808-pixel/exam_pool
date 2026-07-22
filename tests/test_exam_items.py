# -*- coding: utf-8 -*-
"""기출 문항 분할 테스트 — 2단 편집에서 문항 하나씩 정확히 잘리는가."""
import os
import tempfile
import unittest

import fitz

from app import exam_items as ei

KFONT = None
for _c in (r"C:\Windows\Fonts\malgun.ttf", "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"):
    if os.path.exists(_c):
        KFONT = _c
        break


def make_exam_pdf(path, cols=((88, [(160, 1), (500, 2)]), (437, [(160, 3), (600, 4)])),
                  width=842, height=1191):
    """2단 시험지 흉내: (단 x, [(y, 문항번호), ...])"""
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    kw = {"fontname": "kf", "fontfile": KFONT} if KFONT else {}
    for x, items in cols:
        for y, num in items:
            page.insert_text((x, y), f"{num}.", fontsize=12, **kw)
            page.insert_text((x + 22, y), "문항 본문입니다", fontsize=11, **kw)
    doc.save(path)
    doc.close()


class TestDetectItems(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not KFONT:
            raise unittest.SkipTest("한글 폰트 없음")
        cls.tmp = tempfile.mkdtemp()
        cls.pdf = os.path.join(cls.tmp, "exam.pdf")
        make_exam_pdf(cls.pdf)

    def items(self):
        d = fitz.open(self.pdf)
        try:
            return ei.detect_items(d[0]), d[0].rect.width
        finally:
            d.close()

    def test_finds_all_items(self):
        items, _ = self.items()
        self.assertEqual(sorted(i["num"] for i in items), [1, 2, 3, 4])

    def test_columns_do_not_overlap(self):
        """좌측 문항이 우측 단을 침범하면 안 된다 (2010년 시험지 버그)."""
        items, W = self.items()
        left = [i for i in items if i["col"] == 0]
        right = [i for i in items if i["col"] == 1]
        self.assertTrue(left and right)
        self.assertLessEqual(max(i["x1"] for i in left), min(i["x0"] for i in right) + 1)
        for i in items:
            self.assertLess(i["x1"] - i["x0"], W * 0.62, "문항 폭이 페이지 절반을 크게 넘음")

    def test_vertical_split(self):
        """같은 단에서 위 문항은 아래 문항 시작 전에서 끝난다."""
        items, _ = self.items()
        for c in (0, 1):
            col = sorted([i for i in items if i["col"] == c], key=lambda x: x["y0"])
            for a, b in zip(col, col[1:]):
                self.assertLessEqual(a["y1"], b["y0"] + 1)

    def test_item_at(self):
        items, _ = self.items()
        one = [i for i in items if i["num"] == 1][0]
        hit = ei.item_at(items, one["x0"] + 5, one["y0"] + 5)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["num"], 1)

    def test_summarize(self):
        items, _ = self.items()
        self.assertEqual(ei.summarize(items), "1~4번")
        self.assertEqual(ei.summarize([]), "")

    def test_empty_page(self):
        d = fitz.open()
        d.new_page()
        try:
            self.assertEqual(ei.detect_items(d[0]), [])
        finally:
            d.close()


if __name__ == "__main__":
    unittest.main()
