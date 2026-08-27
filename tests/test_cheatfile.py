# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading a cheat file for the right system.

The Game Boy Advance tests are the point of this file. GBA codes are a
different language from Game Boy ones, and the Game Boy parser does not reject
them, it misreads them: every code comes out looking plausible and meaning
something else. These pin the fact that the two are kept apart, and now also
that the right reader gets the right answer.
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

import cheatfile                                             # noqa: E402
import chtparse                                              # noqa: E402
import writer                                                # noqa: E402

# A real CodeBreaker file, from the libretro database. Two of these codes carry
# a second half after the '+', which is where the Game Boy parser loses half
# the file.
GBA_FILE = b'''cheats = 3

cheat0_desc = "Enable Code (Must Be On)"
cheat0_code = "00004E72+000A+100010E4+0007"
cheat0_enable = false

cheat1_desc = "Infinite Money"
cheat1_code = "3300786D+00FF"
cheat1_enable = true

cheat2_desc = "Max Hearts"
cheat2_code = "3300786F+00FF"
cheat2_enable = false
'''

GB_FILE = b'''cheats = 2

cheat0_desc = "Infinite Health"
cheat0_code = "0140AAC6"
cheat0_enable = true

cheat1_desc = "999 Rupees"
cheat1_code = "9199ADC6+9109AEC6"
cheat1_enable = false
'''


class GameBoyStillDecodes(unittest.TestCase):
    def test_codes_are_decoded(self):
        groups = cheatfile.parse(GB_FILE, "gbc")
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].codes[0].address, 0xC6AA)
        self.assertEqual(groups[0].codes[0].value, 0x40)
        self.assertEqual(len(groups[1].codes), 2)

    def test_the_core_has_limits_and_they_are_the_rtl_s(self):
        self.assertEqual(cheatfile.limits("gbc"),
                         (chtparse.MAX_GROUPS, chtparse.MAX_CODES))
        self.assertTrue(cheatfile.decoded("gb"))
        self.assertTrue(cheatfile.decoded("gbc"))

    def test_how_a_code_applies_is_known(self):
        groups = cheatfile.parse(GB_FILE, "gbc")
        self.assertEqual(cheatfile.applied_by(groups[0].codes[0], "gbc"), "poke")


class GameBoyAdvanceIsReadNotGuessed(unittest.TestCase):
    """GBA used to be carried verbatim because nothing could read its codes.

    That changed at both ends: the core defines a cheat format, and `gbacht`
    decodes CodeBreaker and GameShark against the whole libretro directory. The
    reason this module exists did not change - the Game Boy parser still does
    not *reject* a GBA file, it misreads it - so that half is still pinned here
    and the other half now checks the answer instead of the refusal.
    """

    def test_the_game_boy_parser_misreads_gba_codes(self):
        """Why this module exists. Not a rejection: a wrong answer.

        `3300786D+00FF` is a CodeBreaker code. The Game Boy parser sees eight
        hex digits, reads them as a GameShark code, and reports a write to an
        address that is not in the code at all. The `+00FF` is four digits,
        matches nothing, and vanishes.
        """
        wrong = chtparse.parse(GBA_FILE, max_codes=1 << 30, max_groups=1 << 30)
        money = [g for g in wrong if g.desc == "Infinite Money"][0]
        self.assertEqual(len(money.codes), 1)          # the +00FF is gone
        self.assertEqual(money.codes[0].address, 0x6D78)   # invented
        self.assertEqual(money.codes[0].value, 0x00)       # invented

        # And what this module does instead: one code, both halves, and an
        # address that is actually in it.
        right = cheatfile.parse(GBA_FILE, "gba")
        money = [g for g in right if g.desc == "Infinite Money"][0]
        self.assertEqual([c.raw for c in money.codes], ["3300786D+00FF"])
        self.assertEqual(money.codes[0].address, 0x300786C)

    def test_a_byte_code_is_placed_in_its_lane(self):
        """The engine writes 32 bits at a time; a byte code picks a lane.

        `3300786D` and `3300786F` are two bytes of the same word. Decoded, they
        share an address and differ only in where the 0xFF sits, which is the
        read-modify-write in gba_cheats seen from the outside.
        """
        by_desc = {g.desc: g for g in cheatfile.parse(GBA_FILE, "gba")}
        money = by_desc["Infinite Money"].codes[0]
        hearts = by_desc["Max Hearts"].codes[0]
        self.assertEqual(money.address, hearts.address)
        self.assertEqual(money.value, 0x0000FF00)      # byte 1 of the word
        self.assertEqual(hearts.value, 0xFF000000)     # byte 3

    def test_a_code_with_no_memory_effect_produces_nothing(self):
        """"Enable Code (Must Be On)" is a game id and a hook.

        CodeBreaker types 0 and 1 tell the cheat device which game it is
        looking at and where to attach; neither writes memory. There is nothing
        for the engine to do with them, and inventing a poke would invent a
        cheat.
        """
        enable = [g for g in cheatfile.parse(GBA_FILE, "gba")
                  if g.desc == "Enable Code (Must Be On)"][0]
        self.assertEqual(enable.codes, [])

    def test_an_unusable_cheat_is_still_listed(self):
        """Shown and greyed, not silently missing.

        The description is what a user recognises. A row that is present and
        unpickable says "this cheat cannot be expressed"; a row that is absent
        says nothing, and looks like the file was not read.
        """
        descs = [g.desc for g in cheatfile.parse(GBA_FILE, "gba")]
        self.assertIn("Enable Code (Must Be On)", descs)

    def test_the_code_is_read_and_the_limit_is_the_cores(self):
        self.assertTrue(cheatfile.decoded("gba"))
        self.assertEqual(cheatfile.limits("gba"), (32, 32))
        groups = cheatfile.parse(GBA_FILE, "gba")
        money = [g for g in groups if g.desc == "Infinite Money"][0]
        self.assertEqual(cheatfile.applied_by(money.codes[0], "gba"), "poke")

    def test_descriptions_and_enable_flags_are_read(self):
        groups = cheatfile.parse(GBA_FILE, "gba")
        self.assertEqual([g.desc for g in groups],
                         ["Enable Code (Must Be On)", "Infinite Money",
                          "Max Hearts"])
        self.assertEqual([g.enabled for g in groups], [False, True, False])

    def test_a_gba_file_written_back_is_the_same_cheats(self):
        """Round trip, plus the second file the core actually reads.

        Not the same *file*: the codes come back in gbacht's canonical spelling
        rather than the library's, because the library's cannot be recovered.
        What has to survive is which cheats they are.
        """
        groups = [g for g in cheatfile.parse(GBA_FILE, "gba") if g.codes]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "game.gba.cht")
            cheats, codes, removed = writer.write(path, groups, "gba")
            self.assertFalse(removed)
            self.assertEqual(cheats, len(groups))
            self.assertEqual(codes, sum(len(g.codes) for g in groups))
            back = writer.load_library(path, "gba")
            self.assertEqual([writer.key_of(g) for g in back],
                             [writer.key_of(g) for g in groups])
            self.assertEqual([g.desc for g in back], [g.desc for g in groups])
            # Everything written is written enabled: the file is the selection.
            self.assertTrue(all(g.enabled for g in back))
            # And the core does not read that file, so it must exist too.
            self.assertTrue(os.path.exists(
                writer.compiled_path(path, "gba")))

    def test_the_core_limit_is_enforced_now_that_there_is_one(self):
        groups = [g for g in cheatfile.parse(GBA_FILE, "gba") if g.codes]
        self.assertEqual(writer.check(groups, "gba"), [])
        self.assertTrue(writer.check(groups * 50, "gba"))
        # while the Game Boy core's limits are still enforced
        gb = cheatfile.parse(GB_FILE, "gbc")
        self.assertTrue(writer.check(gb * 40, "gbc"))


class SearchDirectories(unittest.TestCase):
    def test_gba_never_matches_a_game_boy_file(self):
        """A near miss between GB and GBC is useful. GB against GBA is not."""
        import cheatlib
        self.assertEqual(cheatlib.SEARCH["gba"], ("gba",))
        self.assertIn("gb", cheatlib.SEARCH["gbc"])
        self.assertIn("gbc", cheatlib.SEARCH["gb"])
        self.assertNotIn("gba", cheatlib.SEARCH["gb"])
        self.assertNotIn("gba", cheatlib.SEARCH["gbc"])


if __name__ == "__main__":
    unittest.main()
