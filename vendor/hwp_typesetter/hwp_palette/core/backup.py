# -*- coding: utf-8 -*-
"""개인 데이터 파일의 롤링 백업 (UI 제안 2).

왜 필요한가:
  config.json 에는 팔레트 배치가, library.json 에는 등록한 서식·문자 목록이
  들어 있다. 저장 도중 강제 종료되거나 디스크 오류가 나면 **통째로 사라진다**.
  이 프로그램은 그 두 파일이 전부라, 잃으면 처음부터 다시 만들어야 한다.

방식:
  저장하기 **직전**에 지금 파일을 .bak1 로 밀어 넣고, 기존 .bak1 → .bak2 →
  .bak3 으로 한 칸씩 민다. 가장 오래된 것은 버린다.
  - 백업은 '직전 상태'다. 저장이 실패해도 .bak1 로 되돌릴 수 있다.
  - 내용이 같으면 밀지 않는다 — 안 바뀐 저장이 반복되면 멀쩡한 백업 3개가
    똑같은 값으로 덮여, 정작 되돌릴 과거가 사라지기 때문이다.

백업 자체가 실패해도 저장은 진행한다(백업 때문에 저장을 막으면 본말전도).
"""

import pathlib

from hwp_palette.core import applog

KEEP = 3            # .bak1 ~ .bak3


def _slot(path, n):
    return path.with_suffix(path.suffix + f".bak{n}")


def rotate(path):
    """저장 직전에 부른다. 지금 파일을 .bak1 로 밀어 넣는다."""
    path = pathlib.Path(path)
    if not path.exists():
        return                      # 첫 저장 — 백업할 과거가 없다
    try:
        current = path.read_bytes()
    except OSError as e:
        applog.exc(f"백업용 읽기 실패 ({path.name}) — 백업 없이 저장 진행", e)
        return
    newest = _slot(path, 1)
    try:
        if newest.exists() and newest.read_bytes() == current:
            return                  # 내용이 그대로면 밀지 않는다
    except OSError:
        pass                        # 못 읽으면 그냥 밀어 덮는다

    try:
        for n in range(KEEP, 1, -1):        # .bak2→.bak3, .bak1→.bak2
            src, dst = _slot(path, n - 1), _slot(path, n)
            if src.exists():
                dst.unlink(missing_ok=True)
                src.replace(dst)
        newest.write_bytes(current)
    except OSError as e:
        applog.exc(f"백업 회전 실패 ({path.name}) — 저장은 계속 진행", e)


def list_backups(path):
    """되돌릴 수 있는 백업들 [(번호, 경로, 크기), ...] — 최신순."""
    path = pathlib.Path(path)
    out = []
    for n in range(1, KEEP + 1):
        s = _slot(path, n)
        if s.exists():
            try:
                out.append((n, s, s.stat().st_size))
            except OSError:
                pass
    return out


def restore(path, n=1):
    """백업 n번으로 되돌린다. 되돌리기 전 상태도 백업해 둔다. 성공 여부 반환."""
    path = pathlib.Path(path)
    src = _slot(path, n)
    if not src.exists():
        return False
    try:
        data = src.read_bytes()
        rotate(path)                # 되돌리기 자체도 되돌릴 수 있게
        path.write_bytes(data)
        applog.info(f"{path.name} 을(를) .bak{n} 으로 되돌렸습니다")
        return True
    except OSError as e:
        applog.exc(f"백업 복원 실패 ({path.name} ← .bak{n})", e)
        return False
