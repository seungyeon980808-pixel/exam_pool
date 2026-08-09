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
        self.css = (ROOT / "static" / "css" / "neis.css").read_text(encoding="utf-8")

    def test_workbench_controls_and_panels_are_present_once(self):
        ids = re.findall(r'id="([^"]+)"', self.html)
        self.assertEqual(len(ids), len(set(ids)))
        for required in (
            "qside", "evList", "evViewBody", "questionSettingsSection",
            "questionReviewSection", "auLivePreview", "auMessages", "auFigurePreview",
            "evSelectStart", "evSelectConfirm", "evSelectCancel",
        ):
            self.assertEqual(ids.count(required), 1, required)

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
        self.assertIn('class="au-figure-reference-pane"', self.html)
        self.assertIn('class="au-figure-result-pane"', self.html)
        self.assertIn(".au-figure-reference-pane .au-reference-list img { display: none; }", self.css)

    def test_live_preview_renders_immediately_before_precise_typesetting(self):
        self.assertIn('id="auQuickPreview"', self.html)
        self.assertIn("function renderQuickPreview(question)", self.preview_js)
        self.assertIn("renderQuickPreview(question);", self.preview_js)
        self.assertIn("delay == null ? 3200 : delay", self.preview_js)

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


if __name__ == "__main__":
    unittest.main()
