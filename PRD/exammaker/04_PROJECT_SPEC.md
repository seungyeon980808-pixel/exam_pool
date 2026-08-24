# ExamMaker — 프로젝트 스펙 (AI 행동 규칙)

> AI가 코드를 짤 때 지켜야 할 규칙. 구현 세션마다 이 문서를 함께 공유한다.
> 이 프로젝트는 **저장소 3개를 넘나든다** — 각 저장소의 CLAUDE.md가 이 문서보다 우선한다.

---

## 기술 스택 (전부 기존 유지 — 신규 도입 없음)

| 영역 | 선택 | 이유 |
|------|------|------|
| 허브 앱 | FastAPI + uvicorn + 바닐라 JS (32_exam_pool 그대로) | exam_set·MCP·변환기 재사용, 새 스택 학습 비용 0 |
| DB | SQLite `data/exam_pool.db` (additive 마이그레이션만) | 로컬 단일 사용자, FTS5 근거 검색 기존 자산 |
| 그림 | 5E (바닐라 JS+SVG, GitHub Pages) + mcp-5e (Node) | MCP·자가검증 루프·pHYs 300dpi가 이미 검증됨 |
| 조판 | hwpPalette (Python/Tkinter + pyhwpx COM) | 유일한 HWP 경로. CLI 진입점만 추가 |
| AI 엔진 | Claude Code 세션 (앱에 LLM·API 키 없음) | ExamPool 설계 원칙. 비용·키 관리 제로 |
| 스타일 | neis.css 재사용 + 루트 CLAUDE.md 시그니처 컬러 | 새 디자인 시스템 금지 |

---

## 프로젝트 구조 (수정 지점만)

```
32_exam_pool/
├── app/
│   ├── db.py               # additive 마이그레이션 추가
│   ├── mcp_server.py       # 쓰기 툴 4종 추가
│   ├── routes_set.py       # 청사진(계획 탭) API
│   ├── export_palette.py   # 신규 템플릿 3종 반영
│   └── prompt_builder.py   # (신규) 출제 지시문 생성
├── static/js/set.js        # 계획 탭 UI
├── docs/PIPELINE.md        # (신규) 파일명 규약·운영 절차
└── launch_all.bat          # (신규) 통합 런처

51_5E/5E_main/tools/mcp-5e/
└── server.js               # save_image 툴 추가 (+lib/)

31_hwp_palette/
├── hwp_palette/cli.py      # (신규) --markdown-file 조판 진입점
└── data/fragments/         # 템플릿 조각 3종 추가
```

---

## 절대 하지 마 (DO NOT)

- [ ] 기존 DB 컬럼·테이블을 **변경/삭제**하지 마 — 추가(ALTER ADD COLUMN)만. 마이그레이션 전 `data/backup` 백업 존재 확인
- [ ] 5E의 버전 문자열·`?v=` 캐시버스트를 올리지 마 (497곳 연동) — 버전은 사용자 지시 시에만 (루트 CLAUDE.md 절대 규칙)
- [ ] hwpPalette `data/fragments/*.hwp` 조각을 수정했으면 export_palette TEMPLATES 동기화 없이 끝내지 마 (슬롯 순서 암묵 계약 — 갭 8)
- [ ] 5E에서 SVG로 내보내지 마 — hwpPalette가 못 읽는다. **PNG 300dpi 고정**
- [ ] Claude가 5E 프로젝트 파일·HWP 파일을 **저장/덮어쓰기 하지 마** — 저장은 사용자 손 (양쪽 규약 공통)
- [ ] 브라우저 콘솔로 5E 내부 모듈을 직접 호출하지 마 (mcp-5e CONVENTIONS 금지 사항)
- [ ] API 키·비밀번호를 코드에 넣지 마 (이 시스템엔 애초에 필요 없음 — 필요해 보이면 설계가 틀린 것)
- [ ] 광범위 리팩토링 금지 — 타깃 수정만 (루트 CLAUDE.md)

---

## 항상 해 (ALWAYS DO)

- [ ] 작업 세션은 **32_exam_pool 폴더에서 시작** (exampool MCP가 프로젝트 스코프이므로 — 5E MCP는 전역이라 함께 잡힘)
- [ ] MCP 서버 코드(server.js, mcp_server.py)를 수정하면 **Claude Code 재시작** 안내
- [ ] 그림은 mcp-5e 표준 6단계 준수: app_status → describe_schema → set_page → add_* → **export_image 자가검증** → 필요 시 remove 후 재시도(2회 이내)
- [ ] 참 명제는 `search_evidence` 근거가 있을 때만 문항화, `standard_code`는 대괄호 포함 `[9과10-01]`
- [ ] 파일명은 규약(`{short_code}_{번호2자리}`) — material·5E 페이지 이름·PNG 파일명 동일 문자열
- [ ] 웹 화면 수정 후 로컬 확인 링크를 보고에 포함 (루트 CLAUDE.md)
- [ ] 커밋은 Conventional Commits, 작업 완료 후 각 저장소 push

---

## 테스트 방법

```bash
# ExamPool 서버
cd C:\ExamPool && run.bat   # http://127.0.0.1:8632

# export_palette 단위 테스트 (한글 불필요)
python -m pytest tests/

# 5E 로컬 (MCP 브릿지 활성)
cd C:\5E && run-server.bat   # ?mcp=1

# hwpPalette CLI (한컴오피스 필요)
python -m hwp_palette.cli --markdown-file 세트.md
```

---

## 배포 방법

- 32_exam_pool / 31_hwp_palette: 로컬 실행 (배포 없음), GitHub push로 버전 관리
- 51_5E: GitHub Pages (main push 시 자동) — save_image는 MCP 서버 쪽이라 Pages 배포와 무관

## 환경변수

- 없음. 경로 설정은 hwpPalette `data/config.json`(photo_dirs)과 5E MCP 인자로 관리.

---

## [NEEDS CLARIFICATION]

- [ ] save_image 저장 경로 화이트리스트 방식 (photo_dirs 연동 vs 서버 인자 고정) — 구현 시 결정
- [ ] CLI가 조판할 한글 문서: 새 문서 vs 열려 있는 문서 이어쓰기 — 가정: 새 문서
