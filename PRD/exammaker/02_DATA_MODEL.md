# ExamMaker — 데이터 모델

> 기존 ExamPool SQLite(`data/exam_pool.db`) 스키마를 **파괴 없이 확장**한다.
> 기존 테이블: subject / standard / unit / objective / proposition / false_variant / evidence / question / choice / exam_set / set_item / document / document_page / lesson / exam_ref

---

## 전체 구조 (변경 부분만)

```
[exam_set] --1:N--> [set_item] --0:1--> [question] --1:N--> [choice]
 (+계획 상태)        (+청사진 슬롯 필드)      |
                                          └--N:M--> [evidence] (기존)
```

핵심 아이디어: **"청사진 슬롯"은 아직 문항이 없는 set_item이다.**
계획 단계에서 set_item을 먼저 만들고(question_id 비움), Claude가 문항을 생성하면 같은 행에 연결한다. 계획 → 생성 → 확정이 별도 테이블 이동 없이 한 행의 생애주기로 흐른다.

> ⚠️ 가정(A2): 별도 blueprint 테이블 대신 exam_set/set_item additive 확장 — 아니라면 알려주세요.

---

## 엔티티 상세 (추가 컬럼)

### exam_set (기존 테이블에 추가)
세트가 "계획 중"인지 "확정"인지 구분한다.

| 필드 | 설명 | 예시 | 필수 |
|------|------|------|------|
| status | `planning` / `generated` / `confirmed` | planning | O (기본 planning) |
| short_code | 파일명 규약용 세트 약칭 | 26-1기말 | O |
| total_points_target | 목표 총점 (합계 검증용) | 100 | X |

### set_item (기존 테이블에 추가 — 청사진 슬롯)
슬롯 하나 = 문항 하나의 "주문서".

| 필드 | 설명 | 예시 | 필수 |
|------|------|------|------|
| question_id | 생성된 문항 연결 (계획 단계엔 NULL) | 42 | X |
| plan_qtype | 유형 | 합답형 / 정답형 / 서술형 | O |
| plan_standard_code | 성취기준 | [9과10-01] | O |
| plan_topic | 주제 키워드 | 단진자 에너지 보존 | X |
| plan_is_negative | 부정발문 여부 | 0/1 | O (기본 0) |
| plan_points | 배점 | 4 | O |
| plan_difficulty | 난이도 | 중 | X |
| plan_needs_figure | 그림 필요 여부 | 0/1 | O (기본 0) |
| plan_figure_hint | 그림 지시 (5E에 전달) | 빗면 위 물체, 높이 h 표시 | X |
| plan_situation | 상황 묘사 (사용자가 쓰는 서술) | 용수철에 매단 추를 당겼다 놓는다 | X |
| slot_status | `empty` / `generated` / `reviewed` | empty | O |

- 파일명 규약(가정 A3): 그림 파일명 = `{exam_set.short_code}_{번호2자리}` (예: `26-1기말_03`). 이 문자열이 question.material, 5E 페이지 이름, PNG 파일명에 동일하게 쓰인다. 두 번째 그림은 `_b` 접미(`26-1기말_03_b`).

### question / choice / evidence (변경 없음)
- Claude가 신규 쓰기 MCP로 행을 만든다. `material` 컬럼에 규약 파일명 기록 → export 시 `\파일이름\` 출력 (기존 `_photos()` 로직 그대로).
- 근거 연결은 기존 `add_evidence` 흐름 유지 — **evidence 없는 참 명제로 문항을 만들지 않는다**는 기존 규칙이 그대로 품질 게이트가 된다.

---

## 신규 MCP 툴이 만지는 데이터

| 툴 (신규) | 하는 일 |
|-----------|---------|
| `create_question` | proposition/false_variant를 재료로 question+choice 행 생성, material 기록 |
| `update_question` | 발문·선지·material·배점 수정 (status 확정 전까지만) |
| `attach_to_set` | question을 set_item 슬롯에 연결, slot_status 갱신 |
| `get_blueprint` | 세트의 슬롯 목록 + 계획 필드 일괄 조회 (지시문과 대조용) |

5E 쪽 신규 `save_image({path?, pageName?, dpi?})`: 현재 페이지(또는 지정 페이지)를 PNG로 지정 경로에 저장. 기본 경로는 설정된 사진 폴더.

---

## 왜 이 구조인가

- **재사용 최대**: question·choice·evidence·export_palette가 전부 그대로 동작한다. 새로 짓는 건 "계획 필드"와 "쓰기 툴"뿐.
- **확장성**: Phase 2 검토 리포트는 slot_status·evidence 조인만으로 만들 수 있고, Phase 3 재생성(리롤)은 slot의 plan_* 필드를 다시 읽으면 된다.
- **단순성**: 별도 blueprint 테이블을 만들면 "계획→세트 변환" 동기화 코드가 필요해진다. additive 컬럼이면 마이그레이션은 `ALTER TABLE ... ADD COLUMN` 몇 줄 + 기존 행 기본값으로 끝난다.
- **안전성**: 기존 컬럼을 바꾸지 않으므로 ExamPool 단독 사용 경로(웹 UI 수동 조립)가 깨질 수 없다.

---

## [NEEDS CLARIFICATION]

- [ ] A2: additive 확장 vs 별도 blueprint 테이블 (Turn 4 확인)
- [ ] A3: 파일명 규약 문자열 형태 (Turn 4 확인)
- [ ] set_item에 이미 있는 컬럼과의 충돌 여부는 구현 직전 `app/db.py` 실사로 확정 (컬럼명이 겹치면 plan_ 접두로 회피)
