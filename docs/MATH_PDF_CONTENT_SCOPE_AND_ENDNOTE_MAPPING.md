# 수학 PDF 작업 범위·문항 대응·미주 매핑 지시서

## 목적

교재 PDF에는 표지, 목차, 학습계획표, 개념 설명, 예제, 본문 문제, 정답표와 해설이
섞일 수 있다. 전체 PDF를 곧바로 OCR하면 사용자가 요청하지 않은 앞부분이 본문에
들어가고, PDF 페이지 하나를 문항 하나로 잘못 취급하며, 문제 페이지와 해설 페이지를
순서대로 연결하는 오류가 생긴다. 이 문서는 OCR 전에 작업 범위를 확정하고 실제
문항 단위로 문제·해설·네이티브 미주를 연결하는 필수 절차를 정한다.

실행 정책과 검사는 다음 파일을 사용한다.

- `config/math_content_scope_policy_v1.json`
- `app/math_content_scope.py`
- `tools/math_content_scope_preflight.py`

## 권위와 우선순위

1. 사용자가 지정한 페이지·영역 범위
2. PDF를 직접 보고 확정한 페이지 역할과 문항 영역
3. OCR 후보와 자동 페이지 분류

사용자가 “PDF 1~9쪽 제외, 10쪽부터 실제 문제 영역만”처럼 범위를 지정하면 그
지시가 자동 OCR 결과보다 우선한다. PDF 파일의 실제 1-based 쪽번호와 문서에 인쇄된
쪽번호는 별도 필드로 기록한다.

## OCR 전 필수 산출물

`math-content-scope-manifest-v1`에는 다음을 모두 기록한다.

- 문제 PDF·해설 PDF SHA-256과 실제 페이지 수
- 모든 물리 페이지의 역할: `cover`, `toc`, `study_plan`, `concept`, `divider`,
  `problem`, `solution`, `mixed`, `answer_key`, `blank`, `advertisement` 등
- 혼합 페이지의 포함/제외 영역 bbox와 crop SHA-256
- 영역별 PDF 쪽, 인쇄 쪽, 단, 읽기 순서, 문항 ID
- 사용자 지정 시작 쪽과 제외 쪽
- 문항별 문제 영역과 해설 영역 목록
- 문제 첫 문장·해설 첫 문장 및 정규화 본문 해시
- 문항 대응 증거와 검수 상태
- OCR에 실제 전달할 region ID의 정확한 목록
- 출력 배치 방식: 문항 중심 재조판 `item_reflow` 또는 원문 영역 배치
  `source_region_layout`

모든 페이지·영역·문항은 `review_status: VERIFIED`, `uncertainties: []`여야 한다.
전체 페이지를 OCR한 뒤 필요 없는 내용을 사후 삭제하는 방식은 허용하지 않는다.

부분 범위 작업의 출력 페이지 수는 전체 원본 PDF 페이지 수와 비교하지 않는다.
`item_reflow`는 검수된 문항 순서·개체 결합을 기준으로 새로 조판하고,
`source_region_layout`은 포함된 source region의 배치를 기준으로 검사한다. 어떤 모드도
제외된 페이지·영역을 페이지 수를 맞추기 위해 다시 넣어서는 안 된다.

## 혼합 페이지와 읽기 순서

개념 설명과 첫 문제가 같은 페이지에 있으면 페이지 전체를 포함하거나 제외하지 않는다.
개념 영역은 `excluded`, 문제 영역만 `problem`으로 분할하여 문제 crop만 OCR한다.
해설 한 페이지에 둘 이상의 문항이 있거나 하나의 해설이 다음 페이지로 이어져도 같은
방식으로 문항별 영역을 분리한다.

2단 페이지는 단순한 y/x 정렬을 금지한다. 먼저 영역을 왼쪽 단·오른쪽 단으로 나누고,
각 단 안에서 위에서 아래로 읽은 뒤 명시된 `reading_order`를 따른다. 같은 페이지의
reading order 중복과 column ID 누락은 FAIL이다.

## 문항 ID와 문제·해설 대응

문항 ID는 책 전체에서 유일해야 한다. 같은 숫자 `1`이라도 단원 예제, 단계별 문제,
미니 모의고사는 서로 다른 ID를 사용한다. 대응은 다음 네 증거를 모두 사용한다.

- 단원·세트·단계
- 인쇄된 문항 라벨
- 문제 첫 문장
- 해설의 대상 문제 내용

다음 방식은 금지한다.

- PDF 페이지당 문항 하나로 가정
- PDF 페이지당 미주 하나 생성
- 문제와 해설 페이지를 인덱스 또는 modulo로 순환 연결
- 인쇄된 번호 하나만으로 대응
- 한 페이지의 여러 해설을 한 문항 해설로 합치기

한 문항이 여러 영역·페이지에 걸치면 같은 item ID 아래 영역 목록을 순서대로 둔다.
모든 포함 영역은 정확히 한 문항에 소유되어야 하며, 해설 영역이 없는 문항은 제작을
시작하지 않는다.

## 네이티브 미주 계약

미주는 페이지가 아니라 검수된 문항에 삽입한다. 다음 등식이 모두 성립해야 한다.

```text
검수된 문제 item 수
= 검수된 해설 item 수
= 실제 미주 참조 수
= 실제 미주 본문 수
```

각 문제 번호 뒤에 해당 item ID의 미주 하나만 삽입한다. 해설 본문·수식·표·허용된
그림은 해설 영역의 원문 순서대로 넣는다. `page_round_robin`, 번호만 일치하는 대체,
해설 페이지 전체 삽입은 즉시 FAIL이다.

## 실행 게이트

OCR 전에 다음 명령이 exit 0이어야 한다.

```powershell
python tools/math_content_scope_preflight.py content-scope.json `
  --json content-scope-qa.json
```

그 다음 OCR provenance도 동일한 scope manifest를 참조해야 한다.

```powershell
python tools/ocr_hybrid_preflight.py ocr-provenance.json `
  --scope-manifest content-scope.json `
  --source-is-copyrighted `
  --json ocr-provenance-qa.json
```

scope manifest의 canonical SHA-256과 OCR provenance의
`content_scope_manifest_sha256`이 다르거나 OCR region ID 집합이 다르면 FAIL이다.

## 필수 실패 조건

- 사용자 지정 시작 쪽보다 앞선 문제 영역 OCR
- 표지·목차·계획표·개념·광고 영역 OCR 유입
- 혼합 페이지의 포함/제외 영역 미분리
- 다단 읽기 순서 미검수
- 포함된 문제/해설 영역 누락 또는 중복 소유
- 문제는 있으나 해설 영역이 없는 문항
- 번호만 사용한 대응
- 페이지당 문항·미주 하나 가정
- 페이지 순번/나머지 연산을 사용한 해설 연결
- 문제 item 수, 해설 item 수, 미주 수 불일치

## 회귀 테스트 원칙

합성 fixture로 다음 사례를 유지한다.

- 앞부분 제외 쪽이 OCR 입력에 섞이면 FAIL
- 개념+문제 혼합 페이지에서 개념 영역이 섞이면 FAIL
- 한 페이지의 두 문제는 미주 두 개로 계산
- 여러 페이지로 이어지는 한 해설은 한 문항으로 계산
- 해설 continuation 영역 누락은 FAIL
- 중복 reading order는 FAIL
- page-round-robin 미주는 FAIL

실제 PDF, OCR 전사, HWP/HWPX, 원문 그림은 저장소에 넣지 않는다.
