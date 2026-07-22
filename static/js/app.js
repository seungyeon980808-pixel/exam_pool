/* ===== ExamPool — 명제 은행 (Phase 1) ===== */
const EP = (() => {
  let standards = [];   // [{unit_no, name, standards:[{code,text,...}]}]
  let curStd = "";      // 좌측 트리에서 고른 성취기준

  // ----- 공통 fetch -----
  async function api(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  // ----- 성취기준 트리 + 조회/등록 select -----
  async function loadStandards() {
    standards = await api("/api/standards");

    // 좌측 트리
    const tree = document.getElementById("tree");
    tree.innerHTML = "";
    standards.forEach((u) => {
      const uEl = document.createElement("div");
      uEl.className = "nz-mi";
      uEl.textContent = `${u.unit_no}. ${u.name}`;
      uEl.onclick = () => toggleUnit(u.unit_no, uEl);
      tree.appendChild(uEl);
    });

    // 조회 필터 select + 등록 폼 select
    const qStd = document.getElementById("q-std");
    const fStd = document.getElementById("f-std");
    qStd.innerHTML = '<option value="">전체</option>';
    fStd.innerHTML = "";
    standards.forEach((u) => {
      u.standards.forEach((s) => {
        const label = `${s.code} ${s.text.slice(0, 22)}…`;
        qStd.appendChild(new Option(label, s.code));
        fStd.appendChild(new Option(label, s.code));
      });
    });
  }

  function toggleUnit(unitNo, uEl) {
    // 이미 펼쳐진 하위 제거
    let next = uEl.nextElementSibling;
    if (next && next.classList.contains("sub")) {
      while (next && next.classList.contains("sub")) {
        const rm = next; next = next.nextElementSibling; rm.remove();
      }
      return;
    }
    const unit = standards.find((u) => u.unit_no === unitNo);
    const frag = document.createDocumentFragment();
    unit.standards.forEach((s) => {
      const sEl = document.createElement("div");
      sEl.className = "nz-mi sub";
      sEl.textContent = `· ${s.code}`;
      sEl.onclick = (e) => { e.stopPropagation(); pickStd(s.code, sEl); };
      frag.appendChild(sEl);
    });
    uEl.after(frag);
  }

  function pickStd(code, el) {
    document.querySelectorAll(".nz-mi.on").forEach((n) => n.classList.remove("on"));
    el.classList.add("on");
    curStd = code;
    document.getElementById("q-std").value = code;
    loadProps();
  }

  // ----- 명제 목록 -----
  async function loadProps() {
    const std = document.getElementById("q-std").value;
    const q = document.getElementById("q-text").value.trim();
    const params = new URLSearchParams();
    if (std) params.set("standard", std);
    if (q) params.set("q", q);
    const rows = await api("/api/propositions?" + params.toString());

    const tbody = document.getElementById("propRows");
    tbody.innerHTML = "";
    let evTotal = 0, varTotal = 0;
    if (!rows.length) {
      tbody.innerHTML = '<tr class="nz-empty"><td colspan="8">조회된 명제가 없습니다. “＋ 명제 등록”으로 추가하세요.</td></tr>';
    }
    rows.forEach((r, i) => {
      evTotal += r.ev_count; varTotal += r.var_count;
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td class="cc">${i + 1}</td>` +
        `<td class="cc code">${r.standard_code}</td>` +
        `<td>${escapeHtml(r.text)}</td>` +
        `<td class="cc">${r.unit_name ? escapeHtml(r.unit_name) : "-"}</td>` +
        `<td class="cc ${r.ev_count ? "g" : ""}">${r.ev_count || "-"}</td>` +
        `<td class="cc ${r.var_count ? "r" : ""}">${r.var_count || "-"}</td>` +
        `<td class="cc ${r.class_verified ? "g" : ""}">${r.class_verified ? "✓" : "-"}</td>` +
        `<td class="cc"><button class="nz-tb" style="padding:2px 7px" onclick="EP.delProp(${r.id})">×</button></td>`;
      tbody.appendChild(tr);
    });
    document.getElementById("cnt").textContent = rows.length;
    document.getElementById("footR").textContent = `근거 ${evTotal}건 · 변형 ${varTotal}건`;
  }

  // ----- 등록 -----
  function toggleForm(show) {
    document.getElementById("propForm").classList.toggle("hidden", !show);
    if (show) document.getElementById("f-text").focus();
  }

  async function saveProp() {
    const text = document.getElementById("f-text").value.trim();
    const standard_code = document.getElementById("f-std").value;
    if (!text) { alert("명제 내용을 입력하세요."); return; }
    if (!standard_code) { alert("성취기준을 선택하세요."); return; }
    await api("/api/propositions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text, standard_code,
        tags: document.getElementById("f-tags").value,
        note: document.getElementById("f-note").value,
      }),
    });
    document.getElementById("f-text").value = "";
    document.getElementById("f-tags").value = "";
    document.getElementById("f-note").value = "";
    toggleForm(false);
    loadProps();
  }

  async function delProp(id) {
    if (!confirm("이 명제를 삭제할까요? (연결된 변형·근거도 함께 삭제됩니다)")) return;
    await api(`/api/propositions/${id}`, { method: "DELETE" });
    loadProps();
  }

  // ----- 탭 -----
  function initTabs() {
    document.querySelectorAll(".nz-navi").forEach((btn) => {
      btn.onclick = () => {
        document.querySelectorAll(".nz-navi").forEach((b) => b.classList.remove("on"));
        btn.classList.add("on");
        const tab = btn.dataset.tab;
        ["bank", "question", "set", "doc"].forEach((t) => {
          document.getElementById("tab-" + t).hidden = t !== tab;
        });
      };
    });
  }

  function notReady(name) { alert(`“${name}” 은 다음 단계에서 구현합니다.`); }

  function escapeHtml(s) {
    return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  async function init() {
    initTabs();
    await loadStandards();
    await loadProps();
  }
  document.addEventListener("DOMContentLoaded", init);

  return { loadProps, toggleForm, saveProp, delProp, notReady };
})();
