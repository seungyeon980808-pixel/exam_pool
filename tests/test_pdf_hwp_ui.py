from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pdf_hwp_tab_has_a_dedicated_accessible_workflow() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert 'data-tab="pdf-hwp"' in html
    assert 'id="tab-pdf-hwp"' in html
    assert 'aria-labelledby="pdfHwpTitle"' in html
    assert 'id="pdfHwpFile"' in html
    assert 'aria-describedby="pdfHwpFileHelp"' in html
    assert 'class="ph-file-action"' in html
    assert 'id="pdfHwpDropzone"' in html
    assert 'PDF·이미지를 놓으세요' in html
    assert 'aria-label="PDF, PNG, JPEG 또는 WebP 파일 선택"' in html
    assert 'accept="application/pdf,.pdf,image/png,image/jpeg,image/webp"' in html
    assert 'multiple required' in html
    assert 'id="pdfHwpFileHelp"' in html
    assert '클립보드 이미지를 Ctrl+V로 붙여넣으세요.' in html
    assert '출력 양식: 수능형 시험지' in html
    assert 'id="pdfHwpProgressBar"' in html
    assert 'id="pdfHwpTotalElapsed"' in html
    assert 'id="pdfHwpStart"' in html
    assert 'id="pdfHwpOutput"' in html
    assert 'id="pdfHwpStatus"' in html
    assert '변환 상세 보기' in html
    assert '원문 미리보기 · HwpPalette 조판 텍스트' in html
    assert '<details class="ph-history"' in html
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
    assert 'request("/runtime")' in client
    assert '이미지 편집 변환 서버가 이전 버전입니다. PDF-HWP 앱을 종료한 뒤 다시 실행해 주세요.' in client
    assert 'EP.pdfHwpInit' in client
    assert 'EP.pdfHwpStart' in client
    assert 'state.pendingFiles = selected' in client
    assert 'document.addEventListener("paste"' in client
    assert '변환 준비 완료' in client
    assert '해당 파일 ${formatDuration(spent)} / 총 진행 ${formatDuration(totalSpent)}' in client
    assert 'EP.pdfHwpStart({ preventDefault() {}, files })' not in client
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
    assert 'fieldTitle.textContent = "HwpPalette용 조판 텍스트"' in client
    assert 'patchReviewItem(byId(job.id) || job, item, { palette_markdown: textarea.value.trim() })' in client
    assert 'makeField("문항번호"' not in client
    assert 'makeField("분야"' not in client
    assert 'makeField("문항 유형"' not in client
    assert 'makeField("응답 구조"' not in client
    assert 'makeField("자료 이미지 개수"' not in client
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
    assert 'dragenter' in client
    assert 'new DataTransfer()' in client
    assert 'renderSourcePreviewContent(job, box)' in client
    assert 'layout_style: "suneung"' in client
    assert 'startAutomaticTypeset(job)' in client
    assert 'if (hasFallback && rasterSource) return job' in client
    assert 'state.autoStart' in client
    assert 'new DataTransfer()' in client
    assert 'function convertedFileName' in client
    assert 'EP.pdfHwpDownloadSelected' in client
    assert 'ph-output-ready-list' in client
    assert '${stem}_converted' in client


def test_standalone_converter_starts_with_a_clean_history_view() -> None:
    html = (ROOT / "static" / "pdf-hwp.html").read_text(encoding="utf-8")
    client = (ROOT / "static" / "js" / "pdf-hwp.js").read_text(encoding="utf-8")

    assert 'data-clear-conversion-history-on-refresh="true"' in html
    assert 'document.body.dataset.clearConversionHistoryOnRefresh === "true"' in client
    assert "state.currentId = state.jobs[0]?.id || null" in client


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
    assert ".ph-page .nz-tb:disabled" in styles
    assert "cursor: not-allowed" in styles
    assert "--ph-space-md" in styles
    assert ".ph-keep { white-space: nowrap; }" in styles
    assert "justify-self: end; inline-size: max-content" in styles
    assert ".ph-review-toolbar" in styles
    assert ".ph-review-workspace" in styles
    assert ".ph-source-preview" in styles
    assert ".ph-source-details" in styles
    assert ".ph-source-preview img" in styles and "object-fit: contain" in styles and "block-size: auto" in styles
    assert ".ph-review-item-failed { border-color: var(--ui-danger); }" in styles
    assert "#d9a09a" not in styles
    assert "#e7c5c1" not in styles
    assert "#fff8f7" not in styles
    assert "#7a3630" not in styles
    assert "padding: 3px 7px" not in styles
    assert ".ph-job-state" in styles and "padding: var(--ph-space-xs) var(--ph-space-sm)" in styles
    assert ".ph-output-ready-list" in styles
    assert ".ph-output-ready-item" in styles
    assert ".ph-output-filename" in styles
    assert "--ph-space-2xs" not in styles
    assert "--ph-space-control" not in styles
    assert "--ph-space-2xl" not in styles


def test_core_registers_pdf_hwp_without_removing_authoring_hooks() -> None:
    core = (ROOT / "static" / "js" / "core.js").read_text(encoding="utf-8")

    assert '"pdf-hwp"' in core
    assert 'if (tab === "pdf-hwp") EP.pdfHwpInit();' in core
    assert "if (EP.authoringInit) EP.authoringInit();" in core
    assert 'btn.setAttribute("aria-current", "page")' in core
