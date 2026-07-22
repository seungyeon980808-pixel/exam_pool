'use strict';

// Electron 없이 색인 + 검색 로직을 실제 PDF 폴더로 검증하는 스크립트.
// 사용법: node test/run-index.js "<폴더경로>" "<검색어>"

const { buildIndex } = require('../src/main/indexer');
const { bm25Search } = require('../src/main/search');

async function main() {
  const folder = process.argv[2] || 'C:/Users/user/Desktop/학교업무/0. 메뉴얼';
  const query = process.argv[3] || '수행평가 부정행위';

  console.log('색인 폴더:', folder);
  const t0 = Date.now();
  const index = await buildIndex(folder, ({ current, total, file }) => {
    console.log(`  [${current}/${total}] ${file}`);
  });
  const t1 = Date.now();

  console.log('\n=== 색인 결과 ===');
  console.log('문서 수:', index.docs.length);
  console.log('페이지 수:', index.pages.length);
  console.log('색인 소요:', ((t1 - t0) / 1000).toFixed(1) + '초');
  if (index.skipped.length) {
    console.log('검색 제외 문서:');
    for (const s of index.skipped) console.log('   -', s.filename, '(' + s.reason + ')');
  }

  console.log('\n=== 검색:', JSON.stringify(query), '===');
  const t2 = Date.now();
  const res = bm25Search(index.pages, query, 8);
  const t3 = Date.now();
  console.log('매칭 페이지 수:', res.total, '| 검색 소요:', (t3 - t2) + 'ms');
  console.log('키워드(AND):', res.terms.join(' + '));
  console.log('');
  res.items.forEach((r, i) => {
    console.log(`${i + 1}. [${r.matchPct}%] ${r.filename} — ${r.page}페이지`);
    console.log(`   ${r.snippet}`);
  });
}

main().catch((e) => {
  console.error('오류:', e);
  process.exit(1);
});
