# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import db
from app.integrations.hwppalette import HwpPaletteProvider
from app.routes_integrations import (_evidence_summary, integration_status,
                                     preview_question, review_report, typeset_set)
from app.routes_question import ChoiceIn, QuestionIn


class TestHwpPaletteContract(unittest.TestCase):
    def test_launcher_forces_utf8_output_for_detached_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hwp_palette").mkdir()
            (root / "hwp_palette" / "cli.py").write_text("", encoding="utf-8")
            markdown = root / "question.md"
            markdown.write_text("test", encoding="utf-8")
            provider = HwpPaletteProvider(root)
            with patch("app.integrations.hwppalette.subprocess.Popen") as popen:
                popen.return_value.pid = 12345
                provider.launch(markdown)
        child_env = popen.call_args.kwargs["env"]
        self.assertEqual(child_env["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(child_env["PYTHONUTF8"], "1")

    def test_launcher_reports_process_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hwp_palette").mkdir()
            (root / "hwp_palette" / "cli.py").write_text("", encoding="utf-8")
            markdown = root / "question.md"
            markdown.write_text("test", encoding="utf-8")
            provider = HwpPaletteProvider(root)
            with patch("app.integrations.hwppalette.subprocess.Popen") as popen:
                popen.return_value.pid = 23456
                popen.return_value.poll.return_value = 0
                provider.launch(markdown)
                status = provider.process_status(23456)
        self.assertTrue(status["ok"])
        self.assertFalse(status["running"])

    def test_slot_contract_detects_match_and_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "data" / "library.json").write_text(json.dumps({
                "템플릿": [
                    {"name": "정답형", "label": "정답형", "slot_count": 8},
                    {"name": "합답형", "label": "합답형", "slot_count": 11},
                ]
            }, ensure_ascii=False), encoding="utf-8")
            provider = HwpPaletteProvider(root)
            result = provider.validate_slot_contract({"정답형": 8, "합답형": 12})
        self.assertFalse(result["ok"])
        self.assertTrue(result["templates"][1]["registered"])
        self.assertEqual(result["templates"][1]["actual"], 11)

    def test_current_active_contract_is_registered(self):
        status = integration_status()["hwppalette"]
        if not status["available"]:
            self.skipTest("hwpPalette sibling repository is not available")
        self.assertTrue(status["slot_contract"]["ok"], status["slot_contract"])

    def test_preview_is_hidden_isolated_and_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "palette"
            cache = Path(tmp) / "cache"
            (root / "hwp_palette").mkdir(parents=True)
            (root / "hwp_palette" / "cli.py").write_text("", encoding="utf-8")
            provider = HwpPaletteProvider(root)

            def fake_run(args, **kwargs):
                Path(args[args.index("--output-hwp") + 1]).write_bytes(b"hwp")
                Path(args[args.index("--output-pdf") + 1]).write_bytes(b"pdf")
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            def fake_render(_pdf, folder):
                (folder / "page-1.png").write_bytes(b"png")
                return [{"page_no": 1, "filename": "page-1.png", "width": 100, "height": 140}]

            with patch("app.integrations.hwppalette.data_dir", return_value=cache), \
                 patch("app.integrations.hwppalette.subprocess.run", side_effect=fake_run) as run, \
                 patch.object(provider, "_render_pdf", side_effect=fake_render):
                first = provider.render_preview("\\template\\\n1", scope="question")
                second = provider.render_preview("\\template\\\n1", scope="question")

        args = run.call_args.args[0]
        self.assertIn("--hidden", args)
        self.assertIn("--output-hwp", args)
        self.assertIn("--output-pdf", args)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(first["pages"][0]["image_url"],
                         f"/api/previews/{first['token']}/pages/1")

    def test_question_preview_accepts_unsaved_editor_payload(self):
        payload = QuestionIn(
            ask="빛의 반사에 대한 설명으로 옳은 것은?",
            choices=[
                ChoiceIn(ord=1, text="입사각과 반사각은 같다.", is_answer=True),
                ChoiceIn(ord=2, text="반사각은 항상 0도이다."),
            ],
        )
        with patch("app.routes_integrations.hwppalette_provider.render_preview") as render:
            render.return_value = {"ok": True, "pages": []}
            result = preview_question(payload)
        self.assertTrue(result["ok"])
        markdown = render.call_args.args[0]
        self.assertIn("빛의 반사", markdown)
        self.assertEqual(render.call_args.kwargs["scope"], "question")


class TestIntegrationRoutes(unittest.TestCase):
    MARK = "integration-route-test"

    @classmethod
    def setUpClass(cls):
        db.init_db()

    def setUp(self):
        self._cleanup()
        conn = db.connect()
        try:
            self.sid = conn.execute(
                "INSERT INTO exam_set (name, short_code) VALUES (?, ?)",
                (self.MARK, "IRT"),
            ).lastrowid
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        conn = db.connect()
        try:
            conn.execute(
                "DELETE FROM set_item WHERE set_id IN "
                "(SELECT id FROM exam_set WHERE name=?)", (self.MARK,)
            )
            conn.execute("DELETE FROM exam_set WHERE name=?", (self.MARK,))
            conn.commit()
        finally:
            conn.close()

    def test_empty_set_is_not_launched(self):
        with self.assertRaises(HTTPException) as caught:
            typeset_set(self.sid)
        self.assertEqual(caught.exception.status_code, 409)

    def test_review_report_is_available_before_items_exist(self):
        report = review_report(self.sid)
        self.assertEqual(report["items"], [])
        self.assertEqual(report["issues"][0]["code"], "empty_set")

    def test_combo_question_counts_bogi_evidence(self):
        question = {
            "qtype": "합답형",
            "bogi_items": json.dumps([
                {"label": "ㄱ", "proposition_id": 1},
                {"label": "ㄴ", "custom_evidence": "직접 근거"},
                {"label": "ㄷ"},
            ], ensure_ascii=False),
        }
        linked, total = _evidence_summary(question, [
            {"combo": '["ㄱ"]'}, {"combo": '["ㄱ", "ㄴ"]'},
        ])
        self.assertEqual((linked, total), (2, 3))


if __name__ == "__main__":
    unittest.main()
