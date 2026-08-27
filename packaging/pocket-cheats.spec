# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
#
# One self-contained binary per platform. Everything the app uses is in the
# Python standard library, so there is nothing to vendor here beyond Python
# and Tk themselves; the cheat database is not bundled and is fetched by the
# app on first run, which keeps third-party content out of the release and the
# artifact under 20 MB.
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(SPEC)), ".."))
GUI = os.path.join(ROOT, "cheatgui")
SHARED = os.path.join(ROOT, "cheats")

# The app's modules are flat, not a package: __main__.py puts both directories
# on sys.path at run time. Frozen there is no sys.path to arrange, so they are
# named here instead and PyInstaller bundles them as top-level modules.
HIDDEN = [
    "card", "carts", "cheatfile", "cheatlib", "cli", "core", "db", "gba",
    "library", "match", "meter",
    "model", "nointro", "pce", "prefs", "reveal", "say", "single", "timing",
    "ui", "version", "work", "writer",
    "chtparse", "ggdecode", "gbacht", "cht2bin",
    # The only third-party thing in a build, and only because a frozen binary
    # has no trust store it can rely on: see db.ssl_context().
    "certifi",
]

# Nothing here talks to a database, a web framework or a notebook, and the
# analysis pulls a surprising amount of that in through the standard library.
EXCLUDE = [
    "numpy", "PIL", "pytest", "setuptools", "pip", "sqlite3", "unittest",
    "pydoc", "doctest", "lib2to3", "test", "distutils", "email.test",
]

a = Analysis(
    [os.path.join(GUI, "__main__.py")],
    pathex=[GUI, SHARED],
    binaries=[],
    # The window icon, which Tk loads at run time from a real file.
    datas=[(os.path.join(ROOT, "assets", "icon.png"), "assets"),
           (os.path.join(ROOT, "assets", "icon-64.png"), "assets")],
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDE,
    noarchive=False,
)
pyz = PYZ(a.pure)

# A console on Windows would open a black window behind the GUI. On Linux the
# flag means nothing: a binary started from a terminal keeps that terminal.
console = sys.platform not in ("win32", "darwin")

# The executable's own icon, which is a different thing from the window icon
# and has to be a format each platform recognises. The .icns is built from the
# committed iconset by the mac job; if it is not there, PyInstaller is given
# nothing rather than something it cannot read.
def icon_for(platform):
    name = {"win32": "icon.ico", "darwin": "icon.icns"}.get(platform)
    if not name:
        return None
    path = os.path.join(ROOT, "assets", name)
    return path if os.path.exists(path) else None


ICON = icon_for(sys.platform)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="pocket-cheats",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=console,
    icon=ICON,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if sys.platform == "darwin":
    # A .app so it can be double clicked and shows a name in the dock rather
    # than "python". LSBackgroundOnly stays off: this is a normal windowed app.
    app = BUNDLE(
        exe,
        name="Pocket Cheats.app",
        icon=ICON,
        bundle_identifier="io.kroy.pocket-cheats",
        info_plist={
            "CFBundleName": "Pocket Cheats",
            "CFBundleDisplayName": "Pocket Cheats",
            "CFBundleShortVersionString": os.environ.get(
                "POCKET_CHEATS_VERSION", "0.0.0"),
            "NSHighResolutionCapable": True,
            # The app reads and writes an SD card, which macOS treats as a
            # removable volume and gates behind this prompt.
            "NSRemovableVolumesUsageDescription":
                "Pocket Cheats reads and writes cheat files on your Analogue "
                "Pocket SD card.",
        },
    )
