import unittest
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.integrations import hwp_security


class TestHwpSecurity(unittest.TestCase):
    def test_frozen_app_uses_bundled_path_checker(self):
        with TemporaryDirectory() as temporary:
            install = Path(temporary)
            executable = install / "ExamPoolHwpConverter.exe"
            checker = install / "pyhwpx" / "FilePathCheckerModule.dll"
            checker.parent.mkdir()
            checker.write_bytes(b"bundled-checker")

            with patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "executable", str(executable)):
                resolved = hwp_security.checker_dll()

        self.assertEqual(resolved, checker)

    def test_non_windows_needs_no_registration(self):
        with patch.object(hwp_security.os, "name", "posix"):
            self.assertTrue(hwp_security.registration_valid())
            ok, _ = hwp_security.ensure_registration()
        self.assertTrue(ok)

    def test_missing_dll_is_reported(self):
        with patch.object(hwp_security.os, "name", "nt"), \
             patch.object(hwp_security, "checker_dll", return_value=None):
            ok, message = hwp_security.ensure_registration()
        self.assertFalse(ok)
        self.assertIn("FilePathCheckerModule.dll", message)

    def test_launcher_runs_security_preflight(self):
        launcher = Path(__file__).resolve().parents[1] / "run.bat"
        text = launcher.read_text(encoding="utf-8", errors="replace")
        self.assertIn("python -m app.integrations.hwp_security", text)
        self.assertIn("if errorlevel 1", text)


if __name__ == "__main__":
    unittest.main()
