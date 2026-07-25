# 5E 기능 명세 (MCP 기준 전수 조사)

> 2026-07-25 `describe_schema` 21종 전수 조사 결과. 그림 작업 전 이 문서를 읽으면
> 타입별 재조사가 필요 없다. (새 필드가 의심될 때만 `describe_schema` 재확인)
> 단위 mm, 원점 아트보드 중앙, +x 오른쪽 / +y 아래. 기본 아트보드 90×60 → x ±45, y ±30.

## 공통 필드 (대부분의 도형)

- `fillLevel` 0~255 회색 명도 (255=흰색) — **평가원 회색 단계 표현의 핵심**. `fillNone: true`면 투명.
- `fillStyle`: solid | dots | cross | hatch (평가원 문법상 hatch는 쓰지 않는다 — solid 회색만)
- `dashLength`/`dashGap`: 0이면 실선. 파선은 예: dashLength 1.2, dashGap 0.8.
- `strokeWidth`: 선 굵기 mm. 위계: 본체 0.5~0.6 / 보조 0.2~0.25 권장.
- `labelType`: **quantity(물리량·이탤릭 수식체) | label(이름·정체)** — 평가원 서체 2분법과 정확히 대응.
- `labelPos`: center | above | below | left | right
- `rotation`: 도(deg).

## 타입별 요점

| 타입 | 필수 | 결정적 기능 |
|---|---|---|
| **line** | p1,p2 | `lineMode`: solid \| arrow \| **middleArrow**(경로 중간 화살촉=광선) \| **lengthArrow**(치수선). `arrowHead`: none\|end\|start\|**both**. label 부착 가능 |
| **polyline** | points[2+] | `arrowHead` both 지원, `closed`+fill로 채움 도형(블록 화살표 우회), `rounded`+`cornerRadius`(말풍선류) |
| **curve** | points[2+] | Catmull-Rom 곡선. 파형·물결(광자) 우회용. arrowHead 지원 |
| **rect** | x,y,w,h | 물체 표준. label(quantity=내부 질량, label=외부 이름) |
| **ellipse** | x,y,w,h | 원·분자(작은 흰 원)·점(작게+fillLevel 0) |
| **triangle** | x,y,w,h | 직각삼각형(빗면). `flipX`/`flipY` |
| **text** | x,y,text | `fontFamily` 지정 가능(기본 돋움 고딕), `italic`, `fontSize`(기본 3.7), halo 기본 켜짐. `{roman1}~{roman12}` → Times 로마숫자 |
| **formula** | x,y,source | 중괄호 수식 문법, **수식체 렌더 — 물리량 기호·첨자는 text가 아니라 이걸로** |
| **labeler** | p1,p2 | 지시선+라벨. p1=가리키는 곳, p2=글자 위치. 기본 ㉠ |
| **coordplane** | x,y,w,h | `axisVariant`: cross \| **quadrant(1사분면=시험 그래프 표준)** \| single. `labelX/labelY`(축 라벨), `labelOrigin`(→ "0"으로 변경), `showGrid/showTicks/showTickLabels`, `gridStep/tickStep` |
| **funcgraph** | points[],planeId | coordplane 좌표계 위 곡선. add_graph 권장이지만 **points 직접 지정으로 계단형 등 임의 곡선 가능** |
| **anglearc** | x,y | 각도 호. `theta_1`→θ₁ 자동 변환, radius/startAngle/sweepAngle |
| **rightangle** | x,y | 직각 ㄱ자 기호. size/angle/orientation |
| **svgAsset** | x,y,w,h,assetId | 내장 심볼: **pulley, cart** (기본 43×38, lockAspect) |
| **optics** | x,y,w,h,kind | convex/concave_lens, convex/concave/plane_mirror, object_arrow, point_light, screen, pulley, node, support_tri, pivot, **bar_magnet** |
| **apparatus** | x,y,kind | wire, compass, pulley, clamp, scale |
| **pendulum** | p1,p2 | 단진자. 중앙/대칭 고스트(잔상)·길이 라벨 내장 |
| **circuit** | p1,p2,element | resistor, dc_source, ac_source, capacitor, inductor, diode, lamp, ammeter, voltmeter, unknown. `R_1`→R₁ |
| **gauge** | x,y,w,h,kind | ruler, protractor |
| axes | x,y,w,h | 구형 — 쓰지 않는다(coordplane 사용) |
| image | - | MCP로 생성 불가(앱에서 붙여넣기 전용) |

## 상위 도구

- **add_graph**: `at`(중심) + `plane`(xMin/xMax/yMin/yMax, cellMm, axisVariant, showGrid, labelX, labelY, showTickLabels…) + `functions`(expr, domain, strokeWidth, dash, label). 빈 functions면 좌표평면만.
- **add_circuit**: `box` 둘레에 소자 자동 배치(전원 왼쪽 변 기본), `branches`로 병렬 가지.
- **set_artboard**: 그리기 전 원본 비율에 맞게 크기 조정 (기출 재현 시 필수).
- **read_app / clear_app / remove_from_app**: 그린 뒤 자가 검증·수정용.
- add_objects는 **필드가 틀리면 전체 거부**(부분 삽입 없음) — 오류 메시지로 필드명을 교정할 수 것.

## 소스 코드로 확정한 렌더 특성 (2026-07-25, 51_5E/5E_main 소스 분석)

**이 절은 시행착오로 배운 것 — 어기면 그림이 깨진다.**

1. **funcgraph의 `points`는 세계 좌표(mm)다.** 평면 좌표(초·m/s)가 아니다. 수동 계열은
   `sourceKind: "points"` + `curveStyle: "straight"`(직선) 지정, planeId는 편집 연결용.
   평면 좌표 → 세계 좌표 변환: `x_mm = box.x + (t - xMin)/(xMax - xMin) * w`,
   `y_mm = box.y + h - (v - yMin)/(yMax - yMin) * h`.
2. **coordplane 축 라벨 글씨체는 `richLabels: true`일 때만** 혼합 라벨러(한글 정자 + 영문
   이탤릭 + 수식 + halo)를 탄다. 없으면 구식 세리프 이탤릭으로 렌더. UI 그래프 모달은 이걸
   자동으로 켜지만 **MCP add_graph는 안 켠다** → coordplane을 add_objects로 직접 만들며
   `richLabels: true`를 넣는다. 라벨은 LaTeX 문법(v_0) 지원, `\text{}`는 미지원(글자 그대로 나옴).
3. **lengthArrow 치수 라벨 필드는 `dimensionLabel`** (`label` 아님 — 없으면 기본 "d").
   끝 종단선은 `dimensionVariant`: basic(없음) | leftBar | rightBar | bothBars.
   라벨 크기는 `dimensionLabelSize`(mm).
4. **svgAsset(cart 등)은 내장 SVG 이미지 방식 — dashLength가 안 먹는다** (파선 불가).
   여러 시점 위치는 전부 실선으로 반복(다중섬광 방식).
   **접지(중요)**: renderSvgAsset이 `preserveAspectRatio="xMidYMid meet"`로 그린다 →
   w:h가 원본 비율과 다르면 상자 안에서 레터박싱되어 위아래에 빈 여백이 생기고 물체가
   떠 보인다. **반드시 원본 비율을 지킬 것**:
   - cart: 362.25 × 186.57 (h = w × 0.5151). 예: w 12 → h 6.18
   - pulley: `describe_schema`의 기본값(43×38) 비율 확인 후 사용
   비율을 맞추면 상자 하단 ≈ 접지선. cart는 내부에 하단 여백 1.6%(h 6.18mm에서 0.1mm)가
   더 있으므로 바닥선 y에 대해 `y = 바닥 - h + 0.1` 로 두면 바퀴가 정확히 닿는다.
5. add_objects는 스키마에 없는 필드도 **통과**시킨다(passthrough) — richLabels·dimensionLabel
   같은 렌더러 필드를 직접 넣을 수 있다. 단 로드 경로는 관대해서 틀린 필드는 조용히 무시된다.
6. funcgraph에 `guideSegs`(파선 안내선)·`markers`(●점)·`arrowMarks`(곡선 위 화살촉)·
   `endLabel`(곡선 끝 라벨)을 직접 실을 수 있다 — 전부 세계 좌표.
7-A. **글자 크기는 칸(cell) 크기에 비례한다 — w/h를 임의로 키우면 숫자가 거대해진다.**
   `cell = plane.w / (xMax - xMin)`, 앱 공식(graph-modal.js `setLabelSizes`):
   - `axisLabelSize = cell × 0.8 − 0.35`
   - `tickLabelSize = cell × 0.68 − 0.35`
   그래서 **w/h를 직접 주지 말고 `cellMm`로 크기를 정한다**(기본 4.8, 모달 상한 9, 권장 5~7).
   그리고 위 공식으로 계산한 `axisLabelSize`·`tickLabelSize`를 **명시적으로 함께 넣는다** —
   넣지 않으면 초기 렌더와 "그래프 편집 모달을 열었다 닫은 뒤"의 크기가 달라진다
   (모달이 열릴 때 setLabelSizes가 다시 돌기 때문. 2026-07-25 숫자 거대화 사건의 원인).
   그래프를 크게 만들고 싶으면 cell을 키우지 말고 **칸 수를 늘린다**(tickStep을 잘게).

7. **그래프는 "그래프 도구(모달) 평면"과 동일 설정으로 만들어야 한다.** UI 그래프 도구가
   평면에 거는 필드 세트 (graph-modal.js `applyCfg`):
   - `richLabels: true`, `gridToData: true`
   - **화살표 여백**: `xMax = 데이터최댓값 + padXPos(기본 1.6)`, `yMax = 최댓값 + padYPos(기본 1.3)`
     (pad 값도 `padXPos`/`padYPos`로 함께 저장 — 재편집 복원용)
   - **눈금 캡**: `gridStepX/Y`(간격) + `gridCountXPos/YPos`(양의 칸 수) → 눈금은 데이터
     범위까지만 찍히고 pad 구간은 순수 화살표 여백이 된다
   - 수동 계열(sourceKind "points")은 평면 이동 시 안 따라가는 baked 좌표 — **데이터 곡선은
     expr 기반 funcgraph**(expr·domainMin/Max·planeId + 좌표 변환한 points)로 만들어야
     재샘플·모달 재편집이 된다. (2026-07-25 MCP 서버 패치로 add_graph의 plane에 위 필드
     전달 가능 — 단 서버 재시작 후부터)

## 갭 목록 (평가원 문법 중 5E에 없는 것 → 우회 or 5E 기능 추가 후보)

| 필요 기능 | 상태 | 우회 레시피 | 5E 추가 제안 |
|---|---|---|---|
| 자동차 실루엣 | 없음 (cart만) | svgAsset cart 사용, 또는 rect+ellipse 2개(바퀴) 조합 | 자동차 assetId 추가 |
| 물결선 광자 화살표 | 없음 | curve(지그재그 points)+작은 polyline 화살촉 | photon 전용 타입 |
| 회색 블록 화살표(에너지 흐름) | 없음 | polyline closed + fillLevel 회색 | block_arrow 타입 |
| 말풍선·인물 | 없음 | 그리지 않음(대화형은 교사 별도 제작 — 가이드 9장) | - |
| 계단형/불연속 그래프 | add_graph expr 불가 | **funcgraph에 points 직접 지정** 또는 polyline | step 함수 지원 |
| Times 세리프 이탤릭 확인 | 미검증 | formula 사용이 원칙, text는 fontFamily로 실험 | - |
