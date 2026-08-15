/* ===== 문항 Pool — 만든 문항 목록·상태·검토 ===== */
(function (EP) {
  "use strict";
  const $ = EP.$, esc = EP.esc, api = EP.api, del = EP.del;
  const S = EP.S;
  EP.questionEditSequence = 0;

  const DEFAULT_DIRECTIONS = {
    created: "desc", updated: "desc", status: "desc", review: "desc", usage: "desc",
    standard: "asc", difficulty: "desc", qtype: "asc", origin: "asc", points: "desc",
  };
  let sortDirection = localStorage.getItem("ep_qbank_sort_direction") || "desc";

  function shortDate(value) {
    if (!value) return "-";
    const part = String(value).split(" ")[0];
    return part.length >= 10 ? part.slice(2) : part;
  }

  function rowTitle(row) {
    const value = row.title || row.passage || row.ask || `문항 ${row.id}`;
    return value.length > 28 ? value.slice(0, 28) + "…" : value;
  }

  function updateSortButton() {
    const btn = $("qbSortDir");
    if (!btn) return;
    btn.textContent = sortDirection === "desc" ? "내림차순 ↓" : "오름차순 ↑";
    btn.setAttribute("aria-label", btn.textContent);
  }

  EP.changeQuestionSort = function () {
    const key = $("qbSort").value;
    sortDirection = DEFAULT_DIRECTIONS[key] || "desc";
    localStorage.setItem("ep_qbank_sort", key);
    localStorage.setItem("ep_qbank_sort_direction", sortDirection);
    updateSortButton();
    EP.loadQuestions();
  };

  EP.toggleQuestionSort = function () {
    sortDirection = sortDirection === "desc" ? "asc" : "desc";
    localStorage.setItem("ep_qbank_sort_direction", sortDirection);
    updateSortButton();
    EP.loadQuestions();
  };

  EP.loadQuestions = async function () {
    EP.refillBankFilters();
    const params = new URLSearchParams();
    const std = $("qbStd") ? $("qbStd").value : "";
    const q = $("qbSearch") ? $("qbSearch").value.trim() : "";
    if (std) params.set("standard", std);
    if (q) params.set("q", q);
    const stt = $("qbStatus") ? $("qbStatus").value : "";
    if (stt) params.set("status", stt);
    const sort = $("qbSort") ? $("qbSort").value : "created";
    params.set("sort", sort);
    params.set("direction", sortDirection);
    let rows = await api("/api/questions?" + params);
    const serverCount = rows.length;
    const allowQ = EP.allowedCodes();
    if (allowQ) rows = rows.filter((r) => !r.standard_code || allowQ.has(r.standard_code));
    const hiddenByScope = serverCount - rows.length;
    const scopeNote = $("qbScopeNote");
    if (scopeNote) {
      scopeNote.hidden = hiddenByScope <= 0;
      scopeNote.innerHTML = hiddenByScope > 0
        ? `현재 과목 범위 밖 문항 ${hiddenByScope}개는 숨겨져 있습니다. ` +
          '<button class="nz-tb mini" onclick="document.querySelector(\'.nz-navi[data-tab=&quot;config&quot;]\').click()">범위 설정</button>'
        : "";
    }
    const unit = $("qbUnit") ? +$("qbUnit").value : 0;
    if (unit && !std) {           // 단원만 고른 경우: 그 단원의 성취기준들로 거른다
      const codes = new Set((EP.scopedStandards().find((u) => u.unit_no === unit) || { standards: [] })
        .standards.map((x) => x.code));
      rows = rows.filter((r) => codes.has(r.standard_code));
    }
    $("qcnt").textContent = rows.length;
    const st = { "초안": 0, "검토중": 0, "완성": 0 };
    rows.forEach((r) => { st[r.status || "초안"] = (st[r.status || "초안"] || 0) + 1; });
    if ($("qbStats")) {
      $("qbStats").innerHTML =
        `<div class="nz-statcard"><b>${rows.length}</b>전체</div>` +
        `<div class="nz-statcard"><b>${st["초안"]}</b>초안</div>` +
        `<div class="nz-statcard"><b>${st["검토중"]}</b>검토중</div>` +
        `<div class="nz-statcard"><b>${st["완성"]}</b>완성</div>`;
    }
    $("qRows").innerHTML = rows.map((r) => `
      <tr><td class="cc code">#${r.id}</td><td class="cc qb-date" title="${esc(r.created_at || "")}">${shortDate(r.created_at)}</td>
      <td class="cc"><span class="nz-badge ${esc(r.status || "초안")}">${esc(r.status || "초안")}</span></td>
      <td class="qb-title" title="${esc(r.title || r.passage || r.ask || "")}">${esc(rowTitle(r))}</td>
      <td class="cc">${esc(r.qtype)}${r.is_negative ? "·부정" : ""}</td>
      <td>${esc(r.ask)}</td><td class="cc code">${esc(r.standard_code || "-")}</td>
      <td class="cc">${esc(r.behavior || "-")}</td>
      <td class="cc">${esc(r.origin || "-")}</td>
      <td class="cc">${esc(r.difficulty)}</td><td class="cc">${r.default_points}</td>
      <td class="cc"><span class="qb-review ${r.review_error_count ? "bad" : (r.review_warn_count ? "warn" : "good")}">${r.review_error_count ? `오류 ${r.review_error_count}` : (r.review_warn_count ? `경고 ${r.review_warn_count}` : "통과")}</span></td>
      <td class="cc">${r.usage_count || 0}</td>
      <td class="cc"><button class="nz-tb mini" onclick="EP.editQuestion(${r.id}, true)">수정</button>
      <button class="nz-tb mini" onclick="EP.checkQuestion(${r.id})">검토</button>
      <button class="nz-tb mini" onclick="EP.delQuestion(${r.id})">×</button></td></tr>`).join("")
      || '<tr class="nz-empty"><td colspan="14">문항이 없습니다.</td></tr>';
  };

  EP.editQuestion = async function (qid, jump) {
    const editSequence = ++EP.questionEditSequence;
    if (jump) {   // 문항 Pool에서 눌렀으면 설계 탭으로 이동
      document.querySelector('.nz-navi[data-tab="question"]').click();
    }
    const d = await api(`/api/questions/${qid}`);
    if (editSequence !== EP.questionEditSequence) return;
    S.editingQid = qid;
    const q = d.question;
    $("qtitle").value = q.title || "";
    $("qtype").value = q.qtype; $("isNeg").checked = !!q.is_negative;
    S.paletteTemplate = (q.style_meta && q.style_meta.palette_template) || "";
    if (EP.loadQuestionPaletteOptions) await EP.loadQuestionPaletteOptions(S.paletteTemplate);
    $("qpassage").value = q.passage; $("qmaterial").value = q.material;
    $("qask").value = q.ask; $("qintent").value = q.intent;
    if ($("qexplanation")) $("qexplanation").value = q.explanation || "";
    $("qpoints").value = q.default_points; $("qdiff").value = q.difficulty;
    if ($("qbehavior")) $("qbehavior").value = q.behavior || "";
    if ($("qorigin")) $("qorigin").value = q.origin || "";
    if ($("qoriginNote")) $("qoriginNote").value = q.origin_note || "";
    // 서술형은 answer 칸에 모범답안 전문이 들어 있다 (선지형은 정답 번호)
    if ($("qModelAnswer")) $("qModelAnswer").value = q.qtype === "서술형" ? (q.answer || "") : "";
    EP.setStdValue(q.standard_code || "");
    S.bogi = q.bogi_items || [];
    if ($("qstatus")) $("qstatus").value = q.status || "초안";
    if ($("imgChoices")) $("imgChoices").checked = !!q.image_choices;
    try { S.checkState = JSON.parse(q.review_note || "{}"); } catch (e) { S.checkState = {}; }
    EP.renderChecklist();
    S.choices = d.choices.map((c) => ({ ...c, is_answer: !!c.is_answer }));
    EP.onTypeChange(); EP.renderBogi(); EP.renderChoices();
    if (EP.authoringOpen) await EP.authoringOpen(qid, true);
    else await EP.loadRefs(qid);
    if (editSequence !== EP.questionEditSequence) return;
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  EP.checkQuestion = async function (qid) {
    const r = await api(`/api/questions/${qid}/check`);
    alert(r.ok && !r.warn_count ? "검토 통과 (지적 사항 없음)"
      : "검토 결과:\n" + r.issues.map((i) => `[${i.level === "error" ? "오류" : "경고"}] ${i.message}`).join("\n"));
  };

  EP.delQuestion = async function (qid) {
    if (!confirm("이 문항을 삭제할까요?")) return;
    await del(`/api/questions/${qid}`);
    EP.loadQuestions();
  };

  document.addEventListener("DOMContentLoaded", () => {
    const savedSort = localStorage.getItem("ep_qbank_sort");
    if (savedSort && $("qbSort") && DEFAULT_DIRECTIONS[savedSort]) $("qbSort").value = savedSort;
    updateSortButton();
  });
})(window.EP);
