/* ===== 명제 Pool — 명제 목록 · 거짓 변형 · 근거 ===== */
(function (EP) {
  "use strict";
  const $ = EP.$, esc = EP.esc, escAttr = EP.escAttr, api = EP.api, post = EP.post, del = EP.del;

  EP.loadProps = async function () {
    const params = new URLSearchParams();
    const std = $("q-std").value, q = $("q-text").value.trim();
    if (std) params.set("standard", std);
    if (q) params.set("q", q);
    const rows = await api("/api/propositions?" + params);
    const allow = EP.allowedCodes();
    EP.S.curProps = allow ? rows.filter((r) => allow.has(r.standard_code)) : rows;
    const firstRun = $("firstRunGuide");
    if (firstRun) firstRun.classList.toggle("hidden", EP.S.curProps.length > 0);
    const tb = $("propRows");
    tb.innerHTML = "";
    let ev = 0, va = 0;
    if (!EP.S.curProps.length) {
      tb.innerHTML = '<tr class="nz-empty"><td colspan="8">조회된 명제가 없습니다. “＋ 명제 등록”으로 추가하세요.</td></tr>';
    }
    EP.S.curProps.forEach((r, i) => {
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
    $("cnt").textContent = EP.S.curProps.length;
    $("footL").textContent = `명제 ${EP.S.curProps.length}건`;
    $("footR").textContent = `근거 ${ev}건 · 변형 ${va}건`;
  };

  EP.toggleForm = function (show) {
    $("propForm").classList.toggle("hidden", !show);
    if (show) $("f-text").focus();
  };

  EP.saveProp = async function () {
    const text = $("f-text").value.trim(), standard_code = $("f-std").value;
    if (!text) return alert("명제 내용을 입력하세요.");
    if (!standard_code) return alert("성취기준을 선택하세요.");
    await post("/api/propositions", { text, standard_code, tags: $("f-tags").value });
    $("f-text").value = ""; $("f-tags").value = "";
    EP.toggleForm(false); EP.loadProps();
  };

  EP.delProp = async function (id) {
    if (!confirm("이 명제를 삭제할까요? (변형·근거도 함께 삭제됩니다)")) return;
    await del(`/api/propositions/${id}`);
    $("propDetail").classList.add("hidden");
    EP.S.curPropId = null;
    EP.loadProps();
  };

  /* ---------- 명제 상세: 변형·근거 ---------- */
  EP.openProp = async function (id) {
    const d = await api(`/api/propositions/${id}`);
    // 오른쪽 근거 검색 패널이 "어느 명제에 붙일지" 를 알아야 '근거로 저장' 이 뜬다
    EP.S.curPropId = id;
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
          || '<tr class="nz-empty"><td colspan="4">아직 없습니다. 아래에서 교과서·수업 기록을 검색해 붙이세요.</td></tr>'}</tbody></table>

        <div class="nz-fr" style="margin-top:6px">
          <input id="pev-q" placeholder="교과서·교육과정·기출·수업 기록 검색 (예: 빛 굴절)"
            onkeydown="if(event.key==='Enter')EP.findEvidenceFor()" />
          <button class="nz-tb blu" onclick="EP.findEvidenceFor()">오른쪽에서 근거 찾기</button>
          <button class="nz-tb" onclick="EP.markVerified(${id})">수업에서 다룸 표시</button>
        </div>
        <p class="nz-sub">찾은 결과에서 <b>근거로 저장</b>을 누르면 이 명제에 붙습니다.</p>
      </div>`;
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
    EP.searchEvidence();          // 이미 검색어가 있으면 '근거로 저장' 버튼이 바로 붙게 다시 그린다
  };

  /** 명제 상세의 검색어를 오른쪽 근거 검색 패널로 넘긴다 (검색은 한 곳에서만 한다) */
  EP.findEvidenceFor = function () {
    const q = $("pev-q").value.trim();
    if (!q) return;
    const side = $("qside");
    if (side && side.classList.contains("folded")) EP.toggleSide();
    $("evSearch").value = q;
    EP.searchEvidence();
  };

  EP.addVariant = async function (pid) {
    const text = $("v-text").value.trim();
    if (!text) return alert("거짓 문장을 입력하세요.");
    await post("/api/variants", { proposition_id: pid, text, distortion: $("v-dist").value });
    EP.openProp(pid); EP.loadProps();
  };
  EP.delVariant = async function (vid, pid) { await del(`/api/variants/${vid}`); EP.openProp(pid); EP.loadProps(); };
  EP.delEvidence = async function (eid, pid) { await del(`/api/evidence/${eid}`); EP.openProp(pid); EP.loadProps(); };
  EP.markVerified = async function (pid) {
    await api(`/api/propositions/${pid}/class-verified?value=true`, { method: "PATCH" });
    EP.openProp(pid); EP.loadProps();
  };

  /** 근거를 명제에 붙인다. 수업 기록이 근거면 '수업에서 다룸'이 자동으로 켜진다 —
   *  출제 조건 2번을 사람이 따로 체크하지 않아도 기록이 답하게 하는 것이 요점이다. */
  EP.attachEvidence = async function (pid, hit) {
    const isLesson = hit.kind === "수업";
    await post("/api/evidence", {
      proposition_id: pid,
      source_type: isLesson ? "수업" : "교과서",
      source_label: hit.source_label,
      quote: hit.snippet.replace(/[\[\]]/g, ""),
      document_page_id: null,
    });
    if (isLesson) await api(`/api/propositions/${pid}/class-verified?value=true`, { method: "PATCH" });
    EP.openProp(pid); EP.loadProps();
  };

  EP.exportCsv = function () {
    if (!EP.S.curProps.length) return alert("내보낼 명제가 없습니다.");
    const head = "성취기준,명제,단원,근거,변형,수업\n";
    const body = EP.S.curProps.map((r) => [r.standard_code, `"${r.text.replace(/"/g, '""')}"`,
      r.unit_name || "", r.ev_count, r.var_count, r.class_verified ? "O" : ""].join(",")).join("\n");
    const blob = new Blob(["﻿" + head + body], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "명제목록.csv"; a.click();
  };

  /* ---------- 명제에서 고르기 (문항 설계에서 부름) ---------- */
  EP.pickFor = async function (target, idx) {
    const code = EP.stdValue();
    const params = new URLSearchParams();
    if (code) params.set("standard", code);
    let rows = await api("/api/propositions?" + params);
    const allowP = EP.allowedCodes();
    if (allowP) rows = rows.filter((r) => allowP.has(r.standard_code));
    const detail = await Promise.all(rows.slice(0, 30).map((r) => api("/api/propositions/" + r.id)));
    const m = EP.modal("pickModal");
    const body = detail.length ? detail.map((d, di) => {
      let html = '<div class="nz-pickgroup"><div class="nz-pickprop"><span>' + esc(d.proposition.text) + "</span>" +
        '<button class="nz-tb mini blu" data-pick-prop data-di="' + di + '" data-target="' + escAttr(target) + '" data-idx="' + idx + '">참 명제로</button></div>';
      d.variants.forEach((v, vi) => {
        html += '<div class="nz-pickvar"><span class="nz-tag r">' + esc(v.distortion) + "</span><span>" + esc(v.text) + "</span>" +
          '<button class="nz-tb mini" data-pick-prop data-di="' + di + '" data-vi="' + vi + '" data-target="' + escAttr(target) + '" data-idx="' + idx + '">오답으로</button></div>';
      });
      return html + "</div>";
    }).join("") : '<p class="nz-sub">이 성취기준에 등록된 명제가 없습니다. 명제 Pool에서 먼저 등록하세요.</p>';

    m.innerHTML = '<div class="nz-modal-box" style="width:min(880px,94vw)">' +
      '<div class="nz-modal-head"><b>명제에서 고르기</b><span class="nz-sub" style="margin:0 0 0 10px">' +
        detail.length + "개 명제</span>" +
      '<button class="nz-tb" style="margin-left:auto" onclick="EP.closeModal(\'pickModal\')">닫기</button></div>' +
      '<div class="nz-modal-body" id="pickBody">' + body + "</div></div>";
    // 버튼 이벤트 위임 — onclick 인라이닝 없이 data 속성으로 안전하게 처리
    document.getElementById("pickBody").addEventListener("click", function (e) {
      const btn = e.target.closest("[data-pick-prop]");
      if (!btn) return;
      const di = parseInt(btn.dataset.di), vi = btn.dataset.vi;
      const d = detail[di];
      const target = btn.dataset.target, idx = parseInt(btn.dataset.idx);
      const primaryEvidence = (d.evidence || [])[0];
      const evidence = primaryEvidence
        ? [primaryEvidence.source_label, primaryEvidence.quote].filter(Boolean).join(" — ") : "";
      if (vi !== undefined) {
        const variant = d.variants[parseInt(vi)];
        EP.applyPick(target, idx, variant.text, null, variant.id, evidence,
          `근거가 되는 참 명제를 '${variant.distortion || "개념 변형"}' 방식으로 바꾼 진술이므로 옳지 않다.`);
      } else {
        EP.applyPick(target, idx, d.proposition.text, d.proposition.id, null, evidence,
          "연결된 근거가 이 보기의 내용을 직접 뒷받침하므로 옳다.");
      }
    });
  };

  EP.applyPick = function (target, idx, text, propId, varId, evidence, explanation) {
    if (target === "bogi") {
      EP.S.bogi[idx] = Object.assign({}, EP.S.bogi[idx], {
        text: text, proposition_id: propId, variant_id: varId,
        evidence: evidence || EP.S.bogi[idx].evidence || "",
        explanation: explanation || EP.S.bogi[idx].explanation || "",
      });
      EP.renderBogi();
    } else {
      EP.S.choices[idx] = Object.assign({}, EP.S.choices[idx], { text: text, proposition_id: propId, variant_id: varId });
      EP.renderChoices();
    }
    EP.closeModal("pickModal");
  };
})(window.EP);
