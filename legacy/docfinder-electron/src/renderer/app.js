'use strict';

/* pdf.js 설정 (미리보기 렌더) */
const pdfjsLib = window.pdfjsLib;
pdfjsLib.GlobalWorkerOptions.workerSrc = '../../node_modules/pdfjs-dist/build/pdf.worker.js';
const CMAP_URL = '../../node_modules/pdfjs-dist/cmaps/';

/* DOM */
const $ = (id) => document.getElementById(id);
const folderPathEl = $('folderPath');
const pickBtn = $('pickBtn');
const reindexBtn = $('reindexBtn');
const searchInput = $('searchInput');
const searchStat = $('searchStat');
const resultsList = $('resultsList');
const emptyState = $('emptyState');
const legend = $('legend');
const previewTitle = $('previewTitle');
const openBtn = $('openBtn');
const previewPlaceholder = $('previewPlaceholder');
const pageWrap = $('pageWrap');
const canvas = $('pdfCanvas');
const textLayer = $('textLayer');
const previewScroll = $('previewScroll');
const overlay = $('overlay');
const overlayText = $('overlayText');
const overlayFile = $('overlayFile');
const progressBar = $('progressBar');

let currentTerms = [];
let currentOpenPath = null;
let previewToken = 0;

/* ---- 상태 반영 ---- */
function applyState(state) {
  if (state.folder) {
    folderPathEl.textContent = state.folder;
    folderPathEl.style.direction = 'rtl';
  } else {
    folderPathEl.textContent = '폴더가 지정되지 않았습니다';
    folderPathEl.style.direction = 'ltr';
  }
  const ready = state.pageCount > 0;
  searchInput.disabled = !ready;
  reindexBtn.disabled = !state.folder;
  if (ready) {
    let msg = `문서 ${state.docCount}개 · ${state.pageCount}페이지 색인됨`;
    if (state.skipped && state.skipped.length) {
      msg += ` · 검색제외 ${state.skipped.length}개`;
    }
    searchStat.textContent = msg;
    emptyState.querySelector('p').textContent = '위 검색창에 키워드를 입력하세요.';
    emptyState.querySelector('.hint').textContent = state.skipped && state.skipped.length
      ? '검색 제외 문서(텍스트 없음/열기 실패): ' + state.skipped.map((s) => s.filename).join(', ')
      : '띄어쓰기로 여러 키워드를 넣으면 모두 포함하는 페이지만 찾습니다.';
  } else {
    searchStat.textContent = '';
  }
}

/* ---- 오버레이 ---- */
function showOverlay(text) {
  overlayText.textContent = text || '색인 중…';
  overlayFile.textContent = '';
  progressBar.style.width = '0%';
  overlay.style.display = 'flex';
}
function hideOverlay() {
  overlay.style.display = 'none';
}

window.api.onProgress((p) => {
  const pct = p.total ? Math.round((p.current / p.total) * 100) : 0;
  progressBar.style.width = pct + '%';
  overlayText.textContent = `색인 중… (${p.current}/${p.total})`;
  overlayFile.textContent = p.file || '';
});

/* ---- 폴더 선택 / 재색인 ---- */
pickBtn.addEventListener('click', async () => {
  showOverlay('폴더를 읽는 중…');
  try {
    const state = await window.api.pickFolder();
    applyState(state);
    clearResults();
    clearPreview();
  } finally {
    hideOverlay();
  }
});

reindexBtn.addEventListener('click', async () => {
  showOverlay('다시 색인하는 중…');
  try {
    const state = await window.api.reindex();
    applyState(state);
    if (searchInput.value.trim()) runSearch();
  } finally {
    hideOverlay();
  }
});

/* ---- 검색 ---- */
let searchTimer = null;
searchInput.addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runSearch, 200);
});
searchInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    clearTimeout(searchTimer);
    runSearch();
  }
});

async function runSearch() {
  const q = searchInput.value.trim();
  if (!q) {
    clearResults();
    return;
  }
  const res = await window.api.search(q);
  currentTerms = res.terms || [];
  renderResults(res);
}

function clearResults() {
  resultsList.innerHTML = '';
  emptyState.style.display = 'block';
  legend.style.display = 'none';
  legend.innerHTML = '';
}

function renderLegend(terms) {
  if (!terms || !terms.length) {
    legend.style.display = 'none';
    legend.innerHTML = '';
    return;
  }
  legend.innerHTML = terms
    .map((t, i) => {
      const c = KW_COLORS[i % KW_COLORS.length];
      return `<span class="legend-chip"><span class="legend-dot" style="background:${c.solid}"></span>${escapeHtml(t)}</span>`;
    })
    .join('');
  legend.style.display = 'flex';
}

function renderResults(res) {
  emptyState.style.display = 'none';
  resultsList.innerHTML = '';
  renderLegend(res.terms);
  if (!res.items.length) {
    const div = document.createElement('div');
    div.className = 'no-result';
    div.textContent = `"${(res.terms || []).join(' + ')}" 를 모두 포함한 페이지를 찾지 못했습니다.`;
    resultsList.appendChild(div);
    return;
  }
  searchStat.textContent = `${res.total}개 페이지 일치 · ${res.items.length}개 표시`;

  // 파일별 그룹화 (items는 이미 일치율 순 → 파일이 처음 등장하는 순서 = 그 파일 최고점 순)
  const groups = new Map();
  for (const r of res.items) {
    if (!groups.has(r.path)) groups.set(r.path, { filename: r.filename, items: [] });
    groups.get(r.path).items.push(r);
  }

  for (const g of groups.values()) {
    const wrap = document.createElement('div');
    wrap.className = 'file-group';

    const head = document.createElement('div');
    head.className = 'file-group-head';
    head.innerHTML =
      `<span class="fg-name" title="${escapeHtml(g.filename)}">📄 ${escapeHtml(g.filename)}</span>` +
      `<span class="fg-count">${g.items.length}개 페이지 · 최고 ${g.items[0].matchPct}%</span>`;
    wrap.appendChild(head);

    g.items.forEach((r, i) => {
      const item = document.createElement('div');
      item.className = 'result-item';

      const top = document.createElement('div');
      top.className = 'result-top';
      top.innerHTML =
        `<span class="result-rank">${i + 1}.</span>` +
        `<span class="match-badge">${r.matchPct}%</span>` +
        `<span class="result-page">${r.page}페이지</span>`;

      const snip = document.createElement('div');
      snip.className = 'result-snippet';
      snip.innerHTML = highlightHtml(escapeHtml(r.snippet), res.terms);

      item.appendChild(top);
      item.appendChild(snip);
      item.addEventListener('click', () => {
        document.querySelectorAll('.result-item.active').forEach((el) => el.classList.remove('active'));
        item.classList.add('active');
        openPreview(r);
      });
      wrap.appendChild(item);
    });
    resultsList.appendChild(wrap);
  }
}

/* ---- 미리보기 ---- */
openBtn.addEventListener('click', async () => {
  if (!currentOpenPath) return;
  const err = await window.api.openInSystem(currentOpenPath);
  if (err) alert('원본을 여는 데 실패했습니다: ' + err);
});

function clearPreview() {
  pageWrap.style.display = 'none';
  previewPlaceholder.style.display = 'block';
  previewTitle.textContent = '미리보기';
  openBtn.disabled = true;
  currentOpenPath = null;
}

async function openPreview(r) {
  const token = ++previewToken;
  currentOpenPath = r.path;
  openBtn.disabled = false;
  previewTitle.textContent = `${r.filename} — ${r.page}페이지`;
  previewPlaceholder.style.display = 'block';
  previewPlaceholder.textContent = '페이지를 불러오는 중…';
  pageWrap.style.display = 'none';

  try {
    const bytes = await window.api.readFile(r.path);
    if (token !== previewToken) return;
    const doc = await pdfjsLib.getDocument({ data: bytes, cMapUrl: CMAP_URL, cMapPacked: true }).promise;
    if (token !== previewToken) return;
    const page = await doc.getPage(r.page);

    const base = page.getViewport({ scale: 1 });
    const containerW = previewScroll.clientWidth - 44;
    const scale = Math.min(Math.max(containerW / base.width, 0.6), 2.4);
    const dpr = window.devicePixelRatio || 1;
    const viewport = page.getViewport({ scale });

    canvas.width = Math.floor(viewport.width * dpr);
    canvas.height = Math.floor(viewport.height * dpr);
    canvas.style.width = viewport.width + 'px';
    canvas.style.height = viewport.height + 'px';
    pageWrap.style.width = viewport.width + 'px';
    pageWrap.style.height = viewport.height + 'px';

    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    await page.render({ canvasContext: ctx, viewport }).promise;
    if (token !== previewToken) return;

    // 텍스트 레이어(하이라이트)
    textLayer.innerHTML = '';
    textLayer.style.width = viewport.width + 'px';
    textLayer.style.height = viewport.height + 'px';
    try {
      const textContent = await page.getTextContent();
      const task = pdfjsLib.renderTextLayer({
        textContentSource: textContent,
        textContent: textContent,
        container: textLayer,
        viewport,
        textDivs: [],
      });
      await task.promise;
      if (token !== previewToken) return;
      highlightTextLayer(currentTerms);
    } catch (e) {
      /* 하이라이트 실패해도 페이지 렌더는 유지 */
    }

    previewPlaceholder.style.display = 'none';
    pageWrap.style.display = 'block';
    // 하이라이트된 첫 위치로 스크롤
    const firstMark = textLayer.querySelector('mark');
    if (firstMark) firstMark.scrollIntoView({ block: 'center' });
  } catch (e) {
    previewPlaceholder.textContent = '이 페이지를 미리보기할 수 없습니다. "원본 열기"를 이용하세요.';
  }
}

function highlightTextLayer(terms) {
  if (!terms || !terms.length) return;
  const re = buildTermRegex(terms);
  if (!re) return;
  const spans = textLayer.querySelectorAll('span');
  spans.forEach((span) => {
    const txt = span.textContent;
    if (txt && re.test(txt)) {
      re.lastIndex = 0;
      span.innerHTML = escapeHtml(txt).replace(re, (m) => `<mark style="background:${bgForMatch(m, terms)}">${m}</mark>`);
    }
  });
}

/* ---- 키워드 색상 (빨·주·노·초·파·남·보) ---- */
const KW_COLORS = [
  { solid: '#e8443c', bg: 'rgba(255, 90, 90, 0.45)' }, // 빨
  { solid: '#e8820a', bg: 'rgba(255, 160, 40, 0.50)' }, // 주
  { solid: '#c9a400', bg: 'rgba(250, 224, 50, 0.62)' }, // 노
  { solid: '#1f9d3a', bg: 'rgba(70, 200, 90, 0.45)' }, // 초
  { solid: '#1e6fe0', bg: 'rgba(70, 150, 255, 0.42)' }, // 파
  { solid: '#5a4fd0', bg: 'rgba(120, 110, 240, 0.48)' }, // 남
  { solid: '#a13fd0', bg: 'rgba(200, 110, 240, 0.45)' }, // 보
];

/* ---- 유틸 ---- */
function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function escapeReg(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
// 긴 키워드를 먼저 매칭하도록 정렬 (예: "부정행위"가 "부정"보다 우선)
function buildTermRegex(terms) {
  const parts = (terms || []).slice().sort((a, b) => b.length - a.length).map(escapeReg).filter(Boolean);
  if (!parts.length) return null;
  return new RegExp('(' + parts.join('|') + ')', 'gi');
}
function termIndex(m, terms) {
  const lm = m.toLowerCase();
  return (terms || []).findIndex((t) => t.toLowerCase() === lm);
}
function bgForMatch(m, terms) {
  let i = termIndex(m, terms);
  if (i < 0) i = 0;
  return KW_COLORS[i % KW_COLORS.length].bg;
}
function highlightHtml(escapedText, terms) {
  const re = buildTermRegex(terms || []);
  if (!re) return escapedText;
  return escapedText.replace(re, (m) => `<mark style="background:${bgForMatch(m, terms)}">${m}</mark>`);
}

/* ---- 시작 ---- */
(async function init() {
  const state = await window.api.getState();
  applyState(state);
})();
