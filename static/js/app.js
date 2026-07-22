/* ===== ExamPool — Phase 1 ===== */
const EP = (() => {
  let standards = [];
  let curProps = [];        // 현재 조회된 명제
  let bogi = [];            // 문항 설계: 보기 [{label,text,proposition_id,variant_id}]
  let choices = [];         // 문항 설계: 선지
  let editingQid = null;
  let curSetId = null;

  const LABELS = ["ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ"];
  const HL_COLORS = ["rgba(255,217,77,.45)", "rgba(140,217,255,.45)", "rgba(166,255,166,.45)",
                     "rgba(255,184,219,.45)", "rgba(209,194,255,.45)"];

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
    renderScopeBar();
    refillStdSelects();
    const subj = await api("/api/subject");
    $("subjectLabel").textContent = `${subj.subject} · 성취기준 ${subj.standard_count}개`;
  }

  function fillSelect(sel, opts, firstLabel) {
    if (!sel) return;
    sel.innerHTML = firstLabel ? `<option value="">${firstLabel}</option>` : "";
    opts.forEach((o) => sel.appendChild(new Option(o.t, o.v)));
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
    scopedStandards().forEach((u) => {
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

  /* ---------- 출제 범위(단원 선택) ---------- */
  // 시험 한 번에 23개 단원을 다 쓰지 않는다. 필요한 단원만 골라 그 안에서 작업한다.
  let scope = JSON.parse(localStorage.getItem("ep_scope") || "[]");
  let stdScope = JSON.parse(localStorage.getItem("ep_std_scope") || "[]");

  function inScope(unitNo) { return !scope.length || scope.includes(unitNo); }
  function scopedStandards() {
    return standards.filter((u) => inScope(u.unit_no)).map((u) => ({
      unit_no: u.unit_no, name: u.name,
      standards: stdScope.length ? u.standards.filter((s) => stdScope.includes(s.code)) : u.standards,
    })).filter((u) => u.standards.length);
  }

  function saveScope(list) {
    scope = list;
    localStorage.setItem("ep_scope", JSON.stringify(scope));
    renderTree(); renderScopeBar(); refillStdSelects();
    loadProps();
  }

  function renderScopeBar() {
    const bars = document.querySelectorAll(".nz-scopebar");
    const label = scope.length
      ? `출제 범위: <b>${scope.map((n) => standards.find((u) => u.unit_no === n)?.name || n).join(" · ")}</b>`
      : "출제 범위: <b>전체 단원</b> (범위를 좁히면 화면이 단순해집니다)";
    bars.forEach((b) => {
      b.innerHTML = `${label}<button class="nz-tb mini" style="margin-left:auto" onclick="EP.openScope()">범위 설정</button>`;
    });
  }

  function openScope() {
    let m = $("scopeModal");
    if (!m) {
      m = document.createElement("div");
      m.id = "scopeModal"; m.className = "nz-modal";
      m.onclick = (e) => { if (e.target === m) m.classList.add("hidden"); };
      document.body.appendChild(m);
    }
    m.classList.remove("hidden");
    m.innerHTML = `<div class="nz-modal-box" style="width:min(760px,92vw)">
      <div class="nz-modal-head"><b>이번 시험 출제 범위</b>
        <span class="nz-sub" style="margin:0 0 0 10px">다룰 단원만 고르세요. 고른 단원만 화면에 보입니다.</span>
        <button class="nz-tb" style="margin-left:auto" onclick="document.getElementById('scopeModal').classList.add('hidden')">닫기</button>
      </div>
      <div class="nz-modal-body">
        <div class="nz-scope" id="scopeList">
          ${standards.map((u) => `
            <label class="${scope.includes(u.unit_no) ? "on" : ""}" data-u="${u.unit_no}">
              <input type="checkbox" ${scope.includes(u.unit_no) ? "checked" : ""}
                     onchange="this.parentElement.classList.toggle('on', this.checked)" />
              ${u.unit_no}. ${esc(u.name)}
            </label>`).join("")}
        </div>
        <div class="nz-fbtn" style="margin-top:12px">
          <button class="nz-tb" onclick="EP.applyScope([])">전체 단원 보기</button>
          <button class="nz-tb blu" onclick="EP.applyScope()">이 범위로 시작</button>
        </div>
      </div></div>`;
  }

  function applyScope(force) {
    let list = force;
    if (list === undefined) {
      list = [...document.querySelectorAll("#scopeList input:checked")]
        .map((i) => +i.parentElement.dataset.u);
    }
    saveScope(list);
    $("scopeModal").classList.add("hidden");
  }

  function showStdFull() {}   // 전문은 select 안에 그대로 들어간다 (별도 공간 없음)

  /** 문항 은행 단원·성취기준 필터 채우기 */
  function refillBankFilters() {
    const uSel = $("qbUnit"), sSel = $("qbStd");
    if (!uSel) return;
    const cur = uSel.value;
    uSel.innerHTML = '<option value="">전체 단원</option>';
    scopedStandards().forEach((u) => uSel.appendChild(new Option(`${u.unit_no}. ${u.name}`, u.unit_no)));
    uSel.value = cur;
    const unit = +uSel.value || 0;
    const list = unit ? scopedStandards().filter((u) => u.unit_no === unit) : scopedStandards();
    sSel.innerHTML = '<option value="">전체 성취기준</option>';
    list.forEach((u) => u.standards.forEach((st) =>
      sSel.appendChild(new Option(`${st.code} ${st.text.slice(0, 14)}…`, st.code))));
  }

  function refillStdSelects() {
    const opts = scopedStandards().flatMap((u) => u.standards.map((s) =>
      ({ v: s.code, t: `${s.code} ${s.text}` })));   // 자르지 않고 전문
    fillSelect($("q-std"), opts, "전체");
    fillSelect($("f-std"), opts);
    if ($("stdList") && !$("stdList").classList.contains("hidden")) renderStdList();
    refillUnitSelect();
    refillBankFilters();
  }

  /** 명제 은행: 단원 select — 단원을 먼저 고르고 성취기준을 좁힌다 */
  function refillUnitSelect() {
    const sel = $("q-unit");
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '<option value="">전체 단원</option>';
    scopedStandards().forEach((u) => sel.appendChild(new Option(`${u.unit_no}. ${u.name}`, u.unit_no)));
    sel.value = cur;
  }

  function onBankUnitChange() {
    const unit = +$("q-unit").value || 0;
    const list = unit ? scopedStandards().filter((u) => u.unit_no === unit) : scopedStandards();
    const opts = list.flatMap((u) => u.standards.map((s) => ({ v: s.code, t: `${s.code} ${s.text}` })));
    fillSelect($("q-std"), opts, "전체");
    loadProps();
  }

  /* ---------- 트리 (접기/펴기) ---------- */
  let openUnits = new Set();

  function renderTree() {
    const tree = $("tree");
    tree.innerHTML = "";
    scopedStandards().forEach((u) => {
      const el = document.createElement("div");
      el.className = "nz-mi";
      const open = openUnits.has(u.unit_no);
      el.innerHTML = `<span class="caret">${open ? "▾" : "▸"}</span>` +
        `<span class="uname">${u.unit_no}. ${esc(u.name)}</span>`;
      el.onclick = () => {
        if (openUnits.has(u.unit_no)) openUnits.delete(u.unit_no);
        else openUnits.add(u.unit_no);
        renderTree();
      };
      tree.appendChild(el);
      if (open) {
        u.standards.forEach((s) => {
          const e = document.createElement("div");
          e.className = "nz-mi sub";
          e.title = `${s.code} ${s.text}`;
          e.innerHTML = `<span class="code">${esc(s.code)}</span><span class="txt">${esc(s.text)}</span>`;
          e.onclick = (ev) => { ev.stopPropagation(); pickStandard(s, e); };
          tree.appendChild(e);
        });
      }
    });
    if (!scopedStandards().length) {
      tree.innerHTML = '<p class="nz-sub" style="padding:10px">범위에 단원이 없습니다.</p>';
    }
  }

  function foldMenu() { document.querySelector(".nz-menu").classList.toggle("folded"); }

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
    $("presetBox").classList.toggle("hidden", !hap);     // 정답형엔 프리셋 없음
    $("imgChoiceBox").classList.toggle("hidden", hap);   // 그림 선지는 정답형만
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
        <button class="nz-tb mini" onclick="EP.pickFor('bogi', ${i})">명제</button>
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
    const img = $("imgChoices") && $("imgChoices").checked;
    $("choiceRows").innerHTML = choices.map((c, i) => `
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
  }
  function setChoice(i, v) { choices[i].text = v; }
  function setCombo(i, v) { choices[i].combo = v.split(",").map((s) => s.trim()).filter(Boolean); }
  function setAnswer(i) { choices.forEach((c, j) => c.is_answer = j === i); renderChoices(); }
  function delChoice(i) { choices.splice(i, 1); choices.forEach((c, j) => c.ord = j + 1); renderChoices(); }

  async function renderPresets() {
    const presets = await api("/api/combo-presets");
    $("presetBtns").innerHTML = Object.entries(presets).map(([k, p]) =>
      `<button class="nz-preset" onclick="EP.applyPreset('${k}')" title="${esc(p.preview)}">
         <b>${esc(p.name)}</b><span>${esc(p.preview)} · ${esc(p.desc)}</span></button>`).join(" ");
  }

  async function applyPreset(name) {
    const presets = await api("/api/combo-presets");
    choices = presets[name].combos.map((combo, i) => ({
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

  let evViewer = { docId: null, page: 1, lastPage: 1, q: "" };
  let evSrc = "";      // "" | 교과서 | 교육과정 | 기출

  function setSrc(src) {
    evSrc = src;
    document.querySelectorAll(".nz-srcbtn").forEach((b) =>
      b.classList.toggle("on", (b.dataset.src || "") === src));
    if ($("evSearch").value.trim()) searchEvidence();
  }

  async function searchEvidence() {
    const q = $("evSearch").value.trim();
    const terms = splitTerms(q);
    if (!terms.length) {
      $("evList").innerHTML = '<p class="nz-sub" style="padding:10px">키워드를 넣으면 교과서에서 근거를 찾습니다.</p>';
      renderTermBar([]);
      return;
    }
    evViewer.q = q;
    const r = await api("/api/evidence/search?q=" + encodeURIComponent(q) + "&limit=60");
    let items = r.items;
    if (evSrc) {
      const docs = await api("/api/documents");
      const ids = new Set(docs.filter((d) => d.doc_type === evSrc).map((d) => d.id));
      items = items.filter((it) => ids.has(it.document_id));
    }
    const groups = {};
    items.forEach((it) => { (groups[it.doc_title] ||= []).push(it); });
    $("evList").innerHTML = items.length
      ? `<div class="nz-hlbar" style="padding:6px 10px"><b>${r.total}</b>개 일치 · ${items.length}개 표시${evSrc ? " · " + evSrc : ""}</div>` +
        Object.entries(groups).map(([title, items]) => `
        <div class="nz-docgroup">
          <div class="nz-docgroup-head">${esc(title)} <span class="n">${items.length}개</span></div>
          ${items.map((h) => `
            <div class="nz-res" onclick="EP.evShow(${h.document_id}, ${h.page_no}, this)">
              <div class="nz-res-top">
                <span class="nz-pct ${h.match_pct < 60 ? "low" : ""}">${h.match_pct}%</span>
                <span class="nz-res-page">${h.page_no}페이지</span>
              </div>
              <div class="nz-res-snip">${markTerms(h.snippet, terms)}</div>
            </div>`).join("")}
        </div>`).join("")
      : '<p class="nz-sub" style="padding:12px">결과 없음 — 근거·기출 탭에서 문서를 인덱싱하세요.</p>';

    // 단어별 적중 수를 칩에 표시
    const counts = {};
    terms.forEach((t) => {
      counts[t] = items.filter((it) => (it.snippet || "").toLowerCase().includes(t.toLowerCase())).length;
    });
    renderTermBar(terms, counts);
  }

  /** 문항 설계 화면의 근거 뷰어 — 교과서 원문을 옆에 띄워두고 문항을 쓴다 */
  async function evShow(docId, pageNo, el) {
    document.querySelectorAll("#evList .nz-res.on").forEach((n) => n.classList.remove("on"));
    if (el) el.classList.add("on");
    evViewer.docId = docId; evViewer.page = pageNo;
    const meta = await api(`/api/documents/${docId}/page/${pageNo}`);
    evViewer.lastPage = meta.last_page;
    evViewer.terms = splitTerms(evViewer.q || "");

    // 기출은 페이지 전체가 아니라 문항 하나씩 보여준다
    const types = await ensureDocTypes();
    if (types[docId] === "기출") {
      try {
        if (await showExamItems(docId, pageNo, meta.title)) return;
      } catch (e) { /* 문항 인식 실패 → 아래 페이지 전체로 */ }
    }
    $("evViewTitle").textContent = `${meta.title} — ${pageNo} / ${meta.last_page}p`;
    $("evViewBody").innerHTML =
      `<div class="nz-pagewrap" id="evWrap"><img id="evImg" src="/api/documents/${docId}/page/${pageNo}/image" /></div>`;
    prefetch(docId, pageNo + 1);
    if (!evViewer.q) return;
    const hl = await api(`/api/documents/${docId}/page/${pageNo}/highlights?q=` +
      encodeURIComponent(evViewer.q));
    const wrap = $("evWrap"), img = $("evImg");
    const paint = () => {
      const scale = img.clientWidth / hl.page_w;
      hl.boxes.forEach((b) => {
        const d = document.createElement("div");
        d.className = "nz-hl";
        d.style.cssText = `left:${b.x * scale}px;top:${b.y * scale}px;width:${b.w * scale}px;` +
          `height:${b.h * scale}px;background:${HL_COLORS[b.color_idx]}`;
        wrap.appendChild(d);
      });
      if (hl.boxes.length) $("evViewBody").scrollTop = Math.max(0, hl.boxes[0].y * scale - 100);
    };
    if (img.complete) paint(); else img.onload = paint;
  }

  /** 근거 검색 접기/펴기 — 펼치면 화면 절반 */
  function toggleSide() {
    const el = $("qside");
    el.classList.toggle("folded");
    const folded = el.classList.contains("folded");
    $("evFoldBtn").textContent = folded ? "◀" : "▶";
    localStorage.setItem("ep_side_folded", folded ? "1" : "");
  }
  function togglePicker() { $("pickerList").classList.toggle("hidden"); }

  function evZoom() {
    if (!evViewer.docId) return alert("먼저 결과를 선택하세요.");
    peekPage(evViewer.docId, evViewer.page, evViewer.q);
  }

  function evViewPage(d) {
    if (!evViewer.docId) return;
    const p = evViewer.page + d;
    if (p < 1 || p > evViewer.lastPage) return;
    evShow(evViewer.docId, p, null);
  }

  function collectQuestion() {
    return {
      title: $("qtitle").value,
      qtype: $("qtype").value,
      is_negative: $("isNeg").checked,
      passage: $("qpassage").value,
      material: $("qmaterial").value,
      ask: $("qask").value,
      bogi_items: $("qtype").value === "합답형" ? bogi : [],
      answer: (() => { const i = choices.findIndex((c) => c.is_answer); return i >= 0 ? "①②③④⑤"[i] : ""; })(),
      default_points: parseFloat($("qpoints").value) || 3,
      difficulty: $("qdiff").value,
      standard_code: stdValue() || null,
      intent: $("qintent").value,
      image_choices: $("imgChoices") ? $("imgChoices").checked : false,
      status: $("qstatus") ? $("qstatus").value : "초안",
      review_note: JSON.stringify(checkState),
      choices,
    };
  }

  async function saveQuestion() {
    const q = collectQuestion();
    if (!q.ask.trim()) return alert("발문을 입력하세요.");
    if (!q.choices.length) return alert("선지를 추가하세요.");
    if (q.status === "완성") {
      const missing = CHECKS.filter((c) => !checkState[c.k]);
      if (missing.length) return alert("'완성'으로 저장하려면 확인 항목을 모두 체크해야 합니다.\n\n미확인: "
        + missing.map((m) => m.t).join(", "));
      if (!(checkState.note || "").trim()) return alert("확인 근거를 적어주세요.");
    }
    if (editingQid) { await put(`/api/questions/${editingQid}`, q); }
    else { await post("/api/questions", q); }
    resetQuestionForm(); loadQuestions();
    alert("문항을 저장했습니다.");
  }

  function resetQuestionForm() {
    editingQid = null; bogi = []; choices = []; checkState = {}; renderChecklist();
    ["qtitle", "qpassage", "qmaterial", "qask", "qintent"].forEach((id) => $(id).value = "");
    setStdValue("");
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
    refillBankFilters();
    const params = new URLSearchParams();
    const std = $("qbStd") ? $("qbStd").value : "";
    const q = $("qbSearch") ? $("qbSearch").value.trim() : "";
    if (std) params.set("standard", std);
    if (q) params.set("q", q);
    const stt = $("qbStatus") ? $("qbStatus").value : "";
    if (stt) params.set("status", stt);
    let rows = await api("/api/questions?" + params);
    const unit = $("qbUnit") ? +$("qbUnit").value : 0;
    if (unit && !std) {           // 단원만 고른 경우: 그 단원의 성취기준들로 거른다
      const codes = new Set((scopedStandards().find((u) => u.unit_no === unit) || {standards: []})
        .standards.map((x) => x.code));
      rows = rows.filter((r) => codes.has(r.standard_code));
    }
    $("qcnt").textContent = rows.length;
    const st = { "초안": 0, "검토중": 0, "완성": 0 };
    rows.forEach((r) => { st[r.status || "초안"] = (st[r.status || "초안"] || 0) + 1; });
    if ($("qbStats")) $("qbStats").innerHTML =
      `<div class="nz-statcard"><b>${rows.length}</b>전체</div>` +
      `<div class="nz-statcard"><b>${st["초안"]}</b>초안</div>` +
      `<div class="nz-statcard"><b>${st["검토중"]}</b>검토중</div>` +
      `<div class="nz-statcard"><b>${st["완성"]}</b>완성</div>`;
    $("qRows").innerHTML = rows.map((r, i) => `
      <tr><td class="cc">${i + 1}</td>
      <td class="cc"><span class="nz-badge ${esc(r.status || "초안")}">${esc(r.status || "초안")}</span></td>
      <td>${esc(r.title || "-")}</td>
      <td class="cc">${esc(r.qtype)}${r.is_negative ? "·부정" : ""}</td>
      <td>${esc(r.ask)}</td><td class="cc code">${esc(r.standard_code || "-")}</td>
      <td class="cc">${esc(r.difficulty)}</td><td class="cc">${r.default_points}</td>
      <td class="cc"><button class="nz-tb mini" onclick="EP.editQuestion(${r.id}, true)">수정</button>
      <button class="nz-tb mini" onclick="EP.checkQuestion(${r.id})">검토</button>
      <button class="nz-tb mini" onclick="EP.delQuestion(${r.id})">×</button></td></tr>`).join("")
      || '<tr class="nz-empty"><td colspan="9">문항이 없습니다.</td></tr>';
  }

  async function editQuestion(qid, jump) {
    if (jump) {   // 문항 은행에서 눌렀으면 설계 탭으로 이동
      document.querySelector('.nz-navi[data-tab="question"]').click();
    }
    const d = await api(`/api/questions/${qid}`);
    editingQid = qid;
    const q = d.question;
    $("qtitle").value = q.title || "";
    $("qtype").value = q.qtype; $("isNeg").checked = !!q.is_negative;
    $("qpassage").value = q.passage; $("qmaterial").value = q.material;
    $("qask").value = q.ask; $("qintent").value = q.intent;
    $("qpoints").value = q.default_points; $("qdiff").value = q.difficulty;
    setStdValue(q.standard_code || "");
    bogi = q.bogi_items || [];
    if ($("qstatus")) $("qstatus").value = q.status || "초안";
    if ($("imgChoices")) $("imgChoices").checked = !!q.image_choices;
    try { checkState = JSON.parse(q.review_note || "{}"); } catch (e) { checkState = {}; }
    renderChecklist();
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
      docTypes = {};
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
    if (e.key === "Enter") { e.preventDefault(); runSearchFromInput(); }
  }
  function renderTags() {
    $("searchTags").innerHTML = searchTags.map((t, i) =>
      `<span class="nz-chip"><span class="dot" style="background:${HL_COLORS[i % HL_COLORS.length]}"></span>` +
      `${esc(t)}<button onclick="EP.delTag(${i})">×</button></span>`).join("");
  }
  function delTag(i) {
    searchTags.splice(i, 1);
    if ($("docSearch")) $("docSearch").value = searchTags.join(" ");
    renderTags(); runSearch();
  }

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
            <div class="nz-res-snip">${markTerms(it.snippet, searchTags)}</div>
          </div>`).join("")}
      </div>`).join("");
    $("docResults").innerHTML = html || '<p class="nz-sub" style="padding:14px">결과가 없습니다.</p>';
  }

  // snippet 의 [키워드] 표시를 <mark> 로
  function mark(s) {
    return esc(s).replace(/\[([^\]]+)\]/g, "<mark>$1</mark>");
  }

  /* --- 페이지 미리보기 (이미지 + 좌표 오버레이) --- */

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






  /* ---------- 기출 스크랩 (참고 문항 + 메모) ---------- */
  let scraps = [];
  let curScrap = null;
  let scrapTimer = null;

  async function loadScraps() {
    const q = $("scrapSearch") ? $("scrapSearch").value.trim() : "";
    scraps = await api("/api/exam-refs" + (q ? "?q=" + encodeURIComponent(q) : ""));
    $("scrapCnt").textContent = scraps.length;
    $("scrapList").innerHTML = scraps.length ? scraps.map((r) => `
      <div class="nz-scrapitem ${curScrap && curScrap.id === r.id ? "on" : ""}" onclick="EP.openScrap(${r.id})">
        <b>${r.item_num}번</b> <span class="src">${esc(r.doc_title)} p.${r.page_no}</span>
        ${r.note ? `<div class="memo">${esc(r.note)}</div>` : ""}
        ${r.tags ? `<div class="tags">${r.tags.split(",").map((t) =>
          t.trim() ? `<span class="nz-tag">${esc(t.trim())}</span>` : "").join("")}</div>` : ""}
      </div>`).join("")
      : '<p class="nz-sub" style="padding:12px">스크랩한 기출이 없습니다. 문항 설계의 기출 검색에서 “참고로 기록”을 누르세요.</p>';
  }

  function openScrap(id) {
    curScrap = scraps.find((r) => r.id === id);
    if (!curScrap) return;
    document.querySelectorAll(".nz-scrapitem.on").forEach((n) => n.classList.remove("on"));
    const el = [...document.querySelectorAll(".nz-scrapitem")][scraps.indexOf(curScrap)];
    if (el) el.classList.add("on");
    $("scrapTitle").textContent = `${curScrap.doc_title} — ${curScrap.page_no}p ${curScrap.item_num}번`;
    $("scrapImg").innerHTML =
      `<img src="/api/documents/${curScrap.document_id}/page/${curScrap.page_no}/item/${curScrap.item_num}/image?dpi=130"
            style="max-width:100%;box-shadow:0 1px 6px rgba(0,0,0,.14);background:#fff" />`;
    $("scrapNote").value = curScrap.note || "";
    $("scrapTags").value = curScrap.tags || "";
    $("scrapSaved").textContent = "";
  }

  function scrapNoteChanged() {
    if (!curScrap) return;
    $("scrapSaved").textContent = "저장 중…";
    clearTimeout(scrapTimer);
    scrapTimer = setTimeout(async () => {
      await api(`/api/exam-refs/${curScrap.id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note: $("scrapNote").value, tags: $("scrapTags").value }),
      });
      curScrap.note = $("scrapNote").value;
      curScrap.tags = $("scrapTags").value;
      $("scrapSaved").textContent = "저장됨";
      loadScraps();
    }, 500);
  }

  async function scrapDelete() {
    if (!curScrap) return;
    if (!confirm(`${curScrap.doc_title} ${curScrap.item_num}번 스크랩을 지울까요?`)) return;
    await del(`/api/exam-refs/${curScrap.id}`);
    curScrap = null;
    $("scrapImg").innerHTML = '<p class="nz-sub" style="padding:16px;text-align:center">삭제했습니다.</p>';
    $("scrapNote").value = ""; $("scrapTags").value = "";
    loadScraps();
  }

  async function scrapOpenOriginal() {
    if (!curScrap) return;
    try { await post(`/api/documents/${curScrap.document_id}/open?page_no=${curScrap.page_no}`, {}); }
    catch (e) { alert("원본을 열 수 없습니다: " + e.message); }
  }

  /* ---------- 기출: 문항 하나씩 보기 ---------- */
  let docTypes = {};        // {문서id: 종류}

  async function ensureDocTypes() {
    if (Object.keys(docTypes).length) return docTypes;
    const docs = await api("/api/documents");
    docs.forEach((d) => { docTypes[d.id] = d.doc_type; });
    return docTypes;
  }

  /** 기출 페이지 → 검색어가 들어있는 문항만 하나씩 크게 */
  async function showExamItems(docId, pageNo, title) {
    const q = evViewer.q || "";
    const r = await api(`/api/documents/${docId}/page/${pageNo}/items?q=` + encodeURIComponent(q));
    const hits = r.items.filter((x) => x.has_hit);
    const list = hits.length ? hits : r.items;      // 적중 없으면 그 페이지 문항 전부
    if (!list.length) return false;                 // 문항을 못 찾으면 페이지 전체로

    $("evViewTitle").textContent =
      `${title} — ${pageNo}p · ${hits.length ? "검색어 포함 " + hits.length + "문항" : r.items.length + "문항"}`;
    $("evViewBody").innerHTML = list.map((x) => {
      const badge = Object.entries(x.hits).map(([t, n], i) =>
        `<span class="nz-termchip"><span class="sw" style="background:${
          HL_COLORS[(evViewer.terms || []).indexOf(t) % HL_COLORS.length]}"></span>${esc(t)} ${n}</span>`).join("");
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
  }

  /** 참고한 기출을 스크랩으로 저장한다 (메모 포함) */
  async function useExam(docId, pageNo, num, title) {
    const note = prompt(`${title} ${num}번 — 메모 (없으면 비워두세요)`, "");
    if (note === null) return;
    const r = await post("/api/exam-refs", {
      document_id: docId, doc_title: title, page_no: pageNo, item_num: num, note: note || "",
    });
    const el = $("qintent");
    if (el && !el.value.includes(`${title} ${num}번`)) {
      el.value = (el.value ? el.value.replace(/\s*$/, "\n") : "") + `참고 기출: ${title} ${num}번`;
    }
    if (confirm((r.existed ? "이미 스크랩된 문항입니다. 메모를 갱신했습니다."
      : "기출 스크랩에 담았습니다.") + "\n\n스크랩 탭으로 갈까요?")) {
      document.querySelector('.nz-navi[data-tab="scrap"]').click();
    }
  }

  /* ---------- 실시간 검색 (입력하면서 바로) ---------- */
  function debounce(fn, ms) {
    let t;
    return function () { clearTimeout(t); t = setTimeout(fn, ms); };
  }

  // 띄어쓰기로 나눈 각 단어를 별도 키워드로 본다 (모두 포함하는 페이지만 = AND)
  function splitTerms(q) {
    return (q || "").replace(/#/g, " ").split(/\s+/).map((t) => t.trim()).filter(Boolean);
  }

  const onEvInput = debounce(() => { searchEvidence(); }, 220);
  const onDocInput = debounce(() => { runSearchFromInput(); }, 220);

  /** 근거 문서 탭: 입력창의 단어들을 그대로 태그로 삼아 검색 */
  function runSearchFromInput() {
    const raw = $("docSearch").value;
    const typed = splitTerms(raw);
    searchTags = typed;          // 입력한 단어가 곧 태그
    renderTags();
    runSearch();
  }

  /** 검색어 단어별 색상 칩 — 어느 색이 어느 단어인지 보이게 */
  function renderTermBar(terms, hitCounts) {
    const bar = $("termBar");
    if (!bar) return;
    bar.innerHTML = terms.map((t, i) =>
      '<span class="nz-termchip"><span class="sw" style="background:' + HL_COLORS[i % HL_COLORS.length] +
      '"></span>' + esc(t) + (hitCounts && hitCounts[t] != null ? '<span class="n">' + hitCounts[t] + '</span>' : "") +
      '</span>').join("");
  }

  /** 스니펫의 [단어] 를 단어별 색으로 칠한다 */
  function markTerms(snippet, terms) {
    return esc(snippet).replace(/\[([^\]]*)\]/g, (m, w) => {
      let idx = terms.findIndex((t) => w.toLowerCase().includes(t.toLowerCase()));
      if (idx < 0) idx = 0;
      return '<mark style="background:' + HL_COLORS[idx % HL_COLORS.length] + '">' + w + '</mark>';
    });
  }

  /* ---------- 성취기준 선택 (줄바꿈 허용 커스텀 UI) ---------- */
  let curStdCode = "";

  function stdValue() { return curStdCode; }

  function toggleStdList() {
    const box = $("stdList");
    if (!box) return;
    if (box.classList.contains("hidden")) renderStdList();
    box.classList.toggle("hidden");
  }

  function renderStdList() {
    const box = $("stdList");
    box.innerHTML = scopedStandards().map((u) =>
      '<div class="nz-stdgrp">' + u.unit_no + '. ' + esc(u.name) + '</div>' +
      u.standards.map((st) =>
        '<div class="nz-stdopt' + (st.code === curStdCode ? " on" : "") + '"' +
        ' onclick="EP.pickStd(\'' + st.code + '\')"><span class="code">' + esc(st.code) + '</span>' +
        esc(st.text) + '</div>').join("")).join("");
  }

  function pickStd(code) {
    curStdCode = code;
    const st = standards.flatMap((u) => u.standards).find((x) => x.code === code);
    $("stdPickText").className = "";
    $("stdPickText").innerHTML = st
      ? '<span class="code">' + esc(st.code) + '</span>' + esc(st.text)
      : "성취기준을 고르세요";
    $("stdList").classList.add("hidden");
  }

  function setStdValue(code) {
    curStdCode = code || "";
    if (!code) {
      $("stdPickText").className = "ph";
      $("stdPickText").textContent = "성취기준을 고르세요";
    } else pickStd(code);
  }

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

  function toggleRoutines() {
    const box = $("routineList");
    if (!box) return;
    if (box.classList.contains("hidden")) {
      const pt = parseFloat($("qpoints").value) || 0;
      const suffix = pt ? " (" + (Number.isInteger(pt) ? pt : pt) + "점)" : "";
      box.innerHTML = ROUTINES.map((r, i) =>
        '<div class="nz-routine" onclick="EP.applyRoutine(' + i + ')"><b>' + r.g + '</b>' +
        esc(r.t + suffix) + '</div>').join("");
    }
    box.classList.toggle("hidden");
  }

  function applyRoutine(i) {
    const pt = parseFloat($("qpoints").value) || 0;
    const suffix = pt ? " (" + pt + "점)" : "";
    $("qask").value = ROUTINES[i].t + suffix;
    $("routineList").classList.add("hidden");
  }

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
  let checkState = {};

  function renderChecklist() {
    const box = $("checkBox");
    if (!box) return;
    box.innerHTML = CHECKS.map((c) =>
      '<label class="nz-chkrow"><input type="checkbox" ' + (checkState[c.k] ? "checked" : "") +
      ' onchange="EP.setCheck(\'' + c.k + '\', this.checked)" /><span>' + esc(c.t) + '</span></label>').join("") +
      '<div class="nz-fr" style="margin-top:6px"><label>확인 근거</label>' +
      '<textarea id="checkNote" rows="2" placeholder="무엇을 근거로 확인했는지 (예: 교과서 p.104, 3/12 수업에서 다룸)"' +
      ' oninput="EP.setCheckNote(this.value)">' + esc(checkState.note || "") + '</textarea></div>' +
      '<p class="nz-sub" id="checkStat"></p>';
    updateCheckStat();
  }
  function setCheck(k, v) { checkState[k] = v; updateCheckStat(); }
  function setCheckNote(v) { checkState.note = v; }
  function updateCheckStat() {
    const done = CHECKS.filter((c) => checkState[c.k]).length;
    const el = $("checkStat");
    if (el) el.innerHTML = done === CHECKS.length
      ? '<span class="g">확인 완료 — 근거를 적고 저장하세요</span>'
      : '확인 ' + done + '/' + CHECKS.length + " · '완성'으로 저장하려면 모두 확인해야 합니다";
  }

  /* ---------- 명제에서 고르기 ---------- */
  async function pickFor(target, idx) {
    const code = stdValue();
    const params = new URLSearchParams();
    if (code) params.set("standard", code);
    const rows = await api("/api/propositions?" + params);
    const detail = await Promise.all(rows.slice(0, 30).map((r) => api("/api/propositions/" + r.id)));
    let m = $("pickModal");
    if (!m) {
      m = document.createElement("div");
      m.id = "pickModal"; m.className = "nz-modal";
      m.onclick = (e) => { if (e.target === m) m.classList.add("hidden"); };
      document.body.appendChild(m);
    }
    m.classList.remove("hidden");
    const body = detail.length ? detail.map((d) => {
      const pj = JSON.stringify(d.proposition.text);
      let html = '<div class="nz-pickgroup"><div class="nz-pickprop"><span>' + esc(d.proposition.text) + '</span>' +
        '<button class="nz-tb mini blu" onclick=\'EP.applyPick("' + target + '",' + idx + ',' + pj + ',' + d.proposition.id + ',null)\'>참 명제로</button></div>';
      d.variants.forEach((v) => {
        html += '<div class="nz-pickvar"><span class="nz-tag r">' + esc(v.distortion) + '</span><span>' + esc(v.text) + '</span>' +
          '<button class="nz-tb mini" onclick=\'EP.applyPick("' + target + '",' + idx + ',' + JSON.stringify(v.text) + ',null,' + v.id + ')\'>오답으로</button></div>';
      });
      return html + '</div>';
    }).join("") : '<p class="nz-sub">이 성취기준에 등록된 명제가 없습니다. 명제 은행에서 먼저 등록하세요.</p>';

    m.innerHTML = '<div class="nz-modal-box" style="width:min(880px,94vw)">' +
      '<div class="nz-modal-head"><b>명제에서 고르기</b><span class="nz-sub" style="margin:0 0 0 10px">' +
      (code ? esc(code) : "전체") + ' 범위 · 참 명제와 오답 변형</span>' +
      '<button class="nz-tb" style="margin-left:auto" onclick="document.getElementById(\'pickModal\').classList.add(\'hidden\')">닫기</button></div>' +
      '<div class="nz-modal-body">' + body + '</div></div>';
  }

  function applyPick(target, idx, text, propId, varId) {
    if (target === "bogi") {
      bogi[idx] = Object.assign({}, bogi[idx], { text: text, proposition_id: propId, variant_id: varId });
      renderBogi();
    } else {
      choices[idx] = Object.assign({}, choices[idx], { text: text, proposition_id: propId, variant_id: varId });
      renderChoices();
    }
    $("pickModal").classList.add("hidden");
  }

  /* ---------- 환경설정 ---------- */
  async function loadConfig() {
    const subj = await api("/api/subject");
    $("cfgSubject").textContent = subj.subject + " · 성취기준 " + subj.standard_count + "개";
    const allStd = standards.flatMap((u) => u.standards).length;
    $("cfgTree").innerHTML = standards.map((u) => {
      const unitOn = !scope.length || scope.includes(u.unit_no);
      const stds = u.standards.map((st) => {
        const on = !stdScope.length || stdScope.includes(st.code);
        return '<label class="nz-cfgstd"><input type="checkbox" data-std="' + esc(st.code) + '"' +
          (on && unitOn ? " checked" : "") + ' /><span class="code">' + esc(st.code) + '</span> ' + esc(st.text) + '</label>';
      }).join("");
      return '<div class="nz-cfgunit"><label class="nz-cfghead"><input type="checkbox" data-unit="' + u.unit_no + '"' +
        (unitOn ? " checked" : "") + ' onchange="EP.cfgToggleUnit(' + u.unit_no + ', this.checked)" />' +
        '<b>' + u.unit_no + '. ' + esc(u.name) + '</b><span class="nz-sub" style="margin:0">' +
        u.standards.length + '개</span></label><div class="nz-cfgstds">' + stds + '</div></div>';
    }).join("");
  }
  function cfgToggleUnit(unitNo, on) {
    const head = document.querySelector('#cfgTree input[data-unit="' + unitNo + '"]');
    if (!head) return;
    head.closest(".nz-cfgunit").querySelectorAll("input[data-std]").forEach((i) => { i.checked = on; });
  }
  function cfgAll(on) {
    document.querySelectorAll("#cfgTree input").forEach((i) => { i.checked = on; });
  }
  function cfgSave() {
    const units = [...document.querySelectorAll("#cfgTree input[data-unit]:checked")].map((i) => +i.dataset.unit);
    const stds = [...document.querySelectorAll("#cfgTree input[data-std]:checked")].map((i) => i.dataset.std);
    const allStd = standards.flatMap((u) => u.standards).length;
    stdScope = (stds.length === allStd) ? [] : stds;
    localStorage.setItem("ep_std_scope", JSON.stringify(stdScope));
    const allUnits = standards.length;
    saveScope(units.length === allUnits ? [] : units);
    alert("범위를 저장했습니다.\n단원 " + (units.length || "전체") + " · 성취기준 " + (stdScope.length || "전체"));
  }

  /* ---------- 탭 ---------- */
  function initTabs() {
    document.querySelectorAll(".nz-navi").forEach((btn) => {
      btn.onclick = () => {
        document.querySelectorAll(".nz-navi").forEach((b) => b.classList.remove("on"));
        btn.classList.add("on");
        const tab = btn.dataset.tab;
        ["bank", "question", "qbank", "set", "doc", "scrap", "config"].forEach((t) => {
          const el = $("tab-" + t); if (el) el.hidden = t !== tab;
        });
        if (tab === "question") { loadPicker(); renderPresets(); renderChecklist(); onTypeChange(); }
        if (tab === "qbank") loadQuestions();
        if (tab === "scrap") loadScraps();
        if (tab === "config") loadConfig();
        if (tab === "set") loadSets();
        if (tab === "doc") loadDocs();
      };
    });
  }

  async function init() {
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".nz-stdwrap")) { const b = $("stdList"); if (b) b.classList.add("hidden"); }
      if (!e.target.closest(".nz-routinewrap")) { const b = $("routineList"); if (b) b.classList.add("hidden"); }
    });
    initTabs();
    if (localStorage.getItem("ep_side_folded")) {
      const el = $("qside");
      if (el) { el.classList.add("folded"); $("evFoldBtn").textContent = "◀"; }
    }
    await loadStandards();
    await loadProps();
  }
  document.addEventListener("DOMContentLoaded", init);

  return {
    loadProps, toggleForm, saveProp, delProp, openProp, addVariant, delVariant, delEvidence,
    markVerified, searchEvidenceFor, attachEvidence, exportCsv, peekPage,
    pickStandard, filterTree, openStdTable, closeStdTable, renderStdTable,
    openScope, applyScope, foldMenu, renderPresets, resetQuestionForm, evShow, evViewPage, evZoom,
    showStdFull, refillBankFilters, toggleSide, togglePicker, onBankUnitChange,
    setCheck, setCheckNote, renderChecklist, pickFor, applyPick,
    loadConfig, cfgToggleUnit, cfgAll, cfgSave, renderChoices, setSrc,
    toggleStdList, pickStd, toggleRoutines, applyRoutine,
    onEvInput, onDocInput, runSearchFromInput, useExam,
    loadScraps, openScrap, scrapNoteChanged, scrapDelete, scrapOpenOriginal,
    onTypeChange, addBogi, setBogi, delBogi, addChoice, setChoice, setCombo, setAnswer, delChoice,
    applyPreset, loadPicker, useProp, searchEvidence, saveQuestion, checkQuestionDraft,
    loadQuestions, editQuestion, checkQuestion, delQuestion,
    loadSets, createSet, loadSet, addItem, removeItem, dragStart, dropOn, checkSet, exportSet,
    loadDocs, toggleDocList, onlyDoc, pickFolder, indexFolder, delDoc,
    onTagKey, delTag, showPage, viewerPage, openOriginal,
  };
})();
