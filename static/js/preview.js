/* HwpPalette-backed print preview for a draft question or an exam set. */
(function (EP) {
  "use strict";

  const esc = EP.esc;
  let liveTimer = null;
  let liveRunning = false;
  let livePending = false;
  let lastFingerprint = "";
  let lastResult = null;
  let liveStartedAt = 0;
  let liveElapsedTimer = null;

  function fingerprint(question) {
    return JSON.stringify(question);
  }

  function errorMessage(error) {
    let message = error.message || "미리보기를 만들지 못했습니다.";
    try {
      const parsed = JSON.parse(message);
      message = parsed.detail || message;
    } catch (_) { /* API may return plain text. */ }
    return message;
  }

  function showLoading(title) {
    const modal = EP.modal("typesetPreviewModal");
    modal.innerHTML = `<div class="nz-modal-box nz-preview-modal">
      <div class="nz-modal-head">
        <b>${esc(title)}</b>
        <button class="nz-tb" style="margin-left:auto" onclick="EP.closeModal('typesetPreviewModal')">닫기</button>
      </div>
      <div class="nz-modal-body nz-preview-body">
        <div class="nz-preview-loading"><span class="nz-preview-spinner"></span>
          한글에서 실제 시험지 양식으로 조판하고 있습니다.<br>
          <small>첫 미리보기는 수십 초가 걸릴 수 있습니다.</small>
        </div>
      </div>
    </div>`;
    return modal;
  }

  function showResult(modal, result, title) {
    const errors = (result.issues || []).filter((issue) => issue.level === "error");
    const warning = errors.length
      ? `<div class="nz-preview-warning"><b>검토 오류 ${errors.length}건</b> — 출력 모양은 확인할 수 있지만 저장 전 수정이 필요합니다.<ul>${errors.map((x) => `<li>${esc(x.message)}</li>`).join("")}</ul></div>`
      : "";
    const pages = result.pages.map((page) => `<figure class="nz-preview-page">
      <img src="${page.image_url}" alt="조판 미리보기 ${page.page_no}쪽" width="${page.width}" height="${page.height}">
      <figcaption>${page.page_no}쪽</figcaption>
    </figure>`).join("");
    modal.innerHTML = `<div class="nz-modal-box nz-preview-modal">
      <div class="nz-modal-head">
        <b>${esc(title)}</b>
        <span class="nz-preview-meta">${result.pages.length}쪽 · ${result.cached ? "캐시된 결과" : "방금 조판"}</span>
        <a class="nz-tb" href="${result.pdf_url}" target="_blank" rel="noopener">PDF 열기</a>
        <a class="nz-tb grn" href="${result.hwp_url}">HWP 내려받기</a>
        <button class="nz-tb" onclick="EP.closeModal('typesetPreviewModal')">닫기</button>
      </div>
      <div class="nz-modal-body nz-preview-body">${warning}<div class="nz-preview-pages">${pages}</div></div>
    </div>`;
  }

  function showError(modal, error) {
    const message = errorMessage(error);
    const body = modal.querySelector(".nz-modal-body");
    body.innerHTML = `<div class="nz-preview-error"><b>조판 미리보기 실패</b><pre>${esc(message)}</pre></div>`;
  }

  function liveElements() {
    return {
      status: document.getElementById("auLivePreviewStatus"),
      empty: document.getElementById("auLivePreviewEmpty"),
      loading: document.getElementById("auLivePreviewLoading"),
      image: document.getElementById("auLivePreviewImage"),
      error: document.getElementById("auLivePreviewError"),
      meta: document.getElementById("auLivePreviewMeta"),
      work: document.getElementById("auLivePreviewWork"),
      workHelp: document.getElementById("auLivePreviewWorkHelp"),
    };
  }

  function stopElapsed() {
    clearInterval(liveElapsedTimer);
    liveElapsedTimer = null;
  }

  function updateElapsed() {
    const el = liveElements();
    if (!el.status || !liveStartedAt) return;
    const seconds = Math.max(0, Math.floor((Date.now() - liveStartedAt) / 1000));
    el.status.textContent = `자동 조판 중 · ${seconds}초`;
    el.work.textContent = `HwpPalette 조판 중 · ${seconds}초`;
    el.workHelp.textContent = seconds < 8
      ? "시험지 양식에 현재 문항을 배치하고 있습니다."
      : "한글 렌더링을 기다리고 있습니다. 첫 실행은 수십 초가 걸릴 수 있습니다.";
  }

  function startElapsed() {
    stopElapsed();
    liveStartedAt = Date.now();
    updateElapsed();
    liveElapsedTimer = setInterval(updateElapsed, 1000);
  }

  function setLiveState(state, data) {
    const el = liveElements();
    if (!el.status) return;
    if (state !== "loading") stopElapsed();
    el.status.className = `au-live-status ${state === "empty" ? "" : state}`;
    el.empty.classList.toggle("hidden", state !== "empty");
    const working = state === "queued" || state === "loading";
    const hasOldPreview = !!el.image.getAttribute("src");
    el.loading.classList.toggle("hidden", !working);
    el.image.classList.toggle("hidden", state !== "ready" && !(working && hasOldPreview));
    el.image.classList.toggle("stale", working && hasOldPreview);
    el.error.classList.toggle("hidden", state !== "error");
    if (state === "empty") {
      el.status.textContent = "입력 대기";
      el.meta.textContent = "입력이 멈추면 자동 갱신";
      el.image.removeAttribute("src");
    } else if (state === "queued") {
      el.status.textContent = liveRunning ? "새 변경 감지" : "변경 감지 · 준비 중";
      el.work.textContent = liveRunning ? "최신 변경 사항 대기 중" : "자동 조판 준비 중";
      el.workHelp.textContent = liveRunning
        ? "현재 조판이 끝나면 변경된 내용으로 다시 조판합니다."
        : "입력을 마치면 1.2초 뒤 자동으로 시작합니다.";
      el.meta.textContent = liveRunning ? "현재 작업 완료 후 다시 갱신" : "입력 변경을 확인했습니다.";
    } else if (state === "loading") {
      el.meta.textContent = "HwpPalette에 현재 문항을 전달했습니다.";
    } else if (state === "ready") {
      const page = data.pages[0];
      el.status.textContent = "최신 상태";
      el.image.src = page.image_url;
      el.image.width = page.width;
      el.image.height = page.height;
      el.image.classList.remove("stale");
      el.meta.textContent = `${data.pages.length}쪽 · ${data.cached ? "캐시됨" : "방금 조판"}`;
    } else {
      el.status.textContent = "조판 오류";
      el.error.textContent = data;
      el.meta.textContent = "내용을 수정하거나 새로고침하세요.";
    }
  }

  async function renderLivePreview(force) {
    if (!document.getElementById("auLivePreview")) return;
    const question = EP.collectQuestion();
    const currentFingerprint = fingerprint(question);
    if (!question.ask.trim()) {
      lastFingerprint = "";
      lastResult = null;
      setLiveState("empty");
      return;
    }
    if (!force && currentFingerprint === lastFingerprint && lastResult) return;
    if (liveRunning) {
      livePending = true;
      return;
    }

    liveRunning = true;
    setLiveState("loading");
    startElapsed();
    try {
      const result = await EP.post("/api/previews/question", question);
      const latestFingerprint = fingerprint(EP.collectQuestion());
      if (latestFingerprint === currentFingerprint) {
        lastFingerprint = currentFingerprint;
        lastResult = result;
        setLiveState("ready", result);
      } else {
        livePending = true;
      }
    } catch (error) {
      if (fingerprint(EP.collectQuestion()) === currentFingerprint) {
        setLiveState("error", errorMessage(error));
      } else {
        livePending = true;
      }
    } finally {
      liveRunning = false;
      if (livePending) {
        livePending = false;
        EP.scheduleQuestionPreview(100);
      }
    }
  }

  EP.scheduleQuestionPreview = function (delay) {
    clearTimeout(liveTimer);
    if (!document.getElementById("auLivePreview")) return;
    const question = EP.collectQuestion();
    if (!question.ask.trim()) {
      setLiveState("empty");
      return;
    }
    if (!liveRunning && fingerprint(question) === lastFingerprint && lastResult) {
      setLiveState("ready", lastResult);
      return;
    }
    setLiveState("queued");
    liveTimer = setTimeout(() => renderLivePreview(false), delay == null ? 1200 : delay);
  };

  EP.refreshQuestionPreview = function () {
    clearTimeout(liveTimer);
    renderLivePreview(true);
  };

  EP.previewQuestion = async function () {
    const question = EP.collectQuestion();
    if (!question.ask.trim()) return alert("발문을 입력한 뒤 미리보기를 실행하세요.");
    if (lastResult && fingerprint(question) === lastFingerprint) {
      const modal = EP.modal("typesetPreviewModal");
      showResult(modal, lastResult, "현재 문항 출력 미리보기");
      return;
    }
    const modal = showLoading("현재 문항 출력 미리보기");
    try {
      const result = await EP.post("/api/previews/question", question);
      lastFingerprint = fingerprint(question);
      lastResult = result;
      setLiveState("ready", result);
      showResult(modal, result, "현재 문항 출력 미리보기");
    } catch (error) { showError(modal, error); }
  };

  EP.previewSet = async function () {
    if (!EP.S.curSetId) return alert("세트를 먼저 고르세요.");
    const modal = showLoading("시험지 세트 출력 미리보기");
    try {
      const result = await EP.post(`/api/sets/${EP.S.curSetId}/preview`, {});
      showResult(modal, result, `시험지 세트 출력 미리보기 · ${result.count}문항`);
    } catch (error) { showError(modal, error); }
  };

  document.addEventListener("DOMContentLoaded", () => {
    const current = document.querySelector(".au-current");
    if (current) {
      current.addEventListener("input", () => EP.scheduleQuestionPreview());
      current.addEventListener("change", () => EP.scheduleQuestionPreview());
    }
    // 이전 Observer 정리 (탭 전환 시 누적 방지)
    if (EP._previewObservers) EP._previewObservers.forEach((o) => o.disconnect());
    EP._previewObservers = [];
    ["bogiRows", "choiceRows"].forEach((id) => {
      const target = document.getElementById(id);
      if (target) {
        const obs = new MutationObserver(() => EP.scheduleQuestionPreview());
        obs.observe(target, { childList: true });
        EP._previewObservers.push(obs);
      }
    });
    EP.scheduleQuestionPreview(250);
  });
})(window.EP);
