# ExamMaker — 디자인 문서

> Show Me The PRD로 생성됨 (2026-08-03)
> ExamPool(32_exam_pool)을 "출제 지휘소"로 확장하는 기획. 기존 `PRD/01~10`은 ExamPool 본체 문서.

## 문서 구성

| 문서 | 내용 | 언제 읽나 |
|------|------|----------|
| [01_PRD.md](./01_PRD.md) | 뭘 만드는지, 목표 흐름, 가정 원장 | 프로젝트 시작 전 |
| [02_DATA_MODEL.md](./02_DATA_MODEL.md) | exam_set/set_item 확장, 신규 MCP 툴 | DB·MCP 작업 전 |
| [03_PHASES.md](./03_PHASES.md) | Phase 1~3, 저장소별 작업 목록 | 개발 순서 정할 때 |
| [04_PROJECT_SPEC.md](./04_PROJECT_SPEC.md) | AI 행동 규칙 (3개 저장소 공통) | 구현 세션마다 |

## 핵심 결정 (확정)

- MVP = 갭 봉합 4종 + 세트 청사진 화면 / ExamPool 확장(신규 앱 없음) / AI 엔진 = Claude Code 세션 / HWP = CLI 반자동

## 다음 단계

Phase 1을 시작하려면 [03_PHASES.md](./03_PHASES.md)의 "Phase 1 시작 프롬프트"를 복사해 새 세션에서 실행.

## 미결 사항

01_PRD.md 말미의 **가정 원장(A1~A10)** 참조 — 특히 A2(스키마 확장 방식)·A3(파일명 규약)은 구현 전 확인 필요.
