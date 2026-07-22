"""롤링 백업 — 슬롯 회전과 복구가 데이터를 잃지 않는지."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import backup


def write_db(path: Path, marker: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS proposition (id INTEGER PRIMARY KEY, text TEXT)")
        conn.execute("INSERT INTO proposition (text) VALUES (?)", (marker,))
        conn.commit()
    finally:
        conn.close()


def read_markers(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT text FROM proposition ORDER BY id")]
    finally:
        conn.close()


class BackupCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "exam_pool.db"
        write_db(self.db, "v1")
        self._patches = [
            mock.patch.object(backup, "DB_PATH", self.db),
            mock.patch.object(backup, "data_dir", lambda: root),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()


class TestRotation(BackupCase):
    def test_first_backup_lands_in_slot1(self):
        backup.make_backup("t")
        self.assertTrue(backup.slot_path(1).exists())
        self.assertEqual(read_markers(backup.slot_path(1)), ["v1"])

    def test_slots_shift_and_oldest_drops(self):
        for v in ("v1", "v2", "v3", "v4"):
            if v != "v1":
                write_db(self.db, v)
            backup.make_backup("t")
        # bak1 이 가장 최신, bak3 이 가장 오래됨. 4번째 백업에서 첫 상태는 밀려났다.
        self.assertEqual(read_markers(backup.slot_path(1))[-1], "v4")
        self.assertEqual(read_markers(backup.slot_path(2))[-1], "v3")
        self.assertEqual(read_markers(backup.slot_path(3))[-1], "v2")
        self.assertEqual(len(backup.list_backups()), 3)

    def test_counts_are_peeked(self):
        backup.make_backup("t")
        slot = backup.list_backups()[0]
        self.assertEqual(slot["counts"]["명제"], 1)


class TestAutoBackup(BackupCase):
    def test_runs_once_a_day(self):
        self.assertIsNotNone(backup.auto_backup_if_due())
        self.assertIsNone(backup.auto_backup_if_due())   # 같은 날 두 번째는 뜨지 않는다


class TestRestore(BackupCase):
    def test_restore_oldest_slot_survives_rotation(self):
        """복구는 백업을 한 번 더 뜬다 — 목표 슬롯이 밀려 사라지면 안 된다."""
        backup.make_backup("t")                  # bak1 = v1
        write_db(self.db, "v2"); backup.make_backup("t")
        write_db(self.db, "v3"); backup.make_backup("t")   # bak1=v3 bak2=v2 bak3=v1
        write_db(self.db, "v4")                  # 현재 DB 는 v4

        backup.restore(3)                        # 가장 오래된 상태로
        self.assertEqual(read_markers(self.db), ["v1"])

    def test_state_before_restore_is_kept(self):
        backup.make_backup("t")
        write_db(self.db, "v2")
        backup.restore(1)
        self.assertEqual(read_markers(self.db), ["v1"])
        # 되돌리기 직전(v1,v2)이 어느 슬롯엔가 남아 있어야 다시 갈 수 있다
        saved = [read_markers(Path(s["path"])) for s in backup.list_backups()]
        self.assertIn(["v1", "v2"], saved)

    def test_missing_slot_raises(self):
        with self.assertRaises(FileNotFoundError):
            backup.restore(2)


if __name__ == "__main__":
    unittest.main()
