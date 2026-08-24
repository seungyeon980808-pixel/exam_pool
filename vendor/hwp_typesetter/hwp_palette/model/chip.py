# -*- coding: utf-8 -*-
r"""칩 — 팔레트를 물감째 통째로 주고받는 꾸러미 (사용자 기획 2026-07-26).

**왜 만드나.** A학교 선생님이 시험문제 팔레트를 정성껏 꾸몄으면, 다른 사람이
그걸 처음부터 다시 만들 이유가 없다. 닌텐도 DS 게임칩처럼 꽂으면 그대로
쓰이고, 그 안의 물감은 **받은 사람의 자산**이 되어 다른 팔레트에서도 쓰인다.

**왜 팔레트만으로는 안 되나.** 팔레트 블럭은 물감을 **가리키기만** 한다:

    {"type": "template", "ref": "ac20fd50…", "template": "소1사진"}
                                 └─ library.json 의 항목 id

받는 쪽에서 물감은 새 id 를 받아야 한다(같은 id 가 두 개면 참조가 엉킨다).
그러면 옛 id 를 가리키던 블럭은 전부 끊긴다. **이 파일이 그 다리를 놓는다** —
가져오기가 돌려준 `id_map`(보낸쪽 id → 내 id)으로 블럭의 ref 를 갈아끼운다.

ref 를 갖는 블럭은 template/form 둘뿐이다. char·function·builtin 은 내용을
블럭 안에 지니고 있어 그대로 옮기면 된다.

**형식은 하나, 입구는 둘** (사용자 결정):

    칩(zip, .hwpal)
     ├ manifest.json    이름·만든이·설명·버전
     ├ tab.json         ← 있으면 팔레트 칩, 없으면 그냥 물감 꾸러미
     ├ library.json     물감 목록 (id 대신 origin_id)
     └ fragments/*.hwp  조각 파일

받는 쪽 코드는 한 벌이다. 물감을 창고에 넣고, tab.json 이 있으면 ref 를
갈아끼워 탭까지 더한다. 없으면 거기서 끝.
"""

import json
import pathlib
import zipfile

from hwp_palette.core import applog
from hwp_palette.core import appinfo
from hwp_palette.model import library
from hwp_palette.model import palette

CHIP_EXT = ".hwpal"
CHIP_VERSION = 1
_MANIFEST = "chip.json"
_TAB = "tab.json"

# 물감을 가리키는 블럭 종류. 나머지(char·function·builtin)는 내용을 스스로
# 지니고 있어 옮길 때 손댈 것이 없다.
_REF_TYPES = ("template", "form")
_TYPE_CATEGORY = {"template": "템플릿", "form": "양식"}


def _walk_blocks(blocks):
    """팔레트 블럭을 화면 순서대로 순회한다(겹침 묶음 안쪽 포함).

    stack은 단순한 UI 장식이 아니라 여러 물감을 한 칸에 넣는 컨테이너다.
    예전 구현은 최상위 블럭만 훑어서 stack 안의 템플릿·양식 파일을 칩에서
    누락했다. 내보내기·누락 검사·ref 재연결이 모두 같은 순회를 사용한다.
    """
    for block in blocks or []:
        yield block
        if block.get("type") == "stack":
            yield from _walk_blocks(block.get("items", []))


# ── 만들기 ──────────────────────────────────────────────
def required_items(tab):
    """이 탭이 실제로 쓰는 물감 [(분류, 항목), ...].

    **고르게 하지 않는다** — 블럭의 ref 를 훑으면 무엇이 필요한지 정해져 있다.
    사용자에게 "무엇을 같이 보낼까요"를 묻는 화면이 아예 필요 없다.
    """
    data = library.load()
    by_id = {it["id"]: (cat, it) for cat in library.CATEGORIES
             for it in data[cat]}
    out, seen = [], set()
    for block in _walk_blocks(tab.get("blocks", [])):
        if block.get("type") not in _REF_TYPES:
            continue
        ref = block.get("ref")
        if not ref or ref in seen:
            continue
        seen.add(ref)
        found = by_id.get(ref)
        if found is None:
            applog.warn(f"칩 만들기: 블럭이 가리키는 물감이 없어 건너뜀 "
                        f"— {block.get('template') or block.get('form') or ref}")
            continue
        out.append(found)
    return out


def missing_refs(tab):
    """탭이 가리키는데 라이브러리에 없는 물감 이름들 (내보내기 전 경고용)."""
    data = library.load()
    have = {it["id"] for cat in library.CATEGORIES for it in data[cat]}
    return [b.get("template") or b.get("form") or "?"
            for b in _walk_blocks(tab.get("blocks", []))
            if b.get("type") in _REF_TYPES and b.get("ref") not in have]


def export_tab(tab, dest_path, note="", author=""):
    """팔레트 탭 하나를 파일로 내보낸다. 반환: {"items": 담긴 개수, "blocks": 개수}.

    팔레트는 **통째로** 나간다(사용자 결정) — 배치 자체가 전달할 가치이고,
    일부만 빼면 빈 격자가 생겨 도면이 깨진다.
    items 는 **실제로 담긴** 개수다 (조각 파일이 사라진 항목은 빠진다).
    """
    pairs = required_items(tab)
    n = library.export_items(pairs, dest_path)  # 물감 + 조각 + manifest
    _add_chip_parts(dest_path, tab=tab,
                    name=tab.get("name", "팔레트"), note=note, author=author)
    return {"items": n, "blocks": len(tab.get("blocks", []))}


def export_items(pairs, dest_path, name, note="", author=""):
    """고른 물감만 파일로 내보낸다(팔레트 없음). 반환: {"items": 담긴 개수}.

    **실제로 담긴 개수**를 돌려준다 — 조각 파일이 사라진 항목은 library 쪽에서
    건너뛰므로, 고른 개수를 그대로 보고하면 "5개 보냈다"고 해 놓고 4개만
    간 것을 아무도 모른다.
    """
    n = library.export_items(pairs, dest_path)
    _add_chip_parts(dest_path, tab=None, name=name, note=note, author=author)
    return {"items": n}


def _add_chip_parts(dest_path, tab, name, note, author):
    """library.export_items 가 만든 zip 에 칩 표지(+탭)를 덧붙인다."""
    manifest = {"chip_version": CHIP_VERSION, "name": name,
                "note": note, "author": author,
                "made_with": f"{appinfo.NAME} v{appinfo.VERSION}"}
    with zipfile.ZipFile(dest_path, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_MANIFEST, json.dumps(manifest, ensure_ascii=False,
                                          indent=2))
        if tab is not None:
            zf.writestr(_TAB, json.dumps(tab, ensure_ascii=False, indent=2))


# ── 읽어 보기 (등록 전 미리보기) ────────────────────────
def peek(src_path):
    r"""칩을 **열어 보기만** 한다 — 아무것도 바꾸지 않는다.

    등록 화면이 "무엇이 들어오고 무엇과 겹치는지"를 먼저 보여주기 위한 것이다.
    예전 가져오기는 넣은 **뒤에** "이름이 겹쳐 바꿨습니다"라고 사후 통보했다.
    순서가 반대였다 — 남의 파일이 내 창고에 섞이는 일이라 먼저 보여줘야 한다.

    반환: {"name", "note", "author", "made_with", "tab", "items",
           "labels", "known", "conflicts": {"names", "labels", "tab"}}
    """
    with zipfile.ZipFile(src_path) as zf:
        names = zf.namelist()
        manifest = (json.loads(zf.read(_MANIFEST).decode("utf-8"))
                    if _MANIFEST in names else {})
        tab = (json.loads(zf.read(_TAB).decode("utf-8"))
               if _TAB in names else None)
        if library._MANIFEST_NAME not in names:
            raise ValueError("올바르지 않은 칩 파일입니다 — library.json이 없습니다")
        lib = json.loads(zf.read(library._MANIFEST_NAME).decode("utf-8"))
    items = lib.get("items", [])

    data = library.load()
    my_names = {cat: {it["name"] for it in data[cat]}
                for cat in library.CATEGORIES}
    my_labels = {library.normalize_label(it.get("label"))
                 for cat in library.CATEGORIES for it in data[cat]}
    mine_by_origin = {it["origin_id"] for cat in library.CATEGORIES
                      for it in data[cat] if it.get("origin_id")}
    my_tabs = {t.get("name") for t in palette.load_tabs()}

    known = sum(1 for rec in items if rec.get("origin_id") in mine_by_origin)
    conflicts = {
        "names": [rec.get("name") for rec in items
                  if rec.get("name") in my_names.get(rec.get("category"), ())],
        "labels": [library.normalize_label(rec.get("label")) for rec in items
                   if library.normalize_label(rec.get("label")) in my_labels],
        "tab": (tab or {}).get("name") if tab and
               (tab or {}).get("name") in my_tabs else None,
    }
    return {"name": manifest.get("name") or pathlib.Path(src_path).stem,
            "note": manifest.get("note", ""),
            "author": manifest.get("author", ""),
            "made_with": manifest.get("made_with", ""),
            "tab": tab, "items": items, "known": known,
            "conflicts": conflicts}


# ── 등록 ────────────────────────────────────────────────
def relink(blocks, id_map):
    r"""블럭의 `ref` 를 받는 쪽 id 로 갈아끼운다. 반환: (블럭들, 못 이은 수).

    **이것이 칩의 핵심이다.** 물감은 받는 쪽에서 새 id 를 받으므로, 이 대응을
    안 해 주면 버튼이 전부 "템플릿을 찾을 수 없습니다"가 된다.

    ref 를 못 이은 블럭은 **버리지 않고 그대로 둔다** — 지우면 배치에 구멍이
    생겨 사용자가 무엇이 빠졌는지도 모른다. 남겨 두면 눌렀을 때 "찾을 수
    없습니다"라고 말해 주므로, 그 자리에 무엇이 있어야 하는지 알 수 있다.
    """
    out, lost = [], 0
    for block in blocks:
        block = dict(block)
        if block.get("type") == "stack":
            block["items"], nested_lost = relink(block.get("items", []), id_map)
            lost += nested_lost
        if block.get("type") in _REF_TYPES and block.get("ref"):
            new = id_map.get(block["ref"])
            if new:
                block["ref"] = new
            else:
                lost += 1
                applog.warn(f"칩 등록: 블럭이 가리킬 물감을 못 찾음 — "
                            f"{block.get('template') or block.get('form')}")
        out.append(block)
    return out, lost


def install(src_path):
    r"""칩을 등록한다. 물감을 창고에 넣고, 탭이 있으면 ref 를 이어 붙인다.

    반환: peek 결과 + {"added", "reused", "renamed", "relabeled",
                       "tab_name", "lost"}

    덮어쓰기는 하지 않는다 — 이름·라벨·탭 이름이 겹치면 번호를 붙인다.
    받은 파일 때문에 내 것이 사라지는 일은 없어야 한다.
    """
    # peek()와 import_archive()가 각각 "known"을 다시 계산한다 — 둘 다 전체
    # 라이브러리를 스캔하지만, peek은 UI 표시용이고 import_archive는 실제 등록이
    # 필요해서다. 구조적으로 합칠 순 있지만 두 흐름이 달라 의도적으로 남긴다.
    info = peek(src_path)
    r = library.import_archive(src_path, from_chip=info["name"])
    out = dict(info)
    out.update({k: r[k] for k in ("added", "reused", "renamed", "relabeled")})
    out["tab_name"] = None
    out["lost"] = 0

    tab = info.get("tab")
    if tab:
        blocks, lost = relink(tab.get("blocks", []), r["id_map"])
        tabs = palette.load_tabs()
        taken = {t.get("name") for t in tabs}
        name = base = tab.get("name") or "받은 팔레트"
        n = 2
        while name in taken:
            name = f"{base} ({n})"
            n += 1
        tabs.append({"name": name,
                     "cols": tab.get("cols", palette.DEFAULT_COLS),
                     "blocks": blocks})
        palette.save_tabs(tabs)
        out["tab_name"] = name
        out["lost"] = lost
    return out
