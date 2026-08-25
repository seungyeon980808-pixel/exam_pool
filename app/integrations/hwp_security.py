"""Install and verify Hancom's FilePathCheckerModule for HWP automation.

Registration belongs to ExamPool startup. Preview workers may be restricted and
must not depend on being able to edit the current user's registry themselves.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import sys


VALUE_NAME = "FilePathCheckerModule"
KEY_PATHS = (
    r"Software\HNC\HwpAutomation\Modules",
    r"Software\Hnc\HwpUserAction\Modules",
)


def checker_dll() -> Path | None:
    if getattr(sys, "frozen", False):
        bundled = Path(sys.executable).resolve().parent / "pyhwpx" / "FilePathCheckerModule.dll"
        if bundled.is_file():
            return bundled
    spec = importlib.util.find_spec("pyhwpx")
    if spec is None or spec.origin is None:
        return None
    path = Path(spec.origin).resolve().parent / "FilePathCheckerModule.dll"
    return path if path.is_file() else None


def _views(winreg) -> tuple[int, ...]:
    return tuple(dict.fromkeys((0, winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY)))


def registration_valid() -> bool:
    if os.name != "nt":
        return True
    dll = checker_dll()
    if dll is None:
        return False
    try:
        import winreg

        expected = str(dll).casefold()
        for key_path in KEY_PATHS:
            found = False
            for view in _views(winreg):
                try:
                    with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER, key_path, 0,
                        winreg.KEY_QUERY_VALUE | view,
                    ) as key:
                        value, _ = winreg.QueryValueEx(key, VALUE_NAME)
                    if str(value).casefold() == expected:
                        found = True
                        break
                except OSError:
                    continue
            if not found:
                return False
        return True
    except (ImportError, OSError):
        return False


def ensure_registration() -> tuple[bool, str]:
    if os.name != "nt":
        return True, "Windows가 아니므로 HWP 보안 모듈 등록을 건너뜁니다."
    dll = checker_dll()
    if dll is None:
        return False, "pyhwpx의 FilePathCheckerModule.dll을 찾지 못했습니다."
    if registration_valid():
        return True, f"HWP 보안 모듈 등록 확인: {dll}"
    try:
        import winreg

        for key_path in KEY_PATHS:
            for view in _views(winreg):
                with winreg.CreateKeyEx(
                    winreg.HKEY_CURRENT_USER, key_path, 0,
                    winreg.KEY_SET_VALUE | view,
                ) as key:
                    winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, str(dll))
    except (ImportError, OSError) as exc:
        return False, (
            "HWP 파일 경로 승인 모듈을 현재 사용자 레지스트리에 등록하지 "
            f"못했습니다: {exc}"
        )
    if not registration_valid():
        return False, "HWP 보안 모듈을 기록했지만 등록 값을 다시 확인하지 못했습니다."
    return True, f"HWP 보안 모듈 등록 완료: {dll}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="등록 여부만 확인")
    args = parser.parse_args(argv)
    if args.check:
        ok = registration_valid()
        message = "HWP 보안 모듈 등록 정상" if ok else "HWP 보안 모듈 등록 필요"
    else:
        ok, message = ensure_registration()
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
