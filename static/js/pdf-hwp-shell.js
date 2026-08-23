window.EP = window.EP || {};

(function (EP) {
  "use strict";

  EP.$ = (id) => document.getElementById(id);
  EP.esc = (value) => String(value ?? "").replace(/[&<>\"]/g,
    (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[character]));
  EP.escAttr = (value) => String(value ?? "").replace(/[&<>\"']/g,
    (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));

  document.addEventListener("DOMContentLoaded", () => EP.pdfHwpInit?.());
})(window.EP);
