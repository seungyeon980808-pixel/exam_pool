/* ===== 근거 문서 — PDF 폴더 인덱싱 · 원문 미리보기 =====
 * 별도 탭이었다가 환경설정 안으로 들어왔다. 검색은 명제 Pool·문항 설계
 * 오른쪽의 근거 검색 패널(question.js)이 담당한다. */
(function (EP) {
  "use strict";
  const $ = EP.$, esc = EP.esc, api = EP.api, post = EP.post, del = EP.del;
  const S = EP.S;

  EP.loadDocs = async function () {
    if (!$("docRows")) return;          // 환경설정을 아직 안 연 상태
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
  /** 이 문서만 보기 — 근거 검색 결과를 한 권으로 좁힌다 */
  EP.onlyDoc = function (id) { S.onlyDocId = S.onlyDocId === id ? 0 : id; EP.loadDocs(); EP.searchEvidence(); };

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
    if ($("docHits")) $("docHits").textContent = "색인 중…";
    try {
      const r = await post("/api/documents/index", { folder: S.curFolder, doc_type: $("docType").value });
      if ($("docHits")) $("docHits").textContent = "";
      alert(`문서 ${r.documents}권 · ${r.pages}페이지를 색인했습니다.` +
        (r.skipped.length ? `\n건너뜀 ${r.skipped.length}건(텍스트 없음/열기 실패)` : ""));
      S.docTypes = {};
      EP.loadDocs(); EP.searchEvidence();
    } catch (e) { if ($("docHits")) $("docHits").textContent = ""; alert("인덱싱 실패: " + e.message); }
  };

  EP.delDoc = async function (id) {
    if (!confirm("이 문서를 근거 검색에서 제거할까요? (원본 파일은 그대로입니다)")) return;
    await del(`/api/documents/${id}`);
    if (S.onlyDocId === id) S.onlyDocId = 0;
    EP.loadDocs(); EP.searchEvidence();
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
