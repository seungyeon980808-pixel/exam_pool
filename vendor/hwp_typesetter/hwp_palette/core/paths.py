# -*- coding: utf-8 -*-
"""파일이 어디 놓이는지 한 곳에서 정한다 (UI 제안 20 — exe 배포).

**왜 필요한가.** 여태 설정·라이브러리·로그는 모두

    pathlib.Path(__file__).parent / "config.json"

이었다. 소스로 실행할 때는 프로젝트 폴더라 맞다. 그런데 PyInstaller 로 exe 를
만들면 __file__ 은 실행할 때마다 새로 풀리는 **임시 폴더**(sys._MEIPASS)를
가리키고, 그 폴더는 프로그램이 끝나면 지워진다. 즉 exe 로 배포하면 팔레트를
아무리 꾸며도 껐다 켜면 전부 사라진다.

그래서 두 가지를 나눈다.
  data_dir()     — 사용자가 만든 것 (설정·라이브러리·조각·로그). 써야 하므로
                   exe 옆(또는 못 쓰면 AppData)에 둔다. 껐다 켜도 남는다.
  resource_dir() — 프로그램에 딸려온 것 (아이콘 등). 읽기만 하므로 임시 폴더로
                   충분하다.

소스로 실행할 때는 둘 다 지금까지와 똑같이 프로젝트 폴더다 — 기존 설정 파일을
그대로 쓴다.
"""

import os
import pathlib
import sys

APP_NAME = "hwp_palette"
# **프로젝트 뿌리**다 — 이 파일이 있는 hwp_palette/core/ 가 아니다 (2026-07-28
# 폴더 개편). 두 칸 위로 올라간다: hwp_palette/core/paths.py → hwp_palette/ → 뿌리.
# 여기가 어긋나면 data/ 와 assets/ 를 패키지 안에서 찾다가 조용히 못 찾는다.
_HERE = pathlib.Path(__file__).resolve().parents[2]


def is_frozen():
    """PyInstaller 로 묶인 exe 로 돌고 있는가."""
    return bool(getattr(sys, "frozen", False))


def resource_dir():
    """딸려온 읽기 전용 자원(assets 등)이 있는 폴더."""
    if is_frozen():
        # onefile 이면 _MEIPASS(임시), onedir 이면 exe 옆
        return pathlib.Path(getattr(sys, "_MEIPASS", _HERE))
    return _HERE


def _writable(d):
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write_test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


# exe 옆에 만드는 데이터 폴더 이름 (사용자 결정 2026-07-27).
#
# 예전에는 **exe 옆에 파일을 그대로 흩뿌렸다.** 폴더에 넣어 쓸 것이라는 전제가
# 있었는데, 사람들은 받은 exe 를 바탕화면이나 '다운로드'에 그냥 둔다. 그러면
# config.json · 백업 3벌 · library.json · 백업 3벌 · app.log · fragments 까지
# **파일 10개 + 폴더 1개**가 바탕화면에 흩어졌다 (실측).
#
# 이름을 'data' 가 아니라 '내 물감' 으로 한 이유: 프로그램이 쓰는 말과 같아야
# "이 폴더가 내 자산이구나"가 읽히고, 백업할 때 무엇을 챙길지 분명해진다.
DATA_FOLDER_NAME = "내 물감"

# 소스로 실행할 때 쓰는 데이터 폴더 (2026-07-28, 폴더 개편 2단계).
#
# exe 는 이미 '내 물감' 폴더에 모아 두는데, **소스 실행만 프로젝트 루트에 그대로
# 쏟아내고 있었다** — config.json + 백업 3벌, library.json + 백업 3벌, app.log,
# fragments/, 미리보기/, 미리보기작업/, 양식작업/ 까지 파일 10개와 폴더 4개가
# 소스 파일 42개와 같은 자리에 섞였다. 개발하는 사람이 폴더를 열 때마다
# "무엇이 코드이고 무엇이 내 데이터인가"를 매번 골라내야 했다.
#
# 이름을 exe 쪽('내 물감')과 다르게 'data' 로 둔 이유: 이쪽은 **개발용 작업
# 폴더**라 .gitignore 한 줄로 통째로 막는 것이 목적이고, 그 자리에서는
# 영어 한 단어가 규칙으로 읽힌다. 사용자가 받는 exe 쪽 이름은 그대로 둔다.
SRC_DATA_FOLDER_NAME = "data"

# 폴더 방식으로 바꾸기 전(v0.1.1)에 exe 옆에 흩어져 있던 것들. 그 시절 exe 를
# 써 본 사람의 팔레트가 조용히 사라지지 않게 한 번만 옮겨 준다.
# 소스 실행에서 루트에 흩어져 있던 것들도 같은 목록으로 옮긴다.
_LEGACY_NAMES = ("config.json", "library.json", "app.log", "fragments",
                 "window_diag.log", "미리보기", "미리보기작업", "양식작업")

# 첫 스캔을 마쳤다는 표식 파일. 이게 있으면 다음 실행부터 스캔을 통째로
# 건너뛴다 — '한 번만'이라는 약속을 파일로 지킨다.
_MIGRATED_MARKER = ".migrated"


def _legacy_candidates():
    """옮길 대상의 **정확한** 이름 목록.

    예전에는 beside.glob(name + "*") 로 접두사를 긁었는데, 그러면 exe 옆에
    있던 사용자의 무관한 파일·폴더('미리보기 자료', 'config.json.txt' 등)까지
    조용히 쓸어 담았다 (2026-07-31 안전 점검). 백업까지 포함해 이름을 전부
    나열하고, 정확히 일치하는 것만 옮긴다.
    """
    out = []
    for name in _LEGACY_NAMES:
        out.append(name)
        if name.endswith(".json"):
            # backup.py 의 롤링 백업 3벌 (.bak1~3). backup.KEEP 을 안 쓰는
            # 이유: backup → applog → paths 라 여기서 임포트하면 순환이 된다.
            out.extend(f"{name}.bak{n}" for n in range(1, 4))
    return out


def _migrate_legacy(beside, folder):
    """exe 옆에 흩어져 있던 예전 데이터를 새 폴더로 옮긴다 (한 번만).

    옮기다 실패해도 프로그램은 떠야 한다 — 실패한 것은 제자리에 남고, 새
    폴더는 빈 채로 시작한다(사용자 눈에는 '처음 켠 것'처럼 보이지만, 원본이
    지워지지는 않는다).
    """
    marker = folder / _MIGRATED_MARKER
    try:
        if marker.exists():
            return 0                    # 이미 한 번 옮겼다 — 다시 훑지 않는다
    except OSError:
        pass
    moved = 0
    skipped = False         # OSError 로 못 옮기고 남긴 것이 하나라도 있었나
    for name in _legacy_candidates():
        src = beside / name
        try:
            if not src.exists():
                continue
            dest = folder / src.name
            if not dest.exists():
                src.replace(dest)
                moved += 1
        except OSError:
            skipped = True  # 잠긴 파일 등 — 제자리에 두고 넘어간다
    # 표식은 **하나도 안 남겼을 때만** 쓴다 (2026-07-31 안전 점검 후속).
    # 첫 실행에 백신·OneDrive 가 파일을 잠그고 있으면 위에서 건너뛰는데,
    # 그 순간 표식을 박으면 다음 실행이 스캔을 통째로 건너뛰어 잠겼던
    # 데이터가 영영 밖에 남는다 — 다음 실행에 한 번 더 훑게 미룬다.
    # (새 폴더에 이미 있어서 안 옮긴 것은 정상이라 표식을 막지 않는다.)
    if not skipped:
        try:
            marker.write_text("", encoding="utf-8")
        except OSError:
            pass            # 표식을 못 남기면 다음 실행에 스캔만 한 번 더 한다
    return moved


def _decorate_folder(folder):
    r"""'내 물감' 폴더에 물감 아이콘을 입힌다 (윈도우 desktop.ini 규칙).

    왜: 바탕화면에 폴더 하나가 늘어나는데, 그게 무엇인지 이름만으로는 지나치기
    쉽다. 물감 아이콘이 붙어 있으면 "아, 프로그램이 만든 내 자산" 이 한눈에
    보이고, 옮기거나 백업할 때 헷갈리지 않는다.

    아이콘 파일을 **폴더 안에** 두는 이유: 폴더째 복사해 옮겨도 아이콘이
    따라간다(USB 로 들고 다니는 쓰임새를 지키기 위함).

    전부 꾸밈이라 실패해도 조용히 넘어간다 — 아이콘 때문에 프로그램이 못 뜨는
    일은 없어야 한다. (applog 를 쓰지 않는 이유: applog 가 paths 를 임포트해
    순환이 된다)
    """
    try:
        import ctypes
        import shutil

        ico = folder / ".folder.ico"
        ini = folder / "desktop.ini"
        if not ico.exists():
            src = resource_dir() / "assets" / "folder.ico"
            if not src.exists():
                return
            shutil.copyfile(src, ico)
        if not ini.exists():
            ini.write_text(
                "[.ShellClassInfo]\n"
                "IconResource=.folder.ico,0\n"
                "InfoTip=HwpPalette 가 만든 물감·팔레트·양식이 들어 있습니다\n",
                encoding="utf-8")

        # 윈도우는 desktop.ini 가 **숨김+시스템**이고 폴더에 시스템 속성이
        # 있어야 그 아이콘을 읽는다. 안 걸면 규칙 파일만 덩그러니 보인다.
        setattrs = ctypes.windll.kernel32.SetFileAttributesW
        setattrs(str(ico), 0x2 | 0x4)          # 숨김 + 시스템
        setattrs(str(ini), 0x2 | 0x4)
        setattrs(str(folder), 0x4)             # 폴더에 시스템 속성
    except Exception:
        pass                # 꾸밈 실패로 프로그램이 멈추면 안 된다


def data_dir():
    """사용자 데이터를 두는 폴더. 없으면 만든다.

    exe 는 **옆에 '내 물감' 폴더를 만들어** 그 안에 넣는다 — 어디에 두어도
    주변이 지저분해지지 않고, 폴더째 복사해 옮기거나 USB 에 넣어 다닐 수
    있으며, 내 자산이 어디 있는지 눈에 보인다.
    Program Files 처럼 쓰기가 막힌 곳에 설치했으면 AppData 로 물러선다
    (거기서도 실패하면 예외 대신 exe 옆 경로를 돌려준다 — 저장할 때
    나는 오류가 여기서 나는 오류보다 다루기 쉽다).
    """
    # ExamPool 같은 내장 호스트는 코드/양식팩과 사용자 데이터를 서로 다른
    # 위치에 둔다. 명시값이 있으면 레거시 이동이나 exe 위치 추측을 하지 않는다.
    override = os.environ.get("HWPPAL_DATA_DIR", "").strip()
    if override:
        folder = pathlib.Path(override).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    if not is_frozen():
        # 소스 실행 — 프로젝트 루트의 data/ (2026-07-28). 쓸 수 없는 자리면
        # 예전처럼 루트를 그대로 쓴다: 데이터를 못 쓰는 것보다 지저분한 편이 낫다.
        folder = _HERE / SRC_DATA_FOLDER_NAME
        if not _writable(folder):
            return _HERE
        _migrate_legacy(_HERE, folder)     # 루트에 흩어져 있던 것 한 번만 이사
        return folder

    beside = pathlib.Path(sys.executable).resolve().parent
    folder = beside / DATA_FOLDER_NAME
    if _writable(folder):
        _migrate_legacy(beside, folder)
        _decorate_folder(folder)
        return folder

    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        appdata = pathlib.Path(base) / APP_NAME
        if _writable(appdata):
            return appdata
    return folder


try:
    DATA_DIR = data_dir()
except Exception as e:
    import sys, os
    DATA_DIR = pathlib.Path(os.path.expanduser("~")) / "HwpPalette_data"
    print(f"데이터 폴더 초기화 실패, 기본 경로 사용: {DATA_DIR}", file=sys.stderr)
RESOURCE_DIR = resource_dir()
