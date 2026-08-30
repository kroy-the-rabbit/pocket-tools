# SPDX-License-Identifier: GPL-3.0-or-later
"""Game Boy Advance: decoding, the two files on the card, and the round trip.

This system is the only one where what lands on the card is not the file that
was picked from. The core cannot parse text, so `writer.write` produces a
`.chtbin` of packed 128-bit entries next to the `.cht` that remains the state
file. Most of what is worth testing is that those two cannot disagree.

The decoding itself is `gbacht`'s, a copy of the core repo's reference model
checked there against all 513 files in libretro's `Nintendo - Game Boy
Advance`. It is not re-tested here. What is tested is the seam: that a code
survives being turned into an entry, named, written to a `.cht`, read back,
and packed, and comes out as the same 128-bit word it started as.

Codes below are real forms, and the expected words are derived from the layout
in gba_cheats.vhd rather than from what the code happens to produce:

    bits  31:0    value            bits  91:64   address, 28 bits
    bits 99:96    optype           bits 103:100  byte enables
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

import card                                                  # noqa: E402
import cheatfile                                             # noqa: E402
import gba                                                   # noqa: E402
import writer                                                # noqa: E402

LIB = b'''cheats = 4

cheat0_desc = "16-bit poke"
cheat0_code = "12000520 000003E7"
cheat0_enable = false

cheat1_desc = "32-bit poke"
cheat1_code = "22000524 0000270F"
cheat1_enable = false

cheat2_desc = "conditional, two entries"
cheat2_code = "D2000530 00000001+22000534 12345678"
cheat2_enable = false

cheat3_desc = "encrypted, nothing usable"
cheat3_code = "0B070768 A3C2D410"
cheat3_enable = false
'''


class Decoding(unittest.TestCase):
    def test_the_system_is_switched_on(self):
        # The whole point of this file. GBA was off because its core had no
        # cheat slot and its codes could not be read; both stopped being true.
        self.assertIn("gba", card.ENABLED)
        self.assertTrue(cheatfile.decoded("gba"))

    def test_one_code_is_one_entry(self):
        groups = gba.parse(LIB)
        self.assertEqual([len(g.codes) for g in groups[:3]], [1, 1, 2])

    def test_a_disabled_cheat_is_still_offered(self):
        # Everything in a libretro file is off. Reading it the converter's way
        # would show an empty list.
        groups = gba.parse(LIB)
        self.assertGreaterEqual(len(groups), 3)
        self.assertTrue(all(not g.enabled for g in groups))

    def test_a_cheat_with_no_usable_code_keeps_its_description(self):
        # 0B070768 is an encrypted word run through a raw decoder: its address
        # is not memory this machine has. It is dropped rather than poked.
        groups = gba.parse(LIB)
        last = [g for g in groups if g.desc and "encrypted" in g.desc]
        self.assertTrue(all(not g.codes for g in last))

    def test_the_word_is_the_layout_in_the_rtl(self):
        groups = gba.parse(LIB)
        blob = gba.pack(groups[:1])
        word = int.from_bytes(blob[16:32], "little")
        self.assertEqual(word & 0xFFFFFFFF, 0x3E7)            # value
        self.assertEqual((word >> 64) & 0x0FFFFFFF, 0x2000520)  # address
        self.assertEqual((word >> 96) & 0xF, 0x0)             # optype: always
        self.assertEqual((word >> 100) & 0xF, 0x3)            # two byte lanes

    def test_a_conditional_keeps_its_pair_adjacent_and_ordered(self):
        # gba_cheats expresses "if" as adjacency: a failed compare sets
        # skip_next, which suppresses whatever entry comes next. Reordering
        # reattaches the condition to a different cheat.
        cond = [g for g in gba.parse(LIB) if len(g.codes) == 2][0]
        blob = gba.pack([cond])
        first = int.from_bytes(blob[16:32], "little")
        second = int.from_bytes(blob[32:48], "little")
        self.assertEqual((first >> 96) & 0xF, 0x1)            # optype ==
        self.assertEqual((second >> 96) & 0xF, 0x0)           # the write
        self.assertEqual((second >> 64) & 0x0FFFFFFF, 0x2000534)

    def test_only_one_mechanism_is_claimed(self):
        # gba_cheats is a poker, not a read override. There is no Game Genie
        # for this machine, so the Applied column has nothing to distinguish.
        self.assertEqual(cheatfile.mechanisms("gba"), ("poke",))

    def test_the_limit_is_the_cheat_table(self):
        self.assertEqual(cheatfile.limits("gba"), (32, 32))


class Header(unittest.TestCase):
    def test_magic_and_count(self):
        blob = gba.pack(gba.parse(LIB)[:3])
        self.assertEqual(blob[:4], b"GBAC")
        self.assertEqual(blob[4], 1)
        self.assertEqual(int.from_bytes(blob[6:8], "little"), 4)
        self.assertEqual(len(blob), 16 + 4 * 16)

    def test_an_empty_selection_is_a_header_and_nothing_else(self):
        self.assertEqual(len(gba.pack([])), 16)

    def test_more_entries_than_the_table_holds_is_refused(self):
        one = gba.parse(LIB)[0]
        with self.assertRaises(ValueError):
            gba.pack([one] * 33)


class RoundTrip(unittest.TestCase):
    """A cheat has to survive .cht -> entry -> name -> .cht -> entry."""

    def test_render_and_reparse_gives_the_same_words(self):
        groups = gba.parse(LIB)[:3]
        again = gba.parse(writer.render(groups).encode())
        self.assertEqual(gba.pack(groups), gba.pack(again))

    def test_a_written_file_shows_as_installed(self):
        # writer.key_of names a cheat by its code text. If render and parse
        # disagree by one character, nothing a user saves reads back as on.
        groups = gba.parse(LIB)[:3]
        again = gba.parse(writer.render(groups).encode())
        self.assertEqual([writer.key_of(g) for g in groups],
                         [writer.key_of(g) for g in again])

    def test_the_library_spelling_and_ours_agree(self):
        # The library writes "12000520 000003E7"; we write "12000520+000003E7".
        # Both have to name the same cheat or a library row never ticks.
        space = gba.parse(b'cheat0_code = "12000520 000003E7"\n')
        plus = gba.parse(b'cheat0_code = "12000520+000003E7"\n')
        joined = gba.parse(b'cheat0_code = "12000520000003E7"\n')
        self.assertEqual(writer.key_of(space[0]), writer.key_of(plus[0]))
        self.assertEqual(writer.key_of(space[0]), writer.key_of(joined[0]))


class TwoFiles(unittest.TestCase):
    """The .cht is the state, the .chtbin is what the hardware reads."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cht = os.path.join(self.dir.name, "Game.gba.cht")
        self.bin = os.path.join(self.dir.name, "Game.gba.chtbin")
        self.addCleanup(self.dir.cleanup)

    def test_compiled_path_only_where_the_core_needs_one(self):
        self.assertEqual(writer.compiled_path(self.cht, "gba"), self.bin)
        self.assertIsNone(writer.compiled_path(self.cht, "gbc"))

    def test_writing_produces_both(self):
        groups = gba.parse(LIB)[:3]
        writer.write(self.cht, groups, "gba")
        self.assertTrue(os.path.exists(self.cht))
        self.assertTrue(os.path.exists(self.bin))
        self.assertEqual(open(self.bin, "rb").read(), gba.pack(groups))

    def test_the_binary_matches_the_text_beside_it(self):
        groups = gba.parse(LIB)[:3]
        writer.write(self.cht, groups, "gba")
        reread = writer.load_library(self.cht, "gba")
        self.assertEqual(open(self.bin, "rb").read(), gba.pack(reread))

    def test_an_empty_selection_removes_both(self):
        writer.write(self.cht, gba.parse(LIB)[:3], "gba")
        _, _, removed = writer.write(self.cht, [], "gba")
        self.assertTrue(removed)
        self.assertFalse(os.path.exists(self.cht))
        self.assertFalse(os.path.exists(self.bin))

    def test_a_stale_binary_is_replaced_not_left(self):
        # The failure this guards: a second save that rewrites the .cht and
        # leaves the hardware reading the first save's cheats.
        first = gba.parse(LIB)[:1]
        second = gba.parse(LIB)[1:2]
        writer.write(self.cht, first, "gba")
        writer.write(self.cht, second, "gba")
        self.assertEqual(open(self.bin, "rb").read(), gba.pack(second))

    def test_installed_cheats_read_back_from_the_text(self):
        groups = gba.parse(LIB)[:3]
        writer.write(self.cht, groups, "gba")
        keys = writer.load_installed(self.cht, "gba")
        self.assertEqual(keys, {writer.key_of(g) for g in groups})


class Limits(unittest.TestCase):
    def test_check_reports_a_selection_that_will_not_fit(self):
        one = gba.parse(LIB)[0]
        self.assertEqual(writer.check([one], "gba"), [])
        problems = writer.check([one] * 33, "gba")
        self.assertTrue(problems)
        self.assertIn("33", problems[0])


if __name__ == "__main__":
    unittest.main()
