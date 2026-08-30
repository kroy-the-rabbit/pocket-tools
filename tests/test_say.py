# SPDX-License-Identifier: GPL-3.0-or-later
"""Lines written when there is nowhere to write them.

A windowed build hands the app sys.stdout and sys.stderr of None, and print()
raises there instead of doing nothing. That is not a hypothetical: it crashed
the Windows binary at startup, on the faulthandler call meant to help diagnose
crashes. So the interesting cases here are all the ones with no streams at all.
"""
from __future__ import annotations

import faulthandler
import io
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "cheatgui"))
sys.path.insert(1, os.path.join(ROOT, "cheats"))

import say                                                    # noqa: E402


class Say(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env(XDG_DATA_HOME=self.tmp.name)
        self.streams(sys.stdout, sys.stderr)
        self.reset()
        self.addCleanup(self.reset)

    def env(self, **kw) -> None:
        for k, v in kw.items():
            old = os.environ.get(k)
            os.environ[k] = v
            self.addCleanup(lambda k=k, old=old:
                            os.environ.__setitem__(k, old) if old is not None
                            else os.environ.pop(k, None))

    def streams(self, out, err) -> None:
        """Swap both streams for the duration of one test."""
        old = sys.stdout, sys.stderr
        self.addCleanup(lambda: setattr_both(*old))
        setattr_both(out, err)

    def reset(self) -> None:
        """Forget where say decided to write; it works that out once."""
        if say._spare_stream is not None:
            try:
                say._spare_stream.close()
            except Exception:                                 # noqa: BLE001
                pass
        say._resolved = False
        say._spare_stream = None
        say._spare_is_console = False
        say._log_file = None

    # -- with streams, nothing clever happens ---------------------------

    def test_uses_the_streams_it_is_given(self):
        out, err = io.StringIO(), io.StringIO()
        self.streams(out, err)
        say.out("to stdout")
        say.err("to stderr")
        self.assertEqual(out.getvalue(), "to stdout\n")
        self.assertEqual(err.getvalue(), "to stderr\n")
        self.assertTrue(say.visible())
        self.assertIs(say.stream(), err)
        # Nothing was opened: no console, no log, no data directory created.
        self.assertIsNone(say._spare_stream)
        self.assertEqual(os.listdir(self.tmp.name), [])

    # -- without them, the windowed build -------------------------------

    def test_no_streams_is_not_an_error(self):
        self.streams(None, None)
        say.out("still said")
        say.err("also said")
        path = say.log_path()
        self.assertIsNotNone(path)
        with open(path) as f:
            self.assertEqual(f.read(), "still said\nalso said\n")

    def test_nothing_visible_without_a_console(self):
        self.streams(None, None)
        self.assertFalse(say.visible())

    def test_faulthandler_can_be_enabled(self):
        """The crash itself: enable() checked sys.stderr, found None and raised
        before the window opened."""
        self.streams(None, None)
        dump = say.stream()
        self.assertIsNotNone(dump)
        was_on = faulthandler.is_enabled()
        try:
            faulthandler.enable(dump)
            self.assertTrue(faulthandler.is_enabled())
        finally:
            faulthandler.disable()
            if was_on:
                faulthandler.enable()

    def test_log_starts_over_when_it_grows(self):
        self.streams(None, None)
        say.out("first run")
        path = say.log_path()
        with open(path, "a") as f:
            f.write("x" * say.LOG_MAX)
        self.reset()
        say.out("second run")
        with open(path) as f:
            self.assertEqual(f.read(), "second run\n")

    def test_appends_across_runs_until_then(self):
        self.streams(None, None)
        say.out("first run")
        path = say.log_path()
        self.reset()
        say.out("second run")
        with open(path) as f:
            self.assertEqual(f.read(), "first run\nsecond run\n")

    def test_a_stream_that_fails_is_not_worth_a_crash(self):
        class Broken(io.StringIO):
            def write(self, _):
                raise OSError("pipe went away")

        self.streams(Broken(), Broken())
        say.out("swallowed")                                  # must not raise
        say.err("swallowed")

    def test_nowhere_to_log_is_not_an_error(self):
        """A data directory that cannot be created is a real state on a locked
        down machine, and losing a debug line to it is the correct outcome."""
        self.streams(None, None)
        # A file where the directory should be: makedirs cannot win from here.
        blocked = os.path.join(self.tmp.name, "blocked")
        with open(blocked, "w") as f:
            f.write("not a directory")
        self.env(XDG_DATA_HOME=blocked)
        say.out("nowhere")
        self.assertIsNone(say.log_path())
        self.assertFalse(say.visible())


def setattr_both(out, err) -> None:
    sys.stdout, sys.stderr = out, err


if __name__ == "__main__":
    unittest.main()
