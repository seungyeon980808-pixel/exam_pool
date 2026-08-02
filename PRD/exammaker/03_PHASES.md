# ExamMaker — Phase 분리 계획

> 각 Phase가 끝날 때마다 "실제로 출제에 쓸 수 있는 상태"를 유지한다.
> 수정 대상 저장소가 3개(32_exam_pool, 51_5E, 31_hwp_palette)이므로, 각 항목에 저장소를 명시한다.

---

## Phase 1: MVP — 파이프라인 완결 + 세트 청사진 (예상 2~3주)

### 목표
청사진 작성 → 지시문 복사 → Claude 생성 → 검토 → CLI 조판까지 **한 번도 파일을 손으로 나르지 않고** 완주.

### 기능 (구현 순서대로 — 앞이 뒤의 전제)
- [ ] **1. 파일명 규약 확정 + 문서화** [32_exam_pool] — `docs/PIPELINE.md` 신설: 파일명 규약, PNG 300dpi 강제(SVG 금지), 세션은 32_exam_pool에서 시작, MCP 수정 시 Claude 재시작
- [ ] **2. ExamPool 쓰기 MCP** [32_exam_pool] — `create_question` / `update_question` / `attach_to_set` / `get_blueprint` (app/mcp_server.py 확장)
- [ ] **3. 5E `save_image` 툴** [51_5E] — tools/mcp-5e/server.js에 추가. 재료는 기존 export 파이프라인 + `lib/project.js` 경로 쓰기 + `tools/png-receiver.py` 로직. 경로 화이트리스트(설정된 사진 폴더만) 적용
- [ ] **4. DB 마이그레이션 + 청사진 화면** [32_exam_pool] — exam_set/set_item additive 컬럼, 세트 화면에 "계획" 탭(슬롯 매트릭스 + 배점 합계 표시), neis.css 재사용
- [ ] **5. 출제 지시문 생성기** [32_exam_pool] — 청사진 → 표준 지시문(한국어+영어 병기, MCP 규약·파일명 규약 포함) 클립보드 복사
- [ ] **6. 템플릿 3종 보강** [31_hwp_palette + 32_exam_pool] — 합답형0사진5선지 / 정답형+사진 / 서술형 조각 등록, export_palette.py TEMPLATES 갱신
- [ ] **7. hwpPalette CLI** [31_hwp_palette] — `python -m hwp_palette.cli --markdown-file 세트.md` → 한글 열어 전체 조판 (저장은 사용자)
- [ ] **8. 통합 런처** [32_exam_pool] — `launch_all.bat`: uvicorn:8632 + 5E 로컬 서버(?mcp=1 링크) + 안내 출력
- [ ] **9. 실전 리허설** — 실제 성취기준으로 5문항 세트 1개를 끝까지 뽑아 성공 기준 검증

### 데이터
- exam_set(+status, short_code, total_points_target), set_item(+plan_* 8종, question_id), 기존 question/choice/evidence

### 인증
- 없음 (로컬 개인 도구, 기존 ExamPool과 동일)

### "진짜 제품" 체크리스트 (로컬 도구 기준으로 재정의)
- [ ] 실제 exam_pool.db에 마이그레이션 적용 (목업 X) — 적용 전 `data/backup` 롤링 백업 확인
- [ ] 실제 한컴오피스에서 CLI 조판 검증 (프린트 미리보기 수준까지)
- [ ] 5E 실제 화면에서 save_image → 사진 폴더 → 조판 삽입까지 통짜 검증
- [ ] 세 저장소 각각 커밋·push 완료

### Phase 1 시작 프롬프트
```
이 기획 문서를 읽고 Phase 1을 구현해주세요.
@PRD/exammaker/01_PRD.md
@PRD/exammaker/02_DATA_MODEL.md
@PRD/exammaker/04_PROJECT_SPEC.md

Phase 1 범위: 03_PHASES.md의 Phase 1 기능 1~9번을 순서대로.
반드시 지켜야 할 것:
- 04_PROJECT_SPEC.md의 "절대 하지 마" 목록 준수
- 기존 스키마는 additive 변경만, 적용 전 백업 확인
- 저장소 3개를 넘나드는 작업은 각 저장소의 CLAUDE.md 규칙 우선

Do not ask clarifying questions. Make reasonable assumptions and proceed.
```

---

## Phase 2: 검토 고도화 (예상 1~2주)

### 전제 조건
- Phase 1 리허설 세트가 실제로 조판까지 성공한 상태

### 목표
"검토만 한다"를 실질화 — 판단 근거가 한 화면에 모이고, 그림 수정 왕복이 짧아진다.

### 기능
- [ ] 검토 리포트 뷰 [32_exam_pool] — 문항별 근거 인용·check_question_rules 결과·그림 썸네일을 한 화면에
- [ ] 슬롯 덤프 검증 CLI [31_hwp_palette] — 조각 파일 빈칸 순서 덤프 → export_palette TEMPLATES 자동 대조 (갭 8 제거)
- [ ] 그림 클릭 → 5E 해당 페이지 딥링크 [32_exam_pool + 51_5E] — "더블클릭 수정" UX의 웹 버전
- [ ] 이원목적분류표·정답표 자동 생성 보강 [32_exam_pool] — 기존 reports.py 확장

### 추가 데이터
- 없음 (기존 구조 조인만)

### 통합 테스트
- Phase 1의 5문항 리허설 세트를 재사용해 회귀 확인

---

## Phase 3: 완전 자동화 후보 (기간 미정 — 필요가 검증되면)

### 전제 조건
- Phase 1+2로 실제 정기고사 1회 이상 출제 완료

### 목표
남은 수동 접점(지시문 붙여넣기, CLI 실행)을 버튼으로.

### 기능
- [ ] 헤드리스 생성 버튼 — 화면 버튼이 `claude -p`로 파이프라인 실행 (진행 표시·에러 처리 포함)
- [ ] hwpPalette MCP화 — docs/SCRIPT_MCP_검토.md의 "제한된 파이썬(갈래 C)" 방향 재검토
- [ ] 문항 리롤 — 슬롯 plan_* 기반 재생성 원클릭

### 주의사항
- claude -p는 실행 시간이 분 단위 — UX 검증 전에 착수하지 않는다
- hwpPalette COM 자동화 MCP는 안정화 비용이 큼 — Phase 2까지의 실사용 데이터로 필요성을 먼저 판단

---

## Phase 로드맵 요약

| Phase | 핵심 기능 | 상태 |
|-------|----------|------|
| Phase 1 (MVP) | 갭 봉합(쓰기 MCP·save_image·템플릿·CLI) + 청사진 화면 + 지시문 생성 | 시작 전 |
| Phase 2 | 검토 리포트, 슬롯 검증, 그림 딥링크 | Phase 1 완료 후 |
| Phase 3 | 헤드리스 버튼, hwpPalette MCP, 리롤 | 실사용 검증 후 |
