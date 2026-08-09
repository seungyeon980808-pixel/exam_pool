# -*- coding: utf-8 -*-
"""가벼운 로거 — 조용히 삼켜지던 실패를 기록한다.

배경: `except Exception: pass` 가 23군데 있었다. 실패해도 사용자는 모르고
"왜 안 되지?"만 남았고, 실제로 창 크기 버그가 오래 숨어 있었다(대소문자 오타로
창을 못 찾는데 아무 소리도 안 났음).

원칙:
  - 무시해도 되는 실패라도 **기록은 남긴다** (나중에 원인을 찾을 수 있게)
  - 로그 쓰기 자체가 앱을 죽이면 안 되므로 여기서는 절대 예외를 올리지 않는다
  - 파일은 프로그램 폴더의 app.log, 일정 크기를 넘으면 새로 시작
"""

import datetime
import pathlib
import traceback

from hwp_palette.core import paths

LOG_PATH = paths.DATA_DIR / "app.log"
MAX_BYTES = 512 * 1024          # 512KB 넘으면 app.log.old 로 밀고 새로 시작
_console = False                # True 면 콘솔에도 출력 (개발용)


def set_console(on=True):
    global _console
    _console = bool(on)


def _write(level, msg):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} [{level}] {msg}"
    if _console:
        print(line)
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_BYTES:
            # 지우지 않고 .old 로 민다 (2026-07-31) — 오류가 쏟아져 한도를
            # 넘는 바로 그 순간에 지워 버리면, 정작 원인을 찾을 기록이
            # 사라진다. 한 세대 전까지는 남긴다 (있던 .old 는 교체).
            LOG_PATH.replace(LOG_PATH.with_name(LOG_PATH.name + ".old"))
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass                    # 로그 실패로 앱이 죽으면 안 됨


def info(msg):
    _write("INFO", msg)


def warn(msg):
    _write("WARN", msg)


def exc(context, error=None, detail=False):
    """예외를 기록한다. context = '무엇을 하다 실패했는지'.

    detail=True 면 스택까지 남긴다(원인 추적이 필요한 곳에만).
    """
    if error is None:
        _write("ERROR", context)
    else:
        _write("ERROR", f"{context} — {type(error).__name__}: {error}")
    if detail:
        try:
            _write("TRACE", traceback.format_exc().strip())
        except Exception:
            pass
