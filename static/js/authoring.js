/* ===== 대화형 문항 제작 — 세션·대화·선택 반영·되돌리기 ===== */
(function (EP) {
  "use strict";
  const $ = EP.$, esc = EP.esc, api = EP.api, post = EP.post, patch = EP.patch, del = EP.del;
  const S = EP.S;
  const EXPECTED_AUTHORING_PROTOCOL = "authoring-v8-default-complete-question";
  const AUTHORING_PANEL_KEYS = {
    evidence: "ep_authoring_evidence_width",
    right: "ep_authoring_right_width",
  };
  const AUTHORING_PANEL_DEFAULTS = { evidence: 37, right: 44 };

  const STATUS = {
    text_drafting: "텍스트 작성 중", text_confirmed: "텍스트 확정",
    figure_drafting: "그림 제작 중", figure_confirmed: "그림 확정",
    reviewing: "문항 검수", saved: "저장 완료",
  };
  const FIGURE_STATUS = { none: "없음", draft: "생성됨", awaiting_image: "이미지 가져오기 대기", editing: "5E 편집 중", confirmed: "확정" };
  let session = null;
  let messages = [];
  let syncing = false;
  let loginPoll = null;
  let connectionState = null;
  let backendOutdated = false;
  let activeRequest = null;
  let lastPrompt = "";

  function savedNotice(text) {
    const el = $("auSaved");
    if (!el) return;
    el.textContent = text || "";
    const expected = el.textContent;
    setTimeout(() => { if (el.textContent === expected) el.textContent = ""; }, 1500);
  }

  function fiveeLaunchTarget(url) {
    const token = `exampool-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    const target = new URL(url, window.location.href);
    target.searchParams.set("activate", token);
    return { url: target.toString(), token };
  }

  function clampPanelWidth(value, min, max) {
    return Math.max(min, Math.min(max, Math.round(value * 10) / 10));
  }

  function applyAuthoringPanelWidths() {
    const layout = document.querySelector("#tab-question .nz-qlayout");
    const grid = document.querySelector("#tab-question .au-grid");
    if (!layout || !grid) return;
    const evidence = clampPanelWidth(
      Number(localStorage.getItem(AUTHORING_PANEL_KEYS.evidence)) || AUTHORING_PANEL_DEFAULTS.evidence, 20, 60
    );
    const right = clampPanelWidth(
      Number(localStorage.getItem(AUTHORING_PANEL_KEYS.right)) || AUTHORING_PANEL_DEFAULTS.right, 18, 45
    );
    layout.style.setProperty("--au-evidence-width", `${evidence}%`);
    grid.style.setProperty("--au-right-width", `${right}%`);
  }

  function initAuthoringPanelResizer(handle, kind) {
    if (!handle || handle.dataset.resizerReady) return;
    handle.dataset.resizerReady = "1";
    const isEvidence = kind === "evidence";
    const key = AUTHORING_PANEL_KEYS[kind];
    const min = isEvidence ? 20 : 18;
    const max = isEvidence ? 60 : 45;
    const target = () => document.querySelector(isEvidence
      ? "#tab-question .nz-qlayout" : "#tab-question .au-grid");
    const update = (clientX) => {
      const element = target();
      if (!element) return;
      const box = element.getBoundingClientRect();
      if (!box.width) return;
      const raw = isEvidence
        ? ((clientX - box.left) / box.width) * 100
        : ((box.right - clientX) / box.width) * 100;
      const value = clampPanelWidth(raw, min, max);
      localStorage.setItem(key, value);
      element.style.setProperty(isEvidence ? "--au-evidence-width" : "--au-right-width", `${value}%`);
      handle.dataset.widthLabel = `${isEvidence ? "근거" : "우측"} ${value}%`;
      handle.setAttribute("aria-valuenow", String(value));
    };
    handle.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      handle.setPointerCapture(event.pointerId);
      handle.classList.add("dragging");
      document.body.classList.add("au-resizing-panels");
      update(event.clientX);
      const move = (moveEvent) => update(moveEvent.clientX);
      const stop = () => {
        handle.classList.remove("dragging");
        document.body.classList.remove("au-resizing-panels");
        handle.removeEventListener("pointermove", move);
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", stop, { once: true });
      handle.addEventListener("pointercancel", stop, { once: true });
    });
    handle.addEventListener("keydown", (event) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      const current = Number(localStorage.getItem(key)) || AUTHORING_PANEL_DEFAULTS[kind];
      const direction = event.key === "ArrowRight" ? 1 : -1;
      const next = isEvidence ? current + direction : current - direction;
      const element = target();
      const value = clampPanelWidth(next, min, max);
      localStorage.setItem(key, value);
      element.style.setProperty(isEvidence ? "--au-evidence-width" : "--au-right-width", `${value}%`);
      handle.dataset.widthLabel = `${isEvidence ? "근거" : "우측"} ${value}%`;
    });
  }

  EP.resetAuthoringPanelWidths = function () {
    Object.values(AUTHORING_PANEL_KEYS).forEach((key) => localStorage.removeItem(key));
    applyAuthoringPanelWidths();
    savedNotice("패널 폭을 기본값으로 돌렸습니다");
  };

  function setFigureProgress(active, percent, label) {
    const box = $("auFigureProgress");
    if (!box) return;
    box.classList.toggle("hidden", !active);
    $("auFigureProgressBar").style.width = `${Math.max(0, Math.min(100, percent || 0))}%`;
    $("auFigureProgressText").textContent = label || "그림 생성 준비 중";
    $("auFigureProgressPercent").textContent = `${Math.round(percent || 0)}%`;
  }

  function answerFromChoices() {
    const i = S.choices.findIndex((c) => c.is_answer);
    return i >= 0 ? "①②③④⑤"[i] : "";
  }

  EP.authoringDraftFromForm = function () {
    return {
      title: $("qtitle").value, qtype: $("qtype").value,
      is_negative: $("isNeg").checked, passage: $("qpassage").value,
      material: $("qmaterial").value, ask: $("qask").value,
      bogi_items: $("qtype").value === "합답형" ? S.bogi : [],
      choices: $("qtype").value === "서술형" ? [] : S.choices,
      answer: $("qtype").value === "서술형" ? ($("qModelAnswer").value || "") : answerFromChoices(),
      explanation: $("qexplanation").value,
      default_points: parseFloat($("qpoints").value) || 3,
      difficulty: $("qdiff").value, standard_code: EP.stdValue() || null,
      intent: $("qintent").value, behavior: $("qbehavior").value,
      origin: $("qorigin").value, origin_note: $("qoriginNote").value,
      image_choices: $("imgChoices").checked, question_status: $("qstatus").value,
      review_note: JSON.stringify(S.checkState || {}),
      figure_plan: (session && session.draft && session.draft.figure_plan) || null,
    };
  };

  function applyAnswer(answer) {
    if ($("qtype").value === "서술형") {
      $("qModelAnswer").value = answer || "";
      return;
    }
    const raw = String(answer == null ? "" : answer).trim();
    let idx = "①②③④⑤".indexOf(raw);
    if (idx < 0 && /^[1-5]$/.test(raw)) idx = Number(raw) - 1;
    S.choices.forEach((c, i) => { c.is_answer = i === idx; });
  }

  EP.authoringDraftToForm = function (d) {
    if (!d) return;
    syncing = true;
    $("qtitle").value = d.title || "";
    $("qtype").value = d.qtype || "정답형";
    $("isNeg").checked = !!d.is_negative;
    $("qpassage").value = d.passage || "";
    $("qmaterial").value = d.material || "";
    $("qask").value = d.ask || "";
    $("qintent").value = d.intent || "";
    $("qexplanation").value = d.explanation || "";
    $("qpoints").value = d.default_points || 3;
    $("qdiff").value = d.difficulty || "중";
    $("qbehavior").value = d.behavior || "";
    $("qorigin").value = d.origin || "";
    $("qoriginNote").value = d.origin_note || "";
    $("qstatus").value = d.question_status || "초안";
    $("imgChoices").checked = !!d.image_choices;
    EP.setStdValue(d.standard_code || "");
    S.bogi = Array.isArray(d.bogi_items) ? d.bogi_items : [];
    S.choices = Array.isArray(d.choices) ? d.choices.map((c, i) => ({
      ord: c.ord || i + 1, text: c.text || "", proposition_id: c.proposition_id || null,
      variant_id: c.variant_id || null, combo: c.combo || null,
      custom_evidence: c.custom_evidence || "", is_answer: !!c.is_answer,
    })) : [];
    applyAnswer(d.answer);
    EP.onTypeChange(); EP.renderBogi(); EP.renderChoices();
    syncing = false;
    if (EP.scheduleQuestionPreview) EP.scheduleQuestionPreview();
  };

  function proposalValue(p) {
    if (p.field === "bogi_items" && Array.isArray(p.value)) {
      return p.value.map((b, i) => `${b.label || "ㄱㄴㄷㄹㅁ"[i] || i + 1}. ${b.text || ""}`).join("\n");
    }
    if (p.field === "choices" && Array.isArray(p.value)) {
      return p.value.map((c, i) => `${"①②③④⑤"[i] || i + 1} ${c.text || ""}`).join("\n");
    }
    if (p.field === "figure_plan" && p.value && typeof p.value === "object") {
      const panels = Array.isArray(p.value.panels) ? p.value.panels : [];
      const count = panels.length ? panels.reduce((n, panel) => n + ((panel.objects || []).length), 0)
        : (Array.isArray(p.value.objects) ? p.value.objects.length : 0);
      const options = p.value.options || {};
      return [p.value.summary || "그림 설계안",
        `${panels.length || 1}개 패널 · ${options.provider === "raster_image" ? "이미지 방식" : `5E 의미 객체 ${count}개`}`,
        options.include_text ? "글자·수치 포함" : "글자·수치 없음",
        p.value.blocked_reason ? `필요한 전용 부품: ${p.value.blocked_reason}` : ""].filter(Boolean).join("\n");
    }
    return typeof p.value === "string" ? p.value : JSON.stringify(p.value, null, 2);
  }

  function messageHtml(m) {
    const proposals = (m.proposals || []).map((p) => `
      <div class="au-proposal">
        <div class="au-proposal-head"><b>${esc(p.label || p.field)}</b>
          <span class="au-proposal-actions">
            <button class="nz-tb mini" onclick="EP.authoringFeedback('${m.id}','${esc(p.id)}')">수정 요청</button>
            <button class="nz-tb mini blu" onclick="EP.authoringApply(${m.id},'${esc(p.id)}')">${p.field === "figure_plan" ? "1단계 · 설계안 반영" : "반영"}</button>
          </span></div>
        <div class="au-proposal-value">${esc(proposalValue(p))}</div>
        <div class="au-feedback hidden" id="auFeedback-${m.id}-${esc(p.id)}">
          <textarea rows="2" placeholder="이 제안에서 바꿀 점만 적으세요."></textarea>
          <button class="nz-tb mini blu" onclick="EP.authoringSendFeedback('${m.id}','${esc(p.id)}')">이 항목만 다시 제안</button>
        </div>
      </div>`).join("");
    return `<div class="au-msg ${m.role}">
      <div class="who">${m.role === "user" ? "나" : "ChatGPT"}</div>
      <div class="bubble">${esc(m.content || "")}</div>
      ${proposals ? `<div class="au-proposals">${proposals}</div>` : ""}
    </div>`;
  }

  function renderMessages() {
    const box = $("auMessages");
    if (!box) return;
    box.innerHTML = messages.length ? messages.map(messageHtml).join("")
      : '<div class="au-empty">문항 생성·수정 요청을 입력하세요. 답변은 제안으로만 표시되며 자동 반영되지 않습니다.</div>';
    box.scrollTop = box.scrollHeight;
  }

  function pendingFigureProposal() {
    if (session && session.draft && session.draft.figure_plan) return null;
    for (let mi = messages.length - 1; mi >= 0; mi -= 1) {
      const proposals = messages[mi].proposals || [];
      for (let pi = proposals.length - 1; pi >= 0; pi -= 1) {
        if (proposals[pi].field === "figure_plan") {
          return { messageId: messages[mi].id, proposal: proposals[pi] };
        }
      }
    }
    return null;
  }

  function renderSession() {
    if (!session) return;
    const status = session.status || "text_drafting";
    $("auStatus").textContent = STATUS[status] || status;
    $("auStatus").classList.toggle("confirmed", status !== "text_drafting");
    $("auConfirm").disabled = status === "text_confirmed";
    $("auUnconfirm").disabled = status === "text_drafting";
    const flags = session.review_flags || [];
    const warning = $("auWarning");
    if (flags.length) {
      const labels = { answer: "정답", explanation: "해설", figure: "그림" };
      warning.textContent = `텍스트 확정 후 내용이 바뀌었습니다. ${flags.map((x) => labels[x] || x).join("·")}을 다시 검토하세요.`;
      warning.classList.remove("hidden");
    } else warning.classList.add("hidden");
    const fs = (session.figure || {}).status || "none";
    const pendingPlan = pendingFigureProposal();
    const materialName = (session.figure || {}).material_name || "";
    const materialImage = !!(session.figure || {}).material_image_path;
    $("auFigureStatus").textContent = pendingPlan && fs === "none"
      ? "설계안 반영 필요" : (FIGURE_STATUS[fs] || fs);
    $("auFigurePreview").classList.toggle("active", fs !== "none" || materialImage);
    $("auFigurePreview").querySelector("b").textContent = fs === "none"
      ? (pendingPlan ? "그림 설계안 반영 대기" : (materialImage ? `문항 자료: ${materialName}` : "연결된 그림 없음"))
      : `그림 상태: ${FIGURE_STATUS[fs] || fs}`;
    const figureAssets = (session.figure || {}).assets || [];
    const renderedAssets = figureAssets.filter((asset) => asset.rendered_image_path);
    const hasImage = !!(session.figure || {}).rendered_image_path || materialImage || renderedAssets.length > 0;
    const multiAssets = renderedAssets.length > 1;
    $("auFigureImage").classList.toggle("hidden", !hasImage || multiAssets);
    if (hasImage && !multiAssets) $("auFigureImage").src = `/api/authoring/sessions/${session.id}/figure/image?t=${Date.now()}`;
    const assetBox = $("auFigureAssets");
    assetBox.classList.toggle("hidden", !multiAssets);
    assetBox.innerHTML = multiAssets ? renderedAssets.map((asset, index) =>
      `<figure><img src="/api/authoring/sessions/${session.id}/figure/assets/${asset.id}/image?t=${Date.now()}" alt="그림 ${index + 1}" ${asset.fivee_project_path ? `onclick="EP.authoringEditAsset(${asset.id})" title="클릭하여 5E에서 편집"` : ""}><figcaption>${index + 1}</figcaption>${asset.fivee_project_path ? `<div><button class="nz-tb mini" onclick="EP.authoringEditAsset(${asset.id})">5E 편집</button><button class="nz-tb mini blu" onclick="EP.authoringSyncAsset(${asset.id})">수정본 가져오기</button></div>` : ""}</figure>`
    ).join("") : "";
    $("auFigurePreview").querySelector(".au-figure-icon").classList.toggle("hidden", hasImage);
    const projectPath = (session.figure || {}).fivee_project_path || "";
    $("auFigurePath").textContent = projectPath ? `프로젝트: ${projectPath}` : "";
    $("auFigurePath").classList.toggle("hidden", !projectPath);
    const plan = session.draft && session.draft.figure_plan;
    const figureOptions = (session.figure || {}).options || {
      provider: "fivee_assets", include_text: false, composition: "auto",
    };
    $("auFigureProvider").value = figureOptions.provider;
    $("auFigureText").value = figureOptions.include_text ? "on" : "off";
    $("auFigureComposition").value = figureOptions.composition;
    $("auFigureOptionSummary").textContent = [
      figureOptions.provider === "fivee_assets" ? "5E 자산" : "이미지",
      figureOptions.include_text ? "글자·수치 포함" : "글자·수치 없음",
      figureOptions.composition === "auto" ? "문항에 따라 자동 분리"
        : (figureOptions.composition === "separate" ? "장면별 개별 생성" : "한 도판에 구성"),
    ].join(" · ");
    const references = (session.figure || {}).references || [];
    const referenceBox = $("auFigureReferences");
    if (referenceBox) {
      referenceBox.innerHTML = references.map((reference) => `
        <figure><img src="/api/authoring/sessions/${session.id}/figure/references/${reference.id}/image?t=${Date.now()}" alt="참고 이미지">
          <figcaption title="${esc(reference.source_label || reference.filename || "참고 자료")}">${esc(reference.source_label || reference.filename || "참고 자료")}</figcaption>
          <select class="nz-sel mini" onchange="EP.authoringReferenceUsage(${reference.id}, this.value)" title="참고 자료 사용 용도">
            <option value="both" ${reference.usage === "both" ? "selected" : ""}>내용+그림</option>
            <option value="content" ${reference.usage === "content" ? "selected" : ""}>내용</option>
            <option value="image" ${reference.usage === "image" ? "selected" : ""}>그림</option>
          </select>
          <button type="button" class="nz-tb mini" onclick="EP.authoringDeleteReference(${reference.id})">삭제</button>
        </figure>`).join("");
      referenceBox.classList.toggle("hidden", !references.length);
      $("auFigureReferenceCount").textContent = references.length ? `${references.length}/6개 첨부` : "최대 6개";
    }
    const hasProject = !!projectPath || fs !== "none";
    const fiveeMode = figureOptions.provider === "fivee_assets";
    $("auFigureCreate").disabled = false;
    $("auFigureCreate").classList.toggle("hidden", hasProject && fiveeMode);
    $("auFigureImport").classList.toggle("hidden", fiveeMode || !hasProject);
    $("auFigureCopyPrompt").classList.toggle("hidden", fiveeMode || !figureAssets.some((asset) => asset.prompt));
    const pendingImport = figureAssets.find((asset) => !asset.rendered_image_path);
    $("auFigureImport").textContent = pendingImport && figureAssets.length > 1
      ? `${pendingImport.ord}번 그림 가져오기` : "생성 이미지 가져오기";
    $("auFigureEdit").classList.toggle("hidden", !hasProject || !(session.figure || {}).fivee_project_path);
    $("auFigureSync").classList.toggle("hidden", !hasProject || !(session.figure || {}).fivee_project_path);
    $("auFigureRevert").classList.toggle("hidden", !(session.figure || {}).previous_image_path);
    $("auFigureConfirm").classList.toggle("hidden", !hasProject);
    $("auFigureCreate").textContent = !fiveeMode
      ? (hasImage ? "AI 이미지 다시 생성" : "AI 이미지 바로 생성")
      : (plan ? "그림 생성"
        : (pendingPlan ? "1단계 · 이 설계안 반영" : "1단계 · 그림 설계 요청"));
    $("auFigureCreate").classList.toggle("attention", !!pendingPlan);
    $("auFigureHelp").textContent = !fiveeMode
      ? "버튼을 누르면 로그인된 ChatGPT 계정으로 이미지를 생성해 바로 미리보기에 넣습니다."
      : (plan
        ? "설계안 반영 완료. 그림 생성 버튼을 누르면 바로 미리보기를 만듭니다."
        : (pendingPlan
          ? "ChatGPT 설계안이 준비되었습니다. 반영한 뒤 그림을 바로 생성할 수 있습니다."
          : "먼저 ChatGPT에 현재 문항의 그림 설계안을 요청합니다."));
    $("auFigureEdit").disabled = false;
    $("auFigureConfirm").disabled = false;
    const stepPlan = $("auFigureStepPlan");
    const stepEdit = $("auFigureStepEdit");
    const stepConfirm = $("auFigureStepConfirm");
    stepPlan.className = plan ? "done" : "active";
    stepEdit.className = hasProject ? "done" : (plan ? "active" : "");
    stepConfirm.className = fs === "confirmed" ? "done" : (hasProject ? "active" : "");
  }

  EP.authoringFigureOptionsChanged = async function () {
    if (!session) await EP.authoringInit();
    const options = {
      provider: $("auFigureProvider").value,
      include_text: $("auFigureText").value === "on",
      composition: $("auFigureComposition").value,
    };
    session = await patch(`/api/authoring/sessions/${session.id}/figure/options`, options);
    renderSession();
    savedNotice("그림 생성 옵션 저장됨");
  };

  EP.authoringImportImage = async function (file) {
    if (!file || !session) return;
    if (file.size > 20 * 1024 * 1024) { alert("이미지는 20MB 이하여야 합니다."); return; }
    const assets = (session.figure || {}).assets || [];
    const target = assets.find((asset) => !asset.rendered_image_path) || assets[0] || { panel_id: "main" };
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
    savedNotice("이미지 가져오는 중…");
    session = await post(`/api/authoring/sessions/${session.id}/figure/import-image`, {
      panel_id: target.panel_id, filename: file.name, data_url: dataUrl,
    });
    $("auFigureImportFile").value = "";
    renderSession();
    savedNotice("이미지 가져오기 완료");
  };

  EP.authoringAddReferences = async function (files) {
    if (!session) await EP.authoringInit();
    const images = [...(files || [])].filter((file) => /^image\/(png|jpeg|webp)$/.test(file.type));
    if (!images.length) return;
    const existing = ((session.figure || {}).references || []).length;
    if (existing + images.length > 6) return alert("참고 이미지는 문항당 최대 6개까지 넣을 수 있습니다.");
    for (const file of images) {
      if (file.size > 20 * 1024 * 1024) return alert("참고 이미지는 파일당 20MB 이하여야 합니다.");
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      session = await post(`/api/authoring/sessions/${session.id}/figure/references`, {
        filename: file.name, data_url: dataUrl, source_label: file.name, usage: "both",
      });
      renderSession();
    }
    $("auFigureReferenceFile").value = "";
    savedNotice(`참고 이미지 ${images.length}개를 추가했습니다`);
  };

  EP.authoringAddReferenceData = async function (reference) {
    if (!session) await EP.authoringInit();
    const existing = ((session.figure || {}).references || []).length;
    if (existing >= 6) throw new Error("참고 자료는 문항별 최대 6개까지 연결할 수 있습니다.");
    session = await post(`/api/authoring/sessions/${session.id}/figure/references`, reference);
    renderSession();
    savedNotice("선택 영역을 참고 자료로 연결했습니다");
    return session;
  };

  EP.authoringReferenceUsage = async function (referenceId, usage) {
    session = await patch(
      `/api/authoring/sessions/${session.id}/figure/references/${referenceId}`, { usage }
    );
    renderSession();
    savedNotice(usage === "content" ? "내용 참고로 설정했습니다"
      : usage === "image" ? "그림 참고로 설정했습니다" : "내용과 그림 참고로 설정했습니다");
  };

  EP.authoringDeleteReference = async function (referenceId) {
    await del(`/api/authoring/sessions/${session.id}/figure/references/${referenceId}`);
    const data = await api(`/api/authoring/sessions/${session.id}`);
    session = data.session;
    renderSession();
    savedNotice("참고 이미지를 제거했습니다");
  };

  EP.authoringCopyImagePrompt = async function () {
    const prompts = ((session.figure || {}).assets || []).filter((asset) => asset.prompt)
      .map((asset) => `[그림 ${asset.ord}]\n${asset.prompt}`).join("\n\n");
    if (!prompts) { alert("복사할 이미지 생성 프롬프트가 없습니다."); return; }
    await navigator.clipboard.writeText(prompts);
    savedNotice("이미지 생성 프롬프트를 복사했습니다");
  };

  EP.authoringEditAsset = async function (assetId) {
    const win = window.open("about:blank", `exampool-fivee-${assetId}`);
    try {
      session = await post(`/api/authoring/sessions/${session.id}/figure/assets/${assetId}/edit`, {});
      const url = (session.figure || {}).launch_url;
      if (!url) throw new Error("5E 실행 주소를 받지 못했습니다.");
      const target = fiveeLaunchTarget(url);
      if (win) win.location.href = target.url;
      session = await post(`/api/authoring/sessions/${session.id}/figure/assets/${assetId}/activate?activation_token=${encodeURIComponent(target.token)}`, {});
      renderSession(); savedNotice("선택한 패널을 5E에 열었습니다");
    } catch (e) {
      if (win) win.close();
      alert(e.message || "5E 패널을 열지 못했습니다.");
    }
  };

  EP.authoringSyncAsset = async function (assetId) {
    savedNotice("선택한 5E 패널 가져오는 중…");
    session = await post(`/api/authoring/sessions/${session.id}/figure/assets/${assetId}/sync`, {});
    renderSession(); savedNotice("선택한 패널 반영 완료");
  };

  EP.authoringOpenFigureEditor = async function (event) {
    if (event && event.target && event.target.closest("button")) return;
    if (!session) await EP.authoringInit();
    const figure = (session && session.figure) || {};
    const editable = (figure.assets || []).find((asset) => asset.fivee_project_path);
    if (editable) return EP.authoringEditAsset(editable.id);
    if (figure.fivee_project_path) return EP.authoringFigure("edit");
    if (figure.rendered_image_path || figure.material_image_path) {
      alert("현재 그림에는 5E 편집 프로젝트가 없습니다. '5E 자산' 방식으로 다시 생성하면 그림을 클릭해 편집할 수 있습니다.");
      return;
    }
    alert("먼저 그림을 생성하세요. 생성된 그림은 이 영역을 클릭해 5E에서 편집할 수 있습니다.");
  };

  function usageText(c) {
    const parts = [];
    const limits = c.rate_limits && c.rate_limits.rateLimits;
    if (limits && limits.primary && limits.primary.usedPercent != null) {
      parts.push(`한도 ${Math.round(limits.primary.usedPercent)}% 사용`);
    }
    const summary = c.usage && c.usage.summary;
    if (summary && summary.lifetimeTokens != null) {
      parts.push(`누적 ${Number(summary.lifetimeTokens).toLocaleString()} 토큰`);
    }
    return parts.length ? ` · ${parts.join(" · ")}` : "";
  }

  function modelById(id) {
    return ((connectionState && connectionState.models) || []).find((m) => m.id === id);
  }

  function renderModelSettings() {
    if (!session) return;
    const models = (connectionState && connectionState.models) || [];
    const modelSelect = $("auModel");
    const selectedModel = session.effective_model || session.model || "";
    modelSelect.innerHTML = models.length
      ? models.map((m) => `<option value="${esc(m.id)}">${esc(m.display_name || m.id)}</option>`).join("")
      : `<option value="${esc(selectedModel)}">${esc(selectedModel || "기본 모델")}</option>`;
    if (selectedModel) modelSelect.value = selectedModel;
    $("auMode").value = session.authoring_mode || "quick";
    const model = modelById(modelSelect.value);
    const efforts = (model && model.supported_reasoning_efforts) || ["low", "medium", "high"];
    const selectedEffort = session.effective_reasoning_effort || session.reasoning_effort
      || (model && model.default_reasoning_effort) || "medium";
    $("auEffort").innerHTML = efforts.map((effort) =>
      `<option value="${esc(effort)}">${esc(effort)}</option>`).join("");
    if (efforts.includes(selectedEffort)) $("auEffort").value = selectedEffort;
    const disabled = session.provider === "mock";
    modelSelect.disabled = disabled;
    $("auEffort").disabled = disabled;
  }

  async function loadConnection() {
    try {
      const provider = session && session.provider === "mock" ? "mock" : "codex_local";
      const c = await api(`/api/authoring/connection?provider=${provider}`);
      connectionState = c;
      const isMock = provider === "mock";
      backendOutdated = !isMock && c.authoring_protocol !== EXPECTED_AUTHORING_PROTOCOL;
      $("auConnection").textContent = backendOutdated ? "서버 재시작 필요"
        : (isMock ? "로컬 시연" : (c.connected ? "ChatGPT 연결됨"
          : (c.service_available ? "로그인 필요" : "서비스 오류")));
      $("auConnection").classList.toggle("on", !!c.connected && !backendOutdated);
      $("auConnection").classList.toggle("error", backendOutdated);
      $("auAccount").classList.toggle("outdated", backendOutdated);
      $("auAccountText").textContent = backendOutdated
        ? "백엔드가 이전 버전으로 실행 중입니다. ExamPool 실행 창을 종료하고 run.bat으로 다시 시작하세요."
        : (c.connected
          ? `${c.account || "ChatGPT 계정"} · ${c.plan || "플랜 미확인"} · ${(session && session.effective_model) || c.model || "기본 모델"}${usageText(c)}`
          : (c.message || "ChatGPT 로그인이 필요합니다."));
      $("auLogin").classList.toggle("hidden", isMock || !!c.connected || !c.service_available);
      $("auDemo").classList.toggle("hidden", isMock);
      $("auCodex").classList.toggle("hidden", !isMock);
      renderModelSettings();
      return c;
    } catch (e) {
      backendOutdated = false;
      $("auConnection").textContent = "서비스 오류";
      $("auAccountText").textContent = e.message;
      $("auLogin").classList.add("hidden");
      connectionState = null;
      renderModelSettings();
      return null;
    }
  }

  EP.authoringRefreshConnection = async function () {
    const provider = session && session.provider === "mock" ? "mock" : "codex_local";
    $("auAccountText").textContent = provider === "mock" ? "로컬 시연 상태를 확인하고 있습니다." : "Codex 로그인 상태를 다시 불러오고 있습니다.";
    try {
      await post(`/api/authoring/connection/refresh?provider=${provider}`, {});
    } catch (e) {
      $("auAccountText").textContent = "연결 새로고침 오류: " + e.message;
    }
    return loadConnection();
  };

  EP.authoringLayoutChanged = function (value) {
    localStorage.setItem("ep_authoring_layout_style", value === "suneung" ? "suneung" : "school");
    if (EP.scheduleQuestionPreview) EP.scheduleQuestionPreview(100);
  };

  EP.authoringChangeMode = async function (mode) {
    if (!session) return;
    session = await patch(`/api/authoring/sessions/${session.id}/settings`, { authoring_mode: mode });
    renderModelSettings();
    savedNotice("작업 모드 저장됨");
  };

  EP.authoringChangeModel = async function () {
    if (!session) return;
    const model = $("auModel").value;
    const known = modelById(model);
    const previousEffort = $("auEffort").value;
    if (known) {
      const efforts = known.supported_reasoning_efforts || [];
      $("auEffort").innerHTML = efforts.map((effort) =>
        `<option value="${esc(effort)}">${esc(effort)}</option>`).join("");
      $("auEffort").value = efforts.includes(previousEffort)
        ? previousEffort : (known.default_reasoning_effort || efforts[0] || "medium");
    }
    session = await patch(`/api/authoring/sessions/${session.id}/settings`, {
      model, reasoning_effort: $("auEffort").value,
    });
    renderModelSettings();
    savedNotice("모델 설정 저장됨");
  };

  EP.authoringUseProvider = async function (provider) {
    if (!session) await EP.authoringOpen(S.editingQid, false);
    session = await post(`/api/authoring/sessions/${session.id}/provider`, { provider });
    await loadConnection();
    renderSession();
    savedNotice(provider === "mock" ? "로컬 시연 모드" : "ChatGPT 모드");
  };

  EP.authoringLogin = async function () {
    const popup = window.open("about:blank", "exampool-codex-login");
    $("auLogin").disabled = true;
    try {
      const result = await post("/api/authoring/login", {});
      if (result.alreadySignedIn) {
        if (popup) popup.close();
        await loadConnection();
        savedNotice("기존 Codex 로그인을 연결했습니다");
        return;
      }
      if (!result.authUrl) throw new Error("로그인 주소를 받지 못했습니다.");
      if (popup) popup.location.href = result.authUrl;
      else window.open(result.authUrl, "_blank", "noopener");
      $("auAccountText").textContent = "브라우저에서 ChatGPT 로그인을 완료하세요.";
      clearInterval(loginPoll);
      loginPoll = setInterval(async () => {
        const state = await loadConnection();
        if (state && state.connected) {
          clearInterval(loginPoll); loginPoll = null;
        }
      }, 2000);
      setTimeout(() => { if (loginPoll) { clearInterval(loginPoll); loginPoll = null; } }, 120000);
    } catch (e) {
      if (popup) popup.close();
      $("auAccountText").textContent = "로그인 시작 오류: " + e.message;
    } finally { $("auLogin").disabled = false; }
  };

  EP.authoringOpen = async function (questionId, force) {
    let data = null;
    const savedId = !questionId && !force ? localStorage.getItem("ep_authoring_session") : null;
    if (savedId) {
      try { data = await api(`/api/authoring/sessions/${savedId}`); } catch (e) { data = null; }
    }
    if (!data) data = await post("/api/authoring/sessions", {
      question_id: questionId || null, provider: "codex_local",
    });
    session = data.session; messages = data.messages || [];
    S.authoringSessionId = session.id;
    localStorage.setItem("ep_authoring_session", session.id);
    EP.authoringDraftToForm(session.draft);
    renderMessages(); renderSession(); await loadConnection();
    return session;
  };

  EP.authoringInit = async function () {
    await loadConnection();
    if (!session) await EP.authoringOpen(S.editingQid, false);
  };

  EP.authoringNew = async function () {
    localStorage.removeItem("ep_authoring_session");
    session = null; messages = [];
    return EP.authoringOpen(null, true);
  };

  EP.authoringDiscard = async function () {
    if (!session) return;
    const existing = !!session.question_id;
    const warning = existing
      ? "현재 작성 내용을 폐기할까요? 문항 Pool에 저장된 원본 문항은 그대로 유지됩니다."
      : "현재 작성 중인 초안과 대화를 폐기할까요? 폐기 기록은 복구를 위해 내부에 보존됩니다.";
    if (!confirm(warning)) return;
    try {
      await post(`/api/authoring/sessions/${session.id}/discard`, {});
      localStorage.removeItem("ep_authoring_session");
      session = null; messages = []; S.authoringSessionId = null;
      await EP.authoringOpen(null, true);
      savedNotice("작성 내용을 폐기하고 새 문항을 열었습니다");
    } catch (e) {
      alert(e.message || "작성 내용을 폐기하지 못했습니다.");
    }
  };

  EP.authoringSync = async function (quiet) {
    if (!session || syncing) return;
    session = await patch(`/api/authoring/sessions/${session.id}/draft`, { draft: EP.authoringDraftFromForm() });
    renderSession();
    if (!quiet) {
      savedNotice("초안 저장됨");
    }
  };
  const syncSoon = EP.debounce(() => EP.authoringSync(true).catch(() => {}), 500);

  EP.authoringSend = async function () {
    if (!session) await EP.authoringInit();
    if (backendOutdated) {
      alert("백엔드가 이전 버전으로 실행 중입니다. ExamPool 실행 창을 종료하고 run.bat으로 다시 시작하세요.");
      return;
    }
    const input = $("auPrompt");
    const content = input.value.trim();
    if (!content || activeRequest) return;
    lastPrompt = content;
    await EP.authoringSync(true);
    messages.push({ id: `local-${Date.now()}`, role: "user", content, proposals: [] });
    input.value = ""; renderMessages();
    $("auSend").disabled = true;
    $("auCancel").classList.remove("hidden");
    $("auRetry").classList.add("hidden");
    const box = $("auMessages");
    const typing = document.createElement("div");
    typing.className = "au-msg assistant streaming";
    typing.innerHTML = '<div class="who">ChatGPT <span class="au-stream-state">요청 전송 중</span></div><div class="bubble"><span class="au-typing-dots"><i></i><i></i><i></i></span></div><div class="au-live-proposals"></div>';
    box.appendChild(typing); box.scrollTop = box.scrollHeight;
    const bubble = typing.querySelector(".bubble");
    const streamState = typing.querySelector(".au-stream-state");
    const liveProposalBox = typing.querySelector(".au-live-proposals");
    let textQueue = "", queueRunning = false;
    const queueWaiters = [];
    function resolveQueueWaiters() {
      if (textQueue || queueRunning) return;
      while (queueWaiters.length) queueWaiters.shift()();
    }
    function pumpTextQueue() {
      if (queueRunning) return;
      queueRunning = true;
      bubble.querySelector(".au-typing-dots")?.remove();
      const tick = () => {
        if (!textQueue) {
          queueRunning = false;
          resolveQueueWaiters();
          return;
        }
        const take = textQueue.length > 240 ? 5 : (textQueue.length > 80 ? 3 : 1);
        const part = textQueue.slice(0, take);
        textQueue = textQueue.slice(take);
        bubble.textContent += part;
        box.scrollTop = box.scrollHeight;
        const pause = /[.!?。！？\n]$/.test(part) ? 34 : 13;
        setTimeout(tick, pause);
      };
      tick();
    }
    function enqueueText(value) {
      textQueue += value || "";
      pumpTextQueue();
    }
    function waitForTextQueue() {
      if (!textQueue && !queueRunning) return Promise.resolve();
      return new Promise((resolve) => queueWaiters.push(resolve));
    }
    activeRequest = new AbortController();
    try {
      const res = await fetch(`/api/authoring/sessions/${session.id}/messages`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content }),
        signal: activeRequest.signal,
      });
      if (!res.ok) {
        const rawError = await res.text();
        try { throw new Error(JSON.parse(rawError).detail || rawError); }
        catch (parsed) { if (parsed instanceof SyntaxError) throw new Error(rawError.slice(0, 240)); throw parsed; }
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "", finalMessage = null;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split("\n\n"); buffer = blocks.pop();
        for (const block of blocks) {
          const event = (block.match(/^event: (.+)$/m) || [])[1];
          const raw = (block.match(/^data: (.+)$/m) || [])[1];
          if (!raw) continue;
          const data = JSON.parse(raw);
          if (event === "status") streamState.textContent = data.label || "답변 작성 중";
          if (event === "heartbeat" && !bubble.textContent.trim()) streamState.textContent = "답변을 준비하고 있습니다";
          if (event === "chunk") {
            streamState.textContent = "답변 작성 중";
            enqueueText(data.delta);
          }
          if (event === "proposal" && data.proposal) {
            const proposal = data.proposal;
            const card = document.createElement("div");
            card.className = "au-proposal live";
            card.innerHTML = `<div class="au-proposal-head"><b>${esc(proposal.label || proposal.field)}</b><span class="au-ready-badge">준비됨</span></div><div class="au-proposal-value">${esc(proposalValue(proposal))}</div>`;
            liveProposalBox.appendChild(card);
            streamState.textContent = `${liveProposalBox.children.length}개 제안 준비됨 · 다음 항목 작성 중`;
          }
          if (event === "done") finalMessage = data.message;
          if (event === "error") throw new Error(data.message || "ChatGPT 응답 오류");
          box.scrollTop = box.scrollHeight;
        }
      }
      await waitForTextQueue();
      typing.remove();
      if (finalMessage) messages.push(finalMessage);
      renderMessages();
    } catch (e) {
      const cancelled = e.name === "AbortError";
      textQueue = "";
      bubble.textContent = cancelled
        ? "응답 생성을 중단했습니다. 같은 요청을 다시 시도할 수 있습니다."
        : "응답 오류: " + e.message;
      $("auRetry").classList.remove("hidden");
    } finally {
      activeRequest = null;
      $("auSend").disabled = false;
      $("auCancel").classList.add("hidden");
    }
  };

  EP.authoringCancel = async function () {
    if (!activeRequest || !session) return;
    try { await post(`/api/authoring/sessions/${session.id}/cancel`, {}); }
    catch (e) { /* Abort locally even when the turn already completed remotely. */ }
    activeRequest.abort();
  };

  EP.authoringRetry = async function () {
    if (!lastPrompt || activeRequest) return;
    $("auPrompt").value = lastPrompt;
    $("auRetry").classList.add("hidden");
    await EP.authoringSend();
  };

  EP.authoringApply = async function (messageId, proposalId) {
    session = await post(`/api/authoring/sessions/${session.id}/apply`, {
      message_id: messageId, proposal_id: proposalId,
    });
    EP.authoringDraftToForm(session.draft); renderSession();
    savedNotice("제안 반영됨");
  };

  EP.authoringFeedback = function (messageId, proposalId) {
    const box = $(`auFeedback-${messageId}-${proposalId}`);
    if (!box) return;
    box.classList.toggle("hidden");
    if (!box.classList.contains("hidden")) box.querySelector("textarea").focus();
  };

  EP.authoringSendFeedback = async function (messageId, proposalId) {
    const message = messages.find((m) => String(m.id) === String(messageId));
    const proposal = message && (message.proposals || []).find((p) => p.id === proposalId);
    const box = $(`auFeedback-${messageId}-${proposalId}`);
    const feedback = box && box.querySelector("textarea").value.trim();
    if (!proposal || !feedback) return;
    $("auPrompt").value = `[${proposal.label || proposal.field}] 항목만 다시 제안해 주세요.\n\n현재 제안:\n${proposalValue(proposal)}\n\n사용자 피드백:\n${feedback}`;
    await EP.authoringSend();
  };

  EP.authoringUndo = async function () {
    if (!session) return;
    try {
      session = await post(`/api/authoring/sessions/${session.id}/undo`, {});
      EP.authoringDraftToForm(session.draft); renderSession();
      savedNotice("반영 취소됨");
    } catch (e) { alert("되돌릴 반영 내역이 없습니다."); }
  };

  EP.authoringConfirm = async function () {
    await EP.authoringSync(true);
    session = await post(`/api/authoring/sessions/${session.id}/confirm-text`, {});
    renderSession(); savedNotice("텍스트 확정됨");
  };

  EP.authoringUnconfirm = async function () {
    if (!session) return;
    session = await post(`/api/authoring/sessions/${session.id}/unconfirm-text`, {});
    renderSession(); savedNotice("확정 해제됨");
  };

  EP.authoringFigure = async function (action) {
    if (!session) await EP.authoringInit();
    if (backendOutdated) {
      alert("그림 기능 업데이트를 적용하려면 ExamPool 실행 창을 종료하고 run.bat으로 다시 시작하세요.");
      return;
    }
    const figureStatus = (session.figure || {}).status || "none";
    if (action === "edit" && figureStatus === "none") {
      // 비활성 버튼처럼 조용히 무시하지 않고 현재 필요한 단계로 보낸다.
      return EP.authoringFigure("create");
    }
    if (action === "confirm" && figureStatus === "none") {
      alert("먼저 5E 초안을 생성하세요.");
      return;
    }
    const selectedFigureMode = ((session.figure || {}).options || {}).provider || "fivee_assets";
    if (action === "create" && selectedFigureMode !== "raster_image"
      && !(session.draft && session.draft.figure_plan)) {
      const pendingPlan = pendingFigureProposal();
      if (pendingPlan) {
        await EP.authoringApply(pendingPlan.messageId, pendingPlan.proposal.id);
        savedNotice("그림 설계안 반영됨 · 이제 그림을 생성하세요");
        return;
      }
      const opts = (session.figure || {}).options || {};
      const method = opts.provider === "raster_image" ? "이미지로 새로 그리는 방식" : "5E 의미 자산을 이용하는 방식";
      const textRule = opts.include_text ? "필요한 글자와 수치를 최소한으로 포함" : "글자·숫자·기호를 넣지 않음";
      const composition = opts.composition === "auto" ? "문항 상황을 분석해 필요한 경우에만 장면별 별도 그림으로"
        : (opts.composition === "separate" ? "각 장면을 각각 별도 그림으로" : "모든 장면을 하나의 도판에");
      $("auPrompt").value = `현재 문항에 필요한 그림을 평가원식 흑백 도판으로 설계해 주세요. ${method}, ${textRule}, ${composition} 구성해 주세요.`;
      await EP.authoringSend();
      return;
    }
    if (action === "create" && session.status === "text_drafting") {
      if (!confirm("현재 문항 텍스트를 확정하고 이 버전으로 그림을 만들까요? 이후 텍스트를 바꾸면 정답·해설·그림 재검토 경고가 표시됩니다.")) return;
      await EP.authoringSync(true);
      session = await post(`/api/authoring/sessions/${session.id}/confirm-text`, {});
      renderSession();
    }
    // 생성은 백그라운드 5E 렌더러에서 끝낸다. 편집할 때만 별도 5E 창을 연다.
    const fiveeWindow = action === "edit" ? window.open("about:blank", "exampool-fivee") : null;
    const actionButtons = {
      create: $("auFigureCreate"), edit: $("auFigureEdit"), sync: $("auFigureSync"),
      revert: $("auFigureRevert"), confirm: $("auFigureConfirm"),
    };
    const actionButton = actionButtons[action];
    const originalLabel = actionButton ? actionButton.textContent : "";
    if (actionButton) {
      actionButton.disabled = true;
      actionButton.textContent = action === "create"
        ? (selectedFigureMode === "raster_image" ? "AI 이미지 생성 중…" : "그림 생성 중…")
        : (action === "sync" ? "5E 수정본 가져오는 중…" : "처리 중…");
    }
    savedNotice(action === "create"
      ? (selectedFigureMode === "raster_image"
        ? "ChatGPT가 이미지를 생성하고 있습니다… 보통 1~3분 걸립니다."
        : "5E 도판 생성·미리보기 렌더링 중…")
      : "그림 작업 처리 중…");
    if (action === "create") setFigureProgress(true, 3, "그림 생성 요청을 준비하는 중");
    try {
      if (action === "create") {
        const response = await fetch(`/api/authoring/sessions/${session.id}/figure/create-stream`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
        });
        if (!response.ok) throw new Error(await response.text());
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "", completed = null;
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const blocks = buffer.split("\n\n"); buffer = blocks.pop();
          for (const block of blocks) {
            const event = (block.match(/^event: (.+)$/m) || [])[1];
            const raw = (block.match(/^data: (.+)$/m) || [])[1];
            if (!raw) continue;
            const data = JSON.parse(raw);
            if (event === "progress") setFigureProgress(true, data.percent, data.label);
            if (event === "done") completed = data.session;
            if (event === "error") throw new Error(data.message || "그림 생성 오류");
          }
        }
        if (!completed) throw new Error("그림 생성 완료 응답을 받지 못했습니다.");
        session = completed;
        setFigureProgress(true, 100, "그림 생성과 연결이 완료되었습니다");
      } else {
        session = await post(`/api/authoring/sessions/${session.id}/figure/${action}`, {});
      }
      renderSession();
      const figure = session.figure || {};
      if (action === "edit" && figure.launch_url) {
        const target = fiveeLaunchTarget(figure.launch_url);
        if (fiveeWindow) fiveeWindow.location.href = target.url;
        else window.open(target.url, "_blank", "noopener");
        // 5E 창이 열린 뒤 MCP가 이 문항 전용 페이지를 선택하거나 새로 만든다.
        session = await post(`/api/authoring/sessions/${session.id}/figure/activate?activation_token=${encodeURIComponent(target.token)}`, {});
        renderSession();
        const path = figure.fivee_project_path || "";
        if (navigator.clipboard && path) navigator.clipboard.writeText(path).catch(() => {});
        savedNotice("5E 편집 화면에 그림을 불러왔습니다");
      }
      if (action === "create") {
        savedNotice("그림 생성 완료 · 미리보기에 표시했습니다");
      }
      if (action === "sync") savedNotice("5E 편집 내용 반영 완료");
      if (action === "revert") savedNotice("직전 그림으로 되돌렸습니다");
      if (action === "confirm") {
        EP.authoringDraftToForm(session.draft);
        savedNotice("그림 PNG 연결 완료");
      }
    } catch (e) {
      if (fiveeWindow) fiveeWindow.close();
      alert(e.message || "그림 작업을 시작할 수 없습니다.");
    } finally {
      if (action === "create") setTimeout(() => setFigureProgress(false, 0, ""), 1200);
      if (actionButton) {
        actionButton.disabled = false;
        actionButton.textContent = originalLabel;
      }
    }
  };

  EP.authoringBind = async function (questionId) {
    if (!session) return;
    session = await post(`/api/authoring/sessions/${session.id}/bind`, { question_id: questionId });
  };

  document.addEventListener("DOMContentLoaded", () => {
    applyAuthoringPanelWidths();
    initAuthoringPanelResizer($("authoringEvidenceDrag"), "evidence");
    initAuthoringPanelResizer($("authoringRightDrag"), "right");
    const layout = $("auLayoutStyle");
    if (layout) layout.value = localStorage.getItem("ep_authoring_layout_style") || "school";
    const current = document.querySelector(".au-current");
    if (current) {
      current.addEventListener("input", () => { if (!syncing) syncSoon(); });
      current.addEventListener("change", () => { if (!syncing) syncSoon(); });
    }
    const prompt = $("auPrompt");
    if (prompt) prompt.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); EP.authoringSend(); }
    });
    const dropzone = $("auFigureReferenceDrop");
    if (dropzone) {
      ["dragenter", "dragover"].forEach((name) => dropzone.addEventListener(name, (e) => {
        e.preventDefault(); dropzone.classList.add("dragging");
      }));
      ["dragleave", "drop"].forEach((name) => dropzone.addEventListener(name, (e) => {
        e.preventDefault(); dropzone.classList.remove("dragging");
      }));
      dropzone.addEventListener("drop", (e) => EP.authoringAddReferences(e.dataTransfer.files));
    }
    const inlineFigure = $("auFigurePreview");
    if (inlineFigure) inlineFigure.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        EP.authoringOpenFigureEditor(e);
      }
    });
  });
})(window.EP);
