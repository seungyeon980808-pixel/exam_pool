# -*- coding: utf-8 -*-
r"""윈도우 클립보드 — **Tk 클립보드를 쓰지 않는다** (실측 2026-07-26).

왜 따로 떼어 놓는가.

Tk 의 `clipboard_append` 는 클립보드에 값을 넣는 것이 아니라 **"내가 클립보드
주인이다, 필요하면 물어봐라"** 라고 등록만 한다(지연 렌더링). 그래서 그 뒤로
같은 프로세스에서 `win32clipboard.OpenClipboard()` 를 부르면
`(5, 'OpenClipboard', '액세스가 거부되었습니다')` 가 쏟아진다(실측).

이것이 튜토리얼에서 변환이 안 잡히던 원인이다:
  [복사] 버튼(Tk 클립보드) → 한글에 붙여넣기 → 드래그 선택 → [마크다운 변환]
  → 한글의 Copy 결과를 읽어야 하는데 클립보드가 잠겨 10회 재시도 모두 실패
  → 선택을 못 읽고 "선택 없음".

그래서 **담을 때도 읽을 때도 윈도우 API 로만** 다룬다. 우리가 클립보드 주인이
되지 않으므로 잠기지 않고, 한글이 Copy 로 넣은 값도 그대로 읽힌다.

Tk 로 물러나는 것은 **win32clipboard 모듈 자체가 없는 환경**(테스트·비윈도우)
뿐이다 (2026-07-31 안전 감사). 윈도우 API 가 **있는데 잠깐 실패**한 경우에
Tk 로 물러나면, 그 순간부터 우리가 클립보드 주인이 되어 위의 잠금이 세션
내내 재발한다 — 일시 실패는 실패라고 정직하게 돌려준다.
"""

import time

from hwp_palette.core import applog

_RETRIES = 10
_DELAY = 0.08


def _win32():
    """win32clipboard 모듈 (없으면 None) — 플랫폼 의존이라 지역 import."""
    try:
        import win32clipboard
        return win32clipboard
    except ImportError:
        return None


def _read_text_once(w):
    """윈도우 API 로 한 번만 읽어 본다 (재시도 없음). 못 읽으면 None."""
    try:
        w.OpenClipboard()
        try:
            if w.IsClipboardFormatAvailable(w.CF_UNICODETEXT):
                return w.GetClipboardData(w.CF_UNICODETEXT)
        finally:
            w.CloseClipboard()
    except Exception:
        return None
    return None


def _set_text_once(w, text):
    """윈도우 API 로 한 번만 담아 본다 (재시도 없음). 성공 여부."""
    w.OpenClipboard()
    try:
        w.EmptyClipboard()
        w.SetClipboardData(w.CF_UNICODETEXT, str(text))
    finally:
        w.CloseClipboard()
    return True


def set_text(text, widget=None):
    """클립보드에 글자를 담는다. 성공하면 True.

    widget 을 주면 **윈도우 API 모듈이 아예 없는 환경에서만** Tk 로 물러난다
    (되도록 쓰이지 않아야 하는 길이다 — 위 설명 참조). 윈도우 API 가 있는데
    일시적으로 실패한 경우는 Tk 로 가지 않고 False 를 돌려준다 — Tk 로 담는
    순간 우리가 클립보드 주인이 되어 이후 OpenClipboard 가 세션 내내 막힌다.

    EmptyClipboard 는 사용자의 클립보드를 지운다. 담기가 끝내 실패하면
    지우기만 하고 새 값은 못 넣은 채가 되므로, 시작 전에 기존 글자를
    읽어 두었다가 실패 시 한 번만 되살려 본다 (둘 다 최선 노력).
    """
    w = _win32()
    if w is None:
        if widget is not None:
            try:
                widget.clipboard_clear()
                widget.clipboard_append(str(text))
                widget.update()
                return True
            except Exception as e:
                applog.exc("Tk 클립보드 담기도 실패", e)
        return False
    saved = _read_text_once(w)          # 실패 시 되살릴 기존 내용 (최선 노력)
    last = None
    for _ in range(_RETRIES):
        try:
            return _set_text_once(w, text)
        except Exception as e:
            last = e            # 다른 앱이 잠깐 잡고 있으면 실패한다
            time.sleep(_DELAY)
    applog.exc(f"클립보드 담기 {_RETRIES}회 모두 실패", last)
    if saved:
        try:                            # 지워 놓기만 한 채 끝나지 않게
            _set_text_once(w, saved)
        except Exception as e:
            applog.exc("클립보드 원래 내용 복원 실패", e)
    return False


def get_text(retries=_RETRIES, delay=_DELAY):
    """클립보드의 글자. 못 읽으면 빈 문자열."""
    w = _win32()
    if w is None:
        return ""
    last = None
    for _ in range(retries):
        try:
            w.OpenClipboard()
            try:
                if w.IsClipboardFormatAvailable(w.CF_UNICODETEXT):
                    text = w.GetClipboardData(w.CF_UNICODETEXT)
                    if text:
                        return text
            finally:
                w.CloseClipboard()
        except Exception as e:
            last = e
        time.sleep(delay)
    if last is not None:
        applog.exc(f"클립보드 읽기 {retries}회 모두 실패", last)
    return ""


def sequence_number():
    """클립보드 순번 (내용이 바뀔 때마다 증가). 못 읽으면 None.

    GetClipboardSequenceNumber 는 OpenClipboard 없이 불리므로 잠금과 무관하다.
    Copy 같은 동작이 **실제로 클립보드를 채웠는지** 증명하는 데 쓴다 —
    순번이 안 움직였으면 클립보드에 있는 것은 예전 내용이다.
    """
    w = _win32()
    if w is None:
        return None
    try:
        return int(w.GetClipboardSequenceNumber())
    except Exception as e:
        applog.exc("클립보드 순번 조회 실패", e)
        return None
