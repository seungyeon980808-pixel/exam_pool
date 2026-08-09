# -*- coding: utf-8 -*-
"""마크다운 텍스트 → 시험문제 데이터 파싱"""

import re
from collections import namedtuple

CIRCLED_MARKER_PATTERN = r'[①-⑳㉠-㉭ⓐ-ⓩ㈀-㈎⒜-⒵Ⓐ-Ⓩ] ?'


def parse(text):
    data = {
        'num': '', 'stem': '', 'material': '',
        'material_type': 'basic',   # basic / photo / experiment
        'material_flag': False,     # 자료: 키워드 감지 여부
        'question': '', 'bogi': [], 'choices': [],
        'choices_type': '5',        # '1' / '3' / '5'
    }
    lines = text.splitlines()
    mode = None

    def starts(s, *keywords):
        for k in keywords:
            if s.startswith(k):
                return k
        return None

    for line in lines:
        s = line.strip()
        if not s:
            continue

        k = starts(s, '번호:', '번 호:')
        if k:
            data['num'] = s[len(k):].strip(); mode = None; continue

        k = starts(s, '발문:', '문:', '발 문:')
        if k:
            data['stem'] = s[len(k):].strip(); mode = 'stem'; continue

        k = starts(s, '실험자료:')
        if k:
            data['material_type'] = 'experiment'
            data['material'] = ''; mode = 'material'; continue

        k = starts(s, '사진자료:')
        if k:
            data['material_type'] = 'photo'
            data['material'] = ''; mode = 'material'; continue

        k = starts(s, '자료:', '자 료:')
        if k:
            data['material'] = s[len(k):].strip()
            data['material_type'] = 'basic'
            data['material_flag'] = True; mode = 'material'; continue

        k = starts(s, '질문:', '질 문:')
        if k:
            data['question'] = s[len(k):].strip(); mode = 'question'; continue

        if starts(s, '보기:', '보 기:') or s == '보기':
            mode = 'bogi'; continue

        k = starts(s, '선지1:', '선지3:', '선지5:', '1선지:', '3선지:', '5선지:', '선지:', '선 지:')
        if k:
            if k in ('선지1:', '1선지:'):
                data['choices_type'] = '1'
            elif k in ('선지3:', '3선지:'):
                data['choices_type'] = '3'
            elif k in ('선지5:', '5선지:'):
                data['choices_type'] = '5'
            else:
                data['choices_type'] = '5'
            mode = 'choices'; continue

        if mode == 'bogi':
            if len(s) >= 2 and s[1] == ':':
                data['bogi'].append(s[2:].strip())
            else:
                data['bogi'].append(s)
        elif mode == 'choices' and s:
            if s[0] in '①②③④⑤':
                s = s[1:].strip()
            data['choices'].append(s)
        elif mode == 'stem' and s:
            data['stem'] += (' ' + s if data['stem'] else s)
        elif mode == 'material' and s:
            data['material'] += ('\n' + s if data['material'] else s)
        elif mode == 'question' and s:
            data['question'] += (' ' + s if data['question'] else s)

    return data


def has_recognized_content(data):
    """발문/자료/질문/보기/선지 중 하나라도 인식됐으면 True (부분 변환 지원)"""
    return bool(
        data['stem'] or data['material_flag']
        or data['material_type'] in ('photo', 'experiment')
        or data['question'] or data['bogi'] or data['choices']
    )


def strip_circled_markers(text):
    """원문자(①②…㉠㉡…ⓐⓑ…) + 뒤 공백 1칸 제거 — 기본 서식 되돌리기용"""
    return re.sub(CIRCLED_MARKER_PATTERN, '', text)


# ══════════════════════════════════════════════════════
# 라이브러리 마크다운 문법
# ══════════════════════════════════════════════════════
r"""두 가지 모양이 있고, 역할이 다르다.

    \인사말\              등록해 둔 것을 **꺼내 넣기** (붙여넣기)
    \굵게{옳지 않은}      이 부분에 **적용하기** (형광펜)

왜 모양이 다른가 — 붙여넣기는 '무엇을'만 있으면 되지만, 형광펜은 '어디부터
어디까지'가 필요하다. 그래서 후자는 범위를 표시할 방법이 있어야 한다.

**여는 글자와 닫는 글자가 달라야 한다** (LaTeX 구조 차용, 2026-07-18):
    괄호 ( 가(나)다 )   → 여는 것/닫는 것이 달라서 안에 또 넣어도 짝을 셀 수 있다
    따옴표 " 가"나"다 " → 같아서 못 센다
예전엔 `\서식\내용\/` 처럼 `\` 하나로 열고 닫으려 해서, 내용 안에 `\라벨\` 을
넣으면 어디가 끝인지 알 수 없었다. `{ }` 로 닫으면 그 문제가 통째로 사라진다.

LaTeX 와 다른 점: 진짜 LaTeX 는 여러 서식을 겹칠 때 중첩(`\textbf{\itshape{…}}`)
하거나 선언형(`{\bfseries\itshape …}`)을 쓰는데, 선언형은 '명령 뒤 공백 한 칸'이
의미를 갖는 함정이 있다. 여기서는 명령을 나열하되 범위는 `{ }` 로 받는다:
    \굵게\기울임\크기15{내용}
"""

from hwp_palette.model import func_catalog
from hwp_palette.model import library        # 서식 물감 → 글자모양 델타 번역

# \라벨\ — 등록한 것을 꺼내 넣기
LIB_TOKEN_RE = re.compile(r'\\([^\\\r\n]+?)\\')
# \명령 — 서식 명령 하나 (이름에는 \ { } 가 들어갈 수 없다)
CMD_RE = re.compile(r'\\([^\\{}\r\n]+)')
# 라이브러리 문법이 하나라도 있는가 (변환 경로를 고를 때 씀)
_ANY_TOKEN_RE = re.compile(r'\\[^\\\r\n]+?\\|\\[^\\{}\r\n]+\{')

_MAX_NEST = 8            # 중첩 깊이 한도 (실수로 무한 중첩되는 것 방지)

# \표3*3\ — 그 자리에 표를 만든다 (2026-07-25).
# 등록한 라벨이 아니라 **모양으로 알아보는** 라벨이다(\원1\ \로마3\ 과 같은 부류).
#
# 행·열을 무언가로 나눠야 한다 — \표310\ 은 3x10 인지 31x0 인지 알 수 없다.
# 기본은 `*` 다: 한글 IME 로 글을 쓰는 중에 `x` 를 치려면 **영문 전환이 필요한데**
# `*` 는 그대로 쳐진다. x·× 도 받아준다(뜻이 같고 헷갈릴 일이 없다).
TABLE_LABEL_RE = re.compile(r'표\s*(\d+)\s*[*xX×]\s*(\d+)')
# 셀 구분자 `&` 하나. 글자로 쓴 & 는 `&&` 로 적는다.
#
# 탈출을 `\&` 로 하지 않는 이유 (2026-07-25 테스트가 잡아냄):
#   `\학교\&\굵게{중요}` 처럼 라벨 바로 뒤에 & 가 오면, 라벨을 **닫는** \ 가
#   앞에 붙어 있어 `\&` 와 구별할 수 없다. 셀에 라벨을 넣는 건 흔한 일이므로
#   그쪽을 살리고, 탈출은 \ 와 무관한 `&&` 로 정했다.
CELL_SPLIT_RE = re.compile(r'(?<!&)&(?!&)')
# 실수로 \표999x999\ 를 써서 한글이 멈추는 것을 막는 상한
_MAX_TABLE_SIDE = 50

# 값이 필요 없는 글자 서식 — 이름이 곧 필드명이다
_TOGGLES = ("굵게", "기울임", "밑줄")

# 문단 전체에 걸리는 것들. 줄 일부만 감쌌는데 문단이 통째로 바뀌면 당황스러우므로
# 인라인 문법에서는 거부한다 (팔레트의 '서식 조합' 블럭에서 쓰면 된다).
_PARA_ONLY = {"가운데정렬", "왼쪽정렬", "양쪽정렬", "줄간격", "들여쓰기",
              "내어쓰기", "왼쪽여백", "오른쪽여백", "어절단위 줄바꿈",
              "자간 자동조절"}

# 색 이름 → HWP 색값(R + G<<8 + B<<16)
_COLOR_NAMES = {
    "검정": (0, 0, 0), "빨강": (255, 0, 0), "파랑": (0, 0, 255),
    "초록": (0, 128, 0), "노랑": (255, 255, 0), "회색": (128, 128, 128),
    "흰색": (255, 255, 255),
}


def has_library_tokens(text):
    return bool(_ANY_TOKEN_RE.search(text or ''))


def has_style_spans(text):
    r"""서식 적용(`\명령{...}`)이 들어 있는가 — 모양만 본다."""
    return bool(re.search(r'\\[^\\{}\r\n]+\{', text or ''))


def _rgb(r, g, b):
    return r + (g << 8) + (b << 16)


def _parse_color(raw):
    v = raw.strip()
    if v in _COLOR_NAMES:
        return _rgb(*_COLOR_NAMES[v])
    m = re.fullmatch(r'#?([0-9A-Fa-f]{6})', v)
    if m:
        h = m.group(1)
        return _rgb(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    return None


def _match_font(name):
    """알려진 글꼴 이름과 맞춰본다. 못 찾으면 None.

    아무 문자열이나 글꼴로 받아주면 '함초롱'(오타) 같은 것이 조용히 통과해
    아무 일도 안 일어난다. 목록에 있는 것만 인정하고, 목록 밖 글꼴은
    `\글꼴맑은 고딕` 처럼 이름을 붙여 쓰게 한다.
    """
    if name in func_catalog.COMMON_FONTS:
        return name
    hits = [f for f in func_catalog.COMMON_FONTS if f.startswith(name)]
    return hits[0] if len(hits) == 1 else None


def resolve_style_token(tok, lookup, warnings):
    r"""서식 명령 하나 → 글자서식 델타. 해석 못 하면 None.

    받는 모양:
      굵게 · 기울임 · 밑줄      토글
      15 · 15.5                맨 숫자는 크기(pt) — 제일 흔하므로 이름 생략 허용
      크기15 · 자간-5           이름+값
      색빨강 · 색#FF0000        색
      함초롬바탕                아는 글꼴 이름
      글꼴맑은 고딕             목록 밖 글꼴을 쓸 때
      내강조                    등록해 둔 '서식' 라벨
    """
    t = (tok or "").strip()
    if not t:
        return None
    if t in _TOGGLES:
        return {t: True}
    if t in _PARA_ONLY:
        warnings.append(
            f"'{t}'는 문단 전체에 걸리는 서식이라 줄 일부에는 쓸 수 없습니다 "
            f"— 팔레트의 '서식 조합' 블럭을 쓰세요")
        return None
    entry = lookup.get(t)
    if entry and entry[0] == '서식':
        # 서식 물감은 지금 조작 목록(actions)으로 저장된다 — 줄 일부에 걸 수
        # 있는 글자모양만 골라 델타로 번역한다 (library.style_fields).
        return library.style_fields(entry[1])
    m = re.fullmatch(r'(?:크기|글씨크기)?\s*(-?\d+(?:\.\d+)?)', t)
    if m:
        return {"크기": float(m.group(1))}
    m = re.fullmatch(r'자간\s*(-?\d+)', t)
    if m:
        return {"자간": int(m.group(1))}
    m = re.fullmatch(r'색\s*(.+)', t)
    if m:
        color = _parse_color(m.group(1))
        if color is None:
            warnings.append(f"모르는 색입니다: '{m.group(1).strip()}' "
                            f"(쓸 수 있는 이름: {', '.join(_COLOR_NAMES)} 또는 #RRGGBB)")
            return None
        return {"글자색": color}
    m = re.fullmatch(r'글꼴\s*(.+)', t)
    if m:
        return {"글꼴": m.group(1).strip()}
    font = _match_font(t)
    if font:
        return {"글꼴": font}
    warnings.append(f"모르는 서식입니다: \\{t}"
                    f"  (등록한 서식 라벨이거나 굵게·기울임·밑줄·숫자·색·글꼴이어야 합니다)")
    return None


SKIP_MARK = '-'      # 이 줄은 해당 빈칸을 비워둔다

# 한 빈칸에 여러 줄을 넣는 덩어리 (2026-07-25).
#
#     {(가) 매질 A, B를 준비한다.
#     (나) A에서 B로 빛을 45°로 입사시킨다.
#     (다) B에서 A로 빛을 30°로 입사시킨다.}
#
# `\줄\` 처럼 한 줄에 몰아 쓰는 방법도 있었지만, 줄이 길어져 원본 시험지와
# 눈으로 대조가 안 된다. 여는 { 를 첫 줄 앞에, 닫는 } 를 마지막 줄 끝에 붙이면
# **줄이 줄로 보이면서** 어디까지가 한 칸인지도 드러난다.
#
# `{` 를 새 기호로 들이지 않아도 되는 이유: 지금 `{` 는 **항상 `\명령` 뒤**에만
# 온다(`\굵게{…}`). 줄 맨 앞에 홀로 선 `{` 는 다른 뜻일 수가 없다.
MultiLine = namedtuple('MultiLine', 'lines')

# 덩어리 안에 들어간 표. MultiLine.lines 의 한 자리를 차지한다 (2026-07-25).
# [실험 결과] 처럼 **글과 표가 한 빈칸에 같이** 들어가는 문항이 실제로 있다.
Table = namedtuple('Table', 'rows cols grid')


def _try_style_span(text, i, lookup, warnings, style, depth):
    r"""text[i] 부터 `\명령\명령…{내용}` 을 읽는다. 아니면 None.

    반환: (다음 위치, 조각들)
    명령을 하나라도 해석 못 하면 서식 구간으로 보지 않는다 — 우연한 일치가
    서식으로 오해받지 않게 하는 안전장치다.
    """
    if depth > _MAX_NEST:
        warnings.append("서식이 너무 깊게 중첩됐습니다")
        return None
    j, toks = i, []
    while j < len(text) and text[j] == '\\':
        m = CMD_RE.match(text, j)
        if not m:
            return None
        # 여러 서식은 쉼표로 잇는다 — `\기울임,굵게{…}` (사용자 결정 2026-07-27).
        # 옛 방식 `\굵게\기울임{…}` 도 계속 읽힌다 (while 이 \ 마다 돈다).
        toks.extend(part.strip() for part in m.group(1).split(','))
        j = m.end()
    if not toks or j >= len(text) or text[j] != '{':
        return None                 # 명령 뒤에 { 가 없으면 서식 구간이 아니다
    fields = dict(style or {})
    for tok in toks:
        delta = resolve_style_token(tok, lookup, warnings)
        if delta is None:
            return None
        fields.update(delta)        # 뒤에 온 것이 이긴다
    segs, end = _parse_inline(text, j + 1, lookup, warnings, fields,
                              depth + 1, stop_at_brace=True)
    return end, segs


def _try_label(text, i, lookup, warnings):
    r"""text[i] 부터 `\라벨\` 을 읽는다. 아니면 None.

    반환: (다음 위치, ('text', 넣을 글자)) 또는 (다음 위치, ('image', 경로)).
    '사진' 항목은 library._photo_lookup() 이 사진 폴더에서 만들어 준다 —
    \실험사진1\ 처럼 등록 없이 파일 이름만으로 그림을 부른다.
    """
    m = LIB_TOKEN_RE.match(text, i)
    if not m:
        return None
    label = m.group(1).strip()
    entry = lookup.get(label)
    if entry and entry[0] == '문자':
        return m.end(), ('text', entry[1]['text'])
    if entry and entry[0] == '사진':
        return m.end(), ('image', entry[1]['path'])
    # 아래는 전부 '원문 그대로 두고 경고' — 사용자가 눈으로 보고 고칠 수 있게
    if entry and entry[0] in ('템플릿', '양식'):
        warnings.append(f"{entry[0]} 라벨은 한 줄에 단독으로 써주세요: \\{label}\\")
    elif entry and entry[0] == '서식':
        warnings.append(f"서식은 적용할 내용이 필요합니다: \\{label}{{내용}} 처럼 써주세요")
    else:
        warnings.append(f"등록되지 않은 라벨: \\{label}\\ "
                        f"(라이브러리·내장 문자·사진 폴더 어디에도 없습니다)")
    return m.end(), ('text', m.group(0))


def _parse_inline(text, i, lookup, warnings, style, depth, stop_at_brace):
    r"""줄을 왼쪽부터 읽어 조각 목록으로 만든다.

    조각 = {'text': 글자들, 'style': 글자서식 델타 또는 None}
    중첩된 서식은 바깥 서식 위에 덧씌워 **납작하게 펴서** 담는다
    (엔진은 '이 구간에 이 서식' 목록만 알면 되므로 트리가 필요 없다).
    """
    segs, buf = [], []

    def flush():
        if buf:
            segs.append({"text": "".join(buf),
                         "style": dict(style) if style else None})
            buf.clear()

    while i < len(text):
        ch = text[i]
        if ch == '\\':
            nxt = text[i + 1:i + 2]
            if nxt == '\\':                 # \\ → 글자 그대로의 역슬래시
                buf.append('\\')
                i += 2
                continue
            if nxt == '}':                  # \} → 글자 그대로의 닫는 중괄호
                buf.append('}')
                i += 2
                continue
            span = _try_style_span(text, i, lookup, warnings, style, depth)
            if span:
                end, sub = span
                flush()
                segs.extend(sub)
                i = end
                continue
            lab = _try_label(text, i, lookup, warnings)
            if lab:
                end, (kind, value) = lab
                if kind == 'image':
                    flush()
                    segs.append({"text": "", "style": dict(style) if style else None,
                                 "image": value})
                else:
                    buf.append(value)
                i = end
                continue
            buf.append(ch)                  # 홑 \ = 템플릿 빈칸 표시. 그대로 둔다
            i += 1
        elif ch == '}' and stop_at_brace:
            flush()
            return segs, i + 1
        else:
            buf.append(ch)
            i += 1
    if stop_at_brace:
        warnings.append("서식을 닫는 } 가 없습니다")
    flush()
    return segs, i


def build_segments(line, lookup, warnings):
    """한 줄 → 조각 목록. 서식·사진이 없으면 조각 하나짜리 목록이 된다."""
    segs, _ = _parse_inline(line, 0, lookup, warnings, None, 0,
                            stop_at_brace=False)
    return [s for s in segs if s["text"] or s.get("image")]


def _slot_value(line, lookup, warnings):
    r"""템플릿 빈칸에 넣을 값.

    반환:
      · 글자만 있으면 **문자열** (대부분의 경우 — 예전과 같다)
      · 사진·서식이 섞였으면 **조각 목록** → fill_slots 가 insert_rich_line 으로 넣는다

    예전에는 사진·서식을 경고와 함께 버렸다. 그런데 '사진 1개 + 선지 3개'처럼
    사진 자리를 가진 템플릿이 실제로 있어서(\합답1사진3선지\), 빈칸에 사진을 못
    넣으면 그 템플릿을 쓸 수가 없었다 — 사진이 템플릿 뒤로 빠져나갔다.
    """
    segs = build_segments(line, lookup, warnings)
    if any(s.get("image") or s["style"] for s in segs):
        return segs
    return "".join(s["text"] for s in segs)


def _cell_value(text, lookup, warnings):
    r"""표 셀 하나의 값. '-' 한 글자면 빈 칸(None).

    빈칸 채우기의 '-' 규칙을 그대로 쓴다 — 새로 배울 것을 늘리지 않는다.
    """
    if text.strip() == SKIP_MARK:
        return None
    return _slot_value(text.replace('&&', '&'), lookup, warnings)


def _read_block(lines, start, lookup, warnings):
    r"""`{` 로 시작하는 줄부터 `}` 로 끝나는 줄까지를 **한 칸 값**으로 읽는다.

    반환: (MultiLine, 다음 줄 번호). 여는 `{` 와 닫는 `}` 는 벗겨낸다.

    괄호를 줄에 붙여 써도 되고 따로 한 줄에 둬도 된다 — 벗기고 남은 것이 없으면
    그 줄은 내용으로 치지 않기 때문이다:
        {가                     {                       {한 줄짜리}
        나}                     가
                                나
                                }
    닫는 `}` 를 **글자로** 쓰려면 이미 있는 `\}` 를 쓴다(그 줄은 안 닫힌다).

    덩어리 안에서도 `\표3*3\` 이 통한다 — [실험 결과] 처럼 **글과 표가 한 빈칸에
    같이** 들어가는 문항이 실제로 있다 (2026-07-25).

    두 걸음으로 읽는 이유: 먼저 괄호를 벗겨 '덩어리 안의 줄' 목록을 만들고,
    그다음 그 안에서 표를 찾는다. 한 번에 하면 표의 행을 읽다가 닫는 `}` 를
    행으로 먹어버린다.
    """
    # ① 범위부터 — 여는 { 와 닫는 } 를 벗긴 줄 목록
    inner, j, closed = [], start, False
    while j < len(lines):
        s = lines[j].strip()
        if j == start:
            s = s[1:]                       # 여는 { 벗기기
        if s.endswith('}') and not s.endswith('\\}'):
            s = s[:-1]
            closed = True
        inner.append(s)
        j += 1
        if closed:
            break
    if not closed:
        warnings.append(
            "덩어리를 닫는 } 가 없습니다 — { 로 시작했으면 } 로 닫아주세요")

    # ② 그 안을 줄과 표로 읽는다
    body, k = [], 0
    while k < len(inner):
        s = inner[k]
        if not s.strip():
            k += 1
            continue
        tm = None
        m = LIB_TOKEN_RE.fullmatch(s.strip())
        if m:
            label = m.group(1).strip()
            if not lookup.get(label):       # 등록한 라벨이 언제나 우선
                tm = TABLE_LABEL_RE.fullmatch(label)
        if tm:
            rows_n, cols_n = int(tm.group(1)), int(tm.group(2))
            if (1 <= rows_n <= _MAX_TABLE_SIDE
                    and 1 <= cols_n <= _MAX_TABLE_SIDE):
                grid, k = _table_rows(inner, k + 1, rows_n, cols_n,
                                      lookup, warnings)
                body.append(Table(rows_n, cols_n, grid))
                continue
            warnings.append(
                f"표 크기가 범위를 벗어났습니다: {rows_n}x{cols_n} "
                f"(1~{_MAX_TABLE_SIDE} 사이여야 합니다)")
        body.append(_slot_value(s, lookup, warnings))
        k += 1
    return MultiLine(tuple(body)), j


def _table_rows(lines, start, rows, cols, lookup, warnings):
    r"""표 라벨 다음 줄들에서 셀 값을 읽는다. 반환: (셀 2차원 목록, 다음 줄 번호).

    한 줄이 한 행이고, 행 안은 `&` 로 나눈다. 줄 수가 모자라거나 한 행의 칸이
    모자라면 그 자리는 빈 칸으로 남는다 — 표는 이미 만들어졌으므로 사용자가
    한글에서 마저 채우면 된다(변환을 통째로 실패시키는 것보다 낫다).
    """
    grid, j = [], start
    while len(grid) < rows and j < len(lines):
        raw = lines[j]
        if not raw.strip():
            j += 1
            continue
        if _starts_new_insert(raw.strip(), lookup):
            break                       # 다음 삽입 시작 — 여기까지가 이 표 몫
        parts = CELL_SPLIT_RE.split(raw)
        if len(parts) > cols:
            warnings.append(
                f"{len(grid) + 1}번째 줄의 칸이 {len(parts)}개인데 표는 {cols}칸입니다 "
                f"— 넘치는 칸은 버립니다")
        grid.append([_cell_value(p, lookup, warnings) for p in parts[:cols]])
        j += 1
    return grid, j


def _starts_new_insert(stripped_line, lookup):
    r"""이 줄이 '다음 삽입을 시작하는' 라벨인가 (그러면 빈칸 채우기를 여기서 끊는다).

    템플릿·양식은 새로운 삽입 명령이라 여기서 끊어야 한다. 반면 문자·사진은
    빈칸에 들어갈 **내용**이다 — 예전에는 이것도 끊어서, 사진 자리를 가진 템플릿의
    빈칸에 사진을 넣을 수 없었다(사진이 템플릿 뒤로 밀려나던 문제).

    미등록 라벨은 예전처럼 끊는다. 무엇인지 모르는 것을 빈칸에 밀어 넣기보다,
    사용자가 경고를 보고 고치는 편이 안전하다.
    """
    m = LIB_TOKEN_RE.fullmatch(stripped_line)
    if not m:
        return False
    entry = lookup.get(m.group(1).strip())
    if entry is None:
        return True                     # 미등록 — 예전 동작 유지
    return entry[0] in ('템플릿', '양식')


def split_selection_units(text):
    r"""선택 텍스트를 '한 셀의 한 줄' 단위로 쪼갠다.

    표에서 여러 셀을 선택해 복사하면 한글은 **열을 탭, 행을 줄바꿈**으로 이어
    붙인 문자열 하나를 준다. 그래서 줄바꿈만 경계로 보면 "셀A<탭>셀B" 가 한 줄로
    묶여, 변환 결과가 전부 한 셀에 몰린다(사진이 한 칸에 쌓이던 버그의 원인).
    탭도 줄바꿈과 똑같은 경계로 본다.

    반환: 문서에 나오는 순서를 지킨 조각 목록. 빈 조각(빈 셀)은 뺀다.
    """
    s = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    units = []
    for line in s.split('\n'):
        for cell in line.split('\t'):
            if cell.strip():
                units.append(cell)
    return units


def build_library_plan(text, lookup):
    r"""선택 텍스트 → 실행 계획.

    lookup: {라벨: (분류명, 항목dict)} — library.label_lookup() 결과.
    반환: (ops, warnings)
      ops: ('line', 텍스트) — 그대로 삽입할 한 줄
           ('rich_line', [조각들]) — 서식 적용(\굵게{내용})이 섞인 한 줄
           ('template', 항목, [줄들]) — 템플릿을 커서에 삽입 + 빈칸(\) 순서대로 채움
           ('form', 항목, [줄들])     — 양식을 새 문서로 열고 + 빈칸 채움
             줄이 '-' 하나면 그 빈칸은 건너뛴다(비워둠).
    """
    ops, warnings = [], []
    lines = (text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        m = LIB_TOKEN_RE.fullmatch(stripped)
        if m:
            label = m.group(1).strip()
            entry = lookup.get(label)
            # 템플릿(삽입)과 양식(새 문서로 열기) — 빈칸 채우는 방식은 같다
            if entry and entry[0] in ('템플릿', '양식'):
                kind = 'form' if entry[0] == '양식' else 'template'
                item = entry[1]
                slot_count = int(item.get('slot_count') or 0)
                fills = []
                j = i + 1
                while len(fills) < slot_count and j < len(lines):
                    cand = lines[j].strip()
                    if not cand:
                        j += 1
                        continue
                    if _starts_new_insert(cand, lookup):
                        break          # 다음 삽입 시작 — 여기까지가 이 템플릿 몫
                    if cand == SKIP_MARK:
                        fills.append(None)     # 이 빈칸은 비움
                        j += 1
                    elif cand.startswith('{'):
                        # { … } — 여러 줄이 이 빈칸 하나에 통째로 들어간다
                        value, j = _read_block(lines, j, lookup, warnings)
                        fills.append(value)
                    else:
                        fills.append(_slot_value(lines[j], lookup, warnings))
                        j += 1
                # 꾸러미(섞은 물감)면 **요소마다 하나씩** 펼친다 (2026-07-31).
                # 사용자 기획: 물감 1·2·3 이 빈칸 2개씩일 때 \숫자\ 아래 여섯
                # 줄을 쓰면 1↦(1,2) 2↦(3,4) 3↦(5,6) 으로 나뉘어 들어가고,
                # 셋을 이어 붙인 결과가 나온다. 나누는 규칙은 '앞에서부터
                # 요소의 빈칸 수만큼' — 낱개 템플릿의 규칙 그대로다.
                members = item.get('_mix_items')
                if members:
                    at = 0
                    for m in members:
                        n = int(m.get('slot_count') or 0)
                        ops.append(('template', m, fills[at:at + n]))
                        at += n
                else:
                    ops.append((kind, item, fills))
                i = j
                continue
            # \표3x3\ — 등록 라벨이 아닐 때만 본다(등록한 것이 언제나 우선)
            tm = None if entry else TABLE_LABEL_RE.fullmatch(label)
            if tm:
                rows_n, cols_n = int(tm.group(1)), int(tm.group(2))
                if not (1 <= rows_n <= _MAX_TABLE_SIDE
                        and 1 <= cols_n <= _MAX_TABLE_SIDE):
                    warnings.append(
                        f"표 크기가 범위를 벗어났습니다: {rows_n}x{cols_n} "
                        f"(1~{_MAX_TABLE_SIDE} 사이여야 합니다)")
                    ops.append(('line', stripped))      # 원문을 남겨 눈에 띄게
                    i += 1
                    continue
                grid, j = _table_rows(lines, i + 1, rows_n, cols_n,
                                      lookup, warnings)
                ops.append(('table', rows_n, cols_n, grid))
                i = j
                continue
        segs = build_segments(lines[i], lookup, warnings)
        if any(s["style"] or s.get("image") for s in segs):
            ops.append(('rich_line', segs))
        else:
            # 서식·사진이 없으면 굳이 조각으로 나눠 넣을 필요가 없다 (COM 호출 절약)
            ops.append(('line', "".join(s["text"] for s in segs)))
        i += 1
    return ops, warnings
