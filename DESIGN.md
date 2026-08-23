# ExamPool UI contract

## 1. Direction

ExamPool is a dense operational authoring tool. Preserve the existing calm NEIS-like visual language and the established `nz-*` / `au-*` component vocabulary. Accessibility and recovery states must remain visually quiet but unmistakable.

## 2. Tokens

- Colors, typography, spacing, radii, and shadows come from `static/css/neis.css` and `static/css/authoring-apple.css` custom properties.
- New states reuse existing semantic colors and borders; no standalone palette values.
- Body text and controls retain the existing system font stack and compact density.
- Authoring density uses the named `--density-*` toolbar, control, gap, and panel-padding tokens. Compact means less repeated chrome, not smaller reading text or reduced hit targets.
- The PDF→HWP surface aliases the 4px spacing rhythm as `--ph-space-xs/sm/md/lg/xl` and its compact type ramp as `--ph-type-xs/sm/md/lg/xl`; component rules use these aliases instead of repeating raw values.

## 3. Layout

- The top navigation remains a single compact row and becomes horizontally scrollable at narrow widths.
- Content owns vertical scrolling; dialogs own their internal overflow while open.
- All interactive content must remain operable at 320 CSS px and 200% zoom.
- The authoring shell uses one scroll owner per pane. Figure references disclose above a full-width result canvas; they never compete in a two-column split.
- Evidence filters belong to the evidence header cluster. Question preview width is adjustable by the splitter and explicit decrease/increase controls.

## 4. Interaction and motion

- Native buttons are preferred over clickable `div`/`span` elements.
- Focus is always visible. Enter and Space activate button-like controls.
- Dialogs move focus inside, trap Tab, close with Escape, and restore opener focus.
- Motion is limited to existing state transitions and respects reduced-motion preferences.
- Conversion progress advances monotonically from left to right for each job; elapsed-time refreshes and stale polling snapshots never move it backward. A new job is the only event that resets its progress to zero.
- Tab activation changes only selected state; it never reorders the tab list. Disclosure chevrons rotate while content uses an opacity/grid reveal, with the reduced-motion fallback already defined by the workbench.

## 5. Reusable primitives and states

- `nz-navi`: default, selected, focus-visible, horizontally overflowed.
- `nz-modal`: opening, active dialog, validation/error, closing with focus restoration.
- `nz-notice`: info, error, success, retry action.
- `nz-empty`: first-run guidance with direct next-step actions.
- Existing result cards, routine items, reference chips, scope headers, and figure preview use native keyboard-operable controls.
- `ui-icon-button`: default, hover, pressed, focus-visible, disabled; a quiet rounded-square control with an SVG icon and tooltip/accessible name.
- `au-draft-tab`: inactive, active, working, closing; activation preserves stable position.
- `au-figure-reference-pane`: collapsed by default, expanded, empty, populated; its trigger owns `aria-expanded` and `aria-controls`.
- `au-preview-size-controls`: decrease and increase the rendered paper inside a fixed preview panel; the persisted zoom is independent from the panel splitter width.
- `nz-setting-field`: compact label/control pair using the same border, radius, typography, focus, and height tokens as existing `nz-fr` inputs.
- `au-chat-field`: content-sized compact selector; fields wrap when the chat rail narrows and never stretch merely to fill unused horizontal space.
- `ph-upload-panel`: empty, drop-active, pasted, uploading, validation-error; the PDF/image target accepts file selection, drag-and-drop, and Ctrl+V clipboard images without intercepting paste inside editable fields.
- `ph-job-card`: uploaded, detecting, converting, partial-failure, failed, completed; the default surface is an abstract status summary, while item-level review remains an advanced disclosure.
- `ph-status`: polite progress, assertive error, success, retry; progress updates preserve the user's selected file and current job context and expose the current phase plus an estimated remaining duration.
- `ph-output-card`: lists every conversion job with a ready HWP; native checkboxes pick files, each row has a direct download, and `선택한 파일 받기` downloads the current selection. Download names are `{원본이름}_converted.hwp`.
- `ph-output-list`: empty and ready; each recorded output exposes a direct download link from the isolated PDF→HWP API.
- `ph-palette-tools`: collapsed, loading, ready, uploading, success, and error; a quiet advanced disclosure that downloads the complete active `.hwpal` package and activates a user-edited replacement without exposing individual template internals.
- `ph-review-toolbar`: mixed-selection summary with native select-all/clear controls; selection is persisted per detected item and never inferred only from DOM state.
- `ph-review-workspace`: selected/unselected and ready/incomplete states; each item pairs an owned source crop or source-page fallback beside its editable auto-draft.
- `ph-manual-review-item`: unselectable failed-item state with its owned source crop, item number, and actionable failure message; it may coexist with editable ready items in a partial-failure workspace.
- `ph-source-preview`: loading, image-ready, PDF-fallback, and unavailable; media uses the job-owned `/api/pdf-hwp` source/asset routes with explicit alternative text.
- Partial-failure layout follows server capabilities rather than the aggregate status alone: `review_items` keeps the mixed workspace visible, `typeset_selected` permits selected ready drafts to convert, and `retry_failed` keeps failed-item recovery available. Existing outputs remain reachable at the same time.

## 6. Accessibility constraints

- WCAG 2.2 AA keyboard operation and focus behavior are required for all authoring flows.
- Dynamic status and failure messages use appropriate live-region semantics.
- No color-only state communication.
- PDF→HWP uploads accept PDF, PNG, JPEG, and WebP files; errors identify the failed step and keep a keyboard-reachable retry action. Raster inputs require the editable OCR runtime contract: body, ask, formulas, and textual choices remain editable while only material figures remain images.
- A freshly loaded client must refuse raster conversion when `/api/pdf-hwp/runtime` is absent or does not advertise editable OCR. The assertive status explains that the standalone server must be restarted, and no conversion job is created on the stale runtime.
- Job progress is readable without motion. Any busy indicator stops under `prefers-reduced-motion`.
- Review selection uses native checkboxes with item-number labels, exposes a live selected count, and keeps batch actions keyboard reachable.
- Completed HWP outputs use native checkboxes with `{원본}_converted` filename labels, expose a selected count, and keep per-file plus selected-file download actions keyboard reachable.
- Manual-review items do not expose selection controls and are excluded from selected-count and typeset readiness calculations.
- Source imagery is supplementary to the labelled editable draft; conversion remains operable when a preview cannot load.

## 7. Responsive behavior

- 375 px: navigation scrolls horizontally; dialogs fit the viewport; primary actions remain reachable.
- 768 px: existing tablet layout is preserved.
- 1280 px: existing desktop density and panel proportions are preserved.
- The PDF→HWP workspace is a single focused conversion surface at every breakpoint. Internal detection/review details are progressive disclosures, not the default screen; job history never creates a nested page scrollbar.
- Each review item uses a source/draft split at desktop and a source-above-draft stack at 720 px and below; previews are bounded by an aspect-ratio frame and never create horizontal scroll.

## 8. Accepted debt

- The legacy UI remains vanilla JavaScript with inline event handlers in unaffected surfaces. New or touched interactive elements must use native semantics.
- The current product typography and blue-gray palette remain in place for this targeted density pass; broader brand/type changes are intentionally out of scope.
- The conversion tab polls while server work is active. Live server push is deferred because the isolated API contract currently exposes job polling.
