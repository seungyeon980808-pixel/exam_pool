# ExamPool — 프로젝트 규칙 (CLAUDE.md)

## 그림(문항 자료 이미지) 규칙 — 절대 준수

문항 그림을 그리는 모든 작업(5E MCP 사용 포함)에서:

1. **그리기 전에 반드시 `PRD/07_KICE_FIGURE_STYLE.md`(평가원 도해 스타일 가이드)를 읽는다.**
   대원칙(무채색·선 굵기 2단 위계·물체 추상화)과 해당 유형의 문법을 적용한다.
2. **실물 예시가 필요하면 `assets/kice_figures/`를 참조한다.**
   `assets/kice_figures/figures.json`에서 `type` 필드로 유형(광선도·그래프·역학상황도 등)을
   필터해 해당 PNG를 열어 보고 구도를 따른다.
3. 5E로 그릴 때는 `describe_schema`로 객체 스키마를 확인하고, 스타일 가이드 8장의
   유형별 5E 매핑을 따른다. 단위는 mm, 원점은 아트보드 중앙.
4. 중학교 문항이므로 가이드 9장(적용 노트)을 따른다: 문법은 그대로, 정보량·수식 수준은 낮춘다.

## ExamPool MCP 규칙

- `create_pool_item`의 `standard_code`는 **대괄호 포함** 형식: `[9과10-01]`
- 참 명제는 반드시 `search_evidence`로 찾은 근거가 있을 때만 만들고, `add_evidence`로 인용을 붙인다.
- 문항·세트 검토 시 `check_question_rules` 결과를 함께 참고한다.

## 경로

- 기출 PDF 원본: `PDF/` (git 제외)
- 추출된 기출 그림: `assets/kice_figures/` (git 제외)
- 그림 추출 스크립트: `tools/extract_figures.py`
