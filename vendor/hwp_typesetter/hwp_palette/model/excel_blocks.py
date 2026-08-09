# -*- coding: utf-8 -*-
r"""문항 엑셀 '덩어리 틀' — 진짜 버튼(xlsm) 방식 (사용자 결정 2026-07-31, 형태 B).

무엇이 바뀌었나:
    예전 문항 엑셀은 "한 줄 = 한 문항"의 가로 표였고, 유형→템플릿 연결이
    코드에 박혀 있었다. 이제는 **덩어리** 방식이다 —

        엑셀 B1 드롭다운에서 꾸러미를 고르고 → [＋ 덩어리 추가] 버튼을
        누르면 → 그 꾸러미의 빈칸 이름들이 세로로 깔린다 → 값 열(B)만 채운다

    버튼과 표 꾸미기는 틀(assets/excel_block_template.xlsm)에 든 작은 VBA 가
    하고, **목록 채우기·읽기·마크다운 조립은 전부 여기(파이썬)가** 한다.
    VBA 를 키우면 학교 PC 마다 디버깅할 길이 없어서다.

    틀은 spikes/build_excel_block_template.py 가 엑셀 COM 으로 한 번 구워
    저장소에 담아 둔 것이다 — openpyxl 은 VBA 를 **만들지 못하고**(vbaProject
    .bin 은 엑셀만 굽는 바이너리) 실어 나를 수만 있다(keep_vba).

세로(이름|값) 방식인 이유: 꾸러미마다 빈칸 수가 달라도 모양이 유지되고,
여러 줄 지문을 한 칸에 넣기도 편하다. 가로 표는 열 머리글이 꾸러미마다
어긋난다.
"""

import re

from hwp_palette.core import applog
from hwp_palette.core import paths
from hwp_palette.model import excel_form
from hwp_palette.model import library
# _esc(} 피하기)·_cell(여러 줄 → {} 덩어리)은 예전 표 방식과 **같은 규칙**을
# 써야 한다 — 두 벌이 되면 한쪽만 고치는 사고가 난다.
from hwp_palette.model.excel_read import _cell, _esc, SKIP

SHEET_MAIN = "시험지"          # 틀과의 약속 (build_excel_block_template.py)
SHEET_PACKS = "_꾸러미"
HEAD_MARK = "▶"               # 덩어리 머리줄 표시 — VBA 가 찍고 여기가 읽는다


def template_path():
    return paths.resource_dir() / "assets" / "excel_block_template.xlsm"


# ── 내보내기 ────────────────────────────────────────────
def packs():
    r"""엑셀 드롭다운에 올릴 [(라벨, [빈칸 이름들])] — 라이브러리에서 뽑는다.

    올라가는 것: 라벨이 있고 **빈칸 이름을 알 수 있는** 템플릿·꾸러미.
    이름표가 없는 템플릿은 못 올린다 — 세로 칸에 무슨 이름을 적을지 모른다.
    """
    out = []
    for label, (cat, item) in sorted(library.label_lookup().items()):
        if cat != "템플릿":
            continue
        names = _slot_names(label, item)
        if names:
            out.append((label, names))
    return out


def _slot_names(label, item):
    if item.get("mix"):
        # 꾸러미 — 요소들의 이름표를 이어 붙인다. 이름 없는 요소는 요소
        # 이름에 번호를 붙여서라도 칸을 만든다(칸 수는 맞아야 한다).
        names = []
        for m in item.get("_mix_items") or []:
            got = [n for n in (m.get("slot_names") or []) if n]
            cnt = int(m.get("slot_count") or 0)
            if len(got) == cnt:
                names.extend(got)
            else:
                base = m.get("name", "칸")
                names.extend(f"{base}{j + 1}" for j in range(cnt))
        return names
    return excel_form.slots_of(label)


def build_xlsm(dest, pack_list=None):
    """틀을 복사해 꾸러미 목록과 드롭다운을 채워 dest(.xlsm)로 저장한다."""
    from openpyxl import load_workbook
    from openpyxl.worksheet.datavalidation import DataValidation
    if pack_list is None:
        pack_list = packs()
    wb = load_workbook(template_path(), keep_vba=True)
    hid = wb[SHEET_PACKS]
    for i, (label, names) in enumerate(pack_list, start=2):
        hid.cell(row=i, column=1, value=label)
        hid.cell(row=i, column=2, value=",".join(names))
    ws = wb[SHEET_MAIN]
    dv = DataValidation(
        type="list", allow_blank=True,
        formula1=f"='{SHEET_PACKS}'!$A$2:$A${len(pack_list) + 1}")
    ws.add_data_validation(dv)
    dv.add("B1")
    wb.save(dest)
    return len(pack_list)


# ── 불러오기 ────────────────────────────────────────────
def read_blocks(path):
    r"""채운 xlsm → [(라벨, [(빈칸 이름, 값)])]. 덩어리 틀이 아니면 None.

    None 이 뜻하는 것: '시험지' 시트가 없다 = 예전 가로 표 파일이다 —
    호출부가 excel_read.read_workbook() 으로 넘긴다. 옛 파일도 계속 읽힌다.
    """
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    try:
        if SHEET_MAIN not in wb.sheetnames:
            return None
        ws = wb[SHEET_MAIN]
        blocks, cur = [], None
        for row in ws.iter_rows(min_row=2, max_col=2):
            a = row[0].value
            b = row[1].value if len(row) > 1 else None
            a_s = "" if a is None else str(a).strip()
            if a_s.startswith(HEAD_MARK):
                cur = (a_s.lstrip(HEAD_MARK).strip(), [])
                blocks.append(cur)
            elif a_s and cur is not None:
                cur[1].append((a_s, "" if b is None else str(b)))
            elif not a_s:
                cur = None              # 빈 줄 = 덩어리 끝
        return blocks
    finally:
        wb.close()


def to_markdown(blocks):
    r"""덩어리들 → (마크다운, 읽기 결과 줄들, 정답표).

    마크다운은 새 문법이 아니다 — `\꾸러미라벨\` 단독 줄 + 아랫줄 값들,
    기존 템플릿 규칙 그대로다. 값이 빈 칸은 `-`(건너뜀)로 적는다.

    정답표: 빈칸 이름이 '정답'인 칸을 모은다. '배점'도 같은 식.
    """
    md, report, answers = [], [], []
    for n, (label, slots) in enumerate(blocks, start=1):
        md.append(f"\\{label}\\")
        filled = 0
        ans, pt = "", ""
        for name, val in slots:
            v = (val or "").strip()
            if not v:
                md.append(SKIP)
                continue
            filled += 1
            md.append(_cell(_esc(v)))
            bare = re.sub(r"\d+$", "", name)
            if bare == "정답":
                ans = v
            elif bare == "배점":
                pt = v
        md.append("")                   # 문항 사이 숨통 (변환은 빈 줄을 건너뛴다)
        report.append(f"{n}. {label} — {filled}/{len(slots)}칸 채움"
                      + ("" if filled else "  ⚠ 전부 비어 있습니다"))
        answers.append((n, label, ans, pt))
    return "\n".join(md).strip() + ("\n" if md else ""), report, answers
