'use strict';

// 실행 시 지정 폴더를 색인해 Electron userData(%APPDATA%/docfinder)에 저장한다.
// 앱을 켜자마자 검색 가능한 상태로 만들기 위한 검증/편의용 스크립트.

const fs = require('fs');
const path = require('path');
const os = require('os');
const { buildIndex } = require('../src/main/indexer');

async function main() {
  const folder = process.argv[2] || 'C:/Users/user/Desktop/학교업무/0. 메뉴얼';
  const dataDir = path.join(process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming'), 'docfinder');
  fs.mkdirSync(dataDir, { recursive: true });

  console.log('색인 폴더:', folder);
  const index = await buildIndex(folder, ({ current, total, file }) => {
    if (current % 3 === 0 || current === total) console.log(`  [${current}/${total}] ${file}`);
  });

  fs.writeFileSync(path.join(dataDir, 'index.json'), JSON.stringify(index), 'utf8');
  fs.writeFileSync(path.join(dataDir, 'config.json'), JSON.stringify({ folder }, null, 2), 'utf8');
  console.log('저장 위치:', dataDir);
  console.log('문서', index.docs.length, '개 / 페이지', index.pages.length, '개 색인 완료');
}

main().catch((e) => { console.error(e); process.exit(1); });
