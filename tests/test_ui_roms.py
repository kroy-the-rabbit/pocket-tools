# SPDX-License-Identifier: GPL-3.0-or-later
"""The boot ROM dialog, driven through the real widgets.

It replaced a message box that laid out a table with spaces in a proportional
font and let Tk decide where the lines broke, so what is worth testing is that
there is now a row per boot ROM rather than a paragraph, that the two ways one
can be wrong are told apart, and that the path can be taken away: it is the
only text in here anybody has to act on, and retyping it was the old answer.

Needs a display. Run under xvfb-run where there is none.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "cheatgui"))
sys.path.insert(1, os.path.join(ROOT, "cheats"))

import tkinter as tk                                         # noqa: E402

import core as core_mod                                      # noqa: E402
import ui                                                    # noqa: E402

BY_ID = {c.id: c for c in core_mod.CORES}
GBC = BY_ID["kroy.GBC"]
GB = BY_ID["kroy.GB"]
GBA = BY_ID["kroy.GBA"]

CARD = os.path.join(os.sep, "card")


def missing(c, rom) -> core_mod.RomState:
    return core_mod.RomState(c, rom, None, 0)


def present(c, rom, size=None) -> core_mod.RomState:
    path = os.path.join(CARD, "Assets", c.platform, "common", rom.filename)
    return core_mod.RomState(c, rom, path, rom.size if size is None else size)


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
    """wait_window blocks, so the dialog is built without the modal tail.

    The same arrangement as the cores dialog's tests, and for the same reason:
    __init__ ends with grab_set() and wait_window(), and a test that called it
    would sit there forever with nobody to click.
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

    def build(self, roms):
        sv = core_mod.Survey(CARD, {c.id: None for c in core_mod.CORES}, roms)
        dlg = ui.RomsDialog(self.root, sv)
        self.addCleanup(dlg.destroy)
        return dlg

    def four(self):
        """One missing, one the wrong size, and two that are fine."""
        return [missing(GBC, GBC.bios[0]),
                present(GB, GB.bios[0], size=64),
                present(GB, GB.bios[1]),
                present(GBA, GBA.bios[0])]

    def tag(self, dlg, iid) -> str:
        return dlg.tree.item(iid, "tags")[0]

    # ------------------------------------------------------------ the table --
    def test_a_row_per_boot_rom_and_not_a_paragraph(self):
        dlg = self.build(self.four())
        rows = dlg.tree.get_children()
        self.assertEqual(len(rows), 4)
        self.assertEqual([dlg.tree.item(i, "text").strip() for i in rows],
                         ["gbc_bios.bin", "gb_bios.bin", "sgb_boot.bin",
                          "gba_bios.bin"])

    def test_a_card_that_needs_nothing_still_opens(self):
        # The PC Engine needs no boot ROM. An empty table is a real answer and
        # a Treeview of height zero is a Tk error.
        dlg = self.build([])
        self.assertEqual(dlg.tree.get_children(), ())
        self.assertIn("nothing to put anywhere", dlg.prose.cget("text"))

    def test_the_two_ways_of_being_wrong_get_different_tags(self):
        dlg = self.build(self.four())
        gbc, gb = dlg.tree.get_children()[0], dlg.tree.get_children()[1]
        self.assertEqual(self.tag(dlg, gbc), "missing")
        self.assertEqual(self.tag(dlg, gb), "wrong")
        self.assertNotEqual(dlg.tree.tag_configure("missing", "foreground"),
                            dlg.tree.tag_configure("wrong", "foreground"))

    def test_a_file_that_is_there_and_the_right_size_is_not_a_fault(self):
        dlg = self.build(self.four())
        for iid in dlg.tree.get_children()[2:]:
            self.assertEqual(self.tag(dlg, iid), "ok")
            self.assertEqual(dlg.tree.set(iid, "state"), "present")

    def test_the_wrong_size_says_both_numbers(self):
        # "wrong size" alone leaves you comparing a file against nothing.
        dlg = self.build(self.four())
        gb = dlg.tree.get_children()[1]
        self.assertEqual(dlg.tree.set(gb, "state"), "wrong size: 64 bytes")
        self.assertEqual(dlg.tree.set(gb, "size"), "256 bytes")

    def test_a_missing_file_names_the_directory_it_goes_in(self):
        dlg = self.build(self.four())
        gbc = dlg.tree.get_children()[0]
        self.assertEqual(dlg.tree.set(gbc, "state"), "missing")
        self.assertEqual(dlg.tree.set(gbc, "where"),
                         os.path.join("Assets", "gbc", "common"))

    def test_a_rom_of_no_stated_size_asks_for_no_size(self):
        rom = core_mod.Rom("odd.bin", 0, "Something")
        dlg = self.build([missing(GBC, rom)])
        self.assertEqual(dlg.tree.set(dlg.tree.get_children()[0], "size"),
                         "any size")

    # ---------------------------------------------------------- the buttons --
    def test_copy_path_puts_the_whole_path_on_the_clipboard(self):
        dlg = self.build(self.four())
        dlg.tree.selection_set(dlg.tree.get_children()[0])
        dlg.copy_path()
        self.assertEqual(dlg.clipboard_get(),
                         os.path.join(CARD, "Assets", "gbc", "common",
                                      "gbc_bios.bin"))

    def test_it_copies_where_a_file_was_found_not_where_it_should_be(self):
        # A boot ROM in the core's own directory counts as present, and the
        # path worth having is the one it is actually at.
        rom = GBC.bios[0]
        found = os.path.join(CARD, "Assets", "gbc", GBC.id, rom.filename)
        dlg = self.build([core_mod.RomState(GBC, rom, found, rom.size)])
        dlg.copy_path()
        self.assertEqual(dlg.clipboard_get(), found)
        self.assertEqual(dlg.tree.set(dlg.tree.get_children()[0], "where"),
                         os.path.join("Assets", "gbc", GBC.id))

    def test_it_selects_the_first_thing_wrong_not_the_first_row(self):
        # So that Copy path means something without a click, and means the row
        # the dialog opened for.
        dlg = self.build([present(GB, GB.bios[0]), missing(GBC, GBC.bios[0])])
        self.assertIn("gbc_bios.bin", dlg.selected())
        self.assertIn("gbc_bios.bin", dlg.note.cget("text"))

    def test_with_nothing_wrong_it_still_offers_a_path(self):
        dlg = self.build([present(GB, GB.bios[0])])
        self.assertIn("gb_bios.bin", dlg.selected())
        self.assertNotIn("disabled", dlg.copy_btn.state())

    def test_an_empty_table_has_no_path_to_copy(self):
        dlg = self.build([])
        self.assertIsNone(dlg.selected())
        self.assertIn("disabled", dlg.copy_btn.state())
        dlg.copy_path()                                # and does not raise

    # ------------------------------------------------------------ the prose --
    def test_the_prose_is_wrapped_here_and_not_by_tk(self):
        # wraplength hands the break point to the widget's width, which is how
        # the message box broke a path in half.
        dlg = self.build(self.four())
        text = dlg.prose.cget("text")
        self.assertIn("\n", text)
        self.assertEqual(int(dlg.prose.cget("wraplength") or 0), 0)
        self.assertTrue(all(len(ln) <= ui.RomsDialog.PROSE
                            for ln in text.splitlines()), text)

    def test_it_says_what_to_do_only_when_there_is_something_to_do(self):
        bad = self.build(self.four()).prose.cget("text")
        self.assertIn("will not start a game", bad)
        good = self.build([present(GB, GB.bios[0])]).prose.cget("text")
        self.assertNotIn("will not start a game", good)
        # And in both cases it says why the file is not simply shipped.
        for text in (bad, good):
            self.assertIn("copyrighted", text)

    def test_it_says_which_card(self):
        self.assertTrue(any(CARD in t for t in labels(self.build(self.four()))))


if __name__ == "__main__":
    unittest.main()
