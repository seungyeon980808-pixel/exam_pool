# ExamPool → 5E 평가원 그래프 계약

> 버전: `5e-fast-scene@1`  
> 상세 렌더 명세 원본: 5E `docs/engine-v2/EXAM_GRAPH_AI_SPEC.md`  
> 목적: ExamPool이 만든 그래프가 원시 선·텍스트 합성물이 아니라 5E에서 재편집 가능한
> `coordplane`·`funcgraph` 자산으로 변환되게 한다.

## 책임 경계

- ExamPool은 `figure_plan.panels[].objects`에 의미 기반 `type:"graph"` GraphSpec을 만든다.
- ExamPool은 저장 전 범위·박스·라벨 역할을 1차 검사한다.
- 5E MCP `add_scene`은 같은 장면을 `ai-scene-fastpath.js`로 다시 엄격 검증하고 네이티브
  `coordplane`·`funcgraph` 객체로 컴파일한다.
- 최종 선 굵기·글자 크기·한글 비율·이탤릭 혼합 렌더링은 5E 렌더러가 담당한다.
- 자체 기능으로 표현할 수 없는 구성은 원시 `line`·`text`로 흉내 내지 않고 5E 기능 갭으로 보고한다.

## 최소 형식

```json
{
  "type": "graph",
  "box": [-35, -25, 70, 50],
  "xRange": [0, 40],
  "yRange": [0, 100],
  "grid": true,
  "ticks": true,
  "axisLabels": true,
  "xLabel": "부피(mL)",
  "yLabel": "질량(g)",
  "axisStrokeWidth": 0.3,
  "seriesStrokeWidth": 0.55,
  "series": [{
    "kind": "scatter",
    "points": [[10, 40], [20, 80]],
    "markers": true
  }]
}
```

필수값은 `box`, `xRange`, `yRange`, `series`다. 한 패널의 그래프는 GraphSpec 하나로
표현하며 축, 눈금, 표시점, 안내선, 곡선 라벨을 별도 text 객체로 덧씌우지 않는다.

## 평가원 복원 항목

원본을 보며 다음을 독립적으로 맞춘다.

1. `box`의 가로세로 비율과 패널 간 간격
2. `xRange`·`yRange`·`xStep`·`yStep`, 격자 범위와 눈금 표시 범위
3. `markers`의 수학 좌표, 라벨 거리·각도·회전
4. `guides`(축까지 내린 수선)와 `guideLines`(임의 구간 안내선)
5. 축 화살표, 프레임, 축 생략 표시, 보조 y축
6. `axisStrokeWidth`와 `seriesStrokeWidth` 또는 계열별 `strokeWidth`
7. `axisLabelSizeX`·`axisLabelSizeY`·`tickLabelSize`·`labelHangulScale`

평가원 기본 위계는 축 0.3mm, 계열 0.55mm, 격자 없음이다. 다만 “기본값에 맞추는 것”이
목표가 아니라 원본의 픽셀 비율을 재는 것이 우선이므로 원본이 다르면 값을 명시해 바꾼다.

## 글꼴과 기울임

- A, B, C, (가), 단열, 등온, 지점명처럼 이름·범주인 라벨은 정체이며
  `labelRole:"label"`이다. 생략 시에도 이 값이 기본이다.
- t, v, I, P, V, E, f처럼 **라벨 자체가 물리량 기호**일 때만
  `labelRole:"quantity"`를 써 이탤릭으로 만든다.
- 숫자, 단위, 괄호, 한글은 정체다.
- 축 제목은 `시간(s)`, `v_y(m/s)`처럼 한 문자열로 주며 5E 혼합 라벨 렌더러가 물리량만
  이탤릭, 나머지는 정체로 나눈다.

## 전송·검증

GraphSpec이 없는 일반 패널은 `add_objects`로 보낸다. GraphSpec이 있는 복합 패널은 다음 장면과
기존 객체를 `add_scene` 한 번에 보내며, 결합 배열 전체가 통과할 때만 원자적으로 삽입된다.

```json
{
  "schema": "5e-fast-scene@1",
  "mode": "complete",
  "artboard": {"w": 90, "h": 60},
  "elements": [{"type": "graph", "...": "..."}]
}
```

전송은 `strict:true`다. 알 수 없는 필드, 잘못된 범위, 지원하지 않는 표현이 있으면 삽입하지
않고 오류를 반환한다. 결과 확인은 원본/5E 렌더를 나란히 비교하며 비율·점 위치·안내선·화살표·
격자 범위·글자 크기·선 굵기·라벨 기울임을 모두 확인한다.
