# ExamMaker 파이프라인 운영 규약

> 청사진 작성 → AI 생성(텍스트+그림) → 검토 → HWP 조판의 전 구간 규칙.
> 기획 원문: `PRD/exammaker/`. 그림 스타일·5E 사용법은 프로젝트 CLAUDE.md 의 문서 순서를 따른다.

---

## 1. 파일명 규약 (그림) — 세 곳이 같은 문자열을 쓴다

```
{세트 short_code}_{문항 ord 2자리}        예: 26-1기말_03
두 번째 그림은 _b 접미                     예: 26-1기말_03_b
```

| 위치 | 값 |
|------|-----|
| ExamPool `question.material` | `26-1기말_03` (쉼표로 2개까지: `26-1기말_03, 26-1기말_03_b`) |
| 5E 페이지 이름 | `26-1기말_03` (그림 1장 = 페이지 1장) |
| PNG 파일명 | `26-1기말_03.png` |

- `short_code` 는 세트 생성 시 확정한다. **공백·`&`·`\` 금지** (hwpPalette 문법 기호와 충돌).
- MCP `get_blueprint` 가 슬롯마다 `figure_name` 으로 계산해 주므로, 그 값을 그대로 쓴다.

## 2. 그림 파일 규칙

- **PNG 300dpi 고정. SVG 금지** — hwpPalette 가 SVG 를 못 읽는다(`[사진 실패]`로 떨어짐).
  5E 는 PNG 에 dpi(pHYs)를 직접 써 주므로 한글에서 크기가 맞는다.
- 저장 위치: 사진 루트 아래 **세트별 폴더** (예: `사진\26-1기말\`).
  폴더를 만들면 hwpPalette 설정의 `photo_dirs` 에 등록한다 (등록 순서 = 탐색 우선순위).
- 5E 표준 6단계 준수: `app_status` → `describe_schema` → `set_page` → `add_*` →
  `export_image`(자가검증) → 필요 시 `remove_objects` 후 재시도(2회 이내).

## 3. 세션 규칙

- **작업 세션은 반드시 `32_exam_pool` 폴더에서 시작한다.**
  exampool MCP 는 프로젝트 스코프(`.mcp.json`)라 이 폴더에서만 붙고, 5E MCP 는 전역이라 함께 잡힌다.
- **MCP 서버 코드를 수정하면 Claude Code 재시작** (`app/mcp_server.py`, 5E `tools/mcp-5e/server.js`).
  앱 소스 수정은 하드 리프레시(Ctrl+F5)로 충분.
- Claude 는 5E 프로젝트 파일·HWP 파일을 저장/덮어쓰지 않는다 — **저장은 사용자 손으로**.

## 4. 문항 생성 규칙 (쓰기 MCP)

- 참 명제는 `search_evidence` 근거가 있을 때만. 선지는 가급적 `proposition_id`/`variant_id` 로
  Pool 과 연결해 검토 화면에서 근거 추적이 되게 한다.
- `create_question` 은 origin='AI초안' 으로 저장된다. status 가 '완성'인 문항은 MCP 로 수정 불가.
- 슬롯 연결은 `attach_to_set` — 이미 찬 슬롯은 덮어쓰지 않는다(교체는 화면에서).

## 5. 표준 작업 순서 (한 세트)

1. 화면에서 세트 + 청사진 슬롯 작성 → "출제 지시문 복사"
2. Claude Code 에 붙여넣기 → `get_blueprint` 로 슬롯 확인
3. 슬롯별: 근거 검색 → 명제/변형 → `create_question` → `attach_to_set`
   → 그림 필요 시 5E 로 그리고 `save_image` (파일명 = `figure_name`)
4. 화면에서 검토 (근거·정답·그림) — 수정은 화면 직접 또는 Claude 재지시
5. 세트 확정 → export → hwpPalette CLI 로 조판 → 한글에서 최종 확인·저장
