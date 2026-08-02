# ExamPool MCP 서버

Claude Code(·Desktop)에서 ExamPool의 **명제·근거·문항**을 직접 다룬다.
앱 화면에 AI 버튼을 다는 대신, 이미 쓰는 Claude Code가 ExamPool 데이터를 도구로 호출한다.

> **이 앱에는 LLM도 API 키도 들어가지 않는다.** 모델은 클라이언트(Claude Code)가 댄다.
> 앱 본체는 그대로 완전 오프라인이고, MCP는 별도 진입점(`python -m app.mcp_server`)이다.

---

## 무엇에 쓰나

**① 참 명제 생성** — 교과서 근거를 깔고 참 명제를 만들어 Pool에 채운다
> Claude Code에서: *"9과10-02 성취기준으로 참 명제 5개 만들어서 Pool에 넣어줘. 근거도 붙여."*
> → `get_standard`(성취기준 확인) → `search_evidence`(교과서 원문) → `create_pool_item` + `add_evidence`(저장·인용)
> → **ExamPool 화면을 새로고침**해 참/거짓 확인 후 확정

**② 문항 검토** — 조립한 문항의 의미 문제를 짚는다
> Claude Code에서: *"3번 세트 검토해줘."*
> → `get_exam_set` / `get_question_detail`로 읽고, 보기 중복·발문 모호·정답 복수·성취기준 불일치를 지적
> (`check_question_rules`로 규칙 검토도 함께 참고)

생성·검토는 **Claude Code 대화창**에서 일어나고, 결과물은 ExamPool DB로 흘러들어가 화면에 나타난다.

---

## 붙이기 (Claude Code)

프로젝트에 `.mcp.json`이 들어 있어, **이 폴더에서 `claude`를 실행하면** Claude Code가
프로젝트 MCP 서버 승인 여부를 묻는다. 승인하면 끝.

```bash
cd C:\Users\user\Desktop\project\32_exam_pool
claude
```

- 확인: 세션에서 `/mcp` → `exampool` 서버와 도구 13개가 보이면 연결된 것.
- FastAPI 앱(`run.bat`)이 떠 있지 않아도 된다. 같은 `data/exam_pool.db`를 공유한다.
- 사전 준비: `pip install -r requirements.txt` (mcp 패키지 포함).

## 붙이기 (Claude Desktop, 선택)

`claude_desktop_config.json`에 아래를 추가한다. Code와 달리 실행 위치를 절대경로로 준다.

```json
{
  "mcpServers": {
    "exampool": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "C:\\Users\\user\\Desktop\\project\\32_exam_pool"
    }
  }
}
```

---

## 도구 목록 (13)

| 도구 | 하는 일 |
|---|---|
| `list_standards` / `get_standard` | 성취기준 조회 (해설·단원 유의사항 포함) |
| `search_evidence` | 교과서·교육과정·기출·수업 근거 검색 (FTS5) |
| `list_pool` / `get_pool_item` | 명제 Pool 조회 (중복 확인) |
| `create_pool_item` | 참 명제 저장 |
| `add_evidence` | 명제에 인용 근거 붙이기 |
| `add_false_variant` | 거짓 변형(오답 재료) 붙이기 |
| `list_question_bank` / `get_question_detail` | 문항 조회 (검토 입력) |
| `check_question_rules` | 규칙 기반 자동 검토 |
| `list_exam_sets` / `get_exam_set` | 세트 조회 (배점·난이도·커버리지 포함) |

---

## 주의

- **AI가 "참"이라 준 명제를 그대로 믿지 않는다.** 항상 인용 근거(`add_evidence`)를 남기게 하고,
  최종 참/거짓 판단은 교사가 ExamPool 화면에서 확인한다.
- MCP를 쓰면 명제·문항 맥락이 Claude(Anthropic)로 전송된다. 완전 오프라인이 필요한 작업은
  `run.bat`(앱 단독)만 쓰면 된다.
