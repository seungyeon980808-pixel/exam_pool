# 평가원 문법 → 5E 레시피

> 스타일 가이드(07)의 규칙을 5E 기능 명세(08)의 실제 파라미터로 구현하는 방법.
> `[검증됨]` 표시가 있는 레시피는 5E 화면에서 기출과 비교 확인을 통과한 것 —
> **그대로 복제하고 수치만 바꾼다.** 표시가 없으면 초안이므로 그린 뒤 read_app으로 확인한다.

## 0. 작업 순서 (모든 그림 공통)

1. `PRD/07_KICE_FIGURE_STYLE.md`에서 해당 유형 문법 확인
2. `assets/kice_figures/figures.json`에서 같은 유형 기출 PNG 1~2장을 열어 구도 확인
3. `set_artboard`로 목표 비율 설정 → `add_objects`/`add_graph`로 그리기
4. `read_app`으로 결과를 읽고 좌표 겹침·이탈 확인, 어긋난 객체는 `remove_from_app` 후 재추가
5. 사용자에게 화면 확인 요청 (스스로 "완성" 선언 금지)

## 1. 규칙 → 파라미터 대응표

| 평가원 규칙 (가이드 07) | 5E 구현 |
|---|---|
| 선 굵기 2단 위계 | 본체 strokeWidth 0.5~0.6 / 보조 0.2~0.25 |
| 법선·안내선·기준선 = 가는 파선 | line, strokeWidth 0.2, dashLength 1.2, dashGap 0.8 |
| 이전/가상 위치 = 파선 윤곽 | 같은 도형 반복 + dashLength/dashGap + fillNone: true |
| 치수(거리) 표기 | **line, lineMode: "lengthArrow"** + label(값), labelPos |
| 광선(경로 중간 화살촉) | **line, lineMode: "middleArrow"**, strokeWidth 0.5 |
| 힘·속도 화살표 | line, lineMode: "arrow" (힘 0.6 / 속도 0.35 굵기 차) |
| 양방향 화살표 | arrowHead: "both" |
| 물리량 기호(이탤릭 수식체) | **formula** (v_0 등 첨자 포함) — text 금지 |
| 물체·지점 이름(정체) / 한글 라벨(고딕) | text (기본 fontFamily가 고딕) / labelType: "label" |
| ㉠㉡ 등 지시선 라벨 | **labeler** (p1=대상, p2=글자) |
| 각도 호 + θ | anglearc (label: "theta_1" → θ₁) |
| 직각 ㄱ자 표시 | rightangle (size 3~4) |
| 회색 명도 단계(매질·벽·마찰구간) | rect 등 + fillLevel (연회색 220 / 중간 190 / 진회색 160) |
| 해칭 금지 | fillStyle은 항상 "solid" |
| 그래프: 1사분면, 원점 0 | coordplane/add_graph plane: axisVariant "quadrant", **labelOrigin "0"** |
| 그래프: 격자 없음 + 눈금 | showGrid false, showTicks true, showTickLabels 필요시 true |
| 축 라벨 "물리량(단위)" | labelX "시간(s)", labelY "속도(m/s)" |
| 계단형·꺾은선 데이터 | funcgraph에 points 직접 지정(planeId 연결) 또는 polyline |
| 수레·도르래 | svgAsset(cart/pulley), optics(pulley), apparatus |
| 자석·거울·렌즈·광원·스크린 | optics(bar_magnet, plane_mirror, convex_lens, point_light, screen…) |
| (가)(나) 캡션 | text, 부분 그림 **하단 중앙**, "(가)" |
| 수평면 | line 가는 실선 + text "수평면"(선 오른쪽 끝 아래) |

## 2. 유형 레시피

### 2-1. 역학상황도: 등가속 자동차 + 구간 거리 (초안)

구성 요소와 좌표 잡기(아트보드 150×60 기준, 좌측 절반 사용 예):

```
line      수평면: 가는 굵기보다 굵게(0.5), 오른쪽 끝 아래 text "수평면"
svgAsset  cart ×3 (시점별 위치) — 파선 불가하므로 전부 실선(다중섬광 방식).
          **비율 필수**: h = w × 0.5151 (예 w 12 → h 6.18). 어기면 레터박싱으로 뜬다.
          접지: y = 바닥선 - h + 0.1
line      속도/진행 화살표: lineMode "arrow", strokeWidth 0.35, 첫 cart 위
line      시점 안내선: 각 cart 중심에서 아래로 가는 파선 (0.2, dash 1.2/0.8)
line      치수: lineMode "lengthArrow", strokeWidth 0.25,
          **dimensionLabel** "3 m" (label 필드 아님!), labelType "label"(정체)
text      "0초" "1초" "2초" 각 안내선 아래, "(가)" 그림 하단 중앙
```

- 자동차를 명시적으로 요구하면 cart 사용을 먼저 제안하고, 실루엣이 꼭 필요하면 rect+ellipse 바퀴 2개로 조립.

### 2-2. v-t 그래프 (계단형)

**주의: 기능 명세(08)의 "소스 코드로 확정한 렌더 특성"을 먼저 읽을 것.**

**데이터 곡선은 반드시 `add_graph` 한 번으로 만든다** (앱 자체 샘플러 사용 = 내장 함수
기능). 손으로 좌표를 계산해 funcgraph를 add_objects 하지 않는다 — 좌표가 맞더라도
재편집·재샘플이 안 되고, 여러 번의 호출로 나뉘면 자동저장 스냅샷이 중간에 끼어
새로고침 후 계열만 사라질 수 있다.

**w/h를 직접 주지 않는다 — `cellMm`로 정한다.** 글자 크기가 칸 크기에 비례하기 때문(08의 7-A).
`cellMm` 권장 5~7, 그리고 `tickLabelSize = cellMm×0.68−0.35`, `axisLabelSize = cellMm×0.8−0.35`를
**계산해서 함께 넣는다**(안 넣으면 모달을 열었다 닫을 때 글자가 커진다).

```
add_graph
  at: { x: <평면 중심>, y: <평면 중심> }
  plane: { cellMm:6,                             # ← w/h 대신 이것으로 (w=cell×(xMax-xMin))
           xMin:0, xMax:3.6, yMin:0, yMax:5.8,   # 데이터 최대 + pad
           axisVariant:"quadrant",
           gridStepX:1, gridStepY:1.5,           # 눈금 간격
           gridCountXPos:2, gridCountYPos:3,     # 눈금 칸 수(데이터 범위까지만)
           padXPos:1.6, padYPos:1.3,             # 마지막 눈금 뒤 화살표 여백
           gridOverXPos:0.5, gridOverYPos:0.5,
           richLabels:true, gridToData:true,     # 그래프 도구와 같은 글씨체·격자
           showGrid:false, showTicks:true, showTickLabels:true,
           labelX:"시간(s)", labelY:"속도(m/s)", labelOrigin:"0",
           axisLabelSize:4.5, tickLabelSize:3.7, # ← cellMm 6에 대한 앱 공식값
           axisLabelScale:1, tickLabelScale:1, labelScale:1,
           strokeWidth:0.3, lockAspect:false, labelType:"quantity" }
  functions: [ { expr:"3",   domain:{min:0,max:1}, strokeWidth:0.45,
                 guides:[{x:1,y:3}] },                    # 수선의 발 = 내장 기능
               { expr:"4.5", domain:{min:1,max:2}, strokeWidth:0.45,
                 guides:[{x:1,y:4.5},{x:2,y:4.5}] } ]
```

**수선의 발·표시점은 반드시 `guides`/`markers`로 만든다** — 직선 도구(`line`)로 따로 긋지
않는다. guides는 그 점에서 x축·y축으로 가는 파선을 내리고, 평면에 종속되어 함께 움직이며,
그래프 편집 모달에서 재편집된다. (2026-07-25 MCP 확장)

**비율**: 값 범위가 좁은 축(예: 시간 0~2)을 그대로 두면 세로로 길쭉해 읽기 어렵다.
`cellMmX`/`cellMmY`로 축별 칸 크기를 달리해 **가로·세로 물리 길이를 비슷하게** 맞춘다.
글자 크기는 x축 칸 기준(`cellX`)이므로 라벨 크기도 그에 맞춰 계산한다.

그래프를 더 크게 하려면 칸을 키우지 말고(글자가 같이 커진다) **눈금 간격을 잘게 나눠
칸 수를 늘린다**.

- **계단형은 상수함수 여러 개**(구간별 domain)로 만든다 — step 함수가 없어도 이걸로 된다.
- 눈금 숫자는 `showTickLabels:true`로 앱이 찍게 둔다(수동 text 금지 — 위치가 어긋난다).
- 불연속 연결선·값 안내선만 별도 `line`(파선 0.2 / dash 1.2·0.8)으로 얹는다.
  세계 좌표 변환: `x_mm = box.x + (t-xMin)/(xMax-xMin)*w`, `y_mm = box.y + (yMax-v)/(yMax-yMin)*h`
  (add_graph 결과 메시지가 평면 id와 크기를 알려주고, 박스 원점은 `at ∓ w/2, h/2`)

### 2-3. 광선도: 굴절 (초안)

```
매질: rect 하단(물) fillLevel 220 / 상단(공기) fillNone — 경계는 가는 실선
법선: line 세로 파선 (strokeWidth 0.2, dash 1.2/0.8)
입사·굴절 광선: line, lineMode "middleArrow", strokeWidth 0.5
각: anglearc (법선 기준 startAngle/sweepAngle), label "theta_1"
직각: rightangle (법선-경계 교차점)
매질 이름: text "공기"/"물" 영역 안 한글
```

### 2-4. 회로도 (초안)

```
add_circuit box + elements [{element:"dc_source"},{element:"resistor",label:"R_1"},
  {element:"ammeter"}...], 병렬은 branches
전류 방향: line "arrow" 가는 화살표 + formula "I"
```

### 2-5. 복합(상황도+그래프) (초안)

```
set_artboard 150×60 (가로 2패널)
좌측 x -70~-10: 상황도 / 우측 x 10~70: add_graph
text "(가)" (좌측 하단 중앙), "(나)" (우측 하단 중앙)
```

## 3. 검증 루프 규칙

- 레시피가 검증을 통과하면 이 문서의 해당 절에 `[검증됨]`을 붙이고 **실제 사용한 add_objects 파라미터 전문**을 코드블록으로 교체한다.
- 통과 기준: ① 기출 PNG와 나란히 놓고 구도·선 위계·서체·화살표가 문법과 일치 ② 사용자(교사) 승인.
- 실패 시 이 문서의 레시피를 고치는 것이 원칙 (개별 그림에서 임기응변 금지 — 재현성이 목적).
