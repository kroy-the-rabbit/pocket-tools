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


class Stub:
    """Stands in for RemoveDialog, which would block on a click."""
    removed = False


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
        self.ui.RemoveDialog = lambda parent, filing: (
            opened.append(filing), Stub())[1]
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
        self.ui.RemoveDialog = lambda parent, filing: Stub()
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

    def test_the_window_does_not_re_read_the_card_to_redraw(self):
        """Rehashing on every answer would cost the whole scan again.

        Measured at 28 seconds for a real card of 32 dumps over USB, so this
        is not a micro-optimisation: it is the difference between a window
        that answers and one that appears to have hung. The card files are
        deleted underneath the dialog and it still draws, which is only
        possible if it kept what it was handed.
        """
        p = self.put("ZELDA.gb", self.zelda)
        found = self.dumps.scan(self.card)
        os.remove(p)
        dlg = self.ui.DumpsDialog(self.root, self.card, self.cat, found)
        self.addCleanup(dlg.destroy)
        self.assertEqual(len(dlg.tree.get_children()), 1)
        dlg.refill()
        self.assertEqual(len(dlg.tree.get_children()), 1)

    def test_a_dump_taken_off_the_card_stops_being_listed(self):
        p = self.put("ZELDA.gb", self.zelda)
        dlg = self.build()
        dlg.tree.selection_set(p)

        class Removed(Stub):
            removed = True
        self.ui.RemoveDialog = lambda parent, filing: Removed()
        dlg.file_one()
        self.assertEqual(dlg.tree.get_children(), ())

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
        self.ui.RemoveDialog = lambda parent, filing: Stub()
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

    # ----------------------------------------------------------- the DATs --
    def dat_dir(self) -> str:
        """A stand-in downloads directory holding what a browser would leave."""
        d = os.path.join(self.tmp.name, "Downloads")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "Nintendo - Game Boy (20260827).dat"),
                  "w") as f:
            f.write(dat_xml(self.nointro.SYSTEMS["gb"], [
                {"name": "Tetris (World) (Rev 1)",
                 "rom": "Tetris (World) (Rev 1).gb", "data": self.tetris}]))
        # A DB Export is well-formed until it is not: two top-level elements.
        with open(os.path.join(d, "Nintendo - Game Boy (DB Export).xml"),
                  "w") as f:
            f.write("<?xml version='1.0'?>\n<header><name>x</name></header>\n"
                    "<datafile><game name='x'/></datafile>\n")
        with open(os.path.join(d, "holiday-photos.zip"), "wb") as f:
            f.write(b"not a dat")
        self.ui.download_dirs = lambda: [d]
        self.addCleanup(setattr, self.ui, "download_dirs",
                        self.ui.__dict__["download_dirs"])
        return d

    def dats(self):
        self.dat_dir()
        dlg = self.ui.DatDialog(self.root, self.nointro.Catalog())
        self.addCleanup(dlg.destroy)
        return dlg

    def test_the_downloads_are_found_without_anybody_browsing_for_them(self):
        dlg = self.dats()
        names = [os.path.basename(i) for i in dlg.tree.get_children()]
        self.assertIn("Nintendo - Game Boy (20260827).dat", names)
        self.assertIn("Nintendo - Game Boy (DB Export).xml", names)

    def test_a_zip_that_is_not_a_dat_is_never_opened_to_find_out(self):
        """Matched on the name, because a downloads folder holds big archives."""
        dlg = self.dats()
        names = [os.path.basename(i) for i in dlg.tree.get_children()]
        self.assertNotIn("holiday-photos.zip", names)

    def test_a_readable_dat_says_its_flavour_and_how_many_it_holds(self):
        dlg = self.dats()
        iid = next(i for i in dlg.tree.get_children() if "DB Export" not in i)
        self.assertEqual(dlg.tree.set(iid, "kind"), "DAT")
        self.assertEqual(dlg.tree.set(iid, "system"), "Game Boy")
        self.assertEqual(dlg.tree.set(iid, "entries"), "1")

    def test_a_db_export_is_shown_and_cannot_be_ticked(self):
        dlg = self.dats()
        iid = next(i for i in dlg.tree.get_children() if "DB Export" in i)
        self.assertEqual(dlg.tree.set(iid, "kind"), "DB Export")
        self.assertEqual(dlg.tree.item(iid, "tags")[0], "bad")
        self.assertNotIn(iid, dlg.ticked())

    def test_ticking_a_dat_and_loading_it_puts_it_in_the_catalog(self):
        dlg = self.dats()
        iid = next(i for i in dlg.tree.get_children() if "DB Export" not in i)
        dlg.tree.item(iid, text=self.ui.TICK + dlg.tree.item(iid, "text")[1:])
        self.assertEqual(dlg.ticked(), [iid])
        dlg.load()
        self.assertEqual(dlg.catalog.loaded(), ("gb",))
        self.assertTrue(dlg.loaded)

    def test_the_maker_is_not_repeated_on_every_row(self):
        self.assertEqual(self.ui.DatDialog.plainly("gba"), "Game Boy Advance")
        self.assertEqual(self.ui.DatDialog.plainly(""), "")

    def test_get_dats_opens_the_page_it_names(self):
        opened = []
        real = self.ui.reveal.website
        self.ui.reveal.website = lambda url: (opened.append(url), True)[1]
        self.addCleanup(setattr, self.ui.reveal, "website", real)
        dlg = self.dats()
        dlg.get()
        self.assertEqual(opened, [self.ui.DATOMATIC])
        self.assertTrue(self.ui.DATOMATIC.startswith("https://"))

    def test_a_url_that_is_not_a_web_page_is_refused(self):
        self.assertFalse(self.ui.reveal.website("file:///etc/passwd"))

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
