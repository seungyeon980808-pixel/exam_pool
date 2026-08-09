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
    S.cfgTrack = cur ? cur.track : (S.cfgTrack || TRACKS[0]);
    EP.renderCfg();
    EP.loadDocs();
    EP.loadPalettes();
    EP.loadBackups();
  };

  EP.renderCfg = function () {
    const tracks = TRACKS.filter((t) => S.subjects.some((s) => s.track === t));
    $("cfgTrack").innerHTML = tracks.map((t) =>
      '<option value="' + esc(t) + '"' + (t === S.cfgTrack ? " selected" : "") + ">" +
      esc(TRACK_LABEL[t] || t) + " (" + S.subjects.filter((s) => s.track === t).length + ")</option>").join("");

    const inTrack = S.subjects.filter((s) => s.track === S.cfgTrack);
    $("cfgSubject2").innerHTML = inTrack.map((s) =>
      '<option value="' + esc(s.name) + '"' + (s.name === S.subject ? " selected" : "") + ">" +
      esc(s.name) + "</option>").join("");

    const cur = S.subjects.find((s) => s.name === S.subject);
    $("cfgSubjInfo").textContent = cur
      ? `${cur.grade_band} · 단원 ${cur.unit_count}개 · 성취기준 ${cur.standard_count}개`
      : "";
    EP.renderCfgTree();
  };

  /** 구분을 바꾸면 그 구분의 첫 과목으로 따라 내려간다 (위계대로) */
  EP.cfgPickTrack = function (t) {
    S.cfgTrack = t;
    const inTrack = S.subjects.filter((s) => s.track === t);
    if (inTrack.length && !inTrack.some((s) => s.name === S.subject)) {
      EP.setSubject(inTrack[0].name);
    }
    EP.renderCfg();
  };

  EP.cfgPickSubject = function (name) {
    EP.setSubject(name);        // 과목을 바꾸면 다른 화면도 이 과목만 보게 된다
    EP.renderCfg();
  };

  EP.renderCfgTree = function () {
    const units = EP.subjectUnits();
    S.cfgOpen = S.cfgOpen || new Set();
    // 단원은 접어 두고 펼쳐 본다. 체크박스는 접혀 있어도 DOM 에 남겨야
    // 저장할 때 접힌 단원의 선택이 사라지지 않는다 (CSS 로만 감춘다).
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
      // 단원 유의사항 — 펼쳤을 때 성취기준 아래에 같이 보인다
      const notes = (u.consider || []).map((c) => "<li>" + esc(c) + "</li>").join("");
      const inq = (u.inquiry || []).map((c) => "<li>" + esc(c) + "</li>").join("");
      const noteBox = (notes || inq)
        ? '<div class="nz-cfgnote">' +
          (inq ? "<b>탐구 활동</b><ul>" + inq + "</ul>" : "") +
          (notes ? "<b>성취기준 적용 시 고려 사항</b><ul>" + notes + "</ul>" : "") + "</div>"
        : "";
      const picked = u.standards.filter((st) => !S.stdScope.length || S.stdScope.includes(st.code)).length;
      return '<div class="nz-cfgunit' + (open ? " open" : "") + '">' +
        '<div class="nz-cfghead" onclick="EP.cfgToggleOpen(' + u.unit_no + ')">' +
        '<input type="checkbox" data-unit="' + u.unit_no + '"' + (unitOn ? " checked" : "") +
        ' onclick="event.stopPropagation()"' +
        ' onchange="EP.cfgToggleUnit(' + u.unit_no + ', this.checked)" />' +
        '<span class="caret">' + (open ? "▾" : "▸") + "</span>" +
        "<b>" + esc(EP.unitLabel(u)) + '</b>' +
        '<span class="nz-sub" style="margin:0 0 0 auto">성취기준 ' +
        (unitOn ? picked + " / " : "") + u.standards.length + "개</span></div>" +
        '<div class="nz-cfgbody"><div class="nz-cfgstds">' + stds + "</div>" + noteBox + "</div></div>";
    }).join("") || '<p class="nz-sub">이 과목에 단원이 없습니다.</p>';
  };

  EP.cfgToggleOpen = function (unitNo) {
    S.cfgOpen = S.cfgOpen || new Set();
    if (S.cfgOpen.has(unitNo)) S.cfgOpen.delete(unitNo); else S.cfgOpen.add(unitNo);
    // 다시 그리면 접힌 단원의 체크 상태가 저장 전 값으로 되돌아가므로 클래스만 바꾼다
    const head = document.querySelector('#cfgTree input[data-unit="' + unitNo + '"]');
    if (!head) return;
    const box = head.closest(".nz-cfgunit");
    box.classList.toggle("open", S.cfgOpen.has(unitNo));
    box.querySelector(".caret").textContent = S.cfgOpen.has(unitNo) ? "▾" : "▸";
  };

  EP.cfgOpenAll = function (on) {
    S.cfgOpen = new Set(on ? EP.subjectUnits().map((u) => u.unit_no) : []);
    document.querySelectorAll("#cfgTree .nz-cfgunit").forEach((box) => {
      box.classList.toggle("open", on);
      box.querySelector(".caret").textContent = on ? "▾" : "▸";
    });
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

  /* ---------- 시험지 팔레트 ---------- */
  const PAL_STYLE = { school: "학교", suneung: "수능" };

  EP.loadPalettes = async function () {
    const rows = $("palRows");
    if (!rows) return;
    try {
      const data = await api("/api/integrations/hwppalette/palettes");
      const active = data.active || {};
      $("palStatus").innerHTML = "현재 적용 · 학교 <b>" + esc(active.school || "내장 기본") +
        "</b> · 수능 <b>" + esc(active.suneung || "내장 기본") + "</b>" +
        (active.school || active.suneung
          ? ' <button class="nz-tb mini" onclick="EP.resetPalette(\'school\')">학교 기본값</button>' +
            ' <button class="nz-tb mini" onclick="EP.resetPalette(\'suneung\')">수능 기본값</button>' : "");
      rows.innerHTML = (data.packages || []).map((p) => {
        const activeFor = p.active_for || [];
        const badge = activeFor.length
          ? activeFor.map((s) => `<b>${PAL_STYLE[s] || s}</b>`).join(", ") : "-";
        const count = (p.items || []).length;
        const note = p.note ? `<div class="nz-sub">${esc(p.note)}</div>` : "";
        return `<tr><td><b>${esc(p.name)}</b>${note}<div class="nz-sub">${esc(p.filename || "")} · ${esc(p.id)}</div></td>` +
          `<td>${count}개</td><td>${badge}</td><td>` +
          `<button class="nz-tb mini" onclick="EP.activatePalette('${esc(p.id)}','school')">학교 적용</button> ` +
          `<button class="nz-tb mini" onclick="EP.activatePalette('${esc(p.id)}','suneung')">수능 적용</button>` +
          `</td></tr>`;
      }).join("") || '<tr><td colspan="4" class="nz-sub">등록된 팔레트가 없습니다. 내장 기본 양식을 사용합니다.</td></tr>';
    } catch (e) {
      rows.innerHTML = `<tr><td colspan="4" class="r">팔레트 목록을 읽지 못했습니다: ${esc(e.message)}</td></tr>`;
    }
  };

  EP.importPalette = async function (input) {
    const file = input.files && input.files[0];
    if (!file) return;
    const style = $("palTarget").value;
    try {
      const url = "/api/integrations/hwppalette/palettes?filename=" +
        encodeURIComponent(file.name) + "&target_style=" + encodeURIComponent(style);
      const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        throw new Error(payload.detail || `HTTP ${res.status}`);
      }
      const out = await res.json();
      const contract = out.slot_contract || {};
      alert(`${out.name} 팔레트를 ${PAL_STYLE[style]} 양식으로 등록했습니다.` +
        (contract.ok ? "\n슬롯 계약도 정상입니다." : "\n일부 슬롯 계약을 확인해 주세요."));
      await EP.loadPalettes();
    } catch (e) {
      alert("팔레트 등록 실패: " + e.message);
    } finally {
      input.value = "";
    }
  };

  EP.activatePalette = async function (id, style) {
    try {
      await post(`/api/integrations/hwppalette/palettes/${id}/activate/${style}`, {});
      await EP.loadPalettes();
    } catch (e) { alert("팔레트 적용 실패: " + e.message); }
  };

  EP.resetPalette = async function (style) {
    if (!confirm(`${PAL_STYLE[style]} 양식을 내장 기본값으로 되돌릴까요?`)) return;
    try {
      await EP.del(`/api/integrations/hwppalette/palettes/active/${style}`);
      await EP.loadPalettes();
    } catch (e) { alert("기본값 복원 실패: " + e.message); }
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
