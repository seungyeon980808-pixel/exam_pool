# -*- coding: utf-8 -*-
r"""라이브러리·팔레트 실행 엔진 — 캡처/적용, 블럭 실행, \라벨\ 변환.

hwp_engine(코어)이 '한글을 어떻게 조작하는가'를 맡는다면, 이 모듈은
'등록해 둔 것을 어떻게 꺼내 쓰는가'를 맡는다 (개선안 19 — 2026-07-18 분할).

  · 서식(부분 델타) 캡처/적용
  · 템플릿(조각 .hwp) 캡처/삽입, 양식(파일 통째) 열기
  · 팔레트 블럭 실행 (문자/기능/템플릿/양식)
  · 마크다운 \라벨\ 변환 계획 실행

한글 조작은 전부 hwp_engine 의 것을 그대로 쓴다(연결 인스턴스를 공유하기 위함).
"""

import json
import time

import pathlib
import shutil
import uuid
from typing import Final

from hwp_palette.core import applog
from hwp_palette.model import form_fill                    # 채울 자리 토큰 규칙(이름표 \학년\) 한 벌로
from hwp_palette.core import paths
from hwp_palette.hwp import hwp_engine
from hwp_palette.model import parser as md_parser          # MultiLine(한 칸에 여러 줄) 판별용
from hwp_palette.hwp import preview                      # 미리보기 그림 캐시
from hwp_palette.hwp.hwp_engine import (
    delete_selection, find_text, has_selection, insert_plain,
)


INLINE_EQUATION_BASE_UNIT: Final = 1150
FRACTION_EQUATION_BASE_UNIT: Final = 1200


def _h():
    """현재 연결된 한글 인스턴스. 재연결로 바뀌므로 매번 모듈에서 읽는다."""
    return hwp_engine.hwp


# ── 라이브러리: 서식(부분 델타) 캡처/적용 ───────────────
# 친화적 이름 : CharShape 딕셔너리 키. 값 단위는 저장소(library.py)에도 그대로
# 노출되므로, 여기 바뀌면 저장된 항목의 의미도 바뀐다는 점 주의.
CHARSHAPE_FIELD_LABELS = ["굵게", "기울임", "밑줄", "글자색", "자간", "글꼴", "크기"]

# 양식 안에 적어 두는 '본문 시작 자리' 표시 (2026-07-24).
#
# 양식은 문서 전체를 여는 것이라, 뒤따라오는 내용(문제 등)을 어디에 넣을지
# 정해 줘야 한다. 문서 끝에 붙이면 머리말·2단 구성·페이지번호가 있는 양식에서
# 엉뚱한 자리로 간다. 그래서 양식 파일 안에 이 표시를 한 번 적어 두면, 변환이
# 그 자리를 찾아 거기서부터 이어 쓴다. 표시가 없으면 문서 끝에서 이어 쓴다.
#
# 주의: 이 표시는 역슬래시를 2개 포함한다. fill_slots 는 빈칸(\)을 찾아 채우므로
# **빈칸 채우기 전에 반드시 다른 마커로 치환**해야 한다 — 안 그러면 본문 표시가
# 빈칸으로 잡아먹힌다. execute_library_plan 이 그 순서를 지킨다.
BODY_ANCHOR = "\\본문\\"

# 한 단 안에서 쪼개지면 읽기 어려운 대형 조각. 시험지의 실험형은 한 단 전체를
# 거의 채우므로, 앞 문항 뒤에 올 때는 머리말 아래의 새 쪽에서 시작한다.
NEW_PAGE_TEMPLATE_LABELS: Final = frozenset({
    "수능AI실제실험형", "수능합답실험1대사진5선지", "수능AI실제직접형새쪽",
})
ATOMIC_BOX_PAGE_BREAK_CHARS: Final = 380


def _cell_text_length(value):
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(len(str(segment.get("text") or "")) for segment in value)
    return len(str(value or ""))


def _template_needs_new_page(item, fills):
    """Return whether an atomic template needs the full next page flow."""
    label = str(item.get("label") or item.get("name") or "").strip("\\ ")
    if label in NEW_PAGE_TEMPLATE_LABELS:
        return True
    return any(
        isinstance(line, md_parser.Table)
        and (line.rows, line.cols) == (1, 1)
        and bool(line.grid) and bool(line.grid[0])
        and _cell_text_length(line.grid[0][0]) >= ATOMIC_BOX_PAGE_BREAK_CHARS
        for value in fills if isinstance(value, md_parser.MultiLine)
        for line in value.lines
    )


def _charshape_get(cs, label):
    if label == "굵게":
        return bool(cs.get("Bold"))
    if label == "기울임":
        return bool(cs.get("Italic"))
    if label == "밑줄":
        return int(cs.get("UnderlineType") or 0) != 0
    if label == "글자색":
        return cs.get("TextColor")
    if label == "자간":
        return cs.get("SpacingHangul")
    if label == "글꼴":
        return cs.get("FaceNameHangul")
    if label == "크기":
        h = cs.get("Height") or 0
        return round(h / 100, 1)
    return None


def capture_charshape(selected_labels):
    """현재 커서/선택 위치의 글자 서식에서, 체크된 항목만 델타로 캡처한다."""
    hwp = _h()
    act = hwp.HAction
    ps = hwp.HParameterSet
    act.GetDefault("CharShape", ps.HCharShape.HSet)
    cs = hwp.get_charshape_as_dict()
    delta = {}
    for label in selected_labels:
        v = _charshape_get(cs, label)
        if v is not None:
            delta[label] = v
    return delta


def apply_charshape_delta(delta):
    """델타에 있는 항목만 현재 선택/커서 위치에 적용한다(그 외 서식은 그대로 유지).

    GetDefault로 대상의 '현재' 서식을 먼저 불러온 뒤, 델타에 있는 필드만
    덮어써서 Execute 한다 — 이 방식이라야 부분 적용(굵기만 바꾸고 글꼴은
    유지 등)이 보장된다.
    """
    hwp = _h()
    act = hwp.HAction
    ps = hwp.HParameterSet
    act.GetDefault("CharShape", ps.HCharShape.HSet)
    if "굵게" in delta:
        ps.HCharShape.Bold = 1 if delta["굵게"] else 0
    if "기울임" in delta:
        ps.HCharShape.Italic = 1 if delta["기울임"] else 0
    if "밑줄" in delta:
        ps.HCharShape.UnderlineType = 1 if delta["밑줄"] else 0
    if "글자색" in delta:
        ps.HCharShape.TextColor = delta["글자색"]
    if "자간" in delta:
        ps.HCharShape.SpacingHangul = delta["자간"]
        ps.HCharShape.SpacingLatin = delta["자간"]
    if "글꼴" in delta:
        ps.HCharShape.FaceNameHangul = delta["글꼴"]
        ps.HCharShape.FaceNameLatin = delta["글꼴"]
    if "크기" in delta:
        ps.HCharShape.Height = hwp.PointToHwpUnit(delta["크기"])
    act.Execute("CharShape", ps.HCharShape.HSet)


# ── 라이브러리: 템플릿(통째 캡처) ───────────────────────
def auto_select_table_if_inside():
    """커서가 표 안이면(선택 없이 클릭만 한 상태) 그 표를 개체로 선택한다.

    예전 방식(Cancel+CloseEx 로 본문에 나온 뒤 MoveSelRight)은 복잡한 문서에서
    커서가 앵커 옆이 아닌 곳에 떨어져 **줄바꿈만 선택**되는 일이 있었다
    (실측 2026-07-19: CloseEx 후 위치가 앵커와 무관한 (0,1,8), 양옆 선택 모두
    '\\r\\n'). 커서가 속한 컨트롤을 ParentCtrl 로 직접 얻어 select_ctrl 로
    선택하는 것이 위치 계산 없이 정확하다.
    반환: 선택 성공 여부.
    """
    hwp = _h()
    try:
        if hwp.GetPos()[0] == 0:
            return False        # 본문에 있음 — 표 안이 아님
    except Exception as e:
        applog.exc("표 안 여부 확인 실패", e)
        return False
    try:
        ctrl = hwp.ParentCtrl
        if ctrl is None:
            return False
        hwp.select_ctrl(ctrl)
    except Exception as e:
        applog.exc("표 선택 실패", e)
        return False
    return has_selection()


def delete_table_containing_text(text):
    """Delete the table that contains ``text``, leaving non-table matches intact."""
    hwp = _h()
    hwp.HAction.Run("MoveDocBegin")
    if not find_text(text):
        return False
    try:
        ctrl = hwp.ParentCtrl
        if ctrl is None or str(getattr(ctrl, "CtrlID", "")) != "tbl":
            hwp.HAction.Run("Cancel")
            return False
        hwp.delete_ctrl(ctrl)
        return True
    except Exception as exc:
        applog.exc("표 삭제 실패", exc)
        return False


def set_paragraph_word_boundary_wrap(text, character_ratio=85):
    """Prevent Korean words in the paragraph containing ``text`` from splitting."""
    hwp = _h()
    hwp.HAction.Run("MoveDocBegin")
    if not find_text(text):
        return False
    hwp.HAction.Run("Cancel")
    act, ps = hwp.HAction, hwp.HParameterSet
    act.GetDefault("ParagraphShape", ps.HParaShape.HSet)
    ps.HParaShape.BreakNonLatinWord = 0
    # KICE paragraphs keep words intact but tighten inter-word spacing enough
    # to retain the source line measure. HWP expresses the lower bound as a
    # percentage of the normal word-space width.
    ps.HParaShape.Condense = 20
    act.Execute("ParagraphShape", ps.HParaShape.HSet)
    hwp.MoveParaBegin()
    hwp.MoveSelParaEnd()
    hwp.set_font(Ratio=character_ratio)
    act.Run("Cancel")
    return True


def set_matching_paragraph_character_ratio(
        text, apply_matches, character_ratio=90, wrapped_only=False,
        required_paragraph_text=(),
):
    """Condense selected ordered matches without changing their font height."""
    hwp = _h()
    hwp.HAction.Run("MoveDocBegin")
    for should_apply in apply_matches:
        while True:
            if not find_text(text):
                return False
            hwp.HAction.Run("Cancel")
            if not required_paragraph_text:
                break
            hwp.MoveParaBegin()
            hwp.MoveSelParaEnd()
            paragraph = hwp_engine.read_selection_direct()
            hwp.HAction.Run("Cancel")
            if all(marker in paragraph for marker in required_paragraph_text):
                break
            hwp.HAction.Run("MoveParaEnd")
        if should_apply:
            hwp.MoveParaBegin()
            start = tuple(hwp.GetPos()) if wrapped_only else None
            wrapped = True
            if wrapped_only:
                hwp.HAction.Run("MoveLineEnd")
                line_end = tuple(hwp.GetPos())
                hwp.HAction.Run("MoveParaEnd")
                paragraph_end = tuple(hwp.GetPos())
                wrapped = line_end != paragraph_end
                hwp.SetPos(*start)
            if wrapped:
                hwp.MoveSelParaEnd()
                hwp.set_font(Ratio=character_ratio)
                hwp.HAction.Run("Cancel")
        hwp.HAction.Run("MoveParaEnd")
    return True


def _set_current_paragraph_word_boundary_wrap():
    """Keep Korean eojeol intact in the paragraph at the insertion cursor."""
    hwp = _h()
    act, ps = hwp.HAction, hwp.HParameterSet
    act.GetDefault("ParagraphShape", ps.HParaShape.HSet)
    ps.HParaShape.BreakNonLatinWord = 0
    ps.HParaShape.Condense = 20
    act.Execute("ParagraphShape", ps.HParaShape.HSet)


def delete_trailing_csat_form_page():
    """Remove an unused trailing page retained by the CSAT form."""
    hwp = _h()
    try:
        page_count = int(hwp.PageCount)
        if page_count < 2:
            return False
        # pyhwpx GetPageText is zero-based, unlike goto_page. Passing PageCount
        # itself probes beyond the document and can stall HWP on large sets.
        text = str(hwp.get_page_text(page_count - 1) or "")
        if any(token in text for token in ("20.", "그림", "①", "②", "③", "④", "⑤")):
            return False
        hwp.goto_page(page_count)
        return bool(hwp.DeletePage())
    except Exception as exc:
        applog.exc("빈 수능 양식 뒷쪽 삭제 실패", exc)
        return False


def capture_fragment(dest_path):
    r"""현재 선택 영역을 통째로 조각 .hwp 파일로 저장한다(병합·서식 그대로).

    방식: 복사 → 새 탭에 붙여넣기 → 그 문서를 통째로 저장 → 탭 닫기.

    save_block_as(FileSaveBlock_S)를 쓰지 않는 이유 (실측 2026-07-19):
      표를 **개체로 선택**한 상태(SelectionMode 4 — 표 테두리 클릭 등)에서
      FileSaveBlock_S 는 선택만 저장하지 않고 **문서 전체를 저장**한다.
      복잡한 문서에서 표 하나를 캡처했는데 다른 내용까지 다 들어가고,
      그 비대한 조각을 삽입하면 한글이 멈추던 버그의 원인.
      복사→붙여넣기는 선택 종류(글자 선택 1 / 개체 선택 4)와 무관하게
      선택한 것만 정확히 담는다 (두 모드 모두 실측 확인).

    부작용: 사용자의 클립보드가 캡처 내용으로 바뀐다 — 캡처 흐름에서는
    read_selection_text 가 이미 Copy 를 쓰고 있어 추가 손해는 없다.

    반환: 조각의 본문 글자 (미리보기용, UI 제안 7). 실패하면 빈 문자열.
      **여기서 뽑는 이유** — 이 순간이 조각 내용을 글로 읽을 수 있는 유일한
      때다. .hwp 는 이진 형식이라 나중에 파일만 보고는 못 읽고, 다시 읽으려면
      한글을 켜서 파일을 열어야 한다. 지금은 이미 임시 탭에 펼쳐져 있다.
    """
    hwp = _h()
    if not has_selection():
        raise RuntimeError("캡처할 선택 영역이 없습니다")
    hwp.HAction.Run("Copy")
    saved = hwp.XHwpDocuments.Count
    preview = ""
    try:
        hwp.XHwpDocuments.Add(1)          # 1 = 새 탭
        hwp.HAction.Run("Paste")
        # 붙여넣기가 비었으면 저장하지 않는다 (2026-07-27). 클립보드는 다른
        # 프로그램(클립보드 관리자·Win+V 기록)이 잠깐 잡을 수 있고, 그러면
        # Copy 가 조용히 실패해 **빈 조각이 원본을 덮어쓴다.** 이 프로젝트는
        # 클립보드 경합을 이미 여러 번 겪었다(clipboard.py 머리말).
        if doc_is_empty():
            applog.warn("조각 캡처: 붙여넣기가 비었습니다 — 다시 시도합니다")
            hwp.HAction.Run("Copy")
            hwp.HAction.Run("Paste")
            if doc_is_empty():
                raise RuntimeError(
                    "복사한 내용이 비어 있습니다 (클립보드를 다른 프로그램이 "
                    "쓰는 중일 수 있습니다).\n잠시 뒤 다시 시도해주세요.")
        # 옛 습관대로 홑 \ 를 쳤어도 저장물은 항상 새 문법(\\)이 되게.
        # 임시 탭에서만 고치므로 사용자의 원본 문서는 그대로다.
        try:
            normalize_marks_to_pairs()
        except Exception as e:
            applog.exc("자리 표시 정리 실패 — 옛 문법 그대로 저장 (읽기는 됨)", e)
        hwp.save_as(str(dest_path), format="HWP")
        try:
            preview = hwp.GetTextFile("TEXT", "") or ""
        except Exception as e:
            applog.exc("조각 미리보기 글자 추출 실패 — 미리보기 없이 등록", e)
    finally:
        try:
            if hwp.XHwpDocuments.Count > saved:
                hwp.XHwpDocuments.Active_XHwpDocument.Close(isDirty=False)
        except Exception as e:
            applog.exc("캡처용 임시 탭 닫기 실패 — 탭이 남아 있을 수 있음", e)
    return preview


def _png_size_mm(path):
    r"""PNG 에 새겨진 '실제 인쇄 크기'(mm). 없으면 None.

    5E 가 내보낸 그림에는 300dpi 가 pHYs 청크로 박혀 있다. 이 값을 안 쓰면
    한글이 제 나름의 기준으로 넣어 시험지에 그림이 지나치게 크게 박힌다
    (2026-08-03 실측: 80mm 도판이 130mm 로 들어갔다).
    """
    try:
        with open(path, "rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                return None
            w = h = ppm_x = ppm_y = None
            while True:
                head = f.read(8)
                if len(head) < 8:
                    break
                ln = int.from_bytes(head[:4], "big")
                typ = head[4:8]
                data = f.read(ln)
                f.read(4)                       # CRC
                if typ == b"IHDR":
                    w = int.from_bytes(data[0:4], "big")
                    h = int.from_bytes(data[4:8], "big")
                elif typ == b"pHYs":
                    if len(data) >= 9 and data[8] == 1:      # 단위 1 = 미터
                        ppm_x = int.from_bytes(data[0:4], "big")
                        ppm_y = int.from_bytes(data[4:8], "big")
                    break
                elif typ == b"IDAT":
                    break                       # 픽셀 데이터 전까지만 본다
            if not (w and h and ppm_x and ppm_y):
                applog.warn(f"PNG pHYs 청크가 없어 이미지가 기본 크기로 삽입됩니다 ({path})")
                return None
            return w / ppm_x * 1000.0, h / ppm_y * 1000.0
    except Exception as e:
        applog.exc(f"PNG 크기 읽기 실패 ({path}) — 한글 기본 크기로 넣는다", e)
        return None


def _image_size_mm(path):
    r"""지원 그림의 인쇄 크기(mm). PNG 메타데이터를 우선하고 나머지는 Pillow로 읽는다.

    JPEG처럼 pHYs 청크가 없는 형식도 단 폭 제한을 받아야 한다. DPI가 없는 파일은
    화면 이미지의 관례값 96dpi로 계산한 뒤 아래 삽입기가 단 폭에 맞춰 축소한다.
    """
    sidecar = pathlib.Path(path).with_suffix(".json")
    try:
        bbox = json.loads(sidecar.read_text(encoding="utf-8")).get("bbox")
        if isinstance(bbox, list) and len(bbox) == 4:
            width_points = float(bbox[2]) - float(bbox[0])
            height_points = float(bbox[3]) - float(bbox[1])
            if width_points > 0 and height_points > 0:
                return width_points * 25.4 / 72.0, height_points * 25.4 / 72.0
    except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    png_size = _png_size_mm(path)
    if png_size:
        return png_size
    try:
        from PIL import Image
        with Image.open(path) as image:
            width_px, height_px = image.size
            dpi = image.info.get("dpi") or (96.0, 96.0)
            dpi_x = float(dpi[0] or 96.0)
            dpi_y = float((dpi[1] if len(dpi) > 1 else dpi_x) or 96.0)
            return width_px / dpi_x * 25.4, height_px / dpi_y * 25.4
    except Exception as e:
        applog.exc(f"그림 크기 읽기 실패 ({path}) — 한글 기본 크기로 넣는다", e)
        return None


def _fit_picture_size_mm(width_mm, height_mm):
    """Fit a picture to the configured print frame without changing its aspect."""
    layout = hwp_engine.S.get("layout", {})
    frame_width = float(layout.get("figure_frame_width_mm") or 0)
    target_ratio = float(layout.get("figure_target_ratio") or 0)
    if frame_width > 0 and 0 < target_ratio <= 1:
        limit = frame_width * target_ratio
    else:
        limit = hwp_engine._col_width_mm()
    if limit and width_mm > limit:
        scale = limit / width_mm
        return limit, height_mm * scale
    return width_mm, height_mm


def _contain_picture_size_mm(
    width_mm, height_mm, max_width_mm, max_height_mm, *, allow_upscale=False,
):
    """Fit a picture inside both bounds with one aspect-preserving scale."""
    if width_mm <= 0 or height_mm <= 0:
        return width_mm, height_mm
    scale = min(max_width_mm / width_mm, max_height_mm / height_mm)
    if not allow_upscale:
        scale = min(1.0, scale)
    return width_mm * scale, height_mm * scale


GRAPHICAL_CHOICE_LABEL_CELL_WIDTH_MM = 4.0
GRAPHICAL_CHOICE_IMAGE_CELL_WIDTH_MM = 33.333
MINIMUM_READABLE_PICTURE_SCALE = 0.70
PICTURE_SCALE_ROUNDING_MARGIN = 0.001
SINGLE_PICTURE_CELL_MIN_WIDTH_MM = 30.0
SMALL_SINGLE_PICTURE_CELL_MAX_WIDTH_MM = 55.0
LARGE_SINGLE_CELL_MAX_WIDTH_MM = 90.0


def _is_graphical_choice_picture_cell(hwp):
    """Recognize the three-choice row by its live HWP cell geometry."""
    width_mm = float(hwp.get_col_width())
    height_mm = float(hwp.get_row_height())
    return (
        (hwp.get_row_num(), hwp.get_col_num()) == (1, 3)
        and 28.5 <= width_mm <= 33.6
        and 19.5 <= height_mm <= 20.5
    )


def _is_one_small_picture_cell(hwp):
    """Recognize the registered 43-by-29 mm one-small picture cell."""
    return (
        (hwp.get_row_num(), hwp.get_col_num()) == (1, 1)
        and 42.5 <= float(hwp.get_col_width()) <= 43.5
        and 28.5 <= float(hwp.get_row_height()) <= 30.5
    )


def _prepare_graphical_choice_grid_cell(hwp):
    """Give each picture in the fixed 3-by-2 answer grid its print-width budget."""
    if not _is_graphical_choice_picture_cell(hwp):
        return False
    position = hwp.get_pos()
    hwp.TableLeftCell()
    hwp.set_col_width(GRAPHICAL_CHOICE_LABEL_CELL_WIDTH_MM, as_="mm")
    hwp.set_cell_margin(0.0, 0.0, 0.5, 0.5, as_="mm")
    hwp.TableRightCell()
    hwp.set_col_width(GRAPHICAL_CHOICE_IMAGE_CELL_WIDTH_MM, as_="mm")
    hwp.set_cell_margin(0.1, 0.1, 0.5, 0.5, as_="mm")
    hwp.set_pos(*position)
    return True


def _table_picture_bounds_mm(hwp):
    """Return the current cell's usable content box, excluding inner margins."""
    width_mm = float(hwp.get_col_width())
    height_mm = float(hwp.get_row_height())
    margins = hwp.get_cell_margin()
    if isinstance(margins, dict):
        width_mm -= float(margins.get("left", 0.0) or 0.0)
        width_mm -= float(margins.get("right", 0.0) or 0.0)
        height_mm -= float(margins.get("top", 0.0) or 0.0)
        height_mm -= float(margins.get("bottom", 0.0) or 0.0)
    column_count = hwp.get_col_num()
    if (
        isinstance(column_count, int)
        and column_count > 1
        and not _is_graphical_choice_picture_cell(hwp)
    ):
        # HWP can paint ordinary paired panels beyond the printable column even
        # when their declared width fits.  The fixed 3-choice-per-row grid is
        # different: each picture already owns a bounded cell, so inheriting
        # the pair-table inset would discard most of its readable width.
        width_mm -= 7.0
    return max(width_mm, 0.1), max(height_mm, 0.1)


def _grow_large_single_picture_cell(hwp, size):
    """Grow a one-picture row when fixed cell bounds would make it unreadable."""
    cell_width = float(hwp.get_col_width())
    if cell_width < SINGLE_PICTURE_CELL_MIN_WIDTH_MM:
        return False
    width_mm, height_mm = _table_picture_bounds_mm(hwp)
    source_width, source_height = size
    target_scale = MINIMUM_READABLE_PICTURE_SCALE + PICTURE_SCALE_ROUNDING_MARGIN
    required_width = source_width * target_scale + cell_width - width_mm
    if width_mm / source_width < MINIMUM_READABLE_PICTURE_SCALE:
        if required_width > LARGE_SINGLE_CELL_MAX_WIDTH_MM:
            return False
        hwp.set_col_width(required_width, as_="mm")
        width_mm, height_mm = _table_picture_bounds_mm(hwp)
    if height_mm / source_height >= MINIMUM_READABLE_PICTURE_SCALE:
        return False
    hwp.set_row_height(source_height * target_scale + 2.0)
    return True


def _top_align_small_single_picture_cell(hwp):
    """Keep compact source-side figures aligned with the first stem line."""
    width_mm = float(hwp.get_col_width())
    if not (
        SINGLE_PICTURE_CELL_MIN_WIDTH_MM <= width_mm
        < SMALL_SINGLE_PICTURE_CELL_MAX_WIDTH_MM
        and (hwp.get_row_num(), hwp.get_col_num()) == (1, 1)
    ):
        return False
    hwp.TableCellAlignCenterTop()
    return True


def _insert_picture_sized(hwp, path, max_bounds_mm=None):
    r"""그림 삽입. 셀 밖이면 PNG 에 새겨진 실제 크기로, 셀 안이면 셀에 맞춘다.

    셀 안에서는 원본 전체를 삽입한 뒤 셀 폭과 높이에 contain-fit 한다. 한글의
    sizeoption=3은 일부 표에서 그림을 셀 비율로 잘라 버리므로 사용하지 않는다.
    셀 밖에서는 한글이 그림을 **판면 폭까지 늘려서** 넣기 때문에(실측: 80mm
    도판이 150mm 로) 실제 크기를 되돌려 준다.

    InsertPicture 의 Width/Height 인자는 이 버전에서 무시된다(실측) — 넣은 뒤
    개체 속성으로 지정해야 먹는다. 판면보다 넓은 그림은 비율을 지켜 줄인다.

    스펙의 exam_image_style 이 설정돼 있으면 평가원 시험지 스타일로 자동 변환한다.
    """
    exam_style = hwp_engine.S.get("exam_image_style", "")
    actual_path = path
    source_size = _image_size_mm(path)
    if exam_style and pathlib.Path(path).suffix.lower() != ".hwp":
        try:
            from hwp_palette.hwp import exam_image
            import tempfile
            tmp = tempfile.NamedTemporaryFile(suffix="_exam.png", delete=False)
            tmp.close()
            actual_path = str(exam_image.convert(path, tmp.name, style=exam_style))
        except Exception as e:
            applog.exc(f"시험지 스타일 변환 실패 ({exam_style}) — 원본 그대로 삽입", e)
            actual_path = path
    size = _image_size_mm(actual_path)
    if actual_path != path and source_size and size:
        source_ratio = source_size[0] / source_size[1]
        converted_ratio = size[0] / size[1]
        if abs(converted_ratio - source_ratio) / source_ratio > 0.02:
            pathlib.Path(actual_path).unlink(missing_ok=True)
            actual_path = path
            size = source_size
    in_table = hwp_engine.in_table()
    table_picture_size = None
    if in_table and size:
        _prepare_graphical_choice_grid_cell(hwp)
        _grow_large_single_picture_cell(hwp, size)
        _top_align_small_single_picture_cell(hwp)
        bounds_mm = _table_picture_bounds_mm(hwp)
        if max_bounds_mm is not None:
            bounds_mm = tuple(
                min(actual, maximum)
                for actual, maximum in zip(bounds_mm, max_bounds_mm, strict=True)
            )
        table_picture_size = _contain_picture_size_mm(
            *size, *bounds_mm,
            allow_upscale=_is_one_small_picture_cell(hwp),
        )
        width_mm, height_mm = table_picture_size
        if hwp.get_col_num() == 1 and hwp.get_row_num() == 1:
            hwp.set_row_height(min(float(hwp.get_row_height()), height_mm + 2.0))
    if in_table and table_picture_size:
        ctrl = hwp.insert_picture(
            str(actual_path), treat_as_char=True, embedded=True, sizeoption=0,
        )
    else:
        ctrl = hwp.insert_picture(str(actual_path), treat_as_char=True, embedded=True,
                                  sizeoption=3)
    if exam_style and actual_path != path:
        try:
            pathlib.Path(actual_path).unlink(missing_ok=True)
        except OSError as e:
            applog.exc(f"임시 시험지 스타일 그림 삭제 실패 ({actual_path})", e)
    if not size or ctrl is None:
        return
    if in_table:
        w_mm, h_mm = table_picture_size
    else:
        w_mm, h_mm = _fit_picture_size_mm(*size)
    try:
        pset = ctrl.Properties
        pset.SetItem("Width", hwp.MiliToHwpUnit(round(w_mm, 2)))
        pset.SetItem("Height", hwp.MiliToHwpUnit(round(h_mm, 2)))
        ctrl.Properties = pset
    except Exception as e:
        applog.exc(f"그림 크기 지정 실패 ({path}) — 한글이 넣은 크기로 둔다", e)


def insert_photo(path, exam_style=None):
    r"""사진 파일을 커서 자리에 글자처럼 삽입 (물감 설정 '사진' 탭의 삽입).

    exam_style이 주어지면 평가원 시험지 스타일로 변환한 뒤 삽입한다.
    사용 가능 스타일: 'exam-clean', 'exam-diagram', 'contour', 'sketch', 'threshold', 'adaptive'.

    옵션은 마크다운 변환의 \사진이름\ 삽입(insert_rich_line)과 같다.
    삽입 직후 한글이 그림 개체를 선택한 채로 두므로 선택을 풀어 준다.
    """
    hwp = _h()
    actual_path = path
    if exam_style:
        try:
            from hwp_palette.hwp import exam_image
            import tempfile, os
            tmp = tempfile.NamedTemporaryFile(suffix="_exam.png", delete=False)
            tmp.close()
            actual_path = str(exam_image.convert(path, tmp.name, style=exam_style))
        except Exception as e:
            applog.exc(f"시험지 스타일 변환 실패 ({exam_style}) — 원본 그대로 삽입", e)
            actual_path = path
    _insert_picture_sized(hwp, actual_path)
    if exam_style and actual_path != path:
        try:
            os.unlink(actual_path)
        except Exception:
            pass
    try:
        hwp.HAction.Run("Cancel")
    except Exception as e:
        applog.exc("사진 삽입 후 개체 선택 해제 실패 (무해)", e)


def insert_fragment(path):
    r"""조각 .hwp 파일을 커서 위치에 그대로 삽입한다.

    keep_section=0 필수 — 조각에는 저장 당시의 구역(secd) 정의가 같이 담기는데,
    이를 유지(1)하면 구역 나눔이 일어나 표가 '다음 페이지'에 생성된다(실측 2026-07-15).

    나머지 keep_* 는 pyhwpx 기본값이 전부 1이라 지금까지 암묵적으로 1이었다
    (2026-07-18 pyhwpx 소스 확인 — 개선안 6). 무엇이 딸려오는지 코드에 드러나도록
    명시해 둔다. 값 자체는 바꾸지 않았다:
      keep_charshape / keep_parashape = 1
        조각의 글자·문단 서식 유지. 표가 원본대로 재현되려면 반드시 1이어야 한다.
      keep_style = 1
        조각에 딸린 '스타일 정의'까지 대상 문서로 들여온다. 같은 이름의 스타일이
        대상 문서에 있으면 덮어쓸 여지가 있다. 0으로 바꾸면 그 부작용은 사라지지만
        조각 모양이 달라질 수 있어, 실제 피해 사례가 관측되기 전까지는 유지한다.
    """
    hwp_engine._diag("insert_fragment: insert_file 직전")
    _h().insert_file(str(path), keep_section=0, keep_charshape=1,
                     keep_parashape=1, keep_style=1)
    hwp_engine._diag("insert_fragment: insert_file 직후  <<< 여기서 바뀌면 insert_file 범인")


def insert_table(rows, cols, grid):
    r"""커서 자리에 rows×cols 표를 만들고 셀을 채운다 (\표3x3\ 변환용, 2026-07-25).

    셀 이동은 TableRightCell 하나로 한다 — 행 끝에서 **다음 행 첫 칸**으로
    넘어간다(exam_engine 이 2행2열 표를 같은 방식으로 채운다).

    grid 가 모자라도 된다. 없는 자리는 건너뛰어 빈 칸으로 남긴다 — 표는 이미
    만들어졌으니 사용자가 한글에서 마저 채우면 된다. 변환을 통째로 실패시키는
    것보다 낫다.
    """
    parent_list_id = _h().GetPos()[0]
    hwp_engine.create_table_autofit(rows, cols)
    act = _h().HAction
    for r in range(rows):
        row = grid[r] if r < len(grid) else []
        for c in range(cols):
            if r or c:                      # 첫 칸은 이미 커서가 있다
                act.Run("TableRightCell")
            act.Run("ParagraphShapeAlignCenter")
            value = row[c] if c < len(row) else None
            if value is None:
                continue                    # '-' 이거나 값이 없는 칸
            if isinstance(value, list):
                insert_rich_line(value)     # 사진·서식이 섞인 칸
            else:
                insert_plain(value)
    hwp_engine.exit_table(parent_list_id=parent_list_id)


# ── 빈칸(\) 처리 ──────────────────────────────────────
# 한 번의 청소에서 훑을 빈칸 개수 상한 — 무한 루프 방지용 안전장치
_MAX_SLOT_SCAN = 200


def measure_insert_span(anchor_pos, insert_fn):
    r"""anchor_pos에 insert_fn()으로 삽입한 내용의 '마지막 문단 번호'를 돌려준다.

    insert_file 직후 커서가 삽입물 뒤로 이동하지 않아(실측) 끝 위치를 커서로는
    알 수 없다. 그래서 삽입 전후의 '문서 마지막 문단 번호' 차이로 삽입물이
    차지한 문단 수를 역산한다.

    **커서가 표 안이면 None 을 돌려준다.** doc_end_para() 는 본문(list 0) 기준
    번호인데 anchor_pos[1] 은 셀 안에서 다시 센 번호라, 둘을 더하면 뜻이 없는
    수가 나온다. 그 수로 범위를 재면 빈칸이 하나도 안 채워지고 `\` 가 문서에
    그대로 남는다. 범위를 모를 땐 개수 상한(max_delete)에 맡기는 게 맞다.
    """
    hwp = _h()
    if anchor_pos[0] != 0:
        insert_fn()
        hwp.SetPos(*anchor_pos)
        return None
    before = hwp_engine.doc_end_para()
    after = before
    try:
        hwp.SetPos(*anchor_pos)
        insert_fn()
        after = hwp_engine.doc_end_para()
    finally:
        # doc_end_para 가 커서를 문서 끝으로 옮기므로, 중간에 무엇이 터져도
        # 커서는 반드시 제자리로 — 안 그러면 사용자의 커서가 문서 끝에 남는다
        hwp.SetPos(*anchor_pos)
    return anchor_pos[1] + max(after - before, 0)


def _beyond(end_para):
    r"""현재 커서가 삽입 범위를 벗어났는가.

    한계 (그래서 개수 상한을 함께 쓴다):
      GetPos() 는 (list, para, pos) 인데 **para 는 list 안에서의 번호**다.
      표 안에 들어가면 list 가 바뀌면서 para 가 셀 기준으로 다시 세어지므로
      (본문 300번째 문단의 표 안이어도 para 가 0 일 수 있다), 본문 기준으로
      계산한 end_para 와 곧바로 비교할 수 없다. 즉 이 검사만으로는
      "삽입 범위 아래에 있는 사용자의 표 속 \" 를 걸러내지 못한다.
      → strip_slot_markers/fill_slots 의 max_delete(빈칸 개수 상한)가 그 구멍을 막는다.
    """
    if end_para is None:
        return False
    try:
        list_id, para, _ = _h().GetPos()
    except Exception as e:
        # 위치를 모르면 '벗어났다'고 본다 — 남의 문서를 지우느니 빈칸을 남긴다
        applog.exc("빈칸 범위 확인 실패 — 안전하게 청소 중단", e)
        return True
    if list_id != 0:
        return False        # 표/각주 등 — para 비교가 무의미. 개수 상한에 맡긴다
    return para > end_para


def _before_anchor(anchor_pos):
    r"""커서가 삽입 지점보다 **앞**에 있는가 (되돌아간 것으로 본다).

    find_text 는 RepeatFind 인데, 문서 끝에 닿았을 때 맨 앞으로 되돌아가
    다시 찾는지 확인하지 못했다. 만약 되돌아간다면 앞쪽에 있는 사용자의 `\`
    가 '범위 안'으로 보여(문단 번호가 end_para 보다 작으므로) 지워져 버린다.
    삽입 지점보다 앞은 무슨 일이 있어도 우리 것이 아니므로 여기서 멈춘다.
    """
    try:
        list_id, para, pos = _h().GetPos()
    except Exception:
        return True         # 모르면 멈춘다
    if list_id != anchor_pos[0]:
        return False        # 다른 리스트 — 앞뒤를 따질 수 없다
    return (para, pos) < (anchor_pos[1], anchor_pos[2])


def strip_slot_markers(anchor_pos, end_para=None, max_delete=None):
    r"""anchor_pos부터 end_para 문단까지 남은 빈칸 표시(\)를 제거한다.

    빈칸 표시는 '여기에 내용이 들어간다'는 안내일 뿐이라, 채워지지 않고 남으면
    출력물에 그대로 보인다 → 삽입/변환 후 이 함수로 청소한다.

    end_para 를 반드시 넘겨야 하는 이유 (개선안 5):
      예전에는 anchor 부터 **문서 끝까지** 무조건 지웠다. 그래서 삽입한 템플릿
      아래에 사용자가 직접 써 둔 \ 까지 같이 사라졌다. 조용히 일어나고 인쇄물을
      보기 전에는 알 수 없는 훼손이라 위험도가 높았다.
      end_para=None 은 '문서 전체가 대상'인 경우(양식을 새 문서로 연 직후)에만
      의도적으로 쓴다.

    max_delete: 지울 빈칸 개수 상한. 템플릿이 선언한 빈칸 수를 넘겨 받는다.
      end_para 만으로는 표 안을 걸러내지 못하기 때문에(_beyond 설명 참고)
      반드시 함께 써야 한다 — "이 템플릿엔 빈칸이 3개다"가 가장 확실한 경계다.
      None 이면 개수 제한 없음(양식 전체 청소).
    """
    hwp = _h()
    act = hwp.HAction
    hwp.SetPos(*anchor_pos)
    limit = _MAX_SLOT_SCAN if max_delete is None else min(max_delete, _MAX_SLOT_SCAN)
    for _ in range(limit):
        if not find_text("\\"):
            break
        if _before_anchor(anchor_pos) or _beyond(end_para):
            break                   # 삽입 범위 밖 — 사용자가 쓴 \ 이므로 건드리지 않는다
        act.Run("Delete")


def _named_slot_candidates(name):
    """Return exact markers for a declared palette slot name."""
    token = f"\\{name}\\"
    # 자간맞춤 is authoring syntax, not text that should remain in the output.
    return [f"\\자간맞춤{{{token}}}", token]


def _is_panel_caption_slot(name):
    return str(name or "").strip() in {
        "(가)", "(나)", "(다)", "(라)", "(마)", "(바)", "(사)", "(아)",
    }


def _is_photo_slot(name):
    normalized = str(name or "").strip()
    return (
        normalized == "자료"
        or normalized.startswith("사진")
        or normalized.startswith("선지사진")
    )


def _set_current_photo_cell_start():
    """Center a picture from a clean ruler origin in every declared cell."""
    hwp = _h()
    action = hwp.HAction
    paragraph = hwp.HParameterSet.HParaShape
    action.GetDefault("ParagraphShape", paragraph.HSet)
    paragraph.LeftMargin = 0
    paragraph.Indentation = 0
    paragraph.AlignType = 3
    action.Execute("ParagraphShape", paragraph.HSet)


def fill_slots(anchor, fills, end_para=None, slot_count=None, slot_names=None):
    r"""anchor 이후의 빈칸(\)을 fills 로 위에서부터 채우고, 남은 건 청소.

    반환: 실제로 채운 개수.

    반환: (채운 개수, 채우려던 개수). 두 값이 다르면 중간에 멈춘 것이다 —
    호출부가 그 사실을 사용자에게 알려야 한다. 조용히 넘기면 인쇄물을 보고서야
    빈칸이 남은 걸 알게 된다.

    end_para 는 삽입 직후 기준의 범위다. 채워 넣는 값이 문단을 늘리지는 않지만
    (한 줄짜리 텍스트만 들어온다), 혹시 어긋나더라도 실패 방향은 '빈칸이 남는다'
    쪽이지 '남의 글자를 지운다' 쪽이 아니다.
    """
    hwp = _h()
    act = hwp.HAction
    filled = 0
    used = 0
    want = sum(1 for v in fills if v is not None)
    # 여러 줄 덩어리({ })를 채우면 문단이 그만큼 늘어난다. end_para 를 그대로
    # 두면 뒤쪽 빈칸이 '범위 밖'으로 밀려 채우기가 멈춘다(2026-08-03 실측:
    # 여러 줄 지문 뒤의 점수·보기·선지가 통째로 안 채워짐) — 늘어난 만큼
    # 범위를 함께 민다. 실패 방향은 여전히 '빈칸이 남는다' 쪽이다: 범위를
    # 넉넉히 잡아도 개수 상한(slot_count)이 아래쪽 사용자 문서를 지킨다.
    grow = 0

    def _limit():
        return None if end_para is None else end_para + grow

    def _put(value, slot_name=""):
        nonlocal filled, grow
        picture_bounds_mm = (43.0, 43.5) if slot_name == "실험과정" else None
        if value is None:
            act.Run("Delete")
        elif isinstance(value, md_parser.MultiLine):
            grow += len(value.lines) - 1
            grow += sum(1 for lv in value.lines
                        if isinstance(lv, md_parser.Table))
            delete_selection()
            for n, line_value in enumerate(value.lines):
                if n:
                    act.Run("BreakPara")
                if isinstance(line_value, md_parser.Table):
                    insert_table(line_value.rows, line_value.cols,
                                 line_value.grid)
                elif isinstance(line_value, list):
                    insert_rich_line(line_value, picture_bounds_mm=picture_bounds_mm)
                    _set_current_paragraph_word_boundary_wrap()
                else:
                    insert_plain(line_value)
                    _set_current_paragraph_word_boundary_wrap()
            filled += 1
        elif isinstance(value, list):
            delete_selection()
            insert_rich_line(value, picture_bounds_mm=picture_bounds_mm)
            _set_current_paragraph_word_boundary_wrap()
            filled += 1
        else:
            insert_plain(value)
            _set_current_paragraph_word_boundary_wrap()
            if _is_panel_caption_slot(slot_name):
                act.Run("ParagraphShapeAlignCenter")
            filled += 1

    # A floating table is visited before the rest of its host paragraph by
    # HWP Find. Sequential anonymous markers can consequently swap 문두 and
    # 사진1 even though the template is valid. Fill declared names exactly;
    # retain anonymous-marker fallback only for legacy templates.
    pending = list(range(len(fills)))
    if slot_names:
        pending = []
        for index, value in enumerate(fills):
            name = str(slot_names[index]).strip() if index < len(slot_names) else ""
            found = False
            if name:
                for query in _named_slot_candidates(name):
                    hwp.SetPos(*anchor)
                    if (find_text(query) and not _before_anchor(anchor)
                            and not _beyond(_limit())):
                        found = True
                        break
            if not found:
                pending.append(index)
                continue
            used += 1
            if _is_photo_slot(name):
                _set_current_photo_cell_start()
            _put(value, name)

    if pending:
        normalize_slot_tokens(anchor, _limit())
    hwp.SetPos(*anchor)
    for index in pending:
        value = fills[index]
        if not find_text("\\"):
            # 빈칸을 못 찾으면 남은 값은 갈 곳이 없다. 예전에는 조용히 멈춰서
            # 사용자가 인쇄물을 보고서야 알았다 → 기록을 남긴다.
            applog.warn(f"빈칸을 더 찾지 못해 채우기를 멈춥니다 "
                        f"({filled}/{want}개 채움)")
            break
        if _before_anchor(anchor) or _beyond(_limit()):
            applog.warn(f"빈칸이 삽입 범위를 벗어나 채우기를 멈춥니다 "
                        f"({filled}/{want}개 채움)")
            break
        used += 1
        name = str(slot_names[index]).strip() if slot_names and index < len(slot_names) else ""
        _put(value, name)
    # 남은 빈칸만 청소한다. slot_count 를 알면 "이 템플릿에 남은 개수"가 정확한
    # 상한이 된다 — 그만큼만 지우므로 아래쪽 사용자 문서는 절대 안 건드린다.
    remaining = None if slot_count is None else max(int(slot_count) - used, 0)
    strip_slot_markers(anchor, _limit(), max_delete=remaining)
    return filled, want


def normalize_slot_tokens(anchor, end_para=None, limit=500):
    r"""anchor~end_para 범위의 새 문법 토큰을 **홑 역슬래시로 줄인다**.

        \학년\  →  \        \\  →  \

    왜 줄이나 (2026-07-27 문법 확정): 채우기·청소 코드는 전부 "홑 \ 를
    순서대로 찾는다"로 짜여 있고 검증돼 있다. 삽입 직후 문서를 그 모습으로
    만들어 두면 아래 코드는 한 줄도 안 바뀐다. 이름을 안 줄이면 `\학년\` 의
    앞쪽 \ 만 값으로 바뀌어 '학년' 이 글자로 남는 사고가 난다 (실측 시나리오).

    이름 정보는 잃지 않는다 — 물감을 저장할 때 slot_names 로 이미 적어 둔다.
    반환: 줄인 토큰 수.
    """
    hwp = _h()
    # 문서에 실제로 있는 이름표만 찾는다 (없는 이름을 찾느라 헤매지 않게)
    names = []
    try:
        text = hwp.GetTextFile("TEXT", "") or ""
        seen = set()
        for m in form_fill.TOKEN_RE.finditer(text.replace(BODY_ANCHOR, "")):
            if m.group(1) and m.group(1) not in seen:
                seen.add(m.group(1))
                names.append(m.group(1))
    except Exception as e:
        applog.exc("이름표 목록 뽑기 실패 — \\\\ 쌍만 줄입니다", e)
    changed = 0
    for tok in [f"\\{n}\\" for n in names] + ["\\\\"]:
        hwp.SetPos(*anchor)
        while changed < limit and find_text(tok):
            if _before_anchor(anchor) or _beyond(end_para):
                break
            delete_selection()
            insert_plain("\\")
            changed += 1
    hwp.SetPos(*anchor)
    return changed


def count_slots_in_text(text):
    r"""글자 안의 채울 자리 개수.

    본문 시작 표시(\본문\)는 채울 빈칸이 아니라 세지 않는다.
    이름표(`\학년\`)는 **역슬래시가 둘이어도 자리 하나**다 (2026-07-27) —
    그냥 세면 이름표 하나가 빈칸 2개로 잡혀 개수가 부풀려진다.
    """
    rest = (text or "").replace(BODY_ANCHOR, "")
    n = 0
    for m in form_fill.TOKEN_RE.finditer(rest):
        if m.group(1) in form_fill.RESERVED_NAMES:
            continue
        n += 1
    return n


def count_slots_in_file(path):
    r"""hwp 파일 안의 빈칸(\) 개수를 센다 (양식 등록 시 안내용).

    현재 열려 있는 문서를 건드리지 않으려고 별도 창에서 열었다 닫는다.
    """
    hwp = _h()
    saved = hwp.XHwpDocuments.Count
    try:
        hwp.XHwpDocuments.Add(1)          # 1 = 새 탭으로 열기
        hwp.open(str(path))
        text = hwp.GetTextFile("TEXT", "") or ""
        return count_slots_in_text(text)
    finally:
        try:
            if hwp.XHwpDocuments.Count > saved:
                hwp.XHwpDocuments.Active_XHwpDocument.Close(isDirty=False)
        except Exception as e:
            applog.exc("빈칸 세기용 임시 문서 닫기 실패 — 창이 남아 있을 수 있음", e)


def slot_tokens_in_file(path):
    r"""hwp 파일 안의 자리 토큰 목록 (이름 없는 자리는 ""). 양식 등록용.

    count_slots_in_file 과 같은 방식으로 별도 탭에서 열었다 닫는다.
    """
    hwp = _h()
    saved = hwp.XHwpDocuments.Count
    try:
        hwp.XHwpDocuments.Add(1)          # 1 = 새 탭으로 열기
        hwp.open(str(path))
        text = (hwp.GetTextFile("TEXT", "") or "").replace(BODY_ANCHOR, "")
        return form_fill.token_list(text)
    finally:
        try:
            if hwp.XHwpDocuments.Count > saved:
                hwp.XHwpDocuments.Active_XHwpDocument.Close(isDirty=False)
        except Exception as e:
            applog.exc("빈칸 세기용 임시 문서 닫기 실패 — 창이 남아 있을 수 있음", e)


def close_stale_temp_docs():
    r"""한글에 열린 채 남아 있는 _tmp_*.hwp 문서를 닫는다.

    구버전의 실패한 캡처가 한글에 임시 문서를 열어둔 채 남겼다(실측 2026-07-19).
    한글이 문서로 붙들고 있는 동안은 디스크에서 지울 수 없으므로, 먼저 그 문서를
    닫아 준다. 그러면 다음에 cleanup_temp_fragments 가 파일을 지울 수 있다.
    반환: 닫은 문서 수.
    """
    hwp = _h()
    closed = 0
    try:
        docs = hwp.XHwpDocuments
        # 닫으면 인덱스가 밀리므로 뒤에서 앞으로 훑는다
        for i in range(docs.Count - 1, -1, -1):
            try:
                name = docs.Item(i).FullName or ""
            except Exception:
                continue
            if "_tmp_" in name and name.lower().endswith(".hwp"):
                try:
                    docs.Item(i).Close(isDirty=False)
                    closed += 1
                except Exception as e:
                    applog.exc(f"임시 문서 닫기 실패 — {name}", e)
    except Exception as e:
        applog.exc("임시 문서 정리 중 오류", e)
    return closed


def export_as_hwpx(src_path, dst_path):
    """.hwp/.hwpx 를 HWPX 로 저장한다. 반환: 성공 여부.

    HWPX 여야 빈칸을 안전하게 채울 수 있다 — .hwp(바이너리)는 문단 레코드를
    직접 고치기 어렵지만, HWPX 는 zip+XML 이라 글자만 갈아끼울 수 있다
    (실측 2026-07-19). form_fill 모듈이 그 일을 한다.

    지금 열려 있는 문서를 건드리지 않으려고 별도 탭에서 열었다 닫는다
    (count_slots_in_file 과 같은 방식).
    """
    hwp = _h()
    saved = hwp.XHwpDocuments.Count
    try:
        hwp.XHwpDocuments.Add(1)          # 1 = 새 탭으로 열기
        hwp.open(str(src_path))
        return bool(hwp.save_as(str(dst_path), format="HWPX"))
    finally:
        try:
            if hwp.XHwpDocuments.Count > saved:
                hwp.XHwpDocuments.Active_XHwpDocument.Close(isDirty=False)
        except Exception as e:
            applog.exc("HWPX 변환용 임시 문서 닫기 실패 — 창이 남아 있을 수 있음", e)


# 고치는 동안 문서 맨 위에 붙는 안내문의 표시 (2026-07-26).
# 저장할 때 이 표시가 있는 줄들을 걷어내므로, 사용자가 절대 쓸 일 없는
# 문자열이어야 한다.
EDIT_NOTE_MARK = "※※ [고치는 중] "


def _insert_edit_note(lines):
    r"""문서 맨 위에 안내문을 넣는다. 각 줄 앞에 EDIT_NOTE_MARK 를 붙인다.

    왜 문서 안에 넣나 (사용자 결정 2026-07-26): 고치는 동안 사용자가 보는
    것은 **한글 창**이지 이 프로그램이 아니다. 무엇을 어떻게 고쳐야 하는지는
    고치는 화면 위에 같이 있어야 읽힌다.
    """
    hwp = _h()
    hwp.MoveDocBegin()
    for ln in lines:
        hwp_engine.insert_plain(EDIT_NOTE_MARK + ln)
        hwp.HAction.Run("BreakPara")
    hwp_engine.insert_plain("")          # 안내와 본문 사이 빈 줄
    hwp.HAction.Run("BreakPara")


def strip_edit_note():
    r"""안내문 줄들을 지운다 (저장 직전). 지운 줄 수를 돌려준다.

    **문단 통째로** 지운다 (2026-07-27 수정). 예전에는 `MoveSelLineEnd` 로
    "그 줄 끝까지" 지웠는데, 한글의 '줄'은 문단이 아니라 **화면에서 접힌 한
    줄**이다. 그래서 안내 문장이 길어 두 줄로 접히는 문서에서는 앞부분만
    지워지고 꼬리가 남았다 — 사용자가 본 그 `다.` 다 (안내 첫 줄
    "…여기서 고칩니**다.**" 의 끝).

    더 나빴던 것은 **저장할 때마다 새로 하나씩 생겼다**는 점이다. 손으로
    지워도 다음 저장에서 또 만들어지니 "지웠는데 계속 남는다"가 됐다.
    실측(지문박스): 지금 코드 → `다.` 2개, 고친 방식 → 새로 안 생김.

    `DeleteBack` 도 함께 뺐다 — 커서 앞 글자를 지우는 명령이라 **본문 글자를
    먹는** 경우가 있었다(실측: '진짜본문내용' → '진짜본문내').
    `MoveSelNextParaBegin` 은 다음 문단 첫머리까지 잡으므로 문단 나눔까지
    한 번에 걷힌다.
    """
    hwp = _h()
    removed = 0
    for _ in range(20):                  # 안내가 20줄을 넘을 일은 없다
        hwp.MoveDocBegin()
        if not hwp_engine.find_text(EDIT_NOTE_MARK):
            break
        try:
            hwp.HAction.Run("MoveParaBegin")        # 찾은 자리가 문단 중간일 수 있다
            hwp.HAction.Run("MoveSelNextParaBegin")  # 문단 전체 + 문단 나눔
            hwp.HAction.Run("Delete")
            removed += 1
        except Exception as e:
            applog.exc("안내문 줄 삭제 실패 — 저장물에 안내가 남을 수 있음", e)
            break
    if removed:
        _strip_note_spacer()
    return removed


def _strip_note_spacer():
    r"""안내문과 본문 사이에 넣었던 빈 줄 하나를 걷는다.

    `_insert_edit_note` 가 안내 끝에 빈 문단을 **하나** 넣는데 아무도 걷지
    않아, 고칠 때마다 문서 맨 위에 빈 줄이 한 줄씩 쌓였다 (실측 2026-07-27).
    넣은 것이 하나이므로 여기서도 **딱 하나만** 지운다 — 조각이 원래 빈 줄로
    시작했다면 그것은 그대로 남는다.
    """
    hwp = _h()
    try:
        text = hwp.GetTextFile("TEXT", "") or ""
        first = text.split("\n", 1)[0]
        if first.strip():
            return False                 # 빈 줄이 아니다 — 건드리지 않는다
        hwp.MoveDocBegin()
        hwp.HAction.Run("MoveSelNextParaBegin")
        hwp.HAction.Run("Delete")
        return True
    except Exception as e:
        applog.exc("안내문 빈 줄 정리 실패 (빈 줄이 남을 수 있음)", e)
        return False


# 어떤 문서에도 항상 들어 있는 컨트롤 — 구역 정의와 단 정의.
# 이 둘 말고 다른 컨트롤(표 tbl, 그림 gso …)이 있으면 내용이 있는 문서다.
_ALWAYS_CTRLS = {"secd", "cold"}


def doc_is_empty():
    r"""지금 문서가 비어 있는가 — 삽입이 조용히 실패했는지 판정한다.

    세 가지를 본다 (실측 2026-07-27). 하나라도 걸리면 '내용 있음'이다:
      ① 본문 글자 ② 표·그림 같은 컨트롤 ③ 문서 처음과 끝의 커서 위치 차이

    글자만 보면 안 되는 이유: 표·그림만 든 조각은 GetTextFile 이 빈 문자열을
    주므로 멀쩡한 템플릿이 '빈 문서'로 오판된다.

    커서 위치를 **절대값으로 비교하면 안 되는** 이유 (실측): 빈 새 탭에서도
    MoveDocEnd 뒤 GetPos 가 (0, 0, 0) 이 아니라 **(0, 0, 16)** 이다. 처음엔
    (0,0,0) 과 비교했는데 그러면 판정이 영영 참이 되지 않아 안전장치가 통째로
    무동작이었다. 처음과 끝을 **서로** 비교해야 한다 — 빈 문서는 두 위치가
    같고(둘 다 (0,0,16)), 표가 하나만 있어도 끝이 (0,0,24) 로 달라진다.
    """
    hwp = _h()
    try:
        hwp.MoveDocBegin()
        begin = tuple(hwp.GetPos())
        hwp.MoveDocEnd()
        if tuple(hwp.GetPos()) != begin:
            return False
        if (hwp.GetTextFile("TEXT", "") or "").strip():
            return False
    except Exception as e:
        applog.exc("빈 문서 판정: 글자 읽기 실패 — 다른 신호로 본다", e)
    try:
        ctrl = hwp.HeadCtrl
        while ctrl is not None:
            if str(ctrl.CtrlID) not in _ALWAYS_CTRLS:
                return False              # 표·그림이 있다 = 내용이 있다
            ctrl = ctrl.Next
    except Exception as e:
        applog.exc("빈 문서 판정: 컨트롤 훑기 실패 — 커서 위치로만 본다", e)
    return True


def _clear_doc():
    """지금 문서를 통째로 비운다 — 삽입 재시도 전 부분 삽입 흔적을 없앤다."""
    hwp = _h()
    try:
        hwp.HAction.Run("SelectAll")
        hwp.HAction.Run("Delete")
    except Exception as e:
        applog.exc("재시도 전 문서 비우기 실패", e)


# 템플릿 편집 탭의 '좁게' 여백 (mm) — 상하좌우 10, 머리말/꼬리말 5.
# 일반 프린터의 물리 여백이 4~5mm 라 10mm 는 인쇄 안전권이고, 한글 기본값
# (좌우 30·위 20·아래 15) 대비 판면이 좌우 40mm·상하 15mm 넓어진다.
_NARROW_MARGIN_MM = 10
_NARROW_HEADFOOT_MM = 5


def apply_narrow_page():
    r"""지금 문서의 쪽 여백을 '좁게'로 바꾼다. 성공 여부.

    왜 (사용자 결정 2026-07-27): 템플릿 편집 탭은 빈 새 탭이라 한글 기본
    여백(좌우 30mm)으로 열리는데, 넓은 표 템플릿은 그 판면에 안 들어가
    **다음 쪽으로 넘어가** 보였다. 여백을 좁히면 한 쪽에 들어온다.

    **템플릿 편집 탭에만** 쓴다. 양식은 여백까지가 양식의 내용이라 건드리면
    저장할 때 양식 자체가 바뀐다 (open_form_copy 는 부르지 않는다).
    저장물에는 안 샌다 — 삽입은 keep_section=0 이라 쪽 정의를 버린다.

    실패해도 편집은 계속돼야 하므로 예외를 삼키고 False 만 돌려준다.
    """
    hwp = _h()
    try:
        act, ps = hwp.HAction, hwp.HParameterSet
        act.GetDefault("PageSetup", ps.HSecDef.HSet)
        pd = ps.HSecDef.PageDef
        pd.LeftMargin = pd.RightMargin = hwp.MiliToHwpUnit(_NARROW_MARGIN_MM)
        pd.TopMargin = pd.BottomMargin = hwp.MiliToHwpUnit(_NARROW_MARGIN_MM)
        pd.HeaderLen = pd.FooterLen = hwp.MiliToHwpUnit(_NARROW_HEADFOOT_MM)
        ps.HSecDef.HSet.SetItem("ApplyTo", 3)      # 3 = 문서 전체
        return bool(act.Execute("PageSetup", ps.HSecDef.HSet))
    except Exception as e:
        applog.exc("쪽 여백 좁히기 실패 — 기본 여백으로 계속", e)
        return False


class EditSession:
    r"""'내용 고치기'로 펼쳐 준 문서 한 벌 — 그 문서를 **정확히** 다시 찾기 위한 것.

    왜 필요한가 (2026-07-27, 사용자 지적 "수정하고 나면 빈 한글 창이 남는다"):
    여태 저장할 때 `Active_XHwpDocument`(지금 활성 문서)를 저장하고 닫았다.
    그런데 저장 과정에서 임시 탭이 몇 번 열리고 닫히므로 '활성'이 어느 것인지
    가정에 기대야 했고, 사용자가 편집 중 다른 탭으로 갈아타면 **엉뚱한 문서를
    저장하거나 닫을** 수 있었다. 펼칠 때 받은 문서 객체를 그대로 들고 있다가
    그것만 활성화하고 그것만 닫으면 그 가정이 통째로 사라진다.

    temp_path: 양식 편집이 만든 사본(편집중_*.hwp). 저장이 끝나면 지운다.
    own_tab: 이 탭을 **우리가 열었는가**. open 이 새 탭을 쓰지 않고 기존
        문서를 갈아치운 경우(open_form_copy 의 예비 경로) False 로 온다 —
        그 문서는 활성화·저장은 해도 되지만 **닫으면 안 된다** (사용자
        탭일 수 있다).
    """

    def __init__(self, doc, temp_path=None, own_tab=True):
        self.doc = doc
        self.temp_path = temp_path
        self.own_tab = own_tab

    def activate(self):
        """저장·닫기 전에 이 문서를 활성으로 되돌린다. 성공 여부."""
        if self.doc is None:
            # 문서 객체를 아예 못 받은 세션 — 재시도해도 같은 결과다.
            # 호출부(overwrite_content)가 이 경우를 따로 안내한다.
            applog.warn("고치던 문서 객체가 없습니다 — 활성화할 대상이 없다")
            return False
        try:
            self.doc.SetActive_XHwpDocument()
            return True
        except Exception as e:
            applog.exc("고치던 문서를 활성화하지 못했습니다", e)
            return False

    def close(self):
        """이 문서만 저장 없이 닫는다. 성공 여부.

        사용자가 이미 손으로 닫았을 수 있으므로 실패를 오류로 보지 않는다.
        우리가 연 탭이 아니면(own_tab=False) **닫지 않는다** — open 이 기존
        문서를 갈아치운 경우라, 닫으면 사용자 탭이 사라질 수 있다.
        """
        if not self.own_tab:
            applog.warn("우리가 연 탭이 아니라 닫지 않습니다 — 사용자가 직접 닫는다")
            return False
        try:
            self.doc.Close(isDirty=False)
            return True
        except Exception as e:
            applog.exc("고치던 탭 닫기 실패 — 사용자가 직접 닫아야 한다", e)
            return False

    def cleanup(self):
        """이 세션이 만든 사본 파일**만** 지운다 (저장이 끝난 뒤).

        사본 이름은 세션마다 다르므로(open_form_copy 의 uuid 접두)
        다른 세션이 아직 고치는 파일을 건드릴 일이 없다.
        """
        if not self.temp_path:
            return
        try:
            pathlib.Path(self.temp_path).unlink(missing_ok=True)
        except Exception as e:
            applog.exc(f"양식 사본 삭제 실패 (남아도 무해) — {self.temp_path}", e)


def open_template_copy(path, note_lines=None):
    r"""템플릿 조각을 **새 탭**에 펼친다 — '꺼내서 고치기'용 (2026-07-25).

    파일을 직접 열지 않고 빈 새 탭에 insert 한다. 직접 열면 한글이 조각
    파일을 잠가(한 번 연 파일은 안 놓는다 — WinError 32 계보) 덮어쓰기
    저장이 막히기 때문이다. 새 탭은 제목 없는 문서라 원본과 무관하다.

    note_lines 를 주면 문서 맨 위에 안내문을 붙인다 (저장할 때 자동으로 빠진다).

    **삽입 결과를 반드시 확인한다** (2026-07-27, 사용자 지적 "가끔 내용이
    출력되지 않는다"): pyhwpx 의 insert_file 은 실패해도 예외를 던지지 않고
    False 만 돌려준다(HAction.Execute 그대로). 여태 그 값을 안 봐서, 조각
    파일이 사라졌거나 잠겨 있으면 **빈 탭에 안내문만** 붙은 채로 "고치세요"가
    떴다. 그 상태에서 [덮어쓰기]를 누르면 원본이 빈 내용으로 바뀐다 —
    표시 버그가 데이터 손실이 되는 길이라 여기서 끊는다.
    """
    hwp = _h()
    src = pathlib.Path(path)
    if not src.is_file():
        # 지워진 조각을 가리키는 스테일 경로 — 예외로 바꿔야 오류창이 뜬다
        raise FileNotFoundError(f"조각 파일이 없습니다 — {src}")

    doc = hwp.XHwpDocuments.Add(1)          # 1 = 새 탭
    try:
        doc.SetActive_XHwpDocument()
    except Exception as e:
        applog.exc("새 탭 활성화 실패 — 활성 문서 그대로 진행", e)
    # 삽입 **전에** 여백을 좁힌다 — 레이아웃 계산이 한 번으로 끝난다
    apply_narrow_page()

    ok = False
    for attempt in (1, 2):
        try:
            ok = bool(hwp.insert_file(str(src), keep_section=0,
                                      keep_charshape=1, keep_parashape=1,
                                      keep_style=1))
        except Exception as e:
            applog.exc(f"조각 삽입 중 오류 (시도 {attempt})", e)
            ok = False
        if ok and not doc_is_empty():
            break
        applog.warn(f"조각 삽입이 비었습니다 (시도 {attempt}, insert_file={ok}) "
                    f"— {src.name}")
        if attempt == 1:
            _clear_doc()                    # 부분 삽입이 겹치지 않게 비우고
            time.sleep(0.3)                 # 한글이 바빴던 경우를 위해 잠깐
    else:
        # 두 번 다 실패 — 빈 탭을 치우고 호출부에 알린다 (오류창은 그쪽이 띄운다)
        try:
            doc.Close(isDirty=False)
        except Exception as e:
            applog.exc("실패한 빈 탭 닫기 실패", e)
        raise RuntimeError(f"조각을 펼치지 못했습니다 — {src.name}\n"
                           "파일이 잠겨 있거나 한글이 응답하지 않았습니다.")

    # 안내문은 **삽입이 확인된 뒤에만** 붙인다 — 빈 탭에 "고치세요"가 뜨면
    # 사용자가 그대로 덮어써 원본을 잃는다.
    if note_lines:
        _insert_edit_note(note_lines)
        hwp.MoveDocBegin()
    return EditSession(doc)


def open_form_copy(path, note_lines=None):
    r"""양식 파일의 **사본**을 열어 고치게 한다 (2026-07-27).

    템플릿처럼 새 탭에 insert 하지 않는 이유: 양식은 용지·여백·머리말까지가
    내용이라 insert 로는 그것들이 안 따라온다. 그렇다고 원본을 직접 열면 한글이
    그 파일을 붙들어(WinError 32 계보) 덮어쓰기가 막힌다. 그래서 사본을 연다 —
    저장은 어차피 새 이름의 조각 파일로 하므로 원본 잠금과 무관하다.

    **새 탭에 연다** (2026-07-27, 사용자 지적 "빈 한글 창이 남는다"):
    여태 `hwp.FileNew()` 를 썼는데, 이 이름과 달리 pyhwpx 의 FileNew 는 새
    탭이 아니라 **새 문서 창**을 여는 명령이다(pyhwpx 문서에 명시. 새 탭은
    FileNewTab 이 따로 있다). 그래서 편집이 끝나 문서를 닫아도 그 창은 남아
    빈 한글 창이 됐다. `XHwpDocuments.Add(1)` 은 이 코드베이스가 이미 여러
    곳에서 쓰는 검증된 '새 탭' 방법이다.
    """
    hwp = _h()
    src = pathlib.Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"양식 파일이 없습니다 — {src}")
    work = paths.data_dir() / "양식작업"
    work.mkdir(parents=True, exist_ok=True)
    # 세션마다 다른 이름을 쓴다 (2026-07-31) — `편집중_{원본이름}` 처럼
    # 정해진 이름이면 같은 양식을 두 번 열었을 때 사본이 서로 덮어쓰고,
    # 한쪽 cleanup() 이 다른 쪽이 아직 고치는 파일을 지운다.
    copy_path = work / f"편집중_{uuid.uuid4().hex[:8]}_{src.name}"
    shutil.copy2(str(src), str(copy_path))

    before = hwp.XHwpDocuments.Count
    doc = hwp.XHwpDocuments.Add(1)          # 1 = 새 탭 (FileNew 는 새 '창'이다)
    try:
        doc.SetActive_XHwpDocument()
    except Exception as e:
        applog.exc("새 탭 활성화 실패 — 활성 문서 그대로 진행", e)
    hwp.open(str(copy_path))
    if note_lines:
        _insert_edit_note(note_lines)
        hwp.MoveDocBegin()
    if hwp.XHwpDocuments.Count <= before:
        # open 이 새 탭을 쓰지 않고 기존 문서를 갈아치웠다는 뜻 — 이 경우
        # 우리가 들고 있는 doc 이 사용자 문서일 수 있어 닫으면 안 된다.
        # 다만 **지금 활성 문서는 방금 open 한 사본**이므로 그것을 세션에
        # 담아 둔다 (2026-07-31 안전 수리): doc=None 으로 두면 저장 전의
        # activate() 가 항상 실패해 이 세션은 영영 저장할 수 없게 된다.
        # own_tab=False 라 닫기(close)만 건너뛴다.
        applog.warn("양식 편집: 새 탭이 늘지 않았습니다 — 탭 자동 닫기를 건너뜁니다")
        try:
            fallback_doc = hwp.XHwpDocuments.Active_XHwpDocument
        except Exception as e:
            applog.exc("양식 편집: 활성 문서 읽기 실패 — 이 세션은 저장할 수 없다", e)
            fallback_doc = None
        return EditSession(fallback_doc, temp_path=copy_path, own_tab=False)
    # open 뒤에는 활성 문서를 다시 받아 둔다 — 탭을 여는 것과 파일을 여는 것이
    # 별개라, Add 가 준 객체가 그대로 그 문서를 가리킨다고 단정할 수 없다.
    try:
        doc = hwp.XHwpDocuments.Active_XHwpDocument
    except Exception as e:
        applog.exc("양식 편집: 활성 문서 다시 읽기 실패 — Add 가 준 객체를 쓴다", e)

    return EditSession(doc, temp_path=copy_path)


def hide_window_if_ours(windows_before):
    r"""고치려고 **우리가 띄우거나 켠** 한글 창이면 되돌린다. 반환: 숨겼는가.

    windows_before: 고치기에 손대기 **전에** 잰 보이는 창 핸들 집합
    (`hwp_engine.visible_window_handles()`).

    핸들로 가리는 이유 (실측 2026-07-27): "연결된 창이 보이는가"로 판단하면
    한글이 아예 없던 경우를 놓친다 — connect() 가 한글을 새로 띄우고 그 창은
    처음부터 보이는 상태라 '원래 있던 창'으로 오인된다. 고치기 전 핸들 목록에
    없던 창이면 우리가 만든 것이다.
    """
    hwnd = hwp_engine.connected_hwnd()
    if hwnd is not None and hwnd in (windows_before or set()):
        return False                # 고치기 전부터 보이던 창 — 사용자 것이다
    return hide_window_if_idle()


def hide_window_if_idle():
    r"""한글에 **빈 무제 문서 하나만** 남았으면 창을 숨긴다. 반환: 숨겼는가.

    왜 필요한가 (실측 2026-07-27, 사용자 지적 "수정하고 닫은 다음에 빈 문서
    하나가 여전히 남아 있다"): 고치기가 끝나면 편집 탭은 닫히지만, 그 한글
    인스턴스의 **바탕 문서**가 남는다. 한글은 문서를 0개로 만들 수 없어서
    마지막 문서는 닫아도 안 없어진다(실측: Close 해도 Count 가 1 그대로).
    남는 길은 **창을 숨기는 것**뿐이다 (실측: Visible=False → 창 0개).

    그 바탕 문서는 대개 우리가 붙은 숨은 COM 인스턴스의 것이라, 편집하려고
    `ensure_visible` 로 켠 창이 그대로 남아 "안 띄운 빈 한글"로 보인다.
    그래서 **우리가 켠 창일 때만** 되돌린다 — 호출부가 고치기 전에
    `hwp_engine.window_is_visible()` 로 재 두고 판단한다.

    사용자 문서가 하나라도 있으면(파일이 열려 있거나 내용이 있으면) 절대
    숨기지 않는다 — 쓰던 창이 사라지는 것이 빈 창이 남는 것보다 나쁘다.
    """
    hwp = _h()
    try:
        docs = hwp.XHwpDocuments
        if docs.Count != 1:
            return False                # 다른 문서가 열려 있다 — 사용자 것이다
        doc = docs.Item(0)
        if (doc.FullName or ""):
            return False                # 파일이 열려 있다
        doc.SetActive_XHwpDocument()
        if not doc_is_empty():
            return False                # 내용이 있다
    except Exception as e:
        applog.exc("한글 창 숨김 판단 실패 — 창을 그대로 둔다", e)
        return False
    return hwp_engine.set_window_visible(False)


def finish_edit_session(session, item_id):
    r"""고치기를 마무리한다 — 미리보기를 뽑고 편집 탭을 닫는다. 반환: (미리보기 성공, 탭 닫음).

    **편집 탭을 그대로 재활용한다** (2026-07-27, 사용자 지적 "창이 여러 개
    닫히는 듯한 모션"). 예전에는 저장 한 번에 한글 탭이 다섯 번 열리고 닫혔다 —
    캡처용 임시 탭, 미리보기용 임시 탭, 그리고 편집 탭. 그런데 편집 탭은 이미
    저장할 내용 그대로이고 미리보기도 여기서 뽑을 수 있다. 이제 눈에 보이는
    움직임은 **'고치던 탭 하나가 닫히는 것'** 뿐이다.

    순서가 중요하다: 탭을 **닫은 뒤에** 임시 파일을 읽는다. 한글이 붙들고
    있는 동안은 지울 수도 없고(WinError 32 계보), PrvImage 는 olefile 로
    읽으므로 한글이 필요 없다.
    """
    hwp = _h()
    work = paths.data_dir() / "미리보기작업"
    tmp = None
    try:
        work.mkdir(parents=True, exist_ok=True)
        tmp = work / f"_prv_{item_id}.hwp"
        strip_marks()                   # 자리표시(\)가 찍힌 그림은 지저분하다
        hwp.save_as(str(tmp), format="HWP")
    except Exception as e:
        applog.exc(f"미리보기 뽑기 실패 (무해) — {item_id}", e)
        tmp = None

    closed = session.close() if session.doc is not None else False
    session.cleanup()

    ok = False
    if tmp is not None:
        try:
            ok = preview.save_cache(item_id, tmp)
        except Exception as e:
            applog.exc(f"미리보기 캐시 저장 실패 (무해) — {item_id}", e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass                        # 남아도 무해하다
    return ok, closed


def build_clean_preview(src_path, item_id):
    r"""자리표시를 걷어낸 모습으로 미리보기 그림을 만든다 (한글 필요).

    hwp 안의 PrvImage 는 저장 당시 화면이라 빈칸 `\` 가 그대로 찍혀 있다.
    그런데 그 표시는 인쇄물에 안 나오는 것이라(변환 때 내용으로 바뀌거나
    strip_marks 로 걷힌다) 미리보기에 보이면 물감이 지저분해 보인다.
    사본을 열어 표시를 지우고 **한 번 저장**하면 한글이 그 시점 화면으로
    PrvImage 를 새로 넣어 준다 — 그 그림만 png 로 빼내 캐시에 둔다.

    **물감을 저장하는 순간에 부른다** (사용자 결정 2026-07-27) — 나중에
    '다듬기' 버튼을 눌러 몰아서 하는 방식은 손이 한 번 더 가고, 안 누르면
    지저분한 그림이 계속 보인다. 저장 시점에는 한글이 이미 연결돼 있어
    비용도 작다. 원본 조각은 건드리지 않고, 실패해도 등록 자체는 성공이다.
    """
    hwp = _h()
    work = paths.data_dir() / "미리보기작업"
    work.mkdir(parents=True, exist_ok=True)
    tmp = work / f"_prv_{item_id}.hwp"
    saved = hwp.XHwpDocuments.Count
    try:
        hwp.XHwpDocuments.Add(1)          # 1 = 새 탭
        hwp.insert_file(str(src_path), keep_section=0, keep_charshape=1,
                        keep_parashape=1, keep_style=1)
        strip_marks()
        hwp.save_as(str(tmp), format="HWP")
    except Exception as e:
        applog.exc(f"미리보기 다듬기 실패 — {src_path}", e)
        return False
    finally:
        try:
            if hwp.XHwpDocuments.Count > saved:
                hwp.XHwpDocuments.Active_XHwpDocument.Close(isDirty=False)
        except Exception as e:
            applog.exc("미리보기용 임시 탭 닫기 실패", e)
    ok = preview.save_cache(item_id, tmp)
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass                             # 남아도 무해하다
    return ok


def insert_template_filled(path, fills, slot_count=None):
    r"""템플릿 조각을 커서 자리에 꽂고, 자리를 fills 로 순서대로 채운다.

    이름 있는 템플릿의 '채우기 표'(form_table_ui)가 쓴다. 채우는 일 자체는
    마크다운 변환과 **같은 fill_slots** 라서 결과가 어긋날 수 없다.
    반환: (채운 수, 채우려던 수).
    """
    hwp = _h()
    anchor = hwp.GetPos()
    end_para = measure_insert_span(anchor, lambda: insert_fragment(path))
    filled, want = fill_slots(anchor, fills, end_para=end_para,
                              slot_count=slot_count)
    strip_slot_markers(anchor, end_para,
                       max_delete=None if slot_count is None
                       else max(int(slot_count) - filled, 0))
    return filled, want


def normalize_marks_to_pairs():
    r"""지금 문서의 홑 자리 표시(\)를 쌍(\\)으로 정리한다. 반환: 정리한 개수.

    2026-07-27 문법 확정 — 자리는 \ 로 열고 \ 로 닫는다. 옛 습관대로 홑 \ 를
    쳐도 저장할 때 여기서 고쳐 준다.

    순서가 생명이다: 이름표(\학년\)와 기존 쌍(\\)을 먼저 다른 글자로 피신시킨
    뒤 홑 \ 를 불리고, 마지막에 되돌린다 — 안 그러면 \학년\ 이 \\학년\\ 으로
    불어난다. 피신 글자(⟪…⟫)는 시험지에 나올 수 없는 조합이다.

    **단계마다 결과를 확인한다** (2026-07-31 안전 수리): replace_all 은
    실패해도 예외 없이 False 만 준다. 중간 단계가 실패한 채 계속 가면 피신
    글자(⟪이름:…⟫)가 문서에 남아 **그대로 저장물이 된다.** 실패하면 이미
    피신시킨 것만 제자리로 되돌리고 예외로 멈춘다 — 저장 쪽(save_active_as)이
    이 예외를 보고 저장을 포기한다.
    """
    hwp = _h()
    try:
        text = hwp.GetTextFile("TEXT", "") or ""
    except Exception:
        return 0
    singles = sum(1 for m in form_fill.TOKEN_RE.finditer(text)
                  if m.group(1) is None and len(m.group(0)) == 1)
    if not singles:
        return 0
    # \본문\ 포함 — 보호하지 않으면 그 안의 \ 두 개가 홑으로 취급돼 불어난다
    names = []
    for m in form_fill.TOKEN_RE.finditer(text):
        if m.group(1) and m.group(1) not in names:
            names.append(m.group(1))
    # 이름 없는 쌍(\\)이 문서에 있을 때만 피신시킨다 — 없는 글자를 찾는
    # 바꾸기는 '실패'와 구분할 수 없어, 있는 것만 만져야 결과 확인이 선다
    pairs = sum(1 for m in form_fill.TOKEN_RE.finditer(text)
                if m.group(1) is None and len(m.group(0)) == 2)
    PAIR = "⟪자리쌍⟫"
    pending = []                        # 아직 안 되돌린 피신 — (찾기, 원래대로)

    def _undo_pending():
        """피신 글자를 제자리로 되돌린다. 반환: 전부 되돌렸는가."""
        ok = True
        for find, repl in reversed(pending):
            if not hwp_engine.replace_all(find, repl):
                applog.warn(f"자리 표시 되돌리기 실패 — {find!r} → {repl!r}")
                ok = False
        return ok

    def _must(find, repl):
        """바꾸기 한 단계 — 실패하면 피신을 되돌리고 예외로 멈춘다."""
        if hwp_engine.replace_all(find, repl):
            return
        if _undo_pending():
            raise RuntimeError(
                "자리 표시 정리에 실패해 문서를 정리 전으로 되돌렸습니다 — "
                "한글이 바쁘거나 응답하지 않았을 수 있습니다.\n"
                "잠시 뒤 다시 시도해 주세요.")
        raise RuntimeError(
            "자리 표시 정리에 실패했고 임시 표시(⟪…⟫)가 문서에 남았을 수 "
            "있습니다.\n한글에서 ⟪ 글자를 찾아 지운 뒤 다시 시도해 주세요.")

    for n in names:
        _must(f"\\{n}\\", f"⟪이름:{n}⟫")
        pending.append((f"⟪이름:{n}⟫", f"\\{n}\\"))
    if pairs:
        _must("\\\\", PAIR)
        pending.append((PAIR, "\\\\"))
    _must("\\", "\\\\")
    if pairs:
        _must(PAIR, "\\\\")
        pending.remove((PAIR, "\\\\"))
    for n in names:
        _must(f"⟪이름:{n}⟫", f"\\{n}\\")
        pending.remove((f"⟪이름:{n}⟫", f"\\{n}\\"))
    # 검산 — 홑 \ 가 남아 있으면 기록만 남긴다 (읽기는 옛 문법도 되므로 무해)
    try:
        after = hwp.GetTextFile("TEXT", "") or ""
        left = sum(1 for m in form_fill.TOKEN_RE.finditer(after)
                   if m.group(1) is None and len(m.group(0)) == 1)
        if left:
            applog.warn(f"자리 표시 정리 뒤에도 홑 \\ {left}개 남음 (읽기는 됨)")
    except Exception:
        pass
    return singles


def apply_exam_page(gap_mm=8.0, top_mm=20.0):
    r"""문서를 시험지 판형(2단)으로 바꾼다. 성공 여부.

    왜 단 폭에 맞춰 여백을 정하나: 문항 조판이 쓰는 표(자료 상자·보기 상자·선지
    표)는 전부 활성 스펙의 `column_width_mm` 로 만들어진다. 단 폭이 그보다 좁으면
    표가 단을 넘어가 깨진다 — 그래서 여백을 '단 폭 두 개 + 사이 간격'에 맞춘다.

    A4 기준: 단 폭 94mm 면 좌우 여백은 (210 - 94*2 - 8) / 2 = 7mm 가 된다.
    """
    hwp = _h()
    col = hwp_engine._col_width_mm()
    try:
        act, ps = hwp.HAction, hwp.HParameterSet
        act.GetDefault("PageSetup", ps.HSecDef.HSet)
        pd = ps.HSecDef.PageDef
        paper_mm = hwp.HwpUnitToMili(pd.PaperWidth) or 210.0
        side = max((paper_mm - (col * 2 + gap_mm)) / 2.0, 5.0)
        pd.LeftMargin = pd.RightMargin = hwp.MiliToHwpUnit(round(side, 1))
        pd.TopMargin = pd.BottomMargin = hwp.MiliToHwpUnit(top_mm)
        pd.HeaderLen = pd.FooterLen = hwp.MiliToHwpUnit(_NARROW_HEADFOOT_MM)
        ps.HSecDef.HSet.SetItem("ApplyTo", 3)          # 3 = 문서 전체
        if not act.Execute("PageSetup", ps.HSecDef.HSet):
            return False

        # 필드 이름은 HColDef 실물에서 확인한 것이다(Count·SameSize·SameGap·Layout).
        # ApplyClass 24 / ApplyTo 6 이라야 Execute 가 성공한다 — 다른 조합(832/3 등)은
        # 조용히 False 만 돌려주고 단이 안 바뀐다(2026-08-03 실측).
        act.GetDefault("MultiColumn", ps.HColDef.HSet)
        ps.HColDef.Count = 2
        ps.HColDef.SameSize = 1
        ps.HColDef.SameGap = hwp.MiliToHwpUnit(gap_mm)
        ps.HColDef.Layout = hwp.ColLayoutType("Left")
        ps.HColDef.HSet.SetItem("ApplyClass", 24)
        ps.HColDef.HSet.SetItem("ApplyTo", 6)
        return bool(act.Execute("MultiColumn", ps.HColDef.HSet))
    except Exception as e:
        applog.exc("시험지 판형(2단) 적용 실패 — 기본 판형으로 계속", e)
        return False


def save_active_as(dest_path):
    """지금 한글에 떠 있는 문서를 통째로 저장한다. 반환: 본문 글자(미리보기용).

    빈 문서는 저장하지 않는다 (2026-07-27) — 삽입이 조용히 실패한 빈 탭을
    그대로 덮어쓰면 원본 양식이 사라진다. open_template_copy 의 검증과 같은
    이유이고, 이쪽은 마지막 방어선이다.
    """
    hwp = _h()
    if doc_is_empty():
        raise RuntimeError("문서가 비어 있어 저장하지 않았습니다 "
                           "(원본을 지우지 않기 위해 멈춥니다).")
    # 정리가 실패하면 저장하지 않는다 (2026-07-31 안전 수리) — 예전에는
    # 여기서 예외를 삼키고 저장을 계속해서, 피신 글자(⟪이름:…⟫)가 문서에
    # 남은 채 **성공한 것처럼** 저장물이 됐다. 예외를 그대로 올리면 호출부
    # (overwrite_content)가 오류창을 띄우고 편집 상태를 유지한다.
    normalize_marks_to_pairs()          # 양식 '내용 고치기' 저장도 새 문법으로
    hwp.save_as(str(dest_path), format="HWP")
    try:
        return hwp.GetTextFile("TEXT", "") or ""
    except Exception as e:
        applog.exc("저장한 문서의 글자 읽기 실패 — 미리보기 없이 저장", e)
        return ""


def select_all():
    """지금 문서 전체 선택 — '꺼내서 고치기'의 덮어쓰기 캡처가 쓴다."""
    _h().HAction.Run("SelectAll")


def close_active_doc():
    r"""지금 문서를 저장하지 않고 닫는다 — 고치기가 끝난 탭 정리용.

    '다 됐으니 닫으셔도 됩니다' 라고 안내만 하던 것을 프로그램이 직접 한다
    (사용자 결정 2026-07-26). 저장은 이미 조각 파일로 끝났으므로 이 탭에는
    남길 것이 없다. 문서가 하나뿐이면 닫지 않는다 — 한글까지 비어 버린다.
    """
    hwp = _h()
    try:
        if hwp.XHwpDocuments.Count <= 1:
            return False
        hwp.XHwpDocuments.Active_XHwpDocument.Close(isDirty=False)
        return True
    except Exception as e:
        applog.exc("고치던 탭 닫기 실패 — 사용자가 직접 닫아야 한다", e)
        return False


def read_file_structure(path):
    r"""파일을 새 탭에서 읽어 (HWPML2X XML, 순수 텍스트) 를 돌려준다.

    양식→AI 프롬프트 변환용. XML 은 표 구조까지 담고 있어 마크다운 표로
    풀 수 있다. XML 추출이 실패하면 (None, 텍스트) — 호출부가 표 없이
    텍스트로만 만든다. 현재 문서는 건드리지 않는다 (count_slots_in_file 방식).
    """
    hwp = _h()
    saved = hwp.XHwpDocuments.Count
    xml = text = ""
    try:
        hwp.XHwpDocuments.Add(1)
        hwp.open(str(path))
        try:
            xml = hwp.GetTextFile("HWPML2X", "") or ""
        except Exception as e:
            applog.exc("HWPML2X 추출 실패 — 표 없이 텍스트로만 변환", e)
        text = hwp.GetTextFile("TEXT", "") or ""
    finally:
        try:
            if hwp.XHwpDocuments.Count > saved:
                hwp.XHwpDocuments.Active_XHwpDocument.Close(isDirty=False)
        except Exception as e:
            applog.exc("구조 읽기용 임시 문서 닫기 실패 — 창이 남아 있을 수 있음", e)
    return (xml or None), text


def strip_marks(limit=500):
    r"""문서에 남은 자리표시를 지운다 — \본문\ 먼저, 그다음 빈칸 \.

    양식 파일 안의 역슬래시는 **글이 아니라 표시**다 (여기가 본문 자리다,
    여기에 내용이 들어간다). 그런데 양식을 그냥 열어 쓸 때는 그 표시가 그대로
    인쇄물에 남았다 (사용자 지적 2026-07-26).
    \본문\ 을 먼저 지우는 이유: 그 안에도 역슬래시가 둘 있어, 빈칸부터 지우면
    '본문' 이라는 글자만 남는다. **이름표(`\학년\`)도 같은 이유로 먼저 지운다**
    (2026-07-27) — 홑 역슬래시부터 지우면 '학년' 이라는 글자가 문서에 남는다.
    반환: 지운 개수.
    """
    hwp = _h()
    removed = 0
    # 문서에 실제로 있는 이름표를 먼저 뽑는다 (없는 것을 찾느라 헤매지 않게)
    names = []
    try:
        text = hwp.GetTextFile("TEXT", "") or ""
        seen = set()
        for m in form_fill.TOKEN_RE.finditer(text.replace(BODY_ANCHOR, "")):
            tok = m.group(0)
            if m.group(1) and tok not in seen:
                seen.add(tok)
                names.append(tok)
    except Exception as e:
        applog.exc("이름표 목록 뽑기 실패 — 홑 역슬래시만 걷어냅니다", e)
    for target in (BODY_ANCHOR, *names, "\\\\", "\\"):
        hwp.MoveDocBegin()
        while removed < limit and find_text(target):
            delete_selection()
            removed += 1
    hwp.MoveDocBegin()
    return removed


def open_form(path, strip_markers=False):
    r"""양식 파일을 새 문서로 연다 (용지·여백·머리말까지 원본 그대로).

    템플릿(insert_fragment)은 문서 '일부'를 커서 위치에 꽂는 것이라 페이지 설정이
    안 따라온다. 표지·통신문처럼 "이 양식으로 새로 시작"하려면 파일 전체를 열어야
    한다. 실측(2026-07-16): 여백 45/40 보존, 창 최대화도 유지됨.

    strip_markers=True 면 열자마자 자리표시(\본문\·빈칸 \)를 걷어낸다.
    **그냥 열어서 손으로 쓰는 경우**가 그렇다 — 표시가 남으면 그대로 인쇄된다.
    변환(\양식라벨\)으로 여는 경우에는 그 표시를 보고 내용을 채우므로 지우면 안 된다.
    """
    hwp = _h()
    # 독립 CLI/미리보기 인스턴스는 이미 빈 문서를 하나 갖고 있다. 거기서 또
    # FileNew를 부르면 별도 한글 창이 생겨 자동화가 끝난 뒤 빈 창이 남고,
    # 저장 대상도 어느 창인지 흔들린다. 현재 문서에 내용이 있을 때만 새 문서를
    # 열어 사용자의 작업을 보존하고, 빈 문서면 그 자리에서 바로 양식을 연다.
    if not doc_is_empty():
        hwp.FileNew()
    hwp.open(str(path))
    if strip_markers:
        try:
            n = strip_marks()
            if n:
                applog.info(f"양식을 열며 자리표시 {n}개를 걷어냈습니다")
        except Exception as e:
            applog.exc("자리표시 걷어내기 실패 — 문서에 \\ 가 남습니다", e)


# ── 팔레트: 기능 블럭 실행 (여러 기능 병렬) ─────────────
_TOGGLE_ACTION = {
    "굵게": "CharShapeBold",
    "기울임": "CharShapeItalic",
    "밑줄": "CharShapeUnderline",
}
_PARA_ACTION = {
    "가운데정렬": "ParagraphShapeAlignCenter",
    "왼쪽정렬": "ParagraphShapeAlignLeft",
    "양쪽정렬": "ParagraphShapeAlignJustify",
}


def execute_function_block(actions):
    """기능 블럭 실행 — actions: [{"func":이름, "value":값}, ...] 를 병렬 적용.

    값 있는 글자서식(글씨체·크기·자간·색)은 한 번의 CharShape로 묶어 적용하고,
    토글(굵게 등)과 문단정렬은 개별 Run, 줄간격은 ParagraphShape로 적용한다.
    선택 영역이 있어야 의미가 있다(호출부에서 보장).
    """
    hwp = _h()
    act = hwp.HAction
    ps = hwp.HParameterSet

    char_fields = {}   # CharShape에 묶어 넣을 값들
    para_fields = {}   # ParagraphShape에 묶어 넣을 값들
    toggles = []       # Run 액션들
    para_aligns = []

    for a in actions:
        func = a.get("func")
        val = a.get("value")
        if func in _TOGGLE_ACTION:
            toggles.append(_TOGGLE_ACTION[func])
        elif func in _PARA_ACTION:
            para_aligns.append(_PARA_ACTION[func])
        elif func == "글씨체":
            char_fields["face"] = val
        elif func == "글씨크기":
            char_fields["height"] = float(val)
        elif func == "자간":
            char_fields["spacing"] = int(val)
        elif func == "글자색":
            char_fields["color"] = val
        elif func == "줄간격":
            para_fields["line_spacing"] = int(val)
        # 들여쓰기/내어쓰기는 같은 필드(Indentation)의 부호 차이 (실측 2026-07-15)
        #   양수 = 첫 줄을 안으로, 음수 = 첫 줄을 밖으로. 단위는 pt(=100 HwpUnit).
        elif func == "들여쓰기":
            para_fields["indent_pt"] = abs(float(val))
        elif func == "내어쓰기":
            para_fields["indent_pt"] = -abs(float(val))
        elif func == "왼쪽여백":
            para_fields["left_mm"] = float(val)
        elif func == "오른쪽여백":
            para_fields["right_mm"] = float(val)
        # 한글 줄나눔 단위 (실측 2026-07-16): BreakNonLatinWord
        #   1 = 글자 단위(기본, 단어 중간에서 잘림) / 0 = 어절 단위
        elif func == "어절단위 줄바꿈":
            para_fields["break_nonlatin"] = 0
        elif func == "자간 자동조절":
            para_fields["condense"] = int(val)

    # 1) 값 있는 글자서식 묶어서 한 번에
    if char_fields:
        act.GetDefault("CharShape", ps.HCharShape.HSet)
        if "face" in char_fields:
            ps.HCharShape.FaceNameHangul = char_fields["face"]
            ps.HCharShape.FaceNameLatin = char_fields["face"]
        if "height" in char_fields:
            ps.HCharShape.Height = hwp.PointToHwpUnit(char_fields["height"])
        if "spacing" in char_fields:
            ps.HCharShape.SpacingHangul = char_fields["spacing"]
            ps.HCharShape.SpacingLatin = char_fields["spacing"]
        if "color" in char_fields:
            ps.HCharShape.TextColor = char_fields["color"]
        act.Execute("CharShape", ps.HCharShape.HSet)

    # 2) 토글 기능
    for action_id in toggles:
        act.Run(action_id)

    # 3) 문단 정렬
    for action_id in para_aligns:
        act.Run(action_id)

    # 4) 값 있는 문단서식(줄간격·들여쓰기/내어쓰기·좌우여백) 묶어서 한 번에
    if para_fields:
        act.GetDefault("ParagraphShape", ps.HParaShape.HSet)
        if "line_spacing" in para_fields:
            ps.HParaShape.LineSpacing = para_fields["line_spacing"]
            ps.HParaShape.LineSpacingType = 0
        if "indent_pt" in para_fields:
            ps.HParaShape.Indentation = hwp.PointToHwpUnit(para_fields["indent_pt"])
        if "left_mm" in para_fields:
            ps.HParaShape.LeftMargin = hwp.MiliToHwpUnit(para_fields["left_mm"])
        if "right_mm" in para_fields:
            ps.HParaShape.RightMargin = hwp.MiliToHwpUnit(para_fields["right_mm"])
        if "break_nonlatin" in para_fields:
            ps.HParaShape.BreakNonLatinWord = para_fields["break_nonlatin"]
        if "condense" in para_fields:
            ps.HParaShape.Condense = para_fields["condense"]
        act.Execute("ParagraphShape", ps.HParaShape.HSet)


# ── 자간 맞춤 (피드백 016 · docs/SPIKE_자간보정.md) ──────────
#
# 무엇을 하는가: 단어가 줄 끝에서 잘리지 않게 하고, 그래서 생긴 줄 끝의 빈
# 폭을 자간을 조금 좁혀 메운다. **두 단계**다 —
#   ⓐ BreakNonLatinWord = 0 (어절 단위 줄나눔).  한글의 기본값은 1(글자 단위)
#      이라 **지금도 단어 중간에서 잘리고 있다**(스파이크 ⑤ — 016 의 전제가
#      틀렸던 지점). 잘림을 없애는 본체는 이것이다.
#   ⓑ ⓐ 때문에 줄 끝에 생긴 빈 폭(실측 최대 7칸 ≒ 3.5글자)을, 다음 줄 첫
#      어절이 올라올 만큼 자간을 좁혀 메운다. 마감 손질이다.
#
# 좁히기만 한다. 넓히면 앞 줄이 다시 흔들려 진동의 여지가 생긴다(스파이크
# '진동 방지'). 재흐름은 손댄 문단 안에 갇히므로(스파이크 ④) 위에서 아래로
# 한 번만 지나간다.
#
# 남는 폭을 직접 재는 API 는 없다(스파이크 ②). 정렬을 잠깐 왼쪽으로 바꿔
# 재는 방법도 되지만, 중간에 예외가 나면 정렬이 어긋난 채 남아서 1차에서는
# 쓰지 않는다 — **걸어 보고 줄이 늘었는지 보는** 안전한 쪽으로 간다.

# 자간은 글자 종류마다 필드가 따로다. 한글만 바꾸면 줄에 섞인 영문·숫자·기호가
# 안 따라와 줄 폭이 계산과 어긋난다 (스파이크 ③).
SPACING_FIELDS = ("SpacingHangul", "SpacingLatin", "SpacingHanja",
                  "SpacingJapanese", "SpacingOther", "SpacingSymbol",
                  "SpacingUser")
FIT_MIN_PCT = -8        # 자간 하한 (스파이크 권장 — 더 좁히면 글자가 붙어 보인다)
FIT_STEP_PCT = 2        # 한 단계
_FIT_MAX_LINES = 300    # 한 번 실행이 손댈 줄 수 상한 (폭주 방지)
_FIT_MAX_PARAS = 200


def _run_action(action):
    return _h().HAction.Run(action)


def _line_col():
    """KeyIndicator 의 칸 번호(1부터) — 그 줄이 쓴 폭의 대용치. 못 읽으면 0."""
    try:
        return int(_h().KeyIndicator()[6])
    except Exception:
        return 0


def _line_bounds(list_id, para, offset):
    """offset 이 놓인 **시각적 한 줄**의 (시작 offset, 끝 offset, 끝칸)."""
    hwp = _h()
    hwp.SetPos(list_id, para, offset)
    _run_action("MoveLineBegin")
    begin = hwp.GetPos()
    _run_action("MoveLineEnd")
    end = hwp.GetPos()
    return begin[2], end[2], _line_col()


def _selected_para_range():
    r"""선택 영역이 걸친 (list_id, 첫 문단, 끝 문단). 못 정하면 None.

    (2026-08-01, 피드백 034) **표 안도 받는다.** 예전에는 본문(list 0)만
    통과시켰다 — 사용자 지적: *"표 안이라도 해도 자간맞춤 기능이 구현이
    되어야 합니다."*

    뺐던 이유는 주석에 "셀마다 폭이 달라 기준 폭 계산이 통째로 달라진다"였는데,
    **실제 구현은 폭을 재지 않는다** — 자간을 한 단계 좁혀 보고 다음 줄 첫
    어절이 끌려 올라왔는지 관찰하는 '해 보고 확인' 방식이라 그릇의 폭과
    무관하다. 남아 있던 것은 좌표 배관 문제뿐이었다.

    조건은 "양끝의 list 가 **같으면**"으로 완화한다 — 셀 하나 안의 선택이
    통과된다. **여러 셀에 걸친 선택은 1차 제외**(양끝 list 가 다르다):
    셀 블럭 선택은 GetSelectedPos 의 모양부터 달라 따로 다뤄야 한다.
    """
    try:
        got = _h().GetSelectedPos()
        # (성공여부, slist, spara, spos, elist, epara, epos)
        if got and got[0] and int(got[1]) == int(got[4]):
            lid = int(got[1])
            a, b = int(got[2]), int(got[5])
            return (lid, a, b) if a <= b else (lid, b, a)
    except Exception as e:
        applog.exc("자간 맞춤: 선택 범위를 읽지 못함", e)
    return None


def _set_break_by_word(list_id, para):
    """그 문단의 줄나눔을 어절 단위로. 이미 그렇다면 건드리지 않고 False."""
    hwp = _h()
    hwp.SetPos(list_id, para, 0)
    act, ps = hwp.HAction, hwp.HParameterSet
    act.GetDefault("ParagraphShape", ps.HParaShape.HSet)
    try:
        if int(ps.HParaShape.BreakNonLatinWord or 0) == 0:
            return False
    except Exception:
        pass
    ps.HParaShape.BreakNonLatinWord = 0
    act.Execute("ParagraphShape", ps.HParaShape.HSet)
    return True


def _read_spacing(list_id, para, offset):
    """그 자리의 한글 자간(%) — 이미 손댄 줄을 가려내는 데 쓴다."""
    hwp = _h()
    hwp.SetPos(list_id, para, offset)
    ps = hwp.HParameterSet
    hwp.HAction.GetDefault("CharShape", ps.HCharShape.HSet)
    try:
        return int(ps.HCharShape.SpacingHangul or 0)
    except Exception:
        return 0


def _select_run(list_id, para, start, end):
    r"""[start, end) 를 실제로 **선택**한다. 성공했는가.

    ⚠ 셀 안에서는 `SelectText` 가 **True 를 돌려주면서 아무것도 선택하지
    않는다** (실측 2026-08-01, spikes/cell_spacing_spike.py):

        SelectText(para, 0, para, 5) -> True
        GetSelectedPos()             -> (False, None, …)   ← 선택이 없다

    예전에는 예외가 났을 때만 대체 경로(MoveSelRight)로 갔다. 셀에서는
    예외가 안 나므로 그 갈래를 못 타고, 뒤이은 CharShape 가 **선택 없는
    자리**에 걸려 아무 일도 안 일어났다 — 표 안에서 조용히 실패하는 길이다.
    그래서 돌려주는 값을 믿지 않고 **GetSelectedPos 로 확인**한다.
    """
    hwp = _h()
    hwp.SetPos(list_id, para, start)
    try:
        hwp.SelectText(para, start, para, end)
        got = hwp.GetSelectedPos()
        if got and got[0]:
            return True
    except Exception:
        pass                                # SelectText 가 없는 판 — 아래로
    _run_action("Cancel")
    hwp.SetPos(list_id, para, start)
    for _ in range(end - start):
        _run_action("MoveSelRight")
    try:
        got = hwp.GetSelectedPos()
        return bool(got and got[0])
    except Exception:
        return False


def _apply_spacing(list_id, para, start, end, pct):
    r"""[start, end) 구간에만 자간을 건다.

    GetDefault 를 먼저 부르는 이유: HSet 을 비운 채 Execute 하면 글꼴·크기까지
    기본값으로 덮어써 문서를 망친다 (set_char_shape 와 같은 관례).
    """
    if end <= start:
        return
    hwp = _h()
    if not _select_run(list_id, para, start, end):
        _run_action("Cancel")
        return                              # 선택이 안 잡혔다 — 걸 곳이 없다
    act, ps = hwp.HAction, hwp.HParameterSet
    act.GetDefault("CharShape", ps.HCharShape.HSet)
    for f in SPACING_FIELDS:
        try:
            setattr(ps.HCharShape, f, pct)
        except Exception:
            pass                            # 그 판에 없는 필드 — 나머지로 간다
    act.Execute("CharShape", ps.HCharShape.HSet)
    _run_action("Cancel")


def _pull_up(list_id, para, start, end):
    """한 줄의 자간을 한 단계씩 좁혀 다음 줄 첫 어절을 끌어올린다.

    반환: (그 줄의 새 끝 offset, 좁혔는가). 하한까지 가도 소득이 없으면
    자간을 0 으로 되돌린다 — 얻는 것 없이 글자만 좁아지지 않게.
    """
    base = end
    cur = end
    pct = -FIT_STEP_PCT
    while pct >= FIT_MIN_PCT:
        _apply_spacing(list_id, para, start, cur, pct)
        _b, new_end, _c = _line_bounds(list_id, para, start)
        if new_end > base:
            # 끌어올린 조각까지 같은 자간으로 — 한 줄 안에서 글자 폭이
            # 갈리면 그 이음매가 눈에 띈다.
            _apply_spacing(list_id, para, start, new_end, pct)
            _b, new_end, _c = _line_bounds(list_id, para, start)
            return new_end, True
        cur = max(cur, new_end)
        pct -= FIT_STEP_PCT
    _apply_spacing(list_id, para, start, cur, 0)
    _b, back, _c = _line_bounds(list_id, para, start)
    return back, False


def _tighten_para(list_id, para, budget):
    r"""문단 하나를 위에서 아래로 훑으며 줄마다 당긴다. 좁힌 줄 수를 반환.

    멈춤 판정에 **list 까지 본다** (2026-08-01, 034): 표 안에서는 줄 끝의
    MoveRight 가 다음 셀로 넘어갈 수 있는데, 문단 번호만 보면 그 셀의
    0번 문단이 '같은 문단'으로 읽혀 남의 셀을 손대게 된다.
    """
    hwp = _h()
    tightened = 0
    start, end, _c = _line_bounds(list_id, para, 0)

    def _still_here(nxt, end_):
        return not (int(nxt[0]) != list_id or nxt[1] != para or nxt[2] <= end_)

    for _ in range(_FIT_MAX_LINES):
        if budget[0] <= 0:
            break
        # 다음 줄이 있어야 당길 것이 있다 (마지막 줄은 그냥 둔다)
        hwp.SetPos(list_id, para, end)
        _run_action("MoveRight")
        if not _still_here(hwp.GetPos(), end):
            break
        budget[0] -= 1
        # 이미 자간을 손봐 둔 줄은 건너뛴다 — 남의 서식을 덮어쓰지 않는다
        if end > start and _read_spacing(list_id, para, start) == 0:
            end, changed = _pull_up(list_id, para, start, end)
            if changed:
                tightened += 1
        hwp.SetPos(list_id, para, end)
        _run_action("MoveRight")
        nxt = hwp.GetPos()
        if not _still_here(nxt, end):
            break
        start, end, _c = _line_bounds(list_id, para, nxt[2])
    return tightened


def fit_line_spacing():
    r"""선택한 문단들의 자간을 맞춘다. 반환 {"paras", "tightened", "broke"}.

    호출부(app.fn_spacing_fit)가 선택 영역을 보장하고, 되돌리기 한 묶음으로
    봉인한다 — 줄마다 자간을 걸므로 한글의 되돌리기 칸이 그만큼 쌓인다
    (스파이크 ③: 자간 적용 1회 = 되돌리기 1칸).
    """
    rng = _selected_para_range()
    if rng is None:
        return {"paras": 0, "tightened": 0, "broke": 0}
    list_id, first, last = rng
    _run_action("Cancel")               # 선택을 풀어야 문단 단위로 옮겨 다닌다
    budget = [_FIT_MAX_LINES]
    paras = tightened = broke = 0
    for para in range(first, min(last, first + _FIT_MAX_PARAS - 1) + 1):
        try:
            if _set_break_by_word(list_id, para):
                broke += 1
            tightened += _tighten_para(list_id, para, budget)
            paras += 1
        except Exception as e:
            applog.exc(f"자간 맞춤: 문단 {para} 는 건너뜀", e)
    return {"paras": paras, "tightened": tightened, "broke": broke}


# ── 팔레트: 블럭 실행 ──────────────────────────────────
def restore_text(text):
    r"""지워진 원문을 커서 자리에 그대로 다시 넣는다. 성공 여부를 돌려준다.

    왜 필요한가 (2026-07-26 검진):
        변환은 **선택을 먼저 지운 뒤** 계획을 실행한다(그 자리가 삽입 지점이라
        순서를 바꿀 수 없다). 그런데 계획 실행이 실패하면 지운 글은 돌아오지
        않았다 — 사용자가 쓴 문장이 조용히 사라지는 유일한 경로였다.

    줄바꿈을 문단으로 되살린다. insert_plain 에 여러 줄을 통째로 주면 한글이
    문단을 나누지 않아 한 줄로 뭉친다(실측).
    """
    try:
        lines = str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for n, line in enumerate(lines):
            if n:
                _h().HAction.Run("BreakPara")
            if line:
                insert_plain(line)
        return True
    except Exception as e:
        applog.exc("지워진 선택을 문서에 되돌리지 못했습니다", e)
        return False


def run_block(block, template_path_fn=None, form_path_fn=None,
              slot_count_fn=None):
    r"""팔레트 블럭 하나를 실행한다. 종류에 따라 삽입/적용 분기.

    template_path_fn: 블럭 → 템플릿 조각 경로 (커서 위치에 삽입)
    form_path_fn:     블럭 → 양식 파일 경로 (새 문서로 열기)
    slot_count_fn:    블럭 → 그 템플릿의 빈칸(\) 개수. 빈칸 청소 범위를 개수로
                      제한하는 데 쓴다(없으면 문단 범위로만 제한).
    반환: (성공여부, 상태메시지)
    """
    btype = block.get("type")
    if btype == "char":
        insert_plain(block.get("value", ""))
        return True, "삽입"
    if btype == "function":
        if not has_selection():
            return False, "기능은 글자를 선택한 뒤 눌러주세요"
        execute_function_block(block.get("actions", []))
        return True, "기능 적용"
    if btype == "template":
        if template_path_fn is None:
            return False, "템플릿 경로를 찾을 수 없습니다"
        path = template_path_fn(block)
        if not path:
            return False, (f"템플릿을 찾을 수 없습니다: {block.get('template', '?')}"
                           " (라이브러리에서 삭제된 것 같습니다)")
        anchor = _h().GetPos()
        # 팔레트로 넣을 땐 채울 내용이 없으므로, 삽입한 범위 안의 빈칸만 청소한다.
        # slot_count 를 알면 그 개수만큼만 지운다 (모르면 문단 범위로만 제한).
        end_para = measure_insert_span(anchor, lambda: insert_fragment(path))
        normalize_slot_tokens(anchor, end_para)   # \이름\·\\ → \ (청소 전에)
        strip_slot_markers(anchor, end_para,
                           max_delete=slot_count_fn(block) if slot_count_fn else None)
        return True, "템플릿 삽입"
    if btype == "form":
        if form_path_fn is None:
            return False, "양식 경로를 찾을 수 없습니다"
        path = form_path_fn(block)
        if not path:
            return False, (f"양식을 찾을 수 없습니다: {block.get('form', '?')}"
                           " (라이브러리에서 삭제된 것 같습니다)")
        # 팔레트 버튼으로 여는 것 = 손으로 채워 쓰는 경우다. 자리표시(\)는
        # 인쇄물에 남으면 안 되므로 열면서 걷어낸다 (2026-07-26).
        open_form(path, strip_markers=True)
        return True, "양식 열기"
    return False, f"알 수 없는 블럭: {btype}"


def apply_default_format(fmt, text=None):
    """선택 영역을 기본 서식으로 초기화(글자모양+문단모양). text 주면 교체 삽입.

    fmt: palette.get_default_format() 결과.
    """
    hwp = _h()
    act = hwp.HAction
    ps = hwp.HParameterSet
    if text is not None:
        delete_selection()
    act.Run("StyleClearCharShape")

    act.GetDefault("CharShape", ps.HCharShape.HSet)
    ps.HCharShape.FaceNameHangul = fmt.get("font", "함초롬바탕")
    ps.HCharShape.FaceNameLatin = fmt.get("font", "함초롬바탕")
    ps.HCharShape.Height = hwp.PointToHwpUnit(fmt.get("size_pt", 10.0))
    ps.HCharShape.SpacingHangul = fmt.get("spacing", 0)
    ps.HCharShape.SpacingLatin = fmt.get("spacing", 0)
    # StyleClearCharShape만으로는 굵게/기울임/밑줄이 남을 수 있어 명시적으로 끔
    ps.HCharShape.Bold = 0
    ps.HCharShape.Italic = 0
    ps.HCharShape.UnderlineType = 0
    ps.HCharShape.TextColor = hwp.rgb_color(0, 0, 0)
    act.Execute("CharShape", ps.HCharShape.HSet)

    act.GetDefault("ParagraphShape", ps.HParaShape.HSet)
    ps.HParaShape.LineSpacing = fmt.get("line_spacing", 160)
    ps.HParaShape.LineSpacingType = 0
    ps.HParaShape.Indentation = 0
    ps.HParaShape.LeftMargin = 0
    ps.HParaShape.RightMargin = 0
    ps.HParaShape.PrevSpacing = 0
    ps.HParaShape.NextSpacing = 0
    ps.HParaShape.AlignType = fmt.get("align", 0)
    ps.HParaShape.BreakNonLatinWord = 1   # 한글 기본값(글자 단위)으로 복귀
    ps.HParaShape.Condense = 0            # 자간 자동조절 해제
    act.Execute("ParagraphShape", ps.HParaShape.HSet)

    if text is not None:
        insert_plain(text)


def insert_rich_line(segments, picture_bounds_mm=None):
    r"""서식 구간이 섞인 한 줄을 삽입한다 (개선안 27 — \굵게{내용}).

    구간마다 [삽입 전 위치 기록 → 삽입 → 그 구간만 다시 선택 → 서식 적용]을
    반복한다. 선택을 걸면 커서가 구간 앞쪽으로 갈 수 있으므로, 다음 구간을
    이어 쓰기 전에 **반드시 끝 위치로 되돌려 놓는다** — 안 그러면 두 번째
    구간부터 글자가 앞에 끼어 들어간다.

    **감싸지 않은 구간에도 서식을 적용한다** — 줄 시작 시점의 서식을 미리
    떠 두었다가 그대로 되입힌다. 한글은 새로 넣는 글자에 '앞 글자의 서식'을
    물려주기 때문에, 그냥 두면 `\굵게{중요} 나머지` 에서 '나머지'까지 굵게
    나온다. 물려받은 서식을 매번 원래대로 되돌려야 감싼 부분만 굵어진다.

    서식 적용에 실패해도 글자는 이미 들어가 있다. 그 경우 서식만 포기하고
    계속 진행한다 — 변환 전체를 되돌리는 것보다 낫다.
    """
    hwp = _h()
    # 줄 시작 시점의 서식 = 감싸지 않은 구간이 유지해야 할 모습
    base = None
    if any(s.get("style") for s in segments):
        try:
            base = capture_charshape(CHARSHAPE_FIELD_LABELS)
        except Exception as e:
            applog.exc("서식 감싸기: 원래 서식을 못 읽음 — 감싼 뒤 서식이 번질 수 있음", e)

    for seg in segments:
        formula = seg.get("formula")
        if formula:
            try:
                _insert_equation(formula)
            except Exception as e:
                applog.exc(f"수식 삽입 실패 ({formula}) — 선형 수식으로 대체", e)
                insert_plain(_formula_plain_fallback(formula))
            continue
        image = seg.get("image")
        if image:
            # \사진이름\ — 사진 폴더의 그림을 글자처럼 삽입.
            # embedded=True 라 문서에 포함된다(원본 폴더를 지워도 그림은 남음).
            # 크기는 _insert_picture_sized 가 정한다(셀 밖이면 PNG 의 실제 크기).
            pos = None
            try:
                pos = hwp.GetPos()
            except Exception as e:
                applog.exc("사진 삽입 전 위치 기록 실패 — 삽입 후 커서를 못 되돌린다", e)
            try:
                _insert_picture_sized(hwp, image, max_bounds_mm=picture_bounds_mm)
            except Exception as e:
                applog.exc(f"사진 삽입 실패 ({image}) — 자리 표시 텍스트로 대체", e)
                insert_plain(f"[사진 실패: {image}]")
                continue
            # ⚠ 그림을 넣으면 한글이 그 '그림 개체'를 선택한 채로 둔다.
            # 개체가 선택돼 있으면 뒤이은 찾기(RepeatFind)가 글자를 못 찾는다 →
            # fill_slots 가 다음 빈칸을 못 찾고 통째로 멈췄다
            # ("사진 전까지는 잘 들어가는데 그 뒤로 안 들어간다"의 원인).
            # 개체 선택을 풀고, 글자처럼취급된 그림(= 글자 한 칸) 바로 뒤로 커서를 옮긴다.
            try:
                hwp.HAction.Run("Cancel")
                if pos:
                    hwp.SetPos(pos[0], pos[1], pos[2] + 1)
            except Exception as e:
                applog.exc("사진 삽입 후 커서 복귀 실패 — 뒤따르는 내용이 밀릴 수 있음", e)
            continue
        text = seg.get("text") or ""
        if not text:
            continue
        start = hwp.GetPos()
        insert_plain(text)
        end = hwp.GetPos()
        delta = seg.get("style") or base
        if not delta:
            continue
        try:
            if hwp.select_text_by_get_pos(start, end):
                apply_charshape_delta(delta)
            else:
                applog.warn(f"서식 감싸기: 구간 선택 실패 — 서식 없이 삽입됨 ({text[:20]!r})")
        except Exception as e:
            applog.exc(f"서식 감싸기 적용 실패 ({text[:20]!r}) — 글자는 삽입됨", e)
        finally:
            hwp.HAction.Run("Cancel")       # 선택 해제
            hwp.SetPos(*end)                # 다음 구간은 이 줄 끝에서 이어 쓴다


def _balanced_group(source, start):
    if start >= len(source) or source[start] != "{":
        return None
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1:index], index + 1
    return None


def _formula_to_hwp(source):
    import re
    value = str(source or "").strip()

    # The reconstruction input is LaTeX-like while Hancom's equation editor
    # uses its own linear grammar.  Simple commands happen to overlap, but
    # environments and delimiter/style commands otherwise appear as literal
    # control text in the finished document.  Normalize those constructs before
    # the existing fraction/radical conversion.
    def environment(match):
        name = match.group(1)
        body = match.group(2)
        body = body.replace(r"\\", " # ")
        keyword = "cases" if name == "cases" else "matrix"
        return keyword + " {" + _formula_to_hwp(body) + "}"

    value = re.sub(
        r"\\begin\{(cases|aligned|array)\}(?:\{[^{}]*\})?(.*?)\\end\{\1\}",
        environment,
        value,
        flags=re.S,
    )

    def grouped_command(command, replacement):
        nonlocal value
        for _ in range(24):
            match = re.search(r"\\?" + re.escape(command) + r"\s*\{", value)
            if not match:
                break
            group = _balanced_group(value, match.end() - 1)
            if not group:
                break
            rendered = _formula_to_hwp(group[0])
            value = value[:match.start()] + " " + replacement + " {" + rendered + "} " + value[group[1]:]

    grouped_command("overrightarrow", "vec")
    grouped_command("overline", "bar")
    grouped_command("boxed", "box")
    grouped_command("text", "rm")
    grouped_command("operatorname", "rm")
    grouped_command("mathrm", "rm")
    grouped_command("mathtt", "rm")
    grouped_command("mathcal", "")

    value = re.sub(r"\\(?:left|right|bigl|bigr|Bigl|Bigr)\b", "", value)
    value = re.sub(r"\\mathcal\s*([A-Za-z])", r" {\1} ", value)
    symbols = {
        "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥",
        "neq": "≠", "ne": "≠", "to": "→", "times": "×",
        "cdot": "·", "angle": "∠", "mid": "|", "perp": "⟂",
        "subset": "⊂", "cup": "∪", "cap": "∩", "emptyset": "∅",
        "infty": "∞", "pi": "π", "theta": "θ", "alpha": "α",
        "beta": "β", "gamma": "γ", "sigma": "σ", "ell": "ℓ",
        "diamond": "◇", "div": "÷", "circ": "∘", "ldots": "…",
    }
    value = re.sub(
        r"\\(" + "|".join(map(re.escape, symbols)) + r")(?![A-Za-z])",
        lambda match: " " + symbols[match.group(1)] + " ",
        value,
    )
    value = re.sub(r"\\quad\b", "  ", value)
    value = re.sub(r"\\(?=\s)", "", value)
    value = value.replace(r"\{", "lbrace ").replace(r"\}", " rbrace")
    value = value.replace(r"\,", " ").replace(r"\;", " ").replace(r"\!", "")
    for _ in range(12):
        match = re.search(r"\\?frac\s*\{", value)
        if not match:
            break
        numerator = _balanced_group(value, match.end() - 1)
        if not numerator:
            break
        den_start = numerator[1]
        while den_start < len(value) and value[den_start].isspace():
            den_start += 1
        denominator = _balanced_group(value, den_start)
        if not denominator:
            break
        replacement = "{" + _formula_to_hwp(numerator[0]) + "} over {" + _formula_to_hwp(denominator[0]) + "}"
        value = value[:match.start()] + replacement + value[denominator[1]:]
    value = re.sub(r"\\?sqrt\s*\{", "sqrt {", value)
    value = re.sub(r"\\?vec\s*\{", "vec {", value)
    return re.sub(r"\\([A-Za-z]+)", r"\1", value)


def _formula_plain_fallback(source):
    import re
    greek = {"theta": "θ", "lambda": "λ", "mu": "μ", "pi": "π", "Delta": "Δ",
             "alpha": "α", "beta": "β", "gamma": "γ", "omega": "ω", "times": "×"}
    value = re.sub(r"\\([A-Za-z]+)", lambda m: greek.get(m.group(1), m.group(1)), str(source or ""))
    return value.replace("{", "(").replace("}", ")").replace("frac", "")


def _insert_equation(source):
    hwp = _h()
    start = hwp.GetPos()
    act = hwp.HAction
    ps = hwp.HParameterSet
    act.GetDefault("EquationCreate", ps.HEqEdit.HSet)
    # HWP 2022's generated COM wrapper exposes this property as lowercase
    # ``string``.  Assigning ``String`` raises AttributeError and silently
    # degrades every formula to the linear fallback in insert_rich_line().
    ps.HEqEdit.string = _formula_to_hwp(source)
    ps.HEqEdit.BaseUnit = (
        FRACTION_EQUATION_BASE_UNIT
        if "frac" in str(source)
        else INLINE_EQUATION_BASE_UNIT
    )
    act.Execute("EquationCreate", ps.HEqEdit.HSet)
    act.Run("Cancel")
    hwp.SetPos(start[0], start[1], start[2] + 1)


def _unit_changes(unit, ops):
    """이 조각이 변환으로 실제로 달라지는가.

    안 달라지면 문서를 아예 건드리지 않는다 — 라벨이 없는 셀까지 지웠다 다시
    넣으면 서식이 흔들리고, 찾기가 어긋났을 때 멀쩡한 글을 망칠 위험만 커진다.
    """
    if len(ops) != 1:
        return True
    if ops[0][0] == "rich_line":
        return True                 # 서식·사진이 섞였다 = 반드시 다시 그려야 한다
    return ops[0][1] != unit


def convert_units_in_place(units, plan_fn, anchor=None):
    r"""조각들을 **있던 자리에서** 변환한다 (표의 셀 경계를 지키려고).

    왜 따로 있나:
      execute_library_plan 은 '선택을 통째로 지우고 커서 한 곳에 다시 쓰는' 방식이다.
      본문에서는 맞지만 표에서는 셀 경계가 사라진다 — 여러 셀을 선택해 변환하면
      모든 셀의 내용이 커서가 남은 한 셀에 줄바꿈으로 쌓였다(사진이 한 칸에
      몰리던 버그). 셀 안 내용은 옮길 필요가 없으므로, 옮기지 않고 제자리에서
      찾아 바꾼다. 그러면 find_text 가 알아서 해당 셀로 들어가므로 셀 이동을
      직접 다룰 필요도 없다.

    앞뒤로 찾는 이유:
      선택을 풀면(Cancel) 커서가 선택의 **끝**에 남을 수 있다(드래그 방향에 달렸다).
      그러면 조각들이 전부 커서 뒤쪽에 있어 앞으로 찾기만 해서는 하나도 못 찾는다.
      그래서 앞으로 찾아 실패하면 뒤로 한 번 더 찾는다. 첫 조각을 뒤에서 찾고
      나면 커서가 그 자리로 오므로, 나머지는 자연히 앞으로 찾기로 이어진다.

    안전 원칙 — 실패 방향은 '원문이 그대로 남는다' 쪽이다:
      · 바뀔 게 없는 조각은 건드리지 않는다 (라벨 없는 셀은 그대로 둔다)
      · 찾지 못한 조각은 경고만 남기고 건너뛴다 (지우지 않는다)
      · 바꾸는 대상은 사용자가 **선택한 글자와 완전히 같은 줄**뿐이다. 문서
        다른 곳의 같은 라벨이 함께 바뀔 수는 있어도, 그것도 사용자가 변환하려던
        라벨이므로 훼손이 아니다.

    plan_fn: 조각 한 개(=한 줄) → (ops, warnings). 경고는 여기서 쓰지 않는다.
      호출부가 이미 선택 전체에 대해 같은 경고를 사용자에게 보여줬기 때문이다.
    반환: 실제로 바꾼 조각 수.
    """
    hwp = _h()
    act = hwp.HAction
    if anchor is not None:
        try:
            hwp.SetPos(*anchor)
        except Exception as e:
            applog.exc("변환 시작 위치 복원 실패 — 커서가 있는 곳부터 찾습니다", e)
    changed = 0
    for unit in units:
        ops, _ = plan_fn(unit)
        if not _unit_changes(unit, ops):
            continue
        if not (find_text(unit) or find_text(unit, direction="Backward")):
            applog.warn(f"바꿀 자리를 찾지 못해 건너뜀: {unit[:30]!r}")
            continue
        delete_selection()
        for idx, op in enumerate(ops):
            if idx:
                act.Run("BreakPara")
            if op[0] == "line":
                if op[1]:
                    insert_plain(op[1])
            elif op[0] == "rich_line":
                insert_rich_line(op[1])
        changed += 1
    return changed


# ── 라이브러리: 마크다운(\라벨\) 변환 실행 ───────────────
def _find_template_marker(marker, previous_anchor):
    """Find the next template marker without rescanning completed fragments."""
    hwp = _h()
    if previous_anchor is None:
        hwp.MoveDocBegin()
    else:
        hwp.SetPos(*previous_anchor)
    return find_text(marker)


def execute_library_plan(ops, template_path_fn, form_path_fn=None):
    r"""parser.build_library_plan()의 실행 계획을 문서에 반영한다.

    호출 전에 선택 영역은 삭제돼 있어야 한다(커서 = 삽입 지점).

    2단계 방식: ① 텍스트 줄과 '템플릿 자리표시 마커'를 순서대로 삽입 →
    ② 마커를 찾아 조각으로 바꾸고, 이어서 빈칸(\)을 아랫줄 내용으로 채움.
    한 번에 삽입하지 않는 이유: insert_file 직후 커서가 조각 뒤로 이동하지
    않아(실측) 순차 삽입 순서가 꼬이기 때문 — 마커 방식이 순서를 보장한다.

    양식('form')은 성격이 달라 **먼저** 처리한다 — 문서 전체를 여는 것이라
    마커를 심어둔 문서가 사라지기 때문이다. 양식을 연 뒤 본문 자리(BODY_ANCHOR)로
    커서를 옮기고, 나머지 계획을 그 문서에서 이어서 실행한다 (2026-07-24).
    예전에는 양식이 있으면 그것만 처리하고 나머지를 버렸다 — 시험지처럼
    "양식 + 문제들"을 한 번에 변환할 수가 없었다.
    """
    hwp = _h()
    act = hwp.HAction

    marker_base = "◈LIB%d_" % (int(time.time() * 1000) % 10**9)

    forms_done = 0
    form_filled = 0
    form_wanted = 0

    # ── 양식이 있으면: 그 문서를 열고 커서를 본문 자리에 놓는다 ──
    form_op = next((o for o in ops if o[0] == "form"), None)
    if form_op is not None:
        _, item, fills = form_op
        path = form_path_fn(item) if form_path_fn else None
        if not path:
            return {"templates": 0, "slots_filled": 0, "forms": 0,
                    "error": f"양식 파일을 찾을 수 없습니다: {item.get('name', '?')}"}
        open_form(path)

        # ① 본문 표시를 고유 마커로 먼저 치환한다.
        #    \본문\ 은 역슬래시 2개라, 그냥 두면 ②의 fill_slots 가 이걸
        #    빈칸으로 보고 내용을 채워 넣어 버린다.
        body_marker = marker_base + "BODY◈"
        hwp.MoveDocBegin()
        has_anchor = find_text(BODY_ANCHOR)
        if has_anchor:
            delete_selection()
            insert_plain(body_marker)

        # ② 양식이 가진 빈칸(\)을 양식 라벨 아랫줄들로 채운다.
        #    새로 연 양식 문서 전체가 대상이므로 범위 제한을 두지 않는다
        #    (사용자가 쓴 다른 내용이 섞여 있을 수 없는, 유일하게 안전한 경우)
        hwp.MoveDocBegin()
        form_filled, form_wanted = fill_slots(
            hwp.GetPos(), fills, end_para=None,
            slot_count=item.get("slot_count"),
            slot_names=item.get("slot_names"))
        forms_done = 1

        # ③ 본문 자리로 커서 이동. 표시가 없는 양식이면 문서 끝에서 이어 쓴다.
        hwp.MoveDocBegin()
        if has_anchor and find_text(body_marker):
            delete_selection()
        else:
            hwp.MoveDocEnd()
            if not has_anchor:
                applog.warn(
                    f"양식 '{item.get('name', '?')}' 에 본문 표시({BODY_ANCHOR})가 "
                    f"없어 문서 끝에 이어 씁니다")

        # ④ 나머지 계획을 이 문서에서 이어서 실행한다.
        ops = [o for o in ops if o[0] != "form"]
        if not ops:
            return {"templates": 0, "slots_filled": form_filled,
                    "slots_wanted": form_wanted, "forms": 1}

    # ① 텍스트/마커 순차 삽입
    templates = []
    first = True
    for op in ops:
        if not first:
            act.Run("BreakPara")
            if op[0] == "template" and _template_needs_new_page(op[1], op[2]):
                # 조각 파일 안의 쪽 나누기는 InsertFile 과정에서 보존되지 않는다.
                # 따라서 실제 출력 문서에 마커를 심는 시점에 쪽을 넘긴다.
                act.Run("BreakPage")
        first = False
        if op[0] == "line":
            if op[1]:
                insert_plain(op[1])
        elif op[0] == "rich_line":
            insert_rich_line(op[1])
        elif op[0] == "table":
            insert_table(op[1], op[2], op[3])
        else:                               # ('template', item, fills)
            insert_plain(marker_base + str(len(templates)) + "◈")
            templates.append(op)

    # ② 마커 → 조각 치환 + 빈칸(\) 순서대로 채움
    filled = 0
    wanted = 0
    previous_anchor = None
    for idx, (_, item, fills) in enumerate(templates):
        marker = marker_base + str(idx) + "◈"
        if not _find_template_marker(marker, previous_anchor):
            applog.warn(f"마커 유실로 템플릿을 건너뜀: {item.get('name', '?')}")
            continue
        delete_selection()
        anchor = hwp.GetPos()
        path = template_path_fn(item)
        end_para = measure_insert_span(anchor, lambda p=path: insert_fragment(p))
        got, want = fill_slots(anchor, fills, end_para,
                               slot_count=item.get("slot_count"),
                               slot_names=item.get("slot_names"))
        filled += got
        wanted += want
        previous_anchor = anchor
    # 양식을 먼저 처리했다면 그 빈칸 개수도 합쳐서 보고한다 — 사용자에겐
    # "이번 변환에서 빈칸 몇 개를 채웠나"가 하나의 숫자여야 한다.
    return {"templates": len(templates), "slots_filled": filled + form_filled,
            "slots_wanted": wanted + form_wanted, "forms": forms_done}
