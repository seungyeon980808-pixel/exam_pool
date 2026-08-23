/* ===== PDF → HWP 전용 작업 화면 ===== */
(function (EP) {
  "use strict";

  const API_ROOT = "/api/pdf-hwp";
  const ACTIVE_STATUSES = new Set(["draft", "uploaded", "detecting", "typesetting"]);
  const STATUS = {
    draft: ["작업 준비", "PDF를 업로드할 준비가 되었습니다.", "working", "upload"],
    uploaded: ["파일 업로드 완료", "문항을 찾기 시작합니다.", "working", "detect"],
    detecting: ["문항 검출 중", "페이지와 문항 경계를 분석하고 있습니다.", "working", "detect"],
    review: ["문항 검출 완료", "문항별 변환 내용을 검토하고 저장하세요.", "idle", "convert"],
    typesetting: ["HWP 변환 중", "원본 배치를 적용해 결과 파일을 만들고 있습니다.", "working", "convert"],
    partial_failure: ["일부 문항 변환 실패", "완료된 결과는 유지됩니다. 실패 단계만 다시 시도할 수 있습니다.", "error", "convert"],
    failed: ["변환 실패", "오류 내용을 확인한 뒤 같은 작업을 다시 시도하세요.", "error", "convert"],
    cancelled: ["변환 취소됨", "이 작업은 중단되었습니다.", "error", "convert"],
    completed: ["변환 완료", "HWP 파일을 받을 수 있습니다.", "success", "complete"],
  };
  const state = { initialized: false, currentId: null, recoveries: new Set(), autoRuns: new Set(), inFlight: new Set(), mutationSeq: new Map(), mutationQueue: new Map(), draftEdits: new Map(), operations: new Map(), startedAt: new Map(), finishedAt: new Map(), progressByJob: new Map(), batchStartedAt: null, batchFinishedAt: null, elapsedTimer: null, pendingFiles: [], jobEpoch: new Map(), dataEpoch: 0, jobs: [], pollTimer: null, autoStart: null, outputPicks: new Set(), outputPickKnown: new Set() };

  async function request(path, options) {
    let response;
    try {
      response = await fetch(`${API_ROOT}${path}`, options);
    } catch (error) {
      throw new Error("서버에 연결하지 못했습니다. ExamPool 실행 상태를 확인하세요.");
    }
    if (!response.ok) {
      const raw = await response.text();
      let message = "요청을 처리하지 못했습니다.";
      try {
        const payload = JSON.parse(raw);
        message = payload.detail?.message || payload.detail || payload.error?.message || message;
      } catch (error) { /* plain server message */ }
      throw new Error(String(message).slice(0, 500));
    }
    return response.json();
  }

  function jobFrom(payload) { return payload.job || payload; }
  function jobsFrom(payload) { return payload.items || []; }
  function byId(id) { return state.jobs.find((job) => String(job.id) === String(id)); }
  function isSelected(id) { return String(state.currentId) === String(id); }
  function actionKey(action, id) { return `${action}:${id}`; }
  function editKey(jobId, itemId) { return `${jobId}:${itemId}`; }
  function reviewLocked(id) { return state.inFlight.has(actionKey("typeset", id)); }
  function setReviewControlsDisabled(disabled) {
    document.querySelectorAll("#pdfHwpCurrent .ph-review-list input, #pdfHwpCurrent .ph-review-list textarea, #pdfHwpCurrent .ph-review-list button, #pdfHwpCurrent .ph-review-list [contenteditable], #pdfHwpCurrent .ph-review-batch button")
      .forEach((control) => {
        if (control.hasAttribute("contenteditable")) {
          control.contentEditable = disabled ? "false" : "true";
          control.setAttribute("aria-disabled", String(disabled));
        } else control.disabled = disabled;
      });
  }
  function beginMutation(id) {
    const next = (state.mutationSeq.get(String(id)) || 0) + 1;
    state.mutationSeq.set(String(id), next); return next;
  }
  function isLatestMutation(id, sequence) { return state.mutationSeq.get(String(id)) === sequence; }
  function bumpJobEpoch(id) {
    const key = String(id);
    state.jobEpoch.set(key, (state.jobEpoch.get(key) || 0) + 1);
    state.dataEpoch += 1;
  }
  function queueMutation(id, operation) {
    const key = String(id);
    const previous = state.mutationQueue.get(key) || Promise.resolve();
    const queued = previous.catch(() => undefined).then(operation);
    state.mutationQueue.set(key, queued);
    if (isSelected(key)) EP.$("pdfHwpTypeset").disabled = true;
    const release = () => {
      if (state.mutationQueue.get(key) === queued) state.mutationQueue.delete(key);
      syncTypesetControl(key);
    };
    queued.then(release, release);
    return queued;
  }
  function acceptMutation(job, sequence) {
    const currentRevision = Number(byId(job.id)?.revision || 0);
    const incomingRevision = Number(job.revision || 0);
    if (incomingRevision < currentRevision) return false;
    if (incomingRevision === currentRevision && !isLatestMutation(job.id, sequence)) return false;
    return replaceJob(job);
  }
  function renderSelected(job, poll = false) {
    if (!isSelected(job.id)) return;
    renderCurrent(job);
    if (poll) schedulePoll(job);
  }
  function statusInfo(status) { return STATUS[status] || ["상태 확인 중", status || "서버 응답을 기다립니다.", "working", "detect"]; }
  function formatDuration(seconds) {
    const value = Math.max(0, Math.round(seconds));
    if (value < 60) return `${value}초`;
    const minutes = Math.floor(value / 60), rest = value % 60;
    return `${minutes}분 ${rest}초`;
  }
  function setProgressBar(progress, jobId = null) {
    const bar = EP.$("pdfHwpProgressBar");
    if (!bar) return;
    const normalized = Math.max(0, Math.min(100, Number(progress) || 0));
    const key = jobId == null ? null : String(jobId);
    const visible = key == null ? normalized : Math.max(state.progressByJob.get(key) || 0, normalized);
    if (key != null) state.progressByJob.set(key, visible);
    bar.style.inlineSize = `${visible}%`;
  }
  function renderProgress(job, operation = null) {
    const phase = EP.$("pdfHwpPhase"), elapsed = EP.$("pdfHwpElapsed"), total = EP.$("pdfHwpTotalElapsed");
    if (!phase || !elapsed || !total) return;
    const batchEnd = state.batchFinishedAt || Date.now();
    const totalSpent = state.batchStartedAt ? Math.max(0, (batchEnd - state.batchStartedAt) / 1000) : 0;
    total.textContent = `총 진행 ${formatDuration(totalSpent)}`;
    if (!job) {
      setProgressBar(0); phase.textContent = "대기 중"; elapsed.textContent = `해당 파일 0초 / 총 진행 ${formatDuration(totalSpent)}`; return;
    }
    const started = state.startedAt.get(String(job.id));
    const finished = state.finishedAt.get(String(job.id)) || Date.now();
    const spent = started ? Math.max(0, (finished - started) / 1000) : 0;
    let progress = 0, label = "준비 중";
    if (job.status === "uploaded") { progress = 5; label = "파일 업로드 완료"; }
    else if (job.status === "detecting") { progress = 10 + Number(operation?.progress ?? job.detection_progress ?? 0) * .4; label = "문항 분석 중"; }
    else if (["review", "partial_failure"].includes(job.status) && outputCount(job) === 0) { progress = 55; label = "문항 분석 완료 · HWP 조판 준비"; }
    else if (job.status === "typesetting") { progress = 55 + Number(operation?.progress ?? job.generation_progress ?? 0) * .45; label = "HWP 조판 중"; }
    else if (job.status === "completed" || (outputCount(job) > 0 && ["partial_failure", "failed"].includes(job.status))) { progress = 100; label = "HWP 파일 준비 완료"; }
    else if (job.status === "failed" && state.autoRuns.has(String(job.id))) { progress = 55; label = "실패 문항 원문 보존 중"; }
    else if (job.status === "failed" || job.status === "cancelled") { label = "변환 중단"; }
    setProgressBar(progress, job.id); phase.textContent = label;
    elapsed.textContent = `해당 파일 ${formatDuration(spent)} / 총 진행 ${formatDuration(totalSpent)}`;
  }
  function startElapsedClock() {
    if (state.elapsedTimer) return;
    state.elapsedTimer = window.setInterval(() => renderProgress(byId(state.currentId)), 1000);
  }
  function stopElapsedClock() {
    if (!state.elapsedTimer) return;
    window.clearInterval(state.elapsedTimer); state.elapsedTimer = null;
  }
  function errorMessage(job) { return job?.error?.message || ""; }
  function itemCount(job) { return Array.isArray(job?.items) ? job.items.length : Number(job?.item_count || 0); }
  function outputCount(job) { return Array.isArray(job?.outputs) ? job.outputs.length : 0; }
  function itemStats(job) {
    const items = Array.isArray(job?.items) ? job.items : [];
    return {
      ready: items.filter((item) => item.status === "ready").length,
      failed: items.filter((item) => ["failed", "manual_required", "conversion_failed"].includes(item.status)).length,
    };
  }
  function jobStatusInfo(job) {
    const stats = itemStats(job);
    if (stats.failed > 0 && outputCount(job) > 0) {
      return ["변환 완료 · 원문 보존", `성공 ${stats.ready}개는 편집 가능하게, 실패 ${stats.failed}개는 자동 추출 원문으로 포함했습니다.`, "success", "complete"];
    }
    if (stats.failed > 0 && stats.ready === 0) {
      return ["실패 문항 원문 보존 후 변환", `실패 ${stats.failed}개 문항도 자동 추출 원문으로 HWP에 포함합니다.`, "working", "convert"];
    }
    if (stats.failed > 0 && stats.ready > 0) {
      return ["모든 문항을 포함해 변환", `성공 ${stats.ready}개는 편집 가능하게, 실패 ${stats.failed}개는 자동 추출 원문으로 보존합니다.`, "working", "convert"];
    }
    return statusInfo(job?.status);
  }
  function jobStatusMessage(job, fallback) {
    const stats = itemStats(job);
    if (stats.failed > 0) return jobStatusInfo(job)[1];
    return errorMessage(job) || fallback;
  }
  function itemErrorMessage(item) {
    const message = item?.error?.message || "";
    if (message.includes("graphical answer choices require manual review")) {
      return "PDF 문자·그림 선지 인식에 실패했습니다.";
    }
    if (message.includes("formula extraction")) return "수식 내용을 자동으로 읽지 못했습니다.";
    return message || "자동 변환이 불확실하여 확인이 필요합니다.";
  }

  function setTone(box, tone) {
    box.dataset.tone = tone;
    box.setAttribute("role", tone === "error" ? "alert" : "status");
    box.setAttribute("aria-live", tone === "error" ? "assertive" : "polite");
  }

  function setBusy(active) {
    const input = EP.$("pdfHwpFile"), dropzone = EP.$("pdfHwpDropzone"), start = EP.$("pdfHwpStart");
    if (input) input.disabled = active;
    if (start) start.disabled = active || state.pendingFiles.length === 0;
    if (dropzone) {
      dropzone.setAttribute("aria-busy", String(active));
      dropzone.classList.toggle("is-processing", active);
    }
  }

  function renderSteps(current) {
    const order = ["upload", "detect", "convert", "complete"];
    const currentIndex = order.indexOf(current);
    EP.$("pdfHwpSteps").querySelectorAll("li").forEach((item, index) => {
      item.classList.toggle("done", currentIndex > index);
      item.classList.toggle("current", currentIndex === index);
      if (currentIndex === index) item.setAttribute("aria-current", "step");
      else item.removeAttribute("aria-current");
    });
  }

  function paletteText(item) { return item?.draft?.palette_markdown || ""; }
  function fallbackItems(job) {
    return (job?.items || []).filter((item) => ["failed", "manual_required", "conversion_failed"].includes(item.status));
  }
  function selectedItems(job) {
    return (job?.items || []).filter((item) => item.selected && !["failed", "manual_required", "conversion_failed"].includes(item.status));
  }
  function canTypeset(job) {
    const viable = selectedItems(job);
    const fallback = fallbackItems(job);
    if (!viable.length && fallback.length) return true;
    return Boolean(job?.capabilities?.typeset_selected) && viable.length > 0 && viable.every((item) => item.status === "ready" && (item.confirmed !== false || (!item.domain && !item.type_id)) && paletteText(item).trim() && !state.draftEdits.has(editKey(job.id, item.id)));
  }

  function syncTypesetControl(id) {
    if (!isSelected(id)) return;
    const job = byId(id), button = EP.$("pdfHwpTypeset");
    button.disabled = !canTypeset(job) || state.mutationQueue.has(String(job.id)) || state.inFlight.has(actionKey("typeset", job.id));
  }

  function sourceAsset(job, item) {
    const roles = ["figure", "figure_panel", "source_crop", "crop"];
    return roles.map((role) => (job.assets || []).find((asset) => asset.item_id === item.id && asset.role === role)).find(Boolean);
  }

  function makeSourceLink(job, item) {
    const link = document.createElement("a"); link.className = "ph-source-fallback";
    link.href = `${API_ROOT}/jobs/${encodeURIComponent(job.id)}/source#page=${encodeURIComponent(item.source_page || 1)}`;
    link.target = "_blank"; link.rel = "noopener";
    link.textContent = `PDF ${item.source_page || "?"}쪽 원문 열기`;
    return link;
  }

  function makeSourcePreview(job, item, number) {
    const figure = document.createElement("figure"); figure.className = "ph-source-preview";
    const asset = sourceAsset(job, item);
    if (asset?.media_type?.startsWith("image/")) {
      const image = document.createElement("img");
      image.src = `${API_ROOT}/jobs/${encodeURIComponent(job.id)}/assets/${encodeURIComponent(asset.id)}`;
      image.alt = `${number}번 원문 자르기 미리보기`;
      image.loading = "lazy";
      image.onerror = () => { if (image.parentNode === figure) figure.replaceChild(makeSourceLink(job, item), image); };
      figure.appendChild(image);
    } else {
      figure.appendChild(makeSourceLink(job, item));
    }
    const caption = document.createElement("figcaption"); caption.textContent = "PDF 원문";
    figure.appendChild(caption); return figure;
  }

  async function patchReviewItem(job, item, payload) {
    return jobFrom(await request(`/jobs/${encodeURIComponent(job.id)}/items/${encodeURIComponent(item.id)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    }));
  }

  function makeItemEditor(job, item) {
    const row = document.createElement("div"); row.className = "ph-review-item";
    const draftValue = item.draft || {};
    const number = item.question_number || item.source_number || item.ord;
    const header = document.createElement("div"); header.className = "ph-review-item-head";
    const choice = document.createElement("label"); choice.className = "ph-item-choice";
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = Boolean(item.selected);
    checkbox.disabled = reviewLocked(job.id);
    checkbox.setAttribute("aria-label", `${number}번 문항 선택`);
    const choiceText = document.createElement("span"); choiceText.textContent = `${number}번 · PDF ${item.source_page || "?"}쪽`;
    choice.append(checkbox, choiceText); header.appendChild(choice);
    const stateBadge = document.createElement("span"); stateBadge.className = "ph-item-status";
    stateBadge.textContent = item.confirmed ? "변환 확정" : (item.detection_status === "manual_required" ? "수동 입력 필요" : "검토 중");
    header.appendChild(stateBadge);
    const workspace = document.createElement("div"); workspace.className = "ph-review-workspace";
    const draft = document.createElement("div"); draft.className = "ph-draft-pane";
    const itemEditKey = editKey(job.id, item.id);
    const field = document.createElement("label"); field.className = "ph-palette-field";
    const fieldTitle = document.createElement("span"); fieldTitle.textContent = "HwpPalette용 조판 텍스트";
    const textarea = document.createElement("textarea"); textarea.className = "ph-palette-editor"; textarea.rows = 16; textarea.required = true; textarea.value = state.draftEdits.get(itemEditKey) ?? paletteText(item) ?? item.source_text ?? draftValue.source_text ?? ""; textarea.setAttribute("aria-label", `${number}번 HwpPalette용 조판 텍스트`);
    field.append(fieldTitle, textarea);
    const actions = document.createElement("div"); actions.className = "ph-editor-actions";
    const button = document.createElement("button"); button.type = "button"; button.className = "nz-tb"; button.textContent = "문항 내용 저장"; button.disabled = reviewLocked(job.id);
    const confirm = document.createElement("input"); confirm.type = "button"; confirm.className = "nz-tb blu"; confirm.value = item.confirmed ? "변환 확정됨" : "변환 확정"; confirm.disabled = reviewLocked(job.id) || Boolean(item.confirmed);
    textarea.oninput = () => { const persisted = paletteText(byId(job.id)?.items?.find((entry) => String(entry.id) === String(item.id)) || item); if (textarea.value === persisted) { state.draftEdits.delete(itemEditKey); row.classList.remove("is-dirty"); } else { state.draftEdits.set(itemEditKey, textarea.value); row.classList.add("is-dirty"); confirm.disabled = true; } syncTypesetControl(job.id); };
    button.onclick = async () => {
      if (reviewLocked(job.id)) return;
      if (!textarea.value.trim()) { showFailure(`${number}번 문항의 조판 텍스트를 입력하세요.`); return; }
      button.disabled = true; const sequence = beginMutation(job.id);
      try { const updated = await queueMutation(job.id, () => patchReviewItem(byId(job.id) || job, item, { palette_markdown: textarea.value.trim() })); state.draftEdits.delete(itemEditKey); row.classList.remove("is-dirty"); if (acceptMutation(updated, sequence)) { renderJobs(); renderSelected(updated); } }
      catch (error) { if (isLatestMutation(job.id, sequence) && isSelected(job.id)) showFailure(error.message); } finally { button.disabled = false; }
    };
    confirm.onclick = async () => { if (confirm.disabled) return; try { const updated = jobFrom(await request(`/jobs/${encodeURIComponent(job.id)}/items/${encodeURIComponent(item.id)}/confirm`, { method: "POST" })); if (replaceJob(updated)) { renderJobs(); renderSelected(updated); } } catch (error) { showFailure(error.message); } };
    checkbox.onchange = async () => {
      if (reviewLocked(job.id)) return; checkbox.disabled = true; const selected = checkbox.checked; const sequence = beginMutation(job.id);
      try { const updated = await queueMutation(job.id, () => patchReviewItem(byId(job.id) || job, item, { selected })); if (acceptMutation(updated, sequence)) { renderJobs(); renderSelected(updated); } }
      catch (error) { if (isSelected(job.id)) { renderCurrent(byId(job.id)); if (isLatestMutation(job.id, sequence)) showFailure(error.message); } } finally { checkbox.disabled = false; }
    };
    actions.append(button, confirm); draft.append(field, actions); workspace.append(makeSourcePreview(job, item, number), draft); row.append(header, workspace); return row;
  }

  function makeFailureDisclosure(job) {
    const failed = (job.items || []).filter((item) => ["failed", "manual_required", "conversion_failed"].includes(item.status));
    if (!failed.length) return null;
    const disclosure = document.createElement("details"); disclosure.className = "ph-failure-disclosure";
    const summary = document.createElement("summary");
    summary.textContent = `실패 문항 ${failed.length}개 자세히 보기`;
    const list = document.createElement("div"); list.className = "ph-failure-list";
    failed.forEach((item) => {
      const row = document.createElement("div"); row.className = "ph-failure-row";
      const copy = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = `${item.source_number || item.ord}번 · PDF ${item.source_page || "?"}쪽`;
      const message = document.createElement("p"); message.textContent = itemErrorMessage(item);
      const extracted = document.createElement("pre"); extracted.className = "ph-auto-extracted-text"; extracted.textContent = `자동 추출 원문\n${item.source_text || item.draft?.source_text || "(텍스트 추출 결과 없음)"}`;
      copy.append(title, message, extracted);
      const actions = document.createElement("div"); actions.className = "ph-failure-actions";
      actions.append(makeSourceLink(job, item));
      const manual = document.createElement("button"); manual.type = "button"; manual.className = "nz-tb"; manual.textContent = "수동 편집";
      manual.onclick = () => { row.replaceWith(makeManualRecovery(job, item)); };
      actions.appendChild(manual); row.append(copy, actions);
      list.appendChild(row);
    });
    disclosure.append(summary, list);
    return disclosure;
  }

  function makeManualRecovery(job, item) {
    const row = document.createElement("div"); row.className = "ph-failure-row ph-manual-recovery";
    const number = item.source_number || item.ord;
    const title = document.createElement("strong"); title.textContent = `${number}번 수동 복구`;
    const help = document.createElement("p"); help.textContent = "PDF 원문을 확인하고 전체 텍스트 또는 영역 순서를 입력하세요.";
    const source = document.createElement("textarea"); source.rows = 5; source.value = item.source_text || item.draft?.source_text || ""; source.setAttribute("aria-label", `${number}번 자동 추출 원문`);
    const save = document.createElement("button"); save.type = "button"; save.className = "nz-tb blu"; save.textContent = "수동 복구 저장";
    save.onclick = async () => { save.disabled = true; try { const updated = jobFrom(await request(`/jobs/${encodeURIComponent(job.id)}/items/${encodeURIComponent(item.id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source_text: source.value, passage: source.value, prompt: "", manual_blocks: [{ kind: "whole_source", text: source.value }], whole_source_text: true, palette_markdown: "" }) })); if (replaceJob(updated)) { renderJobs(); renderSelected(updated); } } catch (error) { showFailure(error.message); } finally { save.disabled = false; } };
    row.append(title, help, source, save, makeSourceLink(job, item)); return row;
  }

  function makeReviewToolbar(job) {
    const toolbar = document.createElement("div"); toolbar.className = "ph-review-toolbar";
    const count = document.createElement("span"); count.className = "ph-selection-count"; count.setAttribute("aria-live", "polite");
    count.textContent = `${selectedItems(job).length}개 문항 선택됨`;
    const actions = document.createElement("div"); actions.className = "ph-review-batch";
    const all = document.createElement("button"); all.type = "button"; all.className = "nz-tb"; all.textContent = "전체 선택";
    all.disabled = reviewLocked(job.id);
    all.onclick = () => EP.pdfHwpSelectAll(true);
    const none = document.createElement("button"); none.type = "button"; none.className = "nz-tb"; none.textContent = "선택 해제";
    none.disabled = reviewLocked(job.id);
    none.onclick = () => EP.pdfHwpSelectAll(false);
    actions.append(all, none); toolbar.append(count, actions); return toolbar;
  }

  function renderDetails(job) {
    const detail = EP.$("pdfHwpCurrent"); detail.replaceChildren();
    const stats = itemStats(job);
    const summary = document.createElement("p");
    summary.textContent = `작업 ${job.id} · 성공 ${stats.ready}개 · 실패 ${stats.failed}개 · 결과 ${outputCount(job)}개`;
    detail.appendChild(summary);
    const failures = makeFailureDisclosure(job);
    if (failures) detail.appendChild(failures);
    if (job.capabilities?.review_items) {
      const list = document.createElement("div"); list.className = "ph-review-list";
      job.items.filter((item) => !["failed", "manual_required", "conversion_failed"].includes(item.status)).forEach((item) => list.appendChild(makeItemEditor(job, item)));
      detail.append(makeReviewToolbar(job), list);
    }
  }

  function sourcePreviewPlaceholder(box, message) {
    box.replaceChildren();
    const text = document.createElement("p");
    text.textContent = message;
    box.appendChild(text);
  }

  function renderSourcePreviewContent(job, box) {
    if (!job?.source_path) { sourcePreviewPlaceholder(box, "작업을 선택하면 PDF 원문이 표시됩니다."); return; }
    if (["draft", "uploaded", "detecting"].includes(job.status)) { const link = makeSourceLink(job, { source_page: 1 }); link.textContent = "PDF 원문 열기"; box.replaceChildren(link); return; }
    const showFallback = () => {
      if (!box.isConnected || !box.contains(frame)) return;
      box.replaceChildren();
      const copy = document.createElement("p");
      copy.textContent = "PDF 미리보기를 불러오지 못했습니다. 원문 열기 링크로 파일을 확인하세요.";
      box.append(copy, makeSourceLink(job, { source_page: 1 }));
    };
    const frame = document.createElement("iframe");
    frame.title = "선택한 PDF 원문";
    frame.loading = "lazy";
    frame.addEventListener("error", showFallback, { once: true });
    frame.addEventListener("load", () => {
      try {
        const bodyText = frame.contentDocument?.body?.textContent || "";
        if (/error response|file not found|nothing matches the given uri|job not found|source pdf|conversion job|404|not found/i.test(bodyText)) showFallback();
      } catch (error) { /* PDF plug-ins may not expose a readable document. */ }
    }, { once: true });
    frame.src = `${API_ROOT}/jobs/${encodeURIComponent(job.id)}/source#page=1`;
    box.appendChild(frame);
  }

  function renderDocumentPreview(job) {
    const box = EP.$("pdfHwpDocumentPreview");
    const details = EP.$("pdfHwpSourceDetails");
    if (!box) return;
    const jobKey = job?.id == null ? "" : String(job.id);
    const wasOpen = Boolean(details?.open && details.dataset.jobId === jobKey && jobKey);
    if (details) {
      details.dataset.jobId = jobKey;
      details.ontoggle = () => {
        if (details.open) renderSourcePreviewContent(job, box);
        else sourcePreviewPlaceholder(box, "PDF 원문은 필요할 때 열 수 있습니다.");
      };
      details.open = wasOpen;
    }
    if (wasOpen) renderSourcePreviewContent(job, box);
    else sourcePreviewPlaceholder(box, job ? "PDF 원문은 필요할 때 열 수 있습니다." : "작업을 선택하면 PDF 원문을 열 수 있습니다.");
  }

  function readyHwp(job) {
    return (job?.outputs || []).find((entry) => entry.kind === "hwp" && entry.status === "ready");
  }
  function convertedFileName(job, output) {
    const raw = String(job?.source_filename || job?.name || "output");
    const stem = raw.replace(/\.[^.]+$/, "").trim() || "output";
    return `${stem}_converted${output?.kind === "pdf" ? ".pdf" : ".hwp"}`;
  }
  function readyDownloads() {
    return state.jobs.filter((job) => readyHwp(job)).slice().sort((left, right) => Number(left.id) - Number(right.id));
  }
  function syncOutputPicks(readyJobs) {
    const ids = new Set(readyJobs.map((job) => String(job.id)));
    [...state.outputPicks].forEach((id) => { if (!ids.has(id)) state.outputPicks.delete(id); });
    [...state.outputPickKnown].forEach((id) => { if (!ids.has(id)) state.outputPickKnown.delete(id); });
    readyJobs.forEach((job) => {
      const id = String(job.id);
      if (!state.outputPickKnown.has(id)) {
        state.outputPicks.add(id);
        state.outputPickKnown.add(id);
      }
    });
  }
  function selectedDownloads() {
    return readyDownloads().filter((job) => state.outputPicks.has(String(job.id)));
  }
  function updateOutputSelectionBar(box) {
    const selected = selectedDownloads();
    const count = EP.$("pdfHwpSelectedCount");
    const download = EP.$("pdfHwpDownloadSelected");
    if (count) count.textContent = selected.length ? `선택 ${selected.length}개` : "선택된 파일 없음";
    if (download) {
      download.disabled = selected.length === 0;
      download.textContent = selected.length > 1 ? `선택한 ${selected.length}개 받기` : "선택한 파일 받기";
    }
    box?.querySelectorAll(".ph-output-ready-item").forEach((row) => {
      row.classList.toggle("is-selected", state.outputPicks.has(row.dataset.jobId));
    });
  }
  function renderPrimaryOutput() {
    const box = EP.$("pdfHwpOutput");
    if (!box) return;
    const readyJobs = readyDownloads();
    syncOutputPicks(readyJobs);
    box.replaceChildren();
    box.classList.toggle("hidden", readyJobs.length === 0);
    if (!readyJobs.length) return;
    const head = document.createElement("div"); head.className = "ph-output-card-head";
    const title = document.createElement("strong"); title.textContent = readyJobs.length > 1 ? `변환 완료 파일 ${readyJobs.length}개` : "HWP 파일이 준비되었습니다";
    const help = document.createElement("span"); help.textContent = "받을 파일을 고른 뒤 다운로드하세요. 파일 이름은 원본 이름 뒤에 _converted를 붙입니다.";
    head.append(title, help);
    const list = document.createElement("ul"); list.className = "ph-output-ready-list"; list.setAttribute("aria-label", "변환 완료 파일");
    readyJobs.forEach((job) => {
      const output = readyHwp(job);
      const filename = convertedFileName(job, output);
      const row = document.createElement("li"); row.className = "ph-output-ready-item"; row.dataset.jobId = String(job.id);
      const choice = document.createElement("label"); choice.className = "ph-output-choice";
      const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = state.outputPicks.has(String(job.id));
      checkbox.setAttribute("aria-label", `${filename} 선택`);
      checkbox.onchange = () => {
        if (checkbox.checked) state.outputPicks.add(String(job.id));
        else state.outputPicks.delete(String(job.id));
        updateOutputSelectionBar(box);
      };
      const name = document.createElement("span"); name.className = "ph-output-filename"; name.textContent = filename;
      choice.append(checkbox, name);
      const link = makeOutputLink(job, output, "받기"); link.classList.add("blu");
      link.setAttribute("aria-label", `${filename} 받기`);
      row.append(choice, link); list.appendChild(row);
    });
    const actions = document.createElement("div"); actions.className = "ph-output-ready-actions";
    const count = document.createElement("span"); count.id = "pdfHwpSelectedCount";
    const selectAll = document.createElement("button"); selectAll.type = "button"; selectAll.className = "nz-tb"; selectAll.textContent = "전체 선택";
    selectAll.onclick = () => { readyJobs.forEach((job) => state.outputPicks.add(String(job.id))); box.querySelectorAll(".ph-output-choice input").forEach((input) => { input.checked = true; }); updateOutputSelectionBar(box); };
    const clear = document.createElement("button"); clear.type = "button"; clear.className = "nz-tb"; clear.textContent = "선택 해제";
    clear.onclick = () => { state.outputPicks.clear(); box.querySelectorAll(".ph-output-choice input").forEach((input) => { input.checked = false; }); updateOutputSelectionBar(box); };
    const download = document.createElement("button"); download.type = "button"; download.className = "nz-tb blu"; download.id = "pdfHwpDownloadSelected";
    download.onclick = () => EP.pdfHwpDownloadSelected();
    actions.append(count, selectAll, clear, download);
    box.append(head, list, actions);
    updateOutputSelectionBar(box);
  }

  function renderCurrent(job) {
    const box = EP.$("pdfHwpStatus"), detail = EP.$("pdfHwpCurrent"), retry = EP.$("pdfHwpRetry"), typeset = EP.$("pdfHwpTypeset");
    if (!job) {
      setTone(box, "idle");
      box.querySelector("strong").textContent = "파일을 선택해 주세요";
      box.querySelector("p").textContent = "선택 후 변환 시작 버튼을 누르면 문항 분석과 HWP 조판을 진행합니다.";
      detail.textContent = "";
      renderDocumentPreview(null);
      renderPrimaryOutput();
      renderProgress(null);
      retry.classList.add("hidden");
      typeset.classList.add("hidden");
      typeset.disabled = true;
      renderSteps("");
      return;
    }
    let [title, description, tone, step] = jobStatusInfo(job);
    if (state.autoRuns.has(String(job.id)) && ["review", "partial_failure", "failed"].includes(job.status)) {
      [title, description, tone, step] = ["HWP 파일 생성 중", "성공 문항은 편집 가능하게, 실패 문항은 자동 추출 원문으로 포함합니다.", "working", "convert"];
    }
    setTone(box, tone);
    box.querySelector("strong").textContent = title;
    box.querySelector("p").textContent = jobStatusMessage(job, description);
    renderDetails(job);
    renderDocumentPreview(job);
    renderPrimaryOutput();
    renderProgress(job);
    const busy = Boolean(state.autoStart) || state.autoRuns.has(String(job.id)) || state.operations.has(String(job.id));
    const canRetry = !busy && (Boolean(job.capabilities?.retry_failed) || state.recoveries.has(String(job.id)));
    retry.classList.toggle("hidden", !canRetry);
    retry.disabled = state.inFlight.has(actionKey("retry", job.id));
    const manualTypeset = ["review", "partial_failure"].includes(job.status) && !state.autoRuns.has(String(job.id)) && outputCount(job) === 0;
    typeset.classList.toggle("hidden", !manualTypeset);
    typeset.disabled = !manualTypeset || !job.capabilities?.review_items || !canTypeset(job) || state.mutationQueue.has(String(job.id)) || state.inFlight.has(actionKey("typeset", job.id));
    const stats = itemStats(job);
    typeset.textContent = stats.failed > 0 ? `성공한 ${stats.ready}개 문항으로 HWP 만들기` : "HWP 파일로 만들기";
    renderSteps(step);
  }

  function makeOutputLink(job, output, label) {
    const filename = convertedFileName(job, output);
    const link = document.createElement("a");
    link.className = "nz-tb";
    link.href = `${API_ROOT}/jobs/${encodeURIComponent(job.id)}/outputs/${encodeURIComponent(output.id)}`;
    link.textContent = label || (output.kind === "pdf" ? "확인용 PDF 받기" : "편집용 HWP 받기");
    link.setAttribute("download", filename);
    return link;
  }
  EP.pdfHwpDownloadSelected = function () {
    selectedDownloads().forEach((job, index) => {
      const output = readyHwp(job);
      if (!output) return;
      window.setTimeout(() => {
        const link = makeOutputLink(job, output, convertedFileName(job, output));
        document.body.appendChild(link);
        link.click();
        link.remove();
      }, index * 250);
    });
  };

  function renderJob(job) {
    const card = document.createElement("article");
    card.className = "ph-job-card";
    const title = document.createElement("div");
    title.className = "ph-job-title";
    title.textContent = job.name || job.source_filename || `변환 작업 ${job.id}`;
    const badge = document.createElement("span");
    const info = jobStatusInfo(job);
    badge.className = `ph-job-state ${info[2] === "error" ? "error" : info[2] === "success" ? "success" : ""}`;
    badge.textContent = info[0];
    const detail = document.createElement("div");
    detail.className = "ph-job-detail";
    const stats = itemStats(job);
    detail.textContent = stats.failed > 0
      ? `성공 ${stats.ready}개 · 실패 ${stats.failed}개 · 결과 ${outputCount(job)}개`
      : job.status === "typesetting" ? `HWP 생성 중 · ${job.generation_progress || 0}%`
        : `${itemCount(job)}개 문항 · ${job.created_at || "방금 생성"}`;
    const outputs = document.createElement("div");
    outputs.className = "ph-output-list";
    (job.outputs || []).forEach((output) => outputs.appendChild(makeOutputLink(job, output)));
    const inspect = document.createElement("button");
    inspect.type = "button"; inspect.className = "nz-tb"; inspect.textContent = "상태 보기";
    inspect.onclick = () => { state.currentId = job.id; renderCurrent(job); schedulePoll(job); };
    const cancel = ["detecting", "typesetting"].includes(job.status) ? document.createElement("button") : null;
    if (cancel) { cancel.type = "button"; cancel.className = "nz-tb"; cancel.textContent = "작업 취소"; cancel.onclick = async () => { cancel.disabled = true; try { const updated = jobFrom(await request(`/jobs/${encodeURIComponent(job.id)}/cancel`, { method: "POST" })); replaceJob(updated); renderJobs(); renderCurrent(updated); } catch (error) { showFailure(error.message); } finally { cancel.disabled = false; } }; }
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "nz-tb"; remove.setAttribute("role", "link"); remove.textContent = "기록 삭제"; remove.disabled = ["detecting", "typesetting"].includes(job.status); remove.title = remove.disabled ? "진행 중인 작업은 먼저 취소하세요" : "이 작업과 출력 파일을 삭제합니다";
    remove.onclick = async () => { if (remove.disabled || !window.confirm(`${job.name || "이 작업"}과 출력 파일 ${outputCount(job)}개를 삭제할까요?`)) return; remove.disabled = true; try { await request(`/jobs/${encodeURIComponent(job.id)}`, { method: "DELETE" }); state.jobs = state.jobs.filter((entry) => String(entry.id) !== String(job.id)); if (isSelected(job.id)) state.currentId = state.jobs[0]?.id || null; renderJobs(); renderCurrent(byId(state.currentId)); } catch (error) { showFailure(error.message); remove.disabled = false; } };
    outputs.prepend(inspect); if (cancel) outputs.appendChild(cancel); outputs.appendChild(remove);
    card.append(title, badge, detail, outputs);
    const failures = makeFailureDisclosure(job);
    if (failures) card.appendChild(failures);
    return card;
  }

  function renderJobs() {
    const box = EP.$("pdfHwpJobs");
    box.replaceChildren();
    if (!state.jobs.length) {
      const empty = document.createElement("div");
      empty.className = "ph-empty";
      const title = document.createElement("strong"), text = document.createElement("span");
      title.textContent = "아직 변환 작업이 없습니다";
      text.textContent = "위에서 PDF를 선택해 첫 작업을 시작하세요.";
      empty.append(title, text); box.appendChild(empty); return;
    }
    state.jobs.forEach((job) => box.appendChild(renderJob(job)));
  }

  function schedulePoll(job) {
    clearTimeout(state.pollTimer);
    if (!job || !ACTIVE_STATUSES.has(job.status)) return;
    state.pollTimer = setTimeout(() => refreshJob(job.id), 1500);
  }

  function replaceJob(job) {
    const current = byId(job.id);
    if (current && Number(job.revision || 0) < Number(current.revision || 0)) return false;
    state.jobs = [job, ...state.jobs.filter((entry) => String(entry.id) !== String(job.id))];
    bumpJobEpoch(job.id);
    return true;
  }

  function reconcileJobs(remoteJobs) {
    const localById = new Map(state.jobs.map((job) => [String(job.id), job]));
    const remoteIds = new Set(remoteJobs.map((job) => String(job.id)));
    const merged = remoteJobs.map((remote) => {
      const local = localById.get(String(remote.id));
      if (local && Number(local.revision || 0) >= Number(remote.revision || 0)) return local;
      bumpJobEpoch(remote.id); return remote;
    });
    state.jobs.filter((job) => !remoteIds.has(String(job.id))).forEach((job) => merged.push(job));
    state.jobs = merged;
  }

  async function refreshJob(id, recoverable = false) {
    const requestEpoch = state.jobEpoch.get(String(id)) || 0;
    try {
      const job = jobFrom(await request(`/jobs/${encodeURIComponent(id)}`));
      if (replaceJob(job)) { renderJobs(); renderSelected(job, true); }
      return job;
    } catch (error) {
      if ((state.jobEpoch.get(String(id)) || 0) === requestEpoch && isSelected(id)) showFailure(error.message, recoverable);
      return null;
    }
  }

  async function reconcileReviewState(id) {
    try {
      const job = jobFrom(await request(`/jobs/${encodeURIComponent(id)}`));
      if (!replaceJob(job)) return job;
      renderJobs();
      if (isSelected(id)) {
        renderDetails(job);
        syncTypesetControl(id);
      }
      return job;
    } catch (error) { return null; }
  }

  function showFailure(message, recoverable = false) {
    const box = EP.$("pdfHwpStatus");
    setTone(box, "error");
    box.querySelector("strong").textContent = "요청을 완료하지 못했습니다";
    box.querySelector("p").textContent = message;
    const retry = EP.$("pdfHwpRetry");
    retry.classList.toggle("hidden", !recoverable);
    retry.textContent = "실패 단계 다시 시도";
  }

  EP.pdfHwpRefresh = async function () {
    if (document.body.dataset.clearConversionHistoryOnRefresh === "true") {
      state.currentId = state.jobs[0]?.id || null;
      renderJobs();
      renderCurrent(byId(state.currentId));
      return;
    }
    const requestEpoch = state.dataEpoch;
    try {
      reconcileJobs(jobsFrom(await request("/jobs")));
      renderJobs();
      const current = byId(state.currentId) || state.jobs[0];
      if (current) { state.currentId = current.id; renderCurrent(current); schedulePoll(current); }
      else renderCurrent(null);
    } catch (error) { if (state.dataEpoch === requestEpoch) showFailure(error.message); }
  };

  EP.pdfHwpSelectAll = async function (selected) {
    const targetId = state.currentId;
    const current = byId(targetId);
    if (!current?.capabilities?.review_items) return;
    if (reviewLocked(targetId)) return;
    const key = actionKey("selection", targetId);
    if (state.inFlight.has(key)) return;
    state.inFlight.add(key);
    const sequence = beginMutation(targetId);
    try {
      const updated = await queueMutation(targetId, async () => {
        let latest = byId(targetId) || current;
        for (const item of latest.items.filter((entry) => entry.status !== "failed" && entry.selected !== selected)) {
          latest = await patchReviewItem(latest, item, { selected });
        }
        return latest;
      });
      if (acceptMutation(updated, sequence)) { renderJobs(); renderSelected(updated); }
    } catch (error) {
      if (isLatestMutation(targetId, sequence) && isSelected(targetId)) showFailure(error.message);
      await reconcileReviewState(targetId);
    } finally {
      state.inFlight.delete(key);
    }
  };

  EP.pdfHwpStart = async function (event = {}) {
    event.preventDefault?.();
    const files = [...(event.files || state.pendingFiles || EP.$("pdfHwpFile").files || [])];
    if (!files.length) { showFailure("변환할 PDF 파일을 선택하세요."); return; }
    if (state.autoStart) return state.autoStart;
    if (files.some((file) => /^image\//i.test(file.type) || /\.(png|jpe?g|webp)$/i.test(file.name || ""))) {
      const staleRuntimeMessage = "이미지 편집 변환 서버가 이전 버전입니다. PDF-HWP 앱을 종료한 뒤 다시 실행해 주세요.";
      try {
        const runtime = await request("/runtime");
        if (runtime.contract_version < 2 || runtime.raster_editable_ocr !== true) throw new Error(staleRuntimeMessage);
      } catch (error) {
        showFailure(staleRuntimeMessage);
        return;
      }
    }
    state.batchStartedAt = Date.now(); state.batchFinishedAt = null;
    startElapsedClock();
    setBusy(true);
    const run = (async () => {
      let lastJob = null;
      for (const file of files) {
        let operationId = null;
        try {
          let job = jobFrom(await request("/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: file.name, layout_style: "suneung" }) }));
          operationId = job.id; state.recoveries.add(String(job.id));
          state.startedAt.set(String(job.id), Date.now());
          state.finishedAt.delete(String(job.id));
          state.progressByJob.set(String(job.id), 0);
          state.currentId = job.id;
          if (replaceJob(job)) { renderJobs(); renderSelected(job); }
          job = await uploadAndDetect(job, file);
          if (replaceJob(job)) { renderJobs(); renderSelected(job, true); }
          if (["review", "partial_failure", "failed"].includes(job.status)) job = await startAutomaticTypeset(job);
          state.recoveries.delete(String(job.id));
          if (replaceJob(job)) { renderJobs(); renderSelected(job, true); }
          lastJob = job;
        } catch (error) {
          if (operationId) state.recoveries.add(String(operationId));
          if (!operationId || isSelected(operationId)) showFailure(error.message, Boolean(operationId));
        } finally {
          if (operationId) state.finishedAt.set(String(operationId), Date.now());
        }
      }
      return lastJob;
    })();
    state.autoStart = run;
    try { return await run; } finally {
      state.batchFinishedAt = Date.now(); stopElapsedClock();
      renderProgress(byId(state.currentId));
      if (state.recoveries.size === 0) {
        state.pendingFiles = [];
        const input = EP.$("pdfHwpFile"); if (input) input.value = "";
        const fileName = EP.$("pdfHwpFileName"); if (fileName) fileName.textContent = "선택된 파일 없음";
      }
      setBusy(false);
      if (state.autoStart === run) state.autoStart = null;
    }
  };

  EP.pdfHwpRetry = async function () {
    const targetId = state.currentId;
    if (!targetId) return;
    const key = actionKey("retry", targetId);
    if (state.inFlight.has(key)) return;
    state.inFlight.add(key);
    EP.$("pdfHwpRetry").disabled = true;
    try {
      const current = byId(targetId);
      let job;
      if (current?.status === "draft" || current?.status === "uploaded") {
        const file = EP.$("pdfHwpFile").files[0];
        if (!file && current.status === "draft") { showFailure("다시 업로드할 PDF 파일을 선택하세요.", true); return; }
        job = await uploadAndDetect(current, file);
      } else {
        job = jobFrom(await request(`/jobs/${encodeURIComponent(targetId)}/retry`, { method: "POST" }));
      }
      if (["review", "partial_failure", "failed"].includes(job.status)) job = await startAutomaticTypeset(job);
      state.recoveries.delete(String(targetId));
      if (replaceJob(job)) { renderJobs(); renderSelected(job, true); }
    } catch (error) {
      const target = byId(targetId);
      if (target?.status === "draft" || target?.status === "uploaded") state.recoveries.add(String(targetId));
      if (isSelected(targetId)) showFailure(error.message, true);
      if (target && target.status !== "draft" && target.status !== "uploaded") await refreshJob(targetId, true);
    } finally {
      state.inFlight.delete(key);
      if (isSelected(targetId)) EP.$("pdfHwpRetry").disabled = false;
    }
  };

  async function uploadAndDetect(current, file) {
    let job = current;
    if (job.status === "draft") {
      const data = new FormData(); data.append("file", file);
      job = jobFrom(await request(`/jobs/${encodeURIComponent(job.id)}/upload`, { method: "POST", body: data }));
      if (replaceJob(job)) { renderJobs(); renderSelected(job); }
    }
    if (job.status === "uploaded") {
      if (job.async_detection === true) {
        const operation = await request(`/jobs/${encodeURIComponent(job.id)}/detect/start`, { method: "POST" });
        state.operations.set(String(job.id), operation.operation_id);
        job = await waitForOperation(job.id, operation.operation_id, (progress) => {
          const live = { ...job, status: "detecting" };
          if (isSelected(job.id)) {
            const box = EP.$("pdfHwpStatus"); setTone(box, "working");
            box.querySelector("strong").textContent = "문항 검출 중";
            box.querySelector("p").textContent = `페이지와 문항 경계를 분석하고 있습니다. ${progress.progress || 0}%`;
            renderProgress(live, progress); renderSteps("detect");
          }
        });
        state.operations.delete(String(job.id));
      } else {
        job = jobFrom(await request(`/jobs/${encodeURIComponent(job.id)}/detect`, { method: "POST" }));
      }
    }
    return job;
  }

  async function startAutomaticTypeset(job) {
    const hasFallback = fallbackItems(job).length > 0;
    const rasterSource = /\.(png|jpe?g|webp)$/i.test(job?.source_filename || "");
    if (hasFallback && rasterSource) return job;
    if (!job || !["review", "partial_failure", "failed"].includes(job.status) || (!hasFallback && !canTypeset(job))) return job;
    const key = actionKey("typeset", job.id);
    if (state.inFlight.has(key)) return byId(job.id) || job;
    state.autoRuns.add(String(job.id));
    state.inFlight.add(key);
    if (isSelected(job.id)) { setReviewControlsDisabled(true); renderCurrent(job); }
    try {
      if (job.async_typeset === true) {
        const operation = await request(`/jobs/${encodeURIComponent(job.id)}/typeset/start`, { method: "POST" });
        state.operations.set(String(job.id), operation.operation_id);
        const completed = await waitForOperation(job.id, operation.operation_id, (progress) => {
          const live = { ...(byId(job.id) || job), status: "typesetting", generation_progress: progress.progress || 0 };
          if (isSelected(job.id)) {
            const box = EP.$("pdfHwpStatus"); setTone(box, "working");
            box.querySelector("strong").textContent = "HWP 조판 중";
            box.querySelector("p").textContent = `수능형 시험지로 조판하고 있습니다. ${progress.progress || 0}%`;
            renderProgress(live, progress); renderSteps("convert");
          }
        });
        state.operations.delete(String(job.id));
        return completed;
      }
      const completed = jobFrom(await request(`/jobs/${encodeURIComponent(job.id)}/typeset`, { method: "POST" }));
      return completed;
    } finally {
      state.inFlight.delete(key);
      state.autoRuns.delete(String(job.id));
      if (isSelected(job.id)) setReviewControlsDisabled(false);
    }
  }

  EP.pdfHwpTypeset = async function () {
    if (!state.currentId) return;
    const targetId = state.currentId;
    const key = actionKey("typeset", targetId);
    if (state.inFlight.has(key)) return;
    const pending = state.mutationQueue.get(String(targetId));
    if (!pending && !canTypeset(byId(targetId))) return;
    const asyncJob = byId(targetId);
    if (asyncJob?.async_typeset === true) {
      state.inFlight.add(key); setReviewControlsDisabled(true); EP.$("pdfHwpTypeset").disabled = true;
      try {
        const operation = await request(`/jobs/${encodeURIComponent(targetId)}/typeset/start`, { method: "POST" });
        state.operations.set(String(targetId), operation.operation_id);
        setTone(EP.$("pdfHwpStatus"), "working"); EP.$("pdfHwpStatus").querySelector("strong").textContent = "HWP 생성 대기 중"; EP.$("pdfHwpStatus").querySelector("p").textContent = "선택 문항 스냅샷을 저장했습니다. 생성 중에도 다른 문항을 검토할 수 있습니다.";
        pollOperation(targetId, operation.operation_id);
      } catch (error) { showFailure(error.message); setReviewControlsDisabled(false); state.inFlight.delete(key); syncTypesetControl(targetId); }
      return;
    }
    state.inFlight.add(key);
    EP.$("pdfHwpTypeset").disabled = true;
    setReviewControlsDisabled(true);
    try {
      let mutationFailed = false;
      if (pending) await pending.catch(() => { mutationFailed = true; });
      if (mutationFailed) return;
      const current = byId(targetId);
      if (!current?.capabilities?.review_items || !canTypeset(current)) return;
      const job = jobFrom(await request(`/jobs/${encodeURIComponent(targetId)}/typeset`, { method: "POST" }));
      if (replaceJob(job)) { renderJobs(); renderSelected(job, true); }
    } catch (error) {
      if (isSelected(targetId)) showFailure(error.message);
      await refreshJob(targetId);
    } finally {
      state.inFlight.delete(key);
      if (isSelected(targetId) && byId(targetId)?.capabilities?.review_items) setReviewControlsDisabled(false);
      syncTypesetControl(targetId);
    }
  };

  async function pollOperation(jobId, operationId) {
    try {
      const operation = await request(`/operations/${encodeURIComponent(operationId)}`);
      if (!isSelected(jobId)) return;
      if (operation.status === "completed") { state.operations.delete(String(jobId)); state.inFlight.delete(actionKey("typeset", jobId)); const job = await refreshJob(jobId); if (job) renderSelected(job, true); setReviewControlsDisabled(false); return; }
      if (operation.status === "failed" || operation.status === "cancelled") { state.operations.delete(String(jobId)); state.inFlight.delete(actionKey("typeset", jobId)); showFailure(operation.error?.message || "HWP 생성 작업이 실패했습니다.", true); setReviewControlsDisabled(false); return; }
      EP.$("pdfHwpStatus").querySelector("p").textContent = `선택 문항 HWP 생성 중 · ${operation.progress || 0}%`;
      setTimeout(() => pollOperation(jobId, operationId), 700);
    } catch (error) { if (isSelected(jobId)) showFailure(error.message, true); }
  }

  async function waitForOperation(jobId, operationId, onProgress) {
    for (;;) {
      const operation = await request(`/operations/${encodeURIComponent(operationId)}`);
      onProgress?.(operation);
      if (operation.status === "completed") return await refreshJob(jobId, true);
      if (operation.status === "failed" || operation.status === "cancelled") throw new Error(operation.error?.message || "작업이 실패했습니다.");
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }

  EP.pdfHwpInit = function () {
    if (!state.initialized) {
      state.initialized = true;
      const fileInput = EP.$("pdfHwpFile");
      const dropzone = EP.$("pdfHwpDropzone");
      const supportedSource = (file) => /\.(pdf|png|jpe?g|webp)$/i.test(file.name || "")
        || ["application/pdf", "image/png", "image/jpeg", "image/webp"].includes(file.type);
      const clipboardExtension = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp" };
      const updateFileName = (files) => {
        const selected = [...(files || [])];
        state.pendingFiles = selected;
        EP.$("pdfHwpFileName").textContent = selected.length > 1
          ? `${selected.length}개 파일 선택됨`
          : selected[0]?.name || "선택된 파일 없음";
        const start = EP.$("pdfHwpStart"); if (start) start.disabled = selected.length === 0 || Boolean(state.autoStart);
        if (selected.length && !state.currentId) {
          const status = EP.$("pdfHwpStatus"); setTone(status, "idle");
          status.querySelector("strong").textContent = "변환 준비 완료";
          status.querySelector("p").textContent = `${selected.length}개 파일이 선택되었습니다. 변환 시작 버튼을 눌러주세요.`;
        }
      };
      fileInput.addEventListener("change", (event) => {
        const files = [...(event.target.files || [])];
        if (!files.length) return;
        if (files.some((file) => !supportedSource(file))) { showFailure("PDF, PNG, JPEG, WebP 파일만 업로드할 수 있습니다."); return; }
        updateFileName(files);
      });
      if (dropzone) {
        let dragDepth = 0;
        dropzone.addEventListener("dragenter", (event) => { event.preventDefault(); dragDepth += 1; dropzone.classList.add("is-dragging"); });
        dropzone.addEventListener("dragover", (event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; });
        dropzone.addEventListener("dragleave", (event) => { event.preventDefault(); dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) dropzone.classList.remove("is-dragging"); });
        dropzone.addEventListener("drop", (event) => {
          event.preventDefault(); dragDepth = 0; dropzone.classList.remove("is-dragging");
          const files = [...(event.dataTransfer?.files || [])];
          if (!files.length) return;
          if (files.some((file) => !supportedSource(file))) { showFailure("PDF, PNG, JPEG, WebP 파일만 업로드할 수 있습니다."); return; }
          try {
            const transfer = new DataTransfer(); files.forEach((file) => transfer.items.add(file)); fileInput.files = transfer.files;
          } catch (error) { /* Browsers that protect input.files still keep the drop feedback visible. */ }
          updateFileName(files);
        });
      }
      document.addEventListener("paste", (event) => {
        const page = EP.$("tab-pdf-hwp");
        const target = event.target;
        const editing = target instanceof Element && target !== fileInput
          && Boolean(target.closest("input, textarea, select, [contenteditable='true']"));
        if (event.defaultPrevented || page?.hidden || editing || state.autoStart) return;
        const files = [...(event.clipboardData?.items || [])]
          .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
          .map((item) => item.getAsFile())
          .filter((file) => file && supportedSource(file))
          .map((file, index) => {
            if (/\.(png|jpe?g|webp)$/i.test(file.name || "")) return file;
            const extension = clipboardExtension[file.type];
            const suffix = index ? `-${index + 1}` : "";
            return new File([file], `clipboard-image${suffix}.${extension}`, { type: file.type, lastModified: file.lastModified });
          });
        if (!files.length) return;
        event.preventDefault();
        try {
          const transfer = new DataTransfer(); files.forEach((file) => transfer.items.add(file)); fileInput.files = transfer.files;
        } catch (error) { /* pendingFiles remains the upload source when input.files is protected. */ }
        updateFileName(files);
      });
      const splitter = EP.$("pdfHwpSplitter") || document.querySelector(".ph-splitter");
      const workbench = document.querySelector(".ph-workbench");
      if (splitter && workbench) {
        let dragging = false;
        splitter.addEventListener("pointerdown", (event) => { dragging = true; splitter.setPointerCapture(event.pointerId); });
        splitter.addEventListener("pointermove", (event) => { if (!dragging || window.matchMedia("(max-width: 720px)").matches) return; const bounds = workbench.getBoundingClientRect(); const sidebar = Math.max(240, Math.min(440, event.clientX - bounds.left)); workbench.style.gridTemplateColumns = `${sidebar}px 8px minmax(0, 1fr)`; });
        splitter.addEventListener("pointerup", () => { dragging = false; });
        splitter.addEventListener("keydown", (event) => { if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return; event.preventDefault(); const current = parseFloat(getComputedStyle(workbench).gridTemplateColumns.split(" ")[0]) || 300; const next = Math.max(240, Math.min(440, current + (event.key === "ArrowRight" ? 16 : -16))); workbench.style.gridTemplateColumns = `${next}px 8px minmax(0, 1fr)`; });
      }
    }
    EP.pdfHwpRefresh();
  };
})(window.EP);
