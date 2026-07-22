"""환경설정 API — 백업·복구.

되돌리기 비싼 동작이므로 화면에서 한 번 더 묻고, 서버에서도 슬롯 존재를 확인한다.
"""
from fastapi import APIRouter, HTTPException

from . import backup

router = APIRouter(prefix="/api")


@router.get("/backups")
def list_backups():
    return {"last": backup.last_backup(), "slots": backup.list_backups(),
            "dir": str(backup.backup_dir())}


@router.post("/backups")
def create_backup():
    try:
        return backup.make_backup("수동")
    except Exception as e:
        raise HTTPException(500, f"백업에 실패했습니다: {e}")


@router.post("/backups/{slot}/restore")
def restore_backup(slot: int):
    if slot < 1 or slot > backup.SLOTS:
        raise HTTPException(400, "잘못된 백업 슬롯입니다.")
    try:
        return backup.restore(slot)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"복구에 실패했습니다: {e}")


@router.post("/backups/open-folder")
def open_backup_folder():
    """백업 폴더를 탐색기로 연다 — 외장 디스크로 직접 복사해 두고 싶을 때."""
    import os
    try:
        os.startfile(str(backup.backup_dir()))   # Windows
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"폴더를 열 수 없습니다: {e}")
