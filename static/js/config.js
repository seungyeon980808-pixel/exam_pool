/* ===== 환경설정 — 출제 범위 · 백업/복구 ===== */
(function (EP) {
  "use strict";
  const $ = EP.$, esc = EP.esc, api = EP.api, post = EP.post;
  const S = EP.S;

  EP.loadConfig = async function () {
    const subj = await api("/api/subject");
    $("cfgSubject").textContent = subj.subject + " · 성취기준 " + subj.standard_count + "개";
    $("cfgTree").innerHTML = S.standards.map((u) => {
      const unitOn = !S.scope.length || S.scope.includes(u.unit_no);
      const stds = u.standards.map((st) => {
        const on = !S.stdScope.length || S.stdScope.includes(st.code);
        return '<label class="nz-cfgstd"><input type="checkbox" data-std="' + esc(st.code) + '"' +
          (on && unitOn ? " checked" : "") + ' /><span class="code">' + esc(st.code) + "</span> " + esc(st.text) + "</label>";
      }).join("");
      return '<div class="nz-cfgunit"><label class="nz-cfghead"><input type="checkbox" data-unit="' + u.unit_no + '"' +
        (unitOn ? " checked" : "") + ' onchange="EP.cfgToggleUnit(' + u.unit_no + ', this.checked)" />' +
        "<b>" + u.unit_no + ". " + esc(u.name) + '</b><span class="nz-sub" style="margin:0">' +
        u.standards.length + "개</span></label><div class=\"nz-cfgstds\">" + stds + "</div></div>";
    }).join("");
    EP.loadBackups();
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
    const units = [...document.querySelectorAll("#cfgTree input[data-unit]:checked")].map((i) => +i.dataset.unit);
    const stds = [...document.querySelectorAll("#cfgTree input[data-std]:checked")].map((i) => i.dataset.std);
    const allStd = S.standards.flatMap((u) => u.standards).length;
    S.stdScope = (stds.length === allStd) ? [] : stds;
    localStorage.setItem("ep_std_scope", JSON.stringify(S.stdScope));
    EP.saveScope(units.length === S.standards.length ? [] : units);
    alert("범위를 저장했습니다.\n단원 " + (units.length || "전체") + " · 성취기준 " + (S.stdScope.length || "전체"));
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
