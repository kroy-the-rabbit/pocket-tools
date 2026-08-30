# SPDX-License-Identifier: GPL-3.0-or-later
"""One GUI at a time.

Two copies pointed at the same card can each hold a different idea of what is
installed, and whichever saves last silently wins: the first window's ticks are
still on screen, still look authoritative, and are already stale. Cheap to
prevent, so prevent it.

An advisory lock on a file rather than a PID file, because the kernel drops it
when the process ends however it ends. A PID file left behind by a crash needs
liveness checks and can still collide after PID reuse.
"""
from __future__ import annotations

import errno
import os

try:
    import fcntl
except ImportError:                                          # noqa: BLE001
    fcntl = None                                             # Windows: no lock


def lock_path() -> str:
    """Runtime dir if there is one, since a lock should not outlive the login."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not base or not os.path.isdir(base):
        base = os.path.join(
            os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
            "pocket-cheats")
        os.makedirs(base, exist_ok=True)
    return os.path.join(base, "cheatgui.lock")


def acquire() -> tuple[object | None, str]:
    """Take the lock. Returns (handle, holder); handle is None if refused.

    Keep the handle for the life of the process: closing it releases the lock.
    `holder` describes who has it, for the message.
    """
    if fcntl is None:
        return object(), ""
    path = lock_path()
    try:
        fd = open(path, "a+")
    except OSError as e:                                     # noqa: BLE001
        return object(), f"(could not open {path}: {e})"     # never block on this

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno not in (errno.EACCES, errno.EAGAIN):
            fd.close()
            return object(), ""                              # unexpected: allow
        fd.seek(0)
        holder = fd.read().strip()
        fd.close()
        return None, holder or "unknown"

    fd.seek(0)
    fd.truncate()
    fd.write(str(os.getpid()))
    fd.flush()
    return fd, ""
