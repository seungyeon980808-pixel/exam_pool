/* ===== 환경설정 — 출제 범위 · 백업/복구 ===== */
(function (EP) {
  "use strict";
  const $ = EP.$, esc = EP.esc, api = EP.api, post = EP.post;
  const S = EP.S;

  // 전 과목 성취기준이 371개다. 한 번에 다 뿌리면 못 쓴다 —
  // 교육과정 구분 → 과목 → 단원 순으로 좁혀 지금 가르치는 과목만 보이게 한다.
  const TRACKS = ["공통", "공통과목", "일반선택", "진로선택", "융합선택"];
  const TRACK_LABEL = { 공통: "공통 교육과정", 공통과목: "공통 과목",
                        일반선택: "일반 선택", 진로선택: "진로 선택", 융합선택: "융합 선택" };

  EP.loadConfig = async function () {
    const subj = await api("/api/subject");
    $("cfgSubject").textContent =
      subj.subject + " · 과목 " + subj.subject_count + "개 · 성취기준 " + subj.standard_count + "개";
    const cur = S.subjects.find((s) => s.name === S.subject);
    if (!S.cfgTrack) S.cfgTrack = cur ? cur.track : TRACKS[0];
    EP.renderCfg();
    EP.loadBackups();
  };

  EP.renderCfg = function () {
    const tracks = TRACKS.filter((t) => S.subjects.some((s) => s.track === t));
    $("cfgTrack").innerHTML = tracks.map((t) =>
      '<button class="nz-pick' + (t === S.cfgTrack ? " on" : "") + '" onclick="EP.cfgPickTrack(\'' + t + '\')">' +
      esc(TRACK_LABEL[t] || t) + '<span class="n">' +
      S.subjects.filter((s) => s.track === t).length + "</span></button>").join("");

    $("cfgSubjects").innerHTML = S.subjects.filter((s) => s.track === S.cfgTrack).map((s) =>
      '<button class="nz-pick' + (s.name === S.subject ? " on" : "") + '" onclick="EP.cfgPickSubject(\'' +
      esc(s.name) + '\')">' + esc(s.name) + '<span class="n">' + s.standard_count + "</span></button>").join("");

    EP.renderCfgTree();
  };

  EP.cfgPickTrack = function (t) { S.cfgTrack = t; EP.renderCfg(); };

  EP.cfgPickSubject = function (name) {
    EP.setSubject(name);        // 과목을 바꾸면 다른 화면도 이 과목만 보게 된다
    EP.renderCfg();
  };

  EP.renderCfgTree = function () {
    const units = EP.subjectUnits();
    S.cfgOpen = S.cfgOpen || new Set();
    $("cfgTree").innerHTML = units.map((u) => {
      const unitOn = !S.scope.length || S.scope.includes(u.unit_no);
      const open = S.cfgOpen.has(u.unit_no);
      const stds = u.standards.map((st) => {
        const on = !S.stdScope.length || S.stdScope.includes(st.code);
        return '<label class="nz-cfgstd"><input type="checkbox" data-std="' + esc(st.code) + '"' +
          (on && unitOn ? " checked" : "") + ' /><span class="code">' + esc(st.code) + "</span> " +
          esc(st.text) +
          (st.explain ? '<button class="nz-why" title="성취기준 해설" onclick="EP.cfgWhy(event, \'' +
            esc(st.code) + '\')">해설</button>' : "") + "</label>";
      }).join("");
      // 단원 유의사항 — 출제 전에 확인해야 할 것들. 접어 두고 필요할 때만 편다.
      const notes = (u.consider || []).map((c) => "<li>" + esc(c) + "</li>").join("");
      const inq = (u.inquiry || []).map((c) => "<li>" + esc(c) + "</li>").join("");
      const noteBox = open && (notes || inq)
        ? '<div class="nz-cfgnote">' +
          (inq ? "<b>탐구 활동</b><ul>" + inq + "</ul>" : "") +
          (notes ? "<b>성취기준 적용 시 고려 사항</b><ul>" + notes + "</ul>" : "") + "</div>"
        : "";
      return '<div class="nz-cfgunit"><label class="nz-cfghead"><input type="checkbox" data-unit="' +
        u.unit_no + '"' + (unitOn ? " checked" : "") +
        ' onchange="EP.cfgToggleUnit(' + u.unit_no + ', this.checked)" />' +
        "<b>" + esc(EP.unitLabel(u)) + '</b><span class="nz-sub" style="margin:0">' +
        u.standards.length + "개</span>" +
        '<button class="nz-tb mini" style="margin-left:auto" onclick="EP.cfgToggleNote(event,' +
        u.unit_no + ')">' + (open ? "유의사항 접기" : "유의사항") + "</button></label>" +
        '<div class="nz-cfgstds">' + stds + "</div>" + noteBox + "</div>";
    }).join("") || '<p class="nz-sub">이 과목에 단원이 없습니다.</p>';
  };

  EP.cfgToggleNote = function (ev, unitNo) {
    ev.preventDefault(); ev.stopPropagation();
    S.cfgOpen = S.cfgOpen || new Set();
    if (S.cfgOpen.has(unitNo)) S.cfgOpen.delete(unitNo); else S.cfgOpen.add(unitNo);
    EP.renderCfgTree();
  };

  /** 성취기준 해설 — "여기까지만 다룬다"가 적혀 있어 출제 범위 판단에 바로 쓴다 */
  EP.cfgWhy = function (ev, code) {
    ev.preventDefault(); ev.stopPropagation();
    const st = S.standards.flatMap((u) => u.standards).find((x) => x.code === code);
    if (!st) return;
    const m = EP.modal("whyModal");
    m.innerHTML = '<div class="nz-modal-box" style="width:min(620px,92vw)">' +
      '<div class="nz-modal-head"><b>성취기준 해설</b>' +
      '<button class="nz-tb" style="margin-left:auto" onclick="EP.closeModal(\'whyModal\')">닫기</button></div>' +
      '<div class="nz-modal-body"><p class="nz-sub" style="margin:0 0 8px"><b>' + esc(st.code) + "</b> " +
      esc(st.text) + "</p><p>" + esc(st.explain) + "</p></div></div>";
  };

  EP.cfgToggleUnit = function (unitNo, on) {
    const head = document.querySelector('#cfgTree input[data-unit="' + unitNo + '"]');
    if (!head) return;
    head.closest(".nz-cfgunit").querySelectorAll("input[data-std]").forEach((i) => { i.checked = on; });
  };
  EP.cfgAll = function (on) {
    document.querySelectorAll("#cfgTree input").forEach((i) => { i.checked = on; });
  };
  EP.cfgSave = function () {
    const all = EP.subjectUnits();          // 저장은 지금 고른 과목에만 적용된다
    const units = [...document.querySelectorAll("#cfgTree input[data-unit]:checked")].map((i) => +i.dataset.unit);
    const stds = [...document.querySelectorAll("#cfgTree input[data-std]:checked")].map((i) => i.dataset.std);
    const allStd = all.flatMap((u) => u.standards).length;
    S.stdScope = (stds.length === allStd) ? [] : stds;
    localStorage.setItem("ep_std_scope", JSON.stringify(S.stdScope));
    localStorage.setItem("ep_std_scope:" + S.subject, JSON.stringify(S.stdScope));
    EP.saveScope(units.length === all.length ? [] : units);
    alert(S.subject + " 범위를 저장했습니다.\n단원 " + (units.length || "전체") +
      " · 성취기준 " + (S.stdScope.length || "전체"));
  };

  /* ---------- 백업 · 복구 ---------- */
  // 한 학기 출제 데이터가 파일 하나에 모여 있다. 슬롯 3개를 돌려 쓰고,
  // 서버가 뜰 때 하루 한 번 자동으로 남는다.
  const KB = (n) => (n / 1024 >= 1024
    ? (n / 1024 / 1024).toFixed(1) + " MB"
    : Math.max(1, Math.round(n / 1024)) + " KB");

  EP.loadBackups = async function () {
    const box = $("bkList");
    if (!box) return;
    const d = await api("/api/backups");
    $("bkLast").innerHTML = d.last
      ? `마지막 백업 <b>${esc(d.last.at.replace("T", " "))}</b> <span class="nz-sub" style="margin:0">${esc(d.last.reason)}</span>`
      : '<span class="r">아직 백업이 없습니다.</span>';
    $("bkDir").textContent = d.dir;
    box.innerHTML = d.slots.length ? d.slots.map((s) => {
      const c = s.counts || {};
      return `<div class="nz-bkrow">
        <span class="slot">bak${s.slot}</span>
        <span class="at">${esc(s.at.replace("T", " "))}</span>
        <span class="cnt">명제 ${c["명제"] ?? "-"} · 문항 ${c["문항"] ?? "-"} · 세트 ${c["세트"] ?? "-"} · 근거 ${c["근거"] ?? "-"}</span>
        <span class="size">${KB(s.size)}</span>
        <button class="nz-tb mini" onclick="EP.restoreBackup(${s.slot}, '${esc(s.at.replace("T", " "))}')">이 시점으로 되돌리기</button>
      </div>`;
    }).join("") : '<p class="nz-sub">백업이 없습니다. “지금 백업”을 눌러 하나 만들어 두세요.</p>';
  };

  EP.makeBackup = async function () {
    try {
      const r = await post("/api/backups", {});
      alert(`백업했습니다 (${KB(r.size)}).`);
      EP.loadBackups();
    } catch (e) { alert("백업 실패: " + e.message); }
  };

  EP.restoreBackup = async function (slot, at) {
    if (!confirm(`bak${slot} (${at}) 시점으로 되돌립니다.\n\n` +
      "지금 데이터는 되돌리기 직전 상태로 따로 백업되므로 다시 돌아올 수 있습니다.\n계속할까요?")) return;
    try {
      await post(`/api/backups/${slot}/restore`, {});
      alert("되돌렸습니다. 화면을 새로 불러옵니다.");
      location.reload();
    } catch (e) { alert("복구 실패: " + e.message); }
  };

  EP.openBackupFolder = async function () {
    try { await post("/api/backups/open-folder", {}); }
    catch (e) { alert("폴더를 열 수 없습니다: " + e.message); }
  };
})(window.EP);
