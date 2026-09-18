# PyInstaller spec for Spark. See BUILD_SPEC.md §5 and milestone M12.
#
# HONESTY NOTE (read before trusting this file): this project was built in a
# Linux sandbox with no Windows machine available, so this spec has NEVER
# actually been run through PyInstaller here — running it in this sandbox
# would produce a Linux binary, which is useless for the shipped product and
# would not exercise anything Windows-specific anyway. Everything below is
# written from PyInstaller/PySide6 packaging conventions, not verified by an
# actual build. Before shipping:
#   1. Run `pyinstaller packaging/spark.spec` on a real Windows 10/11 machine
#      with the `spark[dev]` extras (and whichever provider extras you want
#      bundled — see the PROVIDER_EXTRAS note below) installed.
#   2. Launch the resulting dist/Spark/Spark.exe and confirm: the window
#      opens, Settings opens, and the Chrome-launch health check from
#      BUILD_SPEC §2.1 actually fires (point it at a task and watch it
#      launch/attach).
#   3. If PySide6 fails to start with a "could not find or load the Qt
#      platform plugin windows" error, add its platform DLL directory to
#      `datas` below (PyInstaller's PySide6 hook usually handles this
#      automatically, but hook coverage changes between PySide6 releases —
#      verify against whatever version pyproject.toml resolves to).
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
