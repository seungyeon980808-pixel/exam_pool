# -*- coding: utf-8 -*-
r"""커스텀 팔레트 — 사용자가 만드는 탭 + 블럭.

메인 창의 고정 도구(원문자·보기박스·빠른입력 등)를 없애고, 대신 사용자가
탭을 만들어 원하는 블럭을 배치한다. 블럭은 클릭하면 실행되는 최소 단위:

  블럭 = {type, span, ...}
    문자    {"type":"char",     "value":"√",        "span":1}
    템플릿  {"type":"template",  "template":"결재란", "span":2}
    기능    {"type":"function",  "name":"내 강조",
             "actions":[{"func":"글씨체","value":"굴림"}, ...], "span":1}

  탭   = {"name":..., "cols":5, "blocks":[블럭, ...]}

저장은 settings.config(config.json)의 "palette_tabs" 키. 개인 로컬 데이터.
기본 서식(‘기본 서식으로 변환’ 대상)은 "default_format" 키.

이 두 키의 **소유자는 이 모듈**이다 (개선안 21). settings.py 는 같은 파일의
"profiles"/"active_profile" 을 소유한다. 전체 소유권 표는
settings.CONFIG_KEY_OWNERS 에 있다. 파일 입출력은 settings 의
get_config_value/set_config_value 만 거친다 — 읽고 쓰는 코드를 두 벌 두지 않기 위함.
"""

import copy
from hwp_palette.core import applog
from hwp_palette.core import settings
from hwp_palette.model import builtin_actions

TABS_KEY = "palette_tabs"
DEFAULT_FORMAT_KEY = "default_format"
DEFAULT_COLS = 15       # 격자 가로 칸 수. 칸 크기는 폭에 맞춰 정해진다(정사각형).
# 칸 수의 최소값 (사용자 결정 2026-07-25). 메인 창이 어차피 8칸 폭을 확보하므로
# (main._MIN_GRID_COLS), 그보다 줄여 봐야 오른쪽에 빈 공간만 남고 좁아지지 않는다
# — 줄인 티가 안 나는 조작은 아예 못 하게 막는 게 덜 헷갈린다.
MIN_COLS = 8

# 블럭은 격자 위의 사각형이다: (row, col) 에서 span×rows 칸을 차지한다.
#   span = 가로 칸 수, rows = 세로 줄 수
# 예전에는 좌표 없이 목록 순서대로 흘려 배치했는데, 그러면 '세로로 큰 블럭'을
# 만들 수 없었다(줄을 건너뛰는 개념이 없으므로). 2026-07-19 좌표 방식으로 전환.
BLOCK_POS_KEYS = ("row", "col", "span", "rows")

# 메인 창 상단(변환 버튼 옆)에 그려지는 특수 탭. 팔레트 탭 줄에는 안 나온다.
MAIN_TAB = "메인"

DEFAULT_FORMAT = {
    "font": "함초롬바탕",
    "size_pt": 10.0,
    "line_spacing": 160,   # %
    "align": 0,            # 0=왼쪽/양쪽 기본
    "spacing": 0,          # 자간
}


# ── 탭 ─────────────────────────────────────────────────
def _seed_tabs():
    """새 설치: 기존 빠른입력 기호를 '빠른입력' 탭의 문자 블럭으로 이관."""
    blocks = []
    for sym in settings.get_quick_buttons():
        if sym and sym.strip():
            blocks.append({"type": "char", "value": sym, "span": 1})
    return [{"name": "빠른입력", "cols": DEFAULT_COLS, "blocks": blocks}]


MAIN_TOOLS_SEEDED_KEY = "main_tools_seeded"


def _seed_main_tools(tabs):
    r"""'메인' 탭에 기본 도구 블럭을 **딱 한 번** 깔아 준다 (2026-07-25).

    사진·특수문자·양식 채우기·기본 서식은 예전에 메인 화면에 코드로 박혀 있었다.
    이제 블럭이 되어 사용자가 지우거나 옮길 수 있는데, 그러면 처음 켠 사람은
    빈 자리만 보게 된다. 그래서 첫 실행에 한 번 깔아 준다.

    **한 번만** 깔아야 한다 — 안 그러면 사용자가 지운 것이 다음 실행에 되살아난다.
    그래서 '깔았다'는 사실을 config 에 기록한다(블럭이 비었는지로 판단하면
    일부러 비운 사람에게 계속 되살아난다).

    반환: 무언가 바꿨으면 True.
    """
    if settings.get_config_value(MAIN_TOOLS_SEEDED_KEY, False):
        return False
    main = next((t for t in tabs if t.get("name") == MAIN_TAB), None)
    if main is None:
        return False
    # 이미 다른 블럭이 있어도 **건너뛰면 안 된다** — 이 넷은 예전에 화면에 늘
    # 붙어 있던 것이라, 안 깔면 사용자는 쓰던 기능을 잃는다. 빈자리를 찾아
    # 나란히 놓는다(있던 블럭은 그대로 둔다).
    cols = int(main.get("cols") or 8)
    for key in builtin_actions.DEFAULT_MAIN_KEYS:
        span, rows = builtin_actions.DEFAULT_SPANS.get(key, (2, 1))
        row, col = find_free_spot(main["blocks"], cols, span=span, rows=rows)
        main["blocks"].append({"type": "builtin", "key": key,
                               "name": builtin_actions.name_of(key),
                               "span": span, "rows": rows,
                               "row": row, "col": col})
    settings.set_config_value(MAIN_TOOLS_SEEDED_KEY, True)
    return True


def count_protected(blocks, key):
    """그 탭에 있는 보호 대상(변환 등) 블럭 수 — 마지막 하나는 못 지운다."""
    return sum(1 for b in blocks
               if b.get("type") == "builtin" and b.get("key") == key)


def protected_key_of(block):
    """이 블럭이 보호 대상이면 그 key, 아니면 None."""
    if block.get("type") != "builtin":
        return None
    key = block.get("key")
    return key if key in builtin_actions.PROTECTED_KEYS else None


def _ensure_protected_blocks(tabs):
    r"""'변환' 같은 보호 대상 도구가 메인 탭에 **하나는 있게** 한다 (2026-07-25).

    변환 버튼이 화면에 고정 위젯으로 박혀 있다가 도구 블럭으로 옮겨졌다.
    이미 쓰던 사람의 메인 탭에는 그 블럭이 없으므로 여기서 한 번 넣어 준다.
    _seed_main_tools 와 달리 **매번 확인**한다 — 어쩌다 사라져도 되살아나야
    한다(삭제는 UI 가 막지만, 옛 설정 파일이나 손편집까지 막을 수는 없다).

    반환: 무언가 넣었으면 True.
    """
    main = next((t for t in tabs if t.get("name") == MAIN_TAB), None)
    if main is None:
        return False
    changed = False
    cols = int(main.get("cols") or 8)
    for key in builtin_actions.PROTECTED_KEYS:
        if count_protected(main.get("blocks", []), key):
            continue
        span, rows = builtin_actions.DEFAULT_SPANS.get(key, (2, 1))
        row, col = find_free_spot(main["blocks"], cols, span=span, rows=rows)
        main["blocks"].append({"type": "builtin", "key": key,
                               "name": builtin_actions.name_of(key),
                               "span": span, "rows": rows,
                               "row": row, "col": col})
        changed = True
    return changed


# load_tabs 결과 캐시 (2026-07-28, 버벅임 1단계): 렌더·드래그 놓기·창고
# 갱신·3초 폴링이 전부 load_tabs 를 불러, 상호작용 한 번에 파일 파싱과
# 하위호환 이전 스캔이 수십 번 돌았다. config.json 세대(config_token)가
# 같으면 이전 결과의 **깊은 사본**을 돌려준다 — 사본이어야 예전처럼
# "부를 때마다 새 객체"라는 약속이 유지된다 (호출부가 고쳐도 서로 안 샌다).
_tabs_cache = {"tok": None, "tabs": None}


def load_tabs():
    tok = settings.config_token()
    if tok is not None and _tabs_cache["tok"] == tok:
        return copy.deepcopy(_tabs_cache["tabs"])
    tabs = settings.get_config_value(TABS_KEY, None)
    if not isinstance(tabs, list) or not tabs:
        tabs = _seed_tabs()
        save_tabs(tabs, _record=False)   # 첫 생성 — 되돌릴 과거가 없다
    # 하위호환 기본값
    migrated = False
    if not any(t.get("name") == MAIN_TAB for t in tabs):
        # 메인 버튼칸 탭 — 변환 버튼 옆 영역. 환경설정에서 다른 탭처럼 편집한다.
        tabs.append({"name": MAIN_TAB, "cols": 8, "blocks": []})
        migrated = True
    if _seed_main_tools(tabs):
        migrated = True
    if _ensure_protected_blocks(tabs):
        migrated = True
    if _drop_retired_tools(tabs):
        migrated = True
    for t in tabs:
        t.setdefault("cols", DEFAULT_COLS)
        # 최소 칸 수 도입(2026-07-25) 전에 만든 탭은 8칸 미만일 수 있다 — 올린다
        if int(t.get("cols") or DEFAULT_COLS) < MIN_COLS:
            t["cols"] = MIN_COLS
            migrated = True
        t.setdefault("blocks", [])
        if _migrate_positions(t):
            migrated = True
        for b in t["blocks"]:
            b.setdefault("span", 2 if b.get("type") == "template" else 1)
            # 구 데이터: 템플릿을 '이름'으로 참조 → 고유 id(ref)로 이전.
            # 이름으로 참조하면 이름 변경/중복 시 연결이 끊긴다.
            if (b.get("type") == "template" and not b.get("ref")
                    and b.get("template") and _migrate_template_ref(b)):
                migrated = True
            # 2026-07-27 디자인 개편: 자유 색 고르개로 골라 둔 원색을 12색
            # 파스텔 중 가장 가까운 것으로 옮긴다. 안 옮기면 새 화면 안에
            # 네온 초록 하나가 남아 그것만 튄다.
            if _migrate_block_color(b):
                migrated = True
    if migrated:
        # 하위호환 이전은 사용자의 편집이 아니므로 실행취소에 쌓지 않는다
        save_tabs(tabs, _record=False)
    # 이전(migration)까지 끝난 최종 모습을 캐시한다 — save_tabs 를 탔다면
    # 세대 표식도 그 뒤의 것이어야 다음 호출이 캐시를 쓴다
    _tabs_cache["tok"] = settings.config_token()
    _tabs_cache["tabs"] = copy.deepcopy(tabs)
    return tabs


def _drop_retired_tools(tabs):
    """없어진 도구 블럭을 팔레트에서 걷어낸다. 하나라도 지웠으면 True.

    도구를 카탈로그에서 빼는 것만으로는 이미 놓여 있는 블럭이 안 사라진다 —
    이름도 실행도 없는 '모르는 키' 칸으로 남아 누르면 아무 일도 안 난다.
    없앤 기능은 화면에서도 없어야 한다 (2026-07-31 '기본 서식' 폐지).
    """
    from hwp_palette.model import builtin_actions      # 데이터 전용 모듈
    retired = set(builtin_actions.RETIRED_KEYS)
    if not retired:
        return False
    changed = False
    for t in tabs:
        keep = [b for b in t.get("blocks", [])
                if not (b.get("type") == "builtin" and b.get("key") in retired)]
        if len(keep) != len(t.get("blocks", [])):
            t["blocks"] = keep
            changed = True
    if changed:
        applog.info("없어진 도구 블럭을 팔레트에서 정리했습니다 — "
                    + ", ".join(sorted(retired)))
    return changed


def _migrate_block_color(block):
    r"""블럭의 사용자 지정 색을 12색 파스텔로 맞춘다. 바꿨으면 True.

    이미 파스텔 중 하나면 그대로 둔다 — 매번 저장을 부르지 않기 위함.
    theme 를 최상위에서 import 하지 않는 이유: palette 는 화면이 없는
    저장소 모듈이라 테스트에서 Tk 없이 임포트된다.
    """
    color = block.get("color")
    if not color:
        return False
    try:
        from hwp_palette.design import theme
        if any(color.lower() == hexv.lower()
               for _n, hexv in theme.PASTELS + theme.PASTELS_DARK):
            return False
        near = theme.nearest_pastel(color)
    except Exception as e:
        applog.exc(f"블럭 색 이관 실패 (그대로 둠) — {color!r}", e)
        return False
    if not near or near.lower() == color.lower():
        return False
    block["color"] = near
    return True


def _migrate_positions(tab):
    """구 데이터(좌표 없음)를 흐름 배치 순서 그대로 (row, col) 로 굳힌다.

    예전에는 블럭 목록 순서대로 왼→오, 넘치면 다음 줄로 흘려 배치했다. 그 결과와
    똑같이 보이도록 좌표를 매겨 두면, 사용자가 보던 배치가 그대로 유지된다.
    반환: 바뀐 게 있으면 True.
    """
    blocks = tab.get("blocks", [])
    if all("row" in b and "col" in b for b in blocks):
        return False
    cols = max(1, int(tab.get("cols", DEFAULT_COLS)))
    r = c = 0
    for b in blocks:
        span = max(1, min(int(b.get("span", 1)), cols))
        if c + span > cols:
            r += 1
            c = 0
        b["row"], b["col"] = r, c
        b["span"], b["rows"] = span, max(1, int(b.get("rows", 1)))
        c += span
        if c >= cols:
            r += 1
            c = 0
    return True


def occupied_cells(blocks, skip_index=None):
    """블럭들이 차지한 칸 집합 {(row, col), ...}."""
    used = set()
    for i, b in enumerate(blocks):
        if i == skip_index:
            continue
        r0, c0 = int(b.get("row", 0)), int(b.get("col", 0))
        for dr in range(max(1, int(b.get("rows", 1)))):
            for dc in range(max(1, int(b.get("span", 1)))):
                used.add((r0 + dr, c0 + dc))
    return used


def area_is_free(blocks, row, col, span, rows, skip_index=None):
    """그 사각형이 비어 있는가 (다른 블럭과 안 겹치는가)."""
    used = occupied_cells(blocks, skip_index)
    return all((row + dr, col + dc) not in used
               for dr in range(rows) for dc in range(span))


def find_free_spot(blocks, cols, span=1, rows=1, max_rows=200):
    """span×rows 가 들어갈 첫 빈자리 (row, col). 못 찾으면 맨 아래 새 줄."""
    used = occupied_cells(blocks)
    for r in range(max_rows):
        for c in range(cols - span + 1):
            if all((r + dr, c + dc) not in used
                   for dr in range(rows) for dc in range(span)):
                return r, c
    bottom = max((int(b.get("row", 0)) + int(b.get("rows", 1))
                  for b in blocks), default=0)
    return bottom, 0


def _migrate_template_ref(block):
    """{'template': '결재란'} → {'ref': <id>} 로 이전. 성공 시 True.

    library 를 최상위에서 import 하면 순환 참조라 지역 import.
    """
    try:
        from hwp_palette.model import library         # 순환 참조 회피 (library → palette → library)
        for it in library.load().get("템플릿", []):
            if it.get("name") == block.get("template"):
                block["ref"] = it["id"]
                return True
        applog.warn(f"팔레트 블럭이 가리키는 템플릿을 못 찾음: "
                    f"{block.get('template')!r} (삭제된 것 같음)")
    except Exception as e:
        applog.exc("팔레트 블럭 ref 마이그레이션 실패", e)
    return False


# ── 실행 취소 (UI 제안 1) ───────────────────────────────
# 블럭을 잘못 지우면 되돌릴 방법이 없었다. 저장이 JSON 한 덩어리라 통째로 떠 두는
# 비용이 사실상 0이라, 저장할 때마다 직전 상태를 쌓아 둔다.
# 프로그램을 켠 동안만 유지한다(파일에 안 남긴다) — 되돌리기는 '방금 한 실수'를
# 위한 것이지 어제 것을 위한 게 아니고, 그건 backup.py 가 맡는다.
_UNDO_LIMIT = 30
_undo_stack = []
_redo_stack = []


def save_tabs(tabs, _record=True):
    if _record:
        prev = settings.get_config_value(TABS_KEY, None)
        if prev is not None and prev != tabs:
            _undo_stack.append(copy.deepcopy(prev))
            del _undo_stack[:-_UNDO_LIMIT]
            _redo_stack.clear()     # 새 편집이 생기면 '다시 실행'은 무효
    settings.set_config_value(TABS_KEY, tabs)
    _tabs_cache["tok"] = settings.config_token()
    _tabs_cache["tabs"] = copy.deepcopy(tabs)


def can_undo():
    return bool(_undo_stack)


def can_redo():
    return bool(_redo_stack)


def undo():
    """직전 편집을 되돌린다. 되돌렸으면 True."""
    if not _undo_stack:
        return False
    cur = settings.get_config_value(TABS_KEY, None)
    if cur is not None:
        _redo_stack.append(copy.deepcopy(cur))
    save_tabs(_undo_stack.pop(), _record=False)
    return True


def redo():
    """되돌린 것을 다시 실행한다."""
    if not _redo_stack:
        return False
    cur = settings.get_config_value(TABS_KEY, None)
    if cur is not None:
        _undo_stack.append(copy.deepcopy(cur))
    save_tabs(_redo_stack.pop(), _record=False)
    return True


def add_tab(name, cols=DEFAULT_COLS):
    tabs = load_tabs()
    name = _unique_tab_name(tabs, name or "새 탭")
    tabs.append({"name": name, "cols": cols, "blocks": []})
    save_tabs(tabs)
    return name


def _unique_tab_name(tabs, name):
    existing = {t["name"] for t in tabs}
    if name not in existing:
        return name
    n = 2
    while f"{name} ({n})" in existing:
        n += 1
    return f"{name} ({n})"


def rename_tab(index, new_name):
    tabs = load_tabs()
    if 0 <= index < len(tabs):
        others = [t["name"] for i, t in enumerate(tabs) if i != index]
        if new_name in others:
            raise ValueError(f"이미 있는 탭 이름입니다: {new_name}")
        tabs[index]["name"] = new_name
        save_tabs(tabs)


def delete_tab(index):
    tabs = load_tabs()
    if 0 <= index < len(tabs):
        del tabs[index]
        save_tabs(tabs)


def move_tab(index, delta):
    tabs = load_tabs()
    j = index + delta
    if 0 <= index < len(tabs) and 0 <= j < len(tabs):
        tabs[index], tabs[j] = tabs[j], tabs[index]
        save_tabs(tabs)


def set_tab_cols(index, cols):
    tabs = load_tabs()
    if 0 <= index < len(tabs):
        tabs[index]["cols"] = max(MIN_COLS, int(cols))
        save_tabs(tabs)


# ── 블럭 ───────────────────────────────────────────────
def add_block(tab_index, block, row=None, col=None):
    """블럭을 추가한다. 자리를 안 주면 첫 빈자리를 찾아 넣는다."""
    tabs = load_tabs()
    if not (0 <= tab_index < len(tabs)):
        return
    tab = tabs[tab_index]
    blocks = tab["blocks"]
    b = copy.deepcopy(block)
    b["span"] = max(1, int(b.get("span", 1)))
    b["rows"] = max(1, int(b.get("rows", 1)))
    if row is None or col is None:
        row, col = find_free_spot(blocks, tab.get("cols", DEFAULT_COLS),
                                  b["span"], b["rows"])
    b["row"], b["col"] = int(row), int(col)
    blocks.append(b)
    save_tabs(tabs)


def set_block_area(tab_index, block_index, row, col, span, rows):
    """블럭의 자리·크기를 한 번에 정한다. 겹치면 아무것도 하지 않고 False."""
    tabs = load_tabs()
    if not (0 <= tab_index < len(tabs)):
        return False
    blocks = tabs[tab_index]["blocks"]
    if not (0 <= block_index < len(blocks)):
        return False
    cols = tabs[tab_index].get("cols", DEFAULT_COLS)
    span, rows = max(1, min(int(span), cols)), max(1, int(rows))
    col = max(0, min(int(col), cols - span))
    row = max(0, int(row))
    if not area_is_free(blocks, row, col, span, rows, skip_index=block_index):
        return False
    b = blocks[block_index]
    b["row"], b["col"], b["span"], b["rows"] = row, col, span, rows
    save_tabs(tabs)
    return True


def grid_extent(blocks):
    """블럭들이 쓰는 줄 수 (맨 아래 줄 + 1)."""
    return max((int(b.get("row", 0)) + max(1, int(b.get("rows", 1)))
                for b in blocks), default=0)


def update_block(tab_index, block_index, block):
    tabs = load_tabs()
    if 0 <= tab_index < len(tabs) and 0 <= block_index < len(tabs[tab_index]["blocks"]):
        tabs[tab_index]["blocks"][block_index] = copy.deepcopy(block)
        save_tabs(tabs)


def delete_block(tab_index, block_index):
    tabs = load_tabs()
    if 0 <= tab_index < len(tabs) and 0 <= block_index < len(tabs[tab_index]["blocks"]):
        del tabs[tab_index]["blocks"][block_index]
        save_tabs(tabs)


def move_block(tab_index, block_index, delta):
    tabs = load_tabs()
    if not (0 <= tab_index < len(tabs)):
        return
    blocks = tabs[tab_index]["blocks"]
    j = block_index + delta
    if 0 <= block_index < len(blocks) and 0 <= j < len(blocks):
        blocks[block_index], blocks[j] = blocks[j], blocks[block_index]
        save_tabs(tabs)


def move_block_to(tab_index, block_index, new_index):
    """드래그 재배치 — block_index 블럭을 new_index 위치로 이동."""
    tabs = load_tabs()
    if not (0 <= tab_index < len(tabs)):
        return
    blocks = tabs[tab_index]["blocks"]
    if not (0 <= block_index < len(blocks)):
        return
    new_index = max(0, min(new_index, len(blocks) - 1))
    b = blocks.pop(block_index)
    blocks.insert(new_index, b)
    save_tabs(tabs)


# ── 기본 서식 ───────────────────────────────────────────
def get_default_format():
    saved = settings.get_config_value(DEFAULT_FORMAT_KEY, None) or {}
    fmt = copy.deepcopy(DEFAULT_FORMAT)
    fmt.update({k: v for k, v in saved.items() if k in DEFAULT_FORMAT})
    return fmt


def save_default_format(fmt):
    settings.set_config_value(DEFAULT_FORMAT_KEY, fmt)


# 변환 버튼 크기 설정(get/save_convert_size)은 없앴다 (2026-07-25).
# 변환 버튼이 메인 탭의 도구 블럭이 되어, 다른 블럭처럼 끌어서 크기를 바꾼다.
# 크기는 그 블럭의 span/rows 에 들어 있다.
