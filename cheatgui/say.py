# SPDX-License-Identifier: GPL-3.0-or-later
"""Writing a line when there may be nowhere to write it.

A windowed build has no console, so Python hands it sys.stdout and sys.stderr
of None. print() then raises rather than being ignored, and faulthandler
refuses to start at all: `RuntimeError: sys.stderr is None`, which is what
killed the Windows binary before its window ever opened. A debugging aid took
the whole app down on the one platform that could not print.

So every line the app writes outside the GUI comes through here, and lands in
the first of these that exists:

  * the interpreter's own stream, when there is one -- a checkout, a terminal,
    the Linux build, which is the ordinary case and costs an attribute check;
  * the console the exe was launched from, when a windowed Windows build was
    started from cmd or PowerShell, since Windows does not hand a GUI process
    its parent's console unless it asks;
  * a log file beside the app's data, so a crash dump from a double clicked
    build is still readable afterwards;
  * nowhere, quietly, which is all a missing debug line should ever cost.
"""
from __future__ import annotations

import os
import sys

# Room for a faulthandler dump and a long session of timing lines. Past this
# the file starts over: nothing here is worth an unbounded file in someone's
# data directory, and the interesting part is always the end.
LOG_MAX = 512 * 1024

_resolved = False
_spare_stream = None
_spare_is_console = False
_log_file: str | None = None


def out(text: str = "") -> None:
    """A line to stdout, or to wherever stdout went."""
    _write(sys.stdout if sys.stdout is not None else _spare(), text)


def err(text: str = "") -> None:
    """A line to stderr, same fallbacks."""
    _write(sys.stderr if sys.stderr is not None else _spare(), text)


def stream():
    """A real stream, for something that does its own writing, or None.

    faulthandler dumps from a signal handler and cannot call back into Python,
    so it needs the file object itself rather than a function. None means this
    machine offers nowhere to dump to, and the caller should not enable it.
    """
    return sys.stderr if sys.stderr is not None else _spare()


def visible() -> bool:
    """True if a line written now reaches somebody who is looking at a screen.

    False means the app is windowed and was not started from a console: the
    text is being kept, not shown, and anything the user is meant to read has
    to go on screen some other way.
    """
    if sys.stdout is not None or sys.stderr is not None:
        return True
    _spare()
    return _spare_is_console


def log_path() -> str | None:
    """The log file lines are landing in, if it came to that."""
    _spare()
    return _log_file


def _spare():
    """The stream to use when the interpreter has none. Worked out once."""
    global _resolved, _spare_stream, _spare_is_console
    if not _resolved:
        _resolved = True
        _spare_stream = _attach_console()
        _spare_is_console = _spare_stream is not None
        if _spare_stream is None:
            _spare_stream = _open_log()
    return _spare_stream


def _attach_console():
    """The console that launched a windowed Windows build, if there was one.

    A GUI subsystem exe gets no console even when it was started from one, so
    `pocket-tools.exe --check-db` from cmd would otherwise print into the
    void. AttachConsole(ATTACH_PARENT_PROCESS) borrows the caller's; it fails
    when the app was double clicked from Explorer, which is the answer we want.

    CONOUT$ is the console's screen buffer, so this reaches the window even if
    the caller redirected stdout to a file. The prompt has usually come back by
    then and the lines land under it, which looks untidy and is still the only
    place they could go.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        if not ctypes.windll.kernel32.AttachConsole(-1):
            return None
        return open("CONOUT$", "w", buffering=1, errors="replace")
    except Exception:                                        # noqa: BLE001
        return None


def _open_log():
    """A file beside the app's own data, so support has one place to ask for."""
    global _log_file
    try:
        import db                                            # not free: lazy
        path = os.path.join(db.store(), "pocket-cheats.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "a"
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX:
            mode = "w"
        f = open(path, mode, buffering=1, errors="replace")
        _log_file = path
        return f
    except Exception:                                        # noqa: BLE001
        return None


def _write(stream, text: str) -> None:
    """Never raises. A line the user cannot see is not worth an exception."""
    if stream is None:
        return
    try:
        stream.write(text + "\n")
        stream.flush()
    except Exception:                                        # noqa: BLE001
        pass
