# SPDX-License-Identifier: GPL-3.0-or-later
"""Opening a directory, and the four ways that goes wrong.

Every test here patches the launch, because a passing suite that opens eleven
file manager windows is not a passing suite. What is actually being checked is
the decisions taken before and after the launch: whether a directory is there,
which command gets picked, and what an exit code is allowed to mean.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "cheatgui"))
sys.path.insert(1, os.path.join(ROOT, "cheats"))

import reveal                                                 # noqa: E402


class Fake:
    """A launched command, as Popen would hand it back."""

    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode


class Reveal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        # say writes through sys.stderr, and the failure paths here are all
        # meant to log. Keep the suite's own output clean and readable back.
        self.log = io.StringIO()
        old = sys.stderr
        sys.stderr = self.log
        self.addCleanup(lambda: setattr(sys, "stderr", old))

        reveal._launched.clear()
        self.addCleanup(reveal._launched.clear)

        self.calls = []
        self.patch_popen(Fake())
        self.patch_which(lambda cmd: "/usr/bin/" + cmd)

    def patch_popen(self, result):
        def popen(cmd, **kw):
            self.calls.append((cmd, kw))
            return result() if callable(result) else result

        p = mock.patch.object(subprocess, "Popen", popen)
        p.start()
        self.addCleanup(p.stop)

    def patch_which(self, fn):
        p = mock.patch.object(reveal, "_which", fn)
        p.start()
        self.addCleanup(p.stop)

    # -- a path that may not be there -----------------------------------

    def test_a_directory_that_is_not_there_is_refused(self):
        missing = os.path.join(self.tmp.name, "not yet")
        self.assertFalse(reveal.directory(missing))
        self.assertFalse(reveal.openable(missing))
        self.assertEqual(self.calls, [])

    def test_being_asked_to_look_does_not_create_anything(self):
        """The library before it is chosen, the log before anything is written:
        the answer is no button, not a new empty directory."""
        missing = os.path.join(self.tmp.name, "library")
        reveal.directory(missing)
        reveal.openable(missing)
        reveal.containing(os.path.join(missing, "a.cht"))
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_a_file_is_not_a_directory(self):
        f = os.path.join(self.tmp.name, "prefs.json")
        open(f, "w").close()
        self.assertFalse(reveal.directory(f))
        self.assertFalse(reveal.openable(f))

    def test_no_path_at_all_is_just_no(self):
        self.assertFalse(reveal.openable(None))
        self.assertFalse(reveal.openable(""))
        self.assertFalse(reveal.directory(""))
        self.assertFalse(reveal.containing(""))

    def test_a_directory_that_is_there_opens(self):
        self.assertTrue(reveal.directory(self.tmp.name))
        self.assertTrue(reveal.openable(self.tmp.name))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0][1], self.tmp.name)

    # -- the containing directory of a file ------------------------------

    def test_a_file_opens_the_directory_holding_it(self):
        f = os.path.join(self.tmp.name, "Pokemon Red.cht")
        open(f, "w").close()
        self.assertTrue(reveal.containing(f))
        self.assertEqual(self.calls[0][0][1], self.tmp.name)

    def test_a_file_that_is_not_written_yet_still_has_a_home(self):
        """Somewhere to put it is what the user is being shown."""
        self.assertTrue(reveal.containing(os.path.join(self.tmp.name, "new")))
        self.assertEqual(self.calls[0][0][1], self.tmp.name)

    def test_a_bare_name_resolves_against_the_working_directory(self):
        self.assertTrue(reveal.containing("prefs.json"))
        self.assertEqual(self.calls[0][0][1], os.getcwd())

    # -- which command, and what its exit code means ---------------------

    def test_each_platform_gets_its_own_command(self):
        for platform, want in (("linux", "xdg-open"), ("darwin", "open"),
                               ("win32", "explorer")):
            with mock.patch.object(sys, "platform", platform):
                self.assertEqual(reveal._command(), want)

    def test_a_non_zero_exit_from_explorer_is_not_a_failure(self):
        """explorer.exe returns non-zero having done exactly what was asked.
        Reading that as failure is how a working button reports an error."""
        self.patch_popen(Fake(returncode=1))
        with mock.patch.object(reveal, "_command", lambda: "explorer"):
            self.assertTrue(reveal.directory(self.tmp.name))
            self.assertTrue(reveal.containing(
                os.path.join(self.tmp.name, "x.cht")))
        self.assertEqual(self.log.getvalue(), "")

    def test_windows_gets_a_normalised_path_and_a_detached_process(self):
        """explorer will not take a path with forward slashes in it, and the
        paths it is handed are joined onto a card root the user typed."""
        with mock.patch.object(os, "name", "nt"), \
             mock.patch.object(sys, "platform", "win32"), \
             mock.patch.object(subprocess, "DETACHED_PROCESS", 0x8, create=True):
            self.assertTrue(reveal.directory(self.tmp.name + "/"))
        cmd, kw = self.calls[0]
        self.assertEqual(cmd[0], "explorer")
        self.assertEqual(cmd[1], os.path.normpath(self.tmp.name))
        self.assertEqual(kw["creationflags"], 0x8)

    def test_the_launch_is_detached_from_us_elsewhere(self):
        """Started from a terminal, the file manager must not die with it, and
        must not take a Ctrl-C that was meant for the app."""
        self.assertTrue(reveal.directory(self.tmp.name))
        self.assertTrue(self.calls[0][1]["start_new_session"])

    # -- a machine with no file manager ----------------------------------

    def test_a_sandbox_with_no_command_offers_nothing(self):
        """Under Flatpak or Snap there may be nothing to call. The UI asks
        first so the button quietly does not appear."""
        self.patch_which(lambda cmd: None)
        self.assertFalse(reveal.available())
        self.assertFalse(reveal.openable(self.tmp.name))
        self.assertFalse(reveal.directory(self.tmp.name))
        self.assertEqual(self.calls, [])
        self.assertIn("no ", self.log.getvalue())

    def test_a_command_that_will_not_start_is_not_a_crash(self):
        with mock.patch.object(subprocess, "Popen",
                               mock.Mock(side_effect=OSError("no exec"))):
            self.assertFalse(reveal.directory(self.tmp.name))
        self.assertIn("would not start", self.log.getvalue())

    def test_available_says_yes_when_there_is_a_command(self):
        self.assertTrue(reveal.available())

    # -- the handles we keep ---------------------------------------------

    def test_finished_launches_are_not_left_lying_around(self):
        """Nothing waits on these, so they are cleared on the next press
        instead. Dropping them would leave a zombie per button."""
        self.patch_popen(lambda: Fake(returncode=0))
        reveal.directory(self.tmp.name)
        self.assertEqual(len(reveal._launched), 1)
        reveal.directory(self.tmp.name)
        self.assertEqual(len(reveal._launched), 1)

    def test_a_file_manager_still_open_is_kept(self):
        self.patch_popen(lambda: Fake(returncode=None))
        reveal.directory(self.tmp.name)
        reveal.directory(self.tmp.name)
        self.assertEqual(len(reveal._launched), 2)


if __name__ == "__main__":
    unittest.main()
