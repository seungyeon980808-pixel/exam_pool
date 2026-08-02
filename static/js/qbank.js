/* ===== 문항 Pool — 만든 문항 목록·상태·검토 ===== */
(function (EP) {
  "use strict";
  const $ = EP.$, esc = EP.esc, api = EP.api, del = EP.del;
  const S = EP.S;

  EP.loadQuestions = async function () {
    EP.refillBankFilters();
    const params = new URLSearchParams();
    const std = $("qbStd") ? $("qbStd").value : "";
    const q = $("qbSearch") ? $("qbSearch").value.trim() : "";
    if (std) params.set("standard", std);
    if (q) params.set("q", q);
    const stt = $("qbStatus") ? $("qbStatus").value : "";
    if (stt) params.set("status", stt);
    let rows = await api("/api/questions?" + params);
    const allowQ = EP.allowedCodes();
    if (allowQ) rows = rows.filter((r) => !r.standard_code || allowQ.has(r.standard_code));
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
    $("qRows").innerHTML = rows.map((r, i) => `
      <tr><td class="cc">${i + 1}</td>
      <td class="cc"><span class="nz-badge ${esc(r.status || "초안")}">${esc(r.status || "초안")}</span></td>
      <td>${esc(r.title || "-")}</td>
      <td class="cc">${esc(r.qtype)}${r.is_negative ? "·부정" : ""}</td>
      <td>${esc(r.ask)}</td><td class="cc code">${esc(r.standard_code || "-")}</td>
      <td class="cc">${esc(r.behavior || "-")}</td>
      <td class="cc">${esc(r.origin || "-")}</td>
      <td class="cc">${esc(r.difficulty)}</td><td class="cc">${r.default_points}</td>
      <td class="cc"><button class="nz-tb mini" onclick="EP.editQuestion(${r.id}, true)">수정</button>
      <button class="nz-tb mini" onclick="EP.checkQuestion(${r.id})">검토</button>
      <button class="nz-tb mini" onclick="EP.delQuestion(${r.id})">×</button></td></tr>`).join("")
      || '<tr class="nz-empty"><td colspan="11">문항이 없습니다.</td></tr>';
  };

  EP.editQuestion = async function (qid, jump) {
    if (jump) {   // 문항 Pool에서 눌렀으면 설계 탭으로 이동
      document.querySelector('.nz-navi[data-tab="question"]').click();
    }
    const d = await api(`/api/questions/${qid}`);
    S.editingQid = qid;
    const q = d.question;
    $("qtitle").value = q.title || "";
    $("qtype").value = q.qtype; $("isNeg").checked = !!q.is_negative;
    $("qpassage").value = q.passage; $("qmaterial").value = q.material;
    $("qask").value = q.ask; $("qintent").value = q.intent;
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
    EP.loadRefs(qid);
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
})(window.EP);
