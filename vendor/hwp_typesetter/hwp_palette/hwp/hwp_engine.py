# -*- coding: utf-8 -*-
"""한컴 자동화(pyhwpx) 코어 — 연결·문서·선택·글꼴·표 생성·찾기.

Tkinter/UI에 의존하지 않는다. 표/박스의 모든 치수·글꼴·테두리는 활성 스펙(S)에서
읽는다. S는 settings.py의 프리셋에서 온다. 실패는 예외로 올라간다.
메시지박스/상태표시는 호출부의 책임.

모듈 경계 (개선안 19 — 2026-07-18 분할):
  hwp_engine     (이 파일) 한글을 다루는 원시 동작. 다른 엔진이 공유하는 토대.
  exam_engine    시험문제 조판 (발문·자료박스·보기박스·선지 표).
  engine_library 라이브러리(서식/템플릿/양식) 캡처·적용, 팔레트 블럭 실행,
                 \\라벨\\ 마크다운 변환 실행.
연결 인스턴스(hwp)는 이 모듈이 소유하고, 나머지는 `hwp_engine.hwp` 로 참조한다
— `from hwp_engine import hwp` 로 가져오면 재연결 시 낡은 객체를 붙들게 된다.

지역 import 규칙 (개선안 24):
  함수 안에서 import 하는 경우는 **세 가지뿐**이다.
    1) 순환 참조 회피 — library ↔ palette 처럼 서로를 필요로 하는 경우
    2) 플랫폼/선택적 의존성 — win32gui, win32clipboard 처럼 없을 수도 있고
       ImportError 를 그 자리에서 다뤄야 하는 경우
    3) 시작 시간 — pyhwpx 처럼 import 자체가 수 초 걸리는 무거운 의존성.
       아래 _ensure_pyhwpx() 참조.
  그 외(표준 라이브러리 등)는 전부 파일 맨 위로 올린다.
"""

import os
import re
import time

from hwp_palette.core import applog
from hwp_palette.core import clipboard                        # 윈도우 클립보드 (Tk 클립보드 금지)
from hwp_palette.core import settings

hwp = None

# pyhwpx 는 여기서 import 하지 않는다 — _ensure_pyhwpx() 가 첫 연결 때 채운다.
# 테스트가 mock.patch.object(hwp_engine, "Hwp", ...) 로 갈아끼우므로
# 모듈 속성 자체는 항상 있어야 한다 (None 이 '아직 안 불러옴' 표시).
Hwp = None
pyhwpx_core = None                    # __init__ 우회 시 필요한 기본값(fonts)


def _ensure_pyhwpx():
    r"""pyhwpx 를 실제로 쓰는 순간에야 불러온다 (지역 import 규칙 3).

    왜 미루나 (실측 2026-07-31): 앱 시작 4.4초 중 **3.8초가 이 import** 였다
    — pyhwpx 가 pandas(1.4초)와 한/글 typelib gencache(1.3초)를 끌고 온다.
    모듈 최상단 import 는 그 비용을 **창이 뜨기도 전에** 내게 하므로,
    첫 연결(connect / _attach_without_resize) 순간으로 옮겼다. 사용자는
    어차피 한글에 붙는 첫 동작에서 잠깐 기다리는데, 거기에 합쳐지면
    체감이 거의 없다.

    테스트가 Hwp 를 가짜로 꽂아 둔 상태면(None 이 아님) 아무것도 안 한다
    — 진짜 pyhwpx 를 불러 가짜를 덮어쓰면 안 된다.
    """
    global Hwp, pyhwpx_core
    if Hwp is not None:
        return
    from pyhwpx import Hwp as _Hwp
    import pyhwpx.core as _core
    Hwp = _Hwp
    pyhwpx_core = _core


# 활성 스펙(프리셋). main.py가 시작 시 set_active_spec()으로 주입한다.
S = settings.default_spec()


def set_active_spec(spec):
    """설정 창에서 프리셋을 바꾸거나 저장하면 호출된다."""
    global S
    S = spec


# ── 진단 로거 (창 상태 추적용. 평소엔 꺼둠 — 문제 재현이 필요할 때만 True) ──
DIAG = False
_DIAG_PATH = None


def _diag(tag):
    """현재 한글 창 상태를 파일에 기록. 창을 바꾸는 범인을 찾기 위한 임시 도구."""
    if not DIAG:
        return
    global _DIAG_PATH
    try:
        import win32gui           # 플랫폼 의존 — 없을 수 있어 지역 import
        if _DIAG_PATH is None:
            from hwp_palette.core import paths      # exe 로 묶으면 __file__ 은 지워지는 임시 폴더다
            _DIAG_PATH = str(paths.DATA_DIR / "window_diag.log")
            with open(_DIAG_PATH, "w", encoding="utf-8") as f:
                f.write("=== 창 상태 추적 시작 ===\n")
        lines = []
        for h in _hwp_window_handles():
            pl = win32gui.GetWindowPlacement(h)
            rc = win32gui.GetWindowRect(h)
            state = {1: "보통", 2: "최소", 3: "최대"}.get(pl[1], pl[1])
            lines.append(f"{state} {rc[2]-rc[0]}x{rc[3]-rc[1]}")
        with open(_DIAG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{tag}] {' | '.join(lines) if lines else '(창 없음)'}\n")
    except Exception as e:
        applog.exc("진단 로그 기록 실패", e)


def _hwp_window_handles(include_hidden=False):
    """현재 떠 있는 한글 창 핸들 목록 (기본은 보이는 창만)."""
    try:
        import win32gui
    except ImportError:
        return []
    found = []

    def _cb(hwnd, _):
        try:
            # 클래스명은 'HwndWrapper[Hwp.exe;;...]' — 대소문자가 환경마다 다르므로
            # 반드시 소문자로 비교한다 (실측: Hwp.exe 로 나와 매칭 실패했던 버그)
            if ((include_hidden or win32gui.IsWindowVisible(hwnd))
                    and "hwp.exe" in win32gui.GetClassName(hwnd).lower()):
                found.append(hwnd)
        except Exception as e:
            applog.exc(f"창 정보 조회 실패 (hwnd={hwnd})", e)
    try:
        win32gui.EnumWindows(_cb, None)
    except Exception as e:
        applog.exc("창 목록 열거 실패", e)
        return []
    return found


def is_connected_cheap():
    r"""상태 표시등용 어림 판정 — **COM 을 건드리지 않는다** (2026-07-28).

    표시등은 3초마다 갱신되는데, 여태 is_connected(COM 왕복)를 썼다.
    COM 은 한글이 바쁠 때(모달 대화상자·인쇄·큰 문서 저장) 응답할 때까지
    **우리 UI 스레드를 통째로 붙들어**, 아무것도 안 해도 3초마다 앱이
    걸리는 잰크가 됐다. 창 열거는 우리 프로세스 안에서 끝나 즉시 돌아온다.

    어림이므로 한글이 '떠 있지만 연결이 죽은' 경우를 놓칠 수 있다 — 그건
    다음 실제 조작(변환 등)의 connect() 가 바로잡고, 표시등은 곁눈 정보라
    몇 초 늦어도 된다 (main.py 의 3초 주기 설명과 같은 논리).
    """
    if hwp is None:
        return False
    return bool(_hwp_window_handles(include_hidden=True))


def _active_window_com():
    """연결된 인스턴스의 활성 창 COM 객체. 없으면 None."""
    if hwp is None:
        return None
    try:
        return hwp.hwp.XHwpWindows.Active_XHwpWindow
    except Exception:
        return None


def ensure_visible():
    r"""연결된 한글 **인스턴스**의 창을 화면에 보이게 한다. 성공 여부.

    왜 필요한가 (실측 2026-07-27, "양식 고치기를 눌러도 한글이 안 뜬다"):
    한글은 COM 자동화(다른 프로그램·이전 작업)가 띄워 놓은 **숨은 인스턴스**를
    ROT 에 남겨 둘 수 있다. connect() 는 창을 안 건드리려고 생성자를 우회해
    실행 중인 인스턴스에 붙는데(_attach_without_resize), 그것이 숨은
    인스턴스면 Visible 을 아무도 안 켜 줘서 — 문서를 열어도 **화면에 아무것도
    나타나지 않는다.** 창 목록(_hwp_window_handles)은 IsWindowVisible 로
    거르므로 bring_to_front 도 빈손이었다.

    이미 보이는 창이면 아무것도 안 건드린다 — Visible 대입은 최대화를 풀어
    버리는 부작용이 있어(connect 설명 참고) 숨어 있을 때만 켠다.
    """
    win = _active_window_com()
    if win is not None:
        try:
            if not win.Visible:
                win.Visible = True
                applog.warn("숨어 있던 한글 창을 보이게 켰습니다 "
                            "(숨은 COM 인스턴스에 연결돼 있었음)")
        except Exception as e:
            applog.exc("한글 창 보이기(Visible) 실패", e)
    return bool(_hwp_window_handles())


def visible_window_handles():
    r"""지금 화면에 보이는 한글 창 핸들 집합.

    **연결하기 전에도 잴 수 있다** — 창 클래스명으로 훑을 뿐 COM 을 쓰지
    않기 때문. '고치기'에 들어가기 전에 재 두면, 끝난 뒤 우리가 띄우거나
    켠 창인지 핸들로 정확히 가릴 수 있다.

    왜 `window_is_visible()` 로는 부족한가 (실측 2026-07-27, 사용자 지적
    "한글 창이 없는 상태에서도 빈 창이 안 사라진다"): 그 함수는 **연결된**
    인스턴스를 보므로 connect() 뒤에야 쓸 수 있다. 그런데 한글이 아예 없으면
    connect() 가 한글을 **새로 띄우고**, 그 창은 처음부터 보이는 상태라
    "원래 보이던 창"으로 오인돼 정리 대상에서 빠졌다.
    """
    return set(_hwp_window_handles())


def connected_hwnd():
    """연결된 인스턴스의 창 핸들 (없으면 None). 창 정리 판단에 쓴다."""
    return _connected_hwnd()


def window_is_visible():
    """연결된 인스턴스의 창이 지금 화면에 보이는가."""
    win = _active_window_com()
    if win is None:
        return False
    try:
        return bool(win.Visible)
    except Exception as e:
        applog.exc("한글 창 표시 여부 확인 실패 — 보인다고 본다", e)
        return True             # 모르면 건드리지 않는 쪽이 안전하다


def set_window_visible(on):
    """연결된 인스턴스의 창을 켜거나 끈다. 반환: 실제로 바꿨는가."""
    win = _active_window_com()
    if win is None:
        return False
    try:
        if bool(win.Visible) == bool(on):
            return False        # 이미 그 상태 — Visible 대입은 부작용이 있다
        win.Visible = bool(on)
        return True
    except Exception as e:
        applog.exc(f"한글 창 표시 전환 실패 (on={on})", e)
        return False


def _connected_hwnd():
    """연결된 인스턴스의 창 핸들. 모르면 보이는 아무 한글 창, 없으면 None.

    한글은 인스턴스가 여럿일 수 있다(사용자가 쓰는 창 + 자동화가 숨겨 둔 것).
    문서는 '연결된 인스턴스'에 열리므로 앞으로 끌어올 창도 그 인스턴스여야
    한다 — 아무 한글 창이나 올리면 문서 없는 창이 올라온다.
    """
    win = _active_window_com()
    if win is not None:
        try:
            h = int(win.WindowHandle)
            if h:
                return h
        except Exception:
            pass                    # WindowHandle 이 없는 버전 — 열거로 대신
    handles = _hwp_window_handles()
    if not handles:
        return None
    if len(handles) > 1:
        applog.warn(f"한글 창이 {len(handles)}개 발견됨 — 첫 번째 창을 선택합니다")
    return handles[0]


def bring_to_front():
    """한글 창을 앞으로 끌어온다. 성공 여부.

    양식·템플릿을 '꺼내서 고치기' 할 때 필요하다 (사용자 지적 2026-07-27) —
    한글에 문서를 펼쳐 놨는데 창이 우리 창 뒤에 있으면, 사용자는 무엇을
    고치라는 것인지 모른 채 안내 창만 보게 된다.
    """
    ensure_visible()                # 숨은 인스턴스였다면 먼저 창부터 켠다
    hwnd = _connected_hwnd()
    if hwnd is None:
        applog.warn("bring_to_front: 보이는 한글 창이 없습니다")
        return False
    try:
        import win32gui
        import win32con
    except ImportError:
        return False
    try:
        if win32gui.IsIconic(hwnd):          # 최소화돼 있으면 먼저 편다
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    except Exception as e:
        applog.exc("한글 창 펴기 실패", e)
    try:
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        pass
    # 윈도우는 '지금 앞에 있는 앱'이 아니면 SetForegroundWindow 를 거절한다.
    # ALT 키를 잠깐 눌렀다 떼면 그 잠금이 풀린다 (널리 쓰이는 우회로).
    try:
        import win32api
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        try:
            win32gui.SetForegroundWindow(hwnd)
        finally:
            win32api.keybd_event(win32con.VK_MENU, 0,
                                 win32con.KEYEVENTF_KEYUP, 0)
        return True
    except Exception as e:
        applog.exc("한글 창을 앞으로 가져오지 못했습니다 (ALT 우회 포함)", e)
    # 마지막 수단 — z순서만이라도 끌어올린다 (초점은 못 받아도 눈에는 보인다)
    try:
        flags = (win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                 | win32con.SWP_SHOWWINDOW)
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
        win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        return True
    except Exception as e:
        applog.exc("한글 창 z순서 올리기 실패", e)
        return False


def _connection_error(h):
    r"""연결이 살아 있으면 None, 죽었으면 그 예외를 돌려준다.

    **pyhwpx 의 `hwp.Version` 프로퍼티를 쓰면 안 된다** (실측 2026-07-19):
        return [int(i) for i in self.hwp.Version.split(", ")]
    보다시피 문자열을 파싱한다. 한글이 멀쩡히 살아 있어도 버전 표기가
    `"13, 0, 0, 2151"` 꼴이 아니면 여기서 ValueError 가 난다.

    그걸 '연결이 죽었다'로 오판하면 **변환할 때마다 재연결**하게 되고,
    재연결은 pyhwpx 생성자의 `Visible` 대입 때문에 최대화된 창을 보통 크기로
    되돌린다. 우리가 곧바로 복원하므로 결과는 **"변환을 누르면 창이 작아졌다
    다시 커지는"** 증상이 된다. 실제로 그 버그가 났다.

    그래서 파싱하지 않는 원시 COM 값을 한 번 건드려 살아 있는지만 본다.
    """
    if h is None:
        return ValueError("아직 연결된 적이 없음")
    try:
        _ = h.hwp.Version       # 원시 COM 값 — 파싱하지 않는다
        return None
    except Exception as e:
        return e


def _running_hwp_com():
    """이미 실행 중인 한글의 COM 객체. 없으면 None. (한글을 새로 띄우지 않는다)

    인스턴스가 여럿이면 **창이 보이는 것**을 우선한다 (2026-07-27) — 다른
    자동화 도구가 숨겨 놓은 인스턴스에 붙으면, 사용자가 보고 있는 한글이
    아니라 숨은 창에 문서가 열려 "양식 고치기를 눌러도 아무것도 안 뜬다"가
    된다. 보이는 것이 하나도 없을 때만 숨은 인스턴스를 쓴다 (그 경우는
    ensure_visible 이 창을 켠다).
    """
    import pythoncom
    import win32com.client as win32
    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    except Exception:
        try:
            if pythoncom.CoInitialize() == 0:  # S_OK = first init
                pass
        except Exception:
            pass  # RPC_E_CHANGED_MODE — already initialized by another caller
    ctx = pythoncom.CreateBindCtx(0)
    rot = pythoncom.GetRunningObjectTable()
    hidden = None
    for moniker in rot.EnumRunning():
        if not moniker.GetDisplayName(ctx, moniker).startswith("!HwpObject."):
            continue
        obj = rot.GetObject(moniker)
        com = win32.gencache.EnsureDispatch(
            obj.QueryInterface(pythoncom.IID_IDispatch))
        try:
            visible = bool(com.XHwpWindows.Active_XHwpWindow.Visible)
        except Exception:
            return com              # 보임 여부를 못 물으면 예전처럼 첫 것
        if visible:
            return com
        if hidden is None:
            hidden = com
    return hidden


def _attach_without_resize():
    r"""이미 떠 있는 한글에 **창을 건드리지 않고** 붙는다. 못 하면 None.

    왜 이렇게까지 하나 (실측 2026-07-19):
      pyhwpx 의 Hwp() 생성자는 무조건
          XHwpWindows.Active_XHwpWindow.Visible = visible
      을 실행하는데, 이 대입이 **최대화된 창을 보통 크기로 되돌린다**
      (측정값: 최대 1094x1934 → 보통 1080x802).
      예전엔 창 배치를 저장했다 복원하는 것으로 막으려 했지만, 그건 '되돌리기'라
      사용자 눈에는 여전히 **작아졌다 다시 커지는 깜빡임**으로 보인다.
      → 애초에 생성자를 거치지 않는다.

    pyhwpx.Hwp.__init__ 이 self 에 세팅하는 것은 hwp / on_quit / htf_fonts 세 개뿐이라
    (2026-07-19 확인) 그것만 채워 넣으면 나머지 메서드는 그대로 동작한다.
    pyhwpx 가 올라가면서 필드가 늘어날 수 있으므로, 채운 뒤 실제로 쓸 수 있는지
    확인하고 아니면 None 을 돌려 정상 경로로 넘긴다.
    """
    try:
        com = _running_hwp_com()
        if com is None:
            return None                 # 한글이 안 떠 있음 — 새로 실행해야 한다
        _ensure_pyhwpx()                # 첫 연결이면 여기서야 pyhwpx 를 불러온다
        h = Hwp.__new__(Hwp)            # __init__ 을 건너뛴다 (Visible 대입 회피)
        h.hwp = com
        h.on_quit = False
        h.htf_fonts = pyhwpx_core.fonts
        _ = h.hwp.Version               # 실제로 말이 통하는지 확인
        try:
            h.register_module()         # 보안 모듈 등록 (파일 열기/저장에 필요)
        except Exception as e:
            applog.exc("보안 모듈 등록 실패 — 파일 접근 시 확인창이 뜰 수 있음", e)
        return h
    except Exception as e:
        applog.exc("창 보존 연결 실패 — 일반 연결로 넘어감(창이 한 번 깜빡일 수 있음)", e)
        return None


def is_connected():
    """지금 한글에 붙어 있는가 (표시등용). 연결을 새로 만들지 않는다.

    주기적으로 부를 것이므로 **절대 무거워지면 안 된다** — 새 연결을 시도하거나
    문서를 건드리면 사용자가 타자를 치는 중에 한글을 방해한다. 이미 잡아 둔
    객체의 원시 COM 값을 한 번 읽어 보는 것으로 끝낸다.
    """
    return _connection_error(hwp) is None


def connect():
    """이미 연결돼 있으면 재사용, 아니면 새로 연결. 실패 시 예외 발생.

    창 크기가 변하지 않게 하는 순서 (실측 2026-07-19):
      1) 이미 붙어 있으면 그대로 재사용 — 아무것도 안 건드린다
      2) 한글이 떠 있으면 생성자를 거치지 않고 붙는다(_attach_without_resize)
      3) 한글이 아예 없을 때만 Hwp() 로 새로 실행 — 이때는 보존할 최대화 상태가
         없으므로 창이 줄어들 일도 없다
    2번이 실패할 때만 옛 방식(배치 저장 → Hwp() → 복원)으로 넘어간다. 그 경우엔
    창이 한 번 깜빡이므로, 왜 그랬는지 app.log 에 남는다.
    """
    global hwp
    err = _connection_error(hwp)
    if err is None:
        _diag("connect: 기존 연결 재사용")
        return hwp                  # ← 평소엔 여기서 끝. 창을 건드리지 않는다.

    if hwp is not None:
        applog.warn(f"connect: 연결이 끊어져 새로 연결 — {type(err).__name__}: {err}")

    attached = _attach_without_resize()
    if attached is not None:
        hwp = attached
        _diag("connect: 창 보존 연결 성공")
        return hwp

    _diag("connect: 재연결 직전")
    try:
        import win32gui
        saved = [(h, win32gui.GetWindowPlacement(h)) for h in _hwp_window_handles()]
    except Exception as e:
        applog.exc("창 배치 저장 실패 — 최대화가 풀릴 수 있음", e)
        saved = []

    _ensure_pyhwpx()                    # 첫 연결이면 여기서야 pyhwpx 를 불러온다
    hwp = Hwp()
    _diag("connect: Hwp() 생성 직후")

    for handle, placement in saved:
        try:
            win32gui.SetWindowPlacement(handle, placement)
        except Exception as e:
            applog.exc(f"창 배치 복원 실패 (handle={handle})", e)
    if not saved:
        # 실제로 겪은 버그: 클래스명 대소문자 오타로 창을 0개로 봐서
        # 복원 로직이 통째로 무동작이었는데 아무 소리도 안 났었다.
        applog.warn("connect: 복원할 한글 창을 찾지 못함 "
                    "(한글이 새로 실행된 경우면 정상)")
    _diag(f"connect: 복원 시도 후 (저장했던 창 {len(saved)}개)")
    return hwp


# ── 문서/선택 ─────────────────────────────────────────
def new_document():
    hwp.HAction.Run("FileNew")


def has_selection():
    r"""지금 한글에 블록(선택)이 잡혀 있는가.

    SelectionMode 만 믿지 않는다 (2026-07-26): 이 값이 0 이어도 선택 내용은
    멀쩡히 읽히는 경우가 있어, "선택하세요" 만 반복하며 막히는 일이 있었다.
    그래서 0 일 때는 **선택 내용을 한 번 더 물어본다** — 선택이 없으면 한글이
    빈손을 주므로 판정이 틀리지 않는다. 읽는 쪽(read_selection_text)과 같은
    기준을 쓰게 되어, '변환은 되는데 다른 버튼은 선택이 없다고 한다' 같은
    엇갈림도 생기지 않는다.
    """
    try:
        if hwp.SelectionMode != 0:
            return True
    except Exception as e:
        applog.exc("선택 상태 조회 실패", e)
    return bool(read_selection_direct())


def copy_selection():
    hwp.HAction.Run("Copy")


# 이름 엔티티 — 숫자형과 함께 한 번의 패스로 푼다 (아래 _unescape_entities 참조)
_NAMED_ENTITIES = {"lt": "<", "gt": ">", "quot": "\"", "apos": "'", "amp": "&"}

_ENTITY_RE = re.compile(r"&(#[xX][0-9A-Fa-f]+|#[0-9]+|lt|gt|quot|apos|amp);")


def _entity_char(m):
    """엔티티 하나 → 글자. 범위를 벗어난 숫자 코드는 원문 그대로 둔다."""
    body = m.group(1)
    if body[:2] in ("#x", "#X"):
        try:
            return chr(int(body[2:], 16))
        except (ValueError, OverflowError):
            return m.group(0)
    if body[0] == "#":
        try:
            return chr(int(body[1:], 10))
        except (ValueError, OverflowError):
            return m.group(0)
    return _NAMED_ENTITIES[body]


def _unescape_entities(text):
    r"""한글 TEXT 내보내기가 남긴 &#8212; 꼴 엔티티를 글자로 되돌린다.

    실측 (2026-07-26): GetTextFile("TEXT", ...) 은 줄표(—) 같은 일부 문자를
    `&#8212;` 로 바꿔서 준다 (클립보드 경유는 원문 그대로). 그대로 두면
    — 가 든 줄을 변환할 때 엉뚱한 글자가 문서에 들어가고, 제자리 치환은
    바꿀 자리를 못 찾는다. 사용자가 문서에 진짜로 `&#8212;` 라고 칠 가능성은
    사실상 없으므로 일괄 복원한다.

    범위 (2026-07-31 안전 감사에서 확장): 십진(&#8212;)만 다루던 것을
    십육진(&#x2014;)과 이름 다섯 개(&lt; &gt; &quot; &apos; &amp;)까지 다룬다.
    **한 번의 정규식 패스**로 푼다 — 단계별로 풀면 이미 푼 결과가 다음
    단계에 다시 걸려 `&#38;lt;` 가 `&lt;` 를 거쳐 `<` 까지 두 번 풀린다.
    한 패스에서는 치환된 출력이 재검사되지 않으므로 이중 해석이 없다.
    """
    if "&" not in text:
        return text
    return _ENTITY_RE.sub(_entity_char, text)


def read_selection_direct():
    r"""선택 영역을 **클립보드를 거치지 않고** 한글에서 바로 읽는다.

    GetTextFile("TEXT", "saveblock") = 지금 선택된 부분만 글자로 돌려준다.
    선택이 없으면 한글이 None 을 주므로 **이것 자체가 선택 여부 판정**이다 —
    SelectionMode 를 먼저 물어보지 않는다(실측 2026-07-26: 한 번 더 물어보는
    관문이 늘 뿐, 없어도 결과가 같다).
    클립보드를 안 건드리므로 사용자가 복사해 둔 것을 지우지도 않고, 다른
    프로그램이 클립보드를 점유해도 영향을 받지 않는다.
    """
    try:
        return _unescape_entities(hwp.GetTextFile("TEXT", "saveblock") or "")
    except Exception as e:
        applog.exc("선택 영역 직접 읽기 실패 — 클립보드로 넘어감", e)
        return ""


def read_selection_text(retries=10, delay=0.08):
    r"""선택 영역의 글자를 읽는다.

    순서가 **뒤집혔다** (2026-07-26 — 원인을 실측으로 잡은 뒤):
      1) 한글에서 직접 (GetTextFile saveblock) — 클립보드를 아예 안 건드린다
      2) 그게 빈손일 때만 클립보드 경유 (Copy → 윈도우 클립보드)

    예전에는 1·2 가 반대였다. 그런데 클립보드는 **우리 자신이 잠글 수 있다**:
    Tk 의 clipboard_append 는 값을 넣는 게 아니라 '주인 등록'(지연 렌더링)이라,
    그 뒤 같은 프로세스의 OpenClipboard 가 '액세스가 거부되었습니다' 로 막힌다
    (실측). 튜토리얼 [복사] 를 누른 뒤 변환이 "선택 없음" 이 되던 바로 그 길이다.
    담는 쪽은 clipboard.py 로 고쳤지만, **읽는 쪽도 클립보드에 의존하지 않는
    것이 근본 해결**이다 — 선택 내용은 한글이 직접 준다.

    2번을 남겨 두는 이유: 표처럼 saveblock 이 빈손인 선택이 있을 수 있어서다.
    이때 '선택이 없으면 클립보드를 읽지 않는' 관문은 그대로 지킨다 —
    Copy 는 선택이 없으면 아무 일도 안 하는데 그 뒤 클립보드를 읽으면
    **직전에 복사해 둔 남의 글**이 선택 내용으로 둔갑했다(실측 로그의
    "바꿀 자리를 찾지 못해 건너뜀" 반복).

    Copy 성공의 증명 (2026-07-31 안전 감사): SelectionMode 관문만으로는
    부족하다 — Copy 가 실제로 클립보드를 채웠다는 보장이 없으면, 예전에
    복사해 둔 낡은 내용이 '선택'으로 둔갑해 진짜 선택이 그 내용으로 바뀐다.
    그래서 Copy 전에 클립보드 **순번**을 재 두고, 순번이 움직였을 때만
    클립보드를 읽는다. 순번을 못 재거나 안 움직였으면 선택 없음으로 본다.
    """
    direct = read_selection_direct()
    if direct:
        return direct
    # 여기서 has_selection() 을 부르면 방금 한 직접 읽기를 또 한다 —
    # 이 자리에서는 SelectionMode 만 보는 것으로 충분하다.
    try:
        if hwp.SelectionMode == 0:
            return ""
    except Exception as e:
        applog.exc("선택 상태 조회 실패", e)
        return ""
    seq_before = clipboard.sequence_number()
    if seq_before is None:
        # Copy 가 실제로 담겼는지 증명할 길이 없다 — 낡은 클립보드 내용을
        # 선택으로 둔갑시키느니 선택 없음으로 처리한다.
        applog.warn("클립보드 순번을 읽지 못해 클립보드 경유 읽기를 건너뜁니다")
        return ""
    copy_selection()
    advanced = False
    for _ in range(20):                 # Copy 반영 대기 — 최대 약 1초
        seq_now = clipboard.sequence_number()
        if seq_now is not None and seq_now != seq_before:
            advanced = True
            break
        time.sleep(0.05)
    if not advanced:
        # Copy 가 클립보드를 채우지 못했다(선택이 없거나 실패) — 지금
        # 클립보드에 있는 것은 예전 내용이므로 읽지 않는다.
        applog.warn("Copy 후 클립보드 순번이 그대로라 선택 없음으로 봅니다")
        return ""
    text = clipboard.get_text(retries=retries, delay=delay)
    if text:
        applog.warn("한글이 선택 내용을 직접 주지 않아 클립보드로 읽었습니다 "
                    "(변환은 정상 진행됩니다)")
        return text
    # 여기까지 왔으면 두 길이 모두 막혔다. 다음에 또 겪을 때 원인을 알 수 있게
    # 그때의 한글 상태를 남긴다 (예전엔 아무 기록 없이 "선택 없음" 만 떴다).
    try:
        applog.warn(f"선택을 읽지 못했습니다 — SelectionMode="
                    f"{hwp.SelectionMode}, 문서 수={hwp.XHwpDocuments.Count}")
    except Exception:
        pass
    return ""


# ── 되돌리기 (2026-07-28, 안전판 2026-07-31) ──────────
# 왜 필요한가: 변환 한 번, 템플릿 삽입 한 번은 한글 입장에서 **동작 수십 개**다.
# 사용자가 Ctrl+Z 를 누르면 그중 하나만 돌아가고, 몇 번을 더 눌러야 원래대로
# 오는지 아무도 모른다 (도움말에 "여러 번 눌러야 합니다"라고 적어 둘 정도였다).
#
# 어떻게 안전하게 하나 (2026-07-31 안전 감사에서 개편):
#   record_undo_point()  작업 **직전** 지문 + 문서 식별자를 토큰으로 찍는다
#   seal_undo_point()    작업 **직후** 지문을 토큰에 봉인한다
#   undo_to(token)       누르기 전에 먼저 거절 검사부터 한다 —
#     · 다른 탭/문서로 바뀌었으면 거절 ("other_doc") — 예전 방식은 엉뚱한
#       문서에 Undo 를 60번까지 퍼부을 수 있었다
#     · 작업 뒤 사용자가 손으로 고쳤으면 거절 ("edited_after") — 예전 방식은
#       그 손글부터 먼저 지워 버렸다
#   거절 검사를 다 통과해야 Undo 를 누르고, 지문이 나올 때까지만 누른다.
#   못 찾으면 누른 만큼 Redo 로 도로 감아 원상복구한다.
UNDO_CAP = 60           # 이만큼 눌러도 못 찾으면 포기 (한 동작이 이보다 크진 않다)


def doc_fingerprint_strict():
    """문서 상태 지문 — 본문 글자 전체. **못 읽으면 None** (빈 문서는 "").

    글자 수만 세지 않는 이유: 같은 길이로 바뀌는 편집(글자 교체)이 있으면
    "돌아왔다"고 오판한다. 문서 하나는 대개 수 KB 라 통째로 비교해도 싸다.

    왜 strict 인가 (2026-07-31): 예전 버전은 `... or ""` 로 읽기 실패(None)를
    빈 문자열로 뭉갰다 — 그러면 **읽기 실패가 '빈 문서'와 구별되지 않아**,
    COM 이 잠깐 끊긴 순간을 "문서가 비었다"로 오판하고 되돌리기가 빈 문서를
    향해 Undo 를 퍼부을 수 있다. 실패는 None 으로 정직하게 돌려준다.
    """
    try:
        text = hwp.GetTextFile("TEXT", "")
    except Exception as e:
        applog.exc("문서 지문 읽기 실패 — 되돌리기 불가", e)
        return None
    if text is None:
        return None                     # 한글이 빈손을 줌 — 실패로 본다
    return text


def doc_fingerprint():
    """(구식 이름) 문서 지문 — 예전 배선(app.py) 호환용. 새 코드는 strict 를 쓴다.

    예전 동작 그대로: COM 예외면 None, 한글이 None 을 주면 "" (이 뭉개기가
    strict 를 새로 만든 이유다 — doc_fingerprint_strict 설명 참조).
    """
    try:
        return hwp.GetTextFile("TEXT", "") or ""
    except Exception as e:
        applog.exc("문서 지문 읽기 실패 — 되돌리기 불가", e)
        return None


def doc_identity():
    """활성 문서의 안정 식별자 문자열. 못 읽으면 None.

    무엇으로 구분하나 (선택 근거):
      Active_XHwpDocument 의 **DocumentID 와 FullName 의 조합**을 쓴다.
      · FullName 만으로는 부족하다 — 저장 안 한 문서(새 탭)는 FullName 이
        빈 문자열이라, 빈 탭 두 개가 같은 식별자가 된다.
      · DocumentID 는 한글이 문서(탭)마다 붙이는 값이라 빈 탭끼리도 갈린다.
      · 조합해 두면 어느 한쪽이 재활용돼도 다른 쪽이 구분해 준다.
    DocumentID 속성이 없는 버전이면 FullName 만으로 대신하되, **저장 안 한
    문서는 구분 불가이므로 None** 을 준다 — 되돌리기가 "같은 문서"라고
    잘못 확신하는 것보다 못 쓰는 쪽이 안전하다. DocumentID 가 있어도
    빈 문자열이면 같은 이유로 없는 것으로 취급한다 (FullName 이 빈 탭에서
    "" 인 것과 같은 꼴일 수 있다 — 빈 탭 두 개가 같은 식별자가 되면 안 된다).
    """
    if hwp is None:
        return None
    try:
        doc = hwp.hwp.XHwpDocuments.Active_XHwpDocument
        full = str(doc.FullName or "")
    except Exception as e:
        applog.exc("활성 문서 조회 실패 — 문서 식별 불가", e)
        return None
    try:
        marker = doc.DocumentID
    except Exception:
        marker = None
    if marker not in (None, ""):
        return "%s|%s" % (marker, full)
    if full:
        return "path|%s" % full         # 저장된 문서는 경로만으로도 유일하다
    return None                          # 저장 안 한 문서 + ID 없음 — 구분 불가


def record_undo_point():
    """작업 **직전**에 부른다 — 되돌릴 지점 토큰을 찍는다. 못 찍으면 None.

    토큰: {"doc": 문서 식별자, "before": 직전 지문}. 작업이 성공하면
    seal_undo_point() 로 직후 지문을 봉인해야 undo_to() 가 받아 준다.
    """
    ident = doc_identity()
    if ident is None:
        return None
    before = doc_fingerprint_strict()
    if before is None:
        return None
    return {"doc": ident, "before": before}


def seal_undo_point(token):
    """작업이 **성공한 직후**에 부른다 — 직후 지문을 토큰에 봉인. 성공 여부.

    지문을 못 읽으면 token["after"] 를 None 으로 남겨 토큰을 못 쓰게 만들고
    False 를 준다 — 봉인 안 된 토큰으로는 undo_to() 가 Undo 를 누르지 않는다.
    """
    if not token:
        return False
    after = doc_fingerprint_strict()
    if after is None:
        token["after"] = None
        return False
    token["after"] = after
    return True


def undo_to(token, cap=UNDO_CAP):
    """토큰의 '직전 지문'까지 Undo. (성공?, 누른 횟수, 거절/실패 사유)

    사유는 성공이면 "", 아니면 다음 중 하나다:
      no_token      토큰이 없거나 모양이 아니다
      unsealed      seal_undo_point 가 안 됐다 (작업 직후 지문이 없다)
      other_doc     그 사이 다른 문서/탭으로 바뀌었다 — **누르지 않는다**
      edited_after  작업 뒤 사용자가 문서를 고쳤다 — **누르지 않는다**
      fp_failed     지문을 읽지 못했다 (검사 불가 → 누르지 않거나 되감음)
      not_reached   한도까지 눌러도 못 찾음 → 누른 만큼 Redo 로 원상복구했다
      redo_broken   그 되감기 도중 Redo 가 실패 — 문서가 반쯤 되돌아간 채다
                    (호출부가 사용자에게 정직하게 알려야 한다)

    거절 검사(위 넷)는 **Undo 를 한 번도 누르기 전에** 끝낸다 — 예전 방식은
    탭이 바뀌었든 손글이 있든 일단 누르고 봤고, 그게 남의 글을 지웠다.
    """
    if (not token or not isinstance(token, dict)
            or "doc" not in token or "before" not in token):
        return False, 0, "no_token"
    if token.get("after") is None:
        return False, 0, "unsealed"
    if doc_identity() != token["doc"]:
        return False, 0, "other_doc"
    cur = doc_fingerprint_strict()
    if cur is None:
        return False, 0, "fp_failed"
    if cur != token["after"]:
        return False, 0, "edited_after"
    if cur == token["before"]:
        return True, 0, ""                  # 이미 그 자리 — 누를 것이 없다
    pressed = 0
    fp_broke = False
    while pressed < cap:
        try:
            hwp.HAction.Run("Undo")
        except Exception as e:
            applog.exc("되돌리기 실행 실패", e)
            break
        pressed += 1
        fp = doc_fingerprint_strict()
        if fp is None:                      # 도중에 지문을 못 읽음 — 즉시 되감기
            fp_broke = True
            break
        if fp == token["before"]:
            return True, pressed, ""
    for _ in range(pressed):                # 못 찾았다 — 건드린 만큼 도로
        try:
            hwp.HAction.Run("Redo")
        except Exception as e:
            applog.exc("되감기(Redo) 실패 — 문서가 반쯤 되돌아간 채 남았습니다", e)
            return False, pressed, "redo_broken"
    return False, 0, ("fp_failed" if fp_broke else "not_reached")


def delete_selection():
    hwp.HAction.Run("Delete")


def cancel_selection():
    hwp.HAction.Run("Cancel")


def current_pos():
    """현재 커서 위치 (list, para, pos). 못 읽으면 None."""
    try:
        return hwp.GetPos()
    except Exception as e:
        applog.exc("현재 위치 조회 실패", e)
        return None


def in_table():
    """커서가 표(각주 등 본문 아닌 리스트) 안에 있는가.

    GetPos()[0] 은 리스트 번호이고 본문이 0 이다. 표 안에서는 셀마다 리스트가
    따로라, 여러 셀에 걸친 선택을 '한 덩어리 글'로 다루면 셀 경계가 사라진다.
    """
    try:
        return hwp.GetPos()[0] != 0
    except Exception as e:
        applog.exc("표 안 여부 확인 실패 — 본문으로 간주", e)
        return False


def doc_end_para():
    """문서 마지막 문단 번호.

    주의: 커서를 문서 끝으로 옮긴다. 호출부가 위치를 복원해야 한다.
    """
    hwp.MoveDocEnd()
    return hwp.GetPos()[1]


# ── 텍스트/글꼴 ───────────────────────────────────────
def set_char_shape(font, size_pt):
    act = hwp.HAction
    ps = hwp.HParameterSet
    act.GetDefault("CharShape", ps.HCharShape.HSet)
    ps.HCharShape.FaceNameHangul = font
    ps.HCharShape.FaceNameLatin  = font
    ps.HCharShape.Height = hwp.PointToHwpUnit(size_pt)
    act.Execute("CharShape", ps.HCharShape.HSet)


def read_shape_here():
    r"""커서(또는 선택 영역) 자리의 **글자·문단 서식을 읽어** dict 로 준다.

    왜 필요한가 (사용자 2026-07-31): 서식 조합 블럭을 만들 때 수치를 전부
    손으로 적어야 했다 — "저런식으로 모든 것을 내가 다 정해야한다면 입력하기
    골치가 아픕니다". 한글에는 이미 '모양 복사(Alt+C)'가 있으니, 같은 일을
    우리 창에서 하면 된다: 마음에 드는 문단에 커서를 두고 [한글에서 가져오기]
    를 누르면 아래 칸이 그 값으로 채워진다.

    돌려주는 키는 **func_catalog 의 기능 이름 그대로**다 — 받는 쪽(서식 조합
    창)이 옮겨 담을 때 이름을 매핑할 필요가 없게.

    읽기만 한다 — 문서를 고치지 않는다. GetDefault 는 '지금 자리의 값'을
    파라미터셋에 채워 주므로, Execute 를 부르지 않으면 아무 일도 안 일어난다.
    선택이 없어도 커서 자리 값이 나온다(실측 2026-07-31).
    """
    out = {}
    act = hwp.HAction
    ps = hwp.HParameterSet
    try:
        act.GetDefault("CharShape", ps.HCharShape.HSet)
        cs = ps.HCharShape
        font = getattr(cs, "FaceNameHangul", None)
        if font:
            out["글씨체"] = str(font)
        h = getattr(cs, "Height", None)
        if h:
            # HwpUnit → pt. 되돌리는 함수가 없는 판이 있어 나눗셈으로도 받는다.
            try:
                out["글씨크기"] = round(hwp.HwpUnitToPoint(h), 1)
            except Exception:
                out["글씨크기"] = round(h / 100.0, 1)
        # 자간 속성 이름은 `Spacing` 이 **아니다** — 글자 종류마다 따로다
        # (자간 스파이크 실측 2026-07-31: SpacingHangul/Latin/Hanja/Japanese/
        # Other/Symbol/User 일곱 개). 한글 문서이므로 한글 값을 대표로 읽는다.
        sp = getattr(cs, "SpacingHangul", None)
        if sp is not None:
            out["자간"] = int(sp)
        if getattr(cs, "Bold", 0):
            out["굵게"] = True
        if getattr(cs, "Italic", 0):
            out["기울임"] = True
        if getattr(cs, "UnderlineType", 0):
            out["밑줄"] = True
    except Exception as e:
        applog.exc("서식 읽기: 글자 모양 실패", e)
    try:
        act.GetDefault("ParagraphShape", ps.HParaShape.HSet)
        pp = ps.HParaShape
        ls = getattr(pp, "LineSpacing", None)
        if ls:
            out["줄간격"] = int(ls)
        ind = getattr(pp, "Indentation", None)
        if ind:
            # 양수 = 들여쓰기, 음수 = 내어쓰기. 한글은 한 값에 부호로 담는다.
            pt = _hwp_to_pt(ind)
            out["들여쓰기" if ind > 0 else "내어쓰기"] = abs(pt)
        for key, attr in (("왼쪽여백", "LeftMargin"), ("오른쪽여백", "RightMargin")):
            v = getattr(pp, attr, None)
            if v:
                out[key] = round(_hwp_to_pt(v) * 25.4 / 72.0, 1)   # pt → mm
        align = getattr(pp, "AlignType", None)
        if align is not None:
            out.update({0: {"왼쪽정렬": True}, 1: {"오른쪽정렬": True},
                        2: {"가운데정렬": True}, 3: {"양쪽정렬": True}
                        }.get(int(align), {}))
    except Exception as e:
        applog.exc("서식 읽기: 문단 모양 실패", e)
    return out


def _hwp_to_pt(v):
    try:
        return round(hwp.HwpUnitToPoint(v), 1)
    except Exception:
        return round(v / 100.0, 1)


def _maybe_apply_font():
    f = S.get("font", {})
    if f.get("apply"):
        set_char_shape(f.get("name", "함초롬바탕"), f.get("size_pt", 10))


def _text(s):
    """생성 문항용 텍스트 삽입 — 글꼴 강제 적용 옵션을 반영한다."""
    _maybe_apply_font()
    hwp.insert_text(s)


def insert_plain(text):
    """서식/원문자 버튼용 단순 삽입 — 글꼴 강제 적용 안 함(현재 문서 서식 유지)."""
    act = hwp.HAction
    ps = hwp.HParameterSet
    act.GetDefault("InsertText", ps.HInsertText.HSet)
    ps.HInsertText.Text = text
    act.Execute("InsertText", ps.HInsertText.HSet)


def insert_picture_to_cell(img_path):
    """현재 커서가 있는 셀에 사진 삽입 — 셀 너비 맞춤(비율 유지) + 중앙 정렬"""
    act = hwp.HAction
    try:
        act.Run("ParagraphShapeAlignCenter")
    except Exception as e:
        applog.exc("사진 삽입 전 가운데 정렬 실패 — 정렬 없이 계속 진행", e)
    hwp.insert_picture(str(img_path))


# ── 표/박스 공통 헬퍼 ─────────────────────────────────
def _mm(v):
    return hwp.MiliToHwpUnit(v)


def _col_width_mm():
    return S["layout"]["column_width_mm"]


def _set_cell_border(act, ps, top, bottom, left, right):
    act.GetDefault("CellBorderFill", ps.HCellBorderFill.HSet)
    ps.HCellBorderFill.BorderTypeTop    = hwp.HwpLineType(top)
    ps.HCellBorderFill.BorderTypeBottom = hwp.HwpLineType(bottom)
    ps.HCellBorderFill.BorderTypeLeft   = hwp.HwpLineType(left)
    ps.HCellBorderFill.BorderTypeRight  = hwp.HwpLineType(right)
    act.Execute("CellBorderFill", ps.HCellBorderFill.HSet)


# 표/구역 탈출 시 반복 한도 — 표가 이만큼 깊게 중첩되는 문서는 없다고 본다
_MAX_NEST_DEPTH = 8


def _exit_table(act, parent_list_id=0):
    """표 편집 상태에서 지정한 부모 리스트로 빠져나온다.

    ``parent_list_id=0``이면 본문, 중첩 표에서는 바깥 셀의 리스트 ID를
    넘긴다. 지정한 위치 도달을 확인하면 True를 반환한다.

    주의: 셀 병합(TableMergeCell) 직후처럼 '셀 선택' 상태에서 CloseEx는 표 밖으로
    나가지 않고 선택만 해제한다(실측 2026-07-05 — 이때 다음 표가 셀 안에 중첩되던
    버그의 원인). Cancel로 선택을 먼저 풀고, 본문(list 0)에 도달할 때까지 CloseEx.

    실패하면 False 를 주고 **MoveRight 를 누르지 않는다** (2026-07-31 안전 감사):
    본문에 못 나온 채 MoveRight 를 누르면 셀 안에서 글자 하나만 건너뛰고,
    다음에 만드는 표/문항이 **그 표 안에** 중첩돼 들어간다. 탈출 실패를
    숨기고 한 발 더 걷는 것보다, 그 자리에 멈춰 실패를 알리는 쪽이 안전하다.
    """
    act.Run("Cancel")               # 셀 선택 상태 해제
    reached = False
    for _ in range(_MAX_NEST_DEPTH):
        try:
            if hwp.GetPos()[0] == parent_list_id:
                reached = True
                break
        except Exception as e:
            applog.exc("표 탈출 중 위치 조회 실패 — 탈출 중단", e)
            return False
        act.Run("CloseEx")
    if not reached:
        try:                            # 마지막 CloseEx 뒤 상태도 한 번 본다
            reached = hwp.GetPos()[0] == parent_list_id
        except Exception as e:
            applog.exc("표 탈출 중 위치 조회 실패 — 탈출 중단", e)
            return False
    if not reached:
        applog.warn("표 탈출 실패 — 중첩 한도(%d)까지 CloseEx 해도 본문에 "
                    "도달하지 못했습니다" % _MAX_NEST_DEPTH)
        return False
    # 본문 도달 시 커서는 표 앵커 앞 — MoveDown은 표 '첫 셀로 들어가는' 키라
    # 쓰면 안 되고(실측), MoveRight로 앵커 글자를 건너뛰어 표 뒤로 나온다.
    act.Run("MoveRight")
    return True


# 표 생성 시 열마다 붙는 셀 좌우 안여백(1.8mm×2) — 실측 보정값(2026-07-05)
_CELL_SIDE_MARGIN_MM = 3.6


def _create_table(rows, cols, total_mm, row_heights_mm):
    """rows×cols 표 생성. 완성된 표의 전체 폭이 total_mm가 되도록 열을 균등 분할.

    실측(2026-07-05):
    - WidthType: 0=단에 맞춤, 1=문단에 맞춤 → 지정 너비 무시. 2=임의 값이어야 반영.
    - ColWidth는 셀 '내용' 폭 기준이라, 완성 폭 = Σ(ColWidth + 3.6mm). 열마다
      셀 좌우 안여백만큼 빼서 지정해야 전체 폭이 total_mm에 맞는다.
    - RowHeight는 '최소 높이' — 내용·줄간격·셀 여백이 크면 그만큼 늘어난다.
    """
    act = hwp.HAction
    ps  = hwp.HParameterSet
    act.GetDefault("TableCreate", ps.HTableCreation.HSet)
    ps.HTableCreation.Rows       = rows
    ps.HTableCreation.Cols       = cols
    ps.HTableCreation.WidthType  = 2
    ps.HTableCreation.HeightType = 1
    ps.HTableCreation.WidthValue = _mm(total_mm)
    ps.HTableCreation.CreateItemArray("ColWidth", cols)
    # 열 내용 폭 = 전체 폭/열 수 - 셀 좌우 여백 (반올림 오차는 마지막 칸에서 흡수)
    content_total = total_mm - cols * _CELL_SIDE_MARGIN_MM
    each = max(content_total / cols, 1.0)
    acc = 0.0
    for i in range(cols):
        w = max(content_total - acc, 1.0) if i == cols - 1 else each
        ps.HTableCreation.ColWidth.SetItem(i, _mm(w))
        acc += each
    ps.HTableCreation.CreateItemArray("RowHeight", rows)
    for i in range(rows):
        ps.HTableCreation.RowHeight.SetItem(i, _mm(row_heights_mm[i]))
    act.Execute("TableCreate", ps.HTableCreation.HSet)


GENERATED_TABLE_WIDTH_RATIO = 0.8


def create_table_autofit(rows, cols):
    r"""rows×cols 표를 가용 폭의 80%로 만든다 (\표3x3\ 변환용).

    본문에서는 현재 단 폭, 다른 표의 셀 안에서는 현재 셀 폭을 기준으로 삼는다.
    양쪽 모두 WidthType=2로 고정해야 HWP가 요청한 80% 폭을 무시하지 않는다.

    높이는 지정하지 않는다(HeightType=0) — 내용에 따라 늘어나게 둔다.
    """
    act = hwp.HAction
    ps  = hwp.HParameterSet
    act.GetDefault("TableCreate", ps.HTableCreation.HSet)
    ps.HTableCreation.Rows       = rows
    ps.HTableCreation.Cols       = cols
    nested = in_table()
    available_width_mm = float(hwp.get_col_width()) if nested else _col_width_mm()
    width_mm = available_width_mm * GENERATED_TABLE_WIDTH_RATIO
    width_hu = _mm(width_mm)
    ps.HTableCreation.WidthType  = 2
    ps.HTableCreation.HeightType = 0      # 자동 높이
    ps.HTableCreation.TableProperties.TreatAsChar = True
    ps.HTableCreation.WidthValue = width_hu
    ps.HTableCreation.TableProperties.Width = width_hu
    ps.HTableCreation.CreateItemArray("ColWidth", cols)
    content_total = max(width_mm - cols * _CELL_SIDE_MARGIN_MM, float(cols))
    each = content_total / cols
    for index in range(cols):
        ps.HTableCreation.ColWidth.SetItem(index, _mm(each))
    act.Execute("TableCreate", ps.HTableCreation.HSet)
    # HWP 2022 ignores TableProperties.TreatAsChar in the creation parameter
    # set.  Apply it once more to the created control, as pyhwpx itself does.
    # Without this, a table created inside the experiment material cell is
    # anchored to the page and can float over the exam header.
    control = hwp.CurSelectedCtrl
    if getattr(control, "_com_obj", None) is None:
        control = hwp.ParentCtrl
    inline_properties = hwp.CreateSet("Table")
    inline_properties.SetItem("TreatAsChar", True)
    control.Properties = inline_properties


def exit_table(parent_list_id=0):
    """표 편집 상태에서 지정한 부모 리스트로 빠져나온다. 성공 여부."""
    return _exit_table(hwp.HAction, parent_list_id=parent_list_id)


# ── 찾기 ──────────────────────────────────────────────
def find_text(query, direction="Forward"):
    r"""문서에서 문자열을 찾아 선택한다. 없으면 False.

    pyhwpx의 hwp.find()를 쓰지 않는 이유 (실측 2026-07-16):
      1) 내부에서 HAction.Execute("FindDlg", ...) 로 '찾기 대화상자'를 실제 실행함.
      2) SetMessageBoxMode(0x2FFF1) 로 바꾼 뒤 finally에서 원래값이 아니라
         0xFFFFF 를 강제 세팅해, 한글의 대화상자 처리 모드가 0x0 → 0xFFFFF 로
         영구히 바뀜 (변환할 때 '창 모드가 변하는' 증상의 원인).
    RepeatFind 만 쓰면 대화상자도 안 뜨고 모드도 그대로다(0x0 유지 실측 확인).
    """
    act = hwp.HAction
    pset = hwp.HParameterSet.HFindReplace
    act.GetDefault("RepeatFind", pset.HSet)
    pset.MatchCase = 1
    pset.SeveralWords = 0
    pset.UseWildCards = 0
    pset.WholeWordOnly = 0
    pset.AutoSpell = 1
    pset.Direction = hwp.FindDir(direction)
    pset.FindString = query
    pset.IgnoreMessage = 1
    pset.HanjaFromHangul = 1
    pset.AllWordForms = 0
    pset.FindJaso = 0
    pset.FindRegExp = 0
    pset.FindType = 1
    r = bool(act.Execute("RepeatFind", pset.HSet))
    _diag("find_text 후")
    return r


def replace_all(find, repl):
    r"""문서 전체에서 find → repl 모두 바꾸기. 성공 여부.

    find_text 와 같은 이유로 대화상자 없는 AllReplace 액션을 직접 쓴다.
    자리 표시 정리(홑 \ → \\)가 쓴다 — 실패해도 저장은 계속돼야 하므로
    호출부는 결과를 확인만 하고 막지 않는다.
    """
    act = hwp.HAction
    pset = hwp.HParameterSet.HFindReplace
    act.GetDefault("AllReplace", pset.HSet)
    pset.MatchCase = 1
    pset.SeveralWords = 0
    pset.UseWildCards = 0
    pset.WholeWordOnly = 0
    pset.AutoSpell = 1
    pset.Direction = hwp.FindDir("AllDoc")
    pset.FindString = find
    pset.ReplaceString = repl
    pset.IgnoreMessage = 1
    pset.ReplaceMode = 1
    pset.FindRegExp = 0
    pset.FindType = 1
    try:
        return bool(act.Execute("AllReplace", pset.HSet))
    except Exception as e:
        applog.exc(f"모두 바꾸기 실패 — {find!r} → {repl!r}", e)
        return False
