/* ===== ExamPool — 공통 뼈대 =====
 * 화면 코드는 탭 단위 파일로 나뉘어 있고, 모두 전역 EP 하나에 얹힌다.
 * (index.html 의 onclick="EP.xxx()" 가 그대로 동작해야 하므로 전역 이름은 EP 로 고정)
 *
 *   core.js      ← 이 파일. 통신·공용 도구·성취기준·출제 범위·탭
 *   bank.js      명제 Pool
 *   question.js  문항 설계
 *   qbank.js     문항 Pool
 *   set.js       세트 관리 · 제출 서류
 *   doc.js       근거 문서 등록·원문 미리보기 (환경설정 안)
 *   lesson.js    수업 기록
 *   config.js    환경설정 · 백업
 *
 * 파일 사이에 공유하는 값은 전부 EP.S 에 둔다. 각 파일이 자기 변수를 감추면
 * 다른 파일에서 못 읽어 결국 전역이 늘어난다 — 한 곳에 모아 어디서 바뀌는지 보이게 한다.
 */
window.EP = window.EP || {};

(function (EP) {
  "use strict";

  /* ---------- 공유 상태 ---------- */
  EP.S = {
    standards: [],        // 성취기준 트리
    curProps: [],         // 명제 Pool: 현재 조회 결과
    bogi: [],             // 문항 설계: 보기
    choices: [],          // 문항 설계: 선지
    editingQid: null,
    curSetId: null,
    openUnits: new Set(),
    subjects: [],         // 교육과정 과목 목록 (중학교 과학 + 고등학교 선택 과목)
    subject: localStorage.getItem("ep_subject") || "과학",   // 지금 가르치는 과목 하나
    scope: JSON.parse(localStorage.getItem("ep_scope") || "[]"),
    stdScope: JSON.parse(localStorage.getItem("ep_std_scope") || "[]"),
    curStdCode: "",
    curPropId: null,      // 명제 Pool: 상세를 연 명제 — 근거 검색 패널이 여기에 붙인다
    checkState: {},
    refs: [],
    curRefId: null,
    evViewer: { docId: null, page: 1, lastPage: 1, q: "" },
    evSrc: "",
    searchTags: [],
    curFolder: "",
    onlyDocId: 0,
    viewer: { docId: null, page: 1, lastPage: 1 },
    dragQid: null,
    docTypes: {},
    authoringSessionId: null,
  };

  EP.LABELS = ["ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ"];
  EP.HL_COLORS = ["rgba(255,217,77,.45)", "rgba(140,217,255,.45)", "rgba(166,255,166,.45)",
                  "rgba(255,184,219,.45)", "rgba(209,194,255,.45)"];

  /* ---------- 통신 ---------- */
  EP.api = async function (url, opts) {
    let res;
    try {
      res = await fetch(url, opts);
    } catch (e) {
      throw new Error("서버 연결 실패 — ExamPool이 실행 중인지 확인하세요.");
    }
    if (!res.ok) throw new Error((await res.text()).slice(0, 300));
    return res.json();
  };
  EP.post = (url, body) => EP.api(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  EP.put = (url, body) => EP.api(url, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  EP.patch = (url, body) => EP.api(url, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  EP.del = (url) => EP.api(url, { method: "DELETE" });

  /* ---------- 공용 도구 ---------- */
  EP.esc = (s) => String(s ?? "").replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  EP.escAttr = (s) => String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  EP.$ = (id) => document.getElementById(id);

  EP.debounce = function (fn, ms) {
    let t;
    return function () { clearTimeout(t); t = setTimeout(fn, ms); };
  };

  EP.fillSelect = function (sel, opts, firstLabel) {
    if (!sel) return;
    sel.innerHTML = firstLabel ? `<option value="">${firstLabel}</option>` : "";
    opts.forEach((o) => sel.appendChild(new Option(o.t, o.v)));
  };

  /** 어느 모달이든 같은 방식으로 띄운다 (바깥을 누르면 닫힘) */
  EP.modal = function (id) {
    let m = EP.$(id);
    if (!m) {
      m = document.createElement("div");
      m.id = id;
      m.className = "nz-modal";
      m.onclick = (e) => { if (e.target === m) m.classList.add("hidden"); };
      document.body.appendChild(m);
    }
    m.classList.remove("hidden");
    return m;
  };
  EP.closeModal = function (id) {
    const m = EP.$(id);
    if (m) m.classList.add("hidden");
  };

  /** 클립보드 복사 — 실패하면 상자를 열어 직접 복사하게 한다 */
  EP.copy = async function (text, okMsg) {
    try {
      await navigator.clipboard.writeText(text);
      if (okMsg) alert(okMsg);
      return true;
    } catch (e) {
      return false;
    }
  };

  // snippet 의 [키워드] 표시를 <mark> 로
  EP.mark = (s) => EP.esc(s).replace(/\[([^\]]+)\]/g, "<mark>$1</mark>");

  // 띄어쓰기로 나눈 각 단어를 별도 키워드로 본다 (모두 포함하는 페이지만 = AND)
  EP.splitTerms = function (q) {
    return (q || "").replace(/#/g, " ").split(/\s+/).map((t) => t.trim()).filter(Boolean);
  };

  /** 스니펫의 [단어] 를 단어별 색으로 칠한다 */
  EP.markTerms = function (snippet, terms) {
    return EP.esc(snippet).replace(/\[([^\]]*)\]/g, (m, w) => {
      let idx = terms.findIndex((t) => w.toLowerCase().includes(t.toLowerCase()));
      if (idx < 0) idx = 0;
      return '<mark style="background:' + EP.HL_COLORS[idx % EP.HL_COLORS.length] + '">' + w + "</mark>";
    });
  };

  /** 검색어 단어별 색상 칩 — 어느 색이 어느 단어인지 보이게 */
  EP.renderTermBar = function (terms, hitCounts) {
    const bar = EP.$("termBar");
    if (!bar) return;
    bar.innerHTML = terms.map((t, i) =>
      '<span class="nz-termchip"><span class="sw" style="background:' + EP.HL_COLORS[i % EP.HL_COLORS.length] +
      '"></span>' + EP.esc(t) + (hitCounts && hitCounts[t] != null ? '<span class="n">' + hitCounts[t] + "</span>" : "") +
      "</span>").join("");
  };

  /** PDF 페이지 위에 형광 좌표를 겹쳐 그린다 (검색 결과·미리보기 공통) */
  EP.paintHighlights = function (wrap, img, hl, scrollBox) {
    const paint = () => {
      wrap.querySelectorAll(".nz-hl").forEach((n) => n.remove());
      const scale = img.clientWidth / hl.page_w;
      hl.boxes.forEach((b) => {
        const d = document.createElement("div");
        d.className = "nz-hl";
        d.style.cssText = `left:${b.x * scale}px;top:${b.y * scale}px;` +
          `width:${b.w * scale}px;height:${b.h * scale}px;background:${EP.HL_COLORS[b.color_idx]}`;
        d.title = b.term || "";
        wrap.appendChild(d);
      });
      if (hl.boxes.length && scrollBox) {
        scrollBox.scrollTop = Math.max(0, hl.boxes[0].y * scale - 110);
      }
    };
    if (img.complete) paint(); else img.onload = paint;
  };

  const _prefetched = new Set();
  EP.prefetch = function (docId, pageNo) {
    const key = `${docId}/${pageNo}`;
    if (_prefetched.has(key)) return;
    _prefetched.add(key);
    const im = new Image();
    im.src = `/api/documents/${docId}/page/${pageNo}/image`;
  };

  /* ---------- 성취기준 ---------- */
  EP.loadStandards = async function () {
    EP.S.standards = await EP.api("/api/standards");
    EP.S.subjects = await EP.api("/api/subjects");
    // 저장된 과목이 없어졌으면(다른 교육과정 seed 로 교체) 첫 과목으로 되돌린다
    if (EP.S.subjects.length && !EP.S.subjects.some((s) => s.name === EP.S.subject)) {
      EP.setSubject(EP.S.subjects[0].name, true);
    }
    EP.renderTree();
    EP.renderScopeBar();
    EP.refillStdSelects();
    const subj = await EP.api("/api/subject");
    EP.$("subjectLabel").textContent =
      `${EP.S.subject} · 성취기준 ${EP.curSubjectStdCount()}개 / 전체 ${subj.standard_count}개`;
  };

  /** 지금 과목의 성취기준 개수 */
  EP.curSubjectStdCount = function () {
    return EP.S.standards.filter((u) => u.subject === EP.S.subject)
      .reduce((n, u) => n + u.standards.length, 0);
  };

  /** 화면에 보이는 단원 번호는 과목 안에서의 번호((1),(2)…). unit_no 는 전역 고유값이라 감춘다. */
  EP.unitLabel = function (u) { return `${u.local_no || u.unit_no}. ${u.name}`; };

  /** 과목 전환 — 출제 범위는 과목마다 따로 기억한다 */
  EP.setSubject = function (name, quiet) {
    EP.S.subject = name;
    localStorage.setItem("ep_subject", name);
    EP.S.scope = JSON.parse(localStorage.getItem("ep_scope:" + name) || "[]");
    EP.S.stdScope = JSON.parse(localStorage.getItem("ep_std_scope:" + name) || "[]");
    localStorage.setItem("ep_scope", JSON.stringify(EP.S.scope));
    localStorage.setItem("ep_std_scope", JSON.stringify(EP.S.stdScope));
    EP.S.openUnits = new Set();
    if (quiet) return;
    EP.$("subjectLabel").textContent =
      `${name} · 성취기준 ${EP.curSubjectStdCount()}개`;
    EP.renderTree(); EP.renderScopeBar(); EP.refillStdSelects();
    if (EP.loadProps) EP.loadProps();
  };

  /** 성취기준 선택 — 목록 필터 + 전문 배너 */
  EP.pickStandard = function (s, el) {
    document.querySelectorAll(".nz-mi.sub.on").forEach((n) => n.classList.remove("on"));
    if (el) el.classList.add("on");
    EP.$("q-std").value = s.code;
    const b = EP.$("stdBanner");
    b.classList.remove("hidden");
    b.innerHTML = `<b>${EP.esc(s.code)}</b>${EP.esc(s.text)}`;
    EP.loadProps();
  };

  /** 좌측 트리 검색 — 단원명·성취기준 코드/내용 모두 */
  EP.filterTree = function () {
    const q = EP.$("treeFilter").value.trim().toLowerCase();
    const tree = EP.$("tree");
    tree.innerHTML = "";
    if (!q) { EP.renderTree(); return; }
    EP.scopedStandards().forEach((u) => {
      const unitHit = EP.unitLabel(u).toLowerCase().includes(q);
      const hits = u.standards.filter((s) =>
        s.code.toLowerCase().includes(q) || s.text.toLowerCase().includes(q));
      if (!unitHit && !hits.length) return;
      const uEl = document.createElement("div");
      uEl.className = "nz-mi";
      uEl.textContent = EP.unitLabel(u);
      tree.appendChild(uEl);
      (hits.length ? hits : u.standards).forEach((s) => {
        const e = document.createElement("div");
        e.className = "nz-mi sub";
        e.title = `${s.code} ${s.text}`;
        e.innerHTML = `<span class="code">${EP.esc(s.code)}</span><span class="txt">${EP.esc(s.text)}</span>`;
        e.onclick = () => EP.pickStandard(s, e);
        tree.appendChild(e);
      });
    });
  };

  /* ---------- 출제 범위(단원 선택) ---------- */
  // 시험 한 번에 23개 단원을 다 쓰지 않는다. 필요한 단원만 골라 그 안에서 작업한다.
  EP.inScope = function (unitNo) { return !EP.S.scope.length || EP.S.scope.includes(unitNo); };

  /** 지금 과목의 단원만 (전 과목 371개를 한 화면에 뿌리지 않는다) */
  EP.subjectUnits = function () {
    const cur = EP.S.subject;
    return EP.S.standards.filter((u) => !u.subject || u.subject === cur);
  };

  EP.scopedStandards = function () {
    const ss = EP.S.stdScope;
    return EP.subjectUnits().filter((u) => EP.inScope(u.unit_no)).map((u) => ({
      unit_no: u.unit_no, local_no: u.local_no, name: u.name, subject: u.subject,
      inquiry: u.inquiry || [], consider: u.consider || [],
      standards: ss.length ? u.standards.filter((s) => ss.includes(s.code)) : u.standards,
    })).filter((u) => u.standards.length);
  };

  EP.saveScope = function (list) {
    EP.S.scope = list;
    localStorage.setItem("ep_scope", JSON.stringify(EP.S.scope));
    localStorage.setItem("ep_scope:" + EP.S.subject, JSON.stringify(EP.S.scope));
    EP.renderTree(); EP.renderScopeBar(); EP.refillStdSelects();
    EP.loadProps();
  };

  EP.renderScopeBar = function () {
    const bars = document.querySelectorAll(".nz-scopebar");
    const label = EP.S.scope.length
      ? `<b>${EP.esc(EP.S.subject)}</b> 출제 범위: <b>${EP.S.scope.map((n) => EP.S.standards.find((u) => u.unit_no === n)?.name || n).join(" · ")}</b>`
      : `<b>${EP.esc(EP.S.subject)}</b> 출제 범위: <b>전체 단원</b> (범위를 좁히면 화면이 단순해집니다)`;
    bars.forEach((b) => {
      b.innerHTML = `${label}<button class="nz-tb mini" style="margin-left:auto" onclick="EP.openScope()">범위 설정</button>`;
    });
  };

  EP.openScope = function () {
    const m = EP.modal("scopeModal");
    m.innerHTML = `<div class="nz-modal-box" style="width:min(760px,92vw)">
      <div class="nz-modal-head"><b>이번 시험 출제 범위</b>
        <span class="nz-sub" style="margin:0 0 0 10px">다룰 단원만 고르세요. 고른 단원만 화면에 보입니다.</span>
        <button class="nz-tb" style="margin-left:auto" onclick="EP.closeModal('scopeModal')">닫기</button>
      </div>
      <div class="nz-modal-body">
        <div class="nz-scope" id="scopeList">
          ${EP.subjectUnits().map((u) => `
            <label class="${EP.S.scope.includes(u.unit_no) ? "on" : ""}" data-u="${u.unit_no}">
              <input type="checkbox" ${EP.S.scope.includes(u.unit_no) ? "checked" : ""}
                     onchange="this.parentElement.classList.toggle('on', this.checked)" />
              ${EP.esc(EP.unitLabel(u))}
            </label>`).join("")}
        </div>
        <div class="nz-fbtn" style="margin-top:12px">
          <button class="nz-tb" onclick="EP.applyScope([])">전체 단원 보기</button>
          <button class="nz-tb blu" onclick="EP.applyScope()">이 범위로 시작</button>
        </div>
      </div></div>`;
  };

  EP.applyScope = function (force) {
    let list = force;
    if (list === undefined) {
      list = [...document.querySelectorAll("#scopeList input:checked")]
        .map((i) => +i.parentElement.dataset.u);
    }
    EP.saveScope(list);
    EP.closeModal("scopeModal");
  };

  EP.showStdFull = function () {};   // 전문은 select 안에 그대로 들어간다 (별도 공간 없음)

  /** 문항 Pool 단원·성취기준 필터 채우기 */
  EP.refillBankFilters = function () {
    const uSel = EP.$("qbUnit"), sSel = EP.$("qbStd");
    if (!uSel) return;
    const cur = uSel.value;
    uSel.innerHTML = '<option value="">전체 단원</option>';
    EP.scopedStandards().forEach((u) => uSel.appendChild(new Option(EP.unitLabel(u), u.unit_no)));
    uSel.value = cur;
    const unit = +uSel.value || 0;
    const list = unit ? EP.scopedStandards().filter((u) => u.unit_no === unit) : EP.scopedStandards();
    sSel.innerHTML = '<option value="">전체 성취기준</option>';
    list.forEach((u) => u.standards.forEach((st) =>
      sSel.appendChild(new Option(`${st.code} ${st.text.slice(0, 14)}…`, st.code))));
  };

  EP.refillStdSelects = function () {
    const opts = EP.scopedStandards().flatMap((u) => u.standards.map((s) =>
      ({ v: s.code, t: `${s.code} ${s.text}` })));   // 자르지 않고 전문
    EP.fillSelect(EP.$("q-std"), opts, "전체");
    EP.fillSelect(EP.$("f-std"), opts);
    if (EP.$("stdList") && !EP.$("stdList").classList.contains("hidden")) EP.renderStdList();
    EP.refillUnitSelect();
    EP.refillBankFilters();
  };

  /** 명제 Pool: 단원 select — 단원을 먼저 고르고 성취기준을 좁힌다 */
  EP.refillUnitSelect = function () {
    const sel = EP.$("q-unit");
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '<option value="">전체 단원</option>';
    EP.scopedStandards().forEach((u) => sel.appendChild(new Option(EP.unitLabel(u), u.unit_no)));
    sel.value = cur;
  };

  EP.onBankUnitChange = function () {
    const unit = +EP.$("q-unit").value || 0;
    const list = unit ? EP.scopedStandards().filter((u) => u.unit_no === unit) : EP.scopedStandards();
    const opts = list.flatMap((u) => u.standards.map((s) => ({ v: s.code, t: `${s.code} ${s.text}` })));
    EP.fillSelect(EP.$("q-std"), opts, "전체");
    EP.loadProps();
  };

  /** 현재 출제 범위 안의 성취기준 코드. 범위가 없으면 null(전체) */
  EP.allowedCodes = function () {
    if (!EP.S.scope.length && !EP.S.stdScope.length) return null;
    return new Set(EP.scopedStandards().flatMap((u) => u.standards.map((s) => s.code)));
  };

  /* ---------- 트리 (접기/펴기) ---------- */
  EP.renderTree = function () {
    const tree = EP.$("tree");
    tree.innerHTML = "";
    EP.scopedStandards().forEach((u) => {
      const el = document.createElement("div");
      el.className = "nz-mi";
      const open = EP.S.openUnits.has(u.unit_no);
      el.innerHTML = `<span class="caret">${open ? "▾" : "▸"}</span>` +
        `<span class="uname">${EP.esc(EP.unitLabel(u))}</span>`;
      el.onclick = () => {
        if (EP.S.openUnits.has(u.unit_no)) EP.S.openUnits.delete(u.unit_no);
        else EP.S.openUnits.add(u.unit_no);
        EP.renderTree();
      };
      tree.appendChild(el);
      if (open) {
        u.standards.forEach((s) => {
          const e = document.createElement("div");
          e.className = "nz-mi sub";
          e.title = `${s.code} ${s.text}`;
          e.innerHTML = `<span class="code">${EP.esc(s.code)}</span><span class="txt">${EP.esc(s.text)}</span>`;
          e.onclick = (ev) => { ev.stopPropagation(); EP.pickStandard(s, e); };
          tree.appendChild(e);
        });
      }
    });
    if (!EP.scopedStandards().length) {
      tree.innerHTML = '<p class="nz-sub" style="padding:10px">범위에 단원이 없습니다.</p>';
    }
  };

  EP.foldMenu = function () { document.querySelector(".nz-menu").classList.toggle("folded"); };

  /* ---------- 성취기준 전체보기 ---------- */
  EP.openStdTable = function () { EP.$("stdModal").classList.remove("hidden"); EP.renderStdTable(); };
  EP.closeStdTable = function () { EP.$("stdModal").classList.add("hidden"); };

  EP.renderStdTable = async function () {
    const q = (EP.$("stdModalFilter").value || "").trim().toLowerCase();
    const props = await EP.api("/api/propositions");
    const countBy = {};
    props.forEach((p) => { countBy[p.standard_code] = (countBy[p.standard_code] || 0) + 1; });
    const rows = [];
    EP.subjectUnits().forEach((u) => u.standards.forEach((s) => {
      if (q && !(s.code.toLowerCase().includes(q) || s.text.toLowerCase().includes(q)
        || u.name.toLowerCase().includes(q))) return;
      rows.push(`<tr>
        <td class="cc code">${EP.esc(s.code)}</td>
        <td class="cc">${EP.esc(u.name)}</td>
        <td>${EP.esc(s.text)}</td>
        <td class="cc ${countBy[s.code] ? "g" : ""}">${countBy[s.code] || "-"}</td></tr>`);
    }));
    EP.$("stdTableRows").innerHTML = rows.join("") ||
      '<tr class="nz-empty"><td colspan="4">일치하는 성취기준이 없습니다.</td></tr>';
  };

  /* ---------- 성취기준 선택 (줄바꿈 허용 커스텀 UI) ---------- */
  EP.stdValue = function () { return EP.S.curStdCode; };

  EP.toggleStdList = function () {
    const box = EP.$("stdList");
    if (!box) return;
    if (box.classList.contains("hidden")) EP.renderStdList();
    box.classList.toggle("hidden");
  };

  EP.renderStdList = function () {
    const box = EP.$("stdList");
    box.innerHTML = EP.scopedStandards().map((u) =>
      '<div class="nz-stdgrp">' + EP.esc(EP.unitLabel(u)) + "</div>" +
      u.standards.map((st) =>
        '<div class="nz-stdopt' + (st.code === EP.S.curStdCode ? " on" : "") + '"' +
        " onclick=\"EP.pickStd('" + st.code + "')\"><span class=\"code\">" + EP.esc(st.code) + "</span>" +
        EP.esc(st.text) + "</div>").join("")).join("");
  };

  EP.pickStd = function (code) {
    EP.S.curStdCode = code;
    const st = EP.S.standards.flatMap((u) => u.standards).find((x) => x.code === code);
    EP.$("stdPickText").className = "";
    EP.$("stdPickText").innerHTML = st
      ? '<span class="code">' + EP.esc(st.code) + "</span>" + EP.esc(st.text)
      : "성취기준을 고르세요";
    EP.$("stdList").classList.add("hidden");
    if (EP.updateQuestionSettingsSummary) EP.updateQuestionSettingsSummary();
  };

  EP.setStdValue = function (code) {
    EP.S.curStdCode = code || "";
    if (!code) {
      EP.$("stdPickText").className = "ph";
      EP.$("stdPickText").textContent = "성취기준을 고르세요";
      if (EP.updateQuestionSettingsSummary) EP.updateQuestionSettingsSummary();
    } else EP.pickStd(code);
  };

  /* ---------- 탭 ---------- */
  const TABS = ["bank", "question", "qbank", "set", "lesson", "config"];

  /** 근거 검색 패널은 하나뿐이다. 명제 Pool·문항 설계 중 지금 보는 탭으로 옮겨 붙인다.
   *  (같은 화면을 두 벌 만들면 id 가 겹치고 검색 상태도 갈라진다) */
  EP.moveSide = function (tab) {
    const side = EP.$("qside");
    if (!side) return;
    const host = EP.$("tab-" + tab) && EP.$("tab-" + tab).querySelector(".nz-qlayout");
    if (host && side.parentElement !== host) host.appendChild(side);
    if (EP.applyWidths) EP.applyWidths();
  };

  /* ---------- 폭 조절 (드래그) ---------- */
  // 손잡이 두 개: 패널 왼쪽 모서리(근거 검색 전체 폭) / 결과 목록과 원문 사이.
  // 값은 % 로 저장한다 — 창 크기가 달라져도 비율이 유지된다.
  const SIDE_W = "ep_side_w", EVLIST_W = "ep_evlist_w", EVPREVIEW_H = "ep_evpreview_h";

  function dragWidth(handle, opts) {
    if (!handle) return;
    handle.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const side = EP.$("qside");
      side.classList.add("dragging");
      handle.classList.add("on");
      const move = (ev) => {
        const box = opts.box();
        if (!box || !box.width) return;
        const pct = opts.pct(ev, box);
        opts.apply(Math.max(opts.min, Math.min(opts.max, pct)));
      };
      const up = () => {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        side.classList.remove("dragging");
        handle.classList.remove("on");
        localStorage.setItem(opts.key, opts.read());
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });
  }

  function dragEvidenceSplit(handle, list) {
    if (!handle || !list) return;
    handle.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const side = EP.$("qside");
      const split = list.parentElement;
      const vertical = !!split.closest("#tab-question");
      side.classList.add("dragging");
      handle.classList.add("on");
      const move = (ev) => {
        const box = split.getBoundingClientRect();
        if (vertical) {
          const pct = Math.max(35, Math.min(82, ((ev.clientY - box.top) / box.height) * 100));
          split.style.gridTemplateRows = `minmax(0, ${pct}%) 5px minmax(110px, 1fr)`;
        } else {
          const pct = Math.max(12, Math.min(80, ((ev.clientX - box.left) / box.width) * 100));
          list.style.width = pct + "%";
        }
      };
      const up = () => {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        side.classList.remove("dragging");
        handle.classList.remove("on");
        if (vertical) {
          const match = split.style.gridTemplateRows.match(/([\d.]+)%/);
          if (match) localStorage.setItem(EVPREVIEW_H, match[1]);
        } else {
          localStorage.setItem(EVLIST_W, parseFloat(list.style.width));
        }
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });
  }

  /** 저장해둔 폭을 다시 입힌다. 패널이 탭 사이를 옮겨다녀도 인라인 스타일이라 살아남는다 */
  EP.applyWidths = function () {
    const side = EP.$("qside"), list = EP.$("evList");
    const sw = localStorage.getItem(SIDE_W), lw = localStorage.getItem(EVLIST_W);
    const previewHeight = localStorage.getItem(EVPREVIEW_H);
    if (side && sw) side.style.width = sw + "%";
    if (list && lw) list.style.width = lw + "%";
    if (list && previewHeight && list.parentElement.closest("#tab-question")) {
      list.parentElement.style.gridTemplateRows = `minmax(0, ${previewHeight}%) 5px minmax(110px, 1fr)`;
    }
  };

  EP.initDrag = function () {
    const side = EP.$("qside");
    dragWidth(EP.$("qsideDrag"), {
      key: SIDE_W, min: 20, max: 85,
      box: () => side.parentElement.getBoundingClientRect(),
      // 오른쪽에 붙어 있으므로 '오른쪽 끝 - 마우스' 가 패널 폭이다
      pct: (ev, box) => ((box.right - ev.clientX) / box.width) * 100,
      apply: (p) => { side.style.width = p + "%"; },
      read: () => Math.round(parseFloat(side.style.width) * 10) / 10,
    });
    const list = EP.$("evList");
    dragEvidenceSplit(EP.$("evDrag"), list);
    EP.applyWidths();
  };

  EP.initTabs = function () {
    document.querySelectorAll(".nz-navi").forEach((btn) => {
      btn.onclick = () => {
        document.querySelectorAll(".nz-navi").forEach((b) => b.classList.remove("on"));
        btn.classList.add("on");
        const tab = btn.dataset.tab;
        TABS.forEach((t) => {
          const el = EP.$("tab-" + t); if (el) el.hidden = t !== tab;
        });
        if (tab === "bank" || tab === "question") EP.moveSide(tab);
        if (tab === "question") {
          EP.loadPicker(); EP.renderPresets(); EP.renderChecklist();
          EP.onTypeChange(); EP.loadRefs(EP.S.editingQid);
          if (EP.authoringInit) EP.authoringInit();
        }
        if (tab === "qbank") EP.loadQuestions();
        if (tab === "config") EP.loadConfig();
        if (tab === "set") EP.loadSets();
        if (tab === "lesson") EP.loadLessons();
      };
    });
  };

  async function init() {
    document.addEventListener("keydown", (e) => {
      const t = e.target.tagName;
      if (t === "INPUT" || t === "TEXTAREA" || t === "SELECT") return;
      // 방향키로 근거 검색 결과를 옮겨 다닌다 — 패널이 있는 두 탭에서만
      const on = ["tab-question", "tab-bank"].some((id) => EP.$(id) && !EP.$(id).hidden);
      if (!on) return;
      if (e.key === "ArrowDown") { e.preventDefault(); EP.moveResult(1); }
      if (e.key === "ArrowUp") { e.preventDefault(); EP.moveResult(-1); }
    });
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".nz-stdwrap")) { const b = EP.$("stdList"); if (b) b.classList.add("hidden"); }
      if (!e.target.closest(".nz-routinewrap")) { const b = EP.$("routineList"); if (b) b.classList.add("hidden"); }
    });
    EP.initTabs();
    EP.moveSide("bank");        // 첫 화면이 명제 Pool 이다
    EP.initDrag();
    if (localStorage.getItem("ep_side_folded")) {
      const el = EP.$("qside");
      if (el) {
        el.classList.add("folded");
        el.style.width = "";     // 접힌 채로 시작하면 저장된 폭은 펼칠 때 다시 입힌다
        EP.$("evFoldBtn").textContent = "+";
      }
    }
    await EP.loadStandards();
    await EP.loadProps();
  }
  document.addEventListener("DOMContentLoaded", init);
})(window.EP);
