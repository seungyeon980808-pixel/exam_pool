# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build for the standalone PDF-to-HWP desktop web app."""

from pathlib import Path

import os
import sys

ROOT = Path(SPECPATH).parents[1]
VENDOR_ROOT = ROOT / "vendor" / "hwp_typesetter"
OCR_RUNTIME = Path(
    os.environ.get("EXAMPOOL_OCR_RUNTIME", ROOT / "data" / "pdf_hwp_ocr_runtime")
).resolve()
LEGAL_DIR = Path(os.environ.get("EXAMPOOL_LEGAL_DIR", ROOT / "build" / "legal")).resolve()
BUILD_NAME = os.environ.get("EXAMPOOL_BUILD_NAME", "ExamPool-HWP-Converter")
BUNDLE_OCR = os.environ.get("EXAMPOOL_BUNDLE_OCR", "1") != "0"


def collect_runtime_datas(root):
    excluded_directories = {"tests", "test", "__pycache__", "include"}
    excluded_suffixes = {".pyc", ".pyo", ".lib"}
    collected = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in excluded_directories for part in relative.parts):
            continue
        if not path.is_file() or path.suffix.lower() in excluded_suffixes:
            continue
        collected.append((str(path), str(Path("ocr_runtime") / relative.parent)))
    return collected

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(VENDOR_ROOT))
if not OCR_RUNTIME.is_dir():
    raise SystemExit(f"OCR runtime directory is missing: {OCR_RUNTIME}")

datas = [
    (str(ROOT / "static"), "static"),
    (str(ROOT / "assets" / "icon.svg"), "assets"),
    (str(ROOT / "app" / "seed"), "app/seed"),
    (str(ROOT / "app" / "pdf_hwp_type_catalog.json"), "app"),
    (str(VENDOR_ROOT), "vendor/hwp_typesetter"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
    (str(ROOT / "HANCOM_AUTOMATION_NOTICE.md"), "."),
]
if LEGAL_DIR.is_dir():
    datas.append((str(LEGAL_DIR), "licenses/third-party"))
if BUNDLE_OCR:
    datas += collect_runtime_datas(OCR_RUNTIME)
binaries = []
hiddenimports = [
    "hwp_security",
    "pythoncom",
    "pywintypes",
    "setuptools.command.build_ext",
    "setuptools.command.easy_install",
    "setuptools.command.install",
    "symtable",
    "wave",
]

analysis = Analysis(
    [str(ROOT / "run_pdf_hwp_webapp.py")],
    pathex=[str(ROOT), str(VENDOR_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "playwright",
        "pytest",
        "sklearn",
        "tensorflow",
        "torch",
        "transformers",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="ExamPoolHwpConverter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory=".",
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=BUILD_NAME,
)
