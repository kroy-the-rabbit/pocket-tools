# SPDX-License-Identifier: GPL-3.0-or-later
"""The cartridge list itself: what is filed where, and what a move carries."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "cheatgui"))
sys.path.insert(1, os.path.join(ROOT, "cheats"))


class CartsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp.name, "config")
        # carts and prefs read the config path at import time, so they are
        # imported after the environment is set and reloaded per test.
        import importlib
        import prefs
        import carts
        self.prefs = importlib.reload(prefs)
        self.carts = importlib.reload(carts)

    def tearDown(self) -> None:
        if self.saved is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self.saved
        self.tmp.cleanup()

    # ---------------------------------------------------------------- adding --
    def test_a_cartridge_remembers_its_system(self):
        self.assertTrue(self.carts.add("Pokemon Red (USA)", "gb"))
        self.assertTrue(self.carts.add("Wario Land 3 (World)", "gbc"))
        by_name = {c.name: c.platform for c in self.carts.all()}
        self.assertEqual(by_name["Pokemon Red (USA)"], "gb")
        self.assertEqual(by_name["Wario Land 3 (World)"], "gbc")

    def test_the_system_decides_the_folder_on_the_card(self):
        self.carts.add("Pokemon Red (USA)", "gb")
        cart = self.carts.all("/card")[0]
        self.assertEqual(
            cart.cht_path,
            "/card/Assets/gb/common/Cartridges/Pokemon Red (USA).cht")

    def test_duplicates_are_refused_whatever_the_case(self):
        self.assertTrue(self.carts.add("Zelda DX (USA)", "gbc"))
        self.assertFalse(self.carts.add("zelda dx (usa)", "gb"))
        self.assertEqual(len(self.carts.all()), 1)

    # --------------------------------------------------------------- grouping --
    def test_grouped_gives_positions_in_display_order(self):
        for name, plat in (("Aladdin (USA)", "gb"), ("Wario Land 3", "gbc"),
                           ("Pokemon Red (USA)", "gb")):
            self.carts.add(name, plat)
        everything = self.carts.all()
        groups = self.carts.grouped(everything)
        self.assertEqual([pid for pid, _ in groups], ["gbc", "gb"])
        for pid, positions in groups:
            for i in positions:
                self.assertEqual(everything[i].platform, pid)
        self.assertEqual(sum(len(p) for _, p in groups), len(everything))

    def test_a_system_with_no_cartridges_is_not_shown(self):
        self.carts.add("Wario Land 3", "gbc")
        self.assertEqual([pid for pid, _ in
                          self.carts.grouped(self.carts.all())], ["gbc"])

    def test_grouped_of_nothing_is_nothing(self):
        self.assertEqual(self.carts.grouped([]), [])

    # ------------------------------------------------------------------ moving --
    def test_moving_carries_the_pinned_cheat_file(self):
        """Correcting the system should not also lose the file you chose."""
        self.carts.add("Aladdin (USA)", "gb")
        before = self.carts.all()[0]
        self.prefs.set_source(before.path, "/somewhere/Aladdin (USA).cht")

        self.assertTrue(self.carts.set_platform("Aladdin (USA)", "gbc"))
        after = self.carts.all()[0]
        self.assertEqual(after.platform, "gbc")
        self.assertEqual(self.prefs.get_source(after.path),
                         "/somewhere/Aladdin (USA).cht")
        self.assertIsNone(self.prefs.get_source(before.path))

    def test_moving_where_it_already_is_changes_nothing(self):
        self.carts.add("Wario Land 3", "gbc")
        self.assertFalse(self.carts.set_platform("Wario Land 3", "gbc"))

    def test_moving_something_not_listed_changes_nothing(self):
        self.assertFalse(self.carts.set_platform("Never Added", "gb"))

    def test_an_unknown_system_is_refused(self):
        self.carts.add("Wario Land 3", "gbc")
        with self.assertRaises(ValueError):
            self.carts.set_platform("Wario Land 3", "snes")

    # ---------------------------------------------------------------- removing --
    def test_removing_forgets_the_pin_under_either_system(self):
        self.carts.add("Aladdin (USA)", "gb")
        cart = self.carts.all()[0]
        self.prefs.set_source(cart.path, "/somewhere/Aladdin.cht")
        self.assertTrue(self.carts.remove("Aladdin (USA)"))
        self.assertEqual(self.carts.all(), [])
        for pid in self.carts.PLATFORMS:
            self.assertIsNone(
                self.prefs.get_source(f"cart:{pid}:Aladdin (USA)"))

    def test_removing_something_not_listed_says_so(self):
        self.assertFalse(self.carts.remove("Never Added"))

    def test_only_the_game_boys_can_hold_a_cartridge(self):
        """Game Boy Advance and PC Engine are SD card only.

        Cartridges are unsupported on both until their cores support them.
        This tuple is the enforcement, not a display order: a system absent
        from it has no reachable cartridge path anywhere in the app. Pinned
        because adding a system to card.ENABLED is the natural moment to add
        it here too, and for these two that would be wrong.
        """
        self.assertEqual(self.carts.PLATFORMS, ("gbc", "gb"))
        for pid in ("gba", "pce"):
            self.assertNotIn(pid, self.carts.PLATFORMS)

    def test_a_cartridge_cannot_be_filed_under_a_rom_only_system(self):
        """add() and set_platform() both refuse, not just one of them."""
        for pid in ("gba", "pce"):
            with self.assertRaises(ValueError):
                self.carts.add("Some Game", pid)
        self.carts.add("Aladdin (USA)", "gb")
        for pid in ("gba", "pce"):
            with self.assertRaises(ValueError):
                self.carts.set_platform("Aladdin (USA)", pid)


if __name__ == "__main__":
    unittest.main()
