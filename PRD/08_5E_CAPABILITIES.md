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

## 갭 목록 (평가원 문법 중 5E에 없는 것 → 우회 or 5E 기능 추가 후보)

| 필요 기능 | 상태 | 우회 레시피 | 5E 추가 제안 |
|---|---|---|---|
| 자동차 실루엣 | 없음 (cart만) | svgAsset cart 사용, 또는 rect+ellipse 2개(바퀴) 조합 | 자동차 assetId 추가 |
| 물결선 광자 화살표 | 없음 | curve(지그재그 points)+작은 polyline 화살촉 | photon 전용 타입 |
| 회색 블록 화살표(에너지 흐름) | 없음 | polyline closed + fillLevel 회색 | block_arrow 타입 |
| 말풍선·인물 | 없음 | 그리지 않음(대화형은 교사 별도 제작 — 가이드 9장) | - |
| 계단형/불연속 그래프 | add_graph expr 불가 | **funcgraph에 points 직접 지정** 또는 polyline | step 함수 지원 |
| Times 세리프 이탤릭 확인 | 미검증 | formula 사용이 원칙, text는 fontFamily로 실험 | - |
