from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pdf_hwp_tab_has_a_dedicated_accessible_workflow() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert 'data-tab="pdf-hwp"' in html
    assert 'id="tab-pdf-hwp"' in html
    assert 'aria-labelledby="pdfHwpTitle"' in html
    assert 'id="pdfHwpFile"' in html
    assert 'class="ph-file-action"' in html
    assert 'accept="application/pdf,.pdf"' in html
    assert 'id="pdfHwpStart"' in html
    assert 'id="pdfHwpStatus"' in html
    assert '원본 배치를 살린 <span class="ph-keep">HWP와 확인용 PDF를 만듭니다.</span>' in html
    assert '<span class="ph-keep">서버에 작업을 만들지 않습니다.</span>' in html
    assert '<span class="ph-keep">선택한 파일은 오류가 나도 유지됩니다.</span>' in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert 'id="pdfHwpRetry"' in html
    assert 'id="pdfHwpTypeset"' in html
    assert 'id="pdfHwpJobs"' in html
    assert '/static/js/pdf-hwp.js' in html
    assert '/static/css/pdf-hwp.css' in html


def test_pdf_hwp_client_is_isolated_to_its_api_namespace() -> None:
    client = (ROOT / "static" / "js" / "pdf-hwp.js").read_text(encoding="utf-8")

    assert 'const API_ROOT = "/api/pdf-hwp"' in client
    assert 'EP.pdfHwpInit' in client
    assert 'EP.pdfHwpStart' in client
    assert 'EP.pdfHwpRetry' in client
    assert 'EP.pdfHwpTypeset' in client
    assert 'payload.items || []' in client
    assert '`/jobs/${encodeURIComponent(targetId)}/typeset`' in client
    assert "FormData" in client
    assert "/api/authoring" not in client
    assert "/api/questions" not in client
    assert "window.alert" not in client
    assert 'let message = "요청을 처리하지 못했습니다."' in client
    assert "message = raw" not in client
    assert 'box.setAttribute("role", tone === "error" ? "alert" : "status")' in client
    assert "if (replaceJob(job)) { renderJobs();" in client
    assert "mutationSeq: new Map()" in client
    assert '=== requestEpoch && isSelected(id)) showFailure(error.message, recoverable)' in client
    assert "renderCurrent(job); schedulePoll(job);" in client
    assert 'current?.status === "draft" || current?.status === "uploaded"' in client
    assert "job = await uploadAndDetect(current, file)" in client
    assert "recoveries: new Set()" in client
    assert "const targetId = state.currentId" in client
    assert "state.recoveries.delete(String(targetId))" in client
    assert 'if (isSelected(targetId)) showFailure(error.message' in client
    assert 'showFailure(error.message, true)' in client
    assert 'selectedItems(job)' in client
    assert 'const selected = checkbox.checked' in client
    assert 'const markdown = textarea.value' in client
    assert 'patchReviewItem(byId(job.id) || job, item, { selected })' in client
    assert '`${API_ROOT}/jobs/${encodeURIComponent(job.id)}/assets/${encodeURIComponent(asset.id)}`' in client
    assert '`${API_ROOT}/jobs/${encodeURIComponent(job.id)}/source#page=${encodeURIComponent(item.source_page || 1)}`' in client
    assert 'EP.pdfHwpSelectAll' in client
    assert 'job.capabilities?.review_items' in client
    assert 'job?.capabilities?.typeset_selected' in client
    assert 'job.capabilities?.retry_failed' in client
    assert 'makeFailureDisclosure(job)' in client
    assert 'state.mutationQueue.has(String(job.id))' in client
    assert 'if (pending) await pending.catch' in client
    assert 'function reviewLocked(id)' in client
    assert 'draftEdits: new Map()' in client
    assert 'textarea.oninput' in client
    assert 'image.onerror' in client
    assert 'makeSourceLink(job, item)' in client
    assert 'const roles = ["figure", "figure_panel", "source_crop", "crop"]' in client
    assert 'renderCurrent(byId(job.id))' in client


def test_pdf_hwp_styles_define_responsive_and_accessible_states() -> None:
    styles = (ROOT / "static" / "css" / "pdf-hwp.css").read_text(encoding="utf-8")

    assert ".ph-workspace" in styles
    assert '.ph-status[data-tone="error"]' in styles
    assert '.ph-status[data-tone="success"]' in styles
    assert "@media (max-width: 720px)" in styles
    assert "minmax(min(" in styles
    assert "prefers-reduced-motion: reduce" in styles
    assert ":focus-visible" in styles
    assert "word-break: keep-all" in styles
    assert ".ph-file-field input { position: absolute;" in styles
    assert "--ph-space-md" in styles
    assert ".ph-keep { white-space: nowrap; }" in styles
    assert "justify-self: end; inline-size: max-content" in styles
    assert ".ph-review-toolbar" in styles
    assert ".ph-review-workspace" in styles
    assert ".ph-source-preview" in styles
    assert ".ph-review-item-failed { border-color: var(--ui-danger); }" in styles
    assert "#d9a09a" not in styles
    assert "#e7c5c1" not in styles
    assert "#fff8f7" not in styles
    assert "#7a3630" not in styles
    assert "padding: 3px 7px" not in styles
    assert ".ph-job-state" in styles and "padding: var(--ph-space-xs) var(--ph-space-sm)" in styles
    assert "--ph-space-2xs" not in styles
    assert "--ph-space-control" not in styles
    assert "--ph-space-2xl" not in styles


def test_core_registers_pdf_hwp_without_removing_authoring_hooks() -> None:
    core = (ROOT / "static" / "js" / "core.js").read_text(encoding="utf-8")

    assert '"pdf-hwp"' in core
    assert 'if (tab === "pdf-hwp") EP.pdfHwpInit();' in core
    assert "if (EP.authoringInit) EP.authoringInit();" in core
    assert 'btn.setAttribute("aria-current", "page")' in core
