import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AuthoringWorkbenchStaticTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.question_js = (ROOT / "static" / "js" / "question.js").read_text(encoding="utf-8")
        self.authoring_js = (ROOT / "static" / "js" / "authoring.js").read_text(encoding="utf-8")
        self.preview_js = (ROOT / "static" / "js" / "preview.js").read_text(encoding="utf-8")
        self.config_js = (ROOT / "static" / "js" / "config.js").read_text(encoding="utf-8")
        self.base_css = (ROOT / "static" / "css" / "neis.css").read_text(encoding="utf-8")
        self.apple_css = (ROOT / "static" / "css" / "authoring-apple.css").read_text(encoding="utf-8")
        self.css = self.base_css + "\n" + self.apple_css

    def test_workbench_controls_and_panels_are_present_once(self):
        ids = re.findall(r'id="([^"]+)"', self.html)
        self.assertEqual(len(ids), len(set(ids)))
        for required in (
            "qside", "evList", "evViewBody", "questionSettingsSection",
            "questionReviewSection", "auLivePreview", "auMessages", "auFigurePreview",
            "evSelectStart", "evSelectConfirm", "evSelectCancel",
        ):
            self.assertEqual(ids.count(required), 1, required)

    def test_palette_registration_lists_and_visually_tests_each_paint(self):
        self.assertIn('accept=".hwpal"', self.html)
        self.assertIn('accept=".hwp"', self.html)
        self.assertIn("HWP 템플릿 직접 등록", self.html)
        self.assertIn('<option value="suneung" selected>수능 양식으로</option>', self.html)
        self.assertIn('id="qpalette"', self.html)
        self.assertIn("조판 물감", self.html)
        self.assertIn("EP.loadQuestionPaletteOptions", self.question_js)
        self.assertIn("palette_template", self.question_js)
        self.assertIn("EP.testPaletteItem", self.config_js)
        self.assertIn("EP.palettePaintVerdict", self.config_js)
        self.assertIn("실제 조판 시험", self.config_js)
        self.assertIn("조판 성공 · 판정 전", self.config_js)
        self.assertIn("양식의 의도는 사용자가 최종 판정", self.config_js)
        self.assertIn("사진의 크기와 위치가 해당 물감의 목적에 맞는지", self.config_js)
        self.assertIn("EP.openHwpTemplateRegistration", self.config_js)
        self.assertIn("EP.registerHwpTemplate", self.config_js)
        self.assertIn("EP.editPaletteItem", self.config_js)
        self.assertIn("EP.savePaletteEdit", self.config_js)
        self.assertIn("HwpPalette에서 수정", self.config_js)

    def test_figure_preview_is_inside_printed_question_editor(self):
        printed = self.html.index('<div class="nz-print">')
        inline_figure = self.html.index('id="auInlineFigureTools"')
        printed_end = self.html.index('</div>\n            </div>\n\n            <!-- 저장 전 확인', inline_figure)
        self.assertLess(printed, inline_figure)
        self.assertLess(inline_figure, printed_end)

    def test_objective_questions_are_normalized_to_five_choices(self):
        self.assertIn("EP.ensureFiveChoices", self.question_js)
        self.assertIn("while (S.choices.length < 5)", self.question_js)
        render_start = self.question_js.index("EP.renderChoices = function")
        render_end = self.question_js.index("EP.setChoice = function", render_start)
        self.assertNotIn("EP.delChoice", self.question_js[render_start:render_end])

    def test_bogi_and_choice_text_wrap_and_bogi_keeps_reasoning(self):
        self.assertIn('textarea class="nz-autogrow"', self.question_js)
        self.assertIn("EP.autoGrow", self.question_js)
        self.assertIn("'evidence'", self.question_js)
        self.assertIn("'explanation'", self.question_js)
        self.assertIn(".nz-bogi-meta", self.css)

    def test_evidence_region_can_be_connected_as_reference(self):
        self.assertIn("EP.startEvidenceReference", self.question_js)
        self.assertIn("EP.confirmEvidenceReference", self.question_js)
        self.assertIn("EP.authoringAddReferenceData(payload)", self.question_js)
        self.assertIn("EP.authoringReferenceUsage", self.authoring_js)
        self.assertNotIn('id="auReferenceTray"', self.html)

    def test_question_workspace_uses_internal_scrollers(self):
        self.assertIn("body:has(#tab-question:not([hidden]))", self.css)
        self.assertIn("height: 100vh; overflow: hidden", self.css)
        self.assertIn("#tab-question .au-current", self.css)
        self.assertIn("overflow-y: auto", self.css)
        self.assertIn("#tab-question .nz-qmain", self.css)
        self.assertIn("height: 100%", self.css)

    def test_figure_workspace_is_split_into_reference_names_and_result(self):
        self.assertIn('class="au-figure-workspace"', self.html)
        self.assertIn('class="au-figure-reference-pane collapsed"', self.html)
        self.assertIn('class="au-figure-result-pane"', self.html)
        self.assertIn("au-reference-thumb", self.authoring_js)
        self.assertIn("EP.authoringOpenReference", self.authoring_js)
        self.assertIn(".au-figure-reference-pane .au-reference-list img { display: block !important;", self.apple_css)

    def test_live_preview_renders_immediately_before_precise_typesetting(self):
        self.assertIn('id="auQuickPreview"', self.html)
        self.assertIn('id="auPrecisePreviewBtn"', self.html)
        self.assertIn("정밀 조판 보기", self.html)
        self.assertIn("function renderQuickPreview(question)", self.preview_js)
        self.assertIn("renderQuickPreview(question);", self.preview_js)
        self.assertIn('setLiveState("queued")', self.preview_js)
        self.assertIn("setTimeout(() => renderLivePreview(false)", self.preview_js)
        self.assertIn('message.includes("조판하는 중")', self.preview_js)
        self.assertIn("즉시 미리보기", self.preview_js)
        self.assertIn('class="au-quick-number"', self.preview_js)
        self.assertIn("function previewNumber(question)", self.preview_js)
        self.assertIn("${previewNumber(question)}.", self.preview_js)
        self.assertIn("수능형 배치 즉시 확인", self.preview_js)
        self.assertIn('showLoading("현재 문항 출력 미리보기", true)', self.preview_js)
        self.assertNotIn('<div class="au-quick-ask">1.', self.preview_js)
        self.assertNotIn("아직 미적용", self.preview_js)
        self.assertIn("EP.invalidateQuestionPreview", self.preview_js)
        self.assertIn("EP.invalidateQuestionPreview", self.authoring_js)

    def test_frontend_authoring_protocol_matches_backend(self):
        from app.authoring.providers import CodexLocalProvider
        self.assertIn(
            f'EXPECTED_AUTHORING_PROTOCOL = "{CodexLocalProvider.protocol_version}"',
            self.authoring_js,
        )

    def test_two_photo_palette_drives_separate_figure_generation(self):
        self.assertIn("requiredFigures > 1", self.question_js)
        self.assertIn('composition.value = "separate"', self.question_js)
        self.assertIn("function requiredFigureSlotCount()", self.authoring_js)
        self.assertIn('class="au-figure-slot-empty"', self.authoring_js)
        self.assertIn("사진 ${index + 1}", self.authoring_js)

    def test_device_code_login_is_available_as_browser_login_fallback(self):
        self.assertIn('id="auDeviceLogin"', self.html)
        self.assertIn("EP.authoringDeviceLogin", self.authoring_js)
        self.assertIn('post("/api/authoring/login/device"', self.authoring_js)

    def test_per_session_background_work_and_workflow_modes_are_wired(self):
        self.assertIn('id="auWorkflowMode"', self.html)
        self.assertIn('id="auPurposeMode"', self.html)
        self.assertIn("EP.authoringChangePurpose", self.authoring_js)
        self.assertIn("const activeRequests = new Map()", self.authoring_js)
        self.assertNotIn("현재 AI 요청이 끝난 뒤", self.authoring_js)
        self.assertIn("EP.authoringApplyAll", self.authoring_js)
        self.assertIn("EP.authoringAutoReferences", self.authoring_js)
        self.assertIn('EP.authoringFigure("draw")', self.authoring_js)

    def test_refreshed_workbench_requirements_are_wired(self):
        self.assertIn('class="nz-menu folded"', self.html)
        self.assertIn('class="nz-meta collapsed" id="questionSettingsSection"', self.html)
        self.assertIn('id="qexplanation"', self.html)
        self.assertIn('id="questionFullscreenBtn"', self.html)
        self.assertIn('id="auDraftTabs"', self.html)
        self.assertIn("EP.authoringSwitch", self.authoring_js)
        self.assertIn("nz-combo-choice-row", self.question_js)
        self.assertIn("grid-template-columns: repeat(5", self.apple_css)
        self.assertIn("grid-template-columns: 76px minmax(0, 1fr)", self.apple_css)
        self.assertIn("prefers-reduced-motion: reduce", self.apple_css)
        self.assertIn("question-fullscreen-active", self.question_js)
        self.assertIn('aria-controls="qsideContent"', self.html)
        self.assertIn('class="nz-sidefolded"', self.html)
        self.assertIn('grid-template-columns: 52px minmax(86px,1fr) 76px auto', self.apple_css)
        self.assertIn('event.key === "Escape"', self.question_js)
        self.assertIn('grid-template-rows: 0fr', self.apple_css)
        self.assertIn('id="stdMenuToggle"', self.html)

    def test_diagnostic_recovery_and_accessibility_contracts_are_wired(self):
        core_js = (ROOT / "static" / "js" / "core.js").read_text(encoding="utf-8")
        qbank_js = (ROOT / "static" / "js" / "qbank.js").read_text(encoding="utf-8")
        self.assertIn("EP.showAppError", core_js)
        self.assertIn('setAttribute("role", "dialog")', core_js)
        self.assertIn('event.key === "Escape"', core_js)
        self.assertIn("activationSequence", self.authoring_js)
        self.assertIn("else await EP.loadRefs(qid);", qbank_js)
        self.assertIn('id="appStatus"', self.html)
        self.assertIn('data-first-run', self.html)
        self.assertIn('class="au-figure-preview au-inline-figure-preview"', self.html)
        self.assertIn('onkeydown="EP.activateOnKey(event, () => EP.authoringOpenFigureEditor(event))"', self.html)
        self.assertIn("overflow-x: auto", self.base_css)

    def test_internal_object_envelopes_are_never_rendered_as_public_text(self):
        self.assertIn("function humanText(value)", self.authoring_js)
        self.assertIn("function publicText(value)", self.preview_js)
        self.assertIn('text.trim().toLowerCase() === "[object object]"', self.authoring_js)
        self.assertIn('text.trim().toLowerCase() === "[object object]"', self.preview_js)

    def test_three_panel_widths_are_user_resizable_and_persisted(self):
        self.assertIn('id="authoringEvidenceDrag"', self.html)
        self.assertIn('id="authoringRightDrag"', self.html)
        self.assertIn("ep_authoring_evidence_width", self.authoring_js)
        self.assertIn("ep_authoring_right_width", self.authoring_js)
        self.assertIn("EP.resetAuthoringPanelWidths", self.authoring_js)
        self.assertIn("--au-evidence-width", self.css)
        self.assertIn("--au-right-width", self.css)
        self.assertIn("AUTHORING_PANEL_DEFAULTS = { evidence: 37, right: 44 }", self.authoring_js)

    def test_chat_controls_are_compact_and_preview_can_collapse_independently(self):
        self.assertIn('class="au-chat-toolbar"', self.html)
        self.assertIn('id="auPreviewToggle"', self.html)
        self.assertIn("EP.toggleAuthoringPreview", self.question_js)
        self.assertIn("ep_authoring_preview_collapsed", self.question_js)
        self.assertIn(".au-grid.preview-collapsed", self.css)
        self.assertIn("grid-template-rows: 34px minmax(0, 1fr)", self.css)

    def test_density_refresh_keeps_stable_tabs_and_explicit_preview_sizing(self):
        self.assertIn("function upsertAuthoringTab", self.authoring_js)
        self.assertIn("rows.splice(existingIndex, 1, nextRow)", self.authoring_js)
        self.assertNotIn("next.push({ id: value.id, title })", self.authoring_js)
        self.assertIn('id="auPreviewSmaller"', self.html)
        self.assertIn('id="auPreviewLarger"', self.html)
        self.assertIn("EP.resizeAuthoringPreview", self.authoring_js)

    def test_passage_grows_with_content_and_preview_controls_zoom_the_page(self):
        self.assertIn('id="qpassage" class="nz-autogrow nz-passage-autogrow"', self.html)
        self.assertIn('oninput="EP.autoGrow(this)"', self.html)
        self.assertIn('data-max-height="420"', self.html)
        self.assertIn("ep_authoring_preview_zoom", self.authoring_js)
        self.assertIn('$("auLivePreviewImage")', self.authoring_js)
        resize_start = self.authoring_js.index("EP.resizeAuthoringPreview = function")
        resize_end = self.authoring_js.index("function setFigureProgress", resize_start)
        resize_body = self.authoring_js[resize_start:resize_end]
        self.assertNotIn("--au-right-width", resize_body)
        self.assertNotIn("미리보기 폭", resize_body)
        self.assertIn('id="auPreviewZoomLabel"', self.html)

    def test_settings_and_chat_use_semantic_compact_layouts(self):
        self.assertIn('class="nz-settings-grid"', self.html)
        self.assertIn('class="nz-setting-field', self.html)
        self.assertIn('class="au-chat-field au-chat-field-purpose"', self.html)
        self.assertIn(".nz-settings-grid", self.apple_css)
        self.assertIn(".au-chat-field-purpose", self.apple_css)
        self.assertIn(".nz-setting-field input, .nz-setting-field select", self.apple_css)
        self.assertIn("border: 1px solid var(--ui-border-strong)", self.apple_css)
        self.assertIn("flex: 0 0 104px", self.apple_css)
        self.assertIn("max-width: 138px", self.apple_css)
        self.assertIn("#tab-question .au-chat-toolbar select { min-width: 0;", self.apple_css)
        self.assertNotIn("#tab-question .au-chat-field { flex: 0 1", self.apple_css)

    def test_figure_references_are_disclosed_above_a_full_width_result(self):
        self.assertIn('class="au-figure-reference-pane collapsed"', self.html)
        self.assertIn('id="auFigureReferenceBody"', self.html)
        self.assertIn("EP.toggleFigureReferences", self.question_js)
        self.assertIn("grid-template-columns: minmax(0, 1fr)", self.apple_css)
        self.assertIn(".au-figure-reference-pane.collapsed .au-figure-reference-body", self.apple_css)

    def test_evidence_filters_share_the_compact_panel_header(self):
        side_head = self.html.index('<div class="nz-sidehead">')
        source_filters = self.html.index('<span class="nz-srcbtns">', side_head)
        side_head_end = self.html.index("</div>", side_head)
        self.assertLess(source_filters, side_head_end)
        self.assertIn("--density-toolbar", self.apple_css)

    def test_preview_header_responds_to_its_panel_width(self):
        self.assertIn("container-type: inline-size", self.apple_css)
        self.assertIn("@container (max-width: 360px)", self.apple_css)

    def test_density_pass_preserves_editor_actions_and_mobile_priority(self):
        self.assertIn('onclick="EP.refreshQuestionPreview()"', self.html)
        self.assertIn(".au-preview-size-controls { display: none; }", self.apple_css)
        self.assertIn("#tab-question .au-current { order: 1; }", self.apple_css)
        self.assertIn("#tab-question .au-panel.au-chat { order: 3; }", self.apple_css)
        self.assertIn("#tab-question .au-statebar { flex-wrap: wrap; }", self.apple_css)
        self.assertIn("#tab-question .au-state-actions { flex: 1 1 100%; flex-wrap: wrap;", self.apple_css)

    def test_mobile_labels_are_legible_and_active_tab_stays_visible(self):
        self.assertIn("white-space: nowrap", self.apple_css)
        self.assertIn("font-size: 11px", self.apple_css)
        self.assertIn('box.querySelector(".au-draft-tab.active")', self.authoring_js)
        self.assertIn("box.scrollLeft", self.authoring_js)
        self.assertIn('window.addEventListener("resize", keepActiveAuthoringTabVisible)', self.authoring_js)
        self.assertIn("#tab-question .au-status", self.apple_css)
        self.assertIn("#tab-question .au-inline-figure-head > b", self.apple_css)
        self.assertIn("#tab-question .nz-refempty", self.apple_css)
        self.assertIn("word-break: keep-all", self.apple_css)
        self.assertIn("overflow-wrap: normal", self.apple_css)


if __name__ == "__main__":
    unittest.main()
