/* ===== 수업 기록 (Phase 2) =====
 * 클로바노트 등에서 받은 텍스트를 붙여넣으면 교과서와 같은 인덱스에 들어가
 * 근거 검색창에서 함께 검색된다. 학생 발언이 섞일 수 있어 로컬에만 남는다.
 */
(function (EP) {
  "use strict";
  const $ = EP.$, esc = EP.esc, api = EP.api, post = EP.post, put = EP.put, del = EP.del;

  let editingId = null;

  EP.loadLessons = async function () {
    const q = $("lsSearch") ? $("lsSearch").value.trim() : "";
    const rows = await api("/api/lessons?" + new URLSearchParams(q ? { q } : {}));
    $("lsCnt").textContent = rows.length;
    $("lsRows").innerHTML = rows.map((r, i) => `
      <tr>
        <td class="cc">${i + 1}</td>
        <td class="cc">${esc(r.date)}</td>
        <td class="cc">${esc(r.class_name || "-")}</td>
        <td>${esc(r.summary || "-")}</td>
        <td class="cc">${(r.chars || 0).toLocaleString()}자</td>
        <td class="cc ${r.indexed_at ? "g" : "r"}">${r.indexed_at ? "색인됨" : "미색인"}</td>
        <td class="cc">
          <button class="nz-tb mini" onclick="EP.editLesson(${r.id})">수정</button>
          <button class="nz-tb mini" onclick="EP.delLesson(${r.id})">×</button>
        </td>
      </tr>`).join("")
      || '<tr class="nz-empty"><td colspan="7">등록된 수업 기록이 없습니다. 아래에 붙여넣고 저장하세요.</td></tr>';
  };

  EP.newLesson = function () {
    editingId = null;
    $("lsDate").value = new Date().toISOString().slice(0, 10);
    $("lsClass").value = "";
    $("lsSummary").value = "";
    $("lsText").value = "";
    $("lsFormTitle").textContent = "새 수업 기록";
    $("lsSaveBtn").textContent = "저장하고 색인";
    $("lsText").focus();
  };

  EP.editLesson = async function (id) {
    const d = await api(`/api/lessons/${id}`);
    editingId = id;
    $("lsDate").value = d.date;
    $("lsClass").value = d.class_name || "";
    $("lsSummary").value = d.summary || "";
    $("lsText").value = d.transcript || "";
    $("lsFormTitle").textContent = `수업 기록 수정 (${d.date})`;
    $("lsSaveBtn").textContent = "수정하고 다시 색인";
    $("lsText").scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  EP.saveLesson = async function () {
    const body = {
      date: $("lsDate").value.trim(),
      class_name: $("lsClass").value.trim(),
      summary: $("lsSummary").value.trim(),
      transcript: $("lsText").value,
    };
    if (!body.date) return alert("수업 날짜를 입력하세요.");
    if (!body.transcript.trim()) return alert("수업 기록 내용이 비어 있습니다.");
    const r = editingId ? await put(`/api/lessons/${editingId}`, body)
                        : await post("/api/lessons", body);
    alert(`저장했습니다. ${r.chunks}개 조각으로 색인되어 근거 검색에서 함께 찾힙니다.`);
    EP.newLesson();
    EP.loadLessons();
  };

  EP.delLesson = async function (id) {
    if (!confirm("이 수업 기록을 삭제할까요? 근거 검색에서도 함께 빠집니다.")) return;
    await del(`/api/lessons/${id}`);
    if (editingId === id) EP.newLesson();
    EP.loadLessons();
  };

  EP.reindexLessons = async function () {
    const r = await post("/api/lessons/reindex", {});
    alert(`수업 기록을 다시 색인했습니다 (조각 ${r.chunks}개).`);
    EP.loadLessons();
  };

  EP.onLessonSearch = EP.debounce(() => { EP.loadLessons(); }, 220);
})(window.EP);
