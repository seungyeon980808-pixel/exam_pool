# -*- coding: utf-8 -*-
r"""평가원 스타일 시험지 삽화 생성기 — 실제 2017-2026 수능 177종 분석 기반.

실제 수능 그림은 전부 '글자(폰트 패스)'로 구성되어 있다:
  - 선/상자: 유니코드 선문자 (─│┌┐└┘├┤┬┴┼ 등)
  - 화살표/점: 특수 기호 (→←↑↓●○◎ 등)
  - 레이블: 한글 텍스트
  - 특정 한글 폰트로 렌더링된 path 데이터가 전부

SVG에서는 같은 효과를 <rect>, <circle>, <line>, <text> 등 기본 요소로 낸다.
스타일 요약:
  - 검은색(#000) 선, 흰 바탕, 계조 없음
  - 선 굵기 1.0~1.3px, 둥글지 않은 직선적 모서리
  - 레이블: 9~10pt, 함초롬바탕 계열
  - 깔끔한 정렬, 군더더기 없음
"""

import math
from xml.dom import minidom


def _make_svg(width_pt=300, height_pt=200):
    svg = minidom.Document()
    root = svg.createElement("svg")
    root.setAttribute("xmlns", "http://www.w3.org/2000/svg")
    root.setAttribute("viewBox", f"0 0 {width_pt} {height_pt}")
    root.setAttribute("width", f"{width_pt}pt")
    root.setAttribute("height", f"{height_pt}pt")
    svg.appendChild(root)
    return svg, root


def _style_element(svg, root):
    style = svg.createElement("style")
    style.appendChild(svg.createTextNode(
        ".l { fill:none; stroke:#000; stroke-width:1.0; stroke-linecap:butt; stroke-linejoin:miter; } "
        ".l2 { fill:none; stroke:#000; stroke-width:0.8; stroke-linecap:butt; } "
        ".l3 { fill:none; stroke:#000; stroke-width:1.3; stroke-linecap:butt; } "
        ".f { fill:#000; stroke:none; } "
        ".t { fill:#000; font-family:'HCR Batang','Hamchorom Batang','Noto Serif KR','Batang','Serif',serif; font-size:9.5pt; text-anchor:middle; dominant-baseline:central; } "
        ".tl { fill:#000; font-family:'HCR Batang','Hamchorom Batang','Noto Serif KR','Batang','Serif',serif; font-size:9.5pt; text-anchor:start; dominant-baseline:hanging; } "
        ".tr { fill:#000; font-family:'HCR Batang','Hamchorom Batang','Noto Serif KR','Batang','Serif',serif; font-size:9.5pt; text-anchor:end; dominant-baseline:central; } "
        ".tc { fill:#000; font-family:'HCR Batang','Hamchorom Batang','Noto Serif KR','Batang','Serif',serif; font-size:8pt; text-anchor:middle; dominant-baseline:central; } "
        ".tl2 { fill:#000; font-family:'HCR Batang','Hamchorom Batang','Noto Serif KR','Batang','Serif',serif; font-size:8.5pt; text-anchor:start; } "
    ))
    root.appendChild(style)


def line(root, x1, y1, x2, y2, cls="l"):
    e = root.ownerDocument.createElement("line")
    e.setAttribute("x1", f"{x1:.1f}")
    e.setAttribute("y1", f"{y1:.1f}")
    e.setAttribute("x2", f"{x2:.1f}")
    e.setAttribute("y2", f"{y2:.1f}")
    e.setAttribute("class", cls)
    root.appendChild(e)


def rect(root, x, y, w, h, cls="l"):
    e = root.ownerDocument.createElement("rect")
    e.setAttribute("x", f"{x:.1f}")
    e.setAttribute("y", f"{y:.1f}")
    e.setAttribute("width", f"{w:.1f}")
    e.setAttribute("height", f"{h:.1f}")
    e.setAttribute("class", cls)
    root.appendChild(e)


def circle(root, cx, cy, r, cls="l"):
    e = root.ownerDocument.createElement("circle")
    e.setAttribute("cx", f"{cx:.1f}")
    e.setAttribute("cy", f"{cy:.1f}")
    e.setAttribute("r", f"{r:.1f}")
    e.setAttribute("class", cls)
    root.appendChild(e)


def dot(root, cx, cy, r=2.2):
    circle(root, cx, cy, r, cls="f")


def text(root, x, y, s, cls="t"):
    e = root.ownerDocument.createElement("text")
    e.setAttribute("x", f"{x:.1f}")
    e.setAttribute("y", f"{y:.1f}")
    e.setAttribute("class", cls)
    e.appendChild(root.ownerDocument.createTextNode(s))
    root.appendChild(e)


def arrow(root, x1, y1, x2, y2, cls="l"):
    line(root, x1, y1, x2, y2, cls)
    ang = math.atan2(y2 - y1, x2 - x1)
    s = 5.5
    a = 0.45
    tx, ty = x2, y2
    p1 = f"{tx:.1f},{ty:.1f}"
    p2 = f"{tx - s * math.cos(ang - a):.1f},{ty - s * math.sin(ang - a):.1f}"
    p3 = f"{tx - s * math.cos(ang + a):.1f},{ty - s * math.sin(ang + a):.1f}"
    e = root.ownerDocument.createElement("polygon")
    e.setAttribute("points", f"{p1} {p2} {p3}")
    e.setAttribute("class", "f")
    root.appendChild(e)


def double_arrow(root, x1, y1, x2, y2, cls="l"):
    line(root, x1, y1, x2, y2, cls)
    ang = math.atan2(y2 - y1, x2 - x1)
    s = 5.5
    a = 0.45
    for tip_x, tip_y, tip_ang in [(x2, y2, ang), (x1, y1, ang + math.pi)]:
        p1 = f"{tip_x:.1f},{tip_y:.1f}"
        p2 = f"{tip_x - s * math.cos(tip_ang - a):.1f},{tip_y - s * math.sin(tip_ang - a):.1f}"
        p3 = f"{tip_x - s * math.cos(tip_ang + a):.1f},{tip_y - s * math.sin(tip_ang + a):.1f}"
        e = root.ownerDocument.createElement("polygon")
        e.setAttribute("points", f"{p1} {p2} {p3}")
        e.setAttribute("class", "f")
        root.appendChild(e)


# ═══════════════════════════════════════════════════════
# 템플릿 1: 자료 제시형 구조도 (가장 흔한 유형)
# ═══════════════════════════════════════════════════════
def structure_diagram(groups, width_pt=320, height_pt=200):
    """여러 구역으로 나뉜 자료 제시형 도식.

    groups: [
      {"label": "갑", "items": ["자료1", "자료2"], "x": 30, "y": 30, "w": 120, "h": 80},
      ...
    ]
    """
    svg, r = _make_svg(width_pt, height_pt)
    _style_element(svg, r)

    for g in groups:
        x, y, w, h = g["x"], g["y"], g["w"], g["h"]
        rect(r, x, y, w, h, "l3")
        # 제목 (좌상단 바깥)
        label = g.get("label", "")
        if label:
            text(r, x - 8, y - 10, label, cls="tl")
        # 항목들
        items = g.get("items", [])
        n = len(items)
        for i, it in enumerate(items):
            iy = y + (i + 1) * h / (n + 1)
            text(r, x + w / 2, iy, it)

    return svg


# ═══════════════════════════════════════════════════════
# 템플릿 2: 순서도 (흐름도)
# ═══════════════════════════════════════════════════════
def flow(steps, width_pt=260, height_pt=None):
    """세로형 순서도.

    steps: ["A 과정", "B 과정", ...]
    """
    n = len(steps)
    if height_pt is None:
        height_pt = max(180, n * 42 + 35)
    svg, r = _make_svg(width_pt, height_pt)
    _style_element(svg, r)

    bw, bh = 90, 26
    cx = width_pt / 2
    gap = (height_pt - 30) / max(n, 1)

    for i, step in enumerate(steps):
        y = 15 + i * gap
        rect(r, cx - bw / 2, y, bw, bh)
        text(r, cx, y + bh / 2, step)
        if i < n - 1:
            arrow(r, cx, y + bh, cx, 15 + (i + 1) * gap)

    return svg


# ═══════════════════════════════════════════════════════
# 템플릿 3: 실험 장치도
# ═══════════════════════════════════════════════════════
def apparatus(parts, width_pt=360, height_pt=220):
    """화학/물리 실험 장치.

    parts: [{"shape":"beaker","x":100,"y":120,"w":36,"h":55,"label":"(가)"}, ...]
           shape: beaker, flask, tube, burner, stand, connector
    """
    svg, r = _make_svg(width_pt, height_pt)
    _style_element(svg, r)

    for p in parts:
        s = p["shape"]
        x, y = p.get("x", 0), p.get("y", 0)
        w, h = p.get("w", 30), p.get("h", 40)
        lbl = p.get("label", "")

        if s == "beaker":
            rect(r, x - w / 2, y - h, w, h)
            if lbl:
                text(r, x, y + 11, lbl)

        elif s == "flask":
            # 목 + 삼각플라스크
            neck_w = w * 0.25
            neck_h = h * 0.25
            rect(r, x - neck_w / 2, y - h, neck_w, neck_h)
            body_y = y - h + neck_h
            body_h = h - neck_h
            pts = f"{x - neck_w:.1f},{y - h + neck_h:.1f} {x + neck_w:.1f},{y - h + neck_h:.1f} {x + w / 2:.1f},{y:.1f} {x - w / 2:.1f},{y:.1f}"
            poly = r.ownerDocument.createElement("polygon")
            poly.setAttribute("points", pts)
            poly.setAttribute("class", "l")
            r.appendChild(poly)
            if lbl:
                text(r, x, y + 11, lbl)

        elif s == "tube":
            rect(r, x - w / 3, y - h, w * 2 / 3, h)

        elif s == "burner":
            # 버너 본체
            pts = f"{x - w / 2:.1f},{y:.1f} {x + w / 2:.1f},{y:.1f} {x + w / 4:.1f},{y - h:.1f} {x - w / 4:.1f},{y - h:.1f}"
            poly = r.ownerDocument.createElement("polygon")
            poly.setAttribute("points", pts)
            poly.setAttribute("class", "l")
            r.appendChild(poly)
            # 불꽃
            line(r, x, y - h, x, y - h - 14, cls="l2")
            # 심지 표시
            line(r, x - 3, y - h, x + 3, y - h, cls="l2")

        elif s == "stand":
            # 지지대
            line(r, x, y - h * 0.8, x, y, cls="l3")
            line(r, x - w / 2, y, x + w / 2, y, cls="l3")
            # 클램프
            line(r, x - 6, y - h * 0.65, x + 6, y - h * 0.65, cls="l2")
            # 삼각대
            line(r, x - w * 0.3, y - h * 0.65, x - w / 2, y + 6, cls="l2")
            line(r, x + w * 0.3, y - h * 0.65, x + w / 2, y + 6, cls="l2")

        elif s == "connector":
            line(r, p.get("x1", x), p.get("y1", y),
                 p.get("x2", x + 50), p.get("y2", y))

    return svg


# ═══════════════════════════════════════════════════════
# 템플릿 4: 그래프 (xy 좌표평면)
# ═══════════════════════════════════════════════════════
def graph(points_list, labels=None, width_pt=300, height_pt=210):
    """2차원 좌표평면 그래프. 여러 계열 가능.

    points_list: [[(x1,y1), (x2,y2), ...], [(x3,y3), ...]] — 각 계열
    labels: [(x_label, y_label), ...] 또는 단일 (x,y) 튜플
    """
    m = 38
    svg, r = _make_svg(width_pt, height_pt)
    _style_element(svg, r)

    ox, oy = m, height_pt - m
    gw, gh = width_pt - m * 2, height_pt - m * 2

    # 축
    arrow(r, ox - 6, oy, ox + gw + 6, oy)
    arrow(r, ox, oy + 6, ox, m - 6)

    # 눈금
    for i in range(1, 5):
        x = ox + i * gw / 4
        line(r, x, oy - 3, x, oy + 3, cls="l2")
        text(r, x, oy + 9, str(i * 25), cls="tc")
        y = oy - i * gh / 4
        line(r, ox - 3, y, ox + 3, y, cls="l2")
        text(r, ox - 10, y, str(i * 25), cls="tr")

    text(r, ox - 6, oy + 9, "O", cls="tc")

    # 데이터
    for pi, pts in enumerate(points_list):
        prev = None
        for i, (px, py) in enumerate(pts):
            dx = ox + (px / 100) * gw
            dy = oy - (py / 100) * gh
            if prev:
                line(r, prev[0], prev[1], dx, dy)
            dot(r, dx, dy)
            if labels and pi < len(labels) and i < len(labels[pi]):
                text(r, dx + 9, dy - 6, labels[pi][i], cls="tl")
            prev = (dx, dy)

    return svg


# ═══════════════════════════════════════════════════════
# 템플릿 5: 막대그래프
# ═══════════════════════════════════════════════════════
def bar(values, labels=None, width_pt=300, height_pt=200):
    """막대그래프.

    values: [45, 72, 38, 90, 55]
    labels: ["ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ"]
    """
    m = 38
    svg, r = _make_svg(width_pt, height_pt)
    _style_element(svg, r)

    ox, oy = m, height_pt - m
    gw, gh = width_pt - m * 2, height_pt - m * 2

    arrow(r, ox - 6, oy, ox + gw + 6, oy)
    arrow(r, ox, oy + 6, ox, m - 6)

    for i in range(1, 5):
        y = oy - i * gh / 4
        line(r, ox - 3, y, ox + 3, y, cls="l2")
        text(r, ox - 10, y, str(i * 25), cls="tr")

    text(r, ox - 6, oy + 9, "O", cls="tc")

    n = len(values)
    bar_w = gw / n * 0.55
    gap = gw / n * 0.45

    for i, v in enumerate(values):
        bx = ox + i * (bar_w + gap) + gap / 2
        bh = min((v / 100) * gh, gh)
        by = oy - bh
        rect(r, bx, by, bar_w, bh)
        if labels and i < len(labels):
            text(r, bx + bar_w / 2, oy + 11, labels[i])

    return svg


# ═══════════════════════════════════════════════════════
# 템플릿 6: 집합도 (벤 다이어그램)
# ═══════════════════════════════════════════════════════
def venn(sets, width_pt=280, height_pt=200):
    """벤 다이어그램.

    sets: [{"label":"A","x":110,"y":90,"r":60,"items":["a","b"]}, ...]
    """
    svg, r = _make_svg(width_pt, height_pt)
    _style_element(svg, r)

    for s in sets:
        cx, cy, cr = s["x"], s["y"], s.get("r", 50)
        circle(r, cx, cy, cr)
        text(r, cx, cy - cr - 8, s.get("label", ""))
        for item in s.get("items", []):
            dx, dy = 0, 0
            it = item
            if isinstance(it, dict):
                dx = it.get("dx", 0)
                dy = it.get("dy", 0)
                it = it.get("text", "")
            text(r, cx + dx, cy + dy, it)

    return svg


# ═══════════════════════════════════════════════════════
# 템플릿 7: 먹이그물 / 상호작용도
# ═══════════════════════════════════════════════════════
def network(nodes, edges, width_pt=320, height_pt=220):
    """노드-연결선 다이어그램.

    nodes: [{"label":"A","x":60,"y":100}, ...]
    edges: [(0, 1, {"style":"arrow"|"line"}), ...]
    """
    svg, r = _make_svg(width_pt, height_pt)
    _style_element(svg, r)

    for e in edges:
        frm, to = nodes[e[0]], nodes[e[1]]
        opts = e[2] if len(e) > 2 else {}
        if opts.get("style") == "arrow":
            arrow(r, frm["x"], frm["y"], to["x"], to["y"], cls="l2")
        else:
            line(r, frm["x"], frm["y"], to["x"], to["y"], cls="l2")

    for nd in nodes:
        cx, cy = nd["x"], nd["y"]
        circle(r, cx, cy, 17, cls="l")
        text(r, cx, cy, nd.get("label", ""), cls="tc")

    return svg


# ═══════════════════════════════════════════════════════
# 템플릿 8: 분류 체계도 (트리)
# ═══════════════════════════════════════════════════════
def tree(root_label, branches, width_pt=380, height_pt=None):
    """분류 체계도.

    branches: [
      ("분류1", [("항목A", 1), ("항목B", 2)]),
      ("분류2", [("항목C", 3), ("항목D", 4), ("항목E", 5)]),
    ]
    """
    n = len(branches)
    if height_pt is None:
        height_pt = max(200, n * 90 + 50)
    svg, r = _make_svg(width_pt, height_pt)
    _style_element(svg, r)

    rx, ry = width_pt / 2, 25
    bw, bh = 85, 22
    rect(r, rx - bw / 2, ry, bw, bh)
    text(r, rx, ry + bh / 2, root_label)

    total_h = height_pt - ry - bh - 30
    each_h = total_h / n if n else total_h

    for bi, (b_label, items) in enumerate(branches):
        by = ry + bh + 15 + bi * each_h
        bx = width_pt / 3
        rect(r, bx - bw / 2, by, bw, bh, cls="l2")
        text(r, bx, by + bh / 2, b_label)
        arrow(r, rx, ry + bh, bx, by)

        ni = len(items)
        for ii, (item, val) in enumerate(items):
            ix = width_pt * 2 / 3
            iy = by - (ni - 1) * 16 + ii * 32
            rect(r, ix - bw / 2, iy, bw, bh, cls="l2")
            text(r, ix, iy + bh / 2, f"{item}({val})")
            line(r, bx + bw / 2, by + bh / 2, ix - bw / 2, iy + bh / 2, cls="l2")

    return svg


# ═══════════════════════════════════════════════════════
# 템플릿 9: 비교 대조표
# ═══════════════════════════════════════════════════════
def comparison(headers, rows, width_pt=360, height_pt=None):
    """비교 표.

    headers: ["", "A", "B"]
    rows: [["항목1", "O", "X"], ["항목2", "X", "O"], ...]
    """
    nh = len(headers)
    nr = len(rows)
    if height_pt is None:
        height_pt = max(140, (nr + 1) * 26 + 20)
    svg, r = _make_svg(width_pt, height_pt)
    _style_element(svg, r)

    col_w = (width_pt - 30) / nh
    x0 = 15

    # 헤더
    for j, h in enumerate(headers):
        rx = x0 + j * col_w
        rect(r, rx, 10, col_w, 22)
        text(r, rx + col_w / 2, 21, h)

    # 구분선 (헤더 굵게)
    line(r, x0, 32, x0 + col_w * nh, 32, cls="l3")

    # 행
    for i, row in enumerate(rows):
        ry = 36 + i * 26
        for j, cell in enumerate(row):
            rx = x0 + j * col_w
            if j == 0:
                rect(r, rx, ry, col_w, 22, cls="l2")
            else:
                rect(r, rx, ry, col_w, 22, cls="l2")
            text(r, rx + col_w / 2, ry + 11, str(cell))

    return svg


# ═══════════════════════════════════════════════════════
# 템플릿 10: 순환도 (Cycle Diagram)
# ═══════════════════════════════════════════════════════
def cycle(steps, width_pt=280, height_pt=240):
    """순환 과정도.

    steps: ["과정1", "과정2", "과정3", "과정4"]
    """
    svg, r = _make_svg(width_pt, height_pt)
    _style_element(svg, r)

    cx, cy = width_pt / 2, height_pt / 2
    radius = min(cx, cy) - 35
    n = len(steps)
    bw, bh = 60, 22

    for i, step in enumerate(steps):
        angle = math.pi * 2 * i / n - math.pi / 2
        sx = cx + radius * math.cos(angle)
        sy = cy + radius * math.sin(angle)
        rect(r, sx - bw / 2, sy - bh / 2, bw, bh)
        text(r, sx, sy, step)

    # 화살표
    for i in range(n):
        a1 = math.pi * 2 * i / n - math.pi / 2
        a2 = math.pi * 2 * (i + 1) / n - math.pi / 2
        am = (a1 + a2) / 2
        # 선: 박스 바깥에서 다음 박스 바깥까지
        x1 = cx + (radius - bw / 2) * math.cos(a1)
        y1 = cy + (radius - bw / 2) * math.sin(a1)
        x2 = cx + (radius - bw / 2) * math.cos(a2)
        y2 = cy + (radius - bw / 2) * math.sin(a2)
        # 호 그리기 근사
        steps_i = 8
        pts = []
        for si in range(steps_i + 1):
            a = a1 + (a2 - a1) * si / steps_i
            pts.append(f"{cx + radius * math.cos(a):.1f},{cy + radius * math.sin(a):.1f}")
        e = r.ownerDocument.createElement("polyline")
        e.setAttribute("points", " ".join(pts))
        e.setAttribute("class", "l2")
        r.appendChild(e)
        # 화살촉
        arrow(r, (x1 + x2) / 2, (y1 + y2) / 2, x2, y2, cls="l2")

    return svg


def save_svg(svg, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg.toprettyxml(indent="  "))
