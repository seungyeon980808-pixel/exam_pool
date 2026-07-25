# 평가원 문법 → 5E 레시피

> 스타일 가이드(07)의 규칙을 5E 기능 명세(08)의 실제 파라미터로 구현하는 방법.
> `[검증됨]`은 5E 화면에서 교사 승인을 받은 것 — **그대로 복제하고 수치만 바꾼다.**
> 표시가 없으면 초안이므로 그린 뒤 반드시 `read_app`으로 확인한다.

---

## 0. 작업 순서 (모든 그림 공통)

1. `PRD/07_KICE_FIGURE_STYLE.md` — 해당 유형의 평가원 문법 확인
2. `PRD/08_5E_CAPABILITIES.md` — **특히 "소스 코드로 확정한 렌더 특성" 절을 반드시 읽는다**
3. 이 문서에서 `[검증됨]` 레시피가 있으면 **그대로 복제**
4. `assets/kice_figures/figures.json`에서 같은 `type` 기출 PNG를 열어 구도 확인
5. `set_artboard` → `add_graph`/`add_objects`
6. `read_app`으로 자가 검증 (좌표·묶임·내장 요소 개수)
7. 교사 화면 확인 요청 — **스스로 "완성" 선언 금지**

## 0-A. 절대 규칙 5가지 (어기면 반드시 재작업이 난다)

1. **앱에 기능이 있으면 무조건 그 기능을 쓴다.** 원시 도형(line·text)으로 흉내 내지 않는다.
   특히 그래프 영역: 곡선·수선의 발·표시점·화살표·눈금 숫자는 전부 내장 기능이 있다.
2. **추측하지 말고 소스를 읽는다.** 5E는 사용자 본인 프로그램이다(`51_5E/5E_main/js`).
   렌더 결과가 이상하면 `describe_schema`가 아니라 **렌더러 소스**에서 필드명을 확인한다.
   (`dimensionLabel`, `richLabels`, `annGuides` 전부 소스를 읽고서야 찾았다.)
3. **캔버스 = 편집 모달 미리보기 = 적용 결과**가 항상 성립해야 한다. 하나라도 다르면 버그다.
4. **레시피가 실패하면 개별 그림에서 임기응변하지 말고 이 문서를 고친다.** 재현성이 목적이다.
5. 그린 뒤 `read_app`으로 **스스로 검증한 다음** 교사에게 확인을 요청한다.

---

## 1. 규칙 → 파라미터 대응표

| 평가원 규칙 (07) | 5E 구현 |
|---|---|
| 선 굵기 2단 위계 | 본체 strokeWidth 0.45~0.6 / 보조 0.2~0.25 |
| 법선·기준선 = 가는 파선 | line, strokeWidth 0.2, dashLength 1.2, dashGap 0.8 |
| 이전/가상 위치 = 파선 윤곽 | 같은 도형 반복 + dash + fillNone:true (**svgAsset은 파선 불가**) |
| 치수(거리) 표기 | line, `lineMode:"lengthArrow"` + **`dimensionLabel`**(label 아님!) + `dimensionLabelSize` |
| 광선(경로 중간 화살촉) | line, `lineMode:"middleArrow"` |
| 힘·속도 화살표 | line, `lineMode:"arrow"` (힘 0.6 / 속도 0.35) |
| 양방향 화살표 | `arrowHead:"both"` |
| 물리량 기호(이탤릭 수식체) | **formula** — text 금지 |
| 한글 라벨(고딕) / 이름(정체) | text / `labelType:"label"` |
| ㉠㉡ 지시선 | labeler (p1=대상, p2=글자) |
| 각도 호 θ / 직각 | anglearc(`label:"theta_1"`) / rightangle |
| 회색 명도(매질·벽·마찰) | `fillLevel` 220/190/160, `fillStyle` 항상 "solid"(해칭 금지) |
| 수레·도르래 | svgAsset(cart/pulley) — **비율 필수**, optics, apparatus |
| **그래프 데이터 곡선** | **add_graph의 `functions[].expr`** (앱 샘플러) |
| **수선의 발** | **plane `annGuides:[{x,y}]`** (③표시 탭과 같은 데이터) |
| **불연속 연결·임의 안내선** | **plane `guideLines:[{x1,y1,x2,y2}]`** (축까지 안 내려감) |
| 표시점 ● / 화살촉 | plane `annMarkers` / `annArrows` |
| 눈금 숫자 | `showTickLabels:true` — **수동 text 금지**(위치가 어긋난다) |
| (가)(나) 캡션 | text, 각 부분 그림 하단 중앙 |

---

## 2. 복합 레시피 [검증됨] — 상황도(다중섬광) + v-t 그래프

> 2026-07-25 교사 승인. **이 블록을 통째로 복제하고 수치만 바꾼다.**
> 예시 상황: 자동차가 0~1초에 3 m, 1~2초에 4.5 m 이동.

### ① 아트보드

```
set_artboard  w:130  h:60      # 좌 상황도 / 우 그래프 2패널
```

### ② 그래프 — add_graph 한 번으로 (곡선·수선·연결선 전부 포함)

```json
at: { "x": 35, "y": -2 },
plane: {
  "cellMmX": 9.5, "cellMmY": 6,          // 축별 칸 → 34.2×34.8mm (거의 정사각)
  "xMin": 0, "xMax": 3.6,                // 데이터 최대 2 + padX 1.6
  "yMin": 0, "yMax": 5.8,                // 데이터 최대 4.5 + padY 1.3
  "axisVariant": "quadrant",
  "gridStepX": 1, "gridStepY": 1.5,
  "gridCountXPos": 2, "gridCountXNeg": 0,
  "gridCountYPos": 3, "gridCountYNeg": 0,
  "padXPos": 1.6, "padXNeg": 1.6, "padYPos": 1.3, "padYNeg": 1.3,
  "gridOverXPos": 0.5, "gridOverYPos": 0.5,
  "richLabels": true, "gridToData": true, "seriesLock": true,
  "showGrid": false, "showTicks": true, "showTickLabels": true,
  "labelX": "시간(s)", "labelY": "속도(m/s)", "labelOrigin": "0", "showOrigin": true,
  "axisLabelSize": 4.3, "tickLabelSize": 3.5,          // ↓ 배율과 짝을 맞출 것
  "axisLabelScale": 0.61, "tickLabelScale": 0.6, "labelScale": 0.6,
  "strokeWidth": 0.3, "lockAspect": false, "labelType": "quantity",
  "annGuides":  [{"x":1,"y":3}, {"x":2,"y":4.5}],       // 수선의 발(축까지)
  "guideLines": [{"x1":1,"y1":3,"x2":1,"y2":4.5}]       // 계단 불연속 연결
},
functions: [
  { "expr": "3",   "domain": {"min":0,"max":1}, "strokeWidth": 0.45 },
  { "expr": "4.5", "domain": {"min":1,"max":2}, "strokeWidth": 0.45 }
]
```

**라벨 크기·배율 계산법** (반드시 짝을 맞춘다):
앱은 `size = cellX × 계수 × scale − 0.35`로 다시 계산한다(계수: 축이름 0.8, 눈금 0.68).
원하는 크기 S를 얻으려면 `scale = (S + 0.35) / (cellX × 계수)`.
→ cellX 9.5에서 축이름 4.3 ⇒ 0.61, 눈금 3.5 ⇒ 0.6. **이 값을 넣어야 모달을 열었다 닫아도 안 변한다.**

### ③ 상황도 (add_objects)

```
line      수평면: p1(-60,6) p2(-10,6), strokeWidth 0.5
text      "수평면" (-15, 9.5) size 3.5
svgAsset  cart ×3: x = -58 / -46 / -28,  y = -0.08,  w 12,  h 6.18
          ※ h = w × 0.5151 (원본 362.25:186.57). 어기면 레터박싱으로 뜬다.
          ※ 접지: y = 바닥선 − h + 0.1
          ※ 파선 불가 → 시점별 위치를 전부 실선(다중섬광 방식)
line      진행 화살표: p1(-56,-4) p2(-46,-4), lineMode "arrow", 0.35
line      시점 안내선 ×3: x = -52 / -40 / -22, y 6→13, 0.2, dash 1.2/0.8
line      치수 ×2: y=11.5, lineMode "lengthArrow", 0.25,
          dimensionLabel "3 m" / "4.5 m", dimensionLabelSize 3.5, labelType "label"
text      "0초"(-54.5,18.5) "1초"(-42.5,18.5) "2초"(-24.5,18.5) size 3.5
text      "(가)"(-39,25.5) "(나)"(33,25.5) size 3.7
```

### ④ 자가 검증 (read_app)

통과 조건:
- coordplane에 `seriesLock:true` + `groupId`가 있고, funcgraph 2개가 **같은 groupId**
- 그래프 영역에 별도 `line` 객체가 **하나도 없어야** 한다(있으면 내장 기능을 안 쓴 것)
- 상황도 좌표가 계획과 일치

---

## 3. 이번에 겪은 실패 — 재발 방지 체크리스트

| 증상 | 원인 | 예방 |
|---|---|---|
| 축 이름 글씨체가 다름 | `richLabels` 누락 → 구식 세리프 이탤릭 | 평면에 항상 `richLabels:true` |
| 치수 라벨이 "d"로 나옴 | 필드가 `label`이 아니라 `dimensionLabel` | 대응표대로 |
| 수레가 뜨거나 잠김 | `preserveAspectRatio` 레터박싱 | w:h를 원본 비율로 |
| 눈금 숫자가 거대해짐 | 라벨 크기가 cellX 비례, 모달이 재계산 | cellMm + 크기·배율 짝 지정 |
| 그래프가 안 그려짐 | funcgraph points는 **세계좌표**, 수동 계산 | 무조건 `add_graph` |
| 계열만 사라짐 | 여러 번 호출 중 자동저장 스냅샷(2.5초) | 그래프는 **한 번의 add_graph** |
| 미리보기와 캔버스 눈금이 다름 | `graphCfg` 누락 → 모달이 역산 | MCP가 자동 저장(패치됨) |
| 미리보기와 캔버스 비율이 다름 | 미리보기가 정사각 칸 강제 | 앱 패치됨(`eaefd62`) |
| 수선이 실선처럼 보임 | 수선 2개가 같은 x에서 겹쳐 대시 위상 상쇄 | **한 x에 수선 하나만**, 연결은 guideLines |
| ③표시 탭에 수선이 안 뜸 | 계열 소속 `guides`를 씀 | **평면 소속 `annGuides`**를 쓴다 |

## 4. 환경 규칙 — 수정이 언제 반영되는가

- **5E 앱 소스(js/) 수정** → 브라우저 **하드 리프레시(Ctrl+F5)** 후 반영 (모듈이 `?v=`로 캐시됨)
- **MCP 서버(tools/mcp-5e/) 수정** → **Claude Code 재시작** 후 반영 (세션 시작 때 뜬 프로세스)
- 헷갈리면 파일 모드(`create_project` + `add_graph` + 파이썬으로 JSON 확인)로 **서버 버전을 먼저 확인**한다.
- MCP 연결이 끊기면 앱이 자동 재연결한다(2초→최대 15초 백오프, 탭 복귀 시 즉시).

## 5. 그 밖의 유형 (초안 — 검증 전)

### 5-1. 광선도: 굴절

```
매질: rect 하단 fillLevel 220 / 상단 fillNone, 경계는 가는 실선
법선: line 세로 파선 0.2 (dash 1.2/0.8)
광선: line lineMode "middleArrow" 0.5
각: anglearc label "theta_1" / 직각: rightangle
매질 이름: text 한글, 영역 안
```

### 5-2. 회로도

```
add_circuit box + elements [{element:"dc_source"},{element:"resistor",label:"R_1"}…]
병렬은 branches. 전류: line "arrow" + formula "I"
```

### 5-3. 대화·만화형

5E로 그리지 않는다 — 인물·말풍선 자산이 없다. 교사가 별도 제작(07 가이드 8장).

---

## 6. 검증 루프 규칙

- 통과하면 해당 절에 `[검증됨]`을 붙이고 **실제 사용한 파라미터 전문**으로 교체한다.
- 통과 기준: ① 기출 PNG와 나란히 놓고 문법 일치 ② `read_app` 자가 검증 ③ 교사 승인.
- 실패하면 그림이 아니라 **이 문서를 고친다.**
