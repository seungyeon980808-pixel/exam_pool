/* ===== 세트 관리 — 배열·배점·검토·hwppalette 출력·제출 서류 ===== */
(function (EP) {
  "use strict";
  const $ = EP.$, esc = EP.esc, api = EP.api, post = EP.post, put = EP.put, del = EP.del;
  const S = EP.S;

  EP.loadSets = async function () {
    const sets = await api("/api/sets");
    EP.fillSelect($("setSel"), sets.map((s) => ({ v: s.id, t: `${s.name} (${s.item_count}문항)` })),
      sets.length ? null : "세트 없음");
    if (sets.length) { $("setSel").value = S.curSetId || sets[0].id; EP.loadSet(); }
  };

  EP.createSet = async function () {
    const name = $("newSetName").value.trim();
    if (!name) return alert("세트 이름을 입력하세요.");
    const r = await post("/api/sets", { name, total_points: parseFloat($("newSetTotal").value) || 100 });
    S.curSetId = r.id; $("newSetName").value = "";
    await EP.loadSets();
  };

  EP.loadSet = async function () {
    const sid = $("setSel").value;
    if (!sid) { $("setRows").innerHTML = ""; $("setDash").innerHTML = ""; return; }
    S.curSetId = sid;
    EP.loadBlueprint();                       // 청사진(계획) 표 — 빈 슬롯은 여기서만 보인다
    const d = await api(`/api/sets/${sid}`);
    const dash = d.dashboard;

    // 만점은 세트마다 다르다 (지필 70 + 수행 30 같은 시험이 흔하다)
    $("setTotal").value = dash.target_points;
    const gap = dash.gap;
    const gapLabel = gap === 0 ? '<span class="g">딱 맞음</span>'
      : gap > 0 ? `<span class="r">+${gap}점 초과</span>` : `<span class="r">${gap}점 부족</span>`;

    $("setDash").innerHTML = `
      <div class="nz-mc"><p class="nz-mlbl">배점 합 / 만점</p>
        <p class="nz-mval">${dash.total_points} <span class="unit">/ ${dash.target_points}점</span></p>
        <p class="nz-msub">${gapLabel}</p></div>
      <div class="nz-mc"><p class="nz-mlbl">문항 수</p><p class="nz-mval">${dash.count}개</p></div>
      <div class="nz-mc"><p class="nz-mlbl">난이도 상·중·하</p>
        <p class="nz-mval small">${dash.difficulty["상"]} · ${dash.difficulty["중"]} · ${dash.difficulty["하"]}</p></div>
      <div class="nz-mc"><p class="nz-mlbl">성취기준</p><p class="nz-mval small">${dash.standards.length}종</p></div>`;

    $("setRows").innerHTML = d.items.map((it, i) => `
      <tr draggable="true" data-qid="${it.question.id}" ondragstart="EP.dragStart(event,${it.question.id})"
          ondragover="event.preventDefault()" ondrop="EP.dropOn(event,${it.question.id})">
        <td class="cc grip">≡</td><td class="cc">${i + 1}</td>
        <td class="cc">${esc(it.question.qtype)}</td><td>${esc(it.question.ask)}</td>
        <td class="cc code">${esc(it.question.standard_code || "-")}</td>
        <td class="cc">${esc(it.question.difficulty)}</td>
        <td class="cc"><input class="nz-pt" type="number" step="0.5" min="0"
             value="${it.points ?? it.question.default_points}"
             onchange="EP.setItemPoints(${it.item_id}, this.value)" /></td>
        <td class="cc"><button class="nz-tb mini" onclick="EP.removeItem(${it.item_id})">×</button></td>
      </tr>`).join("") || '<tr class="nz-empty"><td colspan="8">담긴 문항이 없습니다. 아래에서 담으세요.</td></tr>';
    $("setFootL").textContent = `문항 ${dash.count}개`;
    $("setFootR").textContent = `배점 합 ${dash.total_points} / ${dash.target_points}점 · 성취기준 ${dash.standards.length}종`;

    let qs = await api("/api/questions");
    const allowS = EP.allowedCodes();
    if (allowS) qs = qs.filter((r) => !r.standard_code || allowS.has(r.standard_code));
    const inSet = new Set(d.items.map((it) => it.question.id));
    $("setPickRows").innerHTML = qs.filter((q) => !inSet.has(q.id)).map((q, i) => `
      <tr><td class="cc">${i + 1}</td><td>${esc(q.ask)}</td>
      <td class="cc code">${esc(q.standard_code || "-")}</td><td class="cc">${q.default_points}</td>
      <td class="cc"><button class="nz-tb mini blu" onclick="EP.addItem(${q.id})">담기</button></td></tr>`).join("")
      || '<tr class="nz-empty"><td colspan="5">담을 문항이 없습니다.</td></tr>';
  };

  /** 세트 만점 변경 — 검토의 배점 합 기준이 이 값이다 */
  EP.saveSetTotal = async function () {
    if (!S.curSetId) return;
    const v = parseFloat($("setTotal").value);
    if (!(v > 0)) return alert("만점은 0보다 커야 합니다.");
    await EP.patch(`/api/sets/${S.curSetId}`, { total_points: v });
    EP.loadSet();
  };

  /** 세트 안에서만 배점 변경 — 문항 자체의 기본 배점은 건드리지 않는다 */
  EP.setItemPoints = async function (itemId, value) {
    const p = parseFloat(value);
    await EP.patch(`/api/sets/${S.curSetId}/items/${itemId}`, { points: isNaN(p) ? null : p });
    EP.loadSet();
  };

  EP.addItem = async function (qid) { await post(`/api/sets/${S.curSetId}/items`, { question_id: qid }); EP.loadSet(); };
  EP.removeItem = async function (itemId) { await del(`/api/sets/${S.curSetId}/items/${itemId}`); EP.loadSet(); };

  EP.dragStart = function (e, qid) { S.dragQid = qid; };
  EP.dropOn = async function (e, targetQid) {
    e.preventDefault();
    if (S.dragQid === null || S.dragQid === targetQid) return;
    const order = [...document.querySelectorAll("#setRows tr[data-qid]")].map((tr) => +tr.dataset.qid);
    const from = order.indexOf(S.dragQid), to = order.indexOf(targetQid);
    order.splice(to, 0, order.splice(from, 1)[0]);
    await put(`/api/sets/${S.curSetId}/order`, { question_ids: order });
    S.dragQid = null; EP.loadSet();
  };

  EP.checkSet = async function () {
    const r = await api(`/api/sets/${S.curSetId}/check`);
    $("setCheckResult").innerHTML = r.ok && !r.warn_count
      ? '<div class="nz-issues ok">검토 통과 — 출력할 수 있습니다.</div>'
      : `<div class="nz-issues ${r.error_count ? "err" : "warn"}">
          <b>오류 ${r.error_count} · 경고 ${r.warn_count}</b>
          <ul>${r.issues.map((i) => `<li>[${i.level === "error" ? "오류" : "경고"}] ${esc(i.message)}</li>`).join("")}</ul></div>`;
  };

  EP.exportSet = async function () {
    const r = await api(`/api/sets/${S.curSetId}/export`);
    if (!r.count) return alert("담긴 문항이 없습니다.");
    $("exportBox").classList.remove("hidden");
    $("exportText").value = r.markdown;
    $("exportText").select();
    const ok = await EP.copy(r.markdown,
      `${r.count}개 문항을 클립보드에 복사했습니다.\n한글에 붙여넣고 Ctrl+T 하세요.`);
    if (!ok) alert("아래 상자의 내용을 복사해 한글에 붙여넣으세요.");
  };

  /* ---------- 제출 서류: 정답표 · 이원목적분류표 ---------- */
  // 문항을 다 만들고 나면 손으로 다시 만들던 두 장이다. 이미 있는 값으로 세운다.
  let reportCache = null;

  EP.openReports = async function (which) {
    if (!S.curSetId) return alert("세트를 먼저 고르세요.");
    reportCache = await api(`/api/sets/${S.curSetId}/reports`);
    if (!reportCache.answer_key.rows.length) return alert("담긴 문항이 없습니다.");
    renderReport(which || "answer");
  };

  function renderReport(which) {
    const d = reportCache;
    const t = which === "blueprint" ? d.blueprint : d.answer_key;
    const title = which === "blueprint" ? "이원목적분류표" : "정답표";
    const m = EP.modal("reportModal");
    const sum = d.blueprint.summary;
    const foot = which === "blueprint"
      ? `<div class="nz-repsum">
           문항 ${sum.count}개 · 배점 합 ${sum.total_points}점 ·
           난이도 상 ${sum.difficulty["상"]} / 중 ${sum.difficulty["중"]} / 하 ${sum.difficulty["하"]} ·
           행동영역 ${Object.entries(sum.behavior).filter(([, n]) => n).map(([b, n]) => b + " " + n).join(" / ")}
           ${sum.origin ? `<br><span class="nz-sub">출처(내부 관리용, 표에는 안 들어감) —
             ${Object.entries(sum.origin).filter(([, n]) => n).map(([o, n]) => o + " " + n).join(" / ")}</span>` : ""}
         </div>`
      : `<div class="nz-repsum">문항 ${d.answer_key.rows.length}개 · 배점 합 ${d.answer_key.total_points}점</div>`;

    m.innerHTML = `<div class="nz-modal-box" style="width:min(1100px,96vw)">
      <div class="nz-modal-head">
        <b>${esc(d.set.name)} — ${title}</b>
        <div class="nz-repttab">
          <button class="nz-tb mini ${which === "answer" ? "blu" : ""}" onclick="EP.showReport('answer')">정답표</button>
          <button class="nz-tb mini ${which === "blueprint" ? "blu" : ""}" onclick="EP.showReport('blueprint')">이원목적분류표</button>
        </div>
        <button class="nz-tb" style="margin-left:auto" onclick="EP.copyReport('${which}')">표 복사</button>
        <button class="nz-tb" onclick="EP.printReport()">인쇄</button>
        <button class="nz-tb" onclick="EP.closeModal('reportModal')">닫기</button>
      </div>
      <div class="nz-modal-body">
        <p class="nz-sub">“표 복사”는 탭으로 나뉜 텍스트를 넣습니다. 한글 표나 엑셀에 그대로 붙습니다.</p>
        <div id="reportPrint">
          <h3 class="nz-reph">${esc(d.set.name)} ${title}</h3>
          <table class="nz-t nz-rept">
            <thead><tr>${t.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
            <tbody>${t.rows.map((r) => `<tr>${r.map((c, i) =>
              `<td class="${i === 0 ? "cc" : ""}">${esc(c) || "&nbsp;"}</td>`).join("")}</tr>`).join("")}</tbody>
          </table>
          ${foot}
        </div>
      </div></div>`;
  }

  EP.showReport = function (which) { renderReport(which); };

  EP.copyReport = async function (which) {
    if (!reportCache) return;
    const tsv = which === "blueprint" ? reportCache.blueprint.tsv : reportCache.answer_key.tsv;
    const ok = await EP.copy(tsv, "표를 복사했습니다. 한글 표나 엑셀에 붙여넣으세요.");
    if (!ok) {
      const m = EP.modal("reportModal");
      m.querySelector(".nz-modal-body").insertAdjacentHTML("afterbegin",
        `<textarea class="nz-exp" rows="10">${esc(tsv)}</textarea>`);
      alert("자동 복사가 막혀 있습니다. 위 상자의 내용을 복사하세요.");
    }
  };

  /** 인쇄 — 표만 새 창에 담아 띄운다 (화면 UI 가 종이에 섞이지 않게) */
  EP.printReport = function () {
    const html = EP.$("reportPrint").innerHTML;
    const w = window.open("", "_blank", "width=1000,height=700");
    w.document.write(`<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8" />
      <title>ExamPool 제출 서류</title>
      <style>
        body{font-family:"IBM Plex Sans KR","맑은 고딕",sans-serif;padding:18px;color:#0d1117}
        h3{font-size:15px;margin:0 0 10px}
        table{border-collapse:collapse;width:100%;font-size:11.5px}
        th,td{border:1px solid #666;padding:4px 6px;vertical-align:top;line-height:1.45}
        th{background:#f0f2f5;font-weight:600}
        .cc{text-align:center}
        .nz-repsum{margin-top:8px;font-size:11px;color:#444}
        @page{size:A4 landscape;margin:12mm}
      </style></head><body>${html}</body></html>`);
    w.document.close();
    w.focus();
    w.print();
  };
})(window.EP);
