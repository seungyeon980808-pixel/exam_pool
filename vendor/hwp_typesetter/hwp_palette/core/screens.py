# -*- coding: utf-8 -*-
r"""모니터 범위 — 여러 대를 쓸 때 창이 엉뚱한 화면으로 튀지 않게 (2026-07-26).

무엇이 문제였나 (실측):
    Tk 의 winfo_screenwidth/height 는 **주 모니터 하나**의 크기만 말한다.
    이 PC 는 주 모니터가 1080x1920(세로)이고 4K 모니터가 **왼쪽**에 있어서,
    왼쪽 모니터의 x 좌표는 -3840 부터 시작한다.
    그래서 "화면 밖으로 나가지 않게" 하려고 x 를 0..screenwidth 로 자르면,
    왼쪽 모니터에 떠 있는 창의 팝업 메뉴가 **주 모니터로 순간이동**한다 —
    사용자 눈에는 "눌러도 아무 반응이 없다"로 보인다 (2026-07-26 버그).

여기서는 **모든 모니터를 합친 바탕 화면** 범위를 돌려준다. ctypes 로 묻는
값은 이 프로세스의 DPI 인식 수준을 따르므로 Tk 좌표와 같은 자로 잰 값이다.
"""

from hwp_palette.core import applog

# GetSystemMetrics 인덱스 (winuser.h)
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79


def desktop_bounds(widget):
    """모든 모니터를 합친 범위 (x, y, width, height).

    못 물어보면 주 모니터 크기로 물러선다 (윈도우가 아닌 환경·테스트 대비).
    """
    try:
        import ctypes
        gsm = ctypes.windll.user32.GetSystemMetrics
        x, y = gsm(_SM_XVIRTUALSCREEN), gsm(_SM_YVIRTUALSCREEN)
        w, h = gsm(_SM_CXVIRTUALSCREEN), gsm(_SM_CYVIRTUALSCREEN)
        if w > 0 and h > 0:
            return x, y, w, h
    except Exception as e:
        applog.exc("모니터 범위 조회 실패 — 주 모니터 기준으로 동작", e)
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


def monitor_bounds(widget, x=None, y=None):
    r"""그 자리가 놓인 **모니터 한 대**의 작업 영역 (x, y, width, height).

    왜 desktop_bounds 와 따로 필요한가 (사용자 지적 2026-07-26 — 안내창이
    엉뚱한 곳으로 튀었다):
        이 PC 는 4K 모니터가 **왼쪽**(x = -4800 …)에 있고 주 모니터는
        세로 1350x2400 이다. 합친 바탕 화면은 x = -4800 부터 1350 까지다.
        "오른쪽에 자리가 있나"를 **합친 범위**로 재면, 왼쪽 모니터에 있는 창
        옆에 띄우려 해도 '오른쪽 끝'이 1350(다른 모니터의 끝)이라 늘 자리가
        있다고 나오고, 반대로 주 모니터의 창은 늘 자리가 없다고 나온다.
        그러면 안내창이 모니터 경계를 넘어가거나 반대편으로 튄다.
        옆에 붙일 자리는 **그 창이 있는 모니터 안에서** 재야 한다.

    작업 영역(rcWork)을 쓰므로 작업 표시줄을 덮지 않는다.
    못 물어보면 합친 바탕 화면으로 물러선다(윈도우가 아닌 환경·테스트 대비).
    """
    try:
        import ctypes
        from ctypes import wintypes

        class _RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class _MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                        ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]

        if x is None or y is None:
            x = widget.winfo_rootx() + widget.winfo_width() // 2
            y = widget.winfo_rooty() + widget.winfo_height() // 2
        user32 = ctypes.windll.user32
        pt = wintypes.POINT(int(x), int(y))
        hmon = user32.MonitorFromPoint(pt, 2)       # 2 = 가장 가까운 모니터
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        if hmon and user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcWork
            if r.right > r.left and r.bottom > r.top:
                return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception as e:
        applog.exc("모니터 한 대 범위 조회 실패 — 합친 바탕 화면 기준으로 동작", e)
    return desktop_bounds(widget)


def clamp_window(widget, x, y, w, h):
    """(x, y) 를 바탕 화면 안으로 밀어 넣는다. 모니터를 가로질러도 안전하다."""
    dx, dy, dw, dh = desktop_bounds(widget)
    x = max(dx, min(int(x), dx + dw - int(w)))
    y = max(dy, min(int(y), dy + dh - int(h)))
    return x, y


def fits_below(widget, y, h):
    """그 자리에 높이 h 짜리가 아래로 다 들어가는가 (아니면 위로 펼쳐야 한다)."""
    _dx, dy, _dw, dh = desktop_bounds(widget)
    return y + h <= dy + dh


def place_beside(win, master, gap=10, follow=True):
    r"""새 창을 **메인 창 바로 옆**에 붙여 연다 (사용자 결정 2026-07-26).

    창마다 +40+30, -320, -660 처럼 제각각 자리를 잡고 있었다. 그러면 어떤
    창은 화면 왼쪽 끝에, 어떤 창은 메인 창 위에 겹쳐 떠서 "방금 뭐가 열렸지"를
    눈으로 찾아야 했다. 규칙을 하나로 한다 —
      · 기본은 메인 창 **오른쪽 바로 옆**, 위쪽 줄을 맞춰서
      · 오른쪽에 자리가 없으면 왼쪽 옆
      · 그것도 없으면 화면 안으로 밀어 넣는다
    follow=True 면 부모를 따라 최소화되고 늘 부모 위에 뜬다(transient).
    """
    try:
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        # **창틀 기준**으로 계산한다 (2026-07-26).
        # winfo_rootx/y 는 제목표시줄·테두리를 뺀 '내용의 왼쪽 위'라, 그 값으로
        # 자리를 잡으면 새 창이 부모보다 30px 쯤 내려가 어긋나 보인다.
        # geometry() 가 돌려주는 좌표가 창틀 기준이므로 그것을 쓴다.
        mgeo = master.geometry()                     # 예: "660x600+40+120"
        pos = mgeo.split("+")
        mfx, mfy = int(pos[1]), int(pos[2])
        border = max(0, master.winfo_rootx() - mfx)  # 좌우 테두리 두께
        # 자리는 **부모 창이 있는 모니터 안에서** 잰다 (2026-07-26).
        # 합친 바탕 화면으로 재면 모니터 사이의 빈 구간(이 PC 는 -1646~0)에
        # 창을 놓아 버려 "엉뚱한 곳에 떴다"가 된다.
        dx, dy, dw, dh = monitor_bounds(master)
        x = mfx + master.winfo_width() + 2 * border + gap
        if x + w > dx + dw:                 # 오른쪽에 자리가 없으면 왼쪽으로
            x = mfx - w - gap
        x = max(dx, min(int(x), dx + dw - w))
        y = max(dy, min(int(mfy), dy + dh - h))
        win.geometry(f"+{x}+{y}")
        if follow:
            try:
                win.transient(master)
            except Exception:
                pass
    except Exception as e:
        applog.exc("창 자리 잡기 실패 — 시스템 기본 자리에 둔다", e)


def is_on_desktop(widget, x, y, margin=100):
    """그 위치가 지금 붙어 있는 모니터들 안인가 (모니터를 뺐을 때 창 실종 방지)."""
    dx, dy, dw, dh = desktop_bounds(widget)
    return (dx - 50 <= x <= dx + dw - margin
            and dy - 20 <= y <= dy + dh - 80)
