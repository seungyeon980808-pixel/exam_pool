# ExamPool 대화형 문항 제작 개편 — 다음 Codex 세션 인수인계

작성일: 2026-08-06  
대상 저장소: `C:\ExamPool`
관찰된 현재 브랜치: `feature/exammaker`  
새 작업 브랜치 제안: `feat/exampool-conversational-authoring`

## 1. 제품 결정사항

ExamPool을 전체 프로그램의 유일한 메인 프론트로 사용한다. 별도의 통합 프로그램이나 새 프론트를 만들지 않는다.

최종 사용자 흐름은 다음과 같다.

1. ExamPool에서 Codex와 대화하며 문항을 작성한다.
2. 문제 본문, 선택지, 정답, 해설을 구조화된 필드로 확정한다.
3. 확정된 문항을 바탕으로 그림 생성을 요청한다.
4. 생성된 그림을 5E에서 충분히 수정한다.
5. 그림을 확정하고 5E 편집 원본과 출력 이미지를 문항에 연결한다.
6. 완성된 텍스트와 그림을 하나의 문항으로 저장한다.
7. 문항은행에서 문항을 골라 시험지 세트를 구성한다.
8. hwpPalette 엔진으로 HWP 시험지를 출력한다.

현재 다른 Codex 세션에서는 `평가원 스타일 그림 변환 엔진`과 그림 생성 명세를 별도로 고도화한다. 이 브랜치에서는 그 엔진을 구현하거나 변경하지 않고, 나중에 연결할 `FigureProvider` 경계만 준비한다.

## 2. 이번 브랜치의 범위

이번 브랜치는 `대화로 문항 하나를 만들고 확정하여 저장하는 세로 흐름`만 완성한다.

포함:

- ExamPool 문항 편집 화면의 Codex 대화 패널
- 사용자별 로컬 Codex 연결 상태와 로그인 진입점
- 문항별 독립 대화 스레드
- Codex 응답에서 문항 필드로 선택적 반영
- 문제 본문, 선택지, 정답, 해설의 구조화 편집
- 텍스트 확정 상태
- 대화와 문항 초안의 로컬 저장 및 복구
- 반영 전후 되돌리기
- 후속 5E 연동을 위한 `FigureProvider` 인터페이스
- 후속 hwpPalette 연동을 위한 출력 어댑터 경계

제외:

- 평가원 스타일 그림 변환 엔진 구현
- 실제 5E 임베딩과 자동 그림 생성
- 실제 hwpPalette HWP 출력
- 기존 ExamPool UI의 전면 재설계
- 클라우드 계정, 협업, 결제, OpenAI API

## 3. 중요한 기술 결정

- OpenAI API와 API 키를 사용하지 않는다.
- 종량제 과금을 사용하지 않는다.
- 사용자가 자신의 ChatGPT/Codex 계정으로 로그인하는 로컬 단일 사용자 구조다.
- Codex SDK 또는 Codex App Server의 ChatGPT-managed 로그인 경로를 검토한다.
- OpenAI 비밀번호나 인증 토큰을 ExamPool 렌더러와 문항 데이터에 저장하지 않는다.
- AI 실행부를 UI에서 분리하고 `CodexLocalProvider`와 `MockProvider`가 같은 계약을 구현하게 한다.
- 대화 기록과 확정 문항 데이터는 분리한다.
- Codex 응답은 문항을 자동 덮어쓰지 않는다. 사용자가 제안별 `반영`을 눌러야 한다.
- 기존 데이터 스키마는 파괴적으로 변경하지 않는다. 신규 필드는 기본값과 마이그레이션을 제공한다.
- 기존 ExamPool 기능과 저장 데이터를 우선 보존한다.

## 4. 제안 화면 구조

메인 화면은 기존 ExamPool 디자인 시스템을 유지하면서 다음 세 영역으로 구성한다.

1. **Codex 대화**
   - 문항 생성·수정 요청
   - 스트리밍 응답
   - 제안별 반영 버튼
   - 문항별 대화 기록
2. **현재 문항**
   - 문제 본문
   - 선택지
   - 정답
   - 해설
   - 텍스트 확정
3. **문항 그림**
   - 그림 생성
   - 5E에서 편집
   - 그림 확정
   - 이번 브랜치에서는 실제 연동 대신 비활성 진입점 또는 어댑터 스텁만 둔다.

참고 시안: `docs/handoff/assets/exampool-conversational-authoring-v1.png`

시안은 배치와 사용자 흐름의 참고 자료다. 기존 ExamPool의 시각 체계와 컴포넌트를 우선한다.

## 5. 문항 상태

문항은 최소 다음 상태를 가진다.

- `text_drafting`: 텍스트 작성 중
- `text_confirmed`: 텍스트 확정
- `figure_drafting`: 그림 제작 중
- `figure_confirmed`: 그림 확정
- `reviewing`: 문항 검수
- `saved`: 저장 완료

텍스트 확정 후 본문·선택지·정답·해설이 바뀌면 정답·해설·그림 재검토 경고를 표시하고 상태를 적절히 되돌린다.

## 6. 최소 데이터 모델

기존 스키마를 먼저 조사한 뒤, 아래 개념을 기존 구조에 하위 호환 방식으로 매핑한다.

```json
{
  "question_id": "stable-id",
  "revision": 1,
  "authoring_status": "text_drafting",
  "content": {
    "stem": "",
    "choices": [],
    "answer": "",
    "explanation": ""
  },
  "conversation": {
    "provider": "codex-local",
    "thread_id": null,
    "messages": []
  },
  "figure": {
    "status": "none",
    "provider": null,
    "scene_spec_path": null,
    "fivee_project_path": null,
    "rendered_image_path": null
  }
}
```

이 예시를 그대로 도입하지 말고 현재 데이터베이스와 파일 형식을 확인한 뒤 최소 변경으로 적용한다.

## 7. Provider 경계 제안

```ts
interface AuthoringProvider {
  getConnectionState(): Promise<ConnectionState>;
  login(): Promise<void>;
  logout(): Promise<void>;
  startThread(context: QuestionContext): Promise<string>;
  resumeThread(threadId: string): Promise<void>;
  sendMessage(threadId: string, message: string): AsyncIterable<AuthoringEvent>;
}

interface FigureProvider {
  createDraft(request: FigureRequest): Promise<FigureDraft>;
  openEditor(draft: FigureDraft): Promise<FigureResult>;
}

interface DocumentExportProvider {
  exportExamSet(request: ExamSetExportRequest): Promise<ExportResult>;
}
```

프로젝트 언어와 기존 패턴에 맞춰 이름과 타입은 조정한다.

## 8. 수용 기준

1. 기존 ExamPool 기능과 기존 데이터가 정상적으로 유지된다.
2. 새 문항 또는 기존 문항에서 대화 패널을 열 수 있다.
3. MockProvider로도 전체 UI를 테스트할 수 있다.
4. 환경이 지원하면 Codex 로컬 로그인과 실제 대화를 연결한다.
5. 응답이 스트리밍되거나 진행 상태가 명확히 표시된다.
6. Codex 제안을 문제 본문·선택지·정답·해설에 선택적으로 반영할 수 있다.
7. AI 응답이 문항을 자동 덮어쓰지 않는다.
8. 반영 작업을 되돌릴 수 있다.
9. 문항별 대화와 작성 상태가 저장되고 앱 재실행 후 복원된다.
10. 텍스트 확정과 확정 해제가 동작한다.
11. 확정 후 텍스트 변경 시 재검토 경고가 나타난다.
12. 그림 영역에는 후속 연동 진입점이 보이지만 실제 5E 코드는 합치지 않는다.
13. 기존 테스트를 실행하고 신규 핵심 흐름 테스트를 추가한다.

## 9. 새 세션의 첫 작업 순서

1. 저장소의 `AGENTS.md`, `CLAUDE.md`, `README.md`, `HANDOFF.md`, `PRD/`, 데이터 스키마와 실행 방법을 완전히 읽는다.
2. `git status`와 현재 브랜치를 확인한다. 사용자 변경이 있으면 보존하고 덮어쓰지 않는다.
3. 현재 기준 브랜치가 의도된 베이스인지 확인한다. 2026-08-06 관찰 시점에는 `feature/exammaker`였고 작업 트리는 깨끗했다.
4. 베이스가 맞으면 `feat/exampool-conversational-authoring` 브랜치를 만든다.
5. 기존 문항 생성·편집·저장·세트 구성 구조를 분석한다.
6. 변경 파일, 보존할 기능, 데이터 마이그레이션 위험, 테스트 계획을 먼저 보고한다.
7. 사용자에게 별도 선택이 필요하지 않은 범위는 계속 구현한다.
8. 문항 하나의 `대화 → 선택적 반영 → 텍스트 확정 → 저장 → 재실행 복구` 흐름을 먼저 완성한다.
9. 실제 Codex 연동이 막히더라도 MockProvider 기반 UI와 저장 구조를 완성하고 정확한 블로커를 보고한다.

## 10. 다음 세션에 보낼 시작 지시문

```text
ExamPool을 대화형 문항 제작 프로그램으로 개편하십시오.

먼저 저장소의 모든 프로젝트 지침과
docs/handoff/START_NEXT_CODEX_SESSION.md를 완전히 읽으십시오.
첨부 시안 docs/handoff/assets/exampool-conversational-authoring-v1.png도 확인하십시오.

기존 프로그램과 데이터는 그대로 보존하고, 현재 기준 브랜치가 맞는지 확인한 뒤
feat/exampool-conversational-authoring 브랜치를 생성하십시오.

이번 브랜치에서는 대화로 문항 하나를 작성하고, Codex의 제안을 문제 본문·선택지·정답·해설에
선택적으로 반영하고, 텍스트를 확정하여 저장한 뒤 앱 재실행 후 복구하는 세로 흐름을 구현합니다.

OpenAI API와 API 키, 종량제는 사용하지 않습니다. 사용자 자신의 ChatGPT/Codex 로그인으로
작동하는 로컬 구조를 사용하며, AI 실행부는 provider 인터페이스로 분리하십시오.

5E와 hwpPalette의 실제 통합은 이번 범위가 아닙니다. FigureProvider와 출력 어댑터 경계만
준비하고 기존 코드를 물리적으로 합치지 마십시오.

현재 구조와 데이터 스키마를 먼저 분석하고, 변경 파일·위험·단계별 계획을 보고한 다음 구현하십시오.
기존 테스트를 실행하고 신규 핵심 흐름을 검증하십시오.
```

## 11. 후속 브랜치 제안

1. `feat/exampool-conversational-authoring`
2. `feat/exampool-figure-workflow`
3. `feat/exampool-exam-set-builder`
4. `feat/exampool-hwp-export`
5. `feat/exampool-desktop-packaging`
