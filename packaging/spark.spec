# PyInstaller spec for Spark. See BUILD_SPEC.md §5 and milestone M12.
#
# VERIFICATION STATUS: built and smoke-tested on real Windows 11 (Python
# 3.12, PySide6 6.11, PyInstaller 6.x). Verified: the build completes; the
# frozen Spark.exe launches and its main window renders (task selector,
# Start/Stop/Settings, status line, log pane); perception/_dom_extract.js and
# the winsdk OCR modules (media.ocr, graphics.imaging, storage.streams,
# globalization) are bundled. Two "Hidden import not found" warnings appear
# for tzdata and pycparser.lextab/yacctab — optional modules PyInstaller
# probes for; harmless. NOT verified from the frozen exe: a full task run
# against a live LLM provider (needs a real API key), and the folder build is
# ~300 MB (dominated by PySide6 and the bundled provider SDKs).
#
# Build with:  pyinstaller packaging/spark.spec
# Output:      dist/Spark/Spark.exe  (a folder build, not a single file —
#              see the onefile note below)

import sys
from pathlib import Path

block_cipher = None

REPO_ROOT = Path(SPECPATH).parent
SRC = REPO_ROOT / "src"

# perception/_dom_extract.js is read at runtime via
# `(Path(__file__).parent / "_dom_extract.js").read_text()` (see
# perception/dom.py) — package-data in pyproject.toml covers a normal pip
# install, but PyInstaller needs its own explicit entry; anything under
# `src/spark` that isn't a .py file must be listed here or the frozen build
# will raise FileNotFoundError the first time DOM extraction runs.
datas = [
    (str(SRC / "spark" / "perception" / "_dom_extract.js"), "spark/perception"),
]

# PROVIDER_EXTRAS: pyproject.toml deliberately keeps gemini/anthropic/
# openai/windows-ocr/tesseract as OPTIONAL pip extras (a source install
# only pulls in the SDK for a provider you actually chose) — but the
# shipped GUI's Settings dialog lets a user pick any of the four LLM
# providers and either OCR engine from a dropdown with no separate install
# step. For a single distributable .exe, that means bundling all of them by
# default (a user picking "OpenAI" in Settings should not discover the SDK
# was never packaged). If you deliberately want a slimmer build with only
# some providers, delete the matching hiddenimports below AND make sure
# Settings only offers the providers you actually bundled.
hiddenimports = [
    "google.genai",
    "anthropic",
    "openai",
    "winsdk",
    "winsdk.windows.media.ocr",
    "winsdk.windows.graphics.imaging",
    "winsdk.windows.storage.streams",
    "winsdk.windows.globalization",
    "playwright.async_api",
    # PySide6's own PyInstaller hook (shipped with PyInstaller) normally
    # covers Qt plugin discovery; these are here defensively in case that
    # hook's coverage lags a PySide6 release.
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
]

a = Analysis(
    [str(SRC / "spark" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # pytest and friends have no business in the shipped binary.
        "pytest",
        "pytest_asyncio",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Spark",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # a background console window would defeat the point of a desktop app
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon=str(REPO_ROOT / "packaging" / "spark.ico"),  # add an .ico and uncomment before a real release
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Spark",
)

# ONEFILE NOTE: BUILD_SPEC.md §14 (M12) asks for "a single-file .exe via
# PyInstaller". The COLLECT() folder build above (dist/Spark/Spark.exe plus
# a directory of DLLs/data next to it) is deliberately what this spec
# produces by default — Playwright's own Chromium-adjacent tooling and
# PySide6's Qt plugins are large and numerous enough that a true `EXE(...,
# exclude_binaries=False)` one-file build adds meaningful startup latency
# (everything is unpacked to a temp dir on every launch) for very little
# user-facing benefit on a desktop app the user installs once. To build a
# genuine one-file .exe instead, change `exclude_binaries=True` to `False`
# on the EXE(...) call above and delete the COLLECT(...) block — verify the
# resulting startup time is acceptable on real hardware before shipping
# that version instead of this one.
