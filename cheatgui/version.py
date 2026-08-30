# SPDX-License-Identifier: GPL-3.0-or-later
"""The app's version.

Set here rather than derived from git, because a released binary has no
checkout to ask. The release workflow rewrites VERSION from the tag it is
building, so a downloaded build always names the tag it came from and a run
from a checkout says so instead.
"""
from __future__ import annotations

import os
import sys

VERSION = "0.0.0-dev"


def version() -> str:
    return os.environ.get("POCKET_CHEATS_VERSION") or VERSION


def asset(name: str) -> str:
    """A file from assets/, wherever this is running from.

    Frozen, PyInstaller unpacks datas next to the executable's temporary root
    and points sys._MEIPASS at it. From a checkout it is just the repository.
    """
    if frozen():
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        return os.path.join(base, "assets", name)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "assets", name)


def frozen() -> bool:
    """True in a packaged build, where there is no repository alongside."""
    return bool(getattr(sys, "frozen", False))


def label() -> str:
    """Short version for the window, always something.

    A checkout says "dev" rather than nothing. The version is here to be read
    off a screenshot in a bug report, and the builds most likely to be in one
    are the unreleased ones.
    """
    v = version()
    return "dev" if v.startswith("0.0.0") else "v" + v


def title() -> str:
    return f"Pocket Tools  {label()}"
