'use strict';

// 폴더를 스캔해 PDF들의 "페이지별 텍스트"를 추출하고 색인 데이터를 만든다.
// 네이티브 모듈 없이 pdf.js(legacy Node 빌드)만 사용.

const fs = require('fs');
const path = require('path');
const { normalize } = require('./search');

// pdf.js Node(legacy) 빌드
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

// CJK(한글) 폰트의 정확한 텍스트 추출을 위해 cMap / 표준폰트 경로 지정
const PDFJS_ROOT = path.dirname(require.resolve('pdfjs-dist/package.json'));
const CMAP_URL = path.join(PDFJS_ROOT, 'cmaps') + path.sep;
const STD_FONT_URL = path.join(PDFJS_ROOT, 'standard_fonts') + path.sep;

// 폴더(하위 폴더 포함)에서 PDF 파일 경로를 모두 수집
function collectPdfs(root) {
  const out = [];
  function walk(dir) {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch (e) {
      return;
    }
    for (const ent of entries) {
      const full = path.join(dir, ent.name);
      if (ent.isDirectory()) {
        walk(full);
      } else if (ent.isFile() && ent.name.toLowerCase().endsWith('.pdf')) {
        out.push(full);
      }
    }
  }
  walk(root);
  return out.sort();
}

// pdf.js 텍스트 조각(item)을 위치정보 기반으로 이어붙인다.
// 조각마다 무조건 공백을 넣으면 "징 계 기 준"처럼 글자가 벌어지므로,
// 실제 가로 간격이 있을 때만 공백을, 줄이 바뀌면 줄바꿈을 넣는다.
function assembleText(items) {
  let out = '';
  let prevEndX = null;
  let prevY = null;
  let prevH = 12;
  for (const item of items) {
    if (typeof item.str !== 'string') continue;
    const str = item.str;
    const tr = item.transform;
    if (!tr) {
      out += str;
      if (item.hasEOL) out += '\n';
      continue;
    }
    const x = tr[4];
    const y = tr[5];
    const w = item.width || 0;
    const h = item.height || prevH || 12;

    if (str === '') {
      if (item.hasEOL) { out += '\n'; prevEndX = null; prevY = y; }
      continue;
    }

    if (prevY !== null && Math.abs(y - prevY) > h * 0.6) {
      // 줄 바뀜
      if (!out.endsWith('\n')) out += '\n';
    } else if (prevEndX !== null) {
      const gap = x - prevEndX;
      // 실제 간격이 글자 높이의 일정 비율 이상일 때만 공백
      if (gap > h * 0.28 && !out.endsWith(' ') && !str.startsWith(' ')) out += ' ';
    }

    out += str;
    prevEndX = x + w;
    prevY = y;
    prevH = h;
    if (item.hasEOL) { out += '\n'; prevEndX = null; }
  }
  return out.replace(/[ \t]+/g, ' ').replace(/ *\n */g, '\n').replace(/\n{2,}/g, '\n').trim();
}

// PDF 1개에서 페이지별 텍스트 추출
async function extractPdf(filePath) {
  const data = new Uint8Array(fs.readFileSync(filePath));
  const loadingTask = pdfjsLib.getDocument({
    data,
    cMapUrl: CMAP_URL,
    cMapPacked: true,
    standardFontDataUrl: STD_FONT_URL,
    useSystemFonts: true,
    isEvalSupported: false,
    verbosity: 0,
  });
  const doc = await loadingTask.promise;
  const pages = [];
  let totalChars = 0;
  for (let n = 1; n <= doc.numPages; n++) {
    const page = await doc.getPage(n);
    const tc = await page.getTextContent();
    const disp = assembleText(tc.items);
    totalChars += disp.length;
    pages.push({ page: n, disp });
    page.cleanup();
  }
  await doc.cleanup();
  await loadingTask.destroy();
  return { numPages: doc.numPages, pages, totalChars };
}

// 폴더 전체 색인 빌드
// onProgress({ current, total, file, phase })
async function buildIndex(folder, onProgress) {
  const files = collectPdfs(folder);
  const docs = [];
  const pages = [];
  const skipped = []; // 텍스트가 거의 없는(스캔 추정) 문서
  let docId = 0;

  for (let i = 0; i < files.length; i++) {
    const filePath = files[i];
    const filename = path.basename(filePath);
    if (onProgress) onProgress({ current: i + 1, total: files.length, file: filename, phase: 'index' });
    try {
      const stat = fs.statSync(filePath);
      const { numPages, pages: pg, totalChars } = await extractPdf(filePath);
      docId++;
      const avgPerPage = numPages ? totalChars / numPages : 0;
      const likelyScanned = avgPerPage < 15; // 페이지당 평균 글자 수가 매우 적으면 스캔 추정
      docs.push({
        docId,
        path: filePath,
        filename,
        filetype: 'pdf',
        totalPages: numPages,
        size: stat.size,
        mtimeMs: stat.mtimeMs,
        likelyScanned,
      });
      if (likelyScanned) {
        skipped.push({ path: filePath, filename, reason: '텍스트 없음(스캔 추정)' });
      }
      for (const p of pg) {
        if (!p.disp) continue;
        const norm = normalize(p.disp);
        pages.push({
          docId,
          path: filePath,
          filename,
          page: p.page,
          disp: p.disp,
          norm,
          len: norm.length,
        });
      }
    } catch (e) {
      skipped.push({ path: filePath, filename, reason: '열기 실패: ' + (e && e.message ? e.message : e) });
    }
  }

  return {
    folder,
    builtAt: Date.now(),
    docs,
    pages,
    skipped,
  };
}

module.exports = { buildIndex, collectPdfs, extractPdf };
