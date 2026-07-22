# ExamPool — 프로젝트 스펙 (AI 행동 규칙)

> AI가 코드를 짤 때 지켜야 할 규칙과 절대 하면 안 되는 것.
> 이 문서를 구현 세션마다 항상 함께 공유한다.

---

## 기술 스택

| 영역 | 선택 | 이유 |
|------|------|------|
| 백엔드 | Python 3.10+ / FastAPI | PDF 인덱싱·클립보드 등 로컬 작업에 Python이 최적. hwppalette와 언어 통일 → 파서 로직 참조 용이 |
| 프런트 | HTML/CSS/JS (빌드 도구 없는 바닐라 + 필요시 경량 라이브러리) | 나이스식 조회 테이블·드래그 배열·검색 UI는 웹이 Tkinter보다 압도적으로 자유로움. 사용자 JS 학습(3~4주차)과도 접점. 나이스 톤을 재현할 공용 CSS 하나(`neis.css`)를 먼저 만들고 전 화면이 공유 |
| DB | SQLite (파일 1개) + FTS5 전문 검색 | 로컬 단독, 백업=파일 복사, 명제 수천 개 검색 즉답 |
| 실행 | run.bat → uvicorn 기동 → 기본 브라우저 자동 오픈 (127.0.0.1 고정 포트) | 학교 PC에서 더블클릭 한 번 |
| 클립보드 | pyperclip (또는 표준 방식) | hwppalette 출력 전달 |
| PDF | **PyMuPDF (fitz)** 텍스트 추출 | 2026-07-22 실측: `pdftotext`(poppler)는 실제 교과서의 Adobe-Korea1 CID 폰트를 못 읽고 전멸. PyMuPDF는 327쪽 중 326쪽 완전 추출(6.4초). CID 한글 폰트 대응이 poppler보다 확실히 안정적 |
| 검색 | SQLite FTS5 + `bm25()` | DocFinder(Electron+자체 BM25 구현)와 같은 랭킹 방식을 SQLite 내장 기능으로 재현 — 별도 검색 엔진 코드 불필요. 실측 606페이지 인덱싱 7.9초, 검색 2ms 이하 |

> **AI/외부 API 없음**: 근거 확인·오답·선지·검토가 전부 검색·프리셋·규칙으로 해결돼 AI를 기획에서 제외(2026-07-22). 이 프로그램은 네트워크 통신이 아예 없다 — 학교 PC 오프라인에서 100% 동작.

---

## 프로젝트 구조 (기능별 파일 분리 — CLAUDE.md 산출물 구조 원칙)

```
32_exam_pool/
├── app/
│   ├── main.py            # FastAPI 진입점 + 정적 파일 서빙
│   ├── db.py              # SQLite 연결·스키마·마이그레이션
│   ├── models.py          # 엔티티 정의 (02_DATA_MODEL.md와 1:1)
│   ├── routes/            # API 라우트 (propositions.py, questions.py, sets.py, export.py)
│   ├── export_palette.py  # hwppalette 문법 변환기 (핵심 모듈 — 단독 테스트 필수)
│   ├── checklist.py       # 자동 검토 규칙
│   ├── pdf_indexer.py     # 폴더 재귀 스캔 + PyMuPDF 추출 + FTS5 색인 (완성·검증됨, 그대로 이식)
│   └── seed/standards.json  # 내장 성취기준 데이터 — 23단원·87개 이미 추출 완료, 그대로 사용
├── static/
│   ├── index.html         # 탭 구조 (명제 은행 / 문항 설계 / 세트 관리)
│   ├── css/               # 시그니처 디자인 시스템
│   └── js/                # 기능별 분리 (bank.js, question.js, set.js, api.js)
├── data/                  # exam_pool.db + 백업 (.gitignore)
├── PRD/                   # 이 문서들
├── run.bat
└── requirements.txt
```

---

## 용어와 hwppalette 출력 매핑 (중요)

ExamPool의 UI·데이터는 **교육평가 학계 표준 용어**를 쓴다(2026-07-22 리서치 검증). hwppalette의 현재 문법은 이와 어긋나므로(hwppalette가 "발문"을 잘못 씀 — 나중에 hwppalette 쪽을 고칠 예정), `export_palette.py`가 아래 매핑으로 변환한다:

| ExamPool (표준) | 뜻 | hwppalette 문법 키 |
|-----------------|-----|-------------------|
| 지문 (passage) | 도입 설명문 | `발문:` |
| 자료 (material) | 그림·표 | `자료:` / `사진자료:` (이미지는 `\파일이름\`) |
| 발문 (ask) | 실제 물음 | `질문:` |
| 보기 (bogi) | ㄱㄴㄷ 상자 | `보기:` |
| 선지 (choice) | ①~⑤ | `선지:` (합답형은 `선지5:`) |

> hwppalette가 도입설명을 `발문:`, 물음을 `질문:`으로 받는 것은 학계 표준과 반대다. **당장은 ExamPool이 이 매핑으로 맞춰 출력**하고, hwppalette 용어 수정은 별도 작업으로 조율한다. `export_palette.py`는 반드시 `31_hwp_palette/parser.py`의 실제 키를 확인하고 맞출 것.

부정 발문(`is_negative=true`)이면 발문의 "옳지 않은"에 `\굵게{옳지 않은}` 서식을 자동 적용한다.

---

## 절대 하지 마 (DO NOT)

- [ ] **시험 데이터(exam_pool.db, data/)를 git에 커밋하지 마** — 시험 유출 사고. .gitignore 최우선
- [ ] 근거 없는 선지 저장을 허용하는 방향으로 스키마·검증을 완화하지 마 (핵심 가치의 데이터 제약)
- [ ] hwppalette 출력 문법을 추측으로 짜지 마 — `31_hwp_palette/parser.py`·README를 읽고 실문법에 맞출 것
- [ ] 외부 CDN·외부 서버 통신을 일절 넣지 마 (AI·분석·폰트 CDN 모두) — 이 프로그램은 완전 오프라인. 폰트도 로컬 번들
- [ ] AI/LLM 호출 코드를 넣지 마 — 기획에서 제외됨. 넣으려면 먼저 사용자와 상의
- [ ] 기존 DB 스키마를 임의 변경하지 마 — 변경 필요 시 마이그레이션 코드와 함께 제안
- [ ] 목업 데이터로 완성이라고 하지 마
- [ ] 광범위 리팩토링 금지 — 타깃 수정만 (CLAUDE.md)
- [ ] `app/seed/standards.json`을 다시 만들거나 임의로 고치지 마 — 2022 개정 교육과정 원문에서 직접 추출·검증한 데이터(23단원·87개). 오류를 발견하면 원문 대조 후 최소 수정만
- [ ] PDF 문서 등록을 "파일 하나씩 업로드하는 화면"으로 만들지 마 — 반드시 폴더 지정 + 하위 폴더 재귀 스캔 방식(hwppalette 사진 폴더, DocFinder와 동일 UX)

## 항상 해 (ALWAYS DO)

- [ ] **나이스(NEIS) 디자인 언어 준수** (01_PRD "5. 디자인 방향"): 진파랑 헤더 `#2f5fa6`, 조회 버튼 `#2f6ac0`, 엑셀 초록 `#3c9d5a`, 테이블 헤더 `#eef2f7`, 합계 바 `#6b7684`, 테두리 `#d5dce6`, 각진 모서리 3~4px. 조회필터+테이블+합계바 패턴. AI티 금지
- [ ] 섹션 주석 (`# ===== SECTION =====`) 필수
- [ ] 앱 UI 하단에 버전+날짜 표기 (버전 인상은 사용자 지시 시에만)
- [ ] export_palette.py·checklist.py·pdf_indexer.py는 한글/실제 PDF 없이도 도는 단위 테스트 작성 (hwppalette tests/ 방식)
- [ ] 저장 실패·검증 실패는 사용자에게 한국어로 원인 표시
- [ ] 커밋은 Conventional Commits, 작업 완료 후 push

---

## 테스트 방법

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8632   # 또는 run.bat
python -m unittest discover -s tests          # 변환기·체크리스트 단위 테스트
```

실기 검증: 세트 출력 → 한글 붙여넣기 → hwppalette Ctrl+T → 조판 확인 (릴리즈 전 필수)

---

## 배포 방법

- GitHub 레포: `seungyeon980808-pixel/exam_pool` (코드만, data/ 제외)
- 학교 PC: git clone (또는 zip) + Python 설치 + run.bat
- 필요 시 hwppalette처럼 PyInstaller exe 패키징은 안정화 후 검토

---

## 환경변수

없음. 외부 API·네트워크 통신이 전혀 없으므로 API 키·`.env`가 필요 없다. 설정은 로컬 파일(config)만 사용한다.

---

## [NEEDS CLARIFICATION]

- [ ] 포트 번호 확정 (가안 8632)
- [ ] exe 패키징 필요 여부 (다른 교사 배포 계획이 생기면)
