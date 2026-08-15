/* ===== PDF → HWP 전용 작업 화면 ===== */
(function (EP) {
  "use strict";

  const API_ROOT = "/api/pdf-hwp";
  const ACTIVE_STATUSES = new Set(["draft", "uploaded", "detecting", "typesetting"]);
  const STATUS = {
    draft: ["작업 준비", "PDF를 업로드할 준비가 되었습니다.", "working", "upload"],
    uploaded: ["PDF 업로드 완료", "문항을 찾기 시작합니다.", "working", "detect"],
    detecting: ["문항 검출 중", "페이지와 문항 경계를 분석하고 있습니다.", "working", "detect"],
    review: ["문항 검출 완료", "문항별 변환 내용을 검토하고 저장하세요.", "idle", "convert"],
    typesetting: ["HWP 변환 중", "원본 배치를 적용해 결과 파일을 만들고 있습니다.", "working", "convert"],
    partial_failure: ["일부 문항 변환 실패", "완료된 결과는 유지됩니다. 실패 단계만 다시 시도할 수 있습니다.", "error", "convert"],
    failed: ["변환 실패", "오류 내용을 확인한 뒤 같은 작업을 다시 시도하세요.", "error", "convert"],
    cancelled: ["변환 취소됨", "이 작업은 중단되었습니다.", "error", "convert"],
    completed: ["변환 완료", "결과 파일을 내려받아 편집할 수 있습니다.", "success", "complete"],
  };
  const state = { initialized: false, currentId: null, recoveries: new Set(), inFlight: new Set(), mutationSeq: new Map(), mutationQueue: new Map(), draftEdits: new Map(), jobEpoch: new Map(), dataEpoch: 0, jobs: [], pollTimer: null };

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
    document.querySelectorAll("#pdfHwpCurrent .ph-review-list input, #pdfHwpCurrent .ph-review-list textarea, #pdfHwpCurrent .ph-review-list button, #pdfHwpCurrent .ph-review-batch button")
      .forEach((control) => { control.disabled = disabled; });
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
  function errorMessage(job) { return job?.error?.message || ""; }
  function itemCount(job) { return Array.isArray(job?.items) ? job.items.length : Number(job?.item_count || 0); }
  function outputCount(job) { return Array.isArray(job?.outputs) ? job.outputs.length : 0; }
  function itemStats(job) {
    const items = Array.isArray(job?.items) ? job.items : [];
    return {
      ready: items.filter((item) => item.status === "ready").length,
      failed: items.filter((item) => item.status === "failed").length,
    };
  }
  function jobStatusInfo(job) {
    const stats = itemStats(job);
    if (stats.failed > 0 && stats.ready === 0) {
      return ["모든 문항 변환 실패", "변환 가능한 문항이 없습니다. 실패 상세를 확인하세요.", "error", "convert"];
    }
    if (stats.failed > 0 && stats.ready > 0) {
      return ["일부 문항 제외 후 변환 가능", `성공 ${stats.ready}개 · 실패 ${stats.failed}개. 성공한 문항으로 HWP를 만들 수 있습니다.`, "error", "convert"];
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
    const button = EP.$("pdfHwpStart");
    if (!button) return;
    button.disabled = active;
    button.setAttribute("aria-busy", String(active));
    button.textContent = active ? "작업 만드는 중…" : "변환 시작";
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
  function selectedItems(job) {
    return (job?.items || []).filter((item) => item.selected && item.status !== "failed");
  }
  function canTypeset(job) {
    const viable = selectedItems(job);
    return Boolean(job?.capabilities?.typeset_selected) && viable.length > 0 && viable.every((item) => item.status === "ready" && paletteText(item).trim() && !state.draftEdits.has(editKey(job.id, item.id)));
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
    const number = item.source_number || item.ord;
    const header = document.createElement("div"); header.className = "ph-review-item-head";
    const choice = document.createElement("label"); choice.className = "ph-item-choice";
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = Boolean(item.selected);
    checkbox.disabled = reviewLocked(job.id);
    checkbox.setAttribute("aria-label", `${number}번 문항 선택`);
    const choiceText = document.createElement("span"); choiceText.textContent = `${number}번 · PDF ${item.source_page}쪽`;
    choice.append(checkbox, choiceText); header.appendChild(choice);
    const workspace = document.createElement("div"); workspace.className = "ph-review-workspace";
    const draft = document.createElement("div"); draft.className = "ph-draft-pane";
    const draftLabel = document.createElement("label"); draftLabel.textContent = "자동 변환 초안";
    const textarea = document.createElement("textarea");
    textarea.className = "ph-palette-editor"; textarea.rows = 5; textarea.required = true;
    textarea.disabled = reviewLocked(job.id);
    const itemEditKey = editKey(job.id, item.id);
    textarea.value = state.draftEdits.get(itemEditKey) ?? paletteText(item) ?? item.draft?.source_text ?? "";
    textarea.setAttribute("aria-label", `${number}번 HWP 변환 내용`);
    textarea.oninput = () => {
      const persisted = paletteText(byId(job.id)?.items?.find((entry) => String(entry.id) === String(item.id)) || item);
      if (textarea.value === persisted) state.draftEdits.delete(itemEditKey);
      else state.draftEdits.set(itemEditKey, textarea.value);
      syncTypesetControl(job.id);
    };
    const button = document.createElement("button");
    button.type = "button"; button.className = "nz-tb"; button.textContent = "문항 내용 저장"; button.disabled = reviewLocked(job.id);
    button.onclick = async () => {
      if (reviewLocked(job.id)) return;
      if (!textarea.value.trim()) { showFailure(`${number}번 문항의 변환 내용을 입력하세요.`); return; }
      button.disabled = true;
      const markdown = textarea.value;
      const sequence = beginMutation(job.id);
      try {
        const updated = await queueMutation(job.id, () => patchReviewItem(byId(job.id) || job, item, { palette_markdown: markdown }));
        if (state.draftEdits.get(itemEditKey) === markdown) state.draftEdits.delete(itemEditKey);
        if (acceptMutation(updated, sequence)) { renderJobs(); renderSelected(updated); }
      } catch (error) { if (isLatestMutation(job.id, sequence) && isSelected(job.id)) showFailure(error.message); }
      finally { button.disabled = false; }
    };
    checkbox.onchange = async () => {
      if (reviewLocked(job.id)) return;
      checkbox.disabled = true;
      const selected = checkbox.checked;
      const sequence = beginMutation(job.id);
      try {
        const updated = await queueMutation(job.id, () => patchReviewItem(byId(job.id) || job, item, { selected }));
        if (acceptMutation(updated, sequence)) { renderJobs(); renderSelected(updated); }
      } catch (error) {
        if (isSelected(job.id)) {
          renderCurrent(byId(job.id));
          if (isLatestMutation(job.id, sequence)) showFailure(error.message);
        }
      }
      finally { checkbox.disabled = false; }
    };
    draft.append(draftLabel, textarea, button);
    workspace.append(makeSourcePreview(job, item, number), draft);
    row.append(header, workspace); return row;
  }

  function makeFailureDisclosure(job) {
    const failed = (job.items || []).filter((item) => item.status === "failed");
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
      copy.append(title, message);
      row.append(copy, makeSourceLink(job, item));
      list.appendChild(row);
    });
    disclosure.append(summary, list);
    return disclosure;
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
      job.items.filter((item) => item.status !== "failed").forEach((item) => list.appendChild(makeItemEditor(job, item)));
      detail.append(makeReviewToolbar(job), list);
    }
  }

  function renderCurrent(job) {
    const box = EP.$("pdfHwpStatus"), detail = EP.$("pdfHwpCurrent"), retry = EP.$("pdfHwpRetry"), typeset = EP.$("pdfHwpTypeset");
    if (!job) {
      setTone(box, "idle");
      box.querySelector("strong").textContent = "변환할 PDF를 선택하세요";
      box.querySelector("p").innerHTML = '파일을 올리기 전에는 <span class="ph-keep">서버에 작업을 만들지 않습니다.</span>';
      detail.textContent = "";
      retry.classList.add("hidden");
      typeset.classList.add("hidden");
      renderSteps("");
      return;
    }
    const [title, description, tone, step] = jobStatusInfo(job);
    setTone(box, tone);
    box.querySelector("strong").textContent = title;
    box.querySelector("p").textContent = jobStatusMessage(job, description);
    renderDetails(job);
    const canRetry = Boolean(job.capabilities?.retry_failed) || state.recoveries.has(String(job.id));
    retry.classList.toggle("hidden", !canRetry);
    retry.disabled = state.inFlight.has(actionKey("retry", job.id));
    typeset.classList.toggle("hidden", !job.capabilities?.review_items);
    typeset.disabled = Boolean(job.capabilities?.review_items) && (!canTypeset(job) || state.mutationQueue.has(String(job.id)) || state.inFlight.has(actionKey("typeset", job.id)));
    const stats = itemStats(job);
    typeset.textContent = stats.failed > 0 ? `성공한 ${stats.ready}개 문항으로 HWP 만들기` : "검토한 문항으로 HWP 만들기";
    renderSteps(step);
  }

  function makeOutputLink(job, output) {
    const link = document.createElement("a");
    link.className = "nz-tb";
    link.href = `${API_ROOT}/jobs/${encodeURIComponent(job.id)}/outputs/${encodeURIComponent(output.id)}`;
    link.textContent = output.kind === "pdf" ? "확인용 PDF 받기" : "편집용 HWP 받기";
    link.setAttribute("download", "");
    return link;
  }

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
      : `${itemCount(job)}개 문항 · ${job.created_at || "방금 생성"}`;
    const outputs = document.createElement("div");
    outputs.className = "ph-output-list";
    (job.outputs || []).forEach((output) => outputs.appendChild(makeOutputLink(job, output)));
    const inspect = document.createElement("button");
    inspect.type = "button"; inspect.className = "nz-tb"; inspect.textContent = "상태 보기";
    inspect.onclick = () => { state.currentId = job.id; renderCurrent(job); schedulePoll(job); };
    outputs.prepend(inspect);
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

  EP.pdfHwpStart = async function (event) {
    event.preventDefault();
    const file = EP.$("pdfHwpFile").files[0];
    if (!file) { showFailure("변환할 PDF 파일을 선택하세요."); return; }
    setBusy(true);
    const selectedAtStart = state.currentId;
    let operationId = null;
    try {
      const name = EP.$("pdfHwpName").value.trim();
      const layout = document.querySelector('input[name="layoutStyle"]:checked').value;
      let job = jobFrom(await request("/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name || file.name, layout_style: layout }) }));
      operationId = job.id; state.recoveries.add(String(job.id));
      if (String(state.currentId) === String(selectedAtStart)) state.currentId = job.id;
      if (replaceJob(job)) { renderJobs(); renderSelected(job); }
      job = await uploadAndDetect(job, file);
      state.recoveries.delete(String(job.id));
      if (replaceJob(job)) { renderJobs(); renderSelected(job, true); }
    } catch (error) {
      if (operationId) state.recoveries.add(String(operationId));
      if (operationId && isSelected(operationId)) showFailure(error.message, true);
      else if (!operationId && isSelected(selectedAtStart)) showFailure(error.message);
    }
    finally { setBusy(false); }
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
      job = jobFrom(await request(`/jobs/${encodeURIComponent(job.id)}/detect`, { method: "POST" }));
    }
    return job;
  }

  EP.pdfHwpTypeset = async function () {
    if (!state.currentId) return;
    const targetId = state.currentId;
    const key = actionKey("typeset", targetId);
    if (state.inFlight.has(key)) return;
    const pending = state.mutationQueue.get(String(targetId));
    if (!pending && !canTypeset(byId(targetId))) return;
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

  EP.pdfHwpInit = function () {
    if (!state.initialized) {
      state.initialized = true;
      EP.$("pdfHwpFile").addEventListener("change", (event) => {
        EP.$("pdfHwpFileName").textContent = event.target.files[0]?.name || "선택된 파일 없음";
      });
    }
    EP.pdfHwpRefresh();
  };
})(window.EP);
