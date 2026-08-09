# -*- coding: utf-8 -*-
r"""양식 채우기 — HWPX에서 채울 자리를 뽑고, 채운 내용을 다시 넣는다.

왜 HWPX인가 (실측 2026-07-19):
  .hwp(바이너리 5.x)는 문단 레코드를 직접 고치기 어렵다. 반면 **HWPX 는
  zip 안에 XML** 이고, 글자는 `<hp:t>` 태그 하나하나에 들어 있다.
  그 태그의 내용만 바꿔서 zip 을 다시 쓰면 **나머지 바이트는 손대지 않으므로
  표·병합·글꼴·이미지가 그대로 살아남는다** (셀 10개·병합 유지 확인).

이 모듈은 한글(pyhwpx)에 의존하지 않는다 — 순수 zip/XML 조작이라 한글 없이
테스트할 수 있다. .hwp → .hwpx 변환만 호출부(엔진)가 맡는다.

주고받는 단위가 '빈칸'이 아니라 '글자 조각(run)'인 이유:
  한 조각 안에 빈칸이 여러 개 있을 수 있다(실측: `\. \.`).
  빈칸 단위로 쪼개면 그 경우가 지저분해지므로, **조각 통째로 보여주고
  조각 통째로 돌려받는다**. 사람이 읽기에도 이쪽이 자연스럽다.
"""

import os
import re
import xml.sax.saxutils as saxutils
import zipfile

from hwp_palette.core import applog

# HWPX 안에서 본문이 들어 있는 파일들 (구역이 여러 개면 section1, 2 … 로 늘어난다)
SECTION_RE = re.compile(r"Contents/section\d+\.xml$")

# 글자 한 조각. HWPX 는 서식이 바뀌는 지점마다 이 태그를 나눈다.
# 여는 태그에 속성이 붙을 수 있다 (xml:space="preserve" 실측, 2026-07-31) —
# <hp:t> 만 찾으면 그런 조각의 빈칸이 통째로 안 보인다. 속성까지 잡되,
# 바꿔 쓸 때 여는 태그(1번 묶음)를 그대로 살려야 속성이 안 사라진다.
#
# (2026-08-01, 피드백 033-a) **자식 태그가 든 조각까지 잡는다** (`.*?`).
# 예전 판(`[^<]*`)은 `<hp:t><hp:fwSpace/>\\</hp:t>` 처럼 안에 태그가 있으면
# 통째로 건너뛰었다 — 수능양식의 "빈칸 2개 중 1개만 보인다"의 정체다
# (실측 spikes/hidden_slot_spike.py: 글상자가 아니라 고정폭 공백 태그였다).
# 등록(GetTextFile)은 그 글자를 세고 채우기 창은 못 세니 개수가 어긋났다.
RUN_RE = re.compile(r"(<hp:t\b[^>]*>)(.*?)</hp:t>", re.S)

# 조각 안의 자식 태그 (<hp:fwSpace/>·<hp:lineBreak/> …) — 읽을 때 눕힌다
_CHILD_TAG_RE = re.compile(r"<[^>]+>")

SLOT_MARK = "\\"        # 템플릿 빈칸 표시 — 이게 든 조각을 '채울 자리'로 본다

# 주고받는 줄 형식:  [3] 내용
LINE_RE = re.compile(r"^\s*\[(\d+)\]\s?(.*)$")

# ── 자리 문법 (2026-07-27 확정) ─────────────────────────
# 자리는 **역슬래시로 열고 역슬래시로 닫는다**. 혼자 오는 역슬래시는 없다.
#     \\        이름 없는 자리
#     \학년\    이름 있는 자리 — 이름이 하나라도 있으면 누를 때 표가 뜬다
#     \         옛 문법 — 계속 읽어주되, 저장할 때 \\ 로 자동 정리한다
#
# 왜 쌍인가 (사용자 결정): 역슬래시 개수와 자리 개수가 눈으로 세어 같아진다.
#   `\학년\` 이 슬래시 둘·자리 하나라 어긋나던 혼동이 사라진다.
#
# 이름에 쓸 수 있는 글자를 한글·영문·숫자·밑줄로 좁힌 이유 (중요):
#   느슨하게 두면 서로 다른 빈칸 둘이 이름표 하나로 잘못 묶인다 —
#   발문 템플릿의 `\. \` 가 이름 ". " 인 토큰으로 읽히는 식이다.
#   공백·마침표를 빼면 그런 오인이 생기지 않는다.
NAME_CHARS = r"[0-9A-Za-z가-힣_]{1,20}"
# 쌍(이름은 선택)을 먼저, 옛 홑 역슬래시를 나중에 시도한다
TOKEN_RE = re.compile(r"\\(" + NAME_CHARS + r")?\\|\\")

# \본문\ 은 채울 자리가 아니라 '여기가 본문 시작'이라는 표시다 (engine_library).
RESERVED_NAMES = {"본문"}

UNNAMED_PREFIX = "빈칸 "


def _section_names(zf):
    return [n for n in zf.namelist() if SECTION_RE.search(n)]


def _flat(inner):
    r"""조각 속 글자를 사람이 읽을 모양으로 — 자식 태그는 **공백**으로 눕힌다.

    공백인 이유(033-a): 태그 양쪽의 홑 `\` 가 붙어 한 쌍(`\\`)으로 보이는
    오인을 막는다. _hidden_token_count 가 세던 방식과 같은 규칙이다.
    """
    return saxutils.unescape(_CHILD_TAG_RE.sub(" ", inner))


def read_runs(hwpx_path):
    """(번호, 글자) 목록. 번호는 문서 전체에서 조각이 나오는 순서다."""
    runs = []
    with zipfile.ZipFile(hwpx_path) as zf:
        for name in _section_names(zf):
            xml = zf.read(name).decode("utf-8")
            for m in RUN_RE.finditer(xml):
                runs.append((len(runs), _flat(m.group(2))))
    return runs


def _count_tokens(text):
    """채울 자리 토큰 수 — 본문 표시(\\본문\\)는 빼고 센다."""
    return sum(1 for t in TOKEN_RE.finditer(text)
               if t.group(1) not in RESERVED_NAMES)


def _hidden_token_count(xml):
    r"""RUN_RE 가 못 여는 조각 속의 자리 토큰 수 (경고용 안전망).

    (2026-08-01, 033-a) RUN_RE 가 자식 태그 든 조각까지 잡게 되면서 평소에는
    **0 이 정상**이다. 그래도 남겨 둔다 — 아직 모르는 모양(주석·변경추적 등
    `<hp:t>` 밖의 글자)이 나타나면 여기서 다시 잡힌다. 문서 전체를 통째로
    눕혀 센 것에서 조각 단위로 읽은 만큼을 뺀다.
    """
    stripped = _CHILD_TAG_RE.sub(" ", xml)
    total = _count_tokens(saxutils.unescape(stripped))
    exposed = sum(_count_tokens(_flat(m.group(2)))
                  for m in RUN_RE.finditer(xml))
    return max(0, total - exposed)


def hidden_slot_count(hwpx_path):
    r"""파일 전체에서 RUN_RE 가 못 여는 조각 속의 자리 토큰 수.

    표 창이 "채울 자리 없음"을 판정하기 전에 부른다 — 빈칸이 **전부**
    줄바꿈·탭 태그에 가려진 양식을 '자리 없음'으로 오판해 표시(\)를
    지워 버리고 열면 안 되기 때문 (2026-07-31 안전 감사 후속).
    """
    total = 0
    with zipfile.ZipFile(hwpx_path) as zf:
        for name in _section_names(zf):
            total += _hidden_token_count(zf.read(name).decode("utf-8"))
    return total


def slots(hwpx_path):
    r"""채울 자리만 골라낸다 — 빈칸 표시(\)가 든 조각.

    빈칸이 하나도 없으면 빈 목록이 아니라 **글자가 있는 조각 전부**를 돌려준다.
    빈칸을 미리 심어두지 않은 양식도 "이 중에 골라 고치세요"로 쓸 수 있게 하기 위함.
    """
    runs = read_runs(hwpx_path)
    marked = [(i, t) for i, t in runs if SLOT_MARK in t]
    if marked:
        return marked
    return [(i, t) for i, t in runs if t.strip()]


def to_worksheet(hwpx_path, title="양식"):
    """AI(사람)에게 붙여넣을 주고받기 문서를 만든다."""
    runs = read_runs(hwpx_path)
    targets = slots(hwpx_path)
    preview = "\n".join(t for _, t in runs if t.strip()) or "(글자 없음)"
    lines = [
        f"# 양식: {title}",
        "#",
        "# 아래 [번호] 뒤의 내용을 채워서 **그대로 돌려주세요**.",
        "# - 번호는 바꾸지 마세요. 번호로 원래 자리를 찾습니다.",
        "# - 줄 순서는 바뀌어도 되고, 안 채울 줄은 빼도 됩니다.",
        r"# - \ 는 채워야 할 빈칸입니다.",
        "",
        "# ── 문서 미리보기 (어떤 양식인지 파악용) ──",
        *[f"#   {line}" for line in preview.splitlines()],
        "",
        "# ── 채울 자리 ──",
    ]
    lines += [f"[{i}] {t}" for i, t in targets]
    return "\n".join(lines)


def parse_worksheet(text):
    """채워서 돌려받은 문서 → {번호: 글자}. 주석(#)과 빈 줄은 무시."""
    out = {}
    for raw in (text or "").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = LINE_RE.match(raw)
        if m:
            out[int(m.group(1))] = m.group(2).rstrip()
    return out


def _write_zip(zf, dst_path, rewritten):
    r"""바꾼 파일만 갈아끼워 dst 로 쓴다 — **임시 파일에 다 쓴 뒤 바꿔치기**.

    바꾸지 않는 파일은 **읽은 그대로 다시 쓴다** — 압축 방식과 순서까지
    유지해야 한글이 군말 없이 연다.

    dst 를 바로 "w" 로 열면 그 순간 기존 파일이 0바이트로 잘린다 — 중간에
    실패하면 깨진 .hwpx 가 남고, 같은 이름(_완성.hwpx)을 다시 쓰는 흐름에선
    멀쩡하던 이전 결과물까지 날아간다. 임시 파일에 완성한 뒤 os.replace 로
    바꿔치기하면 실패해도 원래 파일이 그대로다 (2026-07-31).
    """
    dst = os.fspath(dst_path)
    tmp = dst + ".tmp"
    try:
        with zipfile.ZipFile(tmp, "w") as out:
            for item in zf.infolist():
                data = rewritten.get(item.filename)
                if data is None:
                    data = zf.read(item.filename)
                info = zipfile.ZipInfo(item.filename, date_time=item.date_time)
                info.compress_type = item.compress_type
                info.external_attr = item.external_attr
                out.writestr(info, data)
        os.replace(tmp, dst)
    except Exception:
        try:
            os.remove(tmp)              # 실패한 찌꺼기는 치운다
        except OSError:
            pass
        raise


def fill(src_hwpx, dst_hwpx, replacements):
    """replacements({번호: 글자})를 넣어 새 HWPX 로 저장. 반환: 실제로 바꾼 개수.

    바꾼 개수는 치환 콜백 안에서 직접 센다 — 예전처럼 "요청한 번호가 범위
    안인가"로 세면, 실제로는 못 바꾼 것도 바꾼 것처럼 보고된다 (2026-07-31).
    """
    changed = [0]
    counter = [-1]
    with zipfile.ZipFile(src_hwpx) as zf:

        def _sub(m):
            counter[0] += 1
            if counter[0] not in replacements:
                return m.group(0)
            changed[0] += 1
            # 자식 태그(<hp:fwSpace/> 등)가 든 조각이면 태그가 함께 사라진다
            # (2026-08-01, 033-a). 사용자는 눕힌 글자를 보고 조각 **통째**의
            # 대체 글을 주므로 이게 맞는 동작이다 — 고정폭 공백 하나가 새 글로
            # 바뀌는 것뿐이지만, 소리 없이 지나가지는 않게 기록만 남긴다.
            if _CHILD_TAG_RE.search(m.group(2)):
                applog.info("양식 채우기: 자식 태그가 든 조각 %d번을 통째로 "
                            "갈아끼움" % counter[0])
            return "%s%s</hp:t>" % (
                m.group(1), saxutils.escape(replacements[counter[0]]))

        rewritten = {}
        hidden = 0
        for name in _section_names(zf):
            xml = zf.read(name).decode("utf-8")
            hidden += _hidden_token_count(xml)
            new_xml = RUN_RE.sub(_sub, xml)
            if new_xml != xml:
                rewritten[name] = new_xml.encode("utf-8")
        if hidden:
            applog.warn("양식 채우기: 빈칸 %d개가 줄바꿈·탭 태그에 가려 "
                        "목록에 안 잡혔습니다" % hidden)
        _write_zip(zf, dst_hwpx, rewritten)
    return changed[0]


def _walk_tokens(text, counter):
    r"""글자 조각 안의 채울 자리를 순서대로 훑는다.

    돌려주는 것: (정규식 match, 자리 이름). 이름표가 아닌 홑 `\` 는
    '빈칸 1', '빈칸 2' … 로 번호를 붙인다 — 이름을 안 심은 옛 양식도
    같은 표에서 채울 수 있게 하기 위함. counter 는 문서 전체에서 이어져야
    하므로 호출한 쪽이 [0] 같은 통을 넘긴다.
    """
    for m in TOKEN_RE.finditer(text):
        name = m.group(1)
        if name and name not in RESERVED_NAMES:
            yield m, name
        elif name:
            continue                    # \본문\ — 건너뛴다
        else:
            counter[0] += 1
            yield m, f"{UNNAMED_PREFIX}{counter[0]}"


def token_list(text):
    r"""글자 안의 자리 토큰을 **문서 순서대로** — 이름 없는 자리는 "".

    물감을 저장할 때 이 목록을 항목에 적어 둔다(slot_names). 표를 그릴 때는
    이름으로 칸을 만들고, 채울 때는 이 순서대로 값을 늘어놓는다.
    """
    out = []
    for m in TOKEN_RE.finditer(text or ""):
        name = m.group(1)
        if name in RESERVED_NAMES:
            continue
        out.append(name or "")
    return out


def named_slots(hwpx_path):
    """채울 자리 목록 — [(이름, 나온 횟수)]. 문서에 나온 순서.

    같은 이름이 여러 곳에 있으면 **한 줄로 합친다**. 학년이 머리말과 본문에
    겹쳐 있어도 사람은 한 번만 치면 된다 (채울 때 전부 들어간다).
    """
    order, count = [], {}
    counter = [0]
    for _, text in read_runs(hwpx_path):
        for _m, name in _walk_tokens(text, counter):
            if name not in count:
                order.append(name)
                count[name] = 0
            count[name] += 1
    return [(n, count[n]) for n in order]


def fill_named(src_hwpx, dst_hwpx, values):
    r"""이름표 자리에 값을 넣어 새 HWPX 로 저장.

    반환: {"filled": 채운 수, "wiped": 지운 수,
           "missing": {이름: 횟수}, "hidden": 못 읽은 빈칸 수}.

    지우는 것은 **이름이 values 에 있는데 값이 빈 경우**뿐이다 — 사용자가
    그 칸을 비워 둔 것이니 토큰만 걷어낸다 (그대로 두면 `\교시\` 가 인쇄물에
    남는다). 이름이 values 에 **아예 없으면**(AI 가 이름을 바꿔 온 경우 등)
    토큰을 문서에 그대로 남기고 missing 으로 센다 — 예전엔 이 경우도 지워
    버려서, 이름이 어긋나면 양식 전체가 빈 채로 나오는데 성공으로 보고됐다
    (2026-07-31). hidden 은 줄바꿈·탭 태그에 가려 아예 못 읽는 빈칸 수다.
    나머지 바이트는 건드리지 않으므로 표·병합·글꼴은 그대로다 (fill 과 같은 이유).
    """
    filled = wiped = 0
    missing = {}
    counter = [0]

    def _sub_run(m):
        nonlocal filled, wiped
        # <hp:t> 안쪽 글자만 토큰 치환한다 (여는 태그의 속성도 그대로 살린다)
        inner = saxutils.unescape(m.group(2))
        out, last = [], 0
        for tm, name in _walk_tokens(inner, counter):
            if name not in values:
                # 이름이 안 맞는 토큰 — 지우지 말고 그대로 남긴다
                missing[name] = missing.get(name, 0) + 1
                continue
            out.append(inner[last:tm.start()])
            if values[name]:
                out.append(values[name])
                filled += 1
            else:
                wiped += 1              # 일부러 비워 둔 칸 — 토큰만 사라진다
            last = tm.end()
        if not out:
            return m.group(0)           # 바꿀 것이 없던 조각 — 그대로 둔다
        out.append(inner[last:])
        return "%s%s</hp:t>" % (m.group(1), saxutils.escape("".join(out)))

    with zipfile.ZipFile(src_hwpx) as zf:
        rewritten = {}
        hidden = 0
        for name in _section_names(zf):
            xml = zf.read(name).decode("utf-8")
            hidden += _hidden_token_count(xml)
            new_xml = RUN_RE.sub(_sub_run, xml)
            if new_xml != xml:
                rewritten[name] = new_xml.encode("utf-8")
        if hidden:
            applog.warn("양식 채우기: 빈칸 %d개가 줄바꿈·탭 태그에 가려 "
                        "채우지 못했습니다" % hidden)
        if missing:
            applog.warn("양식 채우기: 이름이 맞지 않아 남겨 둔 자리 — %s"
                        % ", ".join(f"{n}×{c}" for n, c in missing.items()))
        _write_zip(zf, dst_hwpx, rewritten)
    return {"filled": filled, "wiped": wiped,
            "missing": missing, "hidden": hidden}


def to_named_markdown(slots, values=None, title="양식"):
    """표의 내용을 글자로 — AI 에게 시킬 때 붙여넣는 형식."""
    values = values or {}
    lines = [f"# 양식: {title}", "#",
             "# 아래 각 줄의 ':' 뒤를 채워서 그대로 돌려주세요.",
             "# 이름은 바꾸지 마세요 — 이름으로 자리를 찾습니다.", ""]
    for name, n in slots:
        tail = f"   # {n}곳에 들어갑니다" if n > 1 else ""
        lines.append(f"{name}: {values.get(name, '')}{tail}")
    return "\n".join(lines)


def parse_named_markdown(text):
    """채워서 돌려받은 글자 → ({이름: 값}, 못 읽은 줄 목록).

    주석(#)과 빈 줄은 무시한다. ':' 이 없는 줄은 **못 읽은 줄**로 모아
    돌려준다 — 예전엔 소리 없이 버려져서, AI 가 형식을 어겨도 몇 줄이
    사라졌는지 알 길이 없었다 (2026-07-31).
    """
    out, dropped = {}, []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            dropped.append(line)
            continue
        name, _, val = line.partition(":")
        # 꼬리 주석 제거 — **공백 뒤에 오는 #** 만 주석으로 본다.
        # to_named_markdown 이 붙이는 "   # 2곳에 들어갑니다" 꼴이 대상이다.
        # 예전처럼 # 앞을 몽땅 자르면 "C# 프로그래밍" 이 "C" 가 된다 (2026-07-31).
        val = re.sub(r"\s+#.*$", "", val).strip()
        if name.strip():
            out[name.strip()] = val
    return out, dropped


def unfilled_marks(hwpx_path):
    r"""아직 빈칸(\)이 남아 있는 조각 — 채우고 나서 확인용."""
    return [(i, t) for i, t in read_runs(hwpx_path) if SLOT_MARK in t]
