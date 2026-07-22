# -*- coding: utf-8 -*-
"""하이라이트 매핑·렌더링 테스트.

06 보고서 2-2절이 지적한 '색인은 맞는데 형광펜이 안 찍히는' 위험을 직접 검증한다.
실제 PDF 파일 없이, PyMuPDF 로 메모리에서 PDF 를 만들어 픽스처로 쓴다.
"""
import os
import tempfile
import unittest

import fitz

from app import db, pdf_indexer as pi


# 한글 글자가 실제로 추출되는 PDF 를 만들려면 한글 폰트 파일이 필요하다
# (내장 'china-s' 로 넣으면 텍스트 레이어가 비어 나온다).
KFONT = None
for _cand in (r"C:\Windows\Fonts\malgun.ttf", r"C:\Windows\Fonts\gulim.ttc",
              "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"):
    if os.path.exists(_cand):
        KFONT = _cand
        break


def make_pdf(lines: list[str], path: str, split_word: bool = False) -> None:
    """텍스트 PDF 생성. split_word=True 면 한 단어를 조각내 배치한다(한글 PDF 재현)."""
    doc = fitz.open()
    page = doc.new_page()
    kw = {"fontname": "kfont", "fontfile": KFONT} if KFONT else {}
    y = 80
    for line in lines:
        if split_word:
            x = 60
            for ch in line:                      # 글자마다 따로 배치 → 조각난 단어
                page.insert_text((x, y), ch, fontsize=13, **kw)
                x += 16
        else:
            page.insert_text((60, y), line, fontsize=13, **kw)
        y += 30
    doc.save(path)
    doc.close()


class TestHighlightMapping(unittest.TestCase):
    """검색어가 실제 글자 좌표에 매핑되는가."""

    @classmethod
    def setUpClass(cls):
        if not KFONT:
            raise unittest.SkipTest("한글 폰트가 없어 픽스처를 만들 수 없습니다")
        cls.tmp = tempfile.mkdtemp()
        cls.normal = os.path.join(cls.tmp, "normal.pdf")
        cls.split = os.path.join(cls.tmp, "split.pdf")
        make_pdf(["빛은 매질에 따라 굴절한다", "평면거울의 상은 좌우가 바뀐다"], cls.normal)
        make_pdf(["징계기준을 확인한다"], cls.split, split_word=True)

    def _rects(self, path, terms):
        doc = fitz.open(path)
        try:
            return pi.highlight_rects(doc[0], terms)
        finally:
            doc.close()

    def test_prefix_match_like_fts(self):
        """FTS 가 prefix 로 맞히는 '빛'을 하이라이트도 '빛은'에서 찾아야 한다."""
        hits, misses = self._rects(self.normal, ["빛"])
        self.assertEqual(misses, [], "‘빛’을 못 찾음 — 색인과 하이라이트 경로가 어긋남")
        self.assertTrue(len(hits["빛"]) >= 1)

    def test_multi_terms(self):
        hits, misses = self._rects(self.normal, ["빛", "굴절"])
        self.assertEqual(misses, [])
        self.assertTrue(hits["빛"] and hits["굴절"])

    def test_split_word_fallback(self):
        """'징 계 기 준' 처럼 조각난 단어도 줄 폴백으로 찾아야 한다."""
        hits, misses = self._rects(self.split, ["징계기준"])
        self.assertEqual(misses, [], "조각난 단어를 못 찾음 — 줄 폴백 실패")
        self.assertTrue(hits["징계기준"])

    def test_miss_is_reported(self):
        """없는 단어는 조용히 넘어가지 않고 misses 로 보고돼야 한다."""
        hits, misses = self._rects(self.normal, ["존재하지않는단어"])
        self.assertIn("존재하지않는단어", misses)

    def test_rect_is_on_the_text(self):
        """좌표가 실제 글자 위에 있는지 — 페이지 안이고 넓이가 있어야 한다."""
        doc = fitz.open(self.normal)
        try:
            page = doc[0]
            hits, _ = pi.highlight_rects(page, ["굴절"])
            r = hits["굴절"][0]
            self.assertTrue(r.width > 0 and r.height > 0)
            self.assertTrue(page.rect.contains(r))
        finally:
            doc.close()


class TestRenderPng(unittest.TestCase):
    """render_page_png 통합 — DB 에 문서를 등록해 실제 경로로 돈다."""

    @classmethod
    def setUpClass(cls):
        if not KFONT:
            raise unittest.SkipTest("한글 폰트가 없어 픽스처를 만들 수 없습니다")
        cls.tmp = tempfile.mkdtemp()
        cls.pdf = os.path.join(cls.tmp, "render.pdf")
        make_pdf(["빛은 매질에 따라 굴절한다"], cls.pdf)
        db.init_db()
        conn = db.connect()
        cur = conn.execute(
            "INSERT INTO document (title, doc_type, file_path) VALUES (?,?,?)",
            ("렌더테스트", "교과서", cls.pdf))
        cls.doc_id = cur.lastrowid
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        pi.close_all_docs()
        conn = db.connect()
        conn.execute("DELETE FROM document WHERE id = ?", (cls.doc_id,))
        conn.commit()
        conn.close()

    def test_returns_pure_png(self):
        png = pi.render_page_png(self.doc_id, 1)
        self.assertTrue(png.startswith(bytes([0x89])+b"PNG"))

    def test_highlights_found(self):
        r = pi.page_highlights(self.doc_id, 1, ["빛", "굴절"])
        self.assertEqual(r["misses"], [])
        self.assertTrue(r["hits"]["빛"] >= 1)
        self.assertTrue(r["boxes"])
        b = r["boxes"][0]
        self.assertTrue(b["w"] > 0 and b["h"] > 0)
        self.assertTrue(0 <= b["x"] <= r["page_w"])

    def test_miss_reported(self):
        r = pi.page_highlights(self.doc_id, 1, ["없는단어xyz"])
        self.assertIn("없는단어xyz", r["misses"])

    def test_color_index_differs_per_term(self):
        r = pi.page_highlights(self.doc_id, 1, ["빛", "굴절"])
        idx = {b["term"]: b["color_idx"] for b in r["boxes"]}
        self.assertNotEqual(idx["빛"], idx["굴절"])

    def test_image_is_same_regardless_of_search(self):
        """이미지는 검색어와 무관 — 캐시가 재사용되고 원본이 훼손되지 않는다."""
        a = pi.render_page_png(self.doc_id, 1)
        pi.page_highlights(self.doc_id, 1, ["빛"])
        b = pi.render_page_png(self.doc_id, 1)
        self.assertEqual(a, b, "하이라이트가 원본 페이지에 눌어붙었다")

    def test_cache_returns_same_bytes(self):
        self.assertEqual(pi.render_page_png(self.doc_id, 1), pi.render_page_png(self.doc_id, 1))

    def test_out_of_range_page(self):
        with self.assertRaises(FileNotFoundError):
            pi.render_page_png(self.doc_id, 999)


if __name__ == "__main__":
    unittest.main()
