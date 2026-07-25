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
          접지: cart 박스 하단을 바닥선보다 2mm 아래로 (하단 여백 보정)
line      속도/진행 화살표: lineMode "arrow", strokeWidth 0.35, 첫 cart 위
line      시점 안내선: 각 cart 중심에서 아래로 가는 파선 (0.2, dash 1.2/0.8)
line      치수: lineMode "lengthArrow", strokeWidth 0.25,
          **dimensionLabel** "3 m" (label 필드 아님!), labelType "label"(정체)
text      "0초" "1초" "2초" 각 안내선 아래, "(가)" 그림 하단 중앙
```

- 자동차를 명시적으로 요구하면 cart 사용을 먼저 제안하고, 실루엣이 꼭 필요하면 rect+ellipse 바퀴 2개로 조립.

### 2-2. v-t 그래프 (계단형)

**주의: 기능 명세(08)의 "소스 코드로 확정한 렌더 특성"을 먼저 읽을 것.**

```
1) coordplane을 add_objects로 직접 생성 (add_graph는 richLabels를 못 켠다):
   { type:"coordplane", x,y,w,h, axisVariant:"quadrant", xMin:0, xMax:3, yMin:0, yMax:6,
     tickStepX:1, tickStepY:1.5, labelX:"시간(s)", labelY:"속도(m/s)", labelOrigin:"0",
     showGrid:false, showTicks:true, showTickLabels:false, richLabels:true }
2) read_app으로 평면 id 확보
3) 계단·안내선을 funcgraph로 — points는 세계 좌표(mm)로 변환해 넣는다:
   { type:"funcgraph", planeId:<id>, sourceKind:"points", curveStyle:"straight",
     points:[세계좌표], strokeWidth:0.5 }          ← 데이터 계단(굵은 실선)
   안내선(가는 파선)은 strokeWidth 0.2 + dashLength 1.2 + dashGap 0.8
4) 눈금 값(3, 4.5 등)은 text로 축 바깥에 (필요한 값만)
```

곡선형 그래프(포물선·사인 등)는 add_graph의 expr+domain을 쓰되, 평면은 위처럼
richLabels 평면을 먼저 만들고 add_graph 대신 funcgraph 수동 배치를 검토한다.

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
