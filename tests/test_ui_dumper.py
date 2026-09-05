# SPDX-License-Identifier: GPL-3.0-or-later
"""The dumper core: always installable, never assumed, and it owns its surface.

Two rules, and they pull in opposite directions on purpose.

The install is always available. kroy.CartTools is in the registry like any
other core, the installer lists it whether or not the card has it, and its
version is reported the same way. Hiding it would be circular: the dump
feature needs that core, and the one place anybody looks for a core is the
core installer.

The feature follows the card. The dump surface is on screen when the dumper is
installed and gone when it is not. There is no preference of its own, because
installing the core is the act of asking, and nothing here installs it unasked.

Needs a display. Run under xvfb-run where there is none.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "cheatgui"))
sys.path.insert(1, os.path.join(ROOT, "cheats"))
sys.path.insert(2, HERE)

import tkinter as tk                                         # noqa: E402
from tkinter import messagebox                               # noqa: E402

from test_ui_carts import build_card, build_db                # noqa: E402


def install_dumper(card: str, version: str = "0.3.0") -> None:
    """Put a kroy.CartTools core.json on the card, as an install would."""
    d = os.path.join(card, "Cores", "kroy.CartTools")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "core.json"), "w") as f:
        json.dump({"core": {"metadata": {"version": version},
                            "data": {}}}, f)


def remove_dumper(card: str) -> None:
    import shutil
    shutil.rmtree(os.path.join(card, "Cores", "kroy.CartTools"),
                  ignore_errors=True)


class Registry(unittest.TestCase):
    """The core list, with no window in the way."""

    def setUp(self) -> None:
        import core
        self.core = core
        self.rels = {core.CARTTOOLS_REPO: {
            "repo": core.CARTTOOLS_REPO, "tag": "v0.3.0", "version": "0.3.0",
            "page": "", "assets": {"kroy.CartTools_0.3.0.zip": "url"}}}

    def test_the_dumper_is_in_the_registry(self):
        self.assertIn("kroy.CartTools", [c.id for c in self.core.CORES])

    def test_it_is_the_only_optional_core(self):
        self.assertEqual([c.id for c in self.core.CORES if c.optional],
                         ["kroy.CartTools"])

    def test_its_repository_is_asked_for_releases(self):
        """Always: the installer offers it whether or not the card has it."""
        self.assertIn(self.core.CARTTOOLS_REPO, self.core.repos())

    def test_it_needs_no_boot_rom(self):
        """An empty tuple is an answer. This core runs no games."""
        c = next(c for c in self.core.CORES if c.id == "kroy.CartTools")
        self.assertEqual(c.bios, ())

    def test_absent_it_is_not_an_update(self):
        """Not installed is not out of date, so nothing ticks it for you."""
        out = self.core.outdated({"kroy.CartTools": None}, self.rels)
        self.assertEqual([c.id for c in out], [])

    def test_installed_and_behind_it_updates_like_any_other(self):
        out = self.core.outdated({"kroy.CartTools": "0.1.0"}, self.rels)
        self.assertEqual([c.id for c in out], ["kroy.CartTools"])

    def test_installed_and_current_it_is_not_offered(self):
        out = self.core.outdated({"kroy.CartTools": "0.3.0"}, self.rels)
        self.assertEqual([c.id for c in out], [])

    def test_dumper_installed_reads_the_survey(self):
        sv = self.core.Survey("/card", {"kroy.CartTools": "0.3.0"}, [])
        self.assertTrue(self.core.dumper_installed(sv))
        sv = self.core.Survey("/card", {"kroy.CartTools": None}, [])
        self.assertFalse(self.core.dumper_installed(sv))
        self.assertFalse(self.core.dumper_installed(None))


class Surface(unittest.TestCase):
    """The window, and what the card decides is on it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["XDG_CONFIG_HOME"] = os.path.join(cls.tmp.name, "config")
        os.environ["XDG_DATA_HOME"] = os.path.join(cls.tmp.name, "data")
        cls.card = build_card(os.path.join(cls.tmp.name, "card"), 0)
        os.environ["POCKET_CARD"] = cls.card
        os.environ["POCKET_CHEAT_DB"] = build_db(
            os.path.join(cls.tmp.name, "cht"), [])
        try:
            cls.root = tk.Tk()
        except tk.TclError as e:                             # no display
            raise unittest.SkipTest(f"no display: {e}")
        cls.root.withdraw()
        import prefs
        cls.was_config = prefs.CONFIG
        prefs.CONFIG = os.path.join(cls.tmp.name, "config", "pocket-cheats",
                                    "prefs.json")
        cls.tail = (tk.Toplevel.grab_set, tk.Toplevel.wait_window)
        tk.Toplevel.grab_set = lambda self: None
        tk.Toplevel.wait_window = lambda self, w=None: None

    @classmethod
    def tearDownClass(cls) -> None:
        import prefs
        prefs.CONFIG = cls.was_config
        tk.Toplevel.grab_set, tk.Toplevel.wait_window = cls.tail
        cls.root.destroy()
        cls.tmp.cleanup()

    def setUp(self) -> None:
        import core
        import db
        self.db, self.core = db, core
        self._remote = db.remote_state
        self._latest = core.all_latest
        db.remote_state = lambda timeout=None: {
            "sha": "0" * 40, "date": "2026-01-01T00:00:00Z"}
        core.all_latest = lambda timeout=None: {
            r: {"repo": r, "tag": "v0.0.0", "version": "0.0.0", "page": "",
                "assets": {}} for r in core.repos()}
        self._boxes = {n: getattr(messagebox, n) for n in
                       ("askyesno", "showerror", "showwarning", "showinfo")}
        for name in self._boxes:
            setattr(messagebox, name,
                    lambda *a, _n=name, **k: True if _n == "askyesno" else "ok")
        remove_dumper(self.card)
        import ui
        self.ui = ui
        self.app = None

    def tearDown(self) -> None:
        if self.app is not None:
            end = time.time() + 15
            while time.time() < end and (self.app.dbjob.busy()
                                         or self.app.corejob.busy()
                                         or self.app.worker.busy):
                self.root.update()
                time.sleep(0.01)
            self.app.destroy()
            self.root.update()
        for name, fn in self._boxes.items():
            setattr(messagebox, name, fn)
        self.db.remote_state = self._remote
        self.core.all_latest = self._latest
        remove_dumper(self.card)

    def start(self):
        self.app = self.ui.App(self.root)
        self.settle()
        return self.app

    def settle(self, limit: float = 15.0) -> None:
        end = time.time() + limit
        while time.time() < end and (self.app.worker.busy
                                     or self.app.worker.pending is not None
                                     or self.app.corejob.busy()
                                     or self.app.dbjob.busy()):
            self.root.update()
            time.sleep(0.01)
        end = time.time() + 0.3
        while time.time() < end:
            self.root.update()
            time.sleep(0.01)

    # ------------------------------------------------------------------------
    def test_no_dumper_on_the_card_means_no_dump_surface(self):
        app = self.start()
        self.assertFalse(app.dumps_on)
        self.assertEqual(app.dumps_btn.grid_info(), {})
        self.assertEqual(app.copy_btn.winfo_manager(), "")
        self.assertEqual(app.import_btn.grid_info(), {})
        self.assertEqual(app.restore_btn.grid_info(), {})
        self.assertFalse(app.systems.exists(self.ui.SHELF))
        self.assertTrue(app.systems.get_children(), "the card was not read")

    def test_no_dumper_means_the_library_is_never_read(self):
        """Not merely hidden: the disk work behind it does not happen."""
        import library
        calls = []
        real = library.load
        library.load = lambda root: calls.append(root) or real(root)
        self.addCleanup(lambda: setattr(library, "load", real))
        app = self.start()
        app.rescan()
        self.settle()
        self.assertEqual(calls, [])

    def test_the_dumper_on_the_card_brings_the_surface_with_it(self):
        install_dumper(self.card)
        app = self.start()
        self.assertTrue(app.dumps_on)
        self.assertNotEqual(app.dumps_btn.grid_info(), {})
        self.assertEqual(app.copy_btn.winfo_manager(), "pack")
        self.assertNotEqual(app.import_btn.grid_info(), {})
        self.assertNotEqual(app.restore_btn.grid_info(), {})
        self.assertTrue(app.systems.exists(self.ui.SHELF))
        self.assertEqual(app.systems.item(self.ui.SHELF, "text"),
                         "Cartridge dumps")

    def test_removing_the_dumper_takes_the_surface_away_again(self):
        install_dumper(self.card)
        app = self.start()
        self.assertTrue(app.dumps_on)
        remove_dumper(self.card)
        app.rescan()
        self.settle()
        self.assertFalse(app.dumps_on)
        self.assertEqual(app.dumps_btn.grid_info(), {})
        self.assertFalse(app.systems.exists(self.ui.SHELF))

    def test_there_is_no_setting_to_get_out_of_step_with_the_card(self):
        """The surface is a fact about the card, not a remembered choice."""
        import prefs
        self.assertFalse(hasattr(prefs, "get_cart_dumps"))
        self.assertFalse(hasattr(self.ui, "SettingsDialog"))
