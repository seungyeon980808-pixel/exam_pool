'use strict';

// 순수 JavaScript BM25 검색 엔진 (네이티브 의존성 없음)
// 한글 대응: 공백을 제거한 정규화 텍스트에 대해 부분문자열(substring) 매칭으로
// AND 조건을 처리한다. ("부정행위"가 "부정행위를"의 일부로 있어도 매칭)

const K1 = 1.5;
const B = 0.75;

// 검색/색인 공용 정규화: 소문자 + 모든 공백 제거
function normalize(s) {
  return (s || '').toLowerCase().replace(/\s+/g, '');
}

// haystack 안의 needle 등장 횟수 (겹치지 않게)
function countOcc(hay, needle) {
  if (!needle) return 0;
  let count = 0;
  let pos = 0;
  while (true) {
    const i = hay.indexOf(needle, pos);
    if (i === -1) break;
    count++;
    pos = i + needle.length;
  }
  return count;
}

// 사용자가 입력한 질의를 키워드 배열로 분해 (공백 = AND 구분)
function parseQuery(query) {
  const rawTerms = (query || '').split(/\s+/).map((t) => t.trim()).filter(Boolean);
  const keys = rawTerms.map(normalize).filter(Boolean);
  return { rawTerms, keys };
}

// 결과 목록에 보여줄 스니펫 생성.
// 매칭은 공백 제거 정규화 기준이므로, 정규화 위치를 찾아 원문(disp) 위치로 역매핑해
// 실제 키워드가 있는 부분을 정확히 보여준다. (도표 머리글 같은 엉뚱한 위치 방지)
function buildSnippet(disp, keys) {
  const BEFORE = 45;
  const AFTER = 80;
  // 정규화 문자열 + 원문 인덱스 매핑
  let norm = '';
  const map = [];
  for (let i = 0; i < disp.length; i++) {
    const ch = disp[i];
    if (/\s/.test(ch)) continue;
    norm += ch.toLowerCase();
    map.push(i);
  }
  // 첫 키워드 등장 위치(정규화 기준)
  let pos = -1;
  let klen = 0;
  for (const k of keys) {
    const idx = norm.indexOf(k);
    if (idx !== -1 && (pos === -1 || idx < pos)) { pos = idx; klen = k.length; }
  }
  let s;
  let e;
  if (pos === -1) {
    s = 0;
    e = Math.min(disp.length, 160);
  } else {
    const ns = Math.max(0, pos - BEFORE);
    const ne = Math.min(norm.length, pos + klen + AFTER);
    s = map[ns];
    e = ne < map.length ? map[ne] : disp.length;
  }
  let text = disp.slice(s, e).replace(/\s+/g, ' ').trim();
  if (s > 0) text = '… ' + text;
  if (e < disp.length) text = text + ' …';
  return text;
}

// 키워드 근접도(0~1): 모든 키워드를 포함하는 가장 좁은 구간이 짧을수록 1에 가깝다.
// 붙어 나오는(구절) 페이지를 흩어져 나오는 페이지보다 우대하기 위한 신호.
function proximity(norm, keys) {
  if (keys.length < 2) return 1; // 키워드 1개면 근접도 개념 없음 → 중립
  const events = [];
  for (let k = 0; k < keys.length; k++) {
    const key = keys[k];
    let p = 0;
    let c = 0;
    while (c < 60) {
      const i = norm.indexOf(key, p);
      if (i === -1) break;
      events.push({ s: i, e: i + key.length, k });
      p = i + key.length;
      c++;
    }
  }
  events.sort((a, b) => a.s - b.s);
  const need = keys.length;
  const have = new Map();
  let distinct = 0;
  let best = Infinity;
  let l = 0;
  for (let r = 0; r < events.length; r++) {
    have.set(events[r].k, (have.get(events[r].k) || 0) + 1);
    if (have.get(events[r].k) === 1) distinct++;
    while (distinct === need) {
      const span = events[r].e - events[l].s;
      if (span < best) best = span;
      const lk = events[l].k;
      have.set(lk, have.get(lk) - 1);
      if (have.get(lk) === 0) distinct--;
      l++;
    }
  }
  if (best === Infinity) return 0;
  const minSpan = keys.reduce((a, k) => a + k.length, 0); // 붙어있을 때의 최소 폭
  return Math.max(0, 1 - (best - minSpan) / 140); // 140자 이상 벌어지면 0
}

// pages: [{ docId, path, filename, page, disp, norm, len }]
// query: 사용자 입력 문자열
// 반환: 일치율(BM25) 높은 순 결과 배열
function bm25Search(pages, query, limit = 50) {
  const { rawTerms, keys } = parseQuery(query);
  if (!keys.length || !pages.length) return [];

  const N = pages.length;

  // 각 페이지의 키워드별 등장 횟수(tf) 계산 + 문서빈도(df) 집계
  const df = new Array(keys.length).fill(0);
  const perPage = new Array(N);
  let avglen = 0;
  for (let i = 0; i < N; i++) avglen += pages[i].len;
  avglen = avglen / N || 1;

  for (let i = 0; i < N; i++) {
    const p = pages[i];
    const tfs = new Array(keys.length);
    let all = true;
    for (let k = 0; k < keys.length; k++) {
      const tf = countOcc(p.norm, keys[k]);
      tfs[k] = tf;
      if (tf > 0) df[k]++;
      else all = false;
    }
    perPage[i] = { p, tfs, all };
  }

  // idf 계산 (BM25)
  const idf = keys.map((_, k) => {
    const n = df[k];
    return Math.log(1 + (N - n + 0.5) / (n + 0.5));
  });

  const PROX_WEIGHT = 2.2; // 근접도 가산 가중치 (붙어 나오면 점수를 크게 올림)
  const results = [];
  for (let i = 0; i < N; i++) {
    const { p, tfs, all } = perPage[i];
    if (!all) continue; // AND 조건: 모든 키워드 포함한 페이지만
    let bm25 = 0;
    for (let k = 0; k < keys.length; k++) {
      const tf = tfs[k];
      const denom = tf + K1 * (1 - B + B * (p.len / avglen));
      bm25 += idf[k] * ((tf * (K1 + 1)) / denom);
    }
    const prox = proximity(p.norm, keys); // 0~1
    const score = bm25 * (1 + PROX_WEIGHT * prox);
    results.push({
      docId: p.docId,
      path: p.path,
      filename: p.filename,
      page: p.page,
      score,
      prox,
      snippet: buildSnippet(p.disp, keys),
    });
  }

  results.sort((a, b) => b.score - a.score);
  const top = results.slice(0, limit);

  // 화면 표시용 일치율(%) : 최고점을 100% 기준으로 상대 환산
  const maxScore = top.length ? top[0].score : 1;
  for (const r of top) {
    r.matchPct = Math.round((r.score / maxScore) * 100);
  }
  return { total: results.length, items: top, terms: rawTerms };
}

module.exports = { normalize, countOcc, parseQuery, bm25Search };
