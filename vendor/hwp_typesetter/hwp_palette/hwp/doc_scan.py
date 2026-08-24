# -*- coding: utf-8 -*-
r"""문서 해체 — 열린 문서를 **조각 목록**으로 훑는다 (2026-07-31).

무엇을 하는 물건인가 (사용자 기획):
    이미 완성된 시험지 안에는 부품이 전부 들어 있다 — 머리표, 배점표, 문항
    틀, 답란. 새로 만들 게 아니라 꺼내면 된다. 그래서 문서를 훑어

        표 하나   = 조각 하나
        문단 하나 = 조각 하나

    로 나열해 주고, 사용자는 "쭉쭉 내리면서 필요한 것만 골라 하나씩 묶어"
    담는다.

여기서는 **읽기만** 한다. 담기·빈칸 넣기는 UI 쪽(decompose_ui)이 한다.

두 가지 규칙이 목록을 깔끔하게 만든다:
  ① **표 안 문단은 목록에서 뺀다.** 이미 그 표 조각에 들어 있다. 안 빼면
     배점표 하나가 셀 수만큼 행으로 불어나 목록이 못 쓰게 된다.
  ② **빈 문단은 뺀다.** 줄 간격을 벌리려고 넣어 둔 빈 줄이 조각일 리 없다.

위치를 어떻게 들고 있나:
    조각마다 (list, para, pos) 를 적어 둔다. 그런데 사용자가 목록을 보다가
    한글에서 글자를 지우면 그 위치가 **밀린다**. 그래서 담기 직전에 다시
    찾는다(`relocate`) — 고르는 동안 편집을 막지 않기 위해 치르는 값이다
    (사용자 결정 2026-07-31: 편집 자유가 이 기능의 핵심).
"""

from hwp_palette.core import applog
from hwp_palette.hwp import hwp_engine

# 미리보기에 쓸 글자 수 — 목록 한 줄에 들어가는 만큼만
PREVIEW_CHARS = 30
# 한 번에 훑는 최대 문단 수. 아주 긴 문서에서 창이 멎어 보이지 않게 막는다.
MAX_PARAS = 4000


def _hwp():
    return hwp_engine.hwp


def scan():
    r"""열린 문서를 훑어 조각 목록을 돌려준다.

    반환: [{kind, title, preview, pos, para, ctrl_index, rows, cols}]
      kind — "표" 또는 "문단"
      pos  — 그 조각으로 되돌아갈 때 쓰는 (list, para, char)
    커서는 훑기 전 자리로 되돌려 놓는다 — 남의 커서를 옮겨 놓고 가지 않는다.
    """
    hwp = _hwp()
    try:
        keep = hwp.GetPos()
    except Exception:
        keep = None
    out = []
    try:
        tables = _scan_tables()
        out.extend(tables)
        out.extend(_scan_paragraphs({t["para"] for t in tables}))
    except Exception as e:
        applog.exc("문서 해체: 훑기 실패", e)
    finally:
        if keep:
            try:
                hwp.SetPos(*keep)
            except Exception:
                pass
    # 문서에 나온 차례대로 — 사용자가 위에서 아래로 훑을 수 있게
    out.sort(key=lambda d: (d.get("para", 0), d.get("pos", (0, 0, 0))[2]))
    return out


def _scan_tables():
    """표를 컨트롤 순회로 모은다 — 이미 쓰고 있는 방식이라 확실하다."""
    hwp = _hwp()
    found = []
    try:
        ctrl = hwp.HeadCtrl
    except Exception as e:
        applog.exc("문서 해체: 컨트롤 목록 읽기 실패", e)
        return found
    idx = 0
    while ctrl is not None:
        try:
            if str(getattr(ctrl, "CtrlID", "")) == "tbl":
                pos = _ctrl_pos(ctrl)
                rows, cols = _table_size(ctrl)
                size = f"{rows}×{cols}" if rows and cols else ""
                found.append({
                    "kind": "표",
                    "title": f"표 {size}".strip(),
                    "preview": "",
                    "pos": pos,
                    "para": pos[1] if pos else 0,
                    "ctrl_index": idx,
                    "rows": rows, "cols": cols,
                })
            idx += 1
        except Exception as e:
            applog.exc(f"표 스캔 실패 (#{idx+1})", e)
        finally:
            ctrl = ctrl.Next
    return found


def _ctrl_pos(ctrl):
    try:
        anchor = ctrl.GetAnchorPos(0)
        return (anchor.Item("List"), anchor.Item("Para"), anchor.Item("Pos"))
    except Exception:
        return (0, 0, 0)


def _table_size(ctrl):
    """표의 줄·칸 수. 못 읽으면 (0, 0) — 이름에서 크기만 빠진다."""
    try:
        props = ctrl.Properties
        return int(props.Item("RowCount")), int(props.Item("ColCount"))
    except Exception:
        return 0, 0


def _scan_paragraphs(table_paras):
    r"""본문 문단을 훑는다 — 표 안 문단과 빈 문단은 뺀다.

    표 안인지는 GetPos()[0](list) 로 가린다: 본문은 0 이고 표 셀 안은 다른
    번호다. 이 규칙 덕에 표 안 글자가 목록에 겹쳐 나오지 않는다.
    """
    hwp = _hwp()
    out = []
    try:
        hwp.MoveDocBegin()
    except Exception as e:
        applog.exc("문서 해체: 문서 처음으로 이동 실패", e)
        return out
    seen = set()
    for _ in range(MAX_PARAS):
        try:
            list_id, para, _pos = hwp.GetPos()
        except Exception:
            break
        if list_id == 0 and para not in seen and para not in table_paras:
            seen.add(para)
            text = _para_text()
            if text.strip():
                out.append({
                    "kind": "문단",
                    "title": "문단",
                    "preview": (text[:PREVIEW_CHARS] + "…"
                                if len(text) > PREVIEW_CHARS else text),
                    "pos": (list_id, para, 0),
                    "para": para,
                    "ctrl_index": None,
                    "rows": 0, "cols": 0,
                })
        try:
            if not hwp.MoveNextParaBegin():
                break
        except Exception:
            break
    return out


def _para_text():
    r"""지금 문단의 글자 — **클립보드를 거치지 않는다**.

    클립보드로 읽으면 사용자가 복사해 둔 것을 밟고, Tk 의 클립보드 잠금과도
    엉킨다(엔진 함정 메모). 문단 끝까지 선택해 API 로 읽고, 선택은 바로 푼다.
    """
    hwp = _hwp()
    try:
        keep = hwp.GetPos()
        hwp.MoveSelParaEnd()
        text = hwp_engine.read_selection_direct() or ""
        hwp.Cancel()
        hwp.SetPos(*keep)
        return text.replace("\r", " ").replace("\n", " ").strip()
    except Exception:
        try:
            hwp.Cancel()
        except Exception:
            pass
        return ""


def select_piece(piece):
    r"""그 조각을 한글에서 **선택**해 보인다 — 도킹된 화면이 곧 미리보기다.

    조각마다 그림을 그려 두지 않는 이유: 도킹으로 한글 실물이 이미 옆에 있다.
    실물을 짚어 주는 편이 정확하고, 만들 것도 없다.
    """
    hwp = _hwp()
    try:
        if piece.get("kind") == "표":
            ctrl = _ctrl_at(piece.get("ctrl_index"))
            if ctrl is not None:
                hwp.select_ctrl(ctrl)
                return True
        pos = piece.get("pos")
        if not pos:
            return False
        hwp.SetPos(*pos)
        hwp.MoveSelParaEnd()
        return True
    except Exception as e:
        applog.exc("문서 해체: 조각 선택 실패", e)
        return False


def _ctrl_at(index):
    if index is None:
        return None
    hwp = _hwp()
    try:
        ctrl = hwp.HeadCtrl
        for _ in range(int(index)):
            if ctrl is None:
                return None
            ctrl = ctrl.Next
        return ctrl
    except Exception:
        return None


def relocate(piece):
    r"""담기 직전에 조각을 **다시 찾는다** (2026-07-31).

    고르는 동안 한글에서 글자를 지우면 훑을 때 적어 둔 위치가 밀린다. 표는
    컨트롤 차례가 그대로면 그 컨트롤이 여전히 그 표이므로 다시 잡으면 되고,
    문단은 미리보기 글을 문서에서 찾아 위치를 고쳐 잡는다. 못 찾으면 원래
    위치로 시도한다 — 여기서 실패해도 담기 직전에 사용자가 눈으로 본다.
    """
    if piece.get("kind") == "표":
        return select_piece(piece)
    hwp = _hwp()
    text = (piece.get("preview") or "").rstrip("…").strip()
    if len(text) >= 4:
        try:
            hwp.MoveDocBegin()
            if hwp_engine.find_text(text[:20]):
                hwp.Cancel()
                keep = hwp.GetPos()
                hwp.SetPos(*keep)
                hwp.MoveSelParaEnd()
                return True
        except Exception as e:
            applog.exc("문서 해체: 조각 재탐색 실패 — 원래 자리로 시도한다", e)
    return select_piece(piece)
