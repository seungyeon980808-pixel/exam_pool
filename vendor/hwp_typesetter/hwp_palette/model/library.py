# -*- coding: utf-8 -*-
"""개인 라이브러리 저장소 — 서식 / 문자 / 템플릿 / 양식 4종.

- 서식: 굵기·색상·자간 등 글자 서식 일부만 저장해 아무 글자에나 입히는 "델타"
- 문자: 특수문자·상용구 등 텍스트 그대로 저장해 삽입
- 템플릿: 표·결재란처럼 문서 '일부'를 조각 파일(.hwp)로 저장해 커서 위치에 삽입
- 양식: .hwp 파일 '전체'를 저장해 새 문서로 열기 (용지·여백·머리말까지 그대로)

library.json에 목록/이름/값을 저장한다(개인 파일, git 추적 제외).
템플릿·양식의 실제 내용은 fragments/ 폴더에 개별 .hwp 로 저장하고,
library.json에는 그 파일명만 참조로 남긴다.
"""

import copy
import json
import os
import pathlib
import re
import shutil
import uuid

from hwp_palette.core import applog
from hwp_palette.core import backup
from hwp_palette.model import form_fill                # 채울 자리 토큰 규칙 (이름표 \학년\)
from hwp_palette.core import paths

LIBRARY_PATH = paths.DATA_DIR / "library.json"
FRAGMENTS_DIR = paths.DATA_DIR / "fragments"

CATEGORIES = ("서식", "문자", "템플릿", "양식")

_EMPTY = {"서식": [], "문자": [], "템플릿": [], "양식": []}

# 조각 파일(.hwp)을 갖는 분류 — 삭제 시 파일도 함께 지운다
_FILE_CATEGORIES = ("템플릿", "양식")

# 라이브러리 분류 → 팔레트 블럭 타입 (고아 블럭 정리·사용처 카운트용)
#
# 서식 → "function" (2026-07-31). 창고의 '서식' 물감과 팔레트의 '서식 조합'
# 블럭이 서로 다른 물건이던 것을 **팔레트 쪽으로 합쳤다** (사용자 결정:
# "물감창고에서 서식 기능을 추가할떄랑 팔레트에서 드래그해서 서식을 추가할때랑
# 전혀 다르게 뜨는데 팔레트가 기본이 되어야 합니다"). 이제 둘 다 같은
# actions 목록이고, 팔레트 블럭은 ref 로 창고의 그것을 가리킨다.
_BLOCK_TYPE = {"템플릿": "template", "서식": "function", "문자": "char",
               "양식": "form"}


def _ensure_dirs():
    FRAGMENTS_DIR.mkdir(exist_ok=True)


def cleanup_temp_fragments():
    r"""예전 방식이 남긴 _tmp_*.hwp 찌꺼기를 지운다.

    구버전은 임시 이름으로 저장 후 이름을 바꿨는데, 그 과정이 WinError 32 로
    실패하면 _tmp_*.hwp 가 fragments/ 에 쌓였다(한글에 열린 채 남기도 했다).
    지금은 임시 파일을 아예 안 쓰므로, 남아 있던 것만 조용히 청소한다.
    한글이 아직 물고 있는 파일은 못 지우지만, 그건 그대로 두면 된다(무해).
    """
    try:
        for f in FRAGMENTS_DIR.glob("_tmp_*.hwp"):
            try:
                f.unlink()
            except OSError:
                pass        # 한글이 아직 열고 있는 것 — 다음 기회에
    except OSError:
        pass


# ── 태그 (2026-07-26, 사용자 결정 — 예전의 '분류'를 대체) ──
#
# 왜 분류를 버렸나:
#   분류는 **배타적**이었다. '합답형1사진3선지' 를 수능·시험문제·사진문항 중
#   하나만 고르라고 하니 아무도 고르지 않았고, 실제로 물감 15개가 전부 '기본'
#   이었다. 게다가 팔레트 탭이 이미 서랍 노릇을 해서 서랍 체계가 두 벌이 됐다
#   ("정리를 어디서 하는 거냐"는 혼란의 원인).
#
# 태그는 서랍이 아니라 꼬리표다:
#   · 0개도 되고 여러 개도 된다 → '미분류' 라는 억지 이름이 필요 없다
#   · 팔레트 탭(자리)과 하는 일이 달라 경쟁하지 않는다
#   · 내보낼 때 빠진다 — 남의 정리 습관은 나에게 뜻이 없다 (export_items 참고)
#
# 옛 데이터의 group 값은 **버린다**. 전부 '기본' 이라 옮길 정보가 없었다.
#
# 태그 규칙 (사용자 결정 2026-07-26): **한글만, 5글자 이내, 띄어쓰기 없음.**
# 왜 이렇게 좁히나 — 태그의 유일한 실패 모드는 같은 뜻이 여러 이름으로
# 번식하는 것이다(#수능 / #수능문제 / #수능_문제 / #sooneung). 형태를 하나로
# 강제하면 애초에 갈라질 자리가 줄고, 짧아서 칩으로 늘어놓기도 좋다.
# 띄어쓰기는 규칙 이전에 **구분자**라 태그 안에 들어올 수 없다.
TAG_MAX_LEN = 5
_TAG_RE = re.compile(r"^[가-힣ㄱ-ㅎㅏ-ㅣ]{1,%d}$" % TAG_MAX_LEN)


def is_valid_tag(tag):
    """한글 1~5글자인가. 숫자·영문·기호·공백이 섞이면 태그가 아니다."""
    return bool(_TAG_RE.match(str(tag or "").strip()))


def split_tag_input(text):
    """입력칸 글자를 토막으로 가른다 (검사 전 — 잘못된 것도 그대로 돌려준다).

    띄어쓰기와 쉼표 둘 다 구분자로 본다. `#` 는 표시용이라 벗긴다.
    """
    if not text:
        return []
    return [t for t in
            (s.strip().lstrip("#").strip()
             for s in str(text).replace(",", " ").split()) if t]


def normalize_tags(tags):
    """태그 목록 정리 — # 벗기기, 중복 제거, 순서 유지, **규칙 위반은 버림**.

    문자열 하나('수능 사진문항')로 줘도 받는다 — 입력칸에서 그대로 넘어온다.
    버리기 전에 사용자에게 알리는 일은 화면(MetaDialog)이 한다. 여기서
    조용히 버리는 것은 '어떤 경로로 들어오든 저장된 태그는 규칙을 지킨다'를
    보장하기 위한 마지막 관문이다(가져오기·옛 데이터 등).
    """
    if isinstance(tags, str):
        tags = split_tag_input(tags)
    elif tags:
        tags = [str(t).strip().lstrip("#").strip() for t in tags]
    else:
        return []
    out = []
    for t in tags:
        if t and t not in out and is_valid_tag(t):
            out.append(t)
    return out


# ── 하위 분류 (2026-07-31, 시안 docs/mockups/store-subcats.html) ──
#
# 분류(특수기호·템플릿·서식·양식) **아래**에 사용자가 만드는 한 단계 서랍이다.
# 태그(위)와 다른 점: 하위 분류는 **배타적**이고(항목당 하나), '전체' 보기가
# 없으며, 기본은 미분류다 — 분류는 의무가 아니라 선택이다.
#
# 저장 구조:
#   library.json 최상위 "subcats" = {분류: [이름, …]}  ← 빈 하위 분류도 남는다
#   각 항목의 "subcat" = 이름 ("" 또는 없음 = 미분류)
#
# '도구'는 프로그램이 가진 기능이라 하위 분류가 없다 (라이브러리 밖이므로
# 여기서는 자연히 다룰 일이 없다).
SUBCAT_UNSORTED = "미분류"      # 표시용 이름 — 저장은 "" 로 한다


def normalize_subcat(name):
    """하위 분류 이름 정리 — 앞뒤 공백 제거, '미분류' 는 빈 값과 같다."""
    name = str(name or "").strip()
    return "" if name == SUBCAT_UNSORTED else name


def list_subcats(category):
    """분류의 하위 분류 이름들 (미분류 제외, 만든 차례대로)."""
    subs = load().get("subcats") or {}
    got = subs.get(category)
    return list(got) if isinstance(got, list) else []


def _ensure_subcat(data, category, name):
    """data 안에서 하위 분류 이름을 등록해 둔다 (이미 있으면 그대로)."""
    name = normalize_subcat(name)
    if not name or category not in CATEGORIES:
        return ""
    subs = data.setdefault("subcats", {})
    lst = subs.setdefault(category, [])
    if name not in lst:
        lst.append(name)
    return name


def add_subcat(category, name):
    """하위 분류를 만든다. 반환: 정리된 이름 (빈 값·'미분류' 면 None)."""
    name = normalize_subcat(name)
    if not name or category not in CATEGORIES:
        return None
    data = load()
    _ensure_subcat(data, category, name)
    save(data)
    return name


def rename_subcat(category, old, new):
    """하위 분류 이름 바꾸기 — 그 안의 물감도 함께 옮긴다. 성공 여부 반환."""
    old, new = normalize_subcat(old), normalize_subcat(new)
    if not old or not new or old == new:
        return False
    data = load()
    subs = (data.get("subcats") or {}).get(category)
    if not isinstance(subs, list) or old not in subs:
        return False
    if new in subs:                     # 이미 있는 이름으로는 합치지 않는다 —
        return False                    # 의도치 않은 병합은 되돌릴 수 없다
    subs[subs.index(old)] = new
    for it in data.get(category, []):
        if normalize_subcat(it.get("subcat")) == old:
            it["subcat"] = new
    save(data)
    return True


def delete_subcat(category, name):
    """하위 분류를 지운다 — **그 안의 물감은 미분류로** 돌아간다 (물감은
    지워지지 않는다, 사용자 결정). 반환: 미분류로 옮긴 물감 수, 실패 시 -1."""
    name = normalize_subcat(name)
    data = load()
    subs = (data.get("subcats") or {}).get(category)
    if not name or not isinstance(subs, list) or name not in subs:
        return -1
    subs.remove(name)
    moved = 0
    for it in data.get(category, []):
        if normalize_subcat(it.get("subcat")) == name:
            it["subcat"] = ""
            moved += 1
    save(data)
    return moved


def set_subcat(category, item_id, name):
    """항목의 하위 분류를 바꾼다 ("" = 미분류). 새 이름이면 목록에도 등록."""
    data = load()
    target = next((it for it in data.get(category, [])
                   if it.get("id") == item_id), None)
    if target is None:
        return False
    target["subcat"] = _ensure_subcat(data, category, name)
    save(data)
    return True


def subcat_of(item):
    """항목의 하위 분류 이름 ("" = 미분류). 표시·거르기가 이걸로 통일한다."""
    return normalize_subcat((item or {}).get("subcat"))


def list_tags():
    """지금 쓰이는 태그 목록 — 많이 쓴 순, 같으면 이름 순 (자동완성용)."""
    counts = {}
    for cat in CATEGORIES:
        for it in load()[cat]:
            for t in it.get("tags") or []:
                counts[t] = counts.get(t, 0) + 1
    return sorted(counts, key=lambda t: (-counts[t], t))


def set_tags(category, item_id, tags):
    """항목의 태그를 통째로 갈아끼운다. 성공 여부 반환."""
    data = load()
    target = next((it for it in data.get(category, [])
                   if it.get("id") == item_id), None)
    if target is None:
        return False
    target["tags"] = normalize_tags(tags)
    save(data)
    return True


# load 결과 캐시 (2026-07-28, 버벅임 1단계): 창고 갱신 한 번이 list_items 를
# 8~9번 부르고, 그때마다 파일 전체를 읽고 파싱하고 이전(migration) 스캔까지
# 돌았다. 파일이 안 바뀌었으면(mtime·크기 동일) 이전 결과의 깊은 사본을
# 돌려준다 — 사본이어야 "부를 때마다 새 객체"라는 기존 약속이 유지된다.
_load_cache = {"tok": None, "data": None}

# 창고를 **못 읽은 상태** 표식 (2026-07-31 안전 점검). 잘린 library.json 이
# 조용히 '새 설치'처럼 보이면, 다음 save() 가 빈 창고를 그 위에 덮어써
# 물감 전부를 잃는다 — True 인 동안 save() 는 예외를 올려 저장을 멈춘다.
# 다음 읽기가 성공하면 자동으로 풀린다.
_load_failed = False


def _library_token():
    try:
        st = LIBRARY_PATH.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _atomic_write_text(path, text):
    """같은 폴더의 임시 파일에 다 쓴 뒤 os.replace 로 바꿔치기.

    쓰다가 강제 종료·디스크 오류가 나도 반쪽짜리 파일이 남지 않는다 —
    반쪽이 남으면 다음 실행이 '깨진 파일'로 읽어 창고가 비어 보인다.
    (settings._atomic_write_text 와 같은 도구 — library 는 settings 에
    기대지 않는 모듈이라 여기 한 벌 더 둔다)
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)     # 찌꺼기 청소 (최선 노력)
        except OSError:
            pass
        raise


def _recover_from_backup():
    """깨진 library.json 을 백업(.bak1~3)에서 되살려 본다. 성공 시 dict, 실패 시 None.

    망가진 원본은 library.json.damaged 로 남겨 둔다 — 복구된 백업이 최신
    편집을 놓쳤을 수 있으므로, 조사할 실물을 지우지 않는다 (최선 노력).
    """
    for _n, bak, _size in backup.list_backups(LIBRARY_PATH):
        try:
            data = json.loads(bak.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue                    # 이 백업도 못 쓴다 — 다음 것으로
        if not isinstance(data, dict):
            continue
        applog.info(f"물감 창고를 백업에서 복구 ({bak.name})")
        try:
            shutil.copyfile(LIBRARY_PATH,
                            LIBRARY_PATH.with_name(LIBRARY_PATH.name + ".damaged"))
        except OSError:
            pass
        return data
    return None


def load():
    global _load_failed
    tok = _library_token()
    if tok is not None and _load_cache["tok"] == tok:
        return copy.deepcopy(_load_cache["data"])
    try:
        data = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}                       # 첫 실행 (파일 없음) — 정상
    except Exception as e:
        # 파일은 있는데 못 읽었다(잘림·잠금 등) — '새 설치'가 아니다.
        # 기록은 실패 '진입'에 한 번만 — 매 호출이 재시도하므로 매번 적으면
        # 같은 줄이 로그를 가득 채운다.
        if not _load_failed:
            applog.exc(f"물감 창고 파일을 읽지 못함 ({LIBRARY_PATH.name})", e)
        data = _recover_from_backup()
        if data is None:
            # 백업으로도 못 살렸다 — 저장을 잠근다. 캐시에 담지 않으므로
            # 다음 호출이 다시 읽어 보고, 성공하는 순간 잠금이 풀린다.
            _load_failed = True
            return copy.deepcopy(_EMPTY)
    _load_failed = False
    out = copy.deepcopy(_EMPTY)
    # 하위 분류 목록 — 물감이 하나도 없는 하위 분류도 살아남아야 하므로
    # 항목과 별도로 최상위에 둔다. 모르는 모양이면 조용히 버린다.
    subs_in = data.get("subcats") if isinstance(data, dict) else None
    out["subcats"] = {}
    if isinstance(subs_in, dict):
        for cat in CATEGORIES:
            got = subs_in.get(cat)
            if isinstance(got, list):
                seen = []
                for s in got:
                    s = normalize_subcat(s)
                    if s and s not in seen:
                        seen.append(s)
                if seen:
                    out["subcats"][cat] = seen
    migrated = False
    for cat in CATEGORIES:
        if isinstance(data.get(cat), list):
            out[cat] = data[cat]
        # 하위호환: 예전 항목에 id/라벨/태그(/슬롯수) 기본값 채움
        for it in out[cat]:
            # 고유 id — 팔레트 블럭이 이걸로 참조한다. 이름을 바꿔도 연결이 유지됨.
            if not it.get("id"):
                it["id"] = uuid.uuid4().hex
                migrated = True
            it.setdefault("label", it.get("name", ""))
            # 분류(group) → 태그 이관. 옛 값은 버린다 — 전부 '기본' 이었고
            # (실측 2026-07-26) '기본' 은 "아직 정리 안 함"이라 태그로 옮길
            # 내용이 아니다. 직접 지은 분류가 있었다면 태그 하나로 살린다.
            if "group" in it:
                old = str(it.pop("group") or "").strip()
                if old and old != "기본" and not it.get("tags"):
                    it["tags"] = [old]
                migrated = True
            it["tags"] = normalize_tags(it.get("tags"))
            it.setdefault("subcat", "")             # "" = 미분류
            # 이미 \라벨\ 로 저장돼 있던 항목도 알맹이로 교정 (조회 실패 방지)
            it["label"] = normalize_label(it.get("label")) or it.get("name", "")
            if cat in _FILE_CATEGORIES:
                it.setdefault("slot_count", 0)
                # slot_names 는 2026-07-16 폐지됐다가 2026-07-27 자리 이름표로
                # 되살아났다 — 이름 있는 자리(\학년\)의 순서 목록이다
                it.setdefault("slot_names", [])
    if migrated:
        save(out)          # id를 새로 부여했으면 즉시 영속화 (캐시도 갱신됨)
    else:
        _load_cache["tok"] = tok
        _load_cache["data"] = copy.deepcopy(out)
    return out


def find_by_id(category, item_id):
    for it in load().get(category, []):
        if it.get("id") == item_id:
            return it
    return None


def block_badge(block):
    r"""이 블럭이 **여럿을 담았는가** — 세로 띠 표시 판정 (2026-08-01, 037).

    세 화면(메인 팔레트·설정 격자·창고 카드)이 **같은 답**을 써야 같은 물감이
    화면마다 다르게 보이지 않는다. 그래서 판정은 여기 한 곳뿐이다.

    반환: ("stack", "6") — 겹친 칸은 **개수**(택일, 몇 개 들었나가 궁금한 정보)
          ("mix", "MIX") — 꾸러미는 MIX (합체 — 겹침과 다른 물건이라 글자를 가른다)
          None — 낱개
    """
    if not isinstance(block, dict):
        return None
    if block.get("type") == "stack":
        n = len(block.get("items") or [])
        return ("stack", str(n)) if n else None
    if block.get("type") == "template":
        it = find_by_id("템플릿", block.get("ref"))
        if it and it.get("mix"):
            return ("mix", "MIX")
    return None


def get_item(category, item_id=None, name=None):
    """id 우선, 없으면 이름으로 조회 (구 데이터 하위호환)."""
    items = load().get(category, [])
    if item_id:
        for it in items:
            if it.get("id") == item_id:
                return it
    if name:
        for it in items:
            if it.get("name") == name:
                return it
    return None


def save(data):
    if _load_failed:
        # 창고를 못 읽은 상태의 저장은 무엇이 됐든 진짜 파일을 빈 목록으로
        # 덮어쓰는 길이다 — settings 처럼 False 를 돌려주지 않고 예외를
        # 올리는 이유: 여기 호출부 대부분이 반환값을 보지 않는다.
        applog.exc(f"물감 창고 저장 거부 ({LIBRARY_PATH.name}) — "
                   "창고 파일을 읽지 못한 상태라 덮어쓰지 않음")
        raise RuntimeError(
            "물감 창고 파일을 읽지 못한 상태라 저장을 멈췄습니다 — "
            "프로그램을 다시 시작해 주세요. (기존 물감을 지키기 위한 조치입니다)")
    _ensure_dirs()
    backup.rotate(LIBRARY_PATH)         # 저장 직전 상태를 .bak1 로 보관
    _atomic_write_text(
        LIBRARY_PATH, json.dumps(data, ensure_ascii=False, indent=2))
    _load_cache["tok"] = _library_token()
    _load_cache["data"] = copy.deepcopy(data)


def list_items(category):
    return load().get(category, [])


def _unique_name(items, name):
    existing = {it["name"] for it in items}
    if name not in existing:
        return name
    n = 2
    while f"{name} ({n})" in existing:
        n += 1
    return f"{name} ({n})"


def normalize_label(label):
    r"""라벨에서 감싼 역슬래시를 벗겨낸다.

    사용자는 문서에 쓰는 그대로 `\계획서표지\` 라고 입력하기 쉬운데, 저장은
    알맹이(`계획서표지`)여야 조회가 된다(안 그러면 영원히 매칭 실패).
    """
    return (label or "").strip().strip("\\").strip()


def resolve_edited_label(old_name, old_label, new_name, new_label):
    r"""수정 창에서 돌아온 라벨을 확정한다 (2026-07-25).

    문제: 수정 창의 라벨 칸은 '자세히' 안에 **접혀 있어 보이지 않는데**, 열 때
    옛 라벨이 미리 채워진다. 그래서 이름만 고치면 라벨이 옛 이름으로 남는다
    (실측: '학교합답1사진3선지' → '…5선지' 로 이름을 바꿨는데 라벨이 3선지로
    남아 \학교합답1사진5선지\ 변환이 "등록되지 않은 라벨"로 실패했다.
    팔레트 버튼은 id 로 찾으므로 잘 되어, 원인을 짐작하기 더 어려웠다).

    규칙: **라벨을 따로 지어 두지 않았고(라벨 == 이름) 이번에도 손대지 않았다면
    이름을 따라간다.** 일부러 다르게 지은 라벨(원안지 → 원안지양식)은 건드리지
    않는다 — 그건 사용자의 의도이기 때문이다.
    """
    untouched = normalize_label(new_label) == normalize_label(old_label)
    was_auto = normalize_label(old_label) == normalize_label(old_name)
    return new_name if (untouched and was_auto) else new_label


def _meta(name, label, tags=None):
    lab = normalize_label(label) or normalize_label(name) or name.strip()
    return {"id": uuid.uuid4().hex,
            "name": name,
            "label": lab,
            "tags": normalize_tags(tags)}


def add_style(name, fields=None, label=None, tags=None, subcat=None,
              actions=None):
    r"""서식 물감을 등록한다. 반환: 등록된 항목의 고유 id.

    actions: [{"func": 이름, "value": 값}, …] — **지금 쓰는 형식** (2026-07-31).
        팔레트의 '서식 조합' 블럭과 똑같은 모양이라, 창고에서 만든 서식을
        팔레트에 그대로 끌어다 놓을 수 있다 (사용자 결정: 팔레트가 기본).
    fields: {친화적필드명: 값} — 한글에서 캡처하던 옛 형식. 이미 등록된
        물감이 갖고 있으므로 읽기는 계속 지원한다.
    """
    data = load()
    item = _meta(_unique_name(data["서식"], name), label, tags)
    if actions is not None:
        item["actions"] = list(actions)
    if fields is not None:
        item["fields"] = fields
    item["subcat"] = _ensure_subcat(data, "서식", subcat)
    data["서식"].append(item)
    save(data)
    return item["id"]


def update_style_actions(item_id, name=None, actions=None):
    """서식 물감의 이름·조작 목록 바꾸기 (id 유지 → 팔레트 연결이 안 깨진다)."""
    data = load()
    for it in data.get("서식", []):
        if it.get("id") != item_id:
            continue
        if name and name.strip():
            others = [o for o in data["서식"] if o.get("id") != item_id]
            it["name"] = _unique_name(others, name.strip())
        if actions is not None:
            it["actions"] = list(actions)
            it.pop("fields", None)      # 옛 캡처 형식은 갈아탄 뒤 남길 이유가 없다
        save(data)
        return True
    return False


# 조작 이름 ↔ 글자모양 델타의 친화적 이름 (engine_library.CHARSHAPE_FIELD_LABELS).
# 문단 단위 조작(정렬·줄간격·여백 …)은 여기 없다 — 줄 일부에는 못 걸기 때문이다.
_ACTION_TO_FIELD = {"굵게": "굵게", "기울임": "기울임", "밑줄": "밑줄",
                    "글씨체": "글꼴", "글씨크기": "크기",
                    "자간": "자간", "글자색": "글자색"}


def style_fields(item):
    r"""서식 물감 → 글자모양 델타 {친화적이름: 값}.

    `\서식{글자}` 처럼 **줄 일부**에 입히는 자리가 쓴다. 문단 단위 조작은
    빠진다 — 한 줄 안의 몇 글자에 '가운데 정렬'을 걸 수는 없다.
    """
    item = item or {}
    if not item.get("actions"):
        return dict(item.get("fields") or {})       # 옛 캡처 형식 그대로
    out = {}
    for a in item["actions"]:
        label = _ACTION_TO_FIELD.get(a.get("func"))
        if label is None:
            continue
        out[label] = a.get("value", True) if "value" in a else True
    return out


def style_actions(item):
    r"""서식 물감의 조작 목록. 옛 캡처 형식(fields)도 여기서 번역해 돌려준다.

    한 곳에서만 번역해 두면, 이것을 읽는 쪽(팔레트 실행·카드 요약·수정 창)이
    '옛 것이냐 새 것이냐'를 저마다 따지지 않아도 된다.
    """
    item = item or {}
    if item.get("actions"):
        return list(item["actions"])
    out = []
    for label, val in (item.get("fields") or {}).items():
        if label in ("굵게", "기울임", "밑줄"):
            if val:
                out.append({"func": label})
        elif label == "글꼴":
            out.append({"func": "글씨체", "value": val})
        elif label == "크기":
            out.append({"func": "글씨크기", "value": val})
        elif label == "자간":
            out.append({"func": "자간", "value": val})
        elif label == "글자색":
            out.append({"func": "글자색", "value": val})
    return out


def add_char(name, text, label=None, tags=None, subcat=None):
    """반환: 등록된 항목의 고유 id."""
    data = load()
    item = _meta(_unique_name(data["문자"], name), label, tags)
    item["text"] = text
    item["subcat"] = _ensure_subcat(data, "문자", subcat)
    data["문자"].append(item)
    save(data)
    return item["id"]


# ── 조각 미리보기 (UI 제안 7) ───────────────────────────
# 진짜 그림 썸네일은 만들 수 없다 — .hwp 를 이미지로 굽는 길이 한글 자동화에
# 없고, 화면을 캡처하려면 한글을 띄워 파일을 열어야 한다(느리고 잘 깨진다).
# 대신 **저장하는 순간** 뽑아둔 본문 글자를 몇 줄 보여준다. 이름만으로는
# '결재란2' 가 무엇인지 알 수 없지만, 첫 줄 몇 개를 보면 바로 안다.
PREVIEW_LINES = 4
PREVIEW_WIDTH = 28


def make_preview(text):
    """조각 본문 글자 → 몇 줄짜리 미리보기. 저장할 때 한 번만 계산한다."""
    if not text:
        return ""
    full = [" ".join(raw.split()) for raw in str(text).splitlines()]
    full = [s for s in full if s]           # 표 안의 빈 줄·연속 공백 정리
    lines = [s if len(s) <= PREVIEW_WIDTH else s[:PREVIEW_WIDTH] + "…"
             for s in full[:PREVIEW_LINES]]
    if len(full) > PREVIEW_LINES:           # 딱 맞으면 붙이지 않는다
        lines.append("…")
    return "\n".join(lines)


def get_preview(item):
    """항목의 미리보기 글자. 예전에 등록한 것은 비어 있다."""
    return (item or {}).get("preview", "") or ""


def add_template_from_capture(name, save_to, label=None, tags=None,
                              slot_count=0, subcat=None):
    r"""템플릿을 등록한다. 조각을 **최종 위치에 바로 저장**하는 방식.

    save_to: 함수. 목적지 경로(pathlib.Path)를 받아 그 자리에 조각을 저장한다.
      예) lambda p: engine_library.capture_fragment(p)

    왜 '바로 저장'인가 (실측 2026-07-19):
      예전엔 _tmp_*.hwp 로 저장한 뒤 uuid 이름으로 **바꿨다**. 그런데 한글은
      캡처 과정에서 그 파일을 문서로 열어 붙들 때가 있고, **한 번 연 파일은
      문서를 닫아도 잠금을 놓지 않는다**(실측). 그래서 이름 바꾸기가
      [WinError 32] 로 터졌다.
      → 처음부터 최종 이름으로 저장하면 바꿀 일이 없어 이 오류가 원천적으로 사라진다.
      (최종 이름으로 바로 저장하면 한글이 그 파일을 열지도 않는 것을 확인)

    반환: 등록된 항목의 고유 id.
    """
    _ensure_dirs()
    data = load()
    item = _meta(_unique_name(data["템플릿"], name), label, tags)
    fname = f"{uuid.uuid4().hex}.hwp"
    dest = FRAGMENTS_DIR / fname
    preview = save_to(dest)         # capture_fragment 는 본문 글자를 돌려준다
    if not dest.exists():
        raise RuntimeError("조각 저장에 실패했습니다 (파일이 생성되지 않음)")
    item["file"] = fname
    # 자리 수·이름은 저장된 본문에서 다시 센다 — 저장 시 홑 \ 가 \\ 로
    # 정리되므로(normalize_marks_to_pairs) 넘겨받은 값보다 이쪽이 정확하다.
    # save_to 가 글자를 안 돌려주면(테스트 더미 등) 넘겨받은 값을 쓴다.
    text = preview if isinstance(preview, str) else ""
    item["slot_count"] = count_slots(text) if text else int(slot_count or 0)
    item["slot_names"] = form_fill.token_list(text)
    item["preview"] = make_preview(preview)
    item["subcat"] = _ensure_subcat(data, "템플릿", subcat)
    data["템플릿"].append(item)
    save(data)
    return item["id"]


def add_form_from_file(name, src_path, label=None, tags=None,
                       slot_count=0, slot_names=None, subcat=None):
    r"""양식(.hwp 파일 통째)을 등록한다.

    템플릿과의 차이:
      템플릿 = 문서 '일부'를 캡처해 커서 위치에 꽂는 것 (페이지 설정은 안 따라옴)
      양식   = 파일 '전체'를 새 문서로 여는 것 (용지·여백·머리말까지 그대로)
    표지·가정통신문처럼 "이 양식으로 새로 시작"하는 경우에 쓴다.
    원본 파일과 무관하게 fragments/ 로 복사하므로 원본을 지워도 남는다.
    """
    _ensure_dirs()
    data = load()
    item = _meta(_unique_name(data["양식"], name), label, tags)
    fname = f"{uuid.uuid4().hex}.hwp"
    shutil.copy2(str(src_path), str(FRAGMENTS_DIR / fname))
    item["file"] = fname
    item["slot_count"] = int(slot_count or 0)
    item["slot_names"] = list(slot_names or [])
    item["origin"] = str(src_path)      # 어디서 가져왔는지 (참고용)
    item["subcat"] = _ensure_subcat(data, "양식", subcat)
    data["양식"].append(item)
    save(data)
    return item["id"]


def add_mix(name, member_ids, label=None, tags=None, subcat=None):
    r"""꾸러미(섞은 물감) 등록 — 요소 템플릿들을 **차례대로 가리키는** 항목.

    왜 이런 물건이 필요한가 (사용자 기획 2026-07-31):
        시험문제를 뜯어보면 요소가 반복되는데 조합만 다르다(1234 / 1235 /
        1245 …). 지금까지는 조합이 하나 늘 때마다 hwp 조각을 통째로 새로
        만들어 왔다 — 창고의 '합답형1사진3선지' '학교합답2사진5선지' 같은
        이름들이 그 흔적이다. 요소와 조합을 갈라 놓으면 그 폭발이 없어진다.
        조합에 이름을 붙여 두고 `\가\` 처럼 부르면 된다.

    파일을 갖지 않는다 — `mix` 에 요소 id 만 담는다. 그래서 요소를 고치면
    이 꾸러미로 뽑는 것이 전부 따라 바뀐다(참조 방식, 사용자 결정).
    """
    _ensure_dirs()
    data = load()
    item = _meta(_unique_name(data["템플릿"], name), label, tags)
    item["mix"] = [str(i) for i in member_ids]
    item["slot_count"] = sum(
        int(m.get("slot_count") or 0) for m in mix_members(item, data))
    item["subcat"] = _ensure_subcat(data, "템플릿", subcat)
    data["템플릿"].append(item)
    save(data)
    return item["id"]


def update_mix(item_id, name=None, member_ids=None, subcat=None):
    """꾸러미의 이름·구성 바꾸기 (id 유지 → 팔레트 연결이 안 깨진다)."""
    data = load()
    for it in data.get("템플릿", []):
        if it.get("id") != item_id or not it.get("mix"):
            continue
        if name:
            it["name"] = name
        if member_ids is not None:
            it["mix"] = [str(i) for i in member_ids]
        if subcat is not None:          # None = 안 건드림, "" = 미분류
            it["subcat"] = _ensure_subcat(data, "템플릿", subcat)
        it["slot_count"] = sum(
            int(m.get("slot_count") or 0) for m in mix_members(it, data))
        save(data)
        return True
    return False


def update_item(category, item_id, name=None, label=None, tags=None,
                subcat=None):
    """등록된 항목의 이름·라벨·태그·하위 분류를 수정한다 (id는 유지 → 팔레트 연결 안 깨짐)."""
    data = load()
    items = data.get(category, [])
    target = next((it for it in items if it.get("id") == item_id), None)
    if target is None:
        return False
    if name and name.strip() and name.strip() != target["name"]:
        others = [it for it in items if it.get("id") != item_id]
        target["name"] = _unique_name(others, name.strip())
    if label is not None:
        target["label"] = normalize_label(label) or target["name"]
    if tags is not None:
        # 빈 목록도 뜻이 있다 (태그를 다 뗀 것) — None 일 때만 안 건드린다
        target["tags"] = normalize_tags(tags)
    if subcat is not None:              # "" = 미분류로 옮김, None = 안 건드림
        target["subcat"] = _ensure_subcat(data, category, subcat)
    save(data)
    return True


def count_slots(text):
    r"""글자 안의 채울 자리 개수 — 이름표(`\학년\`)는 하나로 센다.

    engine_library.count_slots_in_text 와 같은 규칙이다. 여기에도 둔 이유:
    library 는 한글(engine)에 기대지 않아야 하고, 저장 시점에 개수를 적어야 한다.
    """
    rest = (text or "").replace("\\본문\\", "")
    return sum(1 for m in form_fill.TOKEN_RE.finditer(rest)
               if m.group(1) not in form_fill.RESERVED_NAMES)


def replace_template_fragment(item_id, save_to, slot_count=None,
                              category="템플릿"):
    r"""조각 파일만 새로 저장한 것으로 교체 (id·이름·라벨 유지).

    save_to: 목적지 경로를 받아 조각을 저장하는 함수
             (add_template_from_capture 와 같은 방식 — WinError 32 회피).
    '꺼내서 고치기'(2026-07-25)가 쓴다. 미리보기도 새 본문으로 갱신하고,
    slot_count 를 안 주면 새 본문의 빈칸 수를 세어 넣는다 — 고치면서
    빈칸을 늘리거나 줄여도 안내가 어긋나지 않게.
    양식도 같은 길을 쓴다 (2026-07-27) — 양식은 이름만 고칠 수 있고 내용은
    못 고치던 것이 사용자 지적이었다.
    """
    data = load()
    target = next((it for it in data.get(category, [])
                   if it.get("id") == item_id), None)
    if target is None:
        return False
    old = FRAGMENTS_DIR / target["file"]
    fname = f"{uuid.uuid4().hex}.hwp"
    dest = FRAGMENTS_DIR / fname
    text = save_to(dest) or ""      # capture_fragment 는 본문 글자를 돌려준다
    if not dest.exists():
        raise RuntimeError("조각 저장에 실패했습니다 (파일이 생성되지 않음)")
    target["file"] = fname
    target["preview"] = make_preview(text)
    if slot_count is None:
        slot_count = count_slots(text)
    target["slot_count"] = int(slot_count)
    target["slot_names"] = form_fill.token_list(text)
    save(data)
    try:
        old.unlink(missing_ok=True)
    except OSError as e:
        applog.exc(f"이전 조각 파일 삭제 실패 (남아 있어도 무해) — {old.name}", e)
    return True


def find_label_owner(label, exclude_id=None):
    r"""이 라벨을 이미 쓰고 있는 항목을 찾는다. (분류명, 항목) 또는 None.

    이름(name)은 `_unique_name`이 분류 안에서 유일성을 보장하지만, **라벨은
    분류를 가로질러 겹칠 수 있는데 아무도 검사하지 않았다**. `label_lookup()`은
    먼저 만난 것만 담으므로, 나중에 등록한 항목은 `\라벨\`로 영영 호출되지 않는다
    — 그런데 사용자에게는 아무 표시도 없었다 (개선안 3과 같은 뿌리).

    exclude_id: 수정 중인 자기 자신은 충돌로 보지 않기 위해 제외할 id.
    """
    lab = normalize_label(label)
    if not lab:
        return None
    data = load()
    for cat in CATEGORIES:
        for it in data[cat]:
            if exclude_id and it.get("id") == exclude_id:
                continue
            if normalize_label(it.get("label")) == lab:
                return cat, it
    return None


# 사진 폴더에서 라벨로 인정하는 확장자 (탐색 순서이기도 하다)
PHOTO_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp")


def _photo_lookup():
    r"""연결된 사진 폴더들의 파일 → {파일이름(확장자 뺀): ("사진", {"path": …})}.

    \실험사진1\ 처럼 등록 없이 파일 이름만으로 부르기 위한 것. 하위 폴더는
    뒤지지 않는다 — 이름 충돌과 속도 문제를 피하려는 의도적 제한.

    폴더는 여러 개일 수 있고, **먼저 등록한 폴더가 이긴다**. 나중 폴더가
    같은 이름을 덮으면 폴더 하나를 추가한 것만으로 기존 \이름\ 이 다른 그림으로
    바뀌어 버린다 — 사용자가 눈치채기 가장 어려운 종류의 사고다.
    폴더 하나가 사라져도(외장 디스크·이동) 나머지는 계속 동작해야 하므로
    빠뜨린 폴더는 로그만 남기고 넘어간다.
    """
    from hwp_palette.core import settings                     # 순환 참조는 아니나 소유권 규칙상 여기서만 조회
    out = {}
    for photo_dir in settings.get_photo_dirs():
        root = pathlib.Path(photo_dir)
        if not root.is_dir():
            applog.warn(
                f"사진 폴더가 없습니다: {photo_dir} — 이 폴더의 \\사진이름\\ 변환이 안 됩니다")
            continue
        try:
            for f in sorted(root.iterdir()):
                if f.is_file() and f.suffix.lower() in PHOTO_EXTS:
                    stem = f.stem.strip()
                    if stem and stem not in out:   # 같은 이름이면 먼저 온 것(폴더 순서 포함)
                        out[stem] = ("사진", {"name": stem, "label": stem,
                                              "path": str(f)})
        except OSError as e:
            applog.exc(f"사진 폴더를 읽지 못함 ({photo_dir})", e)
    return out


# 폴더 세기 캐시 — {경로: (폴더 mtime_ns, count)}. 사진 블럭을 누를 때마다
# 폴더의 파일 전체를 stat 하면(파일 하나당 한 번) OneDrive·수백 장짜리
# 폴더에서 목록이 뜨기까지 화면이 멈춘 것처럼 보였다 (사용자 지적 2026-07-31).
# 폴더의 mtime 은 파일 추가·삭제 때 바뀌므로, 그것이 같으면 지난 결과를 쓴다.
_photo_count_cache = {}


def photo_folders_summary():
    """UI 표시용 폴더 현황 — [{"path", "exists", "count"}] (등록 순서).

    count 는 라벨이 될 수 있는 이미지 파일 수(폴더가 없으면 0). 화면을 그리다
    예외로 죽는 일이 없도록 어떤 실패든 삼키고 0 으로 둔다.
    """
    from hwp_palette.core import settings
    try:
        dirs = settings.get_photo_dirs()
    except Exception:
        dirs = []
    rows = []
    for d in dirs:
        info = {"path": d, "exists": False, "count": 0}
        try:
            root = pathlib.Path(d)
            if root.is_dir():
                info["exists"] = True
                stamp = root.stat().st_mtime_ns
                hit = _photo_count_cache.get(d)
                if hit and hit[0] == stamp:
                    info["count"] = hit[1]
                else:
                    info["count"] = sum(
                        1 for f in root.iterdir()
                        if f.is_file() and f.suffix.lower() in PHOTO_EXTS)
                    _photo_count_cache[d] = (stamp, info["count"])
        except OSError:
            pass
        rows.append(info)
    return rows


class MixInUse(Exception):
    """꾸러미가 쓰고 있는 요소를 지우려 했다 — 호출부가 이름을 보여준다."""

    def __init__(self, name, users):
        self.name = name
        self.users = list(users)
        super().__init__(
            f"‘{name}’ 은(는) 꾸러미 {', '.join(self.users)} 이(가) 쓰고 있습니다")


def mix_members(item, data=None):
    r"""꾸러미(섞은 물감)가 가리키는 **요소 항목들**을 차례대로 돌려준다.

    꾸러미는 요소를 **참조**한다 (사용자 결정 2026-07-31) — 내용을 복사해
    두지 않는다. '선지 5택' 하나를 고치면 그것을 쓰는 꾸러미가 전부 따라
    바뀌어야 시험지 관리가 된다. 대신 지워진 요소는 조용히 빠진다.
    """
    ids = item.get("mix") or []
    if not ids:
        return []
    if data is None:
        data = load()
    by_id = {it.get("id"): it for it in data.get("템플릿", [])}
    return [by_id[i] for i in ids if i in by_id]


def mix_users(item_id, data=None):
    """이 요소를 쓰고 있는 꾸러미 이름들 — 지우기 전에 막아설 때 쓴다."""
    if data is None:
        data = load()
    return [it.get("name", "?") for it in data.get("템플릿", [])
            if item_id in (it.get("mix") or [])]


def _resolve_mixes(data):
    r"""꾸러미마다 요소 항목과 빈칸 합계를 채워 둔다 (변환 계획이 쓴다).

    파서는 라이브러리를 모른다 — 여기서 미리 풀어 항목 안에 담아 주면
    파서는 `_mix_items` 만 보고 펼치면 된다.
    """
    for it in data.get("템플릿", []):
        if not it.get("mix"):
            continue
        members = mix_members(it, data)
        it["_mix_items"] = members
        # 꾸러미의 빈칸 수 = 요소들의 빈칸 합계. 사용자가 아랫줄을 몇 줄
        # 적어야 하는지가 여기서 정해진다.
        it["slot_count"] = sum(int(m.get("slot_count") or 0) for m in members)


def label_lookup():
    """{라벨: (분류명, 항목)} — 마크다운 변환용.

    우선순위: 사용자 등록 항목 > 내장 문자 > 사진 폴더 파일.
    (같은 라벨이면 위가 이긴다 — 사진 파일 이름이 우연히 등록 라벨과 겹쳐도
    등록한 것이 동작해야 예측 가능하다)
    같은 라벨이 둘 이상이면 먼저 만난 것이 이기고, 나머지는 로그에 남긴다
    (등록 시점에 이미 경고하지만, 구 데이터에는 그 경고를 못 받은 항목이 있다).
    """
    data = load()
    _resolve_mixes(data)
    out = {}
    for cat in CATEGORIES:
        for it in data[cat]:
            lab = (it.get("label") or "").strip()
            if not lab:
                continue
            if lab in out:
                applog.warn(
                    f"라벨 중복 — \\{lab}\\ 은(는) [{out[lab][0]}] "
                    f"{out[lab][1].get('name')!r} 로 동작하고, "
                    f"[{cat}] {it.get('name')!r} 은(는) 호출되지 않습니다")
                continue
            out[lab] = (cat, it)
    # 내장 문자 병합 (사용자가 이미 쓴 라벨은 건드리지 않음)
    try:
        from hwp_palette.model import builtin_chars   # 순환 참조 회피 — builtin_chars 는 독립 모듈이나
                               # 여기서만 쓰이므로 지역 유지
        for lab, text, _ in builtin_chars.BUILTINS:
            if lab not in out:
                out[lab] = ("문자", {"name": lab, "text": text, "label": lab})
    except Exception:
        pass
    # 사진 폴더 병합 (가장 낮은 우선순위)
    for lab, entry in _photo_lookup().items():
        if lab not in out:
            out[lab] = entry
    return out


def rename_tag(old, new):
    """태그 이름 바꾸기 — 모든 분류에서. 바뀐 항목 수를 반환.

    태그는 항목마다 붙은 문자열일 뿐이라(별도 목록 없음), 이름을 바꾸려면
    그 태그를 단 항목을 전부 고쳐야 한다. 오타를 고칠 때 쓴다
    (#수능문제 → #수능). 새 이름이 이미 달려 있으면 중복되지 않게 합친다.
    """
    old = normalize_tags(old)
    new = normalize_tags(new)
    if len(old) != 1 or len(new) != 1 or old == new:
        return 0
    old, new = old[0], new[0]
    data = load()
    n = 0
    for cat in CATEGORIES:
        for it in data[cat]:
            tags = it.get("tags") or []
            if old in tags:
                it["tags"] = normalize_tags(
                    [new if t == old else t for t in tags])
                n += 1
    if n:
        save(data)
    return n


def delete_tag(name):
    """태그 떼기 — 그 태그를 단 항목에서 태그만 뗀다 (항목은 그대로).

    태그를 지운다고 안의 자산까지 지우면 실수 한 번이 재앙이 된다.
    """
    name = normalize_tags(name)
    if len(name) != 1:
        return 0
    name = name[0]
    data = load()
    n = 0
    for cat in CATEGORIES:
        for it in data[cat]:
            tags = it.get("tags") or []
            if name in tags:
                it["tags"] = [t for t in tags if t != name]
                n += 1
    if n:
        save(data)
    return n


def template_path(item):
    return FRAGMENTS_DIR / item["file"]


def delete_item(category, item_id):
    """id로 항목 삭제. 팔레트에 남은 참조(고아 블럭)도 함께 정리한다.

    순서가 중요하다 (2026-07-31): **목록 저장이 먼저, 조각 파일 삭제가 나중.**
    파일부터 지웠는데 저장이 실패하면, 목록에는 남았는데 실체가 없는
    유령 항목이 된다(누르면 그때서야 터진다). 반대로 저장은 됐는데 파일
    삭제가 실패하면 조각 하나가 남을 뿐이라 무해하다 — 기록만 남긴다.
    """
    data = load()
    items = data.get(category, [])
    target = next((it for it in items if it.get("id") == item_id), None)
    if target is None:
        return False
    # 꾸러미가 쓰고 있는 요소는 지우지 않는다 (2026-07-31, 참조 방식의 대가).
    # 지워 버리면 그 꾸러미는 빈칸 수가 조용히 줄어 시험지가 어긋난다 —
    # 조용한 어긋남보다 못 지우는 편이 낫다. 호출부가 이유를 보여줄 수 있게
    # 예외로 알린다.
    if category == "템플릿":
        users = mix_users(item_id, data)
        if users:
            raise MixInUse(target.get("name", "?"), users)
    # 구 데이터에는 file 키가 없는 항목도 있다 — KeyError 로 죽지 않는다
    fname = target.get("file") if category in _FILE_CATEGORIES else None
    data[category] = [it for it in items if it.get("id") != item_id]
    save(data)
    if fname:
        try:
            (FRAGMENTS_DIR / fname).unlink(missing_ok=True)
        except OSError as e:
            applog.exc(f"조각 파일 삭제 실패 (남아 있어도 무해) — {fname}", e)
    _purge_palette_refs(category, item_id)
    return True


def _purge_palette_refs(category, item_id):
    """삭제된 라이브러리 항목을 가리키던 팔레트 블럭을 제거 (고아 블럭 방지).

    palette 를 최상위에서 import 하면 순환 참조가 되므로 여기서 지역 import.
    """
    try:
        from hwp_palette.model import palette         # 순환 참조 회피 (palette → library → palette)
    except ImportError:
        return
    btype = _BLOCK_TYPE.get(category)
    if btype is None:
        return
    try:
        tabs = palette.load_tabs()
    except Exception:
        return
    changed = False
    for tab in tabs:
        keep = [b for b in tab.get("blocks", [])
                if not (b.get("type") == btype and b.get("ref") == item_id)]
        if len(keep) != len(tab.get("blocks", [])):
            tab["blocks"] = keep
            changed = True
    if changed:
        palette.save_tabs(tabs)


# ── 내보내기 / 가져오기 (개선안 30) ────────────────────
# 양식 프리셋에는 settings.export_profile 이 있는데 라이브러리엔 없어서, 동료와
# 항목 단위로 나눌 방법이 "폴더째 복사"뿐이었다. 템플릿·양식은 조각 .hwp 파일이
# 따로 있으므로 JSON 하나로는 부족하다 → 목록(JSON) + 조각 파일을 zip 하나로 묶는다.
ARCHIVE_VERSION = 1
_MANIFEST_NAME = "library.json"
_ARCHIVE_FRAGMENT_DIR = "fragments"


def export_items(pairs, dest_path):
    """[(분류, 항목), ...] 을 zip 하나로 내보낸다. 반환: 내보낸 항목 수.

    id 는 일부러 함께 넣지 않는다 — 받는 쪽에서 새로 발급해야 기존 항목과
    충돌하지 않는다(같은 id 가 두 개 있으면 팔레트 참조가 엉킨다).
    대신 **`origin_id` 로 원본 id 를 적어 둔다** (2026-07-27). 두 가지에 쓴다:
      · 팔레트 탭을 함께 보낼 때, 블럭의 `ref`(옛 id)를 받는 쪽의 새 id 로
        갈아끼우기 위한 대응표의 열쇠 (chip.py)
      · 같은 칩을 두 번 받았는지 판정 — 이미 있으면 물감을 또 만들지 않는다

    **태그도 빼고 보낸다** (사용자 결정 2026-07-26). 태그는 '내가 찾기 위한
    표시'라 남에게는 뜻이 없다 — 받는 쪽 태그 목록에 남의 습관(#급할때)이
    섞이면 자동완성이 못 쓰게 된다. 받는 쪽은 태그 없이 시작해 자기 식으로 단다.
    """
    import zipfile
    items = []
    # origin  = 양식을 등록할 때의 원본 파일 경로(내 PC 사정) — 안 보낸다
    # tags    = 내 정리 습관 — 안 보낸다
    # subcat  = 하위 분류도 같은 이유 (받는 쪽 서랍에 남의 이름이 생기면 안 된다)
    # from_chip = 내가 받은 칩 이름 — 다시 보낼 때 남의 출처를 물려주지 않는다
    _DROP = ("id", "origin", "tags", "subcat", "from_chip", "origin_id")
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for cat, it in pairs:
            rec = {k: v for k, v in it.items() if k not in _DROP}
            rec["origin_id"] = it.get("id")     # 받는 쪽이 ref 를 이어붙일 열쇠
            rec["category"] = cat
            if cat in _FILE_CATEGORIES:
                src = FRAGMENTS_DIR / it["file"]
                if not src.exists():
                    applog.warn(f"내보내기: 조각 파일이 없어 건너뜀 — "
                                f"[{cat}] {it.get('name')!r} ({it['file']})")
                    continue
                zf.write(src, f"{_ARCHIVE_FRAGMENT_DIR}/{it['file']}")
            items.append(rec)
        zf.writestr(_MANIFEST_NAME, json.dumps(
            {"version": ARCHIVE_VERSION, "items": items},
            ensure_ascii=False, indent=2))
    return len(items)


def _unique_label(label, taken):
    """라벨 충돌 시 뒤에 번호를 붙인다 (조용히 가려지는 것보다 낫다)."""
    lab = normalize_label(label)
    if not lab or lab not in taken:
        return lab
    n = 2
    while f"{lab}{n}" in taken:
        n += 1
    return f"{lab}{n}"


def import_archive(src_path, from_chip=None):
    r"""내보낸 zip 을 읽어 라이브러리에 추가한다.

    반환: {"added": 개수, "renamed": [(원래이름, 바뀐이름), ...],
           "relabeled": [(원래라벨, 바뀐라벨), ...],
           "id_map": {보낸쪽 id: 내 id}, "reused": 이미 있어 건너뛴 개수}

    항상 **추가**만 한다(덮어쓰기 없음). 이름·라벨이 겹치면 번호를 붙여 피한다 —
    남의 파일을 받아서 내 것이 사라지는 일은 없어야 한다. 라벨을 안 바꾸면
    `\라벨\` 호출이 조용히 가려지므로(find_label_owner 참고) 라벨도 유일하게 만든다.

    **id_map** 은 팔레트 탭을 함께 받을 때 블럭의 `ref` 를 이어붙이는 데 쓴다
    (chip.py). 같은 칩을 두 번 받으면 물감을 또 만들지 않고 **이미 있는 것에
    잇는다** — 그래야 창고가 두 배가 되지 않는다(origin_id 로 판정).

    from_chip: 어느 칩에서 왔는지. 받은 물감에 꼬리표로 남는다(태그가 아니라
    별도 필드 — 사용자가 지울 수 있는 태그와 성격이 다르다).
    """
    import zipfile
    _ensure_dirs()
    data = load()
    taken_labels = {normalize_label(it.get("label"))
                    for cat in CATEGORIES for it in data[cat]}
    # 이미 받아 둔 물감 (origin_id → 내 id). 같은 칩 재등록 판정용.
    known = {it["origin_id"]: it["id"]
             for cat in CATEGORIES for it in data[cat] if it.get("origin_id")}
    added, renamed, relabeled = 0, [], []
    id_map, reused = {}, 0

    with zipfile.ZipFile(src_path) as zf:
        manifest = json.loads(zf.read(_MANIFEST_NAME).decode("utf-8"))
        if manifest.get("version") != ARCHIVE_VERSION:
            raise ValueError(
                f"지원하지 않는 파일 형식입니다 (version={manifest.get('version')})")
        for rec in manifest.get("items", []):
            cat = rec.get("category")
            if cat not in CATEGORIES:
                applog.warn(f"가져오기: 알 수 없는 분류라 건너뜀 — {cat!r}")
                continue
            item = dict(rec)
            origin_id = item.get("origin_id")
            # 같은 칩을 두 번 받았으면 물감을 또 만들지 않고 **이미 있는 것에
            # 잇는다** — 탭의 ref 도 그리로 연결된다(id_map).
            if origin_id and origin_id in known:
                id_map[origin_id] = known[origin_id]
                reused += 1
                continue
            item["id"] = uuid.uuid4().hex
            if origin_id:
                id_map[origin_id] = item["id"]
                known[origin_id] = item["id"]
            if from_chip:
                item["from_chip"] = str(from_chip)
            # 받은 물감은 태그·하위 분류 없이 시작한다 (export_items 머리말 참조).
            # 옛 꾸러미에 group/tags/subcat 이 들어 있어도 여기서 떨군다.
            item.pop("group", None)
            item["tags"] = []
            item["subcat"] = ""

            # 조각 파일 확인을 **이름·라벨을 정하기 전에** 한다. 건너뛸 항목이
            # 이름/라벨을 선점하면, 뒤따르는 멀쩡한 항목이 있지도 않은 충돌
            # 때문에 이름이 바뀌고 "겹쳐서 바꿨다"고 잘못 보고된다.
            arc = None
            if cat in _FILE_CATEGORIES:
                src_name = item.get("file")
                arc = f"{_ARCHIVE_FRAGMENT_DIR}/{src_name}"
                if src_name is None or arc not in zf.namelist():
                    applog.warn("가져오기: 조각 파일이 없어 건너뜀 — "
                                f"{item.get('name', '?')!r}")
                    continue

            orig_name = item.get("name", "이름없음")
            item["name"] = _unique_name(data[cat], orig_name)
            if item["name"] != orig_name:
                renamed.append((orig_name, item["name"]))

            orig_label = normalize_label(item.get("label")) or item["name"]
            item["label"] = _unique_label(orig_label, taken_labels)
            if item["label"] != orig_label:
                relabeled.append((orig_label, item["label"]))
            taken_labels.add(item["label"])

            if arc is not None:
                # 파일명은 새로 발급 — 보낸 쪽과 우연히 같은 이름이어도 안 덮어씀
                fname = f"{uuid.uuid4().hex}.hwp"
                (FRAGMENTS_DIR / fname).write_bytes(zf.read(arc))
                item["file"] = fname

            data[cat].append(item)
            added += 1

    save(data)
    return {"added": added, "renamed": renamed, "relabeled": relabeled,
            "id_map": id_map, "reused": reused}


def count_palette_refs(category, item_id):
    """이 항목을 쓰는 팔레트 블럭 수 (삭제 전 경고용)."""
    try:
        from hwp_palette.model import palette         # 순환 참조 회피 (palette → library → palette)
        tabs = palette.load_tabs()
    except Exception:
        return 0
    btype = _BLOCK_TYPE.get(category)
    if btype is None:
        return 0
    return sum(1 for tab in tabs for b in tab.get("blocks", [])
               if b.get("type") == btype and b.get("ref") == item_id)
