# SPDX-License-Identifier: GPL-3.0-or-later
"""Game Gear cheat codes: both kinds, the skip rules, and writing back.

The decoding is the core's own gg2bin and ggcht, so these check that gg.py
splits a file the way gg2bin.model does and that a written file reads back as
the same cheats.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "cheatgui"))
sys.path.insert(1, os.path.join(ROOT, "cheats"))

import card                                                  # noqa: E402
import cheatfile                                             # noqa: E402
import gg                                                    # noqa: E402
import gg2bin                                                # noqa: E402
import ggcht                                                 # noqa: E402
import writer                                                # noqa: E402

LIB = b'''cheats = 7

cheat0_desc = "Infinite Lives"
cheat0_code = "00D2-9809"
cheat0_enable = false

cheat1_desc = "Genie joined with plus"
cheat1_code = "058+BA8+E66"
cheat1_enable = false

cheat2_desc = "Two genie codes"
cheat2_code = "058-BA8-E66+3A7-17A-2A2"
cheat2_enable = true

cheat3_desc = "Outside work RAM"
cheat3_code = "0080-0001"
cheat3_enable = false

cheat4_desc = "Choose a value"
cheat4_code = "00C0-00XX"
cheat4_enable = false

cheat5_desc = "Mixed widths"
cheat5_code = "058-BA8-E66+00C0-0001"
cheat5_enable = false

cheat6_desc = "Six digit genie"
cheat6_code = "3A7-17A"
'''


class Decoding(unittest.TestCase):
    def setUp(self):
        self.groups = gg.parse(LIB)

    def test_action_replay_is_a_work_ram_poke(self):
        c = self.groups[0].codes
        self.assertEqual([(x.raw, x.kind, x.address, x.value) for x in c],
                         [("00D2-9809", "poke", 0xD298, 0x09)])

    def test_game_genie_is_a_patch_decoded_by_the_core_model(self):
        (c,) = self.groups[1].codes
        self.assertEqual(c.kind, "patch")
        self.assertEqual(c.raw, "058-BA8-E66")
        self.assertEqual((c.address, c.value, c.compare),
                         ggcht.decode("058BA8E66"))

    def test_plus_between_whole_codes_splits_them(self):
        self.assertEqual([c.raw for c in self.groups[2].codes],
                         ["058-BA8-E66", "3A7-17A-2A2"])

    def test_skipped_codes_leave_a_placeholder(self):
        for i in (3, 4, 5):
            self.assertEqual(self.groups[i].codes, [], self.groups[i].desc)

    def test_six_digit_genie_has_no_compare(self):
        (c,) = self.groups[6].codes
        self.assertEqual((c.raw, c.kind, c.compare), ("3A7-17A", "patch", None))

    def test_enable_flags(self):
        self.assertEqual([g.enabled for g in self.groups],
                         [False, False, True, False, False, False, True])

    def test_same_entries_as_the_core_model(self):
        # gg2bin.model, the core's reference, over the same file with every
        # cheat on, must accept the same codes in the same order.
        entries, _, _ = gg2bin.model(LIB.decode(), enable_all=True)
        mine = [(("G" if c.kind == "patch" else "P"), c.address, c.value)
                for g in self.groups for c in g.codes]
        theirs = [(e[0], e[1], e[2]) for e in entries]
        self.assertEqual(mine, theirs)


class System(unittest.TestCase):
    def test_registered(self):
        self.assertIn("gg", card.ENABLED)
        self.assertEqual(card.SUPPORTED["gg"], "Sega - Game Gear")
        self.assertIn(".gg", card.ROM_EXT)
        self.assertTrue(cheatfile.decoded("gg"))
        self.assertEqual(cheatfile.mechanisms("gg"), ("poke", "patch"))
        self.assertEqual(cheatfile.limits("gg"), (32, 32))

    def test_the_core_reads_the_cht_itself(self):
        self.assertIsNone(writer.compiled_path("/x/Game.gg.cht", "gg"))


class WriteBack(unittest.TestCase):
    def test_rendered_file_reads_back_as_the_same_cheats(self):
        picked = [g for g in gg.parse(LIB) if g.codes]
        back = cheatfile.parse(writer.render(picked).encode(), "gg")
        self.assertEqual([writer.key_of(g) for g in back],
                         [writer.key_of(g) for g in picked])
        self.assertTrue(all(g.enabled for g in back))


if __name__ == "__main__":
    unittest.main()
