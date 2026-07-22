import unittest

from app import db
from app import pdf_indexer as pi


class TestFtsQuery(unittest.TestCase):
    def test_and_join_with_prefix(self):
        # 한국어 조사 대응: 각 키워드에 prefix(*) 를 붙인다
        self.assertEqual(pi._fts_query("빛 굴절"), '"빛"* AND "굴절"*')

    def test_strips_special_chars(self):
        self.assertEqual(pi._fts_query("[9과10-01]"), '"9과10"* AND "01"*')

    def test_empty(self):
        self.assertEqual(pi._fts_query("   "), "")


class TestFtsSearch(unittest.TestCase):
    """실제 DB 에 임시 문서를 넣고 검색 → 검증 → 정리."""

    @classmethod
    def setUpClass(cls):
        db.init_db()
        conn = db.connect()
        pi.ensure_fts(conn)
        cur = conn.execute(
            "INSERT INTO document (title, doc_type, file_path) VALUES (?,?,?)",
            ("테스트교과서", "교과서", "__test__.pdf"),
        )
        cls.doc_id = cur.lastrowid
        pages = [
            (1, "빛은 매질에 따라 진행 속력이 달라지며 이때 굴절이 일어난다."),
            (2, "평면거울에서 상은 좌우가 바뀐 것처럼 보인다."),
        ]
        conn.executemany(
            "INSERT INTO document_page (document_id, page_no, text) VALUES (?,?,?)",
            [(cls.doc_id, p, t) for p, t in pages],
        )
        conn.executemany(
            "INSERT INTO page_fts (body, doc_title, page_no, document_id) VALUES (?,?,?,?)",
            [(t, "테스트교과서", p, cls.doc_id) for p, t in pages],
        )
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        conn = db.connect()
        conn.execute("DELETE FROM page_fts WHERE document_id = ?", (cls.doc_id,))
        conn.execute("DELETE FROM document WHERE id = ?", (cls.doc_id,))
        conn.commit()
        conn.close()

    def test_and_search_finds_page(self):
        res = pi.search("빛 굴절")
        hits = [r for r in res["items"] if r["document_id"] == self.doc_id]
        self.assertTrue(hits)
        self.assertEqual(hits[0]["page_no"], 1)
        self.assertIn("p.1", hits[0]["source_label"])

    def test_no_match(self):
        res = pi.search("존재하지않는단어xyz")
        self.assertEqual([r for r in res["items"] if r["document_id"] == self.doc_id], [])

    def test_hashtag_multi_search(self):
        """#해시태그로 여러 키워드를 넣어도 AND 검색이 된다."""
        res = pi.search("#빛 #굴절")
        self.assertEqual(res["terms"], ["빛", "굴절"])
        self.assertTrue([r for r in res["items"] if r["document_id"] == self.doc_id])

    def test_match_pct_present(self):
        res = pi.search("빛")
        hits = [r for r in res["items"] if r["document_id"] == self.doc_id]
        self.assertTrue(hits)
        self.assertTrue(1 <= hits[0]["match_pct"] <= 100)

    def test_total_count(self):
        res = pi.search("빛")
        self.assertGreaterEqual(res["total"], 1)

    def test_doc_filter(self):
        """특정 문서로 좁히기."""
        res = pi.search("빛", doc_id=self.doc_id)
        self.assertTrue(all(r["document_id"] == self.doc_id for r in res["items"]))


class TestTermsOf(unittest.TestCase):
    def test_hashtag_and_space(self):
        self.assertEqual(pi.terms_of("#빛 #굴절"), ["빛", "굴절"])
        self.assertEqual(pi.terms_of("빛 굴절"), ["빛", "굴절"])

    def test_empty(self):
        self.assertEqual(pi.terms_of("  #  "), [])


if __name__ == "__main__":
    unittest.main()
