# 인수인계서 — 5E 도구 확충 작업 (2026-07-26)

> 다음 세션이 **이 문서만 읽고** 이어서 일할 수 있도록 쓴 것.
> 진행 중인 일: **평가원 시험 그림을 5E로 제대로 그리기 위해, 5E에 없는 부품을 도구로 추가**하는 작업.

---

## 0. 30초 요약

- 기출 그림 617장을 추출·분류하고, 스타일 가이드 → 5E 기능 명세 → 레시피 → **객체 갭 목록**까지 문서화가 끝났다.
- 갭 목록에 따라 도구를 하나씩 추가 중이다. **용수철·천장 도르래·스위치**를 넣었고, `fillStyle`을 확장했다.
- **지금 대기 중인 것: 아래 §4의 미해결 3건 + 시안 20종 승인.**

---

## 1. 저장소 두 개

| 경로 | 역할 | 원격 |
|---|---|---|
| `C:\ExamPool` | 문서(PRD)·기출 그림·추출 스크립트. **작업 세션의 cwd** | `exam_pool` |
| `C:\5E` | **5E 앱 본체 + MCP 서버**. 도구 추가는 전부 여기 | `5E` |

두 저장소 모두 **커밋·푸시 완료 상태**로 넘긴다.

## 2. 반드시 먼저 읽을 문서 (`32_exam_pool/PRD/`)

| 문서 | 내용 |
|---|---|
| `07_KICE_FIGURE_STYLE.md` | 평가원 도해 문법 (기출 617장 전수 분석) — **무엇을 그릴지** |
| `08_5E_CAPABILITIES.md` | 5E 기능 명세 + **"소스 코드로 확정한 렌더 특성"** — 무엇으로 그릴 수 있는지 |
| `09_5E_RECIPES.md` | 문법→기능 레시피. `[검증됨]` 블록은 복제해 쓴다 + **실패 10건 체크리스트** |
| `10_5E_OBJECT_GAPS.md` | **부품 인벤토리·갭 목록**(371장 조사, 174종). 다음에 뭘 만들지는 여기서 고른다 |
| `CLAUDE.md`(프로젝트 루트) | 그림 작업 절대 규칙. 매 세션 자동 로드 |

## 3. 지금까지 추가·수정한 것 (5E 저장소)

**새 도구**
- `spring` (용수철) — p1/p2 파라미터 타입. `turns`·`radius`·`leadLength`·`springStyle`. 끝점 스냅.
  파일: `js/render/spring.js`, `js/inspector/section-spring.js` + 배선 12곳
- `apparatus.pulley`의 `variant: ceiling | wall` — 고정판+브래킷+홈 바퀴. 바퀴 **좌우 접선점 스냅 앵커**.
  파일: `js/render/optics-apparatus.js`(`drawMountedPulley`, `pulleyGeom`, `pulleyAnchors`)
- `circuit`의 `switch`(SPST, `closed`) / `switch_spdt`(`throwTo: "a"|"b"`) — 파일: `js/render/circuit.js`
- `fillStyle` 확장: `fillTile`(기호 간격 mm), `fillDotStyle: "ring"`(⊙) — 파일: `js/render/fill.js`
  ※ **자기장 ⊙/× 격자는 새 객체가 아니라 이 fillStyle이 담당한다**(이미 있던 기능)

**MCP 서버 개선** (`tools/mcp-5e/`)
- `set_page`/`list_pages` + `add_objects`/`add_graph`의 `page`로 **탭 자동 생성·전환**
- `add_graph`가 `graphCfg`·`richLabels`·`annGuides`·`guideLines`·`cellMmX/Y` 전달
- `read_app`이 `planeId`·`guides`·`markers`·`groupId`·`seriesLock` 보고(검증용)

**앱 버그 수정** (전부 이 작업 중 발견)
- 라벨 뒤 녹아웃(글자 사이로 선 비침), 그래프 눈금 숫자 굵기, 편집 모달 미리보기 비율,
  `tickStep` 미저장, MCP 자동 재연결

## 4. ⛔ 미해결 — 다음 세션이 할 일

### (1) 천장 도르래가 앱에 제대로 안 들어감 — **최우선**
- 렌더러 자체는 맞다. `_repro/pulley-check.html`에서 SVG를 읽어 확인함
  (ceiling = 브래킷 2 + 고정판 + 원 3개, **팔(polygon) 없음**).
- 그런데 **앱에서 팔레트로 넣으면 옛 모양(팔+볼트)이 나온다**는 사용자 보고.
- 의심 지점: `js/templates.js`의 `pulley_ceiling` 항목이
  `create:{tool:"APPARATUS", kind:"pulley", props:{variant:"ceiling"}}` 인데,
  `armSymbol(symbolId, tool, variant, props)` → `tools.js`의 `_symbolProps` →
  apparatus 생성부 `Object.assign(shape, _symbolProps)` 경로가 실제로 도는지 **미검증**.
- **검증 방법**: 앱을 하드 리프레시 후 팔레트에서 '천장 도르래'를 놓고 `read_app`으로
  객체의 `variant` 값을 확인할 것. `basic`이면 props 경로가 끊긴 것.

### (2) 용수철 디자인 재작업
- 사용자 요구: **훨씬 더 촘촘하게 꼬불꼬불**, **약간 옆에서 본 느낌**의 감긴 코일.
  (참고 이미지: 양끝 짧은 직선 + 고리가 빽빽하게 겹친 전형적인 코일 스프링)
- **사인·지그재그 옵션은 삭제**한다. helix 하나만 남긴다.
- 현재 구현의 한계: 고리가 시작점 밖으로 나가지 않게 기울기 보정 `k ≤ pitch×0.20`으로
  묶어 놓아 **너무 납작하다**. 참고 그림처럼 하려면 고리가 크게 겹쳐야 하므로
  **접근을 바꿀 것** — 굽이마다 타원 호를 겹쳐 그리는 방식(슬링키 표현)을 권함.
  넘치는 부분은 양끝 직선부(lead)가 가려주므로 `[0, coilLen]` 제약은 완화해도 된다.
- 파일: `js/render/spring.js`의 `coilPathD()`

### (3) 물결 화살표 — 끝 테이퍼 제거
- 내가 넣은 "끝 15% 진폭 감소"는 **오류**다. 진폭은 **끝까지 일정**해야 한다.
- 대신 **정수 파장**으로 끝내 마지막 점이 축 위(s=0)에 오게 하고, 화살촉을 축 방향으로 붙인다.
- 아직 앱에 넣지 않았다(시안 단계). `line`의 `lineMode`에 `wavyArrow`로 추가할 예정.

### (4) 시안 20종 승인 대기
- 페이지: `51_5E/5E_main/_repro/tools-draft-20.html` (아래 §5 방법으로 열람)
- 목록: 매질 층 스택+자동 법선 / 반원 매질 / 열역학 상태 경로 / 파면(동심원·평면파) /
  솔레노이드 / 실험 스탠드 / 기체 분자 / 마찰 구간 띠 / 곡선 트랙 / 점전하 /
  축 생략 ≈ / 블록 화살표 / 스펙트럼 띠 / 에너지띠 / 확대 인셋 / 필드라인 /
  실+도르래 감김 / 전자저울 / 표 객체
- **에너지 준위 라벨 겹침**은 두 안(①좁은 준위 라벨 생략 ②지시선으로 빼내기) 중 선택 대기.

## 5. 작업 환경 — 이것 모르면 헛수고한다

**반영 시점**
- **앱 소스(`js/`) 수정 → 브라우저 하드 리프레시(Ctrl+F5)**. 모듈이 `?v=1.2.0`으로 캐시된다.
- **MCP 서버(`tools/mcp-5e/`) 수정 → Claude Code 재시작.** 세션 시작 때 뜬 프로세스다.
- 헷갈리면 파일 모드(`create_project`+`add_graph`)로 결과 JSON을 열어 **서버 버전을 먼저 확인**.

**앱·시안 페이지 띄우기**
- `32_exam_pool/.claude/launch.json`에 `5e-app`(포트 8190) 항목이 있다. `preview_start`로 띄운다.
  (Bash로 서버를 직접 돌리지 말 것)
- 5E 앱: `http://localhost:8190/`
- 시안 페이지: `http://localhost:8190/_repro/tools-draft-20.html`, `.../pulley-check.html`

**렌더 확인 방법 (중요)**
- 브라우저 패널이 닫혀 있으면 스크린샷이 안 된다. 대신 **`javascript_tool`로 생성된 SVG 노드를
  직접 읽어** 확인한다. 이 방법으로 "팔(polygon)이 있나 없나" 같은 판정을 정확히 할 수 있다.
- `_repro/*.html`은 앱 모듈을 **캐시 우회(`?v=Date.now()`)로 import** 하므로 앱을 새로고침하지
  않아도 최신 렌더러 결과를 본다.

**5E MCP**
- 앱이 연결돼 있어야 그린다(`app_status`로 확인, 배지 '5E MCP 연결됨').
- 연결이 끊기면 앱이 자동 재연결한다(2초→최대 15초 백오프).

## 6. 일하는 방식 (사용자가 요구한 절차)

1. 갭 목록(`PRD/10`)에서 만들 부품을 고른다
2. **시안을 먼저 뽑아 사용자 승인**을 받는다 (`_repro/*.html`에 그려서 링크 제공)
3. 승인된 것만 앱에 정식 편입 — 렌더러 + 배선 + 팔레트 + 인스펙터 + MCP 스키마
4. `_repro` 페이지에서 **SVG를 읽어 자가 검증**한 뒤 사용자 확인을 받는다
5. 통과하면 `PRD/09`에 `[검증됨]` 레시피로 박제, `PRD/10` 진행 상황 갱신

**절대 규칙**: 앱에 기능이 있으면 반드시 그 기능을 쓴다(원시 도형으로 흉내 금지).
렌더가 이상하면 추측하지 말고 **5E 소스를 읽는다** — 5E는 사용자 본인 프로그램이라 앱 버그일 수 있다.

## 7. 자주 밟은 지뢰

- `pulley`라는 이름이 **`optics`와 `apparatus` 두 곳**에 있다. 실제 쓰이는 건 `apparatus`의 `drawPulley`.
- `drawPulley`는 `variant !== "simple"`이면 무조건 팔+볼트를 그린다 → 새 variant를 넣을 땐 분기 먼저.
- 라벨 필드 이름이 제각각: 치수선은 `dimensionLabel`, 그래프 끝은 `endLabel`, 축은 `labelX/labelY`.
- 그래프 글자 크기는 **칸(cell) 크기에 비례**한다. `w/h`를 직접 주면 글자가 거대해진다 → `cellMm` 사용.
- `funcgraph.points`는 **세계 좌표(mm)**다. 평면 좌표가 아니다.
