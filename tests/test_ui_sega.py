# SPDX-License-Identifier: GPL-3.0-or-later
"""Sega local sources and cheat selection through real Tk widgets."""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cheatgui"), str(ROOT / "cheats")]

import tkinter as tk

import card
import cheatlib
import db
import model
import prefs
import ui
from test_gg import LIB


class SegaPane(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as e:
            raise unittest.SkipTest(f"no display: {e}")
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        for target, attr, value in (
                (prefs, "CONFIG", str(self.folder / "prefs.json")),
                (cheatlib, "LOCAL", str(self.folder / "local")),
                (db, "db_dir", lambda: str(self.folder / "missing-db"))):
            patcher = patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        cheatlib.refresh()
        self.addCleanup(cheatlib.refresh)
        # Card scanning and network checks are separate from the pane under
        # test. No real card, user preferences or network is accessed here.
        with patch.object(ui.App, "rescan"), patch.object(ui.App, "check_db"), \
                patch.object(ui.App, "check_core"):
            self.app = ui.App(self.root)
        self.addCleanup(self.app.destroy)
        errors = patch.object(ui.messagebox, "showerror")
        self.error = errors.start()
        self.addCleanup(errors.stop)

    def settle(self):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            self.root.update()
            if not self.app.worker.busy and self.app.worker.pending is None:
                return
            time.sleep(0.01)
        self.fail("cheat write did not finish")

    def open_game(self, pid="sg1000", ext="sg"):
        game = card.Game(str(self.folder / f"Game.{ext}"), pid)
        Path(game.path).write_bytes(b"ROM")
        self.app._loaded(model.load(game), None)
        return game

    def test_sg1000_can_choose_send_and_reload_a_local_file_without_database(self):
        game = self.open_game()
        self.assertEqual(self.app.source_label.cget("text"),
                         "no matching cheat file found")
        self.assertNotIn("disabled", self.app.source_btn.state())
        source = self.folder / "chosen.cht"
        source.write_bytes(LIB)
        chooser = ui.Chooser(self.app, self.app.view)
        with patch.object(ui.filedialog, "askopenfilename", return_value=str(source)):
            chooser.browse_btn.invoke()
        self.assertEqual(prefs.get_source(game.path), str(source))
        self.assertEqual(len(self.app.cheats.get_children()), 7)
        self.assertEqual(self.app.cheats.set("0", "how"), "written")
        self.assertEqual(self.app.cheats.set("1", "how"), "patched")
        self.assertIn("dead", self.app.cheats.item("3", "tags"))
        self.app.set_all(True)
        self.assertEqual(self.app.meter.label.cget("text"), "5 / 32 codes")
        self.assertFalse(self.app.view.entries[3].enabled)
        self.app.save_btn.invoke()
        self.settle()
        self.assertTrue(Path(game.cht_path).exists())
        self.assertFalse(Path(game.path + ".chtbin").exists())
        self.app._loaded(model.load(game), None)
        self.assertEqual(len(self.app.view.enabled), 4)
        self.assertEqual(self.app.view.applied_counts, (1, 4))
        self.error.assert_not_called()

    def test_sms_shows_its_database_cheats_and_the_correct_capacity(self):
        database = self.folder / "missing-db" / "Sega - Master System - Mark III"
        database.mkdir(parents=True)
        (database / "Game.cht").write_bytes(LIB)
        self.open_game("sms", "sms")
        self.assertEqual(self.app.view.platform, "sms")
        self.assertEqual(len(self.app.cheats.get_children()), 7)
        self.assertEqual(self.app.meter.limit, 32)
        self.assertNotIn("carried as written", self.app.status.cget("text"))
        self.assertEqual(self.app.cheats.set("0", "how"), "written")
        self.error.assert_not_called()

    def test_invalid_or_cancelled_local_file_does_not_change_the_source(self):
        game = self.open_game()
        invalid = self.folder / "empty.cht"
        invalid.write_text("not a cheat file")
        chooser = ui.Chooser(self.app, self.app.view)
        self.addCleanup(chooser.destroy)
        with patch.object(ui.filedialog, "askopenfilename", return_value=str(invalid)):
            chooser.browse_btn.invoke()
        self.error.assert_called_once()
        self.assertIsNone(prefs.get_source(game.path))
        with patch.object(ui.filedialog, "askopenfilename", return_value=""):
            chooser.browse_btn.invoke()
        self.assertIsNone(self.app.view.source)

    def test_text_browse_does_not_accept_an_archive_for_another_system(self):
        game = self.open_game()
        chooser = ui.Chooser(self.app, self.app.view)
        self.addCleanup(chooser.destroy)
        with patch.object(ui.filedialog, "askopenfilename", return_value="GBA.zip"), \
                patch.object(ui.writer, "load_library") as load:
            chooser.browse_btn.invoke()
        load.assert_not_called()
        self.error.assert_called_once()
        self.assertIn(".cht text file", self.error.call_args.args[1])
        self.assertIsNone(prefs.get_source(game.path))


if __name__ == "__main__":
    unittest.main()
