# -*- coding: utf-8 -*-
r"""시험문제 조판 엔진 — 발문·자료박스·보기박스·선지 표.

hwp_palette 는 원래 시험 출제 도구(exam_scribe)에서 출발했고, 이 모듈이 그
유산이다. 지금의 '팔레트' 기능(문자/템플릿/기능 블럭, \라벨\ 변환)과는
성격이 달라 hwp_engine 에서 분리했다. 마크다운의 시험문제 문법
(발문:/자료:/질문:/보기:/선지:)을 만났을 때만 쓰인다.

표/박스의 모든 치수·글꼴·테두리는 활성 스펙(hwp_engine.S)에서 읽는다.
한글 조작은 전부 hwp_engine 의 것을 그대로 쓴다(연결 인스턴스를 공유하기 위함).
"""

from hwp_palette.hwp import hwp_engine
from hwp_palette.hwp.hwp_engine import (
    _col_width_mm, _create_table, _exit_table, _set_cell_border, _text,
)


def _act():
    return hwp_engine.hwp.HAction


def _ps():
    return hwp_engine.hwp.HParameterSet


# ── 자료박스 ───────────────────────────────────────────
def insert_material_box(text):
    """자료: - 단 폭, 2행2열, 각 셀 테두리는 설정값(기본 투명)"""
    act = _act()
    ps  = _ps()
    box = hwp_engine.S["material_box"]
    bt  = hwp_engine.S["border"]["material_type"]
    _create_table(2, 2, _col_width_mm(),
                  [box["row1_height_mm"], box["row2_height_mm"]])

    def _cell():
        act.Run("TableCellBlock")
        _set_cell_border(act, ps, bt, bt, bt, bt)
        act.Run("Cancel")

    _cell()                     # 1행1열 (현재 위치)
    act.Run("TableRightCell"); _cell()   # 1행2열
    act.Run("TableRightCell"); _cell()   # 2행1열
    act.Run("TableRightCell"); _cell()   # 2행2열
    act.Run("TableLeftCell")             # 1행1열로 복귀
    act.Run("TableLeftCell")
    act.Run("TableLeftCell")
    _text(text)
    _exit_table(act)


def insert_photo_box():
    """사진자료: - 단 폭, 2행2열, 전체 투명 + 중앙정렬"""
    act = _act()
    ps  = _ps()
    box = hwp_engine.S["photo_box"]
    _create_table(2, 2, _col_width_mm(),
                  [box["row1_height_mm"], box["row2_height_mm"]])

    def _cell_center():
        act.Run("TableCellBlock")
        _set_cell_border(act, ps, "None", "None", "None", "None")
        act.Run("Cancel")
        act.Run("ParagraphShapeAlignCenter")

    _cell_center()
    act.Run("TableRightCell"); _cell_center()
    act.Run("TableRightCell"); _cell_center()
    act.Run("TableRightCell"); _cell_center()
    _exit_table(act)


def insert_experiment_box():
    """실험자료: - 단 폭 1칸, 설정 높이, 설정 테두리, 안내 문구"""
    act = _act()
    ps  = _ps()
    box = hwp_engine.S["experiment_box"]
    bt  = hwp_engine.S["border"]["experiment_type"]
    _create_table(1, 1, _col_width_mm(), [box["height_mm"]])
    act.Run("TableCellBlock")
    _set_cell_border(act, ps, bt, bt, bt, bt)
    act.Run("Cancel")
    _text(box["label"])
    _exit_table(act)


# ── 보기박스 ───────────────────────────────────────────
def insert_bogi_box(items=None):
    """〈보 기〉 박스 — 3행 5열 표를 병합해 만든다. 치수·테두리·여백·줄간격은 설정값."""
    act = _act()
    ps  = _ps()
    box = hwp_engine.S["bogi_box"]
    bt  = hwp_engine.S["border"]["bogi_type"]

    _create_table(3, 5, _col_width_mm(),
                  [box["title_height_mm"], box["gap_height_mm"],
                   box["content_height_mm"]])

    # 전체 테두리 삭제
    act.Run("TableCellBlock")
    act.Run("TableCellBlockExtend")
    act.Run("TableCellBlockExtend")
    _set_cell_border(act, ps, "None", "None", "None", "None")
    act.GetDefault("CellBorderFill", ps.HCellBorderFill.HSet)
    ps.HCellBorderFill.TypeVert = hwp_engine.hwp.HwpLineType("None")
    ps.HCellBorderFill.TypeHorz = hwp_engine.hwp.HwpLineType("None")
    act.Execute("CellBorderFill", ps.HCellBorderFill.HSet)
    act.Run("Cancel")

    # 2~3행 바깥 테두리 (설정 테두리 종류)
    act.Run("TableCellBlock")
    act.Run("TableCellBlockExtend")
    act.Run("TableUpperCell")
    act.Run("TableLeftCell")
    act.Run("TableLeftCell")
    act.Run("TableLeftCell")
    act.Run("TableLeftCell")
    _set_cell_border(act, ps, bt, bt, bt, bt)
    act.Run("Cancel")

    # 3열 병합 후 〈보 기〉
    act.Run("TableRightCell")
    act.Run("TableRightCell")
    act.Run("TableCellBlock")
    act.Run("TableCellBlockExtend")
    act.Run("TableMergeCell")
    act.Run("ParagraphShapeAlignCenter")
    _text(box["title"])

    # 3행 전체 병합 후 ㄱㄴㄷ
    act.Run("TableLowerCell")
    act.Run("TableLeftCell")
    act.Run("TableLeftCell")
    act.Run("TableCellBlock")
    act.Run("TableCellBlockExtend")
    act.Run("TableRightCell")
    act.Run("TableRightCell")
    act.Run("TableRightCell")
    act.Run("TableRightCell")
    act.Run("TableMergeCell")

    # ㄱㄴㄷ 입력 — 셀 여백/줄간격 설정
    hwp_engine.hwp.set_cell_margin(
        left=box["cell_margin_left_mm"], right=box["cell_margin_right_mm"],
        top=box["cell_margin_top_mm"], bottom=box["cell_margin_bottom_mm"],
        as_="mm")
    act.Run("ParagraphShapeAlignJustify")
    act.GetDefault("ParagraphShape", ps.HParaShape.HSet)
    ps.HParaShape.LineSpacing     = box["line_spacing"]
    ps.HParaShape.LineSpacingType = 0
    act.Execute("ParagraphShape", ps.HParaShape.HSet)

    if items:
        labels = ["ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ"]
        real = [it for it in items if it is not None and str(it).strip()]
        for i, item in enumerate(real[:5]):
            if i > 0:
                act.Run("BreakPara")
            _text(f"{labels[i]}. ")
            act.Run("Indent")   # Alt+Shift+Tab 들여쓰기
            _text(item)
    else:
        for i, label in enumerate(["ㄱ", "ㄴ", "ㄷ"]):
            if i > 0:
                act.Run("BreakPara")
            _text(f"{label}. ")
            act.Run("Indent")

    # 1행3열+2행3열 병합 → 〈보기〉 위치 확정
    act.Run("TableColPageUp")
    act.Run("TableColBegin")
    act.Run("TableRightCell")
    act.Run("TableRightCell")
    act.Run("TableCellBlock")
    act.Run("TableCellBlockExtend")
    act.Run("TableLowerCell")
    act.Run("TableMergeCell")

    _exit_table(act)


# ── 발문/자료/질문/보기/선지 통합 삽입 ─────────────────
def insert_question(data, num=1, use_num=True):
    """반환값: 문항번호 자동증가가 필요하면 True (data['num']이 비어있고 use_num=True)"""
    act = _act()
    stem_cfg = hwp_engine.S["stem"]

    if data['stem']:
        ps_s = _ps()
        display_num = data['num'] or str(num)
        act.GetDefault("ParagraphShape", ps_s.HParaShape.HSet)
        ps_s.HParaShape.LeftMargin    = 0
        ps_s.HParaShape.RightMargin   = 0
        ps_s.HParaShape.Indentation   = stem_cfg["indentation"]
        ps_s.HParaShape.LineSpacing   = stem_cfg["line_spacing"]
        ps_s.HParaShape.AlignType     = 0
        act.Execute("ParagraphShape", ps_s.HParaShape.HSet)
        if use_num:
            _text(f"{display_num}. {data['stem']}")
        else:
            _text(data['stem'])
        act.Run("BreakPara")
        act.GetDefault("ParagraphShape", ps_s.HParaShape.HSet)
        ps_s.HParaShape.Indentation   = 0
        act.Execute("ParagraphShape", ps_s.HParaShape.HSet)

    # 자료박스
    mtype = data.get('material_type', 'basic')
    if mtype == 'photo':
        insert_photo_box()
    elif mtype == 'experiment':
        insert_experiment_box()
    elif mtype == 'basic' and data.get('material_flag'):
        insert_material_box(data['material'])

    # 질문
    if data['question']:
        q_cfg = hwp_engine.S["question"]
        ps2 = _ps()
        act.GetDefault("ParagraphShape", ps2.HParaShape.HSet)
        ps2.HParaShape.Indentation = 0
        ps2.HParaShape.PrevSpacing = q_cfg["prev_spacing"]
        ps2.HParaShape.NextSpacing = q_cfg["next_spacing"]
        act.Execute("ParagraphShape", ps2.HParaShape.HSet)
        _text("  " + data['question'])
        act.GetDefault("ParagraphShape", ps2.HParaShape.HSet)
        ps2.HParaShape.Indentation = 0
        ps2.HParaShape.PrevSpacing = 0
        ps2.HParaShape.NextSpacing = 0
        act.Execute("ParagraphShape", ps2.HParaShape.HSet)
        act.Run("BreakPara")

    # 보기박스 — 출제자가 쓴 순서 그대로. 예전엔 글자 수 짧은 순으로 다시
    # 정렬했는데, 그러면 출제자가 ㄱ으로 쓴 진술이 ㄷ 라벨로 찍혀
    # 정답표가 조용히 어긋난다 (2026-07-31 제거)
    if data['bogi']:
        insert_bogi_box(data['bogi'])

    # 선지
    if data['choices']:
        _insert_choices(data)

    return use_num and not data['num']


_JAMO_ORDER = ['ㄱ','ㄴ','ㄷ','ㄹ','ㅁ','ㅂ','ㅅ','ㅇ','ㅈ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ']


def _fmt_choice(c):
    r"""순수 자모 선지("ㄱ,ㄴ")만 "ㄱ, ㄴ" 꼴로 고르게 다듬는다.

    자모와 구분자(쉼표·가운뎃점·공백)만으로 된 선지는 쉼표+공백으로 통일하고,
    "ㄱ만 옳다" 처럼 다른 글자가 섞이면 출제자가 쓴 그대로 둔다 — 예전엔
    자모가 하나라도 있으면 나머지 글자를 전부 버려서, "ㄱ만 옳다" 가
    시험지에 "ㄱ" 으로 찍히는 사고가 있었다 (2026-07-31).
    """
    s = c.strip()
    jamo = [ch2 for ch2 in s if ch2 in _JAMO_ORDER]
    pure = bool(jamo) and all(
        ch2 in _JAMO_ORDER or ch2 in ",·、" or ch2.isspace() for ch2 in s)
    return ', '.join(jamo) if pure else s


def _insert_choices(data):
    """선지 - 표 기반 배치 (단 폭 균등 분할, 줄바꿈 방지)"""
    act = _act()
    ps  = _ps()
    circles = ["①", "②", "③", "④", "⑤"]
    ctype = data.get('choices_type', '5')
    row_h = hwp_engine.S["choices"]["row_height_mm"]
    total_mm = _col_width_mm()

    ch = [_fmt_choice(c) for c in data['choices'][:5]]
    parts = [f"{circles[i]} {c}" for i, c in enumerate(ch)]

    def _draw_choice_table(rows, cols, layout):
        """rows×cols 투명 표. layout: {(행,열): part}. 칸은 cols 균등 분할."""
        _create_table(rows, cols, total_mm, [row_h] * rows)

        def _trans_and_text(txt):
            act.Run("TableCellBlock")
            _set_cell_border(act, ps, "None", "None", "None", "None")
            act.Run("Cancel")
            act.Run("ParagraphShapeAlignLeft")
            if txt:
                _text(txt)

        for r in range(rows):
            for c in range(cols):
                _trans_and_text(layout.get((r, c), ""))
                if not (r == rows - 1 and c == cols - 1):
                    act.Run("TableRightCell")
        _exit_table(act)

    if ctype == '1':
        act.GetDefault("ParagraphShape", ps.HParaShape.HSet)
        ps.HParaShape.Indentation = 0
        ps.HParaShape.PrevSpacing = 0
        ps.HParaShape.NextSpacing = 0
        ps.HParaShape.LeftMargin  = 0
        ps.HParaShape.AlignType   = 0
        act.Execute("ParagraphShape", ps.HParaShape.HSet)
        for i, p in enumerate(parts):
            if i > 0:
                act.Run("BreakPara")
            _text(p)
        act.Run("BreakPara")
    elif ctype == '3':
        layout = {(0, 0): parts[0], (0, 1): parts[1], (0, 2): parts[2]}
        if len(parts) > 3:
            layout[(1, 0)] = parts[3]
        if len(parts) > 4:
            layout[(1, 1)] = parts[4]
        _draw_choice_table(2, 3, layout)
        act.Run("BreakPara")
    else:  # '5'
        layout = {(0, i): parts[i] for i in range(len(parts))}
        _draw_choice_table(1, 5, layout)
        act.Run("BreakPara")
