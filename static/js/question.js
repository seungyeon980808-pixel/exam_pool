/* ===== 문항 설계 — 보기·선지 조립, 근거 뷰어, 참고 기출, 저장 전 검토 ===== */
(function (EP) {
  "use strict";
  const $ = EP.$, esc = EP.esc, api = EP.api, post = EP.post, put = EP.put, del = EP.del;
  const S = EP.S;

  /* ---------- 유형 ---------- */
  EP.loadQuestionPaletteOptions = async function (selected) {
    const select = $("qpalette");
    if (!select) return;
    const requested = selected !== undefined ? (selected || "") : (S.paletteTemplate || "");
    const sessionId = Number(S.authoringSessionId || 0);
    if (selected !== undefined) S.paletteTemplate = requested;
    try {
      const data = await api("/api/integrations/hwppalette/palettes");
      const activeId = data.active && data.active.suneung;
      const active = (data.packages || []).find((p) => p.id === activeId);
      const templates = active ? (active.items || []).filter((item) => item.category === "템플릿") : [];
      select.innerHTML = '<option value="">자동 선택</option>' + templates.map((item) =>
        `<option value="${esc(item.label)}" data-slots="${esc((item.slot_names || []).join(','))}">${esc(item.name || item.label)}</option>`
      ).join("");
      if (sessionId && Number(S.authoringSessionId || 0) !== sessionId) return;
      select.value = requested;
      if (!select.value && !requested) S.paletteTemplate = "";
      EP.updateQuestionSettingsSummary();
    } catch (e) {
      select.innerHTML = '<option value="">물감 목록을 불러오지 못함</option>';
    }
  };

  EP.onPaletteTemplateChange = function () {
    const select = $("qpalette");
    S.paletteTemplate = select ? select.value : "";
    const option = select && select.selectedOptions[0];
    const signature = `${S.paletteTemplate} ${(option && option.dataset.slots) || ""}`;
    const photoSlots = ((option && option.dataset.slots) || "").split(",")
      .filter((slot) => /^(?:사진|photo)\d+$/i.test(slot.trim())).length;
    const labelCount = /([1-6])(?:소|대)?사진/.exec(S.paletteTemplate);
    const requiredFigures = Math.max(photoSlots, labelCount ? Number(labelCount[1]) : 0);
    if (signature.includes("합답") || /(?:^|,)ㄱ(?:,|$)/.test((option && option.dataset.slots) || "")) {
      $("qtype").value = "합답형";
    } else if (signature.includes("정답")) {
      $("qtype").value = "정답형";
    } else if (signature.includes("서술")) {
      $("qtype").value = "서술형";
    }
    EP.onTypeChange();
    const composition = $("auFigureComposition");
    if (requiredFigures > 1 && composition && composition.value !== "separate") {
      composition.value = "separate";
      if (EP.authoringFigureOptionsChanged) EP.authoringFigureOptionsChanged();
    }
  };

  EP.questionStyleMeta = function (base) {
    const meta = { ...(base || {}) };
    if (S.paletteTemplate) meta.palette_template = S.paletteTemplate;
    else delete meta.palette_template;
    return meta;
  };

  EP.onTypeChange = function () {
    const t = $("qtype").value;
    const hap = t === "합답형";
    const essay = t === "서술형";                        // 선지 없이 모범답안만 받는다
    $("bogiBox").classList.toggle("hidden", !hap);
    $("presetBox").classList.toggle("hidden", !hap);     // 정답형엔 프리셋 없음
    $("imgChoiceBox").classList.toggle("hidden", hap || essay);  // 그림 선지는 정답형만
    if ($("choiceArea")) $("choiceArea").classList.toggle("hidden", essay);
    if ($("modelAnswerBox")) $("modelAnswerBox").classList.toggle("hidden", !essay);
    if (hap && !S.bogi.length) { EP.addBogi(); EP.addBogi(); EP.addBogi(); }
    if (!essay) EP.ensureFiveChoices();
    EP.updateQuestionSettingsSummary();
    EP.renderChoices();
  };

  /* ---------- 보기 (ㄱㄴㄷ) ---------- */
  EP.autoGrow = function (el) {
    if (!el) return;
    const maxHeight = Number(el.dataset.maxHeight) || 180;
    el.style.height = "0px";
    el.style.height = `${Math.min(Math.max(el.scrollHeight, 34), maxHeight)}px`;
    el.style.overflowY = el.scrollHeight > maxHeight ? "auto" : "hidden";
  };

  function growRenderedTextareas(containerId) {
    requestAnimationFrame(() => {
      const container = $(containerId);
      if (container) container.querySelectorAll("textarea.nz-autogrow").forEach(EP.autoGrow);
    });
  }

  EP.addBogi = function () {
    if (S.bogi.length >= 5) return;
    S.bogi.push({ label: EP.LABELS[S.bogi.length], text: "", proposition_id: null, variant_id: null,
                  evidence: "", explanation: "" });
    EP.renderBogi();
  };
  EP.renderBogi = function () {
    $("bogiRows").innerHTML = S.bogi.map((b, i) => `
      <div class="nz-bogi-card">
        <div class="nz-fr nz-item-main">
          <label>${b.label}</label>
          <textarea class="nz-autogrow" rows="1" oninput="EP.setBogi(${i}, this.value);EP.autoGrow(this)" placeholder="보기 문장">${esc(b.text)}</textarea>
          <button class="nz-tb mini" onclick="EP.pickFor('bogi', ${i})">명제</button>
          <span class="nz-tag ${b.proposition_id || b.variant_id || b.evidence ? "g" : "r"}">${b.proposition_id ? "명제" : b.variant_id ? "변형" : b.evidence ? "직접근거" : "근거없음"}</span>
          <button class="nz-tb mini" onclick="EP.delBogi(${i})">×</button>
        </div>
        <div class="nz-bogi-meta">
          <label>근거</label>
          <textarea class="nz-autogrow" rows="1" oninput="EP.setBogiDetail(${i}, 'evidence', this.value);EP.autoGrow(this)" placeholder="교과서·교육과정·수업 자료의 출처와 근거 문장">${esc(b.evidence || "")}</textarea>
          <label>판단 해설</label>
          <textarea class="nz-autogrow" rows="1" oninput="EP.setBogiDetail(${i}, 'explanation', this.value);EP.autoGrow(this)" placeholder="이 근거로 보기를 참·거짓으로 판단하는 이유">${esc(b.explanation || "")}</textarea>
        </div>
      </div>`).join("");
    growRenderedTextareas("bogiRows");
  };
  EP.setBogi = function (i, v) { S.bogi[i].text = v; };
  EP.setBogiDetail = function (i, field, value) {
    if (S.bogi[i] && (field === "evidence" || field === "explanation")) S.bogi[i][field] = value;
  };
  EP.delBogi = function (i) {
    S.bogi.splice(i, 1);
    S.bogi.forEach((b, j) => { b.label = EP.LABELS[j]; });
    EP.renderBogi(); EP.renderChoices();
  };

  /* ---------- 선지 ---------- */
  EP.ensureFiveChoices = function () {
    S.choices = (S.choices || []).slice(0, 5);
    while (S.choices.length < 5) {
      S.choices.push({ ord: S.choices.length + 1, text: "", proposition_id: null, variant_id: null,
                       combo: null, custom_evidence: "", is_answer: false });
    }
    S.choices.forEach((c, i) => { c.ord = i + 1; });
  };

  EP.addChoice = function () {
    if (S.choices.length >= 5) return;
    S.choices.push({ ord: S.choices.length + 1, text: "", proposition_id: null, variant_id: null,
                     combo: null, custom_evidence: "", is_answer: false });
    EP.renderChoices();
  };
  function comboValue(choice) {
    const raw = choice && choice.combo;
    if (Array.isArray(raw)) return raw.join(", ");
    if (typeof raw === "string") {
      try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) return parsed.join(", ");
      } catch (e) { /* already plain text */ }
      return raw;
    }
    return "";
  }
  EP.renderChoices = function () {
    const essay = $("qtype").value === "서술형";
    if (!essay) EP.ensureFiveChoices();
    const hap = $("qtype").value === "합답형";
    const img = $("imgChoices") && $("imgChoices").checked;
    $("choiceRows").classList.toggle("nz-combo-choice-row", hap);
    $("choiceRows").innerHTML = S.choices.map((c, i) => `
      <div class="nz-fr${hap ? " nz-combo-choice" : ""}">
        <label>${"①②③④⑤"[i] || i + 1}</label>
        ${hap
          ? `<input value="${esc(comboValue(c))}" oninput="EP.setCombo(${i}, this.value)" placeholder="예: ㄱ, ㄷ" />`
          : `<textarea class="nz-autogrow" rows="1" oninput="EP.setChoice(${i}, this.value);EP.autoGrow(this)"
               placeholder="${img ? "그림 파일명 (예: 보기1.png)" : "선지 문장"}">${esc(c.text)}</textarea>`}
        ${hap ? "" : `<button class="nz-tb mini" onclick="EP.pickFor('choice', ${i})">명제</button>`}
        ${hap ? "" : `<span class="nz-tag ${c.proposition_id || c.variant_id || c.custom_evidence ? "g" : "r"}">
          ${c.proposition_id ? "명제" : c.variant_id ? "변형" : (c.combo && c.combo.length) ? "조합" : c.custom_evidence ? "직접근거" : "근거없음"}</span>
        `}
        <label class="nz-lb"><input type="radio" name="ans" ${c.is_answer ? "checked" : ""} onchange="EP.setAnswer(${i})" /> 정답</label>
      </div>`).join("");
    growRenderedTextareas("choiceRows");
    EP.hintAnswerLength();
  };
  EP.setChoice = function (i, v) { S.choices[i].text = v; EP.hintAnswerLength(); };
  EP.setCombo = function (i, v) { S.choices[i].combo = v.split(",").map((s) => s.trim()).filter(Boolean); };
  EP.setAnswer = function (i) { S.choices.forEach((c, j) => { c.is_answer = j === i; }); EP.renderChoices(); };
  EP.delChoice = function (i) {
    S.choices.splice(i, 1);
    S.choices.forEach((c, j) => { c.ord = j + 1; });
    EP.renderChoices();
  };

  /** 정답 선지만 유독 길면 그 자리에서 바로 알린다 (서버 검토 규칙과 같은 기준) */
  EP.hintAnswerLength = function () {
    const box = $("lenHint");
    if (!box) return;
    if ($("qtype").value === "합답형" || ($("imgChoices") && $("imgChoices").checked)) {
      box.innerHTML = ""; return;
    }
    const lens = S.choices.map((c) => (c.text || "").trim().length).filter(Boolean);
    const ans = S.choices.find((c) => c.is_answer);
    if (!ans || lens.length < 3) { box.innerHTML = ""; return; }
    const aLen = (ans.text || "").trim().length;
    const others = S.choices.filter((c) => !c.is_answer).map((c) => (c.text || "").trim().length).filter(Boolean);
    if (!others.length) { box.innerHTML = ""; return; }
    const avg = others.reduce((a, b) => a + b, 0) / others.length;
    box.innerHTML = (aLen === Math.max(...lens) && aLen >= avg * 1.4)
      ? `<span class="warn">정답 선지가 가장 깁니다 (${aLen}자 / 나머지 평균 ${Math.round(avg)}자) — 길이만 보고 답을 고를 수 있습니다.</span>`
      : "";
  };

  /* ---------- 합답형 프리셋 ---------- */
  EP.renderPresets = async function () {
    const presets = await api("/api/combo-presets");
    $("presetBtns").innerHTML = Object.entries(presets).map(([k, p]) =>
      `<button class="nz-preset" onclick="EP.applyPreset('${k}')" title="${esc(p.preview)} · ${esc(p.desc)}">
         <b>${esc(p.name)}</b></button>`).join(" ");
  };

  EP.applyPreset = async function (name) {
    const presets = await api("/api/combo-presets");
    S.choices = presets[name].combos.map((combo, i) => ({
      ord: i + 1, text: "", proposition_id: null, variant_id: null,
      combo, custom_evidence: "", is_answer: false,
    }));
    EP.renderChoices();
  };

  /* ---------- 명제 고르기 패널 ---------- */
  EP.loadPicker = async function () {
    const search = $("pickSearch");
    const list = $("pickerList");
    if (!search || !list) return;
    const q = search.value.trim();
    let rows = await api("/api/propositions?" + new URLSearchParams(q ? { q } : {}));
    const allowPk = EP.allowedCodes();
    if (allowPk) rows = rows.filter((r) => allowPk.has(r.standard_code));
    list.innerHTML = rows.slice(0, 20).map((r, ri) => `
      <div class="nz-hit">
        <div class="nz-hit-snip">${esc(r.text)}</div>
        <div class="nz-hit-src">${esc(r.standard_code)} · 근거 ${r.ev_count} · 변형 ${r.var_count}</div>
        <button class="nz-tb mini" data-use-prop data-ri="${ri}">보기/선지로</button>
      </div>`).join("") || '<p class="nz-sub">명제가 없습니다.</p>';
    // 데이터를 rows 변수에 보관하고 이벤트 위임으로 안전하게 처리
    list._pickerRows = rows;
    list.addEventListener("click", function (e) {
      const btn = e.target.closest("[data-use-prop]");
      if (!btn) return;
      const r = list._pickerRows[parseInt(btn.dataset.ri)];
      if (r) EP.useProp(r.id, r.text);
    });
  };

  EP.useProp = function (id, text) {
    if ($("qtype").value === "합답형") {
      if (!S.bogi.find((b) => !b.text)) EP.addBogi();
      const target = S.bogi.find((b) => !b.text);
      if (target) { target.text = text; target.proposition_id = id; EP.renderBogi(); }
    } else {
      if (!S.choices.find((c) => !c.text)) EP.addChoice();
      const target = S.choices.find((c) => !c.text);
      if (target) { target.text = text; target.proposition_id = id; EP.renderChoices(); }
    }
  };

  /* ---------- 근거 검색 (문항 설계 오른쪽 패널) ---------- */
  EP.setSrc = function (src) {
    S.evSrc = src;
    document.querySelectorAll(".nz-srcbtn").forEach((b) =>
      b.classList.toggle("on", (b.dataset.src || "") === src));
    if ($("evSearch").value.trim()) EP.searchEvidence();
  };

  EP.searchEvidence = async function () {
    const q = $("evSearch").value.trim();
    const terms = EP.splitTerms(q);
    if (!terms.length) {
      // 검색어를 지우면 결과·원문·색상칩까지 같이 지운다. 남겨두면 방금 지운 검색의
      // 결과를 지금 결과로 착각한다.
      $("evList").innerHTML = '<p class="nz-sub" style="padding:10px">키워드를 넣으면 교과서·기출·수업 기록에서 근거를 찾습니다.</p>';
      EP.evSeq++;                 // 불러오는 중이던 원문이 뒤늦게 덮어쓰지 못하게
      $("evViewTitle").textContent = "원문";
      $("evViewBody").innerHTML =
        '<p class="nz-sub" style="padding:14px;text-align:center">결과를 클릭하면 원문이 뜹니다.</p>';
      S.evViewer = { docId: null, page: 1, lastPage: 1, q: "" };
      EP.renderTermBar([]);
      return;
    }
    S.evViewer.q = q;
    const params = new URLSearchParams({ q: q, limit: 60 });
    if (S.onlyDocId) params.set("doc_id", S.onlyDocId);   // 환경설정에서 '이 문서만' 을 켠 경우
    if (S.evSrc) params.set("doc_type", S.evSrc);         // 걸러내기는 서버에서 (상위 60건 잘림 방지)
    const r = await api("/api/evidence/search?" + params);
    const items = r.items;
    S.evSearchItems = items;
    const groups = {};
    items.forEach((it) => { (groups[it.doc_title] ||= []).push(it); });
    $("evList").innerHTML = items.length
      ? `<div class="nz-hlbar" style="padding:6px 10px"><b>${r.total}</b>개 일치 · ${items.length}개 표시${S.evSrc ? " · " + S.evSrc : ""}</div>` +
        Object.entries(groups).map(([title, list]) => `
        <div class="nz-docgroup">
          <div class="nz-docgroup-head">${esc(title)} <span class="n">${list.length}개</span></div>
          ${list.map((h) => `
            <div class="nz-res" role="button" tabindex="0" onclick="EP.evShow(${h.document_id}, ${h.page_no}, this, ${items.indexOf(h)})"
                 onkeydown="EP.activateOnKey(event, () => EP.evShow(${h.document_id}, ${h.page_no}, this, ${items.indexOf(h)}))">
              <div class="nz-res-top">
                <span class="nz-pct ${h.match_pct < 60 ? "low" : ""}">${h.match_pct}%</span>
                <span class="nz-res-page">${h.kind === "수업" ? h.page_no + "번째 조각" : h.page_no + "페이지"}</span>
                ${S.curPropId ? `<button class="nz-tb mini blu" style="margin-left:auto"
                   onclick='event.stopPropagation();EP.attachEvidence(${S.curPropId}, ${JSON.stringify(h).replace(/'/g, "&#39;")})'
                   >근거로 저장</button>` : ""}
              </div>
              <div class="nz-res-snip">${EP.markTerms(h.snippet, terms)}</div>
            </div>`).join("")}
        </div>`).join("")
      : '<p class="nz-sub" style="padding:12px">결과 없음 — 환경설정 &gt; 근거 문서에서 PDF 를 인덱싱하거나 수업 기록을 등록하세요.</p>';

    // 단어별 적중 수를 칩에 표시
    const counts = {};
    terms.forEach((t) => {
      counts[t] = items.filter((it) => (it.snippet || "").toLowerCase().includes(t.toLowerCase())).length;
    });
    EP.renderTermBar(terms, counts);
  };

  /** 원문 표시 요청 일련번호. 페이지를 불러오는 사이 검색어를 지우거나 다른 결과를
   *  누르면, 늦게 도착한 이전 응답이 화면을 덮어써 "지웠는데 그대로 남아있다"가 된다. */
  EP.evSeq = 0;
  EP.evStale = (seq) => seq !== EP.evSeq;

  /** 문항 설계 화면의 근거 뷰어 — 원문을 옆에 띄워두고 문항을 쓴다 */
  EP.evShow = async function (docId, pageNo, el, resultIndex) {
    const seq = ++EP.evSeq;
    document.querySelectorAll("#evList .nz-res.on").forEach((n) => n.classList.remove("on"));
    if (el) el.classList.add("on");
    S.evViewer.docId = docId; S.evViewer.page = pageNo;
    S.evViewer.terms = EP.splitTerms(S.evViewer.q || "");
    S.evViewer.currentSource = Number.isInteger(resultIndex)
      ? (S.evSearchItems || [])[resultIndex] || null : null;

    // 수업 기록은 PDF 가 아니라 글이다 — 그대로 본문을 보여준다
    if (docId < 0) return EP.evShowLesson(-docId, pageNo, seq);

    const meta = await api(`/api/documents/${docId}/page/${pageNo}`);
    if (EP.evStale(seq)) return;
    S.evViewer.lastPage = meta.last_page;

    // 기출은 페이지 전체가 아니라 문항 하나씩 보여준다
    const types = await EP.ensureDocTypes();
    if (EP.evStale(seq)) return;
    if (types[docId] === "기출") {
      try {
        if (await EP.showExamItems(docId, pageNo, meta.title, seq)) return;
      } catch (e) { /* 문항 인식 실패 → 아래 페이지 전체로 */ }
      if (EP.evStale(seq)) return;
    }
    $("evViewTitle").textContent = `${meta.title} — ${pageNo} / ${meta.last_page}p`;
    $("evViewBody").innerHTML =
      `<div class="nz-pagewrap" id="evWrap"><img id="evImg" src="/api/documents/${docId}/page/${pageNo}/image" /></div>`;
    EP.prefetch(docId, pageNo + 1);
    if (!S.evViewer.q) return;
    const hl = await api(`/api/documents/${docId}/page/${pageNo}/highlights?q=` +
      encodeURIComponent(S.evViewer.q));
    if (EP.evStale(seq) || !$("evWrap")) return;
    EP.paintHighlights($("evWrap"), $("evImg"), hl, $("evViewBody"));
  };

  let evidenceCrop = null;

  function evidenceSelectionButtons(selecting) {
    $("evSelectStart").classList.toggle("hidden", selecting);
    $("evSelectConfirm").classList.toggle("hidden", !selecting);
    $("evSelectCancel").classList.toggle("hidden", !selecting);
  }

  EP.cancelEvidenceReference = function () {
    if (evidenceCrop) {
      evidenceCrop.host.onpointerdown = null;
      evidenceCrop.host.onpointermove = null;
      evidenceCrop.host.onpointerup = null;
      evidenceCrop.host.classList.remove("nz-reference-selecting");
      if (evidenceCrop.overlay) evidenceCrop.overlay.remove();
    }
    evidenceCrop = null;
    evidenceSelectionButtons(false);
  };

  EP.startEvidenceReference = function () {
    EP.cancelEvidenceReference();
    const img = $("evViewBody").querySelector("img");
    if (!img) return alert("영역을 지정할 원문 이미지를 먼저 선택하세요.");
    if (!img.complete || !img.naturalWidth) return alert("원문 이미지가 로드된 뒤 다시 시도하세요.");
    const host = img.parentElement;
    const overlay = document.createElement("div");
    overlay.className = "nz-reference-selection hidden";
    host.appendChild(overlay);
    host.classList.add("nz-reference-selecting");
    evidenceCrop = { img, host, overlay, rect: null, start: null };
    evidenceSelectionButtons(true);

    const point = (event) => {
      const box = img.getBoundingClientRect();
      return {
        x: Math.max(0, Math.min(box.width, event.clientX - box.left)),
        y: Math.max(0, Math.min(box.height, event.clientY - box.top)),
      };
    };
    const paint = (a, b) => {
      const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y);
      const w = Math.abs(a.x - b.x), h = Math.abs(a.y - b.y);
      evidenceCrop.rect = { x, y, w, h };
      overlay.classList.remove("hidden");
      overlay.style.left = `${img.offsetLeft + x}px`;
      overlay.style.top = `${img.offsetTop + y}px`;
      overlay.style.width = `${w}px`;
      overlay.style.height = `${h}px`;
    };
    host.onpointerdown = (event) => {
      event.preventDefault();
      evidenceCrop.start = point(event);
      host.setPointerCapture(event.pointerId);
      paint(evidenceCrop.start, evidenceCrop.start);
    };
    host.onpointermove = (event) => {
      if (evidenceCrop && evidenceCrop.start) paint(evidenceCrop.start, point(event));
    };
    host.onpointerup = (event) => {
      if (!evidenceCrop || !evidenceCrop.start) return;
      paint(evidenceCrop.start, point(event));
      evidenceCrop.start = null;
      if (host.hasPointerCapture(event.pointerId)) host.releasePointerCapture(event.pointerId);
    };
  };

  EP.confirmEvidenceReference = async function () {
    if (!evidenceCrop || !evidenceCrop.rect) return alert("원문에서 참고할 영역을 드래그하세요.");
    const { img, rect } = evidenceCrop;
    if (rect.w < 12 || rect.h < 12) return alert("참고할 영역을 조금 더 크게 지정하세요.");
    const canvas = document.createElement("canvas");
    const scaleX = img.naturalWidth / img.clientWidth;
    const scaleY = img.naturalHeight / img.clientHeight;
    canvas.width = Math.max(1, Math.round(rect.w * scaleX));
    canvas.height = Math.max(1, Math.round(rect.h * scaleY));
    canvas.getContext("2d").drawImage(
      img, rect.x * scaleX, rect.y * scaleY, rect.w * scaleX, rect.h * scaleY,
      0, 0, canvas.width, canvas.height
    );
    const source = S.evViewer.currentSource || {};
    const sourceLabel = $("evViewTitle").textContent.trim();
    const payload = {
      filename: `reference_${Math.abs(S.evViewer.docId || 0)}_${S.evViewer.page || 1}.png`,
      data_url: canvas.toDataURL("image/png"),
      source_label: sourceLabel,
      source_text: source.snippet || (S.evViewer.q ? `검색어: ${S.evViewer.q}` : ""),
      usage: "both",
      source_meta: {
        document_id: S.evViewer.docId, page_no: S.evViewer.page, query: S.evViewer.q || "",
        crop: {
          x: rect.x / img.clientWidth, y: rect.y / img.clientHeight,
          w: rect.w / img.clientWidth, h: rect.h / img.clientHeight,
        },
      },
    };
    try {
      await EP.authoringAddReferenceData(payload);
      EP.cancelEvidenceReference();
    } catch (error) {
      alert("참고 자료 연결 실패: " + error.message);
    }
  };

  /** 수업 기록 조각을 오른쪽 패널에 — 검색어는 형광으로 칠한다 */
  EP.evShowLesson = async function (lessonId, chunkNo, seq) {
    const d = await api(`/api/lesson-chunk/${lessonId}/${chunkNo}`);
    if (seq !== undefined && EP.evStale(seq)) return;
    S.evViewer.lastPage = d.last_chunk;
    $("evViewTitle").textContent = `${d.title} — ${d.chunk_no} / ${d.last_chunk} 조각`;
    const terms = S.evViewer.terms || [];
    let html = esc(d.text);
    terms.forEach((t, i) => {
      if (!t) return;
      const re = new RegExp(t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
      html = html.replace(re, (m) =>
        `<mark style="background:${EP.HL_COLORS[i % EP.HL_COLORS.length]}">${m}</mark>`);
    });
    $("evViewBody").innerHTML = `<div class="nz-lessonview">${html.replace(/\n/g, "<br />")}</div>`;
  };

  /** 근거 검색 접기/펴기 — 펼치면 화면 절반 */
  EP.toggleSide = function () {
    const el = $("qside");
    el.classList.toggle("folded");
    const folded = el.classList.contains("folded");
    // 접을 땐 인라인 폭을 비운다 — 안 그러면 드래그로 정한 폭이 34px 규칙을 이긴다
    if (folded) el.style.width = ""; else EP.applyWidths();
    $("evFoldBtn").textContent = "›";
    $("evFoldBtn").setAttribute("aria-expanded", folded ? "false" : "true");
    $("evFoldBtn").setAttribute("aria-label", folded ? "근거 검색 펼치기" : "근거 검색 접기");
    $("evFoldBtn").title = folded ? "근거 검색 펼치기" : "근거 검색 접기";
    localStorage.setItem("ep_side_folded", folded ? "1" : "");
  };

  EP.toggleAuthoringRight = function (fold) {
    const grid = document.querySelector("#tab-question .au-grid");
    if (!grid) return;
    const folded = typeof fold === "boolean" ? fold : !grid.classList.contains("right-folded");
    grid.classList.toggle("right-folded", folded);
    document.querySelectorAll("#tab-question .au-column-toggle").forEach((button) => {
      button.setAttribute("aria-expanded", folded ? "false" : "true");
    });
    localStorage.setItem("ep_authoring_right_folded", folded ? "1" : "");
  };

  EP.toggleAuthoringPreview = function (collapse) {
    const grid = document.querySelector("#tab-question .au-grid");
    const button = $("auPreviewToggle");
    if (!grid) return;
    const collapsed = typeof collapse === "boolean"
      ? collapse : !grid.classList.contains("preview-collapsed");
    grid.classList.toggle("preview-collapsed", collapsed);
    if (button) {
      button.textContent = "⌄";
      button.setAttribute("aria-expanded", collapsed ? "false" : "true");
      button.setAttribute("aria-label", collapsed ? "문항 미리보기 펼치기" : "문항 미리보기 접기");
      button.title = collapsed ? "문항 미리보기 펼치기" : "문항 미리보기 접기";
    }
    localStorage.setItem("ep_authoring_preview_collapsed", collapsed ? "1" : "0");
  };

  EP.toggleFigureReferences = function () {
    const pane = document.querySelector("#tab-question .au-figure-reference-pane");
    const body = $("auFigureReferenceBody");
    if (!pane || !body) return;
    const open = pane.classList.contains("collapsed");
    pane.classList.toggle("collapsed", !open);
    body.toggleAttribute("inert", !open);
    body.setAttribute("aria-hidden", open ? "false" : "true");
    const button = pane.querySelector(".au-figure-reference-toggle");
    if (button) button.setAttribute("aria-expanded", open ? "true" : "false");
  };

  EP.setQuestionSection = function (name, open) {
    const section = $(name === "settings" ? "questionSettingsSection" : "questionReviewSection");
    const body = $(name === "settings" ? "questionSettingsBody" : "questionReviewBody");
    if (!section || !body) return;
    section.classList.toggle("collapsed", !open);
    body.classList.toggle("hidden", !open);
    if (open) body.removeAttribute("inert"); else body.setAttribute("inert", "");
    body.setAttribute("aria-hidden", open ? "false" : "true");
    const button = section.querySelector(".nz-section-toggle");
    if (button) {
      const sectionName = name === "settings" ? "문항 설정" : "문항 검토";
      button.setAttribute("aria-expanded", open ? "true" : "false");
      button.textContent = "⌄";
      button.setAttribute("aria-label", `${sectionName} ${open ? "접기" : "펼치기"}`);
      button.title = `${sectionName} ${open ? "접기" : "펼치기"}`;
    }
    localStorage.setItem(`ep_question_${name}_open`, open ? "1" : "0");
  };

  EP.toggleQuestionSection = function (name) {
    const section = $(name === "settings" ? "questionSettingsSection" : "questionReviewSection");
    EP.setQuestionSection(name, !!section && section.classList.contains("collapsed"));
  };

  EP.updateQuestionSettingsSummary = function () {
    const summary = $("questionSettingsSummary");
    if (!summary) return;
    summary.textContent = [
      $("qpalette") && $("qpalette").value,
      $("qtype") && $("qtype").value,
      $("qdiff") && $("qdiff").value,
      $("qpoints") && `${$("qpoints").value || 3}점`,
      EP.stdValue && EP.stdValue(),
    ].filter(Boolean).join(" · ");
  };
  EP.togglePicker = function () { $("pickerList").classList.toggle("hidden"); };

  EP.toggleQuestionFullscreen = async function () {
    const button = $("questionFullscreenBtn");
    const body = document.body;
    const active = !!document.fullscreenElement || body.classList.contains("question-fullscreen-active");
    try {
      if (!active && document.documentElement.requestFullscreen) {
        await document.documentElement.requestFullscreen();
        body.classList.add("question-fullscreen-active");
        body.classList.remove("question-fullscreen-fallback");
      } else if (document.fullscreenElement && document.exitFullscreen) {
        await document.exitFullscreen();
        body.classList.remove("question-fullscreen-active", "question-fullscreen-fallback");
      } else if (active) {
        body.classList.remove("question-fullscreen-active", "question-fullscreen-fallback");
      } else {
        body.classList.add("question-fullscreen-active", "question-fullscreen-fallback");
      }
    } catch (e) {
      if (active) body.classList.remove("question-fullscreen-active", "question-fullscreen-fallback");
      else body.classList.add("question-fullscreen-active", "question-fullscreen-fallback");
    }
    const isActive = !!document.fullscreenElement || body.classList.contains("question-fullscreen-active");
    if (button) {
      button.textContent = isActive ? "⛶ 전체화면 종료" : "⛶ 전체화면";
      button.setAttribute("aria-pressed", isActive ? "true" : "false");
      button.title = isActive ? "문항 설계 전체화면 종료" : "문항 설계 전체화면";
    }
  };

  EP.evZoom = function () {
    if (!S.evViewer.docId) return alert("먼저 결과를 선택하세요.");
    if (S.evViewer.docId < 0) return EP.peekLesson(-S.evViewer.docId, S.evViewer.page);
    EP.peekPage(S.evViewer.docId, S.evViewer.page, S.evViewer.q);
  };

  EP.evViewPage = function (d) {
    if (!S.evViewer.docId) return;
    const p = S.evViewer.page + d;
    if (p < 1 || p > S.evViewer.lastPage) return;
    EP.evShow(S.evViewer.docId, p, null);
  };

  /* ---------- 검색 결과: 방향키로 오르내리기 ---------- */
  EP.moveResult = function (delta) {
    const list = [...document.querySelectorAll("#evList .nz-res")];
    if (!list.length) return;
    const cur = list.findIndex((el) => el.classList.contains("on"));
    const next = cur < 0 ? 0 : Math.min(list.length - 1, Math.max(0, cur + delta));
    list[next].click();
    list[next].scrollIntoView({ block: "nearest" });
  };

  EP.onEvInput = EP.debounce(() => { EP.searchEvidence(); }, 220);

  /* ---------- 저장 ---------- */
  EP.collectQuestion = function () {
    return {
      title: $("qtitle").value,
      qtype: $("qtype").value,
      is_negative: $("isNeg").checked,
      passage: $("qpassage").value,
      material: $("qmaterial").value,
      ask: $("qask").value,
      bogi_items: $("qtype").value === "합답형" ? S.bogi : [],
      // 선지형은 정답 번호를, 서술형은 모범답안 전문을 answer 칸에 넣는다.
      answer: $("qtype").value === "서술형"
        ? ($("qModelAnswer") ? $("qModelAnswer").value : "")
        : (() => { const i = S.choices.findIndex((c) => c.is_answer); return i >= 0 ? "①②③④⑤"[i] : ""; })(),
      default_points: parseFloat($("qpoints").value) || 3,
      difficulty: $("qdiff").value,
      standard_code: EP.stdValue() || null,
      intent: $("qintent").value,
      explanation: $("qexplanation") ? $("qexplanation").value : "",
      behavior: $("qbehavior") ? $("qbehavior").value : "",
      origin: $("qorigin") ? $("qorigin").value : "",
      origin_note: $("qoriginNote") ? $("qoriginNote").value : "",
      image_choices: $("imgChoices") ? $("imgChoices").checked : false,
      status: $("qstatus") ? $("qstatus").value : "초안",
      review_note: JSON.stringify(S.checkState),
      style_meta: EP.questionStyleMeta(
        (S.authoringSessionId && EP.authoringStyleMeta) ? EP.authoringStyleMeta() : {}),
      choices: $("qtype").value === "서술형" ? [] : S.choices,
    };
  };

  EP.saveQuestion = async function () {
    const q = EP.collectQuestion();
    if (EP.authoringCanSave && !EP.authoringCanSave()) return;
    if (!q.ask.trim()) return alert("발문을 입력하세요.");
    // 서술형은 선지가 없다 — 대신 모범답안(채점 기준)이 있어야 한다.
    if (q.qtype === "서술형") {
      if (!(q.answer || "").trim()) return alert("서술형은 모범답안(채점 기준)을 적어야 합니다.");
    } else if (!q.choices.length) {
      return alert("선지를 추가하세요.");
    }
    if (q.status === "완성") {
      const missing = CHECKS.filter((c) => !S.checkState[c.k]);
      if (missing.length) {
        EP.setQuestionSection("review", true);
        return alert("'완성'으로 저장하려면 확인 항목을 모두 체크해야 합니다.\n\n미확인: "
          + missing.map((m) => m.t).join(", "));
      }
      if (!(S.checkState.note || "").trim()) {
        EP.setQuestionSection("review", true);
        return alert("확인 근거를 적어주세요.");
      }
    }
    let qid = S.editingQid;
    if (S.editingQid) { await put(`/api/questions/${S.editingQid}`, q); }
    else { const r = await post("/api/questions", q); qid = r.id; }
    // 이 문항을 만들며 담은 참고 기출을 문항에 연결한다
    for (const rf of S.refs.filter((x) => !x.question_id && x.authoring_session_id === S.authoringSessionId)) {
      await EP.patch(`/api/exam-refs/${rf.id}`, { question_id: qid });
    }
    if (EP.authoringBind) await EP.authoringBind(qid);
    EP.resetQuestionForm(); EP.loadQuestions();
    alert("문항을 저장했습니다.");
  };

  EP.resetQuestionForm = function () {
    // The authoring workbench keeps independent server-backed tabs. Opening a new
    // question must not clear or overwrite the currently active draft first.
    if (EP.authoringNew) return EP.authoringNew();
    S.editingQid = null; S.bogi = []; S.choices = []; S.checkState = {};
    S.paletteTemplate = "";
    if ($("qpalette")) $("qpalette").value = "";
    EP.renderChecklist();
    S.refs = []; S.curRefId = null; EP.renderRefs();
    ["qtitle", "qpassage", "qmaterial", "qask", "qintent"].forEach((id) => { $(id).value = ""; });
    EP.setStdValue("");
    $("isNeg").checked = false; $("qpoints").value = "3";
    if ($("qbehavior")) $("qbehavior").value = "";
    if ($("qorigin")) $("qorigin").value = "";
    if ($("qoriginNote")) $("qoriginNote").value = "";
    if ($("qModelAnswer")) $("qModelAnswer").value = "";
    EP.renderBogi(); EP.renderChoices(); $("qCheckResult").innerHTML = "";
  };

  /** 저장 전 초안 검토: 서버 규칙과 같은 항목을 클라이언트에서 미리 본다 */
  EP.checkQuestionDraft = function () {
    const q = EP.collectQuestion();
    const issues = [];
    if (!q.ask.trim()) issues.push("발문(질문)이 비어 있습니다.");
    if (q.qtype === "서술형") {
      if (!(q.answer || "").trim()) issues.push("모범답안(채점 기준)이 비어 있습니다.");
    } else {
      if (q.choices.length < 2) issues.push("선지가 2개 미만입니다.");
      if (!q.choices.some((c) => c.is_answer)) issues.push("정답이 지정되지 않았습니다.");
      q.choices.forEach((c, i) => {
        const ok = c.proposition_id || c.variant_id || c.custom_evidence || (c.combo && c.combo.length);
        if (!ok) issues.push(`${i + 1}번 선지에 근거가 없습니다.`);
      });
      if (q.qtype === "합답형") {
        q.bogi_items.forEach((b, i) => {
          const label = b.label || EP.LABELS[i] || i + 1;
          if (!(b.proposition_id || b.variant_id || (b.evidence || "").trim())) {
            issues.push(`${label} 보기의 작성 근거가 없습니다.`);
          }
          if (!(b.explanation || "").trim()) issues.push(`${label} 보기의 근거 판단 해설이 없습니다.`);
        });
      }
    }
    if (!q.standard_code) issues.push("성취기준이 선택되지 않았습니다.");

    // 형식 너머 — 서버 checklist.py 와 같은 기준
    const NEG = ["않은", "아닌", "틀린", "옳지 않", "적절하지 않"];
    const looksNeg = NEG.some((w) => q.ask.includes(w));
    if (looksNeg && !q.is_negative) issues.push("발문이 부정형인데 '부정 문항' 표시가 꺼져 있습니다.");
    if (q.is_negative && !looksNeg) issues.push("'부정 문항'으로 표시했는데 발문에 부정어가 없습니다.");
    if (!q.behavior) issues.push("행동영역이 비어 있습니다 (이원목적분류표에 필요).");
    if (!q.origin) issues.push("출처가 비어 있습니다 (직접 / AI초안 / 기출변형 / 기출복원).");
    if (q.origin === "AI초안" && q.status === "완성") {
      // 막지는 않는다 — 검토를 마쳤다면 정당한 상태다. 다만 눈에 띄게 남긴다.
      issues.push("AI 초안을 '완성'으로 표시했습니다. 사실 관계와 정답 성립을 직접 확인했는지 다시 보세요.");
    }

    $("qCheckResult").innerHTML = issues.length
      ? `<div class="nz-issues err"><b>검토 결과 ${issues.length}건</b><ul>${issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul></div>`
      : '<div class="nz-issues ok">검토 통과 — 저장할 수 있습니다.</div>';
  };

  /* ---------- 발문 루틴 ---------- */
  // 자주 쓰는 발문. 배점은 위에서 정한 값을 자동으로 붙인다.
  const ROUTINES = [
    { g: "합답형", t: "이에 대한 설명으로 옳은 것만을 〈보기〉에서 있는 대로 고른 것은?" },
    { g: "합답형", t: "이에 대한 설명으로 옳지 않은 것만을 〈보기〉에서 있는 대로 고른 것은?" },
    { g: "합답형", t: "이에 대한 설명으로 옳은 것만을 〈보기〉에서 모두 고른 것은?" },
    { g: "정답형", t: "이에 대한 설명으로 옳은 것은?" },
    { g: "정답형", t: "이에 대한 설명으로 옳지 않은 것은?" },
    { g: "정답형", t: "이에 대한 설명으로 가장 적절한 것은?" },
    { g: "자료형", t: "그림에 대한 설명으로 옳은 것은?" },
    { g: "자료형", t: "실험 결과에 대한 해석으로 옳은 것만을 〈보기〉에서 있는 대로 고른 것은?" },
  ];

  EP.toggleRoutines = function () {
    const box = $("routineList");
    if (!box) return;
    if (box.classList.contains("hidden")) {
      const pt = parseFloat($("qpoints").value) || 0;
      const suffix = pt ? " (" + pt + "점)" : "";
      box.innerHTML = ROUTINES.map((r, i) =>
        '<div class="nz-routine" role="button" tabindex="0" onclick="EP.applyRoutine(' + i + ')"' +
        ' onkeydown="EP.activateOnKey(event, () => EP.applyRoutine(' + i + '))"><b>' + r.g + "</b>" +
        esc(r.t + suffix) + "</div>").join("");
    }
    box.classList.toggle("hidden");
  };

  EP.applyRoutine = function (i) {
    const pt = parseFloat($("qpoints").value) || 0;
    const suffix = pt ? " (" + pt + "점)" : "";
    $("qask").value = ROUTINES[i].t + suffix;
    $("routineList").classList.add("hidden");
    // 부정 발문을 고르면 표시도 같이 켠다 — 둘이 어긋나면 검토에서 걸린다
    $("isNeg").checked = /않은|아닌|틀린/.test(ROUTINES[i].t);
  };

  /* ---------- 저장 전 체크리스트 ---------- */
  // 교사가 실제로 확인해야 하는 항목. '완성'으로 저장하려면 모두 체크하고 근거를 적어야 한다.
  const CHECKS = [
    { k: "curriculum", t: "교육과정을 벗어나지 않는가" },
    { k: "standard", t: "성취기준에 부합하는가" },
    { k: "textbook", t: "교과서(또는 수업)에서 다룬 내용인가" },
    { k: "single", t: "복수정답 가능성이 없는가" },
    { k: "clear", t: "발문이 모호하지 않은가" },
    { k: "typo", t: "오탈자·기호를 확인했는가" },
  ];

  EP.renderChecklist = function () {
    const box = $("checkBox");
    if (!box) return;
    box.innerHTML = CHECKS.map((c) =>
      '<label class="nz-chkrow"><input type="checkbox" ' + (S.checkState[c.k] ? "checked" : "") +
      " onchange=\"EP.setCheck('" + c.k + "', this.checked)\" /><span>" + esc(c.t) + "</span></label>").join("") +
      '<div class="nz-fr" style="margin-top:6px"><label>확인 근거</label>' +
      '<textarea id="checkNote" rows="2" placeholder="무엇을 근거로 확인했는지 (예: 교과서 p.104, 3/12 수업에서 다룸)"' +
      ' oninput="EP.setCheckNote(this.value)">' + esc(S.checkState.note || "") + "</textarea></div>" +
      '<p class="nz-sub" id="checkStat"></p>';
    updateCheckStat();
  };
  EP.setCheck = function (k, v) { S.checkState[k] = v; updateCheckStat(); };
  EP.setCheckNote = function (v) { S.checkState.note = v; };

  function updateCheckStat() {
    const done = CHECKS.filter((c) => S.checkState[c.k]).length;
    const el = $("checkStat");
    if (el) {
      el.innerHTML = done === CHECKS.length
        ? '<span class="g">확인 완료 — 근거를 적고 저장하세요</span>'
        : "확인 " + done + "/" + CHECKS.length + " · '완성'으로 저장하려면 모두 확인해야 합니다";
    }
    const summary = $("questionReviewSummary");
    if (summary) summary.textContent = done === CHECKS.length ? "검토 완료 6/6" : `${CHECKS.length - done}개 확인 필요`;
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (localStorage.getItem("ep_question_settings_default_closed_v1") !== "1") {
      localStorage.removeItem("ep_question_settings_open");
      localStorage.setItem("ep_question_settings_default_closed_v1", "1");
    }
    EP.setQuestionSection("settings", localStorage.getItem("ep_question_settings_open") === "1");
    EP.setQuestionSection("review", localStorage.getItem("ep_question_review_open") === "1");
    if (localStorage.getItem("ep_authoring_right_folded") === "1") EP.toggleAuthoringRight(true);
    EP.toggleAuthoringPreview(localStorage.getItem("ep_authoring_preview_collapsed") === "1");
    EP.loadQuestionPaletteOptions(S.paletteTemplate || "");
    ["qtype", "qpalette", "qdiff", "qpoints"].forEach((id) => {
      const el = $(id);
      if (el) el.addEventListener("change", EP.updateQuestionSettingsSummary);
    });
    document.addEventListener("fullscreenchange", () => {
      const button = $("questionFullscreenBtn");
      const active = !!document.fullscreenElement;
      document.body.classList.toggle("question-fullscreen-active", active);
      if (!active) document.body.classList.remove("question-fullscreen-fallback");
      if (button) {
        button.textContent = active ? "⛶ 전체화면 종료" : "⛶ 전체화면";
        button.setAttribute("aria-pressed", active ? "true" : "false");
        button.title = active ? "문항 설계 전체화면 종료" : "문항 설계 전체화면";
      }
    });
    document.addEventListener("pointerdown", (event) => {
      const wrap = event.target.closest && event.target.closest(".nz-stdwrap");
      if (!wrap && $("stdList")) $("stdList").classList.add("hidden");
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && $("stdList")) $("stdList").classList.add("hidden");
      if (event.key === "Escape" && !document.fullscreenElement
          && document.body.classList.contains("question-fullscreen-active")) {
        document.body.classList.remove("question-fullscreen-active", "question-fullscreen-fallback");
        const button = $("questionFullscreenBtn");
        if (button) {
          button.textContent = "⛶ 전체화면";
          button.setAttribute("aria-pressed", "false");
          button.title = "문항 설계 전체화면";
        }
      }
    });
  });

  /* ---------- 참고 기출 (문항 설계 안에서) ---------- */
  // 이 문항을 만들 때 참고한 기출들. 성취기준 아래 버튼으로 놓고, 누르면 옆에서 바로 본다.
  let refLoadSequence = 0;
  EP.loadRefs = async function (questionId) {
    const sequence = ++refLoadSequence;
    const params = new URLSearchParams();
    if (questionId) params.set("question_id", questionId);
    else if (S.authoringSessionId) params.set("authoring_session_id", S.authoringSessionId);
    else { S.refs = []; EP.renderRefs(); return; }
    const rows = await api("/api/exam-refs?" + params);
    if (sequence !== refLoadSequence) return;
    S.refs = rows;
    EP.renderRefs();
  };

  EP.renderRefs = function () {
    const bar = $("refBar");
    if (!bar) return;
    bar.innerHTML = S.refs.length ? S.refs.map((r) => `
      <span class="nz-refbtn ${S.curRefId === r.id ? "on" : ""}" role="button" tabindex="0"
            onclick="EP.showRef(${r.id})" onkeydown="EP.activateOnKey(event, () => EP.showRef(${r.id}))">
        <b>${r.item_num}번</b>${esc(r.doc_title)}
        ${r.note ? `<span class="memo">${esc(r.note)}</span>` : ""}
        <button type="button" class="x" aria-label="참고 자료 삭제" onclick="event.stopPropagation();EP.delRef(${r.id})">×</button>
      </span>`).join("")
      : '<span class="nz-refempty">왼쪽 검색 결과에서 참고할 영역을 지정하면 참고 자료로 연결됩니다.</span>';
  };

  /** 참고 기출 버튼 → 오른쪽 패널에 그 문항과 메모를 띄운다 */
  EP.showRef = function (refId) {
    const r = S.refs.find((x) => x.id === refId);
    if (!r) return;
    S.curRefId = refId;
    EP.renderRefs();
    $("evViewTitle").textContent = `${r.doc_title} — ${r.page_no}p ${r.item_num}번 (참고 자료)`;
    $("evViewBody").innerHTML = `
      <div class="nz-refview">
        <img src="/api/documents/${r.document_id}/page/${r.page_no}/item/${r.item_num}/image?dpi=130" />
      </div>
      <div class="nz-refnote">
        <div class="nz-fr" style="margin:0">
          <label>메모</label>
          <textarea id="refNote" rows="2" placeholder="변형 아이디어·주의점"
                    oninput="EP.refNoteChanged(${r.id})">${esc(r.note || "")}</textarea>
          <span class="nz-sub" id="refSaved" style="margin:0;min-width:44px"></span>
        </div>
      </div>`;
  };

  let refTimer = null;
  EP.refNoteChanged = function (refId) {
    $("refSaved").textContent = "저장 중…";
    clearTimeout(refTimer);
    refTimer = setTimeout(async () => {
      const note = $("refNote").value;
      await EP.patch(`/api/exam-refs/${refId}`, { note });
      const r = S.refs.find((x) => x.id === refId);
      if (r) r.note = note;
      $("refSaved").textContent = "저장됨";
      EP.renderRefs();
    }, 500);
  };

  EP.delRef = async function (refId) {
    await del(`/api/exam-refs/${refId}`);
    S.refs = S.refs.filter((r) => r.id !== refId);
    if (S.curRefId === refId) S.curRefId = null;
    EP.renderRefs();
  };

  /* ---------- 기출: 문항 하나씩 보기 ---------- */
  EP.ensureDocTypes = async function () {
    if (Object.keys(S.docTypes).length) return S.docTypes;
    const docs = await api("/api/documents");
    docs.forEach((d) => { S.docTypes[d.id] = d.doc_type; });
    return S.docTypes;
  };

  /** 기출 페이지 → 검색어가 들어있는 문항만 하나씩 크게 */
  EP.showExamItems = async function (docId, pageNo, title, seq) {
    const q = S.evViewer.q || "";
    const r = await api(`/api/documents/${docId}/page/${pageNo}/items?q=` + encodeURIComponent(q));
    if (seq !== undefined && EP.evStale(seq)) return true;   // 늦게 온 응답은 그리지 않는다
    const hits = r.items.filter((x) => x.has_hit);
    const list = hits.length ? hits : r.items;      // 적중 없으면 그 페이지 문항 전부
    if (!list.length) return false;                 // 문항을 못 찾으면 페이지 전체로

    $("evViewTitle").textContent =
      `${title} — ${pageNo}p · ${hits.length ? "검색어 포함 " + hits.length + "문항" : r.items.length + "문항"}`;
    $("evViewBody").innerHTML = list.map((x) => {
      const badge = Object.entries(x.hits).map(([t]) =>
        `<span class="nz-termchip"><span class="sw" style="background:${
          EP.HL_COLORS[(S.evViewer.terms || []).indexOf(t) % EP.HL_COLORS.length]}"></span>${esc(t)}</span>`).join("");
      return `<div class="nz-examitem">
        <div class="nz-examhead"><b>${x.num}번</b>${badge}
          <button class="nz-tb mini" style="margin-left:auto"
            onclick="EP.useExam(${docId}, ${pageNo}, ${x.num}, '${esc(title)}')">참고로 기록</button>
        </div>
        <div class="nz-itemwrap">
          <img src="/api/documents/${docId}/page/${pageNo}/item/${x.num}/image?dpi=120"
               alt="${esc(title)} ${x.num}번" loading="lazy" />
          ${(x.boxes || []).map((b) =>
            `<div class="nz-hl" title="${esc(b.term)}" style="left:${b.x * 100}%;top:${
              b.y * 100}%;width:${b.w * 100}%;height:${b.h * 100}%;background:${
              EP.HL_COLORS[b.color_idx]}"></div>`).join("")}
        </div>
      </div>`;
    }).join("");
    return true;
  };

  /** 참고한 기출을 담는다. 출제 의도는 건드리지 않는다 — 참고 기출은 버튼으로만 관리한다. */
  EP.useExam = async function (docId, pageNo, num, title) {
    const r = await post("/api/exam-refs", {
      document_id: docId, doc_title: title, page_no: pageNo, item_num: num, note: "",
      question_id: S.editingQid || null,
      authoring_session_id: S.editingQid ? null : S.authoringSessionId,
    });
    // '참고로 기록'한 기출은 북마크에만 남기지 않고 현재 작성 세션의
    // 레퍼런스 패키지에도 즉시 연결한다. 생성 모델은 문항 이미지와 해당 PDF
    // 페이지의 추출 텍스트를 함께 받는다.
    if (EP.authoringAddReferenceData) {
      try {
        const response = await fetch(`/api/documents/${docId}/page/${pageNo}/item/${num}/image?dpi=150`);
        if (!response.ok) throw new Error("기출 문항 이미지를 읽지 못했습니다.");
        const blob = await response.blob();
        const dataUrl = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result);
          reader.onerror = reject;
          reader.readAsDataURL(blob);
        });
        await EP.authoringAddReferenceData({
          filename: `exam_${docId}_${pageNo}_${num}.png`, data_url: dataUrl,
          source_label: `${title} — ${pageNo}p ${num}번`,
          source_text: `선택한 기출 문항: ${title} ${num}번`, usage: "both",
          source_meta: { document_id: docId, page_no: pageNo, item_num: num, kind: "기출" },
        });
      } catch (error) {
        alert("기출은 기록했지만 제작 레퍼런스 연결에 실패했습니다: " + error.message);
      }
    }
    await EP.loadRefs(S.editingQid);
    EP.showRef(r.id);      // 담자마자 오른쪽에 뜨고, 메모는 거기서 적는다
  };
})(window.EP);
