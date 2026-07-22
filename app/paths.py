"""파일 저장 위치 — 소스 실행 / exe 실행을 구분한다.

hwppalette와 같은 원칙: 개인 데이터(db, config)는 실행 파일 옆 data/ 에 둔다.
쓰기가 막힌 곳(Program Files 등)이면 %LOCALAPPDATA%\\exam_pool 로 물러난다.
"""
import os
import sys
from pathlib import Path


def _base_dir() -> Path:
    # PyInstaller exe 로 묶였으면 실행 파일 위치, 아니면 프로젝트 루트(app/ 의 부모)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()
APP_DIR = Path(__file__).resolve().parent          # app/ (seed 등 번들 리소스)
STATIC_DIR = APP_DIR.parent / "static"
SEED_DIR = APP_DIR / "seed"


def data_dir() -> Path:
    """개인 데이터 폴더. 쓰기 불가하면 LOCALAPPDATA 로 폴백."""
    d = BASE_DIR / "data"
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return d
    except OSError:
        fallback = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "exam_pool"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


DB_PATH = data_dir() / "exam_pool.db"
