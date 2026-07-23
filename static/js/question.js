/* ===== 문항 설계 — 보기·선지 조립, 근거 뷰어, 참고 기출, 저장 전 검토 ===== */
(function (EP) {
  "use strict";
  const $ = EP.$, esc = EP.esc, api = EP.api, post = EP.post, put = EP.put, del = EP.del;
  const S = EP.S;

  /* ---------- 유형 ---------- */
  EP.onTypeChange = function () {
    const hap = $("qtype").value === "합답형";
    $("bogiBox").classList.toggle("hidden", !hap);
    $("presetBox").classList.toggle("hidden", !hap);     // 정답형엔 프리셋 없음
    $("imgChoiceBox").classList.toggle("hidden", hap);   // 그림 선지는 정답형만
    if (hap && !S.bogi.length) { EP.addBogi(); EP.addBogi(); EP.addBogi(); }
    EP.renderChoices();
  };

  /* ---------- 보기 (ㄱㄴㄷ) ---------- */
  EP.addBogi = function () {
    if (S.bogi.length >= 5) return;
    S.bogi.push({ label: EP.LABELS[S.bogi.length], text: "", proposition_id: null, variant_id: null });
    EP.renderBogi();
  };
  EP.renderBogi = function () {
    $("bogiRows").innerHTML = S.bogi.map((b, i) => `
      <div class="nz-fr">
        <label>${b.label}</label>
        <input value="${esc(b.text)}" oninput="EP.setBogi(${i}, this.value)" placeholder="보기 문장" />
        <button class="nz-tb mini" onclick="EP.pickFor('bogi', ${i})">명제</button>
        <span class="nz-tag ${b.proposition_id || b.variant_id ? "g" : ""}">${b.proposition_id ? "명제" : b.variant_id ? "변형" : "직접"}</span>
        <button class="nz-tb mini" onclick="EP.delBogi(${i})">×</button>
      </div>`).join("");
  };
  EP.setBogi = function (i, v) { S.bogi[i].text = v; };
  EP.delBogi = function (i) {
    S.bogi.splice(i, 1);
    S.bogi.forEach((b, j) => { b.label = EP.LABELS[j]; });
    EP.renderBogi(); EP.renderChoices();
  };

  /* ---------- 선지 ---------- */
  EP.addChoice = function () {
    if (S.choices.length >= 5) return;
    S.choices.push({ ord: S.choices.length + 1, text: "", proposition_id: null, variant_id: null,
                     combo: null, custom_evidence: "", is_answer: false });
    EP.renderChoices();
  };
  EP.renderChoices = function () {
    const hap = $("qtype").value === "합답형";
    const img = $("imgChoices") && $("imgChoices").checked;
    $("choiceRows").innerHTML = S.choices.map((c, i) => `
      <div class="nz-fr">
        <label>${"①②③④⑤"[i] || i + 1}</label>
        ${hap
          ? `<input value="${esc((c.combo || []).join(", "))}" oninput="EP.setCombo(${i}, this.value)" placeholder="예: ㄱ, ㄷ" />`
          : `<input value="${esc(c.text)}" oninput="EP.setChoice(${i}, this.value)"
               placeholder="${img ? "그림 파일명 (예: 보기1.png)" : "선지 문장"}" />`}
        ${hap ? "" : `<button class="nz-tb mini" onclick="EP.pickFor('choice', ${i})">명제</button>`}
        <span class="nz-tag ${c.proposition_id || c.variant_id || c.custom_evidence || (c.combo && c.combo.length) ? "g" : "r"}">
          ${c.proposition_id ? "명제" : c.variant_id ? "변형" : (c.combo && c.combo.length) ? "조합" : c.custom_evidence ? "직접근거" : "근거없음"}</span>
        <label class="nz-lb"><input type="radio" name="ans" ${c.is_answer ? "checked" : ""} onchange="EP.setAnswer(${i})" /> 정답</label>
        <button class="nz-tb mini" onclick="EP.delChoice(${i})">×</button>
      </div>`).join("");
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
      `<button class="nz-preset" onclick="EP.applyPreset('${k}')" title="${esc(p.preview)}">
         <b>${esc(p.name)}</b><span>${esc(p.preview)} · ${esc(p.desc)}</span></button>`).join(" ");
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
    const q = $("pickSearch").value.trim();
    let rows = await api("/api/propositions?" + new URLSearchParams(q ? { q } : {}));
    const allowPk = EP.allowedCodes();
    if (allowPk) rows = rows.filter((r) => allowPk.has(r.standard_code));
    $("pickerList").innerHTML = rows.slice(0, 20).map((r) => `
      <div class="nz-hit">
        <div class="nz-hit-snip">${esc(r.text)}</div>
        <div class="nz-hit-src">${esc(r.standard_code)} · 근거 ${r.ev_count} · 변형 ${r.var_count}</div>
        <button class="nz-tb mini" onclick="EP.useProp(${r.id}, ${JSON.stringify(r.text).replace(/'/g, "&#39;")})">보기/선지로</button>
      </div>`).join("") || '<p class="nz-sub">명제가 없습니다.</p>';
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
    const groups = {};
    items.forEach((it) => { (groups[it.doc_title] ||= []).push(it); });
    $("evList").innerHTML = items.length
      ? `<div class="nz-hlbar" style="padding:6px 10px"><b>${r.total}</b>개 일치 · ${items.length}개 표시${S.evSrc ? " · " + S.evSrc : ""}</div>` +
        Object.entries(groups).map(([title, list]) => `
        <div class="nz-docgroup">
          <div class="nz-docgroup-head">${esc(title)} <span class="n">${list.length}개</span></div>
          ${list.map((h) => `
            <div class="nz-res" onclick="EP.evShow(${h.document_id}, ${h.page_no}, this)">
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
  EP.evShow = async function (docId, pageNo, el) {
    const seq = ++EP.evSeq;
    document.querySelectorAll("#evList .nz-res.on").forEach((n) => n.classList.remove("on"));
    if (el) el.classList.add("on");
    S.evViewer.docId = docId; S.evViewer.page = pageNo;
    S.evViewer.terms = EP.splitTerms(S.evViewer.q || "");

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
    $("evFoldBtn").textContent = folded ? "◀" : "▶";
    localStorage.setItem("ep_side_folded", folded ? "1" : "");
  };
  EP.togglePicker = function () { $("pickerList").classList.toggle("hidden"); };

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
      answer: (() => { const i = S.choices.findIndex((c) => c.is_answer); return i >= 0 ? "①②③④⑤"[i] : ""; })(),
      default_points: parseFloat($("qpoints").value) || 3,
      difficulty: $("qdiff").value,
      standard_code: EP.stdValue() || null,
      intent: $("qintent").value,
      behavior: $("qbehavior") ? $("qbehavior").value : "",
      image_choices: $("imgChoices") ? $("imgChoices").checked : false,
      status: $("qstatus") ? $("qstatus").value : "초안",
      review_note: JSON.stringify(S.checkState),
      choices: S.choices,
    };
  };

  EP.saveQuestion = async function () {
    const q = EP.collectQuestion();
    if (!q.ask.trim()) return alert("발문을 입력하세요.");
    if (!q.choices.length) return alert("선지를 추가하세요.");
    if (q.status === "완성") {
      const missing = CHECKS.filter((c) => !S.checkState[c.k]);
      if (missing.length) return alert("'완성'으로 저장하려면 확인 항목을 모두 체크해야 합니다.\n\n미확인: "
        + missing.map((m) => m.t).join(", "));
      if (!(S.checkState.note || "").trim()) return alert("확인 근거를 적어주세요.");
    }
    let qid = S.editingQid;
    if (S.editingQid) { await put(`/api/questions/${S.editingQid}`, q); }
    else { const r = await post("/api/questions", q); qid = r.id; }
    // 이 문항을 만들며 담은 참고 기출을 문항에 연결한다
    for (const rf of S.refs.filter((x) => !x.question_id)) {
      await EP.patch(`/api/exam-refs/${rf.id}`, { question_id: qid });
    }
    EP.resetQuestionForm(); EP.loadQuestions();
    alert("문항을 저장했습니다.");
  };

  EP.resetQuestionForm = function () {
    S.editingQid = null; S.bogi = []; S.choices = []; S.checkState = {};
    EP.renderChecklist();
    S.refs = []; S.curRefId = null; EP.renderRefs();
    ["qtitle", "qpassage", "qmaterial", "qask", "qintent"].forEach((id) => { $(id).value = ""; });
    EP.setStdValue("");
    $("isNeg").checked = false; $("qpoints").value = "3";
    if ($("qbehavior")) $("qbehavior").value = "";
    EP.renderBogi(); EP.renderChoices(); $("qCheckResult").innerHTML = "";
  };

  /** 저장 전 초안 검토: 서버 규칙과 같은 항목을 클라이언트에서 미리 본다 */
  EP.checkQuestionDraft = function () {
    const q = EP.collectQuestion();
    const issues = [];
    if (!q.ask.trim()) issues.push("발문(질문)이 비어 있습니다.");
    if (q.choices.length < 2) issues.push("선지가 2개 미만입니다.");
    if (!q.choices.some((c) => c.is_answer)) issues.push("정답이 지정되지 않았습니다.");
    q.choices.forEach((c, i) => {
      const ok = c.proposition_id || c.variant_id || c.custom_evidence || (c.combo && c.combo.length);
      if (!ok) issues.push(`${i + 1}번 선지에 근거가 없습니다.`);
    });
    if (!q.standard_code) issues.push("성취기준이 선택되지 않았습니다.");

    // 형식 너머 — 서버 checklist.py 와 같은 기준
    const NEG = ["않은", "아닌", "틀린", "옳지 않", "적절하지 않"];
    const looksNeg = NEG.some((w) => q.ask.includes(w));
    if (looksNeg && !q.is_negative) issues.push("발문이 부정형인데 '부정 문항' 표시가 꺼져 있습니다.");
    if (q.is_negative && !looksNeg) issues.push("'부정 문항'으로 표시했는데 발문에 부정어가 없습니다.");
    if (!q.behavior) issues.push("행동영역이 비어 있습니다 (이원목적분류표에 필요).");

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
        '<div class="nz-routine" onclick="EP.applyRoutine(' + i + ')"><b>' + r.g + "</b>" +
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
  }

  /* ---------- 참고 기출 (문항 설계 안에서) ---------- */
  // 이 문항을 만들 때 참고한 기출들. 성취기준 아래 버튼으로 놓고, 누르면 옆에서 바로 본다.
  EP.loadRefs = async function (questionId) {
    const all = await api("/api/exam-refs");
    S.refs = questionId ? all.filter((r) => r.question_id === questionId)
                        : all.filter((r) => !r.question_id);   // 아직 저장 안 한 문항의 임시 스크랩
    EP.renderRefs();
  };

  EP.renderRefs = function () {
    const bar = $("refBar");
    if (!bar) return;
    bar.innerHTML = S.refs.length ? S.refs.map((r) => `
      <span class="nz-refbtn ${S.curRefId === r.id ? "on" : ""}" onclick="EP.showRef(${r.id})">
        <b>${r.item_num}번</b>${esc(r.doc_title)}
        ${r.note ? `<span class="memo">${esc(r.note)}</span>` : ""}
        <span class="x" onclick="event.stopPropagation();EP.delRef(${r.id})">×</span>
      </span>`).join("")
      : '<span class="nz-refempty">기출 검색에서 “참고로 기록”을 누르면 여기 버튼으로 담깁니다.</span>';
  };

  /** 참고 기출 버튼 → 오른쪽 패널에 그 문항과 메모를 띄운다 */
  EP.showRef = function (refId) {
    const r = S.refs.find((x) => x.id === refId);
    if (!r) return;
    S.curRefId = refId;
    EP.renderRefs();
    $("evViewTitle").textContent = `${r.doc_title} — ${r.page_no}p ${r.item_num}번 (참고 기출)`;
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
        <img src="/api/documents/${docId}/page/${pageNo}/item/${x.num}/image?dpi=120"
             alt="${esc(title)} ${x.num}번" loading="lazy" />
      </div>`;
    }).join("");
    return true;
  };

  /** 참고한 기출을 담는다. 출제 의도는 건드리지 않는다 — 참고 기출은 버튼으로만 관리한다. */
  EP.useExam = async function (docId, pageNo, num, title) {
    const r = await post("/api/exam-refs", {
      document_id: docId, doc_title: title, page_no: pageNo, item_num: num, note: "",
    });
    await EP.loadRefs(S.editingQid);
    EP.showRef(r.id);      // 담자마자 오른쪽에 뜨고, 메모는 거기서 적는다
  };
})(window.EP);
