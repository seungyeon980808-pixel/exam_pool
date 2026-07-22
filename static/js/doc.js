/* ===== 근거 문서 — PDF 폴더 인덱싱 · 검색 · 페이지 뷰어 ===== */
(function (EP) {
  "use strict";
  const $ = EP.$, esc = EP.esc, api = EP.api, post = EP.post, del = EP.del;
  const S = EP.S;

  EP.loadDocs = async function () {
    const docs = await api("/api/documents");
    $("docCnt").textContent = docs.length;
    $("docRows").innerHTML = docs.map((d, i) => `
      <tr><td class="cc">${i + 1}</td><td>${esc(d.title)}</td><td class="cc">${esc(d.doc_type)}</td>
      <td class="cc">${d.pages}</td><td class="cc">${esc((d.indexed_at || "").slice(0, 16))}</td>
      <td class="cc"><button class="nz-tb mini ${S.onlyDocId === d.id ? "blu" : ""}"
        onclick="EP.onlyDoc(${d.id})">${S.onlyDocId === d.id ? "해제" : "선택"}</button></td>
      <td class="cc"><button class="nz-tb mini" onclick="EP.delDoc(${d.id})">×</button></td></tr>`).join("")
      || '<tr class="nz-empty"><td colspan="7">등록된 문서가 없습니다. “폴더 선택”으로 PDF 폴더를 지정하세요.</td></tr>';
  };
  EP.toggleDocList = function () { $("docListBox").classList.toggle("hidden"); EP.loadDocs(); };
  EP.onlyDoc = function (id) { S.onlyDocId = S.onlyDocId === id ? 0 : id; EP.loadDocs(); EP.runSearch(); };

  EP.pickFolder = async function () {
    try {
      const r = await post("/api/pick-folder", {});
      if (!r.folder) return;
      S.curFolder = r.folder;
      $("docFolderLabel").textContent = S.curFolder;
      await EP.indexFolder();
    } catch (e) { alert("폴더 선택 실패: " + e.message); }
  };

  EP.indexFolder = async function () {
    if (!S.curFolder) return alert("먼저 “폴더 선택”으로 PDF 폴더를 지정하세요.");
    $("docHits").textContent = "색인 중…";
    try {
      const r = await post("/api/documents/index", { folder: S.curFolder, doc_type: $("docType").value });
      $("docHits").textContent = "";
      alert(`문서 ${r.documents}권 · ${r.pages}페이지를 색인했습니다.` +
        (r.skipped.length ? `\n건너뜀 ${r.skipped.length}건(텍스트 없음/열기 실패)` : ""));
      S.docTypes = {};
      EP.loadDocs(); EP.runSearch();
    } catch (e) { $("docHits").textContent = ""; alert("인덱싱 실패: " + e.message); }
  };

  EP.delDoc = async function (id) {
    if (!confirm("이 문서를 근거 검색에서 제거할까요? (원본 파일은 그대로입니다)")) return;
    await del(`/api/documents/${id}`);
    if (S.onlyDocId === id) S.onlyDocId = 0;
    EP.loadDocs(); EP.runSearch();
  };

  /* --- 해시태그 --- */
  EP.onTagKey = function (e) {
    if (e.key === "Enter") { e.preventDefault(); EP.runSearchFromInput(); }
  };
  EP.renderTags = function () {
    $("searchTags").innerHTML = S.searchTags.map((t, i) =>
      `<span class="nz-chip"><span class="dot" style="background:${EP.HL_COLORS[i % EP.HL_COLORS.length]}"></span>` +
      `${esc(t)}<button onclick="EP.delTag(${i})">×</button></span>`).join("");
  };
  EP.delTag = function (i) {
    S.searchTags.splice(i, 1);
    if ($("docSearch")) $("docSearch").value = S.searchTags.join(" ");
    EP.renderTags(); EP.runSearch();
  };

  /** 근거 문서 탭: 입력창의 단어들을 그대로 태그로 삼아 검색 */
  EP.runSearchFromInput = function () {
    S.searchTags = EP.splitTerms($("docSearch").value);
    EP.renderTags();
    EP.runSearch();
  };
  EP.onDocInput = EP.debounce(() => { EP.runSearchFromInput(); }, 220);

  EP.runSearch = async function () {
    if (!S.searchTags.length) {
      $("docResults").innerHTML = '<p class="nz-sub" style="padding:14px">키워드를 입력해 검색하세요.</p>';
      $("docHits").textContent = "";
      return;
    }
    const params = new URLSearchParams({ q: S.searchTags.join(" ") });
    if (S.onlyDocId) params.set("doc_id", S.onlyDocId);
    const r = await api("/api/evidence/search?" + params);
    $("docHits").innerHTML = `<b>${r.total}</b>개 페이지 일치 · ${r.items.length}개 표시`;

    // 문서별 그룹
    const groups = {};
    r.items.forEach((it) => { (groups[it.doc_title] ||= []).push(it); });
    const html = Object.entries(groups).map(([title, items]) => `
      <div class="nz-docgroup">
        <div class="nz-docgroup-head">${esc(title)} <span class="n">${items.length}개 · 최고 ${items[0].match_pct}%</span></div>
        ${items.map((it) => `
          <div class="nz-res" data-doc="${it.document_id}" data-page="${it.page_no}"
               onclick="EP.showPage(${it.document_id}, ${it.page_no}, this)">
            <div class="nz-res-top">
              <span class="nz-pct ${it.match_pct < 60 ? "low" : ""}">${it.match_pct}%</span>
              <span class="nz-res-page">${it.kind === "수업" ? it.page_no + "번째 조각" : it.page_no + "페이지"}</span>
            </div>
            <div class="nz-res-snip">${EP.markTerms(it.snippet, S.searchTags)}</div>
          </div>`).join("")}
      </div>`).join("");
    $("docResults").innerHTML = html || '<p class="nz-sub" style="padding:14px">결과가 없습니다.</p>';
  };

  /* --- 페이지 미리보기 (이미지 + 좌표 오버레이) --- */
  EP.showPage = async function (docId, pageNo, el) {
    document.querySelectorAll(".nz-res.on").forEach((n) => n.classList.remove("on"));
    if (el) el.classList.add("on");
    S.viewer.docId = docId; S.viewer.page = pageNo;

    // 수업 기록은 PDF 가 아니다 (document_id 가 음수)
    if (docId < 0) {
      const d = await api(`/api/lesson-chunk/${-docId}/${pageNo}`);
      S.viewer.lastPage = d.last_chunk;
      $("viewerTitle").textContent = `${d.title} — ${d.chunk_no} / ${d.last_chunk} 조각`;
      let html = esc(d.text);
      S.searchTags.forEach((t, i) => {
        const re = new RegExp(t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
        html = html.replace(re, (m) =>
          `<mark style="background:${EP.HL_COLORS[i % EP.HL_COLORS.length]}">${m}</mark>`);
      });
      $("viewerBody").innerHTML = `<div class="nz-lessonview">${html.replace(/\n/g, "<br />")}</div>`;
      return;
    }

    const meta = await api(`/api/documents/${docId}/page/${pageNo}`);
    S.viewer.lastPage = meta.last_page;
    $("viewerTitle").textContent = `${meta.title} — ${pageNo} / ${meta.last_page}페이지`;

    // 이미지는 검색어와 무관 → 캐시가 그대로 맞아 빠르다
    $("viewerBody").innerHTML =
      `<div class="nz-pagewrap" id="pagewrap">
         <img id="pageImg" src="/api/documents/${docId}/page/${pageNo}/image"
              alt="${esc(meta.title)} ${pageNo}페이지" />
       </div>`;

    EP.prefetch(docId, pageNo + 1);   // 다음 페이지 미리 받아두기
    if (!S.searchTags.length) return;

    const hl = await api(`/api/documents/${docId}/page/${pageNo}/highlights?q=` +
      encodeURIComponent(S.searchTags.join(" ")));
    drawHighlights(hl);
  };

  function drawHighlights(hl) {
    const wrap = $("pagewrap"), img = $("pageImg");
    if (!wrap || !img) return;
    EP.paintHighlights(wrap, img, hl, $("viewerBody"));

    // 조용한 실패 방지 — 검색은 맞았는데 위치를 못 찾은 경우 알린다
    const warn = hl.misses.length
      ? `<div class="nz-hlwarn">이 페이지에서 <b>${esc(hl.misses.join(", "))}</b>의 정확한 위치를 찾지 못했습니다 (글자가 이미지이거나 조각나 있을 수 있음)</div>`
      : "";
    const legend = Object.entries(hl.hits).filter(([, n]) => n).map(([t, n], i) =>
      `<span class="nz-leg"><i style="background:${EP.HL_COLORS[i % EP.HL_COLORS.length]}"></i>${esc(t)} ${n}</span>`).join("");
    const bar = document.createElement("div");
    bar.className = "nz-hlbar";
    bar.innerHTML = legend + warn;
    wrap.parentElement.insertBefore(bar, wrap);
  }

  EP.viewerPage = function (delta) {
    if (!S.viewer.docId) return;
    const p = S.viewer.page + delta;
    if (p < 1 || p > S.viewer.lastPage) return;
    EP.showPage(S.viewer.docId, p, null);
  };

  EP.openOriginal = async function () {
    if (!S.viewer.docId) return alert("먼저 결과를 선택하세요.");
    if (S.viewer.docId < 0) return alert("수업 기록은 원본 파일이 없습니다. 수업 기록 탭에서 여세요.");
    try { await post(`/api/documents/${S.viewer.docId}/open?page_no=${S.viewer.page}`, {}); }
    catch (e) { alert("원본을 열 수 없습니다: " + e.message); }
  };

  /** 어디서든 PDF 페이지 원문을 크게 띄운다 (모달, 하이라이트 포함) */
  EP.peekPage = async function (docId, pageNo, q) {
    const meta = await api(`/api/documents/${docId}/page/${pageNo}`);
    const m = EP.modal("peekModal");
    m.innerHTML = `<div class="nz-modal-box">
      <div class="nz-modal-head"><b>${esc(meta.title)} — ${pageNo}페이지</b>
        <button class="nz-tb" style="margin-left:auto" onclick="EP.closeModal('peekModal')">닫기</button>
      </div>
      <div class="nz-modal-body" style="background:#eef1f5;text-align:center">
        <div class="nz-pagewrap" id="peekWrap">
          <img id="peekImg" src="/api/documents/${docId}/page/${pageNo}/image?dpi=130" />
        </div>
      </div></div>`;
    if (!q) return;
    const hl = await api(`/api/documents/${docId}/page/${pageNo}/highlights?q=` +
      encodeURIComponent(q) + "&dpi=130");
    EP.paintHighlights($("peekWrap"), $("peekImg"), hl, null);
  };

  /** 수업 기록 조각을 크게 띄운다 */
  EP.peekLesson = async function (lessonId, chunkNo) {
    const d = await api(`/api/lesson-chunk/${lessonId}/${chunkNo}`);
    const m = EP.modal("peekModal");
    m.innerHTML = `<div class="nz-modal-box">
      <div class="nz-modal-head"><b>${esc(d.title)} — ${d.chunk_no} / ${d.last_chunk} 조각</b>
        <button class="nz-tb" style="margin-left:auto" onclick="EP.closeModal('peekModal')">닫기</button>
      </div>
      <div class="nz-modal-body"><div class="nz-lessonview">${esc(d.text).replace(/\n/g, "<br />")}</div></div>
    </div>`;
  };
})(window.EP);
