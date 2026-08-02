/* ===== 청사진(계획) — AI 출제 주문서 =====
 * 슬롯 = 아직 문항이 없는 set_item. 여기서 유형·성취기준·배점·그림을 계획하고
 * "출제 지시문 복사"로 Claude Code 에 넘긴다. 문항이 채워지면 잠긴 행으로 보인다.
 */
(function (EP) {
  "use strict";
  const $ = EP.$, esc = EP.esc, api = EP.api, post = EP.post;
  const S = EP.S;
  const QTYPES = ["정답형", "합답형", "서술형"];
  const STATUS = { empty: "계획", generated: "생성됨", reviewed: "검토됨" };

  let bp = null;

  function stdOptions(selected) {
    const opts = EP.scopedStandards()
      .flatMap((u) => u.standards.map((s) => ({ v: s.code, t: `${s.code} ${s.text}` })));
    return ['<option value="">성취기준 선택</option>']
      .concat(opts.map((o) =>
        `<option value="${esc(o.v)}" ${o.v === selected ? "selected" : ""}>${esc(o.t.length > 46 ? o.t.slice(0, 46) + "…" : o.t)}</option>`))
      .join("");
  }

  EP.loadBlueprint = async function () {
    if (!S.curSetId) { $("bpRows").innerHTML = ""; return; }
    bp = await api(`/api/sets/${S.curSetId}/blueprint`);
    $("bpShort").value = bp.set.short_code || "";

    const rows = bp.slots.map((s) => {
      if (s.question_id) {
        // 문항이 채워진 슬롯 — 계획은 잠그고 연결 상태만 보여준다 (교체는 담긴 문항 표에서)
        return `<tr class="bp-done">
          <td class="cc">${s.ord}</td>
          <td class="cc">${esc(s.plan_qtype || "-")}</td>
          <td colspan="5">문항 #${s.question_id} 연결됨 — ${esc((s.q_ask || "").slice(0, 60))}</td>
          <td class="cc">${s.points ?? "-"}</td>
          <td class="cc"><span class="bp-chip on">${esc(STATUS[s.slot_status] || s.slot_status || "생성됨")}</span></td>
          <td class="cc"></td></tr>`;
      }
      const i = s.item_id;
      return `<tr>
          <td class="cc">${s.ord}</td>
          <td class="cc"><select class="nz-sel mini" onchange="EP.bpPatch(${i},'plan_qtype',this.value)">
            ${QTYPES.map((t) => `<option ${t === s.plan_qtype ? "selected" : ""}>${t}</option>`).join("")}</select></td>
          <td><select class="nz-sel mini w100" onchange="EP.bpPatch(${i},'plan_standard_code',this.value)">
            ${stdOptions(s.plan_standard_code)}</select></td>
          <td><input class="nz-inp mini w100" value="${esc(s.plan_topic)}" placeholder="주제 (예: 단진자 에너지)"
               onchange="EP.bpPatch(${i},'plan_topic',this.value)" /></td>
          <td class="cc"><input type="checkbox" ${s.plan_is_negative ? "checked" : ""}
               onchange="EP.bpPatch(${i},'plan_is_negative',this.checked)" /></td>
          <td class="cc"><input type="checkbox" ${s.plan_needs_figure ? "checked" : ""}
               onchange="EP.bpPatch(${i},'plan_needs_figure',this.checked)" /></td>
          <td><input class="nz-inp mini w100" value="${esc(s.plan_figure_hint)}"
               placeholder="${s.plan_needs_figure ? `그림 지시 → ${esc(s.figure_name)}` : "그림 없음"}"
               ${s.plan_needs_figure ? "" : "disabled"}
               onchange="EP.bpPatch(${i},'plan_figure_hint',this.value)" /></td>
          <td class="cc"><input class="nz-pt" type="number" step="0.5" min="0" value="${s.points ?? ""}"
               placeholder="점" onchange="EP.bpPatch(${i},'points',this.value)" /></td>
          <td class="cc"><span class="bp-chip">${esc(STATUS[s.slot_status] || "계획")}</span></td>
          <td class="cc"><button class="nz-tb mini" onclick="EP.removeItem(${i})">×</button></td>
        </tr>
        <tr class="bp-sub"><td></td><td colspan="9">
          <input class="nz-inp mini w100" value="${esc(s.plan_situation)}"
            placeholder="상황 묘사 — 어떤 물리적 상황인지 서술 (예: 용수철에 매단 추를 당겼다 놓는다)"
            onchange="EP.bpPatch(${i},'plan_situation',this.value)" /></td></tr>`;
    }).join("");

    $("bpRows").innerHTML = rows ||
      '<tr class="nz-empty"><td colspan="10">슬롯이 없습니다 — "슬롯 추가"로 출제 계획을 시작하세요.</td></tr>';

    const planned = bp.slots.reduce((n, s) => n + (s.points || 0), 0);
    const figs = bp.slots.filter((s) => s.plan_needs_figure && !s.question_id).length;
    const empty = bp.slots.filter((s) => !s.question_id).length;
    $("bpFoot").textContent =
      `슬롯 ${bp.slots.length}개 (빈 슬롯 ${empty}) · 계획 배점 합 ${planned}점 / 만점 ${bp.set.total_points}점 · 그림 ${figs}장 예정`;
  };

  EP.bpPatch = async function (itemId, field, value) {
    const body = {};
    body[field] = field === "points" ? (parseFloat(value) || null) : value;
    try {
      await EP.patch(`/api/sets/${S.curSetId}/slots/${itemId}`, body);
    } catch (e) { alert(e.message); }
    EP.loadBlueprint();
  };

  EP.addSlot = async function () {
    if (!S.curSetId) return alert("세트를 먼저 고르세요.");
    // 직전 슬롯의 유형을 이어받는다 — 같은 유형을 몰아 계획하는 흐름이 잦다
    const last = bp && bp.slots.length ? bp.slots[bp.slots.length - 1] : null;
    await post(`/api/sets/${S.curSetId}/slots`,
      { plan_qtype: last ? last.plan_qtype : "정답형" });
    EP.loadBlueprint();
  };

  EP.saveShortCode = async function () {
    if (!S.curSetId) return alert("세트를 먼저 고르세요.");
    try {
      await EP.put(`/api/sets/${S.curSetId}/short-code`, { short_code: $("bpShort").value });
    } catch (e) { return alert(e.message.replace(/.*"detail":"([^"]+)".*/, "$1")); }
    EP.loadBlueprint();
  };

  EP.copyPrompt = async function () {
    if (!S.curSetId) return alert("세트를 먼저 고르세요.");
    let r;
    try {
      r = await api(`/api/sets/${S.curSetId}/prompt`);
    } catch (e) { return alert(e.message.replace(/.*"detail":"([^"]+)".*/, "$1")); }
    const ok = await EP.copy(r.prompt,
      `빈 슬롯 ${r.slot_count}개의 출제 지시문을 복사했습니다.\n` +
      "32_exam_pool 폴더에서 연 Claude Code 세션에 붙여넣으세요.");
    if (!ok) {
      $("bpPromptBox").classList.remove("hidden");
      $("bpPromptText").value = r.prompt;
      $("bpPromptText").select();
    }
  };
})(window.EP);
