# -*- coding: utf-8 -*-
r"""채운 문항 엑셀 → 마크다운. 선생님이 문법을 쓸 일이 없게 하는 쪽이 전부다.

  · 빈 칸        → 건너뜀(`-`) 을 넣어 준다
  · 보기·선지 칸 → 줄로 나누고 앞의 `ㄱ.` `①` 을 떼어 빈칸에 하나씩 넣는다
  · 여러 줄 지문 → `{ }` 덩어리로 묶는다 (안 묶으면 줄 수만큼 빈칸을 먹는다)
  · `}` 글자     → `\}` 로 피한다 (직접 쓴 서식은 건드리지 않는다)
  · 배점         → 템플릿에 배점 칸이 없으면 발문 뒤에 `(3점)` 으로 붙인다
  · 자료         → 확장자 없는 이름 그대로 `\이름\` (사진 폴더가 찾는다)

한글 없이 도는 순수 변환 — 창을 안 띄우고 테스트할 수 있다.
"""
import re

from hwp_palette.model import excel_form
from hwp_palette.model import library

SKIP = "-"          # 이 빈칸은 비운다 (parser.SKIP_MARK)

# 줄 첫머리가 이 낱말이면 파서가 '시험문제 문법'으로 알아채 템플릿 경로를 건너뛴다.
# 지문에 우연히 들어가면 시험지가 통째로 깨지므로 콜론 앞에 공백을 넣어 무력화한다.
LEGACY_KEYS = ("번호:", "발문:", "문:", "자료:", "사진자료:", "실험자료:",
               "질문:", "보기:", "선지:", "선지1:", "선지3:", "선지5:")

CHOICES = "①②③④⑤"

# 줄 앞에 습관적으로 붙이는 말머리 — 있으면 뗀다.
#
# **한글 자모·숫자는 마침표(또는 괄호)가 있을 때만 뗀다.** 합답형 선지는 내용
# 자체가 'ㄱ' 'ㄱ, ㄴ' 이라, 마침표 없이 떼면 선지가 통째로 사라진다.
BULLET = re.compile(r"^\s*(?:[①-⑤]\s*|(?:[ㄱ-ㅎ]|\(?\d\)?)\s*[.)\]]\s+)")


# ===== 값 다듬기 ============================================================
def _text(cell):
    v = cell.value
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).replace("\r\n", "\n").strip()


def _guard(text):
    out = []
    for line in text.split("\n"):
        s = line.lstrip()
        for k in LEGACY_KEYS:
            if s.startswith(k):
                line = line.replace(k, k[:-1] + " :", 1)
                break
        out.append(line)
    return "\n".join(out)


def _esc(text):
    r"""원문의 '}' 를 글자로 피한다.

    단 칸 안에 `\명령{` 이 하나라도 있으면 **일부러 서식을 쓰신 것**으로 보고
    손대지 않는다. 안 그러면 `\굵게{옳지 않은}` 의 닫는 괄호까지 `\}` 가 되어
    서식이 통째로 깨진다.
    """
    if re.search(r"\\[^\s\\{}]+\{", text):
        return text
    return text.replace("}", "\\}")


def _cell(text):
    """빈칸 한 칸에 들어갈 값. 여러 줄이면 { } 덩어리로 묶는다."""
    t = _guard(text.strip())
    if not t:
        return SKIP
    return "{" + t + "}" if "\n" in t else t


def _lines(text, count):
    """보기·선지 칸을 줄로 나누고 말머리를 뗀다. 모자라면 빈칸으로 채운다."""
    out = []
    for ln in text.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        stripped = BULLET.sub("", ln).strip()
        out.append(stripped or ln)      # 다 지워지면 원문을 살린다
    return (out + [""] * count)[:count]


def _photos(material):
    """자료 칸에서 그림 이름을 뽑는다. 쉼표로 2장까지. 확장자는 떼어 준다."""
    out = []
    for p in material.split(","):
        p = p.strip()
        if not p:
            continue
        out.append(re.sub(r"\.(png|jpg|jpeg|bmp|gif|tif|tiff)$", "", p, flags=re.I))
    return out[:2]


def _guess_type(row):
    if row.get("유형") in excel_form.QTYPES:
        return row["유형"]
    if row.get("보기"):
        return "합답형"
    if row.get("선지"):
        return "정답형"
    return "서술형"


# ===== 한 문항 → 마크다운 ===================================================
def question_to_markdown(row, qtype, num, styles=None):
    if qtype == "서술형":
        return _essay(row, num)

    photos = _photos(row.get("자료", ""))
    styles = styles or excel_form.default_styles()
    mapping = excel_form.mapping_of(qtype, styles.get(qtype, ""))
    label = mapping.get(len(photos), "")
    if not label:
        return _plain(row, qtype, num)

    # 코드에 박힌 표가 아니라 **조각에 붙은 빈칸 이름표**를 먼저 본다
    # (excel_form.slots_of 설명 참고) — 그래야 사용자가 만든 템플릿과
    # 문서 해체로 꺼낸 문항 틀도 문항 엑셀에 참여한다.
    slots = excel_form.slots_of(label)
    if not slots:
        return _plain(row, qtype, num)
    pt = row.get("배점", "")
    ask = _esc(row.get("발문", ""))
    if pt and "배점" not in slots:
        ask = (ask + " (%s점)" % pt).strip()
    passage = _esc(row.get("지문", ""))
    bogi = _lines(row.get("보기", ""), 3)
    choices = _lines(row.get("선지", ""), 5)

    def value(slot):
        if slot == "번호":
            return str(num)
        if slot == "지문":
            return _cell(passage)
        if slot == "발문":
            if "지문" in slots:
                return _cell(ask)
            # 지문 칸이 없는 템플릿(정답형)이면 지문·사진을 발문에 함께 묶는다
            parts = [passage] + ["\\%s\\" % p for p in photos] + [ask]
            return _cell("\n".join([p for p in parts if p]))
        if slot == "사진1":
            return photos[0] if len(photos) >= 1 else SKIP
        if slot == "사진2":
            return photos[1] if len(photos) >= 2 else SKIP
        if slot == "배점":
            return pt or SKIP
        if slot.startswith("보기"):
            m = re.match(r'보기(\d+)$', slot)
            idx = int(m.group(1)) - 1 if m else -1
            if 0 <= idx < len(bogi):
                return _cell(_esc(bogi[idx]))
        if slot.startswith("선"):
            m = re.match(r'선(\d+)$', slot)
            idx = int(m.group(1)) - 1 if m else -1
            if 0 <= idx < len(choices):
                return _cell(_esc(choices[idx]))
        return SKIP

    return "\n".join(["\\%s\\" % label] + [value(s) for s in slots])


def _essay(row, num):
    r"""서술형 — 조각이 없어서 평문 + 답란 표(\표1*1\)로 만든다."""
    lines = []
    body = [_esc(row["지문"])] if row.get("지문") else []
    body += ["\\%s\\" % p for p in _photos(row.get("자료", ""))]
    if body:
        lines.append(_guard("\n".join(body)))
    head = "%d. %s" % (num, _esc(row.get("발문", "")))
    if row.get("배점"):
        head += " (%s점)" % row["배점"]
    lines += [_guard(head), "\\표1*1\\", SKIP]
    return "\n".join(lines)


def _plain(row, qtype, num):
    out = ["[%d번 · %s — 쓸 틀이 없어 평문으로 나갑니다]" % (num, qtype)]
    for k in ("지문", "발문", "보기", "선지"):
        if row.get(k):
            out.append(row[k])
    return "\n".join(out)


# ===== 파일 읽기 ============================================================
def read_workbook(path):
    """엑셀 → (마크다운, 리포트 줄들, 정답표).

    리포트는 나이스 오류 리포트 감각으로 '몇 행이 무엇이 비었는지'를 적는다.
    """
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True)
    if excel_form.SHEET not in wb.sheetnames:
        return "", ["· '%s' 시트가 없습니다 — 이 프로그램이 만든 양식이 "
                    "맞는지 확인해 주세요." % excel_form.SHEET], []
    ws = wb[excel_form.SHEET]
    styles = _read_styles(wb)

    header = [_text(c) for c in ws[1]]
    idx = {name: header.index(name) for name in excel_form.HEADERS if name in header}
    report = []
    lost = [c for c in excel_form.HEADERS if c not in idx]
    if lost:
        report.append("· 열 제목이 바뀌었습니다: %s (그 칸은 빈 것으로 봅니다)"
                      % ", ".join(lost))

    items = []
    for r in range(2, ws.max_row + 1):
        cells = ws[r]
        row = {name: (_text(cells[i]) if i < len(cells) else "")
               for name, i in idx.items()}
        if not any(row.get(k) for k in ("지문", "발문", "선지")):
            continue                       # 빈 줄
        row["_행"] = r
        row["_유형"] = _guess_type(row)
        items.append(row)

    report.insert(0, "· 문항 %d개 읽음  (%s)"
                  % (len(items), " · ".join("%s=%s" % (q, styles.get(q, "?"))
                                            for q in excel_form.QTYPES)))

    photo_names = _known_photos()
    for row in items:
        _check(row, photo_names, report)

    # 번호 순으로 시험지 순서를 맞춘다 (번호 없으면 쓴 순서 뒤로)
    items.sort(key=lambda x: (0, int(x["번호"])) if x.get("번호", "").isdigit()
               else (1, x["_행"]))

    nums = [x["번호"] for x in items if x.get("번호", "").isdigit()]
    dup = sorted({n for n in nums if nums.count(n) > 1})
    if dup:
        report.append("· 번호가 겹칩니다: %s" % ", ".join(dup))

    out, answers = [], []
    for i, row in enumerate(items, start=1):
        num = int(row["번호"]) if row.get("번호", "").isdigit() else i
        out.append(question_to_markdown(row, row["_유형"], num, styles))
        answers.append((num, row["_유형"], row.get("정답", ""), row.get("배점", "")))

    return ("\n\n".join(out) + "\n") if out else "", report, answers


def _read_styles(wb):
    """양식을 만들 때 고른 스타일. 없으면(손으로 만든 파일) 기본값."""
    styles = excel_form.default_styles()
    if excel_form.LIST_SHEET not in wb.sheetnames:
        return styles
    ws = wb[excel_form.LIST_SHEET]
    for r in range(2, ws.max_row + 1):
        q, name = _text(ws.cell(r, 3)), _text(ws.cell(r, 4))
        if q in styles and name:
            styles[q] = name
    return styles


def _check(row, photo_names, report):
    where = "· %d행(%s번) —" % (row["_행"], row.get("번호", "?"))
    qtype = row["_유형"]
    if not row.get("발문"):
        report.append("%s 발문이 비었습니다" % where)
    if qtype != "서술형":
        got = [c for c in _lines(row.get("선지", ""), 5) if c]
        if len(got) < 5:
            report.append("%s 선지가 %d개뿐입니다 (5개 필요)" % (where, len(got)))
    if qtype == "합답형":
        got = [b for b in _lines(row.get("보기", ""), 3) if b]
        if len(got) < 3:
            report.append("%s 〈보기〉가 %d개뿐입니다 (3개 필요)" % (where, len(got)))
        if not _photos(row.get("자료", "")):
            report.append("%s 사진이 없어 그림 자리가 빈 칸으로 남습니다 "
                          "(한글에서 지우세요)" % where)
    if photo_names is not None:
        for p in _photos(row.get("자료", "")):
            if p not in photo_names:
                report.append("%s 그림 '%s' 을(를) 사진 폴더에서 못 찾았습니다"
                              % (where, p))


def _known_photos():
    """사진 폴더가 아는 이름들. 못 읽으면 None — 그림 확인을 건너뛴다."""
    try:
        return {name for name, (kind, _e) in library.label_lookup().items()
                if kind == "사진"}
    except Exception:
        return None


def answers_to_text(answers):
    """정답표 — 탭으로 나눠 엑셀·한글 표에 그대로 붙는다."""
    lines = ["번호\t유형\t정답\t배점"]
    for num, qtype, ans, pt in answers:
        lines.append("%d\t%s\t%s\t%s" % (num, qtype, ans, pt))
    return "\n".join(lines)
