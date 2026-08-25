# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build for the standalone PDF-to-HWP desktop web app."""

from pathlib import Path

import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, copy_metadata

ROOT = Path(SPECPATH).parents[1]
VENDOR_ROOT = ROOT / "vendor" / "hwp_typesetter"
OCR_RUNTIME = Path(
    os.environ.get("EXAMPOOL_OCR_RUNTIME", ROOT / "data" / "pdf_hwp_ocr_runtime")
).resolve()
LEGAL_DIR = Path(os.environ.get("EXAMPOOL_LEGAL_DIR", ROOT / "build" / "legal")).resolve()
BUILD_NAME = os.environ.get("EXAMPOOL_BUILD_NAME", "ExamPool-HWP-Converter")
BUNDLE_OCR = os.environ.get("EXAMPOOL_BUNDLE_OCR", "1") != "0"
WINDOWED = os.environ.get("EXAMPOOL_WINDOWED", "1") != "0"
OCR_PACKAGES = ("paddleocr", "paddlex")
OCR_METADATA = (
    "imagesize",
    "opencv-contrib-python",
    "pyclipper",
    "pypdfium2",
    "python-bidi",
    "shapely",
)

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(VENDOR_ROOT))
sys.path.insert(0, str(OCR_RUNTIME))
if not OCR_RUNTIME.is_dir():
    raise SystemExit(f"OCR runtime directory is missing: {OCR_RUNTIME}")

datas = [
    (str(ROOT / "static"), "static"),
    (str(ROOT / "assets" / "icon.svg"), "assets"),
    (str(ROOT / "assets" / "hwp_templates"), "assets/hwp_templates"),
    (str(ROOT / "app" / "seed"), "app/seed"),
    (str(ROOT / "app" / "pdf_hwp_type_catalog.json"), "app"),
    (str(VENDOR_ROOT), "vendor/hwp_typesetter"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
    (str(ROOT / "HANCOM_AUTOMATION_NOTICE.md"), "."),
    (str(ROOT / "packaging" / "windows" / "paddle-libs.marker"), "paddle/libs"),
    (str(OCR_RUNTIME / "paddle" / "libs" / "mklml.dll"), "paddle/libs"),
]
if LEGAL_DIR.is_dir():
    datas.append((str(LEGAL_DIR), "licenses/third-party"))
binaries = collect_dynamic_libs("pyhwpx", destdir="pyhwpx")
hiddenimports = [
    "app.integrations.hwp_security",
    "pythoncom",
    "pywintypes",
    "setuptools.command.build_ext",
    "setuptools.command.easy_install",
    "setuptools.command.install",
    "symtable",
    "wave",
]
if BUNDLE_OCR:
    for distribution_name in OCR_METADATA:
        datas += copy_metadata(distribution_name)
    for package_name in OCR_PACKAGES:
        package_datas, package_binaries, package_imports = collect_all(
            package_name,
            include_py_files=False,
            exclude_datas=["**/tests/**", "**/test/**", "**/include/**"],
        )
        datas += package_datas
        binaries += package_binaries
        hiddenimports += package_imports

analysis = Analysis(
    [str(ROOT / "run_pdf_hwp_webapp.py")],
    pathex=[str(ROOT), str(VENDOR_ROOT), str(OCR_RUNTIME)],
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
        "tkinter",
        "tzdata",
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
    console=not WINDOWED,
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
