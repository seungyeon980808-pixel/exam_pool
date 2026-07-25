# ExamPool — 프로젝트 규칙 (CLAUDE.md)

## 그림(문항 자료 이미지) 규칙 — 절대 준수

문항 그림을 그리는 모든 작업(5E MCP 사용 포함)에서, **그리기 전에 세 문서를 순서대로 읽는다**:

1. `PRD/07_KICE_FIGURE_STYLE.md` — 평가원 도해 스타일 가이드 (무엇을 그릴지)
2. `PRD/08_5E_CAPABILITIES.md` — 5E 기능 명세 (무엇으로 그릴 수 있는지: lineMode·labelType·fillLevel 등)
3. `PRD/09_5E_RECIPES.md` — 문법→기능 레시피 (`[검증됨]` 레시피는 그대로 복제, 수치만 변경)

그리고:

4. **실물 예시 참조**: `assets/kice_figures/figures.json`에서 `type`으로 필터해 같은 유형 기출 PNG를 열어 구도를 따른다.
5. **그린 뒤 자가 검증**: `read_app`으로 결과를 읽어 좌표 겹침·이탈을 확인하고, 사용자 화면 확인을 받기 전에는 완성 선언하지 않는다.
6. **앱에 기능이 있으면 무조건 그 기능을 쓴다.** 원시 도형(line·text)으로 흉내 내지 않는다.
   - 물리량 기호 → `formula` / 치수 → `lineMode:"lengthArrow"` + `dimensionLabel`
   - 광선 → `middleArrow` / 회색 → `fillLevel`
   - **그래프 곡선 → `add_graph`의 `functions[].expr`** (수동 좌표 계산 금지)
   - **수선의 발 → 평면 `annGuides`** / 임의 안내선 → 평면 `guideLines` / 눈금 숫자 → `showTickLabels`
7. **렌더 결과가 이상하면 추측하지 말고 5E 소스를 읽는다** — `C:\Users\user\Desktop\project\51_5E\5E_main\js`.
   5E는 사용자 본인 프로그램이라 앱 자체 버그일 수 있다. 버그면 5E 저장소에서 고친다.
8. **캔버스 = 편집 모달 미리보기 = 적용 결과**가 항상 같아야 한다. 다르면 버그로 보고한다.
9. 중학교 문항이므로 가이드 9장(적용 노트)을 따른다: 문법은 그대로, 정보량·수식 수준은 낮춘다.
10. 레시피가 실패하면 개별 그림에서 임기응변하지 말고 `PRD/09_5E_RECIPES.md`를 고친다.
11. 반영 시점: **앱 소스 수정 → 하드 리프레시(Ctrl+F5)**, **MCP 서버 수정 → Claude Code 재시작**.
    헷갈리면 파일 모드로 결과 JSON을 열어 서버 버전을 먼저 확인한다.

## ExamPool MCP 규칙

- `create_pool_item`의 `standard_code`는 **대괄호 포함** 형식: `[9과10-01]`
- 참 명제는 반드시 `search_evidence`로 찾은 근거가 있을 때만 만들고, `add_evidence`로 인용을 붙인다.
- 문항·세트 검토 시 `check_question_rules` 결과를 함께 참고한다.

## 경로

- 기출 PDF 원본: `PDF/` (git 제외)
- 추출된 기출 그림: `assets/kice_figures/` (git 제외)
- 그림 추출 스크립트: `tools/extract_figures.py`
