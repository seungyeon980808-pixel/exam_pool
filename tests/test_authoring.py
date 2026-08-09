# -*- coding: utf-8 -*-
"""대화형 문항 제작 세션의 저장·확정·선택 반영·복구 테스트."""
import json
import base64
import asyncio
import tempfile
import unittest
from pathlib import Path
from fastapi import HTTPException

from app import db, routes_authoring as ra
from unittest.mock import patch

from app.authoring.providers import (
    CodexLocalProvider, DEVELOPER_INSTRUCTIONS, MockProvider, PROPOSAL_MARKER,
    PROPOSAL_EVENT_MARKER,
)
from app.authoring.codex_app_server import CodexAppServerClient
from app.authoring.figures import (
    FiveELocalProvider, RasterImageProvider, StubFigureProvider, get_figure_provider,
)


class TestAuthoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def setUp(self):
        self.session_ids = []
        self.question_ids = []

    def tearDown(self):
        conn = db.connect()
        try:
            for sid in self.session_ids:
                conn.execute("DELETE FROM authoring_session WHERE id=?", (sid,))
            for qid in self.question_ids:
                conn.execute("DELETE FROM question WHERE id=?", (qid,))
            conn.commit()
        finally:
            conn.close()

    def _new(self):
        data = ra.create_session(ra.SessionIn(provider="mock"))
        sid = data["session"]["id"]
        self.session_ids.append(sid)
        return sid, data["session"]

    def test_new_session_is_separate_draft(self):
        sid, session = self._new()
        self.assertEqual(session["status"], "text_drafting")
        self.assertEqual(session["draft"]["ask"], "")
        self.assertEqual(session["figure"]["status"], "none")
        loaded = ra.get_session(sid)
        self.assertEqual(loaded["messages"], [])

    def test_discard_is_soft_and_source_question_is_preserved(self):
        conn = db.connect()
        try:
            qid = conn.execute(
                "INSERT INTO question(title,qtype,ask,status) VALUES(?,?,?,?)",
                ("원본", "정답형", "원본 발문", "검토중"),
            ).lastrowid
            conn.commit()
        finally:
            conn.close()
        self.question_ids.append(qid)
        first = ra.create_session(ra.SessionIn(question_id=qid, provider="mock"))
        sid = first["session"]["id"]
        self.session_ids.append(sid)
        result = ra.discard_session(sid)
        self.assertTrue(result["discarded"])
        self.assertTrue(result["source_question_preserved"])
        with self.assertRaises(HTTPException) as raised:
            ra.get_session(sid)
        self.assertEqual(raised.exception.status_code, 410)
        reopened = ra.create_session(ra.SessionIn(question_id=qid, provider="mock"))
        self.session_ids.append(reopened["session"]["id"])
        self.assertNotEqual(reopened["session"]["id"], sid)
        conn = db.connect()
        try:
            self.assertEqual(conn.execute(
                "SELECT ask FROM question WHERE id=?", (qid,)
            ).fetchone()["ask"], "원본 발문")
            self.assertEqual(conn.execute(
                "SELECT status FROM authoring_session WHERE id=?", (sid,)
            ).fetchone()["status"], "discarded")
        finally:
            conn.close()

    def test_confirm_then_edit_sets_review_warning(self):
        sid, session = self._new()
        draft = session["draft"]
        draft["ask"] = "옳은 것은?"
        ra.update_draft(sid, ra.DraftIn(draft=draft))
        confirmed = ra.confirm_text(sid)
        self.assertEqual(confirmed["status"], "text_confirmed")

        changed = dict(confirmed["draft"])
        changed["ask"] = "옳지 않은 것은?"
        result = ra.update_draft(sid, ra.DraftIn(draft=changed))
        self.assertEqual(result["status"], "text_drafting")
        self.assertEqual(result["review_flags"], ["answer", "explanation"])

    def test_apply_is_explicit_and_undo_restores(self):
        sid, session = self._new()
        proposal = {"id": "p1", "field": "ask", "label": "발문", "value": "제안 발문"}
        conn = db.connect()
        try:
            mid = conn.execute(
                "INSERT INTO authoring_message(session_id,role,content,proposals_json) VALUES(?,?,?,?)",
                (sid, "assistant", "제안합니다", json.dumps([proposal], ensure_ascii=False))).lastrowid
            conn.commit()
        finally:
            conn.close()

        # 메시지를 저장한 것만으로 초안은 변하지 않는다.
        self.assertEqual(ra.get_session(sid)["session"]["draft"]["ask"], "")
        applied = ra.apply_proposal(sid, ra.ApplyIn(message_id=mid, proposal_id="p1"))
        self.assertEqual(applied["draft"]["ask"], "제안 발문")
        undone = ra.undo_apply(sid)
        self.assertEqual(undone["draft"]["ask"], "")

    def test_figure_plan_is_explicit_and_undoable(self):
        sid, _ = self._new()
        plan = {"summary": "막대자석과 나침반", "artboard": {"w": 90, "h": 60},
                "objects": [{"type": "apparatus", "kind": "bar_magnet", "x": -20, "y": -4}]}
        proposal = {"id": "fig-1", "field": "figure_plan", "label": "그림 설계안", "value": plan}
        conn = db.connect()
        try:
            mid = conn.execute(
                "INSERT INTO authoring_message(session_id,role,content,proposals_json) VALUES(?,?,?,?)",
                (sid, "assistant", "그림 설계안입니다", json.dumps([proposal], ensure_ascii=False))).lastrowid
            conn.commit()
        finally:
            conn.close()
        self.assertIsNone(ra.get_session(sid)["session"]["draft"].get("figure_plan"))
        applied = ra.apply_proposal(sid, ra.ApplyIn(message_id=mid, proposal_id="fig-1"))
        self.assertEqual(applied["draft"]["figure_plan"], plan)
        self.assertIsNone(ra.undo_apply(sid)["draft"].get("figure_plan"))

    def test_mock_provider_returns_proposals(self):
        reply = MockProvider().generate("선지를 제안해줘", {})
        self.assertTrue(reply.content)
        self.assertTrue(any(p["field"] == "choices" for p in reply.proposals))
        self.assertTrue(MockProvider().connection_state()["connected"])

    def test_mock_provider_returns_figure_plan(self):
        reply = MockProvider().generate("그림 설계안을 만들어줘", {})
        plan = next(p for p in reply.proposals if p["field"] == "figure_plan")
        self.assertTrue(plan["value"]["objects"])

    def test_figure_options_are_persisted_per_session(self):
        sid, _ = self._new()
        session = ra.update_figure_options(sid, ra.FigureOptionsIn(
            provider="raster_image", include_text=True, composition="separate",
        ))
        self.assertEqual(session["figure"]["options"], {
            "provider": "raster_image", "include_text": True, "composition": "separate",
        })

    def test_figure_plan_removes_text_when_option_is_off(self):
        proposals = CodexLocalProvider._validated_proposals([{
            "field": "figure_plan", "value": {
                "summary": "검전기", "artboard": {"w": 90, "h": 60},
                "objects": [
                    {"type": "text", "x": 0, "y": 0, "text": "(가)"},
                    {"type": "apparatus", "kind": "electroscope", "x": 0, "y": 0},
                ],
            },
        }], {"provider": "fivee_assets", "include_text": False, "composition": "combined"})
        plan = proposals[0]["value"]
        self.assertEqual([o["type"] for o in plan["objects"]], ["apparatus"])
        self.assertFalse(plan["options"]["include_text"])

    def test_raster_provider_generates_panels_with_managed_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = RasterImageProvider()
            source = Path(tmp) / "source.png"
            import fitz
            pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 2, 2), False)
            pixmap.clear_with(255)
            pixmap.save(str(source))
            output = Path(tmp) / "photos"
            output.mkdir()
            plan = {"version": 2, "summary": "달의 위상", "panels": [
                {"id": "a", "image_prompt": "글자 없는 달의 위상 도판"},
                {"id": "b", "image_prompt": "글자 없는 지구와 달 도판"},
            ]}
            with (
                patch.object(provider, "_folder", return_value=Path(tmp)),
                patch("app.authoring.figures.codex_app_server.generate_image",
                      return_value={"savedPath": str(source)}),
                patch("app.authoring.figures.hwppalette_provider.photo_dir", return_value=output),
                patch("app.authoring.figures.hwppalette_provider.register_photo_dir"),
            ):
                result = provider.create(99, {"figure_plan": plan}, {
                    "options": {"include_text": False}, "figure_name": "draft_99",
                })
            self.assertEqual(result["status"], "draft")
            self.assertEqual(len(result["assets"]), 2)
            self.assertTrue(Path(result["scene_spec_path"]).is_file())
            self.assertTrue(Path(result["assets"][0]["rendered_image_path"]).is_file())
            self.assertTrue(Path(result["fivee_project_path"]).is_file())
            project = json.loads(Path(result["fivee_project_path"]).read_text(encoding="utf-8"))
            self.assertEqual([page["name"] for page in project["pages"]], [
                "draft_99_01", "draft_99_02",
            ])
            self.assertIn("plus/minus signs", result["assets"][0]["image_prompt"])

    def test_explanation_column_is_additive(self):
        conn = db.connect()
        try:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(question)")}
            self.assertIn("explanation", cols)
        finally:
            conn.close()

    def test_codex_stream_hides_protocol_marker_and_builds_proposal(self):
        raw = (
            "발문을 다음처럼 다듬어 보았습니다."
            + PROPOSAL_MARKER
            + '[{"field":"ask","label":"발문","value":"옳은 것은?"}]'
        )
        with patch(
            "app.authoring.codex_app_server.codex_app_server.stream_turn",
            return_value=("thread-1", iter([raw[:12], raw[12:]])),
        ) as stream_turn:
            thread_id, events = CodexLocalProvider().stream(
                "수정해줘", {}, model="gpt-5.6-luna", reasoning_effort="medium")
            events = list(events)
        self.assertEqual(thread_id, "thread-1")
        visible = "".join(value for kind, value in events if kind == "delta")
        reply = next(value for kind, value in events if kind == "done")
        self.assertNotIn("EXAMPOOL_PROPOSALS", visible)
        self.assertEqual(reply.content, "발문을 다음처럼 다듬어 보았습니다.")
        self.assertEqual(reply.proposals[0]["field"], "ask")
        self.assertTrue(reply.proposals[0]["id"])
        self.assertEqual(stream_turn.call_args.kwargs["model"], "gpt-5.6-luna")
        self.assertEqual(stream_turn.call_args.kwargs["reasoning_effort"], "medium")

    def test_codex_stream_emits_each_progressive_proposal_before_done(self):
        raw = (
            "두 항목을 차례로 제안합니다."
            + PROPOSAL_EVENT_MARKER
            + '{"field":"ask","label":"발문","value":"옳은 것은?"}'
            + PROPOSAL_EVENT_MARKER
            + '{"field":"answer","label":"정답","value":"2"}'
        )
        chunks = [raw[:32], raw[32:70], raw[70:105], raw[105:]]
        with patch(
            "app.authoring.codex_app_server.codex_app_server.stream_turn",
            return_value=("thread-2", iter(chunks)),
        ):
            _, events = CodexLocalProvider().stream("제안해줘", {})
            events = list(events)
        kinds = [kind for kind, _ in events]
        self.assertEqual(kinds.count("proposal"), 2)
        self.assertLess(kinds.index("proposal"), kinds.index("done"))
        reply = next(value for kind, value in events if kind == "done")
        self.assertEqual([p["field"] for p in reply.proposals], ["ask", "answer"])
        self.assertEqual(reply.proposals[1]["value"], "②")

    def test_codex_stream_parses_all_proposals_from_one_final_chunk(self):
        raw = (
            "문항 전체를 제안합니다."
            + PROPOSAL_EVENT_MARKER
            + '{"field":"passage","label":"제시문","value":"제시문"}'
            + PROPOSAL_EVENT_MARKER
            + '{"field":"ask","label":"발문","value":"옳은 것은?"}'
            + PROPOSAL_EVENT_MARKER
            + '{"field":"choices","label":"선지","value":['
              '{"ord":1,"text":"가","is_answer":true},'
              '{"ord":2,"text":"나","is_answer":false},'
              '{"ord":3,"text":"다","is_answer":false},'
              '{"ord":4,"text":"라","is_answer":false},'
              '{"ord":5,"text":"마","is_answer":false}]}'
            + PROPOSAL_EVENT_MARKER
            + '{"field":"answer","label":"정답","value":"1"}'
            + PROPOSAL_EVENT_MARKER
            + '{"field":"explanation","label":"해설","value":"해설"}'
        )
        with patch(
            "app.authoring.codex_app_server.codex_app_server.stream_turn",
            return_value=("thread-final", iter([raw])),
        ):
            _, events = CodexLocalProvider().stream("문항 전체를 만들어줘", {})
            events = list(events)
        reply = next(value for kind, value in events if kind == "done")
        self.assertEqual(
            [proposal["field"] for proposal in reply.proposals],
            ["passage", "ask", "choices", "answer", "explanation"],
        )

    def test_codex_stream_parses_jsonl_after_one_marker(self):
        raw = (
            "문항 전체를 제안합니다."
            + PROPOSAL_EVENT_MARKER
            + '{"field":"passage","label":"제시문","value":"제시문"}\n'
            + '{"field":"ask","label":"발문","value":"옳은 것은?"}\n'
            + '{"field":"answer","label":"정답","value":"1"}\n'
            + '{"field":"explanation","label":"해설","value":"해설"}'
        )
        with patch(
            "app.authoring.codex_app_server.codex_app_server.stream_turn",
            return_value=("thread-jsonl", iter([raw])),
        ):
            _, events = CodexLocalProvider().stream("문항 전체를 만들어줘", {})
            events = list(events)
        reply = next(value for kind, value in events if kind == "done")
        self.assertEqual(
            [proposal["field"] for proposal in reply.proposals],
            ["passage", "ask", "answer", "explanation"],
        )

    def test_reference_image_is_stored_separately_from_generated_assets(self):
        sid, _ = self._new()
        data_url = "data:image/png;base64," + base64.b64encode(b"reference-png").decode()
        with tempfile.TemporaryDirectory() as tmp, patch.object(ra, "data_dir", return_value=Path(tmp)):
            session = ra.add_figure_reference(
                sid, ra.FigureReferenceIn(
                    filename="setup.png", data_url=data_url,
                    source_label="교과서 247쪽", source_text="검전기 정전기 유도",
                    usage="both", source_meta={"page_no": 247},
                ))
            reference = session["figure"]["references"][0]
            self.assertTrue(Path(reference["image_path"]).is_file())
            self.assertEqual(reference["source_label"], "교과서 247쪽")
            self.assertEqual(reference["source_text"], "검전기 정전기 유도")
            self.assertEqual(session["figure"]["assets"], [])
            updated = ra.update_figure_reference(
                sid, reference["id"], ra.FigureReferenceUsageIn(usage="content"))
            self.assertEqual(updated["figure"]["references"][0]["usage"], "content")
            ra.delete_figure_reference(sid, reference["id"])
            reopened = ra.get_session(sid)["session"]
            self.assertEqual(reopened["figure"]["references"], [])

    def test_codex_prompt_contains_selected_reference_material(self):
        prompt = CodexLocalProvider._prompt("문항 전체를 만들어줘", {
            "_references": [{
                "source_label": "교과서 247쪽",
                "source_text": "검전기에서 정전기 유도가 일어난다.",
                "usage": "content",
            }],
        })
        self.assertIn("교과서 247쪽", prompt)
        self.assertIn("정전기 유도", prompt)

    def test_codex_prompt_defaults_question_creation_to_complete_question(self):
        prompt = CodexLocalProvider._prompt("정전기 유도 문항을 만들어줘", {})
        self.assertIn("전체 문항 제작 요청", prompt)
        self.assertIn("선지 5개", prompt)

    def test_codex_prompt_respects_explicit_partial_request(self):
        prompt = CodexLocalProvider._prompt("선지만 다시 만들어줘", {})
        self.assertIn("명시적으로 지정된 항목만", prompt)
        self.assertNotIn("전체 문항 제작 요청이다", prompt)

    def test_authoring_model_settings_are_persisted_per_session(self):
        sid, session = self._new()
        self.assertEqual(session["effective_model"], "gpt-5.6-luna")
        updated = ra.update_settings(sid, ra.SettingsIn(authoring_mode="precise"))
        self.assertEqual(updated["model"], "gpt-5.6-terra")
        self.assertEqual(updated["reasoning_effort"], "medium")
        reopened = ra.get_session(sid)["session"]
        self.assertEqual(reopened["authoring_mode"], "precise")
        self.assertEqual(reopened["effective_model"], "gpt-5.6-terra")

    def test_manual_model_setting_overrides_mode_preset(self):
        sid, _ = self._new()
        updated = ra.update_settings(sid, ra.SettingsIn(
            model="gpt-5.6-sol", reasoning_effort="high"))
        self.assertEqual(updated["effective_model"], "gpt-5.6-sol")
        self.assertEqual(updated["effective_reasoning_effort"], "high")

    def test_app_server_model_catalog_is_reduced_to_safe_fields(self):
        client = CodexAppServerClient()
        with patch.object(client, "request", return_value={"data": [{
            "id": "gpt-test", "displayName": "GPT Test", "description": "test",
            "isDefault": True, "hidden": False, "defaultReasoningEffort": "low",
            "supportedReasoningEfforts": [
                {"reasoningEffort": "low", "description": "fast"},
                {"reasoningEffort": "high", "description": "careful"},
            ], "internalSecret": "not-for-ui",
        }]}):
            models = client.list_models()
        self.assertEqual(models[0]["id"], "gpt-test")
        self.assertEqual(models[0]["supported_reasoning_efforts"], ["low", "high"])
        self.assertNotIn("internalSecret", models[0])

    def test_codex_connection_exposes_safe_model_catalog(self):
        with patch(
            "app.authoring.codex_app_server.codex_app_server.account_state",
            return_value={
                "account": {"email": "teacher@example.com", "type": "chatgpt", "planType": "plus"},
                "model": "gpt-test", "models": [{"id": "gpt-test", "display_name": "GPT Test"}],
                "rate_limits": None, "usage": None,
            },
        ):
            state = CodexLocalProvider().connection_state()
        self.assertTrue(state["connected"])
        self.assertEqual(state["models"][0]["id"], "gpt-test")

    def test_connection_exposes_authoring_protocol_version(self):
        with patch.object(CodexLocalProvider, "connection_state", return_value={"connected": True}):
            state = ra.connection("codex_local")
        self.assertEqual(state["authoring_protocol"], CodexLocalProvider.protocol_version)

    def test_connection_refresh_restarts_codex_child(self):
        with patch.object(ra.codex_app_server, "restart") as restart, patch.object(
            CodexLocalProvider, "connection_state", return_value={"connected": True}
        ):
            state = ra.refresh_connection("codex_local")
        restart.assert_called_once_with()
        self.assertTrue(state["connected"])

    def test_login_reuses_existing_managed_codex_login(self):
        with patch.object(ra.codex_app_server, "restart") as restart, patch.object(
            ra.codex_app_server,
            "account_state",
            return_value={"signed_in": True, "account": {"type": "chatgpt"}},
        ), patch.object(ra.codex_app_server, "start_login") as start_login:
            result = ra.login()
        restart.assert_called_once_with()
        self.assertTrue(result["alreadySignedIn"])
        start_login.assert_not_called()

    def test_app_server_interrupts_the_active_turn(self):
        client = CodexAppServerClient()
        client._active_turns["thread-1"] = "turn-1"
        with patch.object(client, "request", return_value={}) as request:
            self.assertTrue(client.interrupt_turn("thread-1"))
        request.assert_called_once_with(
            "turn/interrupt", {"threadId": "thread-1", "turnId": "turn-1"}, timeout=15)
        self.assertFalse(client.interrupt_turn("missing"))

    def test_app_server_returns_managed_generated_image_path(self):
        client = CodexAppServerClient()
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "generated.png"
            image.write_bytes(b"png")

            def request(method, params=None, timeout=30):
                if method == "thread/start":
                    return {"thread": {"id": "image-thread"}}
                if method == "turn/start":
                    for target in list(client._subscribers):
                        target.put({"method": "item/completed", "params": {
                            "threadId": "image-thread", "turnId": "turn-1",
                            "item": {"type": "imageGeneration", "status": "completed",
                                     "savedPath": str(image)},
                        }})
                        target.put({"method": "turn/completed", "params": {
                            "threadId": "image-thread", "turnId": "turn-1",
                            "turn": {"status": "completed"},
                        }})
                    return {"turn": {"id": "turn-1"}}
                return {}

            with (
                patch.object(client, "account_state", return_value={
                    "account": {"type": "chatgpt"},
                }),
                patch.object(client, "capabilities", return_value={"imageGeneration": True}),
                patch.object(client, "request", side_effect=request),
            ):
                result = client.generate_image("검전기")
        self.assertEqual(result["savedPath"], str(image))

    def test_app_server_passes_reference_as_local_image_input(self):
        client = CodexAppServerClient()
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "generated.png"
            reference = Path(tmp) / "reference.png"
            image.write_bytes(b"png")
            reference.write_bytes(b"ref")
            captured = {}

            def request(method, params=None, timeout=30):
                if method == "thread/start":
                    return {"thread": {"id": "image-thread"}}
                if method == "turn/start":
                    captured["input"] = params["input"]
                    for target in list(client._subscribers):
                        target.put({"method": "item/completed", "params": {
                            "threadId": "image-thread", "turnId": "turn-1",
                            "item": {"type": "imageGeneration", "savedPath": str(image)},
                        }})
                        target.put({"method": "turn/completed", "params": {
                            "threadId": "image-thread", "turnId": "turn-1",
                            "turn": {"status": "completed"},
                        }})
                    return {"turn": {"id": "turn-1"}}
                return {}

            with patch.object(client, "account_state", return_value={"account": {"type": "chatgpt"}}), \
                 patch.object(client, "capabilities", return_value={"imageGeneration": True}), \
                 patch.object(client, "request", side_effect=request):
                client.generate_image("새 도판", reference_paths=[str(reference)])
        self.assertEqual(captured["input"][1]["type"], "localImage")
        self.assertEqual(captured["input"][1]["path"], str(reference.resolve()))

    def test_app_server_splits_distinct_figure_situations(self):
        client = CodexAppServerClient()
        payload = json.dumps([
            {"id": "before", "summary": "접촉 직후", "image_prompt": "접촉 직후 검전기"},
            {"id": "after", "summary": "막대를 가까이 함", "image_prompt": "유리 막대를 가까이 한 검전기"},
        ], ensure_ascii=False)
        with patch.object(client, "stream_turn", return_value=("thread", iter([payload]))):
            panels = client.plan_image_panels("서로 다른 두 상태를 비교한다.")
        self.assertEqual([panel["id"] for panel in panels], ["before", "after"])

    def test_codex_normalizes_numeric_multiple_choice_answer(self):
        proposals = CodexLocalProvider._validated_proposals([
            {"field": "answer", "label": "정답", "value": "1"}
        ])
        self.assertEqual(proposals[0]["value"], "①")

    def test_codex_accepts_figure_plan_proposal(self):
        proposals = CodexLocalProvider._validated_proposals([{
            "field": "figure_plan", "label": "그림 설계안",
            "value": {"summary": "검전기", "objects": [], "blocked_reason": "electroscope"},
        }])
        self.assertEqual(proposals[0]["field"], "figure_plan")
        self.assertEqual(proposals[0]["value"]["blocked_reason"], "electroscope")

    def test_codex_accepts_bogi_as_a_separate_proposal(self):
        proposals = CodexLocalProvider._validated_proposals([{
            "field": "bogi_items", "label": "보기 전체",
            "value": [{"label": "ㄱ", "text": "전자 일부가 검전기로 이동한다."},
                      {"label": "ㄴ", "text": "금속박의 벌어짐이 줄어든다."}],
        }])
        self.assertEqual(proposals[0]["field"], "bogi_items")
        self.assertEqual(proposals[0]["value"][1]["label"], "ㄴ")
        self.assertIn("bogi_items", DEVELOPER_INSTRUCTIONS)
        self.assertIn("figure_plan", DEVELOPER_INSTRUCTIONS)

    def test_authoring_protocol_column_is_additive(self):
        conn = db.connect()
        try:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(authoring_session)")}
            self.assertIn("provider_protocol", cols)
        finally:
            conn.close()

    def test_stale_provider_thread_is_replaced_for_new_protocol(self):
        sid, _ = self._new()
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE authoring_session SET provider='codex_local',provider_thread_id='old-thread',"
                "provider_protocol='old-protocol' WHERE id=?", (sid,))
            conn.commit()
        finally:
            conn.close()

        seen = {}

        class NewProtocolProvider:
            protocol_version = "new-protocol"

            def stream(self, message, draft, thread_id=None, **kwargs):
                seen["thread_id"] = thread_id
                return "new-thread", iter([])

        with patch.object(ra, "get_provider", return_value=NewProtocolProvider()):
            response = ra.send_message(sid, ra.MessageIn(content="그림을 만들어줘"))
            async def consume():
                return [chunk async for chunk in response.body_iterator]
            asyncio.run(consume())
        self.assertIsNone(seen["thread_id"])
        reopened = ra.get_session(sid)["session"]
        self.assertEqual(reopened["provider_thread_id"], "new-thread")
        self.assertEqual(reopened["provider_protocol"], "new-protocol")

    def test_default_session_uses_local_codex_provider(self):
        data = ra.create_session(ra.SessionIn())
        sid = data["session"]["id"]
        self.session_ids.append(sid)
        self.assertEqual(data["session"]["provider"], "codex_local")

    def test_saved_session_refreshes_from_live_question_without_losing_messages(self):
        conn = db.connect()
        try:
            qid = conn.execute(
                "INSERT INTO question(title,qtype,ask,status) VALUES(?,?,?,?)",
                ("처음 제목", "정답형", "처음 발문", "검토중"),
            ).lastrowid
            conn.commit()
        finally:
            conn.close()
        self.question_ids.append(qid)
        data = ra.create_session(ra.SessionIn(question_id=qid, provider="mock"))
        sid = data["session"]["id"]
        self.session_ids.append(sid)
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO authoring_message(session_id,role,content) VALUES(?,?,?)",
                (sid, "user", "보존할 대화"),
            )
            conn.execute(
                "UPDATE question SET title=?,status=? WHERE id=?",
                ("수정된 제목", "완성", qid),
            )
            conn.commit()
        finally:
            conn.close()
        ra.bind_question(sid, ra.BindIn(question_id=qid))

        reopened = ra.create_session(ra.SessionIn(question_id=qid, provider="codex_local"))
        self.assertEqual(reopened["session"]["draft"]["title"], "수정된 제목")
        self.assertEqual(reopened["session"]["draft"]["question_status"], "완성")
        self.assertEqual(reopened["messages"][0]["content"], "보존할 대화")

    def test_figure_provider_boundary_keeps_stub_state(self):
        provider = get_figure_provider("stub")
        self.assertIsInstance(provider, StubFigureProvider)
        created = provider.create(1, {}, {"status": "none"})
        edited = provider.edit(1, {}, created)
        confirmed = provider.confirm(1, {}, edited)
        self.assertEqual([created["status"], edited["status"], confirmed["status"]],
                         ["draft", "editing", "confirmed"])

    def test_existing_material_is_visible_without_migrating_figure_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "LEGACY_01.png"
            image.write_bytes(b"not-a-real-png")
            conn = db.connect()
            try:
                qid = conn.execute(
                    "INSERT INTO question(title,qtype,ask,material,status) VALUES(?,?,?,?,?)",
                    ("기존 그림 문항", "정답형", "옳은 것은?", "LEGACY_01", "완성"),
                ).lastrowid
                conn.commit()
            finally:
                conn.close()
            self.question_ids.append(qid)
            with patch(
                "app.routes_authoring.hwppalette_provider.resolve_photo",
                return_value=image,
            ):
                data = ra.create_session(ra.SessionIn(question_id=qid, provider="mock"))
                sid = data["session"]["id"]
                self.session_ids.append(sid)
                figure = data["session"]["figure"]
                response = ra.get_figure_image(sid)
            self.assertEqual(figure["status"], "none")
            self.assertEqual(figure["material_name"], "LEGACY_01")
            self.assertEqual(figure["material_image_path"], str(image))
            self.assertEqual(Path(response.path), image)

    def test_figure_routes_use_provider_without_schema_change(self):
        sid, _ = self._new()
        ra.confirm_text(sid)
        with patch("app.routes_authoring.get_figure_provider", return_value=StubFigureProvider()):
            created = ra.figure_action(sid, "create")
            edited = ra.figure_action(sid, "edit")
            confirmed = ra.figure_action(sid, "confirm")
        self.assertEqual(created["figure"]["status"], "draft")
        self.assertEqual(edited["figure"]["status"], "editing")
        self.assertEqual(confirmed["figure"]["status"], "confirmed")

    def test_fivee_provider_creates_compatible_project_and_reuses_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = FiveELocalProvider(root=Path(tmp), port=18190)
            project_path = Path(tmp) / "session" / "figure.5e.json"
            plan = {"summary": "자석과 나침반", "artboard": {"w": 90, "h": 60},
                    "objects": [{"type": "apparatus", "kind": "bar_magnet", "x": -20, "y": -4}]}
            fake_client = unittest.mock.MagicMock()
            def fake_call(name, args):
                if name == "create_project":
                    target = Path(args["path"])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(json.dumps(provider._new_project(7, {"ask": "속력이 증가하는 구간은?"}), ensure_ascii=False), encoding="utf-8")
                elif name == "add_objects" and args.get("path"):
                    target = Path(args["path"])
                    data = json.loads(target.read_text(encoding="utf-8"))
                    data["pages"][0]["objects"] = args["objects"]
                    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            fake_client.call.side_effect = fake_call
            with patch.object(provider, "_ensure_server"), patch.object(
                provider, "_mcp", return_value=fake_client
            ), patch.object(
                provider, "_project_path", return_value=project_path
            ), patch(
                "app.authoring.figures.hwppalette_provider.photo_dir",
                return_value=Path(tmp),
            ), patch("app.authoring.figures.hwppalette_provider.register_photo_dir"), patch.object(
                provider, "_render_preview", return_value=(Path(tmp) / "draft_7.png", "draft_7")
            ):
                created = provider.create(7, {"ask": "속력이 증가하는 구간은?", "figure_plan": plan}, {})
                edited = provider.edit(7, {}, created)
                confirmed = provider.confirm(7, {}, edited)
            data = json.loads(project_path.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], "0.17")
            self.assertEqual(data["pages"][0]["name"], "속력이 증가하는 구간은?")
            self.assertEqual(fake_client.call.call_args_list[1].args[0], "add_objects")
            self.assertTrue(Path(created["scene_spec_path"]).is_file())
            self.assertEqual(edited["fivee_project_path"], str(project_path))
            self.assertEqual(confirmed["status"], "confirmed")

    def test_fivee_activate_materializes_draft_in_live_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = FiveELocalProvider(root=Path(tmp), port=18191)
            project = Path(tmp) / "figure.5e.json"
            project.write_text(json.dumps(provider._new_project(8, {})), encoding="utf-8")
            scene = Path(tmp) / "figure.scene.json"
            scene.write_text(json.dumps({
                "artboard": {"w": 90, "h": 60},
                "objects": [{"type": "apparatus", "kind": "electroscope", "x": -11, "y": -17}],
            }), encoding="utf-8")
            client = unittest.mock.MagicMock()
            client.call.side_effect = lambda name, args=None: (
                json.dumps({"objects": []}) if name == "read_app" else None
            )
            with patch.object(provider, "_mcp", return_value=client):
                result = provider.activate(8, {}, {
                    "status": "draft", "fivee_project_path": str(project),
                    "scene_spec_path": str(scene), "figure_name": "draft_8",
                    "activation_token": "target-editor-123",
                })
            client.wait_for_app.assert_called_once_with(href_token="target-editor-123")
            names = [call.args[0] for call in client.call.call_args_list]
            self.assertEqual(names, [
                "load_project", "set_page", "read_app", "clear_app",
                "set_artboard", "add_objects", "read_app",
            ])
            self.assertEqual(result["status"], "editing")


if __name__ == "__main__":
    unittest.main()
