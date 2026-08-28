# SPDX-License-Identifier: GPL-3.0-or-later
"""Opening a directory in whatever file manager the machine has.

The app knows a dozen paths -- the cheat database, the log, the card, the
cartridge files on it -- and used to show them as text for the user to copy out
and paste somewhere else. This is the one click that does it instead, and it is
the same three commands everywhere: xdg-open on Linux, open on macOS, explorer
on Windows.

Nothing here raises. Failing to open a file manager is not worth an exception,
for the same reason say.py will not raise over a line it cannot print: the user
still has the path on screen, and a traceback out of a button that was only
ever a convenience would cost more than the convenience was worth.

Three things this has to get right, all of them learned the hard way:

  * explorer.exe returns a non-zero exit code even when it worked, so the exit
    code is never consulted. Success here means the command was launched.
  * a sandboxed build may have no file manager to call at all, under Flatpak or
    Snap, so `available()` and `openable()` let the UI ask before it draws the
    button rather than offering one that fails.
  * a path may not exist yet -- the library before it is chosen, the log before
    anything is written -- so a directory that is not there is refused, and
    never created as a side effect of being asked to look at it.
"""
from __future__ import annotations

import os
import subprocess
import sys

import say

# Launched, not waited for. Each is finished within moments -- these commands
# hand the path to the desktop and exit -- but nothing here blocks the Tk
# thread to find out, so the handles are kept and cleared on the next call.
# Dropping them instead would leave a zombie for every button press.
_launched: list = []


def available() -> bool:
    """True if this machine has a command that can open a directory.

    False under a sandbox that ships without one, and the answer the UI wants
    before it draws a button: a button that cannot work should not appear.
    """
    return _which(_command()) is not None


def openable(path: str | None) -> bool:
    """True if offering to open `path` makes sense: a directory, and a way in."""
    return bool(path) and os.path.isdir(path) and available()


def directory(path: str) -> bool:
    """Open `path` in the file manager. True if the command was launched.

    False means there was nothing to open or nothing to open it with, which is
    a fact about the machine rather than an error, and is logged as such.
    """
    if not path or not os.path.isdir(path):
        say.err(f"reveal: {path!r} is not a directory")
        return False
    return _launch(path)


def website(url: str) -> bool:
    """Open a page in the browser. True if the handoff was made.

    Here rather than anywhere else because it is the same job as the rest of
    this module - hand something to the desktop and stop caring - and it fails
    the same way, quietly. `webbrowser` is standard library and picks the same
    opener the file manager calls do, so this needs no command table of its
    own; it can still return False on a machine with no browser configured,
    and the URL is always on screen beside the button that offers it.
    """
    if not url.startswith(("http://", "https://")):
        # Never hand an arbitrary string to a URL opener. Nothing in this app
        # builds one from user input today, and this is what keeps that true.
        say.err(f"reveal: refusing to open {url!r}")
        return False
    try:
        import webbrowser
        return bool(webbrowser.open(url))
    except Exception as e:                                   # noqa: BLE001
        say.err(f"reveal: cannot open {url}: {e}")
        return False


def containing(path: str) -> bool:
    """Open the directory holding `path`, for pointing at one file.

    The file itself need not exist. Somewhere to put it is what the user is
    being shown, and that is the directory, which does have to be there.
    """
    if not path:
        return False
    return directory(os.path.dirname(os.path.abspath(path)))


def _command() -> str:
    if sys.platform == "win32":
        return "explorer"
    if sys.platform == "darwin":
        return "open"
    return "xdg-open"


def _which(cmd: str):
    """shutil is not free to import, and this is called to draw a button."""
    import shutil
    return shutil.which(cmd)


def _launch(path: str) -> bool:
    """Start the file manager detached. Never raises, never blocks."""
    cmd = _command()
    if _which(cmd) is None:
        say.err(f"reveal: no {cmd} on this machine")
        return False

    kw = {}
    if os.name == "nt":
        # explorer rejects forward slashes, and the paths this is handed are
        # built with os.path.join from card roots the user typed.
        path = os.path.normpath(path)
        kw["creationflags"] = subprocess.DETACHED_PROCESS
    else:
        # Its own session, so a file manager started from a terminal build does
        # not die with the terminal and does not take Ctrl-C meant for us.
        kw["start_new_session"] = True

    try:
        _launched[:] = [p for p in _launched if p.poll() is None]
        _launched.append(subprocess.Popen(
            [cmd, path], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw))
    except Exception as e:                                   # noqa: BLE001
        say.err(f"reveal: {cmd} would not start: {e}")
        return False

    # Deliberately not the exit code. explorer.exe returns 1 on success, and
    # waiting for any of them would block the Tk thread for a launcher that
    # has already done its job.
    return True
