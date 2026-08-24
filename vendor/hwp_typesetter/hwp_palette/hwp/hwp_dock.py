# -*- coding: utf-8 -*-
r"""한글 창 도킹 — 편집하는 동안 미리보기 판 자리에 딱 맞춰 붙인다 (2026-07-27).

왜 만들었나 (사용자 결정): 템플릿·양식을 고칠 때 한글 창이 아무 데나 떠서
"불필요한 창이 하나 더 생긴" 느낌이었다. 진짜 임베드(SetParent)는 검토 끝에
버렸다 — 프로세스 경계를 넘는 부모 관계는 입력 큐가 묶여 한글이 멈추면 우리도
멈추고, 우리가 죽으면 한글 창이 통째로 사라지며, IME 조합이 깨질 위험이 있다.
대신 **도킹**한다: 창 이동·크기 조절로 판 자리에 겹쳐 두고, 편집이 끝나면
원래 배치로 되돌린다. 한글은 끝까지 독립된 창이라 서로를 해칠 수 없다.

(2026-07-30 최종) 잠깐 임베드(hwp_embed.py)를 만들어 함께 두고 고르게 했다가
**임베드는 버리고 파일도 지웠다** — 강제 종료 때 원고가 위험한데, 도킹의
버벅임을 이벤트 훅으로 없앤 뒤로는 얻는 것이 없었다.

추적은 **별도 스레드**가 한다 (2026-07-28 재작성, 사용자 지적 "창을 놓아야
따라온다"): 제목줄을 끄는 동안 윈도우는 모달 이동 루프에 들어가 Tk 의 after
타이머가 멎는다 — <Configure> 디바운스 방식은 그래서 놓은 뒤에야 따라왔다.
스레드는 Win32 호출만 쓰므로(Tk·COM 금지) 그 루프와 무관하게 계속 돈다.

따라오는 방식 (2026-07-30 재작성, 사용자 지시 "버벅임을 최소화하라"):
    첫 판은 30ms 폴링 + 완화(easing 45%)였다. 그런데 그 둘이 곧 버벅임이었다 —
    폴링은 잠들어 있다 깨어나는 시간(최대 30ms)만큼 늦고, 완화는 **일부러**
    ~150ms 에 걸쳐 따라붙는다. '미끄러지듯'은 곧 '늦게'다.
    이제는 **윈도우 이벤트 훅**(SetWinEventHook, EVENT_OBJECT_LOCATIONCHANGE)
    이다. 우리 창이 1px 이라도 움직이면 OS 가 그 즉시 이벤트를 쏘고, 훅
    스레드는 받은 즉시 완화 없이 **한 번에 스냅**한다. 기다리는 시간 자체가
    없다. 훅 등록이 실패하면 옛 폴링(8ms, 완화 없음)으로 물러난다.
    이벤트가 밀릴 때의 뭉개짐은 걱정할 것 없다 — 목표 좌표를 이벤트가 아니라
    **처리 시점의 GetWindowRect** 에서 다시 읽으므로, 밀린 이벤트는 '이미 맞는
    자리'를 확인만 하고 지나간다 (자연스러운 병합).

좌표 규칙 (실측 2026-07-27, dock_spike):
    이 프로세스는 DPI 미인식이라 winfo_rootx 같은 Tk 좌표는 4K 모니터에서
    배율만큼 어긋날 수 있다. 그래서 **읽기(GetWindowRect)와 쓰기(SetWindowPos)
    를 같은 프로세스 관점**으로 맞춘다 — 같은 가상 좌표계끼리는 상쇄되므로
    주모니터·4K·모니터 사이 빈 구간까지 오차 0px 로 맞았다.
"""

import ctypes
import threading
import time
from ctypes import wintypes

import win32api
import win32con
import win32gui

from hwp_palette.core import applog

# 판을 도킹용으로 넓힐 때의 폭 — 세로 주모니터(가상 폭 1080)에 창 전체가
# 들어가는 상한이다 (사용자 결정 2026-07-27: '둘 다 접기' 안).
EDIT_PANE_W = 1010

_FALLBACK_TICK_S = 0.008  # 훅 실패 시 폴링 주기 — 8ms 면 지연이 눈에 안 띈다
_EASE = 0.45              # restore() 의 되돌아가는 활강에만 쓴다 (추적엔 안 씀)
_SNAP_PX = 2              # 이 안쪽이면 정확히 맞춰 붙인다

# ── 이벤트 훅 상수 (ctypes — pywin32 에 SetWinEventHook 이 없다) ──
_EVENT_OBJECT_LOCATIONCHANGE = 0x800B
_OBJID_WINDOW = 0
_QS_ALLINPUT = 0x04FF
_WINEVENTPROC = ctypes.WINFUNCTYPE(
    None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
    ctypes.c_long, ctypes.c_long, wintypes.DWORD, wintypes.DWORD)
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
# CreateRectRgn 은 pywin32 의 win32gui 에 없다 (실측 2026-07-30)
_gdi32 = ctypes.windll.gdi32
# 창 사이의 **소유자 위계** (2026-08-02, 041) — 소유 창은 소유자보다 늘 위에
# 있도록 윈도우가 지켜 준다. Dock._set_owner 참고.
_GWLP_HWNDPARENT = -8
# 64비트에서 GetWindowLongPtrW 는 핸들을 돌려주므로 int 로 못 받으면 잘린다.
_user32.GetWindowLongPtrW.restype = ctypes.c_void_p
_user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
_user32.SetWindowLongPtrW.restype = ctypes.c_void_p
_user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int,
                                      ctypes.c_void_p)
# **z순서를 건드리지 않는다** (실측 2026-07-30, spikes/dock_click_spike.py):
# 여태 매 틱 HWND_TOPMOST 로 밀어 올렸는데, 우리 창도 '항상 위'라 둘이 같은
# 띠에서 자리다툼을 했다. 그 결과 한글이 활성화되는 순간 우리 빈 판이 위로
# 올라와 **마우스·키보드를 가로챘다** — "한글 안이 클릭이 안 되고 글이 안
# 써진다"의 정체다. 이제 z 는 윈도우가 알아서 하게 두고(활성화된 창이 위),
# 우리는 자리와 크기만 맞춘다.
_MOVE_FLAGS = (win32con.SWP_SHOWWINDOW | win32con.SWP_NOACTIVATE
               | win32con.SWP_NOZORDER)

_RGN_DIFF = 4                 # CombineRgn 모드 — A 에서 B 를 뺀다


def dpi_scale(hwnd):
    r"""그 창이 놓인 화면의 배율 (1.0 / 1.25 / 1.5 …).

    우리 프로세스는 DPI 미인식이라 우리 창에 물어보면 늘 96 이 나온다. 그래서
    **DPI 를 아는 창**(한글)에게 묻는다 — 창 영역(SetWindowRgn)의 좌표는
    가상화되지 않은 실제 픽셀이므로, 이 배율로 곱해 줘야 자리가 맞는다.

    (2026-08-01) 이 함수와 _RGN_DIFF 는 e84cc46(제목줄 잘라내기 철회)에서
    **정의만 지워지고 호출부는 남아** 있었다. 그동안 _punch_hole 은 매번
    NameError 로 실패했고(로그에만 남았다), 구멍이 없으니 도킹 화면은
    keep_order 의 z 순서 하나에만 기대고 있었다 — 순서가 흔들리는 순간
    (팝오버·항상 위) 판이 하얗게 보이던 035·036 증상의 바닥이다.
    """
    try:
        return (_user32.GetDpiForWindow(hwnd) or 96) / 96.0
    except Exception:
        return 1.0


def fit_on_screen(hwnd, w, h):
    r"""그 창을 w×h 로 키울 때 **화면 밖으로 안 나가는** 좌상단 좌표.

    왜 필요한가 (2026-07-29, 감싸기 도킹): 평소 창은 화면 오른쪽 위에 선다
    (한글을 가리지 않으려고). 거기서 폭 1180 으로 키우면 오른쪽 절반이 화면
    밖으로 나가 도구줄이 통째로 안 보인다.

    창이 놓인 모니터의 작업 영역 안으로 밀어 넣는다 — 그 모니터가 창보다
    작으면 왼쪽 위에 붙인다(잘려도 왼쪽부터 보이는 편이 낫다).
    """
    try:
        l, t, _r, _b = win32gui.GetWindowRect(hwnd)
        mon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        wl, wt, wr, wb = win32api.GetMonitorInfo(mon)["Work"]
        x = min(max(l, wl + 8), max(wl + 8, wr - w - 8))
        y = min(max(t, wt + 8), max(wt + 8, wb - h - 8))
        return int(x), int(y)
    except Exception as e:
        applog.exc("감싸기 창 자리 계산 실패 — 있던 자리에서 키운다", e)
        return None


# 제목줄 잘라내기는 **철회했다** (사용자 결정 2026-07-30).
#
# 한글이 자기 그림 영역에 직접 그리는 제목줄을 창 영역(SetWindowRgn)으로
# 오려 내 봤는데, 잘라낼 높이가 배율·버전마다 어긋나 **리본(파일·편집·서식
# 도구줄)까지 함께 날아갔다.** 사용자 결정: "그냥 윗부분은 슬라이스 안 하는
# 걸로 하겠습니다. 필요한 부분까지 날아가니까 불편합니다."
#
# 아래 두 함수는 **치우는 쪽으로만** 남긴다: 그 시절에 잘린 채 남은 창을
# 다시 붙일 때 원래대로 되돌려 주는 일을 한다.
def crop_top_of(hwnd):
    """그 창이 지금 위로 얼마나 잘려 있는가 (논리 픽셀, 0 이면 안 잘림)."""
    try:
        box = wintypes.RECT()
        if not _user32.GetWindowRgnBox(hwnd, ctypes.byref(box)):
            return 0
        return max(0, box.top)
    except Exception:
        return 0


def clear_crop(hwnd):
    r"""잘라내기를 없앤다 — 창을 원래대로 통째로 보이게. 두 번 시도한다.

    왜 두 번인가 (실측 2026-07-30): `SetWindowRgn(NULL)` 은 성공(1)을 돌려주면서
    남의 프로세스 창에서는 먹지 않는 경우가 있었다. 그때는 **창 전체를 덮는
    영역**을 씌우면 실질적으로 안 잘린 것과 같아진다.

    이게 중요한 이유: 잘라내기가 남으면 한글은 제목줄 없는 창으로 **우리가 죽은
    뒤에도** 남는다. 임베드를 버린 이유와 같은 종류의 사고라, 여기서 반드시
    되돌려야 한다.
    """
    try:
        if not win32gui.IsWindow(hwnd):
            return
        win32gui.SetWindowRgn(hwnd, 0, True)
        if crop_top_of(hwnd) <= 0:
            return
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        k = dpi_scale(hwnd)
        full = _gdi32.CreateRectRgn(0, 0, int((r - l) * k) + 2,
                                    int((b - t) * k) + 2)
        win32gui.SetWindowRgn(hwnd, full, True)
    except Exception as e:
        applog.exc("한글 창 잘라내기 해제 실패 — 창이 잘린 채 남을 수 있음", e)


# ── 도킹 소유권 대장 (2026-08-01, 피드백 032) ──────────────
#
# 한글 창의 도킹 주인은 **한 번에 하나**다. 지금까지는 Dock 인스턴스가 둘
# (메인 도킹 · 양식 수정) 살아 있을 수 있었고, 둘이 같은 hwnd 를 서로 제 판
# 자리로 SetWindowPos 해서 한글이 두 자리를 왕복했다 — 사용자가 말한
# 버벅임의 정체다.
#
# 두 호출부(app · palette_ui)는 서로를 모르므로, 둘이 함께 쓰는 이 모듈이
# 심판이 된다. 겹치면 **더 구체적인 작업이 이긴다** (사용자 결정):
# 양식 수정(편집 세션) > 메인 도킹.
PRIORITY_EDIT = 100        # 양식 수정·내용 고치기 (편집 세션)
PRIORITY_MAIN = 10         # 메인 창 도킹 (◫)

_owner_lock = threading.RLock()
_owner_stack = []          # [[dock, priority, on_resume]] — LIFO


def owner():
    """지금 한글 창을 쥐고 있는 Dock (없으면 None)."""
    with _owner_lock:
        return _owner_stack[-1][0] if _owner_stack else None


def owner_priority():
    """지금 주인의 우선순위 (주인이 없으면 0)."""
    with _owner_lock:
        return _owner_stack[-1][1] if _owner_stack else 0


def owner_has_hierarchy():
    r"""지금 주인이 **소유자 위계**로 붙어 있는가 (2026-08-02, 041).

    위계로 붙었으면 우리 창이 '항상 위'가 되어도 한글이 그 위에 남는다 —
    도킹 중 '항상 위' 금지(036)를 풀 수 있는 조건이 이것 하나다.
    구멍 뚫기로 후퇴한 판에서는 옛 문제가 그대로라 False 를 준다.
    """
    d = owner()
    return bool(d is not None and getattr(d, "_owner_set", False))


def claim(dock, priority, on_resume=None):
    r"""이 Dock 이 한글 창의 주인이 된다. 잡았으면 True.

    지금 주인이 **더 높은 우선순위**면 뺏지 않고 False 를 돌려준다 —
    호출부는 안내만 하고 물러난다. 같거나 높으면 지금 주인을 재우고
    (`stop_follow()` 만 — 창 복원은 하지 않는다. 새 주인이 곧바로 이어받으므로
    제자리로 튕겼다 다시 붙는 깜빡임이 없어야 한다) 그 위에 쌓인다.
    """
    with _owner_lock:
        if any(e[0] is dock for e in _owner_stack):
            return True                     # 이미 주인이거나 스택 안에 있다
        if _owner_stack and priority < _owner_stack[-1][1]:
            return False
        if _owner_stack:
            prev = _owner_stack[-1][0]
            for attr, msg in (("stop_follow", "stop_follow"),
                              ("clear_topmost", "clear_topmost"),
                              ("clear_owner", "clear_owner"),
                              ("clear_hole", "clear_hole")):
                try:
                    getattr(prev, attr)()
                except Exception as e:
                    applog.exc(f"도킹 주인 재우기 실패 ({attr})", e)
        _owner_stack.append([dock, priority, on_resume])
        return True


def release(dock):
    r"""주인 자리를 내려놓는다. 스택에 없으면 아무 일도 없다(이중 호출 안전).

    맨 위에서 내려오면 **바로 아래 주인이 깨어난다** — 양식 수정을 끝내면
    메인 도킹이 저절로 돌아오는 길이다.
    """
    with _owner_lock:
        before = len(_owner_stack)
        _owner_stack[:] = [e for e in _owner_stack if e[0] is not dock]
        if len(_owner_stack) == before:
            return                          # 주인이 아니었다
        while _owner_stack:
            nxt, _prio, on_resume = _owner_stack[-1]
            if not win32gui.IsWindow(nxt.hwnd):
                _owner_stack.pop()          # 그새 한글이 닫혔다 — 건너뛴다
                continue
            try:
                if on_resume:
                    on_resume()             # ensure_visible 등 (COM 은 호출부 몫)
                nxt.start()
            except Exception as e:
                applog.exc("이전 도킹 주인 되살리기 실패 — 도킹이 풀린 채 남는다", e)
                _owner_stack.pop()
                continue
            break


def reorder_now():
    r"""지금 주인에게 **창 순서를 다시 잡으라**고 시킨다 (팝오버가 열릴 때 등).

    팝오버는 자체 hwnd 를 가진 창이라 우리 메인 창에 <Activate> 를 주지 않는다
    — 순서를 다시 잡을 계기가 없어 도킹 판이 하얗게 비던 원인이다 (035).
    """
    d = owner()
    if d is not None:
        try:
            d.keep_order(force=True)
        except Exception as e:
            applog.exc("도킹 창 순서 다시 잡기 실패", e)


def _reset_owners_for_test():
    """테스트 전용 — 대장을 비운다."""
    with _owner_lock:
        _owner_stack.clear()


def is_hung(hwnd):
    r"""그 창의 스레드가 '응답 없음'인가 (2026-07-31, ctypes IsHungAppWindow).

    왜 필요한가: SetWindowPos·SetWindowPlacement·SetWindowRgn 은 남의 프로세스
    창에 **동기**로 메시지를 보낸다. 상대(한글)가 멈춰 있으면 그 응답을
    기다리는 우리 스레드도 함께 멈춘다 — Tk 주 스레드에서 그러면 사용자는
    이 프로그램 창을 닫을 수조차 없다. 그래서 그런 호출 앞에서 먼저 물어본다.
    (pywin32 에는 IsHungAppWindow 가 없어 ctypes 로 부른다.)
    """
    try:
        return bool(_user32.IsHungAppWindow(hwnd))
    except Exception:
        return False                    # 판단이 안 되면 원래 하던 대로 진행


def preposition(hwnd, host_widget):
    r"""**숨어 있는** 한글 창을 미리 판 자리로 옮겨 둔다. 성공 여부.

    왜 (사용자 지적 2026-07-28): 숨은 창을 COM 으로 켜면 **옛 자리에서**
    나타난 뒤 도킹으로 끌려와 '엉뚱한 곳에 생겼다가 붙는' 점프가 보였다.
    숨긴 채로 먼저 옮겨 두면 켜지는 순간 이미 제자리다.

    보이는 창은 건드리지 않는다 — 그쪽은 Dock 의 완화 추적이 미끄러지듯
    데려온다 (순간이동보다 눈에 편하다).
    """
    try:
        if not win32gui.IsWindow(hwnd) or win32gui.IsWindowVisible(hwnd):
            return False
        left, top, right, bottom = win32gui.GetWindowRect(host_widget.winfo_id())
        win32gui.SetWindowPos(hwnd, 0, left, top,
                              max(right - left, 200), max(bottom - top, 200),
                              win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
        return True
    except Exception as e:
        applog.exc("한글 창 미리 배치 실패 (도킹이 끌어온다)", e)
        return False


class Dock:
    """한글 창 하나를 Tk 위젯 자리에 붙였다 되돌리는 한 벌.

    start() → (스레드가 실시간으로 따라감) → stop().
    stop 은 몇 번 불려도 안전하고, 한글이 죽어 있으면 조용히 건너뛴다.

    ⚠ start 전에 반드시 `hwp_engine.ensure_visible()` 로 창을 **COM 차원에서**
    먼저 켜 둘 것 (실측 2026-07-28). 숨은 인스턴스를 SetWindowPos 의
    SWP_SHOWWINDOW 로 먼저 보이게 하면 한글 내부는 여전히 '숨김'이라
    렌더러가 꺼진 채 창만 떠서 **통째로 검게** 나온다.
    """

    def __init__(self, toplevel, host_widget, hwnd):
        self.top = toplevel          # 남겨 둔다 — 호출부가 창 수명 판단에 씀
        self.host = host_widget      # 이 위젯 자리에 붙인다 (_zoom_canvas)
        self.hwnd = hwnd
        self._placement = None       # 원복용 — 시작할 때의 창 배치
        self._rect0 = None           # 원복용 — 시작할 때의 실제 사각형
        self._host_hwnd = None
        self._root_hwnd = None       # 우리 최상위 창 — 이벤트 훅이 지켜보는 대상
        self._hook_tid = None        # 훅 스레드의 Win32 스레드 id (WM_QUIT 용)
        self._focus_bind = None      # 우리 창 활성화 → 한글 다시 올리기
        self._hole = None            # 우리 창에 오려 낸 판 자리 (액자 구멍)
        self._owner_set = False      # 소유자 위계를 세웠는가 (041)
        self._owner0 = 0             # 원래 소유자 — 뗄 때 돌려놓는다
        self._hwp_was_topmost = False    # 한글이 원래 '항상 위'였는가
        self._dead = False
        self._stop_evt = threading.Event()
        self._thread = None

    # ── 시작 ─────────────────────────────────────────
    def start(self):
        try:
            if not win32gui.IsWindow(self.hwnd):
                return False
            # 지난번 도킹이 비정상 종료로 남긴 잘라내기가 있으면 먼저 걷어낸다 —
            # 안 그러면 그 위에 또 잘라 리본까지 사라진다 (실측 2026-07-30).
            if crop_top_of(self.hwnd):
                clear_crop(self.hwnd)
            self._placement = win32gui.GetWindowPlacement(self.hwnd)
            # 배치만 저장하면 모니터를 건너간 뒤 원복이 밀린다 (실측 2026-07-30:
            # 뗀 뒤 한글이 163px 옆으로 갔다) — 실제 사각형도 함께 떠 둔다.
            self._rect0 = win32gui.GetWindowRect(self.hwnd)
            # 우리가 '항상 위'로 올렸는지 뗄 때 가리려면 원래 값을 알아야 한다
            self._hwp_was_topmost = bool(
                win32gui.GetWindowLong(self.hwnd, win32con.GWL_EXSTYLE)
                & win32con.WS_EX_TOPMOST)
            # 최대화·최소화 상태면 SetWindowPos 가 안 먹는다 — 먼저 보통으로
            if (self._placement[1] == win32con.SW_SHOWMAXIMIZED
                    or win32gui.IsIconic(self.hwnd)):
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
            # 스레드에서는 Tk 를 못 부른다 — 핸들을 지금(주 스레드) 떠 둔다
            self._host_hwnd = self.host.winfo_id()
            # 훅은 **최상위 창**을 지켜본다 — 판(host)은 자식 창이라 부모가
            # 움직여도 자기 좌표는 그대로여서 LOCATIONCHANGE 가 안 온다.
            self._root_hwnd = win32gui.GetAncestor(self._host_hwnd,
                                                   win32con.GA_ROOT)
            self._hole = None            # 마지막으로 뚫은 구멍 (판의 상대 자리)
            # 위계를 먼저 세워 본다 — 되면 구멍을 안 뚫는다 (2026-08-02, 041).
            if not self._set_owner():
                self._punch_hole()       # 위계 실패 — 옛 길로 후퇴
            self._stop_evt.clear()
            self._thread = threading.Thread(target=self._follow_loop,
                                            daemon=True, name="hwp-dock")
            self._thread.start()
            # 우리 창이 앞으로 나올 때마다 한글을 다시 올린다 (2026-07-30).
            #
            # <Activate> 가 본줄이다 — 창이 활성화되는 순간에 온다. <FocusIn> 은
            # 자식 위젯이 초점을 받을 때는 토플레벨에 오지 않으므로 그것만으로는
            # 새는 경우가 있다. 둘 다 걸어 두고 raise_above 는 여러 번 불려도
            # 무해하게 만들었다.
            self._focus_bind = [
                self.top.bind("<Activate>", lambda e: self.raise_above(),
                              add="+"),
                self.top.bind("<FocusIn>", lambda e: self.raise_above(),
                              add="+"),
            ]
            # 시작할 때는 포그라운드가 무엇이든 **한 번은** 순서를 잡아 둔다 —
            # 이게 없으면 방금 켠 한글이 다른 창 뒤에 깔린 채 시작한다.
            self.keep_order(force=True)
            return True
        except Exception as e:
            applog.exc("한글 창 도킹 실패 — 도킹 없이 계속", e)
            self._placement = None
            # 여기 오기 전에 위계를 세우거나 구멍을 뚫었을 수 있다 — 실패한 채
            # 남기면 한글이 엉뚱한 창을 따라다니거나, 창에 눌리지도 그려지지도
            # 않는 자리가 생긴다 (2026-08-01·02).
            self.clear_topmost()
            self.clear_owner()
            self.clear_hole()
            return False

    def _set_owner(self):
        r"""한글을 우리 창의 **소유 창**으로 삼는다 — 진짜 위계 (2026-08-02, 041).

        사용자 지적: *"저렇게 구멍을 뚫지 않고는 해결을 할 수 없나?
        명확한 위계를 세우면 되는거잖아"* — 맞는 말이었다.

        윈도우에는 창 사이의 위계가 실제로 있다(소유자, GWLP_HWNDPARENT).
        소유 창은 **소유자보다 늘 위에 있도록 윈도우가 지켜 준다** — 우리가
        z 순서를 폴링할 필요도, 판을 오려 낼 필요도 없다.

        실측 (spikes/dock_hierarchy_spike.py):
          · 소유자만 세우면 순서를 안 맞춰도 한글이 위 — keep_order 없이도 지켜짐
          · 우리 창을 '항상 위'로 올려도 한글이 그 위에 남는다
            → 도킹 중 '항상 위' 금지(036)를 풀 수 있는 근거가 이것이다

        왜 안전한가 — 임베드(SetParent)와 무엇이 다른가
        (실측 spikes/owner_survival_spike.py):
          · 소유자를 세운 프로세스를 **정리 없이 즉사**시켜도 한글은 살아남고,
            소유자 값은 저절로 0 으로 풀렸다.
          · 임베드는 달랐다 — 우리 프레임을 부수자 한글의 COM 이 죽고
            (원격 프로시저 호출 실패) 프로세스가 좀비로 남았다
            (spikes/embed_spike.log). 그래서 임베드는 버렸다.
          소유는 **z 순서 관계**일 뿐 창의 부모 자식 관계가 아니다.

        실패하면 False — 호출부가 옛 길(구멍 뚫기)로 후퇴한다. 이 API 는
        다른 프로세스의 창에 쓰는 것을 문서가 권하지 않으므로, 어느 판에서
        막히더라도 도킹 자체는 굴러가야 한다.
        """
        self._owner_set = False
        try:
            if not (self._root_hwnd and win32gui.IsWindow(self.hwnd)):
                return False
            self._owner0 = _user32.GetWindowLongPtrW(self.hwnd,
                                                     _GWLP_HWNDPARENT)
            _user32.SetWindowLongPtrW(self.hwnd, _GWLP_HWNDPARENT,
                                      self._root_hwnd)
            got = _user32.GetWindowLongPtrW(self.hwnd, _GWLP_HWNDPARENT)
            if int(got) != int(self._root_hwnd):
                applog.warn("도킹: 소유자 위계를 세우지 못했다 — 구멍 뚫기로 간다")
                return False
            self._owner_set = True
            return True
        except Exception as e:
            applog.exc("도킹: 소유자 위계 실패 — 구멍 뚫기로 간다", e)
            return False

    def _root_is_topmost(self):
        """우리 창이 지금 '항상 위' 띠에 있는가."""
        try:
            if not self._root_hwnd:
                return False
            ex = win32gui.GetWindowLong(self._root_hwnd, win32con.GWL_EXSTYLE)
            return bool(ex & win32con.WS_EX_TOPMOST)
        except Exception:
            return False

    def clear_topmost(self):
        r"""한글을 '항상 위' 띠에서 내린다 — **우리가 올렸을 때만** (041).

        원래부터 사용자가 한글을 항상 위로 두고 썼다면 그대로 둔다.
        """
        try:
            if self._hwp_was_topmost or not win32gui.IsWindow(self.hwnd):
                return
            win32gui.SetWindowPos(
                self.hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                | win32con.SWP_NOACTIVATE)
        except Exception as e:
            applog.exc("도킹: 한글을 '항상 위'에서 내리지 못했다", e)

    def clear_owner(self):
        r"""소유 관계를 푼다 (도킹을 뗄 때).

        **반드시 0 으로 되돌린다.** 죽은 창을 소유자로 물고 있으면 한글이
        엉뚱한 창을 따라다닌다. 우리가 즉사한 경우엔 윈도우가 알아서 0 으로
        풀어 주지만(실측), 정상 종료에서는 우리가 치우는 것이 맞다.
        """
        if not getattr(self, "_owner_set", False):
            return
        self._owner_set = False
        try:
            if win32gui.IsWindow(self.hwnd):
                _user32.SetWindowLongPtrW(self.hwnd, _GWLP_HWNDPARENT,
                                          self._owner0 or 0)
        except Exception as e:
            applog.exc("도킹: 소유 관계를 풀지 못했다", e)

    def _punch_hole(self):
        r"""우리 창에서 **판 자리를 오려 낸다** — 액자처럼 (2026-07-30 재작성).

        ⚠ 이제는 **후퇴 경로**다 (2026-08-02, 041): 평소에는 _set_owner 의
        위계로 해결되고, 그것이 막히는 판에서만 여기로 온다.

        왜 이렇게까지 하나 (실측 spikes/dock_real_spike.py): 한글을 우리 창
        위로 올리는 방식은 실제 화면에서 계속 밀렸다. 우리 창이 활성 창이면
        윈도우가 그것을 맨 위에 두려 하기 때문에, 순서를 아무리 맞춰도
        (HWND_TOP 이든 상대 순서든) 판이 우리 배경으로 덮였다 — 사용자에게는
        "감쌌다면서 하얗게 비어 있다"로 보였다.

        순서를 다투는 대신 **판을 없앤다**: 그 자리에 창이 아예 없으면 위에
        있어도 가릴 것이 없다. 그림도 안 그리고 마우스도 안 받으므로, 구멍
        아래의 한글이 그대로 보이고 클릭도 한글이 받는다. 도구줄은 구멍 밖에
        있어 늘 보인다.
        """
        try:
            # 위계로 붙어 있으면 **뚫지 않는다** (2026-08-02, 041 — 실측 회귀).
            # 이 함수는 start() 뿐 아니라 따라가는 스레드(_snap)도 매 틱 부른다.
            # start 에서만 안 부르게 막았더니 스레드가 곧바로 다시 뚫어,
            # 위계가 서 있는데도 창이 오려진 채였다 (spikes/dock_e2e_verify.py).
            if self._owner_set:
                return
            if not (self._root_hwnd and win32gui.IsWindow(self._host_hwnd)):
                return
            wl, wt, wr, wb = win32gui.GetWindowRect(self._root_hwnd)
            hl, ht, hr, hb = win32gui.GetWindowRect(self._host_hwnd)
            box = (hl - wl, ht - wt, hr - wl, hb - wt)
            if box == self._hole:
                return                      # 이미 그 자리에 뚫려 있다
            k = dpi_scale(self.hwnd)        # 영역 좌표는 실제 픽셀이다
            full = _gdi32.CreateRectRgn(0, 0, int((wr - wl) * k) + 2,
                                        int((wb - wt) * k) + 2)
            hole = _gdi32.CreateRectRgn(*[int(v * k) for v in box])
            _gdi32.CombineRgn(full, full, hole, _RGN_DIFF)
            _gdi32.DeleteObject(hole)
            win32gui.SetWindowRgn(self._root_hwnd, full, True)
            self._hole = box
        except Exception as e:
            applog.exc("판 자리 오려 내기 실패 — 한글이 판 뒤에 있을 수 있음", e)

    def clear_hole(self):
        """구멍을 메운다 (도킹을 뗄 때) — 안 메우면 창에 빈 구멍이 남는다."""
        self._hole = None
        try:
            if self._root_hwnd and win32gui.IsWindow(self._root_hwnd):
                win32gui.SetWindowRgn(self._root_hwnd, 0, True)
        except Exception as e:
            applog.exc("창 구멍 메우기 실패 — 창 가운데가 뚫린 채 남는다", e)

    def _is_ours(self, fg):
        r"""지금 앞에 나온 창이 **우리 편**인가 — 소유 사슬까지 본다 (035).

        예전에는 핸들 둘(한글·우리 메인 창)과만 비교했다. 그런데 팝오버·
        대화상자·툴팁은 **자체 hwnd 를 가진 창**이라, 그것이 활성이 되는 순간
        '남의 프로그램'으로 읽혀 keep_order 가 그냥 돌아갔다 — 순서를 다시
        잡아 줄 사람이 없어 도킹 판이 하얗게 비었다.

        소유자(owner)를 끝까지 따라가 뿌리가 우리 창이면 우리 편으로 본다.
        한글 쪽도 같은 방식이라 한글의 하위 대화상자까지 덮인다.
        """
        if not fg:
            return False
        mine = tuple(h for h in (self.hwnd, self._root_hwnd) if h)
        if fg in mine:
            return True
        try:
            return win32gui.GetAncestor(fg, win32con.GA_ROOTOWNER) in mine
        except Exception:
            return False

    def keep_order(self, force=False):
        r"""감싸는 동안 **우리 창은 늘 한글 아래**로 깔린다 (사용자 결정 2026-07-30).

            "한글 문서가 아닌 영역을 누르면 바로 한글 파일이 다른 쪽으로 넘어가.
             한글파일을 도킹했을 경우에는 무조건 내 프로그램이 가장 아래에
             깔릴 수 있도록 해야합니다."

        정체: 우리 액자를 누르면 우리 창이 활성화돼 위로 올라오고, 그때 한글은
        다른 프로그램(브라우저 등) 아래로 밀려 구멍에 엉뚱한 창이 비쳤다.

        두 가지를 한 번에 한다 — 한글을 맨 위로, 우리 창을 그 바로 아래로.
        **우리 짝(우리 창 또는 한글)이 활성일 때만** 손댄다: 선생님이 다른
        프로그램을 쓰는 중에 한글을 올리면 남의 창을 가로채는 짓이 된다.
        """
        if self._dead:
            return
        try:
            if not win32gui.IsWindow(self.hwnd):
                return
            # 이 둘은 순서가 생명이라(한글을 올린 **다음** 그 바로 아래에 낀다)
            # 비동기로 못 부친다 — 대신 멈춘 한글이면 손대지 않는다 (2026-07-31).
            # 이 함수는 <Activate>/<FocusIn> 로 Tk 주 스레드에서도 불리므로,
            # 동기 호출이 멈춘 창을 기다리면 앱이 통째로 굳는다.
            if is_hung(self.hwnd):
                return
            if not force and not self._is_ours(win32gui.GetForegroundWindow()):
                return                      # 남의 프로그램을 쓰는 중 — 건드리지 않는다
            flags = (win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                     | win32con.SWP_NOACTIVATE)
            # 한글을 **우리와 같은 띠**로 올린다 (2026-08-02, 041).
            #
            # 윈도우의 z 순서에는 '항상 위' 띠가 따로 있다. 소유 관계는 같은 띠
            # 안에서만 위아래를 지켜 주므로, 우리 창만 그 띠로 올라가면 한글은
            # 아래 띠에 남아 가려진다 — 실측에서 판이 우리 창으로 덮였다
            # (spikes/dock_e2e_verify.py). 우리가 '항상 위'면 한글도 함께 올린다.
            band = (win32con.HWND_TOPMOST if self._root_is_topmost()
                    else win32con.HWND_TOP)
            win32gui.SetWindowPos(self.hwnd, band, 0, 0, 0, 0,
                                  flags | win32con.SWP_SHOWWINDOW)
            if self._root_hwnd:             # 우리 창은 한글 바로 아래로
                win32gui.SetWindowPos(self._root_hwnd, self.hwnd, 0, 0, 0, 0,
                                      flags)
        except Exception as e:
            applog.exc("도킹 창 순서 맞추기 실패 — 판에 남의 창이 비칠 수 있음", e)

    # 예전 이름 — 호출부(양식 수정 도킹)가 그대로 쓴다
    raise_above = keep_order

    # ── 추적 (별도 스레드 — Win32 호출만, Tk·COM 금지) ──
    def _snap(self):
        """한글을 판 자리에 **즉시** 맞춘다. 이미 맞으면 아무것도 안 한다.

        목표 좌표를 부르는 쪽이 아니라 **여기서, 지금** 읽는다 — 이벤트가
        밀려 있어도 늦은 이벤트는 이미 맞는 자리를 확인만 하고 지나간다.
        """
        if self._dead:
            return
        try:
            if not (win32gui.IsWindow(self.hwnd)
                    and win32gui.IsWindow(self._host_hwnd)):
                return
            # 최소화 중이면 판 좌표가 (-32000…) 쓰레기 값이다 — 건드리지 않는다
            if self._root_hwnd and win32gui.IsIconic(self._root_hwnd):
                return
            l, t, r, b = win32gui.GetWindowRect(self._host_hwnd)
            tw, th = max(r - l, 200), max(b - t, 200)
            cl, ct, cr, cb = win32gui.GetWindowRect(self.hwnd)
            if (cl, ct, cr - cl, cb - ct) != (l, t, tw, th):
                win32gui.SetWindowPos(self.hwnd, 0, l, t, tw, th, _MOVE_FLAGS)
            self._punch_hole()               # 창 크기가 바뀌면 구멍도 따라간다
        except Exception:
            pass                         # 창 파괴 경합 등 — 다음 이벤트에 다시

    def _follow_loop(self):
        r"""이벤트 훅으로 따라간다. 훅이 안 잡히면 8ms 폴링으로 물러난다.

        훅(WINEVENT_OUTOFCONTEXT)의 콜백은 **이 스레드의 메시지 펌프 안**에서
        불린다 — 그래서 GetMessage 대신 MsgWaitForMultipleObjects(200ms) +
        PeekMessage 로 펌프를 돌린다. 200ms 타임아웃은 안전망이다: 이벤트를
        놓치는 일이 있어도(모니터 전환 등) 드리프트가 이내 바로잡힌다.
        """
        self._hook_tid = _kernel32.GetCurrentThreadId()

        @_WINEVENTPROC
        def _on_event(_hook, _event, hwnd, obj_id, _child, _tid, _time):
            # 우리 최상위 창의 '창 자체' 이동만 본다 — 커서(OBJID_CURSOR=-9)
            # 이동도 같은 이벤트로 오므로 거르지 않으면 초당 수백 번 스냅한다.
            if hwnd == self._root_hwnd and obj_id == _OBJID_WINDOW:
                self._snap()

        tk_tid = _user32.GetWindowThreadProcessId(self._root_hwnd, None)
        hook = _user32.SetWinEventHook(
            _EVENT_OBJECT_LOCATIONCHANGE, _EVENT_OBJECT_LOCATIONCHANGE,
            0, _on_event, 0, tk_tid, 0)          # 0 = WINEVENT_OUTOFCONTEXT
        if not hook:
            applog.warn("도킹 이벤트 훅 등록 실패 — 8ms 폴링으로 물러남")
            self._poll_fallback()
            return
        self._snap()                             # 시작하자마자 한 번 맞춘다
        try:
            msg = wintypes.MSG()
            while not self._stop_evt.is_set():
                _user32.MsgWaitForMultipleObjects(0, None, False, 200,
                                                  _QS_ALLINPUT)
                while _user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1):
                    _user32.TranslateMessage(ctypes.byref(msg))
                    _user32.DispatchMessageW(ctypes.byref(msg))
                if not (win32gui.IsWindow(self.hwnd)
                        and win32gui.IsWindow(self._host_hwnd)):
                    break
                self._snap()                     # 200ms 안전망 (드리프트 교정)
                self.keep_order()                # 순서도 함께 (우리 짝이 활성일 때만)
        finally:
            _user32.UnhookWinEvent(hook)

    def _poll_fallback(self):
        """훅이 안 잡히는 환경용 — 완화 없이 빠르게 스냅만 반복한다."""
        while not self._stop_evt.is_set():
            if not (win32gui.IsWindow(self.hwnd)
                    and win32gui.IsWindow(self._host_hwnd)):
                break
            self._snap()
            time.sleep(_FALLBACK_TICK_S)

    # ── 멈춤과 원복 (둘로 나뉜 이유: 사이에 '숨기기'가 끼어야 한다) ──
    def stop_follow(self):
        """추적 스레드만 멈춘다. 창은 아직 판 자리에 있다."""
        if self._focus_bind:
            for event, fid in zip(("<Activate>", "<FocusIn>"), self._focus_bind):
                try:
                    self.top.unbind(event, fid)
                except Exception:
                    pass                 # 창이 이미 파괴됐다 — 바인딩도 함께 갔다
            self._focus_bind = None
        self._dead = True
        self._stop_evt.set()
        if self._hook_tid:               # 대기 중인 펌프를 즉시 깨운다
            try:
                _user32.PostThreadMessageW(self._hook_tid, 0x0012, 0, 0)  # WM_QUIT
            except Exception:
                pass
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout=0.5)
        self._hook_tid = None

    def restore(self):
        r"""한글 창을 원래 자리·상태로 되돌린다.

        보이는 창이면 **미끄러지듯** 되돌린다 (2026-07-28, 사용자 지적
        "저장하면 깜빡거린다") — 순간이동은 '창이 튀었다'로 보인다.
        숨겨진 창이면 그냥 배치만 써 둔다 (아무것도 안 보인다).

        (2026-07-31) 활강의 위치 변경은 **동기**로 보낸다 — ASYNC 로 부치면
        한글이 잠깐 바쁜 사이 쌓인 이동 요청이, 뒤이은 SetWindowPlacement 의
        보낸 메시지(sent)보다 **늦게**(posted) 처리돼 확정된 배치를 도로
        중간 지점으로 밀어 버린다. 멈춘 한글에 붙잡히는 문제는 위의
        is_hung 검사가 이미 막는다. 배치가 건드리지 않는 Z순서(NOTOPMOST)
        되돌리기만 ASYNC 로 부친다.
        """
        # 한글이 '응답 없음'이면 원복 전체를 건너뛴다 (2026-07-31): 아래의
        # SetWindowRgn·SetWindowPlacement 는 비동기 선택지가 없는 동기 호출이라,
        # 멈춘 한글을 붙잡으면 Tk 주 스레드가 같이 멈춰 사용자가 이 창을 닫을
        # 수도 없다. 한글이 판 자리에 남는 쪽이 앱이 통째로 굳는 것보다 낫다.
        if win32gui.IsWindow(self.hwnd) and is_hung(self.hwnd):
            applog.warn("한글 창이 응답하지 않아 원복을 건너뜀 — 창이 지금 자리에 남습니다")
            self._placement = None
            return
        # 지난 판이 남긴 잘라내기가 있으면 여기서도 걷어낸다 (무조건, 맨 먼저):
        # placement 가 없다고 먼저 돌아가 버리면 제목줄 없는 한글이 남는다.
        if crop_top_of(self.hwnd):
            clear_crop(self.hwnd)
        placement, self._placement = self._placement, None
        if placement is None:
            return
        try:
            if not win32gui.IsWindow(self.hwnd):
                return
            visible = win32gui.IsWindowVisible(self.hwnd)
            was_maximized = placement[1] == win32con.SW_SHOWMAXIMIZED
            if visible and not was_maximized:
                tl, tt, tr, tb = self._rect0 or placement[4]
                tw, th = tr - tl, tb - tt
                for i in range(8):                   # ~115ms 활강
                    cl, ct, cr, cb = win32gui.GetWindowRect(self.hwnd)
                    dl, dt = tl - cl, tt - ct
                    dw, dh = tw - (cr - cl), th - (cb - ct)
                    if all(abs(v) <= _SNAP_PX for v in (dl, dt, dw, dh)):
                        break
                    # 동기: ASYNC 로 부치면 늦게 처리된 이동이 아래
                    # SetWindowPlacement 확정을 도로 밀어낸다 (docstring 참고)
                    win32gui.SetWindowPos(
                        self.hwnd, 0,
                        cl + int(dl * _EASE), ct + int(dt * _EASE),
                        (cr - cl) + int(dw * _EASE), (cb - ct) + int(dh * _EASE),
                        win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
                    if i < 7:            # 마지막 틱 뒤에는 잘 필요가 없다
                        time.sleep(0.016)
            win32gui.SetWindowPos(
                self.hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                | win32con.SWP_ASYNCWINDOWPOS)
            if not visible:
                # 이미 숨겨진 창이면 **숨긴 채로** 배치만 되돌린다 (실측
                # 2026-07-28): 원래 배치의 showCmd 가 SW_SHOWNORMAL 이라
                # SetWindowPlacement 가 방금 숨긴 창을 도로 보이게 만들어,
                # 저장·취소 뒤 빈 한글 창이 되살아났다.
                placement = (placement[0], win32con.SW_HIDE,
                             placement[2], placement[3], placement[4])
            win32gui.SetWindowPlacement(self.hwnd, placement)
        except Exception as e:
            applog.exc("한글 창 원복 실패 — 창이 판 자리에 남을 수 있음", e)

    def stop(self):
        """추적을 멈추고 되돌린다 — 한 번에 끝내는 기본 경로."""
        self.stop_follow()
        self.clear_topmost()        # 우리가 올렸으면 '항상 위'에서 내린다
        self.clear_owner()          # 위계를 먼저 푼다 (안 풀면 한글이 계속 따라온다)
        self.clear_hole()
        self.restore()
