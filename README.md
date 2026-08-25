# ExamPool

**근거 있는 시험 출제 도구.** 참인 명제를 근거와 함께 모아두고, 그것을 조합해 문항과 세트를 설계한 뒤 hwppalette 문법으로 내보낸다.

Copyright © 2026 박승연 (SOMC) · [AGPL-3.0-only](LICENSE)

```
5E (이미지)  →  ExamPool (내용)  →  hwppalette (편집)
```

시험문제의 3요소 중 **내용**을 담당한다. 세트의 모든 선지가 "성취기준 + 교과서 원문(또는 수업 기록)"과 연결된 채로 완성되므로, 민원·오류 시비가 왔을 때 근거를 바로 제시할 수 있다.

---

## 무엇이 되나

| 화면 | 하는 일 |
|---|---|
| **명제 Pool** | 참인 명제를 성취기준·단원별로 축적. 거짓 변형(오답 재료)과 근거(교과서 원문)를 붙여 관리 |
| **문항 설계** | 명제를 골라 정답형·합답형 문항 조립. 합답형 선지는 프리셋 버튼으로 자동 생성 |
| **세트 관리** | 문항을 담아 드래그로 배열. 세트별 만점 대비 배점 합·난이도 분포·커버리지를 실시간 확인. **정답표·이원목적분류표**를 바로 뽑는다 |
| **근거 문서** | 교과서·교육과정 PDF 폴더를 색인해 근거를 즉시 검색 |
| **수업 기록** | 수업 녹취 텍스트를 붙여넣으면 교과서와 같은 인덱스에 들어가 함께 검색된다 |
| **환경설정** | 출제 범위 설정 + 롤링 백업·복구 (하루 1회 자동, 최근 3시점 보관) |
| **PDF/HWP 변환** | PDF 또는 클립보드 이미지를 문항 구조로 분석해 본문·발문·보기·선지·수식을 편집 가능한 HWP로 변환. 자료 그림은 원본 이미지로 유지 |

**외부 AI API가 필요 없다.** 근거 확인은 전문 검색(SQLite FTS5), 오답은 왜곡 유형 프리셋, 선지는 조합 프리셋, 검토는 규칙 체크리스트로 해결한다. 시험 데이터가 외부로 나가지 않는다. 이미지 OCR 모델을 최초로 준비할 때만 인터넷 연결이 필요하다.

---

## 설치 · 실행

**필요 환경**: Windows + Python 3.10+

```bash
pip install -r requirements.txt
run.bat
```

`run.bat`을 더블클릭하면 로컬 서버가 뜨고 브라우저가 자동으로 열린다 (http://127.0.0.1:8632).

### PDF/HWP 단독 변환 앱

`PDF-HWP 웹앱 실행.bat`을 더블클릭한다. 변환 전용 화면이 브라우저 탭이 아닌
독립 데스크톱 창으로 열린다. 내부 변환 서버는 매번 비어 있는 로컬 포트를 사용하며,
창을 닫으면 함께 종료된다.

- PDF: 텍스트·글꼴·글자 좌표·벡터 도형을 분석한다.
- 클립보드 이미지/PNG/JPG: 로컬 PaddleOCR로 글자와 위치를 복원한다.
- 본문·지문·발문·보기·선지: 편집 가능한 한글 텍스트로 만든다.
- 인식 가능한 수식: 한글 수식 개체로 만든다.
- 문항 자료 그림·그래프: 원래 순서와 비율을 유지한 이미지로 넣는다.

이미지 OCR 런타임은 처음 실행할 때만 `data/pdf_hwp_ocr_runtime/`에 설치된다.
이 디렉터리와 OCR 모델·사용자 PDF·변환 결과는 Git에 포함되지 않는다.

개발 환경에서 OCR까지 한 번에 설치하려면 다음 명령을 사용한다.

```bash
pip install -e ".[pdf-hwp,dev]"
```

---

## 처음 쓸 때

1. **환경설정 > 근거 문서** → 교과서·교육과정·기출 PDF가 있는 폴더를 지정해 인덱싱 (한 번만)
2. **명제 Pool 탭** → `＋ 명제 등록`으로 참인 명제를 넣고, `상세`를 연 뒤 오른쪽 근거 검색에서 `근거로 저장`
3. 참 명제에 **거짓 변형**을 추가해두면 오답 재료가 쌓인다
4. **문항 설계 탭** → 유형(정답형/합답형)을 고르고 명제를 가져와 조립 → `검토` → `문항 저장`
5. **세트 관리 탭** → 세트를 만들고(만점 지정) 문항을 담아 배열 → `세트 검토` → `hwppalette 문법 복사`
6. 한글에 붙여넣고 **Ctrl+T** (hwppalette 마크다운 변환)
7. `정답표` · `이원목적분류표` 버튼 → 표 복사(한글 표·엑셀에 그대로 붙음) 또는 인쇄

---

## 다른 과목 선생님도 쓸 수 있나

**쓸 수 있다.** 성취기준 seed만 교체하면 코드 수정 없이 동작한다 (국어·영어로 실측 검증).

```bash
python tools/extract_standards.py "국어과 교육과정.pdf" \
  --prefix 9국 --subject "중학교 국어 (2022 개정)" \
  --start "[중학교 1~3학년]" --end "선택 중심 교육과정" \
  --out app/seed/standards.json
```

자세한 내용과 한계는 [PRD/05_EXTENSIBILITY.md](PRD/05_EXTENSIBILITY.md) 참고.

---

## 문항 부분 명칭

교육평가 학계 표준 용어를 쓴다. hwppalette 출력 시 자동 변환된다.

| ExamPool | 뜻 | hwppalette 문법 |
|---|---|---|
| 지문 | 도입 설명문 | `발문:` |
| 자료 | 그림·표 | `자료:` / `사진자료:` |
| 발문 | 실제 물음 | `질문:` |
| 보기 | ㄱㄴㄷ 상자 | `보기:` |
| 선지 | ①~⑤ | `선지:` |

> hwppalette가 도입설명을 `발문:`이라 부르는 것은 표준과 어긋난다(문두=발문=물음). 지금은 ExamPool이 매핑해서 맞춰 출력하고, hwppalette 용어 수정은 별도로 조율한다.

---

## 파일 구조

```
app/
  main.py            FastAPI 진입점
  db.py              SQLite 스키마 · 성취기준 seed 적재 · 마이그레이션
  routes_bank.py     명제 · 거짓변형 · 근거 API
  routes_question.py 문항 · 세트 · 출력 · 제출 서류 API
  routes_doc.py      PDF 인덱싱 · 근거 검색 API
  routes_lesson.py   수업 기록 API
  routes_config.py   백업 · 복구 API
  pdf_indexer.py     PyMuPDF 추출 + FTS5 색인/검색
  pdf_hwp_*.py       PDF·이미지 문항 분석, 구조 복원, HWP 검증
  pdf_hwp_webapp.py  변환 단독 앱 진입점
  lessons.py         수업 기록을 같은 FTS5 인덱스에 얹기
  export_palette.py  hwppalette 문법 변환기
  reports.py         정답표 · 이원목적분류표
  checklist.py       자동 검토 규칙
  backup.py          롤링 백업(.bak1~3) · 복구
  seed/standards.json  2022 개정 과학과 교육과정 — 중·고 20과목, 성취기준 371개
                       (성취기준 해설 · 단원별 유의사항 · 탐구 활동 포함)
static/
  index.html         화면 (나이스 디자인)
  js/core.js         공용 뼈대 — 통신·성취기준·출제 범위·탭
  js/bank.js         명제 Pool        js/question.js  문항 설계
  js/qbank.js        문항 Pool        js/set.js       세트 관리 · 제출 서류
  js/doc.js          근거 문서        js/lesson.js    수업 기록
  js/config.js       환경설정 · 백업
  pdf-hwp.html       PDF/HWP 단독 변환 화면
  js/pdf-hwp*.js     변환 화면 동작
tools/               성취기준 추출 도구
tests/               단위 테스트 (한글·PDF 없이 실행)
data/                DB · 백업 (git 제외)
```

## 라이선스

ExamPool 자체 소스는 GNU Affero General Public License v3.0 only에 따라
배포됩니다. 자세한 조건은 [LICENSE](LICENSE), 포함된 외부 구성 요소는
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), 한글 오토메이션 관련 안내는
[HANCOM_AUTOMATION_NOTICE.md](HANCOM_AUTOMATION_NOTICE.md)를 확인하세요.

## 테스트

```bash
python -m pytest
```

---

## 라이선스 · 저작자

박승연 (© 2026)
