# SPDX-License-Identifier: GPL-3.0-or-later
"""The cores dialog, driven through the real widgets.

It replaced a button that decided for you and a yes/no box that confirmed the
decision, so the thing worth testing is that it hands back what was ticked
rather than what the app would have chosen. The rest is about the states a row
can be in: there are four cores from three repositories now, released at
different times, and one with no release at all.

Needs a display. Run under xvfb-run where there is none.
"""
from __future__ import annotations

import os
import sys
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "cheatgui"))
sys.path.insert(1, os.path.join(ROOT, "cheats"))

import tkinter as tk                                         # noqa: E402

import core as core_mod                                      # noqa: E402
import ui                                                    # noqa: E402


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


def releases(version: str, repos) -> dict:
    """A release map carrying `version` for the named repositories only."""
    out = {}
    for repo in repos:
        out[repo] = {
            "repo": repo, "tag": "v" + version, "version": version, "page": "",
            "assets": {f"{c.asset}{version}.zip": "zip:" + c.id
                       for c in core_mod.CORES if c.repo == repo},
        }
    return out


class Dialog(unittest.TestCase):
    """wait_window blocks, so the dialog is built without the modal tail.

    __init__ ends with grab_set() and wait_window(); a test that called it
    would sit there forever with nobody to click. The widgets and every
    decision about them are built before that point, so the tests reach in and
    drive the object rather than the window.
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

    def build(self, versions, rels):
        sv = core_mod.Survey("/card", versions, [])
        dlg = ui.CoresDialog(self.root, sv, rels)
        self.addCleanup(dlg.destroy)
        return dlg

    def rows(self, dlg):
        return {cid: (on, asset) for cid, (c, on, asset) in dlg.rows.items()}

    def test_every_core_gets_a_row(self):
        dlg = self.build({c.id: None for c in core_mod.CORES}, None)
        self.assertEqual(list(dlg.rows), [c.id for c in core_mod.CORES])
        self.assertEqual(list(dlg.tree.get_children()),
                         [c.id for c in core_mod.CORES])

    def test_clicking_a_row_toggles_it_and_the_tick_follows(self):
        rels = releases("2.0", [core_mod.GBC_REPO])
        dlg = self.build({c.id: None for c in core_mod.CORES}, rels)
        was = dlg.rows["kroy.GBC"][1]
        dlg.toggle_row("kroy.GBC")
        self.assertNotEqual(dlg.rows["kroy.GBC"][1], was)
        self.assertIn(ui.UNTICK if was else ui.TICK,
                      dlg.tree.item("kroy.GBC", "text"))
        dlg.toggle_row("kroy.GBC")
        self.assertEqual(dlg.rows["kroy.GBC"][1], was)

    def test_clicking_a_row_with_nothing_to_install_says_why(self):
        # Silence would read as a broken checkbox. It has a reason; it says it.
        rels = releases("2.0", [core_mod.GBC_REPO])
        dlg = self.build({c.id: None for c in core_mod.CORES}, rels)
        dlg.toggle_row("kroy.GBA")
        self.assertFalse(dlg.rows["kroy.GBA"][1])
        self.assertIn("nothing to install", dlg.note.cget("text"))

    def test_a_core_behind_its_release_is_ticked(self):
        rels = releases("2.0", [core_mod.GBC_REPO])
        versions = {c.id: None for c in core_mod.CORES}
        rows = self.rows(self.build(versions, rels))
        self.assertTrue(rows["kroy.GBC"][0])
        self.assertTrue(rows["kroy.GB"][0])

    def test_a_core_already_current_is_offered_but_not_ticked(self):
        # Reinstall is a repair, not a routine: available, never assumed.
        rels = releases("2.0", [core_mod.GBC_REPO])
        versions = {c.id: None for c in core_mod.CORES}
        versions["kroy.GBC"] = "2.0"
        rows = self.rows(self.build(versions, rels))
        self.assertFalse(rows["kroy.GBC"][0])
        self.assertIsNotNone(rows["kroy.GBC"][1])

    def test_a_core_with_no_release_cannot_be_ticked(self):
        # Game Boy Advance has a repository and no tag on it yet; the PC
        # Engine has no repository. Neither is installable and both are shown.
        rels = releases("2.0", [core_mod.GBC_REPO])
        rows = self.rows(self.build({c.id: None for c in core_mod.CORES}, rels))
        for cid in ("kroy.GBA", "kroy.PCE"):
            self.assertIsNone(rows[cid][1], cid)
            self.assertFalse(rows[cid][0], cid)

    def test_offline_ticks_nothing_and_installs_nothing(self):
        dlg = self.build({c.id: "1.0" for c in core_mod.CORES}, None)
        self.assertTrue(all(not v for v, _a in self.rows(dlg).values()))
        dlg.ok()
        self.assertIsNone(dlg.result)
        # And Install is not merely inert, it is visibly unavailable.
        self.assertIn("disabled", dlg.go.state())

    def test_it_returns_what_was_ticked_not_what_was_behind(self):
        # The whole reason this is a dialog. GB is current and GBC is behind;
        # ticking the current one and clearing the stale one has to be obeyed.
        rels = releases("2.0", [core_mod.GBC_REPO])
        versions = {c.id: None for c in core_mod.CORES}
        versions["kroy.GB"] = "2.0"
        dlg = self.build(versions, rels)
        for cid, row in dlg.rows.items():
            row[1] = (cid == "kroy.GB")
        dlg.ok()
        self.assertEqual([c.id for c in dlg.result], ["kroy.GB"])

    def test_ticking_nothing_does_not_close_with_an_empty_install(self):
        rels = releases("2.0", [core_mod.GBC_REPO])
        dlg = self.build({c.id: None for c in core_mod.CORES}, rels)
        for row in dlg.rows.values():
            row[1] = False
        dlg.ok()
        self.assertIsNone(dlg.result)

    def test_it_says_which_card_and_what_is_missing(self):
        dlg = self.build({c.id: None for c in core_mod.CORES},
                         releases("2.0", [core_mod.GBC_REPO]))
        shown = labels(dlg)
        self.assertTrue(any("/card" in t for t in shown), shown)
        # Every core is absent, so every row says so rather than saying nothing.
        cards = [dlg.tree.set(cid, "card") for cid in dlg.rows]
        self.assertEqual(cards, ["not installed"] * len(core_mod.CORES))
        # And the ones with nothing to install say so. Every core has a
        # repository now, so they all reach the same branch; "not released
        # yet", the no-repository wording, is covered below with a registry
        # built for it rather than by leaving a core without one.
        cells = [dlg.tree.set(cid, "avail") for cid in dlg.rows]
        self.assertIn("no release yet", cells)             # repo, not in map
        self.assertNotIn("not released yet", cells)

    def test_a_core_with_no_repository_says_a_different_kind_of_nothing(self):
        # "no release yet" starts working on its own when a tag lands. "not
        # released yet" does not, and the dialog says which is which.
        norepo = core_mod.Core("kroy.NONE", "none", "Nothing", "kroy.NONE_",
                               None, ())
        with unittest.mock.patch.object(core_mod, "CORES",
                                        core_mod.CORES + (norepo,)):
            dlg = self.build({c.id: None for c in core_mod.CORES},
                             releases("2.0", [core_mod.GBC_REPO]))
            cells = [dlg.tree.set(cid, "avail") for cid in dlg.rows]
            self.assertIn("not released yet", cells)


if __name__ == "__main__":
    unittest.main()
