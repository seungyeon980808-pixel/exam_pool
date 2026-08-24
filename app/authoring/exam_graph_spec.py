"""ExamPool-side contract for editable KICE-style graphs rendered by 5E.

ExamPool owns GraphSpec production and early diagnostics. 5E remains the
authoritative compiler and renderer, so graph elements are sent through
``add_scene`` instead of being approximated with native line/text objects.
"""
from __future__ import annotations

import copy
import math


FAST_SCENE_SCHEMA_ID = "5e-fast-scene@1"
GRAPH_SPEC_NAME = "KICE editable graph"
LABEL_ROLES = {"label", "quantity"}
ENDPOINT_TYPES = {
    "line", "labeler", "circuit", "pendulum", "spring", "chargefield",
    "fieldlines", "standingwave", "parabola", "groundarc", "brace",
    "chromosome", "bilayer", "neuron",
}

GRAPH_PROMPT_RULES = """그래프는 원시 line/text 조합이 아니라 5E GraphSpec의 type="graph" 객체로 만든다.
필수값은 box:[x,y,w,h], xRange:[min,max], yRange:[min,max], series 배열이다.
평가원 기본값은 grid=false, ticks=true, axisLabels=true, axisStrokeWidth=0.3,
seriesStrokeWidth=0.55이다. 원본을 복원할 때는 그래프 비율, 축 범위, 눈금 간격, 점 위치,
안내선, 화살표, 격자 유무, 축/계열 선 굵기, axisLabelSizeX/Y, tickLabelSize를 원본에 맞춰 명시한다.
일반 라벨(A, B, C, (가), 단열, 지점명)은 정체이며 labelRole="label"을 쓴다.
라벨 자체가 물리량 기호(t, v, I, P, V, E, f 등)일 때만 이탤릭이므로
labelRole="quantity"를 쓴다. 숫자·단위·괄호·한글은 정체다.
축 제목은 xLabel/yLabel 한 문자열 안에서 물리량은 이탤릭, 단위와 한글은 정체로 혼합 렌더된다.
표시점은 markers, 수선은 guides, 임의 안내선은 guideLines, 곡선은 series에 넣어
모든 요소가 coordplane/funcgraph 내부 편집 자산으로 남게 한다."""


class GraphSpecError(ValueError):
    """Raised before any partial 5E insertion when a GraphSpec is invalid."""


def _finite(value, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise GraphSpecError(f"{path}: 유한한 숫자가 필요합니다.")
    return float(value)


def _range(value, path: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise GraphSpecError(f"{path}: [최솟값, 최댓값] 형식이어야 합니다.")
    lo, hi = (_finite(value[0], f"{path}[0]"), _finite(value[1], f"{path}[1]"))
    if lo >= hi:
        raise GraphSpecError(f"{path}: 최댓값이 최솟값보다 커야 합니다.")
    return [lo, hi]


def _box(value, path: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise GraphSpecError(f"{path}: [x, y, w, h] 형식이어야 합니다.")
    box = [_finite(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if box[2] <= 0 or box[3] <= 0:
        raise GraphSpecError(f"{path}: 폭과 높이는 0보다 커야 합니다.")
    return box


def _normalize_label_role(item: dict, path: str) -> None:
    role = item.get("labelRole", "label")
    if role not in LABEL_ROLES:
        raise GraphSpecError(f'{path}.labelRole: "label" 또는 "quantity"만 허용됩니다.')
    item["labelRole"] = role


def normalize_graph_object(value: dict, path: str = "graph") -> dict:
    if not isinstance(value, dict) or value.get("type") != "graph":
        raise GraphSpecError(f'{path}: type="graph" 객체가 필요합니다.')
    graph = copy.deepcopy(value)
    graph["box"] = _box(graph.get("box"), f"{path}.box")
    graph["xRange"] = _range(graph.get("xRange"), f"{path}.xRange")
    graph["yRange"] = _range(graph.get("yRange"), f"{path}.yRange")
    series = graph.get("series")
    if not isinstance(series, list):
        raise GraphSpecError(f"{path}.series: 배열이어야 합니다.")
    for index, row in enumerate(series):
        if not isinstance(row, dict):
            raise GraphSpecError(f"{path}.series[{index}]: 객체여야 합니다.")
        _normalize_label_role(row, f"{path}.series[{index}]")
    for field in ("markers", "labels"):
        rows = graph.get(field, [])
        if not isinstance(rows, list):
            raise GraphSpecError(f"{path}.{field}: 배열이어야 합니다.")
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise GraphSpecError(f"{path}.{field}[{index}]: 객체여야 합니다.")
            _normalize_label_role(row, f"{path}.{field}[{index}]")

    graph.setdefault("grid", False)
    graph.setdefault("ticks", True)
    graph.setdefault("axisLabels", True)
    graph.setdefault("showNumbers", False)
    graph.setdefault("axisStrokeWidth", 0.3)
    graph.setdefault("seriesStrokeWidth", 0.55)
    graph.setdefault("labelHangulScale", 0.72)
    return graph


def strip_graph_text(graph: dict) -> dict:
    """Honor ExamPool's include_text=false option recursively."""
    graph = copy.deepcopy(graph)
    for key in ("xLabel", "yLabel", "y2Label", "originLabel", "panelLabel",
                "tickTextX", "tickTextY", "tickTextY2", "labels", "legends"):
        graph.pop(key, None)
    graph["axisLabels"] = False
    graph["showNumbers"] = False
    for field in ("series", "markers"):
        for item in graph.get(field, []):
            if isinstance(item, dict):
                item.pop("label", None)
    return graph


def _point(value, path: str) -> dict:
    """Accept model-friendly point aliases and return 5E's canonical point."""
    if isinstance(value, dict):
        x, y = value.get("x"), value.get("y")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        x, y = value
    else:
        raise GraphSpecError(f"{path}: {{x, y}} 좌표가 필요합니다.")
    return {"x": _finite(x, f"{path}.x"), "y": _finite(y, f"{path}.y")}


def _normalize_native_object(value: dict, path: str) -> dict:
    if not isinstance(value, dict):
        raise GraphSpecError(f"{path}: 5E 객체여야 합니다.")
    obj = copy.deepcopy(value)
    obj_type = str(obj.get("type") or "")
    if not obj_type:
        raise GraphSpecError(f"{path}.type: 5E 객체 종류가 필요합니다.")
    if obj_type not in ENDPOINT_TYPES:
        return obj

    # Models often express a segment as x1/y1/x2/y2 or from/to.  The 5E
    # schema requires p1/p2 objects, so canonicalize before opening any tabs.
    p1 = obj.get("p1", obj.get("from"))
    p2 = obj.get("p2", obj.get("to"))
    if p1 is None and all(key in obj for key in ("x1", "y1")):
        p1 = {"x": obj.get("x1"), "y": obj.get("y1")}
    if p2 is None and all(key in obj for key in ("x2", "y2")):
        p2 = {"x": obj.get("x2"), "y": obj.get("y2")}
    obj["p1"] = _point(p1, f"{path}.p1")
    obj["p2"] = _point(p2, f"{path}.p2")
    if obj["p1"] == obj["p2"]:
        raise GraphSpecError(f"{path}: p1과 p2가 같은 점일 수 없습니다.")
    for key in ("from", "to", "x1", "y1", "x2", "y2"):
        obj.pop(key, None)
    return obj


def normalize_figure_objects(objects: list[dict], *, include_text: bool = True,
                             path: str = "objects") -> list[dict]:
    normalized = []
    for index, raw in enumerate(objects):
        if raw.get("type") == "graph":
            graph = normalize_graph_object(raw, f"{path}[{index}]")
            normalized.append(graph if include_text else strip_graph_text(graph))
        else:
            normalized.append(_normalize_native_object(raw, f"{path}[{index}]"))
    return normalized


def split_native_and_graph_objects(objects: list[dict]) -> tuple[list[dict], list[dict]]:
    native, graphs = [], []
    for obj in objects:
        (graphs if obj.get("type") == "graph" else native).append(obj)
    return native, graphs


def graph_scene(artboard: dict, graphs: list[dict]) -> dict:
    return {
        "schema": FAST_SCENE_SCHEMA_ID,
        "mode": "complete",
        "artboard": {"w": float(artboard["w"]), "h": float(artboard["h"])},
        "elements": copy.deepcopy(graphs),
    }
