"""롤링 백업 · 복구.

한 학기 출제 데이터가 exam_pool.db 파일 하나에 모여 있고 그 폴더는 git 대상이 아니다.
파일 하나가 깨지면 명제·문항·세트·근거가 동시에 사라지므로 슬롯 3개를 돌려 쓴다.

- data/backup/exam_pool.bak1  가장 최근
- data/backup/exam_pool.bak2
- data/backup/exam_pool.bak3  가장 오래됨 (다음 백업에서 버려짐)

복사는 sqlite3 온라인 백업 API 로 한다. 서버가 열어 둔 연결이 있어도 안전하고,
쓰기 중간 상태가 섞이지 않는다(shutil.copy 는 그 보장이 없다).
"""
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .config import logger
from .paths import DB_PATH, data_dir

SLOTS = 3


def backup_dir() -> Path:
    d = data_dir() / "backup"
    d.mkdir(parents=True, exist_ok=True)
    return d


def slot_path(n: int) -> Path:
    return backup_dir() / f"exam_pool.bak{n}"


# ===== 백업 =====
def _rotate() -> None:
    """bak3 을 버리고 bak2→bak3, bak1→bak2 로 민다. bak1 자리를 비운다."""
    oldest = slot_path(SLOTS)
    if oldest.exists():
        oldest.unlink()
    for n in range(SLOTS - 1, 0, -1):
        src = slot_path(n)
        if src.exists():
            src.replace(slot_path(n + 1))


def make_backup(reason: str = "manual") -> dict:
    """지금 상태를 bak1 로 저장한다. 기존 슬롯은 한 칸씩 밀린다."""
    if not Path(DB_PATH).exists():
        raise FileNotFoundError("백업할 DB 파일이 없습니다.")
    _rotate()
    dest = slot_path(1)
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)          # 온라인 백업 — 잠금·중간상태 걱정 없음
    finally:
        dst.close()
        src.close()
    _write_stamp(reason)
    return {"path": str(dest), "size": dest.stat().st_size, "reason": reason}


def _stamp_file() -> Path:
    return backup_dir() / "last_backup.txt"


def _write_stamp(reason: str) -> None:
    _stamp_file().write_text(
        f"{datetime.now().isoformat(timespec='seconds')}\t{reason}", encoding="utf-8")


def last_backup() -> dict | None:
    f = _stamp_file()
    if not f.exists():
        return None
    try:
        at, _, reason = f.read_text(encoding="utf-8").partition("\t")
        return {"at": at, "reason": reason}
    except OSError:
        return None


def auto_backup_if_due() -> dict | None:
    """서버가 뜰 때 호출. 오늘 백업한 적이 없으면 한 번 뜬다.

    하루 1회로 묶는 이유: 하루에 서버를 여러 번 켜는 사용 방식이라
    켤 때마다 백업하면 슬롯 3개가 같은 날 데이터로 다 차버린다.
    """
    last = last_backup()
    if last and last["at"][:10] == date.today().isoformat():
        return None
    try:
        return make_backup("자동(하루 1회)")
    except (OSError, sqlite3.Error) as e:
        logger.warning("자동 백업 실패: %s", e)
        return None      # 백업 실패로 앱이 안 뜨면 더 손해다


# ===== 조회 =====
def list_backups() -> list[dict]:
    out = []
    for n in range(1, SLOTS + 1):
        p = slot_path(n)
        if not p.exists():
            continue
        st = p.stat()
        out.append({
            "slot": n,
            "path": str(p),
            "size": st.st_size,
            "at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            "counts": _peek_counts(p),
        })
    return out


def _peek_counts(path: Path) -> dict:
    """백업 안에 뭐가 몇 개 들었는지 — 복구 전에 어느 슬롯인지 판단하려면 필요하다."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            def n(table):
                try:
                    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except sqlite3.Error:
                    return 0
            return {"명제": n("proposition"), "문항": n("question"),
                    "세트": n("exam_set"), "근거": n("evidence")}
        finally:
            conn.close()
    except sqlite3.Error:
        return {}


# ===== 복구 =====
def restore(slot: int) -> dict:
    """백업 슬롯으로 되돌린다. 되돌리기 직전 상태는 bak1 로 먼저 남긴다.

    되돌린 뒤 되돌리기 전으로 다시 갈 수 있어야 한다 — 그래서 복구도 백업으로 시작한다.
    백업 파일을 sqlite3 온라인 백업 API로 현재 DB에 되쓴다(shutil.copy 보다 안전).
    """
    src = slot_path(slot)
    if not src.exists():
        raise FileNotFoundError(f"백업 슬롯 {slot}이 없습니다.")

    # 복구 직전 상태를 먼저 백업한다 (되돌리기 전으로 돌아갈 수 있게).
    # make_backup 이 슬롯을 한 칸씩 미므로, 복구 대상 슬롯을 덮어쓰지 않게
    # 대상 파일을 먼저 임시로 복사해 둔다.
    staged = backup_dir() / "_restoring.tmp"
    shutil.copy2(src, staged)
    try:
        try:
            make_backup(f"복구 직전(bak{slot}로 되돌림)")
        except (OSError, sqlite3.Error):
            pass
        # sqlite3 온라인 백업 API 사용 — shutil.copy2 보다 안전하다
        dst_conn = sqlite3.connect(DB_PATH)
        try:
            src_conn = sqlite3.connect(f"file:{staged}?mode=ro", uri=True)
            try:
                src_conn.backup(dst_conn)
            finally:
                src_conn.close()
        finally:
            dst_conn.close()
        for suffix in ("-wal", "-shm"):   # 남은 저널이 되돌린 DB 를 덮어쓰지 않도록
            j = Path(str(DB_PATH) + suffix)
            if j.exists():
                j.unlink()
    finally:
        staged.unlink(missing_ok=True)
    return {"restored_from": f"bak{slot}"}
