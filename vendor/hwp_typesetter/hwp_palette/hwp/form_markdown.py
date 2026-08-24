# -*- coding: utf-8 -*-
r"""양식/템플릿 → AI 프롬프트 (2026-07-25, 사용자 기획 18번).

원하는 작업 흐름:
    ① 여기서 만든 프롬프트를 복사해 AI(ChatGPT·Claude 등)에 붙여넣는다
    ② AI 가 빈칸 채움 답을 돌려준다 (아래 '답 형식')
    ③ 답을 복사해 한글에 붙여넣고 그 부분을 선택 → Ctrl+Alt+T (마크다운 변환)
    ④ 변환기가 \라벨\ 을 보고 **진짜 HWP 양식**을 삽입하며 빈칸을 채운다

핵심 설계: 양식을 마크다운으로 완벽히 재현할 필요가 없다.
    AI 에게는 **구조를 보여주기만** 하고(표 포함), 답은 변환기가 이미 아는
    문법(\라벨\ 한 줄 + 빈칸 채움 줄들)으로 받는다. 그래서 표 병합·서식이
    마크다운으로 표현이 안 돼도 **결과물은 원본 양식 그대로**다.

구조 추출: 한글의 GetTextFile("HWPML2X") — 표(<TABLE><ROW><CELL>)까지 담긴
XML 이다. XML 을 못 얻으면 순수 텍스트로만 만든다 (표가 줄글로 펴진다).
"""

import xml.etree.ElementTree as ET

from hwp_palette.core import applog
from hwp_palette.hwp import engine_library
from hwp_palette.model import form_fill                    # 자리 토큰 규칙(TOKEN_RE) 한 벌로

# 빈칸 표시 — 자리 토큰(form_fill.TOKEN_RE) 하나가 빈칸 하나다.
# AI 가 순서를 셀 수 있게 【빈칸1】 처럼 번호를 붙여 보여준다.
SLOT_MARK = "【빈칸{n}】"
# 이름표 자리(\학년\)는 이름을 같이 보여준다 — AI 가 무슨 칸인지 알 수 있게
NAMED_SLOT_MARK = "【빈칸{n}:{name}】"


def _para_text(p):
    """문단 하나의 글자 — CHAR 는 서식이 바뀌는 지점마다 쪼개질 뿐이라
    사이에 공백을 넣지 않고 붙인다. 공백을 넣으면 "학년" 이 "학 년" 이
    된다 (2026-07-31)."""
    return "".join(ch.text for ch in p.iter("CHAR") if ch.text)


def _cell_text(cell):
    """표 칸 안의 글자 (안에 또 표가 있으면 그 글자까지 평평하게).

    같은 문단 안의 CHAR 는 붙이고, 문단(P) 사이에만 공백을 둔다 —
    줄바꿈이 진짜 경계이기 때문이다.
    """
    parts = [_para_text(p) for p in cell.iter("P")]
    if not parts:                       # P 없이 CHAR 만 있는 별난 구조 대비
        parts = ["".join(ch.text for ch in cell.iter("CHAR") if ch.text)]
    return " ".join(" ".join(parts).split())


def _own_text(p, parents):
    """문단의 글자 — 안에 든 표의 글자는 뺀다 (표는 따로 그린다)."""
    parts = []
    for ch in p.iter("CHAR"):
        e = ch
        inside_table = False
        while e is not None and e is not p:
            if e.tag == "TABLE":
                inside_table = True
                break
            e = parents.get(e)
        if not inside_table and ch.text:
            parts.append(ch.text)
    # 같은 문단 안의 CHAR 는 서식 경계일 뿐 — 공백 없이 붙인다 (2026-07-31)
    return " ".join("".join(parts).split())


def _table_md(table):
    """<TABLE> → 마크다운 파이프 표. 병합은 펴진다 (구조 참고용이라 충분)."""
    rows = []
    for row in table.iter("ROW"):
        cells = [_cell_text(c) for c in row.iter("CELL")]
        if cells:
            rows.append(cells)
    if not rows:
        return []
    width = max(len(r) for r in rows)
    lines = []
    for i, r in enumerate(rows):
        r = r + [""] * (width - len(r))
        lines.append("| " + " | ".join(r) + " |")
        if i == 0:
            lines.append("|" + "---|" * width)
    return lines


def xml_to_markdown(xml_str):
    r"""HWPML2X → 구조 마크다운. 실패하면 예외 (호출부가 텍스트로 대체)."""
    root = ET.fromstring(xml_str)
    parents = {c: p for p in root.iter() for c in p}

    def inside_table(e):
        e = parents.get(e)
        while e is not None:
            if e.tag == "TABLE":
                return True
            e = parents.get(e)
        return False

    lines = []
    # ET 의 iter() 는 문서 순서 — 표 밖 문단과 최상위 표를 순서대로 늘어놓는다
    for e in root.iter():
        if e.tag == "P" and not inside_table(e):
            t = _own_text(e, parents)
            if t:
                lines.append(t)
        elif e.tag == "TABLE" and not inside_table(e):
            lines.extend(_table_md(e))
            lines.append("")
    return "\n".join(lines).strip()


def _number_slots(md):
    r"""빈칸 자리에 순서 번호를 붙인다. (본문 표시 \본문\ 은 빈칸이 아니다)

    자리 하나 = form_fill.TOKEN_RE 토큰 하나다 — `\\` 도 `\학년\` 도 자리
    **하나**다. 예전엔 역슬래시 낱개마다 번호를 붙여서, 이름표 하나가 빈칸
    둘로 잡히고 개수가 채우는 쪽(library.count_slots)의 두 배로 부풀려졌다 —
    AI 답 줄이 밀려 둘째 답부터 엉뚱한 칸에 들어갔다 (2026-07-31 수정).
    """
    md = md.replace(engine_library.BODY_ANCHOR, "〔여기부터 본문〕")
    count = [0]

    def rep(m):
        name = m.group(1)
        if name in form_fill.RESERVED_NAMES:
            return m.group(0)           # \본문\ — 빈칸이 아니다
        count[0] += 1
        if name:
            return NAMED_SLOT_MARK.format(n=count[0], name=name)
        return SLOT_MARK.format(n=count[0])

    return form_fill.TOKEN_RE.sub(rep, md), count[0]


def build_structure_md(path):
    """조각/양식 파일 → (구조 마크다운, 빈칸 수). 표 실패 시 텍스트로."""
    xml, text = engine_library.read_file_structure(path)
    md = ""
    if xml:
        try:
            md = xml_to_markdown(xml)
        except Exception as e:
            applog.exc("HWPML2X 파싱 실패 — 텍스트로만 프롬프트 생성", e)
    if not md:
        md = "\n".join(" ".join(ln.split()) for ln in (text or "").splitlines()
                       if ln.strip())
    return _number_slots(md)


def build_prompt(name, label, structure_md, slot_count):
    """AI 에 붙여넣을 프롬프트 전문. 답 형식이 곧 마크다운 변환 문법이다."""
    slot_lines = "\n".join(f"(빈칸{i}에 들어갈 내용)"
                           for i in range(1, min(slot_count, 3) + 1))
    if slot_count > 3:
        slot_lines += "\n..."
    return (
        f"다음은 한글(HWP) 문서 양식 '{name}' 의 구조입니다.\n"
        f"【빈칸N】 이 내용을 채워야 할 자리이고, 모두 {slot_count}개입니다.\n"
        "표 병합·서식은 생략된 참고용 구조입니다.\n"
        "\n"
        "----- 양식 구조 -----\n"
        f"{structure_md}\n"
        "----- 구조 끝 -----\n"
        "\n"
        "위 양식의 빈칸에 들어갈 내용을 정한 뒤, 아래 형식 **그대로만** 답하세요.\n"
        f"첫 줄은 \\{label}\\ 그대로 쓰고, 둘째 줄부터 빈칸 1번부터 순서대로 "
        "한 줄에 하나씩 씁니다.\n"
        "비울 칸에는 - 만 씁니다. 다른 설명·인사말은 쓰지 않습니다.\n"
        "\n"
        f"\\{label}\\\n"
        f"{slot_lines}\n"
    )
