# -*- coding: utf-8 -*-
r"""전역 단축키 — 한글에서 작업하는 중에도 눌리는 단축키 (2026-07-25).

왜 필요한가:
    Tk 의 `root.bind_all("<Control-t>")` 는 **이 프로그램 창이 선택돼 있을 때만**
    동작한다. 그런데 이 도구의 실제 흐름은 "한글에서 드래그 → 단축키"다. 그
    순간 키보드의 주인은 한글이라 Tk 는 키를 아예 보지 못한다. 안내문에는
    "드래그 → Ctrl+T" 라고 적혀 있었는데 **안내대로 하면 반드시 안 되는** 상태였다.

어떻게 하나 (win32 RegisterHotKey, 실측 2026-07-25):
    · hwnd 없이 등록하면 **등록한 스레드의 메시지 큐**로 WM_HOTKEY 가 온다.
    · Tk 는 자기 메시지 루프를 돌리는데 hwnd 없는 스레드 메시지는 흘려버린다.
      그래서 **전용 스레드**에서 등록하고 그 스레드가 GetMessage 로 받는다.
    · 받은 것은 큐에 넣고, Tk 쪽에서 poll() 로 꺼내 간다 —
      **Tk 위젯을 다른 스레드에서 건드리면 안 되기 때문**이다.
    · 끝낼 때는 그 스레드에 WM_QUIT 를 보내 GetMessage 를 빠져나오게 한다.

`keyboard` 같은 라이브러리를 쓰지 않은 이유: 일부 환경에서 관리자 권한을
요구해 exe 배포와 상성이 나쁘다. pywin32 는 이미 쓰고 있어 새 부담이 없다.

폴링(GetAsyncKeyState) 대신 이 방식을 쓴 이유: RegisterHotKey 는 키를 **가로챈다**.
폴링은 못 가로채서 한글도 같은 키를 함께 받는다.
"""

import queue
import threading

from hwp_palette.core import applog

try:
    import win32api
    import win32con
    import win32gui
except ImportError:                     # pywin32 가 없는 환경 — 단축키만 꺼진다
    win32api = win32con = win32gui = None

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
# 누르고 있을 때 자동 반복으로 쏟아지지 않게 (Windows 7+)
MOD_NOREPEAT = 0x4000
# start() 가 스레드의 등록 결과를 기다리는 시간 (초) — 테스트가 줄여 쓴다
_READY_TIMEOUT_S = 3.0

# 수정키 이름 → 비트. 조합을 글로 적어두면 설정으로 빼기도 쉽다.
_MODS = {"ctrl": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}


def parse_combo(combo):
    r"""'ctrl+alt+t' → (수정키 비트, 가상키 코드). 못 읽으면 ValueError.

    글자 키와 숫자 키만 받는다 — 지금 필요한 건 그것뿐이고, 기능키까지 받으면
    표를 들고 다녀야 한다.
    """
    parts = [p.strip().lower() for p in (combo or "").split("+") if p.strip()]
    if not parts:
        raise ValueError("빈 단축키")
    mods, key = 0, None
    for p in parts:
        if p in _MODS:
            mods |= _MODS[p]
        elif key is None and len(p) == 1 and (p.isalpha() or p.isdigit()):
            key = p.upper()
        else:
            raise ValueError(f"알 수 없는 키: {p}")
    if key is None:
        raise ValueError("글자/숫자 키가 없습니다")
    if not mods:
        raise ValueError("수정키(ctrl/alt/shift)가 없으면 전역으로 잡을 수 없습니다")
    return mods | MOD_NOREPEAT, ord(key)


class GlobalHotkey:
    r"""전역 단축키 하나. 눌린 횟수를 poll() 로 꺼내 쓴다.

    쓰는 법:
        hk = GlobalHotkey("ctrl+alt+t")
        ok, err = hk.start()          # err 는 사용자에게 보여줄 문구
        ...
        if hk.poll():                 # Tk 의 after 루프에서 주기적으로
            변환()
    """

    def __init__(self, combo, hotkey_id=1):
        self.combo = combo
        self._id = hotkey_id
        self._q = queue.Queue()
        self._thread = None
        self._tid = None
        self._ready = threading.Event()
        # start() 가 기다리다 포기했다는 표시 (2026-07-31) — 이게 선 뒤에
        # 등록이 끝나면 스레드가 스스로 도로 푼다. 안 풀면 아무도 받지 않는
        # 가로채기가 남아 그 조합이 **모든 프로그램에서** 죽은 키가 된다.
        self._cancel = threading.Event()
        self._error = None

    # ── 시작/종료 ────────────────────────────────────
    def start(self):
        """등록을 시도한다. 반환: (성공여부, 실패 사유 또는 None).

        **실패를 조용히 넘기지 않는다.** 다른 프로그램이 같은 조합을 이미 쓰고
        있으면 등록이 실패하는데, 그걸 안 알려주면 사용자는 "왜 안 되지"만 남는다.
        """
        if win32gui is None:
            self._error = "pywin32 가 없어 전역 단축키를 쓸 수 없습니다"
            return False, self._error
        try:
            mods, vk = parse_combo(self.combo)
        except ValueError as e:
            self._error = f"단축키를 읽을 수 없습니다 ({self.combo}): {e}"
            return False, self._error
        self._thread = threading.Thread(
            target=self._run, args=(mods, vk), daemon=True,
            name=f"hotkey-{self.combo}")
        self._thread.start()
        # 등록은 스레드 안에서 일어나므로 결과가 나올 때까지 잠깐 기다린다
        if not self._ready.wait(timeout=_READY_TIMEOUT_S):
            # 그냥 돌아가면 안 된다 (2026-07-31): 잠시 뒤 등록이 **성공해
            # 버리면** 그 조합을 시스템 전체에서 가로채는데 아무도 받아 가지
            # 않는다. 취소를 세워 두면 스레드가 등록을 마치는 즉시 도로 풀고,
            # 이미 수신에 들어갔다면 stop() 의 WM_QUIT 가 깨워서 풀게 한다.
            self._cancel.set()
            self.stop()
            self._error = "단축키 등록이 응답하지 않습니다"
            return False, self._error
        return (self._error is None), self._error

    def stop(self):
        """스레드를 깨워 등록을 풀고 끝낸다 (몇 번 불러도, 언제 불러도 안전).

        데몬이라 안 불러도 프로세스는 죽지만, 등록만 남기고 죽으면 다음 실행
        까지 그 조합이 시스템에서 자리를 차지할 수 있어 반드시 풀고 간다.
        """
        # 등록이 아직 진행 중이라면, 끝나는 즉시 스스로 풀게 한다 (2026-07-31)
        self._cancel.set()
        if self._tid is None or win32gui is None:
            return
        try:
            win32gui.PostThreadMessage(self._tid, WM_QUIT, 0, 0)
        except Exception as e:
            applog.exc("전역 단축키 종료 신호 실패 (무해)", e)

    # ── 사용 ────────────────────────────────────────
    def poll(self):
        """마지막 poll 이후 눌린 횟수. Tk 스레드에서 부른다."""
        n = 0
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
            n += 1
        return n

    # ── 내부 ────────────────────────────────────────
    def _run(self, mods, vk):
        self._tid = win32api.GetCurrentThreadId()
        try:
            win32gui.RegisterHotKey(None, self._id, mods, vk)
        except Exception as e:
            # 대개 '이미 다른 프로그램이 쓰는 조합'이다
            self._error = (f"단축키 {self.combo} 를 등록하지 못했습니다 "
                           f"— 다른 프로그램이 쓰고 있을 수 있습니다 ({e})")
            applog.exc(f"전역 단축키 등록 실패 ({self.combo})", e)
            self._ready.set()
            return
        applog.info(f"전역 단축키 등록: {self.combo}")
        self._ready.set()
        try:
            # start() 가 기다리다 포기한 **뒤에야** 등록이 끝났을 수 있다
            # (2026-07-31). 그 등록은 임자가 없다 — 아무도 poll 하지 않는데
            # 키만 시스템 전체에서 가로챈다. 곧바로 나가 finally 가 풀게 한다.
            if self._cancel.is_set():
                applog.warn(f"단축키 {self.combo} 등록이 뒤늦게 끝나 도로 풉니다")
                return
            while True:
                rc, msg = win32gui.GetMessage(None, 0, 0)
                if rc in (0, -1):          # WM_QUIT 또는 오류
                    break
                if msg[1] == WM_HOTKEY:
                    self._q.put(1)
        except Exception as e:
            applog.exc(f"전역 단축키 수신 중단 ({self.combo})", e)
        finally:
            try:
                win32gui.UnregisterHotKey(None, self._id)
            except Exception as e:
                applog.exc("전역 단축키 해제 실패 (무해)", e)
