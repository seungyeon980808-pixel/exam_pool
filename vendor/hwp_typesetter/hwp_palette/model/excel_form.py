# -*- coding: utf-8 -*-
r"""문항 엑셀 — 작성 서식 정의와 빈 양식 만들기.

왜 엑셀인가 (2026-07-29 사용자 기획):
    마크다운 문법이 완성돼도 **그 문법을 치는 것 자체를 힘들어할 사람**이 있다.
    반면 선생님들은 나이스에서 엑셀을 내려받아 채우고 올리는 리듬에 이미 익숙하다.
    그 리듬을 그대로 빌린다 — 양식 만들기 → 엑셀에서 채우기 → 불러오기 → 변환.

**한 줄 = 한 문항.** 여러 문항이 한 표에 놓여야 비교·정렬·복사가 된다.
한 화면에 들어오게 열을 10개로 묶었다(총 폭 180자). 그 대신 〈보기〉 셋과
선지 다섯은 각각 **한 칸에 여러 줄**로 넣는다 — 시험지에 찍히는 모양 그대로다.

    보기 칸                        선지 칸
    맨틀은 지구 전체 부피의 …      ㄱ
    외핵은 액체 상태로 …           ㄴ
    내핵은 지각보다 …              ㄱ, ㄴ

**ㄱ. ㄴ. ㄷ. 와 ① ~ ⑤ 는 템플릿 조각(*.hwp)에 이미 인쇄돼 있다.** 빈칸은 그
뒤의 `\` 뿐이라, 여기서 또 쓰면 시험지에 두 번 찍힌다. 내용만 쓰게 하고,
습관대로 붙여 쓴 말머리는 excel_read 가 떼어낸다.

빈칸 순서(slots)로 옮기는 일은 excel_read.py 가 감춘다.
"""

from hwp_palette.core import applog
from hwp_palette.model import library

# ===== 템플릿 빈칸 지도 =====================================================
# **추측하지 않았다** — fragments/*.hwp 를 직접 열어 확인한 순서다.
#   학교합답1사진5선지  1473080b86d745f99e7dea0077c81507.hwp  (12칸, 배점 칸 없음)
#   학교합답2사진5선지  223cb667f0984742a2016dbc9dbc1319.hwp  (14칸)
#   학교정답0사진1선지  f7fbdd7ad2684f14b3e4a4618fdc4547.hwp  (8칸)
#   합답형1사진3선지    54c6380dd2d545efb68214a5bfa5a5c7.hwp  (12칸)
#   합답형2사진3선지    1d64d6825a3742f7ac62946b980d9205.hwp  (13칸)
#   합답형실험3선지     7e0d6662e63340089369f353124761bd.hwp  (11칸)
#
# 여기 없는 템플릿은 스타일 목록에 뜨지 않는다. 빈칸 **개수**는 라이브러리가
# 알지만 **각 칸이 무슨 자리인지**는 조각을 열어 보기 전에는 알 수 없어서다.
TEMPLATES = {
    "학교합답1사진5선지": ["번호", "지문", "사진1", "발문",
                           "보기1", "보기2", "보기3",
                           "선1", "선2", "선3", "선4", "선5"],
    "학교합답2사진5선지": ["번호", "지문", "사진1", "사진2", "발문", "배점",
                           "보기1", "보기2", "보기3",
                           "선1", "선2", "선3", "선4", "선5"],
    "학교정답0사진1선지": ["번호", "발문", "배점",
                           "선1", "선2", "선3", "선4", "선5"],
    "합답형1사진3선지":   ["번호", "지문", "사진1", "배점",
                           "보기1", "보기2", "보기3",
                           "선1", "선2", "선3", "선4", "선5"],
    "합답형2사진3선지":   ["번호", "지문", "사진1", "사진2", "배점",
                           "보기1", "보기2", "보기3",
                           "선1", "선2", "선3", "선4", "선5"],
    "합답형실험3선지":    ["번호", "지문", "배점",
                           "보기1", "보기2", "보기3",
                           "선1", "선2", "선3", "선4", "선5"],
}

# ===== 스타일 — 유형마다 '어떤 틀로 뽑을지' =================================
# 스타일 하나 = {사진 장수: 템플릿 라벨}. 사진 장수는 자료 칸을 보고 정해지므로
# 사용자는 계열만 고르면 된다.
#
# 사진 없는 합답형에 1사진 틀을 쓰는 것은 **임시**다 — 그림 자리가 빈 칸으로
# 남는다. '학교합답0사진5선지' 조각을 등록하면 0 자리만 고치면 된다.
STYLES = {
    "합답형": [
        ("학교 5지선다", {0: "학교합답1사진5선지", 1: "학교합답1사진5선지",
                          2: "학교합답2사진5선지"}),
        ("수능형 3선지", {0: "합답형실험3선지", 1: "합답형1사진3선지",
                          2: "합답형2사진3선지"}),
    ],
    "정답형": [
        ("학교 5지선다", {0: "학교정답0사진1선지", 1: "학교정답0사진1선지",
                          2: "학교정답0사진1선지"}),
    ],
    # 서술형 조각이 아직 없다. 평문 + \표1*1\ 답란으로 만든다(excel_read).
    "서술형": [
        ("평문 + 답란 표", {0: "", 1: "", 2: ""}),
    ],
}
QTYPES = list(STYLES)


def slots_of(label):
    r"""라벨 → 그 템플릿의 **빈칸 이름 목록**.

    2026-07-31: 여기가 이 기능의 진짜 병목이었다. 아래 TEMPLATES 는 조각
    이름과 각 빈칸의 뜻을 **코드에 직접 적어 둔 표**라, 거기 없는 템플릿은
    문항 엑셀에 참여할 수 없었다("여기 없는 템플릿은 스타일 목록에 뜨지
    않는다"). 선생님이 만든 템플릿은 물론이고, 문서 해체(015)로 시험지에서
    꺼낸 문항 틀도 마찬가지였다 — 만들기가 쉬워질수록 이 벽이 더 아프다.

    이제 **라이브러리를 먼저 본다.** 조각에 `\번호\ \발문\ \선1\` 처럼 이름
    붙은 빈칸이 있으면 그 이름들이 곧 열 지도다(등록할 때 slot_names 로 이미
    적힌다 — 새 데이터가 필요하지 않다). 이름표가 없는 옛 조각만 아래 표로
    떨어진다.
    """
    try:
        entry = library.label_lookup().get(label)
        if entry and entry[0] in ("템플릿", "양식"):
            names = [n for n in (entry[1].get("slot_names") or []) if n]
            # 빈칸 수와 이름 수가 같을 때만 믿는다 — 일부만 이름이 붙어
            # 있으면 순서가 어긋나 값이 엉뚱한 칸으로 간다.
            if names and len(names) == int(entry[1].get("slot_count") or 0):
                return names
    except Exception as e:
        applog.exc("슬롯 읽기 실패", e)
    return TEMPLATES.get(label, [])


def styles_for(qtype):
    """그 유형에서 고를 수 있는 스타일 이름들 — **라이브러리에 실제 등록된 것만.**

    등록 안 된 라벨을 골라 두면 변환할 때 "등록되지 않은 라벨"로 조용히 실패한다.
    고르는 자리에서 미리 걸러낸다.
    """
    have = set(library.label_lookup())
    out = []
    for name, mapping in STYLES.get(qtype, []):
        labels = {v for v in mapping.values() if v}
        if not labels or labels <= have:
            out.append(name)
        elif labels:
            applog.warn(f"스타일 '{name}'의 일부 라벨이 없어 숨깁니다 "
                        f"(없는 라벨: {labels - have})")
    return out or [STYLES[qtype][0][0]]


def mapping_of(qtype, style_name):
    for name, mapping in STYLES.get(qtype, []):
        if name == style_name:
            return mapping
    return STYLES[qtype][0][1] if STYLES.get(qtype) else {}


def default_styles():
    return {q: styles_for(q)[0] for q in QTYPES}


# ===== 시트 · 열 ===========================================================
SHEET = "문항"
LIST_SHEET = "목록"        # 사진 이름 + 고른 스타일을 숨겨 두는 시트

# 열 = (제목, 폭, 무리). 무리는 색으로 묶어 어디가 어디인지 눈에 띄게 한다.
COLUMNS = [
    ("번호",   5,  "딴"),
    ("배점",   5,  "딴"),
    ("유형",   8,  "딴"),
    ("지문",  38,  "문제"),
    ("자료",  12,  "문제"),
    ("발문",  38,  "문제"),
    ("보기",  34,  "보기"),
    ("선지",  22,  "선지"),
    ("정답",   6,  "딴"),
    ("메모",  12,  "딴"),
]
HEADERS = [c[0] for c in COLUMNS]
CENTER = {"번호", "배점", "유형", "정답"}

GROUP_COLOR = {"딴": "57606A", "문제": "0969DA", "보기": "0E7490", "선지": "1A7F37"}
GROUP_TINT = {"딴": "F6F8FA", "문제": "F2F7FD", "보기": "F0F7F8", "선지": "F2F8F3"}

# 목록에서 고르게 할 열 — True 면 목록 밖 값을 막는다
CHOICE_LISTS = {
    "유형": (QTYPES, True),
    "정답": (["①", "②", "③", "④", "⑤"], False),
    "배점": (["2", "3", "4", "5", "6"], False),
}

READ_ME = [
    ("이 파일 쓰는 법", ""),
    ("", ""),
    ("1", "한 줄이 한 문항이다. 문항 시트에 한 줄씩 채워 나간다."),
    ("2", "유형·정답·배점·자료 칸은 누르면 목록이 뜬다. 골라 쓰면 된다."),
    ("", "   ① 이나 ③ 같은 기호를 직접 칠 일이 없다."),
    ("3", "〈보기〉 세 개는 '보기' 칸 하나에 Alt+Enter 로 줄을 나눠 넣는다."),
    ("", "   선지 다섯 개도 '선지' 칸 하나에 같은 방법으로 넣는다."),
    ("", "   ★ ㄱ. ㄴ. ㄷ. 와 ① ~ ⑤ 는 시험지 틀에 이미 있다. 내용만 쓴다."),
    ("4", "지문이 여러 줄이면 지문 칸에서 Alt+Enter 로 줄을 바꾼다."),
    ("5", "자료 칸에는 그림 이름만 적는다. 확장자(.png)도, 폴더 경로도 쓰지 않는다."),
    ("", "   팔레트에 연결된 사진 폴더의 이름이 목록으로 뜬다."),
    ("", "   두 장이면 쉼표로 나눠 직접 친다 → 굴절실험, 결과표"),
    ("6", "빈 칸은 그냥 비워 둔다. 도구가 건너뛴다."),
    ("7", "번호는 시험지에 나갈 순서다. 번호 순으로 정렬해 내보낸다."),
    ("8", "회색 열(번호·배점·유형·정답·메모)은 시험지에 안 나간다. 정답표에 쓴다."),
    ("", ""),
    ("다 채운 뒤", "팔레트의 [문항 엑셀] → '채운 엑셀 불러오기' 를 누른다."),
    ("", ""),
    ("아는 사람만", "굵게 하려면 \\굵게{옳지 않은} — 안 써도 그만이다."),
    ("", ""),
    ("주의", "사진이 없는 합답형은 그림 자리가 빈 칸으로 남는다. 한글에서 지우면 된다."),
]

# ===== 예시 문항 (2학년 과학 — 지구 내부·빛·광물·암석·풍화) ===============
# 순서: 번호 배점 유형 지문 자료 발문 보기 선지 정답 메모
SAMPLES = [
    [1, 3, "합답형",
     "지구 내부는 지각, 맨틀, 외핵, 내핵으로 나뉜다.\n"
     "다음은 각 층의 상태와 부피에 대한 자료이다.",
     "지구내부구조",
     "이에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은?",
     "맨틀은 지구 전체 부피의 약 80 %를 차지한다.\n"
     "외핵은 액체 상태로 추정된다.\n"
     "내핵은 지각보다 온도가 낮다.",
     "ㄱ\nㄴ\nㄱ, ㄴ\nㄴ, ㄷ\nㄱ, ㄴ, ㄷ",
     "③", "지구 내부 구조"],

    [2, 4, "합답형",
     "빛이 서로 다른 매질을 지날 때의 성질을 알아보는 실험이다.\n"
     "[실험 과정]\n"
     "(가) 매질 A, B를 맞대어 놓는다.\n"
     "(나) A에서 B로 빛을 45°로 입사시키고 굴절각을 측정한다.\n"
     "(다) B에서 A로 빛을 30°로 입사시키고 굴절각을 측정한다.",
     "굴절실험",
     "이에 대한 설명으로 \\굵게{옳지 않은} 것만을 <보기>에서 있는 대로 고른 것은?",
     "(나)에서 굴절각은 45°보다 작다.\n"
     "빛의 속력은 A에서가 B에서보다 크다.\n"
     "(다)에서 빛은 법선에 가까워지며 꺾인다.",
     "ㄱ\nㄷ\nㄱ, ㄴ\nㄴ, ㄷ\nㄱ, ㄴ, ㄷ",
     "②", "부정 발문"],

    [3, 3, "합답형",
     "다음은 광물 A, B의 특징을 조사한 것이다.\n"
     "· A: 색은 무색, 조흔색은 흰색, 자성이 없다.\n"
     "· B: 색은 검은색, 조흔색은 검은색, 자석에 붙는다.",
     "",
     "A와 B에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은?",
     "A는 석영일 가능성이 높다.\n"
     "B는 자철석일 가능성이 높다.\n"
     "조흔색은 광물의 겉색과 항상 같다.",
     "ㄱ\nㄷ\nㄱ, ㄴ\nㄴ, ㄷ\nㄱ, ㄴ, ㄷ",
     "③", "사진 없는 합답형"],

    [4, 3, "정답형",
     "마그마가 식어 굳으면 화성암이 된다.\n화성암은 식은 장소와 속도에 따라 나뉜다.",
     "",
     "화성암에 대한 설명으로 옳은 것은?",
     "",
     "현무암은 땅속 깊은 곳에서 천천히 식어 만들어진다.\n"
     "화강암은 알갱이의 크기가 매우 작다.\n"
     "천천히 식을수록 알갱이가 크게 자란다.\n"
     "화산암은 모두 밝은색을 띤다.\n"
     "화성암은 퇴적물이 굳어 만들어진다.",
     "③", "화성암"],

    [5, 5, "서술형",
     "지표의 암석은 물과 공기, 생물의 작용으로 잘게 부서진다.",
     "",
     "위와 같이 암석이 부서지는 현상을 무엇이라 하는지 쓰고, 그 원인을 두 가지 서술하시오.",
     "",
     "",
     "", "채점: 풍화 2점 + 원인 각 1.5점"],
]

EMPTY_ROWS = 25          # 이어서 쓸 빈 줄


def build_workbook(path, styles=None, with_samples=True):
    """양식 파일을 만든다.

    styles: {유형: 스타일 이름}. 고른 것을 파일 안(숨긴 시트)에 적어 둔다 —
            **엑셀 하나가 자기 스타일을 안고 다녀야** 나중에 읽을 때 같은
            틀로 나간다.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    styles = styles or default_styles()
    wb = Workbook()

    # --- 읽어보세요 ------------------------------------------------------
    ws = wb.active
    ws.title = "읽어보세요"
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 92
    for r, (a, b) in enumerate(READ_ME, start=1):
        ws.cell(r, 1, a).font = Font(
            bold=(a in ("이 파일 쓰는 법", "주의", "아는 사람만", "다 채운 뒤")))
        ws.cell(r, 2, b)
    ws.cell(1, 1).font = Font(bold=True, size=14)

    # --- 문항 ------------------------------------------------------------
    ws = wb.create_sheet(SHEET)
    ws.freeze_panes = "D2"          # 머리줄 + 번호·배점·유형 열 고정
    ws.sheet_view.showGridLines = False

    thin = Side(style="thin", color="D0D7DE")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)

    for i, (name, width, group) in enumerate(COLUMNS, start=1):
        c = ws.cell(1, i, name)
        c.fill = PatternFill("solid", fgColor=GROUP_COLOR[group])
        c.font = Font(bold=True, color="FFFFFF")
        c.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[1].height = 24

    rows = SAMPLES if with_samples else []
    for r, row in enumerate(rows, start=2):
        _put_row(ws, r, row, box)
        ws.row_dimensions[r].height = 96

    last = 1 + len(rows) + EMPTY_ROWS
    for r in range(2 + len(rows), last + 1):
        _put_row(ws, r, [""] * len(COLUMNS), box)
        ws.row_dimensions[r].height = 96

    _add_lists(wb, ws, last, styles)
    wb.save(path)
    return path


def _put_row(ws, r, row, box):
    from openpyxl.styles import Alignment, PatternFill

    for i, (name, _w, group) in enumerate(COLUMNS, start=1):
        c = ws.cell(r, i, row[i - 1] if i - 1 < len(row) else "")
        c.fill = PatternFill("solid", fgColor=GROUP_TINT[group])
        c.border = box
        c.alignment = Alignment(
            wrap_text=True, vertical="top",
            horizontal="center" if name in CENTER else "left")


def _add_lists(wb, ws, last_row, styles):
    """유형·정답·배점은 목록에서 고르게, 자료는 사진 이름을 목록으로 준다.

    ① 이나 ③ 같은 기호를 손으로 칠 일이 없게 하는 것이 목적이다.
    고른 스타일도 같은 숨긴 시트에 적어 둔다(excel_read 가 읽는다).
    """
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    def col_of(name):
        return get_column_letter(HEADERS.index(name) + 1)

    for name, (values, strict) in CHOICE_LISTS.items():
        dv = DataValidation(type="list", formula1='"%s"' % ",".join(values),
                            allow_blank=True, showErrorMessage=strict)
        dv.error = "목록에서 골라 주세요"
        ws.add_data_validation(dv)
        dv.add("%s2:%s%d" % (col_of(name), col_of(name), last_row))

    lst = wb.create_sheet(LIST_SHEET)
    lst.sheet_state = "hidden"

    # A열 — 사진 이름. 개수를 모르니 목록 대신 이 범위를 참조한다.
    lst.cell(1, 1, "사진 이름")
    photos = photo_names()
    for i, name in enumerate(photos, start=2):
        lst.cell(i, 1, name)
    if photos:
        dv = DataValidation(
            type="list",
            formula1="=%s!$A$2:$A$%d" % (LIST_SHEET, len(photos) + 1),
            allow_blank=True, showErrorMessage=False)   # 두 장은 쉼표로 직접 친다
        ws.add_data_validation(dv)
        dv.add("%s2:%s%d" % (col_of("자료"), col_of("자료"), last_row))

    # C·D열 — 이 파일이 쓸 스타일
    lst.cell(1, 3, "유형")
    lst.cell(1, 4, "스타일")
    for i, q in enumerate(QTYPES, start=2):
        lst.cell(i, 3, q)
        lst.cell(i, 4, styles.get(q, ""))


def photo_names():
    """팔레트 사진 폴더의 그림 이름들(확장자 뺀). 못 읽으면 빈 목록."""
    try:
        return sorted(name for name, (kind, _e) in library.label_lookup().items()
                      if kind == "사진")
    except Exception as e:
        applog.exc("사진 목록 읽기 실패", e)
        return []
