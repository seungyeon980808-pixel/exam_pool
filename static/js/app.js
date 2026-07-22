/* ===== ExamPool — Phase 1 ===== */
const EP = (() => {
  let standards = [];
  let curProps = [];        // 현재 조회된 명제
  let bogi = [];            // 문항 설계: 보기 [{label,text,proposition_id,variant_id}]
  let choices = [];         // 문항 설계: 선지
  let editingQid = null;
  let curSetId = null;

  const LABELS = ["ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ"];

  async function api(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) throw new Error((await res.text()).slice(0, 300));
    return res.json();
  }
  const post = (url, body) => api(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const put = (url, body) => api(url, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const del = (url) => api(url, { method: "DELETE" });
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const $ = (id) => document.getElementById(id);

  /* ---------- 공통: 성취기준 ---------- */
  async function loadStandards() {
    standards = await api("/api/standards");
    renderTree();
    const opts = standards.flatMap((u) => u.standards.map((s) =>
      ({ v: s.code, t: `${s.code} ${s.text.slice(0, 20)}…` })));
    fillSelect($("q-std"), opts, "전체");
    fillSelect($("f-std"), opts);
    fillSelect($("q-std2"), opts);
    const subj = await api("/api/subject");
    $("subjectLabel").textContent = `${subj.subject} · 성취기준 ${subj.standard_count}개`;
  }

  function fillSelect(sel, opts, firstLabel) {
    if (!sel) return;
    sel.innerHTML = firstLabel ? `<option value="">${firstLabel}</option>` : "";
    opts.forEach((o) => sel.appendChild(new Option(o.t, o.v)));
  }

  function toggleUnit(unitNo, el) {
    let next = el.nextElementSibling;
    if (next && next.classList.contains("sub")) {
      while (next && next.classList.contains("sub")) { const rm = next; next = next.nextElementSibling; rm.remove(); }
      return;
    }
    const unit = standards.find((u) => u.unit_no === unitNo);
    const frag = document.createDocumentFragment();
    unit.standards.forEach((s) => {
      const e = document.createElement("div");
      e.className = "nz-mi sub";
      e.title = `${s.code} ${s.text}`;   // 전문 툴팁
      e.innerHTML = `<span class="code">${esc(s.code)}</span><span class="txt">${esc(s.text)}</span>`;
      e.onclick = (ev) => { ev.stopPropagation(); pickStandard(s, e); };
      frag.appendChild(e);
    });
    el.after(frag);
  }

  /** 성취기준 선택 — 목록 필터 + 전문 배너 */
  function pickStandard(s, el) {
    document.querySelectorAll(".nz-mi.sub.on").forEach((n) => n.classList.remove("on"));
    if (el) el.classList.add("on");
    $("q-std").value = s.code;
    const b = $("stdBanner");
    b.classList.remove("hidden");
    b.innerHTML = `<b>${esc(s.code)}</b>${esc(s.text)}`;
    loadProps();
  }

  /** 좌측 트리 검색 — 단원명·성취기준 코드/내용 모두 */
  function filterTree() {
    const q = $("treeFilter").value.trim().toLowerCase();
    const tree = $("tree");
    tree.innerHTML = "";
    if (!q) { renderTree(); return; }
    standards.forEach((u) => {
      const unitHit = `${u.unit_no}. ${u.name}`.toLowerCase().includes(q);
      const hits = u.standards.filter((s) =>
        s.code.toLowerCase().includes(q) || s.text.toLowerCase().includes(q));
      if (!unitHit && !hits.length) return;
      const uEl = document.createElement("div");
      uEl.className = "nz-mi";
      uEl.textContent = `${u.unit_no}. ${u.name}`;
      tree.appendChild(uEl);
      (hits.length ? hits : u.standards).forEach((s) => {
        const e = document.createElement("div");
        e.className = "nz-mi sub";
        e.title = `${s.code} ${s.text}`;
        e.innerHTML = `<span class="code">${esc(s.code)}</span><span class="txt">${esc(s.text)}</span>`;
        e.onclick = () => pickStandard(s, e);
        tree.appendChild(e);
      });
    });
  }

  function renderTree() {
    const tree = $("tree");
    tree.innerHTML = "";
    standards.forEach((u) => {
      const el = document.createElement("div");
      el.className = "nz-mi";
      el.textContent = `${u.unit_no}. ${u.name}`;
      el.onclick = () => toggleUnit(u.unit_no, el);
      tree.appendChild(el);
    });
  }

  /* ---------- 성취기준 전체보기 ---------- */
  function openStdTable() { $("stdModal").classList.remove("hidden"); renderStdTable(); }
  function closeStdTable() { $("stdModal").classList.add("hidden"); }
  async function renderStdTable() {
    const q = ($("stdModalFilter").value || "").trim().toLowerCase();
    const props = await api("/api/propositions");
    const countBy = {};
    props.forEach((p) => { countBy[p.standard_code] = (countBy[p.standard_code] || 0) + 1; });
    const rows = [];
    standards.forEach((u) => u.standards.forEach((s) => {
      if (q && !(s.code.toLowerCase().includes(q) || s.text.toLowerCase().includes(q)
        || u.name.toLowerCase().includes(q))) return;
      rows.push(`<tr>
        <td class="cc code">${esc(s.code)}</td>
        <td class="cc">${esc(u.name)}</td>
        <td>${esc(s.text)}</td>
        <td class="cc ${countBy[s.code] ? "g" : ""}">${countBy[s.code] || "-"}</td></tr>`);
    }));
    $("stdTableRows").innerHTML = rows.join("") ||
      '<tr class="nz-empty"><td colspan="4">일치하는 성취기준이 없습니다.</td></tr>';
  }

  /* ---------- 명제 은행 ---------- */
  async function loadProps() {
    const params = new URLSearchParams();
    const std = $("q-std").value, q = $("q-text").value.trim();
    if (std) params.set("standard", std);
    if (q) params.set("q", q);
    curProps = await api("/api/propositions?" + params);
    const tb = $("propRows");
    tb.innerHTML = "";
    let ev = 0, va = 0;
    if (!curProps.length) tb.innerHTML = '<tr class="nz-empty"><td colspan="8">조회된 명제가 없습니다. “＋ 명제 등록”으로 추가하세요.</td></tr>';
    curProps.forEach((r, i) => {
      ev += r.ev_count; va += r.var_count;
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td class="cc">${i + 1}</td><td class="cc code">${esc(r.standard_code)}</td>` +
        `<td>${esc(r.text)}</td><td class="cc">${esc(r.unit_name || "-")}</td>` +
        `<td class="cc ${r.ev_count ? "g" : ""}">${r.ev_count || "-"}</td>` +
        `<td class="cc ${r.var_count ? "r" : ""}">${r.var_count || "-"}</td>` +
        `<td class="cc ${r.class_verified ? "g" : ""}">${r.class_verified ? "✓" : "-"}</td>` +
        `<td class="cc"><button class="nz-tb mini" onclick="EP.openProp(${r.id})">상세</button>` +
        `<button class="nz-tb mini" onclick="EP.delProp(${r.id})">×</button></td>`;
      tb.appendChild(tr);
    });
    $("cnt").textContent = curProps.length;
    $("footL").textContent = `명제 ${curProps.length}건`;
    $("footR").textContent = `근거 ${ev}건 · 변형 ${va}건`;
  }

  function toggleForm(show) { $("propForm").classList.toggle("hidden", !show); if (show) $("f-text").focus(); }

  async function saveProp() {
    const text = $("f-text").value.trim(), standard_code = $("f-std").value;
    if (!text) return alert("명제 내용을 입력하세요.");
    if (!standard_code) return alert("성취기준을 선택하세요.");
    await post("/api/propositions", { text, standard_code, tags: $("f-tags").value });
    $("f-text").value = ""; $("f-tags").value = "";
    toggleForm(false); loadProps();
  }

  async function delProp(id) {
    if (!confirm("이 명제를 삭제할까요? (변형·근거도 함께 삭제됩니다)")) return;
    await del(`/api/propositions/${id}`); $("propDetail").classList.add("hidden"); loadProps();
  }

  /* ---------- 명제 상세: 변형·근거 ---------- */
  async function openProp(id) {
    const d = await api(`/api/propositions/${id}`);
    const dists = await api("/api/distortions");
    const box = $("propDetail");
    box.classList.remove("hidden");
    box.innerHTML = `
      <div class="nz-h" style="margin-top:14px"><b>명제 상세</b></div>
      <div class="nz-detail-body">
        <p class="nz-prop-text">${esc(d.proposition.text)}</p>
        <p class="nz-sub">${esc(d.proposition.standard_code)} · ${esc(d.proposition.unit_name || "")}
          ${d.proposition.class_verified ? '<span class="g">· 수업 확인됨</span>' : ""}</p>

        <p class="nz-sub" style="margin-top:10px">거짓 변형(오답 재료) ${d.variants.length}건</p>
        <table class="nz-t"><thead><tr><th style="width:100px">왜곡 유형</th><th>거짓 문장</th><th style="width:40px">삭제</th></tr></thead>
        <tbody>${d.variants.map((v) => `<tr><td class="cc">${esc(v.distortion)}</td><td>${esc(v.text)}</td>
          <td class="cc"><button class="nz-tb mini" onclick="EP.delVariant(${v.id},${id})">×</button></td></tr>`).join("")
          || '<tr class="nz-empty"><td colspan="3">아직 없습니다.</td></tr>'}</tbody></table>
        <div class="nz-fr" style="margin-top:6px">
          <select id="v-dist" style="max-width:130px">${dists.map((x) => `<option>${x}</option>`).join("")}</select>
          <input id="v-text" placeholder="원본 명제를 비튼 거짓 문장" />
          <button class="nz-tb blu" onclick="EP.addVariant(${id})">변형 추가</button>
        </div>

        <p class="nz-sub" style="margin-top:14px">근거 ${d.evidence.length}건</p>
        <table class="nz-t"><thead><tr><th style="width:70px">종류</th><th style="width:180px">출처</th><th>원문 인용</th><th style="width:40px">삭제</th></tr></thead>
        <tbody>${d.evidence.map((e) => `<tr><td class="cc">${esc(e.source_type)}</td><td>${esc(e.source_label)}</td>
          <td>${esc(e.quote)}</td><td class="cc"><button class="nz-tb mini" onclick="EP.delEvidence(${e.id},${id})">×</button></td></tr>`).join("")
          || '<tr class="nz-empty"><td colspan="4">아직 없습니다. 아래에서 교과서를 검색해 붙이세요.</td></tr>'}</tbody></table>

        <div class="nz-fr" style="margin-top:6px">
          <input id="pev-q" placeholder="교과서·교육과정 검색 (예: 빛 굴절)"
            onkeydown="if(event.key==='Enter')EP.searchEvidenceFor(${id})" />
          <button class="nz-tb" onclick="EP.searchEvidenceFor(${id})">근거 검색</button>
          <button class="nz-tb" onclick="EP.markVerified(${id})">수업에서 다룸 표시</button>
        </div>
        <div id="pevList" class="nz-picker"></div>
      </div>`;
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function addVariant(pid) {
    const text = $("v-text").value.trim();
    if (!text) return alert("거짓 문장을 입력하세요.");
    await post("/api/variants", { proposition_id: pid, text, distortion: $("v-dist").value });
    openProp(pid); loadProps();
  }
  async function delVariant(vid, pid) { await del(`/api/variants/${vid}`); openProp(pid); loadProps(); }
  async function delEvidence(eid, pid) { await del(`/api/evidence/${eid}`); openProp(pid); loadProps(); }
  async function markVerified(pid) { await api(`/api/propositions/${pid}/class-verified?value=true`, { method: "PATCH" }); openProp(pid); loadProps(); }

  async function searchEvidenceFor(pid) {
    const q = $("pev-q").value.trim();
    if (!q) return;
    const r = await api("/api/evidence/search?q=" + encodeURIComponent(q) + "&limit=12");
    $("pevList").innerHTML = r.items.length ? r.items.map((h) => `
      <div class="nz-hit">
        <div class="nz-hit-src"><span class="nz-pct ${h.match_pct < 60 ? "low" : ""}">${h.match_pct}%</span>
          ${esc(h.source_label)}</div>
        <div class="nz-hit-snip">${mark(h.snippet)}</div>
        <div style="display:flex;gap:4px;margin-top:5px">
          <button class="nz-tb mini" onclick='EP.attachEvidence(${pid}, ${JSON.stringify(h).replace(/'/g, "&#39;")})'>근거로 저장</button>
          <button class="nz-tb mini" onclick="EP.peekPage(${h.document_id}, ${h.page_no}, '${esc(q)}')">원문 보기</button>
        </div>
      </div>`).join("") : '<p class="nz-sub">검색 결과가 없습니다. 근거 문서 탭에서 교과서를 인덱싱했는지 확인하세요.</p>';
  }

  /** 어디서든 PDF 페이지 원문을 크게 띄운다 (모달, 하이라이트 포함) */
  async function peekPage(docId, pageNo, q) {
    const meta = await api(`/api/documents/${docId}/page/${pageNo}`);
    let m = $("peekModal");
    if (!m) {
      m = document.createElement("div");
      m.id = "peekModal"; m.className = "nz-modal";
      m.onclick = (e) => { if (e.target === m) m.classList.add("hidden"); };
      document.body.appendChild(m);
    }
    m.classList.remove("hidden");
    m.innerHTML = `<div class="nz-modal-box">
      <div class="nz-modal-head"><b>${esc(meta.title)} — ${pageNo}페이지</b>
        <button class="nz-tb" style="margin-left:auto" onclick="document.getElementById('peekModal').classList.add('hidden')">닫기</button>
      </div>
      <div class="nz-modal-body" style="background:#eef1f5;text-align:center">
        <div class="nz-pagewrap" id="peekWrap">
          <img id="peekImg" src="/api/documents/${docId}/page/${pageNo}/image?dpi=130" />
        </div>
      </div></div>`;
    if (!q) return;
    const hl = await api(`/api/documents/${docId}/page/${pageNo}/highlights?q=` +
      encodeURIComponent(q) + "&dpi=130");
    const wrap = $("peekWrap"), img = $("peekImg");
    const paint = () => {
      const scale = img.clientWidth / hl.page_w;
      hl.boxes.forEach((b) => {
        const d = document.createElement("div");
        d.className = "nz-hl";
        d.style.cssText = `left:${b.x * scale}px;top:${b.y * scale}px;` +
          `width:${b.w * scale}px;height:${b.h * scale}px;background:${HL_COLORS[b.color_idx]}`;
        wrap.appendChild(d);
      });
    };
    if (img.complete) paint(); else img.onload = paint;
  }

  async function attachEvidence(pid, hit) {
    await post("/api/evidence", {
      proposition_id: pid, source_type: "교과서",
      source_label: hit.source_label, quote: hit.snippet.replace(/[\[\]]/g, ""),
      document_page_id: null,
    });
    openProp(pid); loadProps();
  }

  function exportCsv() {
    if (!curProps.length) return alert("내보낼 명제가 없습니다.");
    const head = "성취기준,명제,단원,근거,변형,수업\n";
    const body = curProps.map((r) => [r.standard_code, `"${r.text.replace(/"/g, '""')}"`,
      r.unit_name || "", r.ev_count, r.var_count, r.class_verified ? "O" : ""].join(",")).join("\n");
    const blob = new Blob(["﻿" + head + body], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "명제목록.csv"; a.click();
  }

  /* ---------- 문항 설계 ---------- */
  function onTypeChange() {
    const hap = $("qtype").value === "합답형";
    $("bogiBox").classList.toggle("hidden", !hap);
    $("presetBox").classList.toggle("hidden", !hap);
    if (hap && !bogi.length) { addBogi(); addBogi(); addBogi(); }
    renderChoices();
  }

  function addBogi() {
    if (bogi.length >= 5) return;
    bogi.push({ label: LABELS[bogi.length], text: "", proposition_id: null, variant_id: null });
    renderBogi();
  }
  function renderBogi() {
    $("bogiRows").innerHTML = bogi.map((b, i) => `
      <div class="nz-fr">
        <label>${b.label}</label>
        <input value="${esc(b.text)}" oninput="EP.setBogi(${i}, this.value)" placeholder="보기 문장" />
        <span class="nz-tag ${b.proposition_id || b.variant_id ? "g" : ""}">${b.proposition_id ? "명제" : b.variant_id ? "변형" : "직접"}</span>
        <button class="nz-tb mini" onclick="EP.delBogi(${i})">×</button>
      </div>`).join("");
  }
  function setBogi(i, v) { bogi[i].text = v; }
  function delBogi(i) { bogi.splice(i, 1); bogi.forEach((b, j) => b.label = LABELS[j]); renderBogi(); renderChoices(); }

  function addChoice() {
    if (choices.length >= 5) return;
    choices.push({ ord: choices.length + 1, text: "", proposition_id: null, variant_id: null, combo: null, custom_evidence: "", is_answer: false });
    renderChoices();
  }
  function renderChoices() {
    const hap = $("qtype").value === "합답형";
    $("choiceRows").innerHTML = choices.map((c, i) => `
      <div class="nz-fr">
        <label>${"①②③④⑤"[i] || i + 1}</label>
        ${hap
          ? `<input value="${esc((c.combo || []).join(", "))}" oninput="EP.setCombo(${i}, this.value)" placeholder="예: ㄱ, ㄷ" />`
          : `<input value="${esc(c.text)}" oninput="EP.setChoice(${i}, this.value)" placeholder="선지 문장" />`}
        <span class="nz-tag ${c.proposition_id || c.variant_id || c.custom_evidence || (c.combo && c.combo.length) ? "g" : "r"}">
          ${c.proposition_id ? "명제" : c.variant_id ? "변형" : (c.combo && c.combo.length) ? "조합" : c.custom_evidence ? "직접근거" : "근거없음"}</span>
        <label class="nz-lb"><input type="radio" name="ans" ${c.is_answer ? "checked" : ""} onchange="EP.setAnswer(${i})" /> 정답</label>
        <button class="nz-tb mini" onclick="EP.delChoice(${i})">×</button>
      </div>`).join("");
  }
  function setChoice(i, v) { choices[i].text = v; }
  function setCombo(i, v) { choices[i].combo = v.split(",").map((s) => s.trim()).filter(Boolean); }
  function setAnswer(i) { choices.forEach((c, j) => c.is_answer = j === i); renderChoices(); }
  function delChoice(i) { choices.splice(i, 1); choices.forEach((c, j) => c.ord = j + 1); renderChoices(); }

  async function applyPreset(name) {
    const presets = await api("/api/combo-presets");
    choices = presets[name].map((combo, i) => ({
      ord: i + 1, text: "", proposition_id: null, variant_id: null,
      combo, custom_evidence: "", is_answer: false,
    }));
    renderChoices();
  }

  async function loadPicker() {
    const q = $("pickSearch").value.trim();
    const rows = await api("/api/propositions?" + new URLSearchParams(q ? { q } : {}));
    $("pickerList").innerHTML = rows.slice(0, 20).map((r) => `
      <div class="nz-hit">
        <div class="nz-hit-snip">${esc(r.text)}</div>
        <div class="nz-hit-src">${esc(r.standard_code)} · 근거 ${r.ev_count} · 변형 ${r.var_count}</div>
        <button class="nz-tb mini" onclick="EP.useProp(${r.id}, ${JSON.stringify(r.text).replace(/'/g, "&#39;")})">보기/선지로</button>
      </div>`).join("") || '<p class="nz-sub">명제가 없습니다.</p>';
  }

  function useProp(id, text) {
    if ($("qtype").value === "합답형") {
      const slot = bogi.find((b) => !b.text);
      if (!slot) { addBogi(); }
      const target = bogi.find((b) => !b.text);
      if (target) { target.text = text; target.proposition_id = id; renderBogi(); }
    } else {
      const slot = choices.find((c) => !c.text);
      if (!slot) addChoice();
      const target = choices.find((c) => !c.text);
      if (target) { target.text = text; target.proposition_id = id; renderChoices(); }
    }
  }

  async function searchEvidence() {
    const q = $("evSearch").value.trim();
    if (!q) return;
    const r = await api("/api/evidence/search?q=" + encodeURIComponent(q) + "&limit=12");
    $("evList").innerHTML = r.items.length ? r.items.map((h) => `
      <div class="nz-hit">
        <div class="nz-hit-src"><span class="nz-pct ${h.match_pct < 60 ? "low" : ""}">${h.match_pct}%</span>
          ${esc(h.source_label)}</div>
        <div class="nz-hit-snip">${mark(h.snippet)}</div>
        <button class="nz-tb mini" style="margin-top:5px"
          onclick="EP.peekPage(${h.document_id}, ${h.page_no}, '${esc(q)}')">원문 보기</button>
      </div>`).join("")
      : '<p class="nz-sub">결과 없음 — 근거 문서 탭에서 인덱싱하세요.</p>';
  }

  function collectQuestion() {
    return {
      qtype: $("qtype").value,
      is_negative: $("isNeg").checked,
      passage: $("qpassage").value,
      material: $("qmaterial").value,
      ask: $("qask").value,
      bogi_items: $("qtype").value === "합답형" ? bogi : [],
      answer: (() => { const i = choices.findIndex((c) => c.is_answer); return i >= 0 ? "①②③④⑤"[i] : ""; })(),
      default_points: parseFloat($("qpoints").value) || 3,
      difficulty: $("qdiff").value,
      standard_code: $("q-std2").value || null,
      intent: $("qintent").value,
      choices,
    };
  }

  async function saveQuestion() {
    const q = collectQuestion();
    if (!q.ask.trim()) return alert("발문을 입력하세요.");
    if (!q.choices.length) return alert("선지를 추가하세요.");
    if (editingQid) { await put(`/api/questions/${editingQid}`, q); }
    else { await post("/api/questions", q); }
    resetQuestionForm(); loadQuestions();
    alert("문항을 저장했습니다.");
  }

  function resetQuestionForm() {
    editingQid = null; bogi = []; choices = [];
    ["qpassage", "qmaterial", "qask", "qintent"].forEach((id) => $(id).value = "");
    $("isNeg").checked = false; $("qpoints").value = "3";
    renderBogi(); renderChoices(); $("qCheckResult").innerHTML = "";
  }

  async function checkQuestionDraft() {
    // 저장 전 초안 검토: 서버 규칙과 동일한 항목을 클라이언트에서 미리 본다
    const q = collectQuestion();
    const issues = [];
    if (!q.ask.trim()) issues.push("발문(질문)이 비어 있습니다.");
    if (q.choices.length < 2) issues.push("선지가 2개 미만입니다.");
    if (!q.choices.some((c) => c.is_answer)) issues.push("정답이 지정되지 않았습니다.");
    q.choices.forEach((c, i) => {
      const ok = c.proposition_id || c.variant_id || c.custom_evidence || (c.combo && c.combo.length);
      if (!ok) issues.push(`${i + 1}번 선지에 근거가 없습니다.`);
    });
    if (!q.standard_code) issues.push("성취기준이 선택되지 않았습니다.");
    $("qCheckResult").innerHTML = issues.length
      ? `<div class="nz-issues err"><b>검토 결과 ${issues.length}건</b><ul>${issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul></div>`
      : '<div class="nz-issues ok">검토 통과 — 저장할 수 있습니다.</div>';
  }

  async function loadQuestions() {
    const rows = await api("/api/questions");
    $("qcnt").textContent = rows.length;
    $("qRows").innerHTML = rows.map((r, i) => `
      <tr><td class="cc">${i + 1}</td><td class="cc">${esc(r.qtype)}${r.is_negative ? "·부정" : ""}</td>
      <td>${esc(r.ask)}</td><td class="cc code">${esc(r.standard_code || "-")}</td>
      <td class="cc">${esc(r.difficulty)}</td><td class="cc">${r.default_points}</td>
      <td class="cc"><button class="nz-tb mini" onclick="EP.editQuestion(${r.id})">수정</button>
      <button class="nz-tb mini" onclick="EP.checkQuestion(${r.id})">검토</button>
      <button class="nz-tb mini" onclick="EP.delQuestion(${r.id})">×</button></td></tr>`).join("")
      || '<tr class="nz-empty"><td colspan="7">문항이 없습니다.</td></tr>';
  }

  async function editQuestion(qid) {
    const d = await api(`/api/questions/${qid}`);
    editingQid = qid;
    const q = d.question;
    $("qtype").value = q.qtype; $("isNeg").checked = !!q.is_negative;
    $("qpassage").value = q.passage; $("qmaterial").value = q.material;
    $("qask").value = q.ask; $("qintent").value = q.intent;
    $("qpoints").value = q.default_points; $("qdiff").value = q.difficulty;
    $("q-std2").value = q.standard_code || "";
    bogi = q.bogi_items || [];
    choices = d.choices.map((c) => ({ ...c, is_answer: !!c.is_answer }));
    onTypeChange(); renderBogi(); renderChoices();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function checkQuestion(qid) {
    const r = await api(`/api/questions/${qid}/check`);
    alert(r.ok ? "검토 통과 (오류 없음)"
      : "검토 결과:\n" + r.issues.map((i) => `[${i.level}] ${i.message}`).join("\n"));
  }
  async function delQuestion(qid) {
    if (!confirm("이 문항을 삭제할까요?")) return;
    await del(`/api/questions/${qid}`); loadQuestions();
  }

  /* ---------- 세트 관리 ---------- */
  async function loadSets() {
    const sets = await api("/api/sets");
    fillSelect($("setSel"), sets.map((s) => ({ v: s.id, t: `${s.name} (${s.item_count}문항)` })), sets.length ? null : "세트 없음");
    if (sets.length) { $("setSel").value = curSetId || sets[0].id; loadSet(); }
  }
  async function createSet() {
    const name = $("newSetName").value.trim();
    if (!name) return alert("세트 이름을 입력하세요.");
    const r = await post("/api/sets", { name });
    curSetId = r.id; $("newSetName").value = "";
    await loadSets();
  }
  async function loadSet() {
    const sid = $("setSel").value;
    if (!sid) { $("setRows").innerHTML = ""; $("setDash").innerHTML = ""; return; }
    curSetId = sid;
    const d = await api(`/api/sets/${sid}`);
    const db_ = d.dashboard;
    $("setDash").innerHTML = `
      <div class="nz-mc"><p class="nz-mlbl">총 배점</p><p class="nz-mval">${db_.total_points}점</p></div>
      <div class="nz-mc"><p class="nz-mlbl">문항 수</p><p class="nz-mval">${db_.count}개</p></div>
      <div class="nz-mc"><p class="nz-mlbl">난이도 상·중·하</p><p class="nz-mval small">${db_.difficulty["상"]} · ${db_.difficulty["중"]} · ${db_.difficulty["하"]}</p></div>
      <div class="nz-mc"><p class="nz-mlbl">성취기준</p><p class="nz-mval small">${db_.standards.length}종</p></div>`;
    $("setRows").innerHTML = d.items.map((it, i) => `
      <tr draggable="true" data-qid="${it.question.id}" ondragstart="EP.dragStart(event,${it.question.id})"
          ondragover="event.preventDefault()" ondrop="EP.dropOn(event,${it.question.id})">
        <td class="cc grip">≡</td><td class="cc">${i + 1}</td>
        <td class="cc">${esc(it.question.qtype)}</td><td>${esc(it.question.ask)}</td>
        <td class="cc code">${esc(it.question.standard_code || "-")}</td>
        <td class="cc">${esc(it.question.difficulty)}</td>
        <td class="cc">${it.points ?? it.question.default_points}</td>
        <td class="cc"><button class="nz-tb mini" onclick="EP.removeItem(${it.item_id})">×</button></td>
      </tr>`).join("") || '<tr class="nz-empty"><td colspan="8">담긴 문항이 없습니다. 아래에서 담으세요.</td></tr>';
    $("setFootL").textContent = `문항 ${db_.count}개`;
    $("setFootR").textContent = `배점 합 ${db_.total_points}점 · 성취기준 ${db_.standards.length}종`;

    const qs = await api("/api/questions");
    const inSet = new Set(d.items.map((it) => it.question.id));
    $("setPickRows").innerHTML = qs.filter((q) => !inSet.has(q.id)).map((q, i) => `
      <tr><td class="cc">${i + 1}</td><td>${esc(q.ask)}</td>
      <td class="cc code">${esc(q.standard_code || "-")}</td><td class="cc">${q.default_points}</td>
      <td class="cc"><button class="nz-tb mini blu" onclick="EP.addItem(${q.id})">담기</button></td></tr>`).join("")
      || '<tr class="nz-empty"><td colspan="5">담을 문항이 없습니다.</td></tr>';
  }
  async function addItem(qid) { await post(`/api/sets/${curSetId}/items`, { question_id: qid }); loadSet(); }
  async function removeItem(itemId) { await del(`/api/sets/${curSetId}/items/${itemId}`); loadSet(); }

  let dragQid = null;
  function dragStart(e, qid) { dragQid = qid; }
  async function dropOn(e, targetQid) {
    e.preventDefault();
    if (dragQid === null || dragQid === targetQid) return;
    const order = [...document.querySelectorAll("#setRows tr[data-qid]")].map((tr) => +tr.dataset.qid);
    const from = order.indexOf(dragQid), to = order.indexOf(targetQid);
    order.splice(to, 0, order.splice(from, 1)[0]);
    await put(`/api/sets/${curSetId}/order`, { question_ids: order });
    dragQid = null; loadSet();
  }

  async function checkSet() {
    const r = await api(`/api/sets/${curSetId}/check`);
    $("setCheckResult").innerHTML = r.ok && !r.warn_count
      ? '<div class="nz-issues ok">검토 통과 — 출력할 수 있습니다.</div>'
      : `<div class="nz-issues ${r.error_count ? "err" : "warn"}">
          <b>오류 ${r.error_count} · 경고 ${r.warn_count}</b>
          <ul>${r.issues.map((i) => `<li>[${i.level === "error" ? "오류" : "경고"}] ${esc(i.message)}</li>`).join("")}</ul></div>`;
  }

  async function exportSet() {
    const r = await api(`/api/sets/${curSetId}/export`);
    if (!r.count) return alert("담긴 문항이 없습니다.");
    $("exportBox").classList.remove("hidden");
    $("exportText").value = r.markdown;
    $("exportText").select();
    try { await navigator.clipboard.writeText(r.markdown); alert(`${r.count}개 문항을 클립보드에 복사했습니다.\n한글에 붙여넣고 Ctrl+T 하세요.`); }
    catch { alert("아래 상자의 내용을 복사해 한글에 붙여넣으세요."); }
  }

  /* ---------- 근거 문서 (DocFinder 방식) ---------- */
  let searchTags = [];        // 해시태그 다중 검색
  let curFolder = "";
  let onlyDocId = 0;          // 특정 문서로 좁히기
  let viewer = { docId: null, page: 1, lastPage: 1 };

  async function loadDocs() {
    const docs = await api("/api/documents");
    $("docCnt").textContent = docs.length;
    $("docRows").innerHTML = docs.map((d, i) => `
      <tr><td class="cc">${i + 1}</td><td>${esc(d.title)}</td><td class="cc">${esc(d.doc_type)}</td>
      <td class="cc">${d.pages}</td><td class="cc">${esc((d.indexed_at || "").slice(0, 16))}</td>
      <td class="cc"><button class="nz-tb mini ${onlyDocId === d.id ? "blu" : ""}"
        onclick="EP.onlyDoc(${d.id})">${onlyDocId === d.id ? "해제" : "선택"}</button></td>
      <td class="cc"><button class="nz-tb mini" onclick="EP.delDoc(${d.id})">×</button></td></tr>`).join("")
      || '<tr class="nz-empty"><td colspan="7">등록된 문서가 없습니다. “폴더 선택”으로 PDF 폴더를 지정하세요.</td></tr>';
  }
  function toggleDocList() { $("docListBox").classList.toggle("hidden"); loadDocs(); }
  function onlyDoc(id) { onlyDocId = onlyDocId === id ? 0 : id; loadDocs(); runSearch(); }

  async function pickFolder() {
    try {
      const r = await post("/api/pick-folder", {});
      if (!r.folder) return;
      curFolder = r.folder;
      $("docFolderLabel").textContent = curFolder;
      await indexFolder();
    } catch (e) { alert("폴더 선택 실패: " + e.message); }
  }

  async function indexFolder() {
    if (!curFolder) return alert("먼저 “폴더 선택”으로 PDF 폴더를 지정하세요.");
    $("docHits").textContent = "색인 중…";
    try {
      const r = await post("/api/documents/index", { folder: curFolder, doc_type: $("docType").value });
      $("docHits").textContent = "";
      alert(`문서 ${r.documents}권 · ${r.pages}페이지를 색인했습니다.` +
        (r.skipped.length ? `\n건너뜀 ${r.skipped.length}건(텍스트 없음/열기 실패)` : ""));
      loadDocs(); runSearch();
    } catch (e) { $("docHits").textContent = ""; alert("인덱싱 실패: " + e.message); }
  }

  async function delDoc(id) {
    if (!confirm("이 문서를 근거 검색에서 제거할까요? (원본 파일은 그대로입니다)")) return;
    await del(`/api/documents/${id}`);
    if (onlyDocId === id) onlyDocId = 0;
    loadDocs(); runSearch();
  }

  /* --- 해시태그 --- */
  function onTagKey(e) {
    const inp = e.target;
    if (e.key === "Enter") {
      const v = inp.value.trim().replace(/^#/, "");
      if (v && !searchTags.includes(v)) { searchTags.push(v); inp.value = ""; renderTags(); runSearch(); }
      e.preventDefault();
    } else if (e.key === "Backspace" && !inp.value && searchTags.length) {
      searchTags.pop(); renderTags(); runSearch();
    }
  }
  function renderTags() {
    $("searchTags").innerHTML = searchTags.map((t, i) =>
      `<span class="nz-chip"><span class="dot"></span>${esc(t)}<button onclick="EP.delTag(${i})">×</button></span>`).join("");
  }
  function delTag(i) { searchTags.splice(i, 1); renderTags(); runSearch(); }

  async function runSearch() {
    if (!searchTags.length) {
      $("docResults").innerHTML = '<p class="nz-sub" style="padding:14px">키워드를 입력해 검색하세요.</p>';
      $("docHits").textContent = "";
      return;
    }
    const params = new URLSearchParams({ q: searchTags.join(" ") });
    if (onlyDocId) params.set("doc_id", onlyDocId);
    const r = await api("/api/evidence/search?" + params);
    $("docHits").innerHTML = `<b>${r.total}</b>개 페이지 일치 · ${r.items.length}개 표시`;

    // 문서별 그룹
    const groups = {};
    r.items.forEach((it) => { (groups[it.doc_title] ||= []).push(it); });
    const html = Object.entries(groups).map(([title, items]) => `
      <div class="nz-docgroup">
        <div class="nz-docgroup-head">${esc(title)} <span class="n">${items.length}개 페이지 · 최고 ${items[0].match_pct}%</span></div>
        ${items.map((it) => `
          <div class="nz-res" data-doc="${it.document_id}" data-page="${it.page_no}"
               onclick="EP.showPage(${it.document_id}, ${it.page_no}, this)">
            <div class="nz-res-top">
              <span class="nz-pct ${it.match_pct < 60 ? "low" : ""}">${it.match_pct}%</span>
              <span class="nz-res-page">${it.page_no}페이지</span>
            </div>
            <div class="nz-res-snip">${mark(it.snippet)}</div>
          </div>`).join("")}
      </div>`).join("");
    $("docResults").innerHTML = html || '<p class="nz-sub" style="padding:14px">결과가 없습니다.</p>';
  }

  // snippet 의 [키워드] 표시를 <mark> 로
  function mark(s) {
    return esc(s).replace(/\[([^\]]+)\]/g, "<mark>$1</mark>");
  }

  /* --- 페이지 미리보기 (이미지 + 좌표 오버레이) --- */
  const HL_COLORS = ["rgba(255,217,77,.45)", "rgba(140,217,255,.45)", "rgba(166,255,166,.45)",
                     "rgba(255,184,219,.45)", "rgba(209,194,255,.45)"];

  async function showPage(docId, pageNo, el) {
    document.querySelectorAll(".nz-res.on").forEach((n) => n.classList.remove("on"));
    if (el) el.classList.add("on");
    viewer.docId = docId; viewer.page = pageNo;

    const meta = await api(`/api/documents/${docId}/page/${pageNo}`);
    viewer.lastPage = meta.last_page;
    $("viewerTitle").textContent = `${meta.title} — ${pageNo} / ${meta.last_page}페이지`;

    // 이미지는 검색어와 무관 → 캐시가 그대로 맞아 빠르다
    $("viewerBody").innerHTML =
      `<div class="nz-pagewrap" id="pagewrap">
         <img id="pageImg" src="/api/documents/${docId}/page/${pageNo}/image"
              alt="${esc(meta.title)} ${pageNo}페이지" />
       </div>`;

    prefetch(docId, pageNo + 1);   // 다음 페이지 미리 받아두기
    if (!searchTags.length) return;

    const hl = await api(`/api/documents/${docId}/page/${pageNo}/highlights?q=` +
      encodeURIComponent(searchTags.join(" ")));
    drawHighlights(hl);
  }

  function drawHighlights(hl) {
    const wrap = $("pagewrap"), img = $("pageImg");
    if (!wrap || !img) return;
    const paint = () => {
      wrap.querySelectorAll(".nz-hl").forEach((n) => n.remove());
      const scale = img.clientWidth / hl.page_w;   // 이미지가 축소돼 보여도 좌표가 맞도록
      hl.boxes.forEach((b) => {
        const d = document.createElement("div");
        d.className = "nz-hl";
        d.style.cssText = `left:${b.x * scale}px;top:${b.y * scale}px;` +
          `width:${b.w * scale}px;height:${b.h * scale}px;background:${HL_COLORS[b.color_idx]}`;
        d.title = b.term;
        wrap.appendChild(d);
      });
      if (hl.boxes.length) {                      // 첫 일치로 스크롤
        const first = hl.boxes[0];
        $("viewerBody").scrollTop = Math.max(0, first.y * scale - 120);
      }
    };
    if (img.complete) paint(); else img.onload = paint;

    // 조용한 실패 방지 — 검색은 맞았는데 위치를 못 찾은 경우 알린다
    const warn = hl.misses.length
      ? `<div class="nz-hlwarn">이 페이지에서 <b>${esc(hl.misses.join(", "))}</b>의 정확한 위치를 찾지 못했습니다 (글자가 이미지이거나 조각나 있을 수 있음)</div>`
      : "";
    const legend = Object.entries(hl.hits).filter(([, n]) => n).map(([t, n], i) =>
      `<span class="nz-leg"><i style="background:${HL_COLORS[i % HL_COLORS.length]}"></i>${esc(t)} ${n}</span>`).join("");
    $("viewerTitle").insertAdjacentHTML("afterend", "");
    const bar = document.createElement("div");
    bar.className = "nz-hlbar";
    bar.innerHTML = legend + warn;
    wrap.parentElement.insertBefore(bar, wrap);
  }

  const _prefetched = new Set();
  function prefetch(docId, pageNo) {
    const key = `${docId}/${pageNo}`;
    if (_prefetched.has(key)) return;
    _prefetched.add(key);
    const im = new Image();
    im.src = `/api/documents/${docId}/page/${pageNo}/image`;
  }

  function viewerPage(delta) {
    if (!viewer.docId) return;
    const p = viewer.page + delta;
    if (p < 1 || p > viewer.lastPage) return;
    showPage(viewer.docId, p, null);
  }
  async function openOriginal() {
    if (!viewer.docId) return alert("먼저 결과를 선택하세요.");
    try { await post(`/api/documents/${viewer.docId}/open?page_no=${viewer.page}`, {}); }
    catch (e) { alert("원본을 열 수 없습니다: " + e.message); }
  }

  /* ---------- 탭 ---------- */
  function initTabs() {
    document.querySelectorAll(".nz-navi").forEach((btn) => {
      btn.onclick = () => {
        document.querySelectorAll(".nz-navi").forEach((b) => b.classList.remove("on"));
        btn.classList.add("on");
        const tab = btn.dataset.tab;
        ["bank", "question", "set", "doc"].forEach((t) => $("tab-" + t).hidden = t !== tab);
        if (tab === "question") { loadQuestions(); loadPicker(); }
        if (tab === "set") loadSets();
        if (tab === "doc") loadDocs();
      };
    });
  }

  async function init() {
    initTabs();
    await loadStandards();
    await loadProps();
  }
  document.addEventListener("DOMContentLoaded", init);

  return {
    loadProps, toggleForm, saveProp, delProp, openProp, addVariant, delVariant, delEvidence,
    markVerified, searchEvidenceFor, attachEvidence, exportCsv, peekPage,
    pickStandard, filterTree, openStdTable, closeStdTable, renderStdTable,
    onTypeChange, addBogi, setBogi, delBogi, addChoice, setChoice, setCombo, setAnswer, delChoice,
    applyPreset, loadPicker, useProp, searchEvidence, saveQuestion, checkQuestionDraft,
    loadQuestions, editQuestion, checkQuestion, delQuestion,
    loadSets, createSet, loadSet, addItem, removeItem, dragStart, dropOn, checkSet, exportSet,
    loadDocs, toggleDocList, onlyDoc, pickFolder, indexFolder, delDoc,
    onTagKey, delTag, showPage, viewerPage, openOriginal,
  };
})();
