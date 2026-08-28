# SPDX-License-Identifier: GPL-3.0-or-later
"""The cartridge dumps window, driven through the real widgets.

The engine underneath is already covered by test_dumps.py, so what is worth
testing here is the part a user can get wrong: that every verdict reaches the
screen, that only the ones worth acting on offer to write anything, that the
three collision answers are three different outcomes rather than three labels
on one, and that the card is emptied only after a verified copy and only when
somebody says so.

Needs a display. Run under xvfb-run where there is none.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "cheatgui"))
sys.path.insert(1, os.path.join(ROOT, "cheats"))
sys.path.insert(2, HERE)

import tkinter as tk                                         # noqa: E402

from test_dumps import dat_xml, gb_rom                        # noqa: E402


def labels(widget) -> list[str]:
    """Every piece of text the dialog actually put on screen."""
    out = []
    for w in widget.winfo_children():
        try:
            out.append(str(w.cget("text")))
        except tk.TclError:                                  # no -text option
            pass
        out.extend(labels(w))
    return out


class Dialog(unittest.TestCase):
    """A temporary card, library and config, and the modal tail stubbed out.

    The same arrangement the cores and boot ROM dialogs use: __init__ ends in
    grab_set() and wait_window(), and a test that called those would sit there
    forever with nobody to click.
    """

    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.root = tk.Tk()
        except tk.TclError as e:                             # no display
            raise unittest.SkipTest(f"no display: {e}")
        cls.root.withdraw()
        cls.real_tail = (tk.Toplevel.grab_set, tk.Toplevel.wait_window)
        tk.Toplevel.grab_set = lambda self: None
        tk.Toplevel.wait_window = lambda self, w=None: None

    @classmethod
    def tearDownClass(cls) -> None:
        tk.Toplevel.grab_set, tk.Toplevel.wait_window = cls.real_tail
        cls.root.destroy()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        saved = {k: os.environ.get(k) for k in
                 ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "POCKET_CHEAT_DB")}

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp.name, "config")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp.name, "data")
        os.environ["POCKET_CHEAT_DB"] = os.path.join(self.tmp.name, "cht")

        import importlib
        import prefs
        importlib.reload(prefs)
        import dumps
        import library
        import nointro
        import ui
        # prefs is reloaded because it reads CONFIG at import time; dumps is
        # deliberately not, because ui.py holds a reference to this very module
        # object and a reload would give it a second set of Verdict members
        # that compare equal to nothing in the dialog's table.
        self.dumps, self.library, self.nointro, self.ui = (
            dumps, library, nointro, ui)

        self.card = os.path.join(self.tmp.name, "card")
        self.lib = os.path.join(self.tmp.name, "library")
        os.makedirs(dumps.dump_dir(self.card))
        library.create(self.lib)
        library.set_path(self.lib)

        self.zelda = gb_rom(b"ZELDA", filler=b"\x5a")
        self.tetris = gb_rom(b"TETRIS", filler=b"\x54")
        self.mystery = gb_rom(b"MYSTERY", filler=b"\x4d")
        self.cat = self.catalog()

    # ------------------------------------------------------------- fixtures --
    def put(self, name: str, data: bytes) -> str:
        path = os.path.join(self.dumps.dump_dir(self.card), name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def catalog(self):
        path = os.path.join(self.tmp.name, "gb.dat")
        with open(path, "w") as f:
            f.write(dat_xml(self.nointro.SYSTEMS["gb"], [
                {"name": "Legend of Zelda, The - Link's Awakening (USA, Europe)",
                 "rom": "Legend of Zelda, The - Link's Awakening "
                        "(USA, Europe).gb",
                 "data": self.zelda},
                {"name": "Tetris (World) (Rev 1)",
                 "rom": "Tetris (World) (Rev 1).gb", "data": self.tetris}]))
        cat = self.nointro.Catalog()
        self.assertEqual(cat.add(path), "gb")
        return cat

    def build(self):
        dlg = self.ui.DumpsDialog(self.root, self.card, self.cat)
        self.addCleanup(dlg.destroy)
        return dlg

    def state(self, dlg, path: str) -> str:
        return dlg.tree.set(path, "state")

    # ------------------------------------------------------------- verdicts --
    def test_a_dump_the_dat_knows_is_offered_under_its_real_name(self):
        p = self.put("ZELDA.gb", self.zelda)
        dlg = self.build()
        self.assertEqual(self.state(dlg, p), "ready to file")
        self.assertEqual(
            dlg.tree.set(p, "name"),
            "Legend of Zelda, The - Link's Awakening (USA, Europe).gb")

    def test_a_dump_no_dat_has_is_said_rather_than_guessed(self):
        p = self.put("UNKNOWN.gb", self.mystery)
        dlg = self.build()
        self.assertEqual(self.state(dlg, p), "not in any DAT")
        self.assertEqual(dlg.tree.item(p, "tags")[0], "lesser")
        self.assertIn("can tell those apart", dlg.detail.cget("text"))

    def test_an_unidentified_dump_offers_nothing_automatic(self):
        self.put("UNKNOWN.gb", self.mystery)
        dlg = self.build()
        self.assertIn("disabled", dlg.file_btn.state())

    def test_the_file_the_core_leaves_behind_is_not_a_dump(self):
        self.put("ZELDA.gb", self.zelda)
        self.put(".gitkeep", b"")
        dlg = self.build()
        self.assertEqual(len(dlg.tree.get_children()), 1)

    def test_the_window_opens_on_the_question_it_was_opened_to_ask(self):
        self.put("AAA_UNKNOWN.gb", self.mystery)   # sorts first, needs nothing
        p = self.put("ZELDA.gb", self.zelda)
        dlg = self.build()
        self.assertEqual(dlg.tree.selection()[0], p)

    # -------------------------------------------------------------- filing --
    def test_filing_writes_both_names_and_leaves_the_card_alone(self):
        p = self.put("ZELDA.gb", self.zelda)
        dlg = self.build()
        dlg.tree.selection_set(p)
        opened = []
        self.ui.RemoveDialog = lambda parent, filing: opened.append(filing)
        dlg.file_one()
        self.assertEqual(
            os.listdir(self.library.roms_dir(self.lib)),
            ["Legend of Zelda, The - Link's Awakening (USA, Europe).gb"])
        self.assertEqual(os.listdir(self.library.dumps_dir(self.lib)),
                         ["ZELDA.gb"])
        # commit() never touches the card. Only RemoveDialog does, and only
        # once somebody has answered it.
        self.assertTrue(os.path.exists(p))
        self.assertTrue(opened and opened[0].verified)

    def test_a_filed_dump_is_not_asked_about_twice(self):
        p = self.put("ZELDA.gb", self.zelda)
        dlg = self.build()
        dlg.tree.selection_set(p)
        self.ui.RemoveDialog = lambda parent, filing: None
        dlg.file_one()
        self.assertEqual(self.state(dlg, p), "already filed")
        self.assertIn("disabled", dlg.file_btn.state())

    def test_turning_a_dump_down_is_remembered(self):
        p = self.put("ZELDA.gb", self.zelda)
        dlg = self.build()
        dlg.tree.selection_set(p)
        dlg.turn_down()
        self.assertEqual(self.state(dlg, p), "turned down")
        self.assertTrue(self.dumps.rejected(
            self.dumps.read(p).sha1))

    # ----------------------------------------------------------- collisions --
    def collide(self):
        """A different cartridge already filed under the name this one wants."""
        taken = os.path.join(
            self.library.roms_dir(self.lib),
            "Legend of Zelda, The - Link's Awakening (USA, Europe).gb")
        with open(taken, "wb") as f:
            f.write(b"\x00" * len(self.zelda))
        p = self.put("ZELDA.gb", self.zelda)
        dlg = self.build()
        dlg.tree.selection_set(p)
        return dlg, p, taken

    def answer(self, dlg, choice):
        self.ui.CollisionDialog = type(
            "Stub", (), {"__init__": lambda s, parent, prop: None,
                         "result": choice})
        self.ui.RemoveDialog = lambda parent, filing: None
        dlg.file_one()

    def test_a_taken_name_is_a_question_and_not_a_silent_overwrite(self):
        dlg, p, _ = self.collide()
        self.assertEqual(self.state(dlg, p), "name taken")

    def test_keep_both_files_it_under_a_hash_and_keeps_the_old(self):
        dlg, p, taken = self.collide()
        self.answer(dlg, self.dumps.Choice.KEEP_BOTH)
        roms = sorted(os.listdir(self.library.roms_dir(self.lib)))
        self.assertEqual(len(roms), 2)
        self.assertTrue(os.path.exists(taken))
        # A hash prefix, never a counter: a counter records the order files
        # arrived, which is not a fact about either of them.
        self.assertNotIn("_2", "".join(roms))

    def test_replace_writes_the_new_one_and_drops_the_old(self):
        dlg, p, taken = self.collide()
        self.answer(dlg, self.dumps.Choice.REPLACE)
        self.assertEqual(len(os.listdir(self.library.roms_dir(self.lib))), 1)
        with open(taken, "rb") as f:
            self.assertEqual(f.read(), self.zelda)

    def test_discard_leaves_the_library_and_the_card_alone(self):
        dlg, p, taken = self.collide()
        self.answer(dlg, self.dumps.Choice.DISCARD)
        self.assertEqual(len(os.listdir(self.library.roms_dir(self.lib))), 1)
        with open(taken, "rb") as f:
            self.assertNotEqual(f.read(), self.zelda)
        self.assertTrue(os.path.exists(p))

    def test_discarding_is_not_rejecting_so_it_comes_back(self):
        dlg, p, _ = self.collide()
        self.answer(dlg, self.dumps.Choice.DISCARD)
        self.assertEqual(self.state(dlg, p), "name taken")
        self.assertFalse(self.dumps.rejected(self.dumps.read(p).sha1))

    # -------------------------------------------------------- the DAT note --
    def test_a_db_export_says_which_download_to_fetch_instead(self):
        dat = self.nointro.Dat(system="", name="", version="", flavour="",
                               entries={},
                               problem=self.nointro.Problem.DB_EXPORT)
        why = self.ui.DumpsDialog.why(dat, "/x/Nintendo - Game Boy (DB Export).zip")
        self.assertIn("DB Export", why)
        self.assertIn("Parent-Clone", why)

    def test_a_broken_file_is_not_blamed_on_the_wrong_download(self):
        dat = self.nointro.Dat(system="", name="", version="", flavour="",
                               entries={},
                               problem=self.nointro.Problem.DAMAGED)
        why = self.ui.DumpsDialog.why(dat, "/x/torn.zip")
        self.assertNotIn("DB Export", why)
        self.assertIn("again", why)

    def test_the_window_says_which_dats_it_searched(self):
        self.put("ZELDA.gb", self.zelda)
        dlg = self.build()
        note = dlg.dat_label.cget("text")
        self.assertIn("Game Boy", note)

    # ---------------------------------------------------------------- Open --
    def test_no_open_button_where_there_is_no_file_manager(self):
        real = self.ui.reveal.available
        self.ui.reveal.available = lambda: False
        self.addCleanup(setattr, self.ui.reveal, "available", real)
        self.assertIsNone(self.ui.open_button_for(self.root, lambda: self.lib))

    def test_no_open_button_for_a_directory_that_is_not_there(self):
        gone = os.path.join(self.tmp.name, "nowhere")
        self.assertIsNone(self.ui.open_button(self.root, gone))
        # and looking is never what creates it
        self.assertFalse(os.path.exists(gone))

    def test_the_directory_offered_is_the_one_holding_the_file(self):
        p = self.put("ZELDA.gb", self.zelda)
        self.assertEqual(self.ui.holding(p),
                         self.dumps.dump_dir(self.card))


if __name__ == "__main__":
    unittest.main()
