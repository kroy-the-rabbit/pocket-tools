# SPDX-License-Identifier: GPL-3.0-or-later
"""Entry point: python -m cheatgui, or tools/cheatgui/run.sh"""
import faulthandler
import os
import signal
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# The GUI's own modules, then tools/cheats for chtparse and ggdecode. The
# parser there is the reference model the RTL is checked against, so the GUI
# reads cheat files through exactly the code the core is verified to match.
# This comes first because everything below is one of those modules.
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(os.path.dirname(HERE), "cheats"))

import say                                                   # noqa: E402

# Everything this app does runs on the Tk thread, so anything that blocks looks
# identical from outside: a window that stops repainting. `kill -USR1 <pid>`
# prints the stack of wherever it actually is, which beats guessing.
#
# A windowed build has no stderr to print it to, and enabling faulthandler
# anyway raises before the window opens; say.stream() finds the console the exe
# was launched from, or the log file, and None means there is nowhere at all.
_dump = say.stream()
if _dump is not None:
    faulthandler.enable(_dump)
    if hasattr(signal, "SIGUSR1"):
        faulthandler.register(signal.SIGUSR1, file=_dump)


def check_db() -> int:
    """Prints what a support question needs: which build, which database, and
    whether this machine can actually reach and verify upstream.

    A frozen binary carries its own CA bundle and there is no other way to find
    out whether the one it carries works.
    """
    import db
    import ssl
    import version

    lines: list[str] = []

    def report(line: str) -> None:
        lines.append(line)
        say.out(line)

    report(f"version:  {version.version()}"
           f"{' (packaged)' if version.frozen() else ' (checkout)'}")
    report(f"database: {db.db_dir()}")
    local = db.local_state()
    report(f"local:    {db.describe(local)}")
    try:
        import certifi
        report(f"ca store: {certifi.where()} (bundled)")
    except ImportError:
        p = ssl.get_default_verify_paths()
        report(f"ca store: {p.cafile or p.capath} (system)")
    try:
        remote = db.remote_state(timeout=20)
        report(f"upstream: {remote['sha'][:10]} {db.day(remote['date'])}")
        report("verdict:  upstream reachable and verified")
        rc = 0
    except Exception as e:                                   # noqa: BLE001
        report(f"upstream: {type(e).__name__}: {e}")
        report("verdict:  COULD NOT REACH UPSTREAM")
        rc = 1

    if not say.visible():
        # Double clicked on Windows or macOS: the report went to the log and
        # nobody would ever see it. This is the one command whose entire point
        # is being read back to somebody, so put it on the screen.
        where = say.log_path()
        alert("Database check",
              "\n".join(lines) + (f"\n\nAlso written to {where}" if where else ""))
    return rc


def alert(title: str, text: str) -> None:
    """A box, for when there is no console to print to.

    Best effort by design: if Tk will not start then the app was never going to
    run either, and the log file still has the text.
    """
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showinfo(f"Pocket Tools: {title}", text)
        root.destroy()
    except Exception:                                        # noqa: BLE001
        pass


def main() -> int:
    if "--check-db" in sys.argv:
        return check_db()

    if "--list" in sys.argv:
        # Read only, so any number of these can run at once.
        import cli
        return cli.main(sys.argv[1:])

    import single
    handle, holder = single.acquire()
    if handle is None:
        text = (f"Pocket Tools is already running (pid {holder}).\n"
                "Use that window, or close it first.")
        say.err(text)
        if not say.visible():
            # Otherwise a second launch does nothing at all, on the platforms
            # where double clicking the icon again is the obvious thing to try.
            alert("Already running", text)
        return 1

    import ui
    try:
        return ui.main()
    finally:
        # Explicit, so the lock goes as the window does rather than whenever
        # the handle happens to be collected.
        if hasattr(handle, "close"):
            handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
