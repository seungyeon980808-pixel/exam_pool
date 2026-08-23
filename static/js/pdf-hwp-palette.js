window.EP = window.EP || {};

(function (EP) {
  "use strict";

  const API = "/api/pdf-hwp/palette";

  function setStatus(message, tone = "") {
    const status = EP.$("pdfHwpPaletteStatus");
    if (!status) return;
    status.textContent = message;
    status.dataset.tone = tone;
  }

  async function readResponse(response) {
    if (response.ok) return response.json();
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "양식을 처리하지 못했습니다.");
  }

  async function loadPalette() {
    const name = EP.$("pdfHwpPaletteName");
    const download = EP.$("pdfHwpPaletteDownload");
    try {
      const palette = await readResponse(await fetch(API, { cache: "no-store" }));
      if (name) name.textContent = palette.name || palette.filename || "현재 수능 양식";
      if (download) download.hidden = false;
      setStatus(`${palette.items?.length || 0}개 양식이 포함되어 있습니다.`, "ready");
    } catch (error) {
      if (name) name.textContent = "등록된 수능 양식 없음";
      if (download) download.hidden = true;
      setStatus(error.message, "error");
    }
  }

  async function uploadPalette(file) {
    const input = EP.$("pdfHwpPaletteFile");
    const form = new FormData();
    form.append("file", file);
    if (input) input.disabled = true;
    setStatus("수정본을 확인하고 적용하는 중입니다.", "working");
    try {
      const palette = await readResponse(await fetch(API, { method: "POST", body: form }));
      const name = EP.$("pdfHwpPaletteName");
      const download = EP.$("pdfHwpPaletteDownload");
      if (name) name.textContent = palette.name || palette.filename || file.name;
      if (download) download.hidden = false;
      setStatus("수정본을 현재 수능 양식으로 적용했습니다. 다음 변환부터 사용합니다.", "success");
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      if (input) {
        input.disabled = false;
        input.value = "";
      }
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const input = EP.$("pdfHwpPaletteFile");
    input?.addEventListener("change", () => {
      const file = input.files?.[0];
      if (file) uploadPalette(file);
    });
    loadPalette();
  });
})(window.EP);
