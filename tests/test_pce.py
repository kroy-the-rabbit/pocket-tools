# SPDX-License-Identifier: GPL-3.0-or-later
"""PC Engine cheat codes: both forms the database writes, and writing back.

The corpus this is pinned against is the 397 files in libretro's
`NEC - PC Engine - TurboGrafx 16`, read in full on 2026-08-25. Every case below
is a real row from it, not an invented one, because the surprises here were all
data rather than design: a third of the directory carries its codes in keys
other than `cheatN_code`, one value is written with an unbalanced quote, and
one address is a negative number stored as unsigned 64-bit.

The round-trip matters more than it looks. `writer.key_of` identifies a cheat
by its code text, so if this and `writer.render` disagree by one character then
nothing a user saves ever shows as already installed.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "cheatgui"))
sys.path.insert(1, os.path.join(ROOT, "cheats"))

import cheatfile                                             # noqa: E402
import pce                                                   # noqa: E402
import writer                                                # noqa: E402


def cht(text: str) -> bytes:
    return text.encode()


FORM_A = cht('''cheats = 2

cheat0_desc = "Infinite Energy"
cheat0_code = "1f1548:64"
cheat0_enable = false

cheat1_desc = "Max Gold"
cheat1_code = "1f141d:09+1f141e:09+1f141f:09"
cheat1_enable = false
''')

# Adventures Of Mr. Heli, trimmed of the rumble keys it also carries.
FORM_B = cht('''cheat0_address = "6148"
cheat0_address_bit_position = "0"
cheat0_big_endian = "false"
cheat0_cheat_type = "1"
cheat0_code = ""
cheat0_desc = "Infinite Energy"
cheat0_enable = "false"
cheat0_handler = "1"
cheat0_memory_search_size = "3"
cheat0_rumble_type = "0"
cheat0_value = "10"
cheats = "1"
''')


class FormA(unittest.TestCase):
    def test_one_code(self) -> None:
        g = pce.parse(FORM_A)[0]
        self.assertEqual(g.desc, "Infinite Energy")
        self.assertEqual(len(g.codes), 1)
        self.assertEqual(g.codes[0].address, 0x1F1548)
        self.assertEqual(g.codes[0].value, 0x64)
        self.assertEqual(g.codes[0].kind, "poke")

    def test_codes_joined_with_plus_are_one_cheat(self) -> None:
        g = pce.parse(FORM_A)[1]
        self.assertEqual([c.address for c in g.codes],
                         [0x1F141D, 0x1F141E, 0x1F141F])

    def test_enable_false_is_carried(self) -> None:
        self.assertFalse(any(g.enabled for g in pce.parse(FORM_A)))

    def test_a_cheat_with_no_enable_key_defaults_to_on(self) -> None:
        g = pce.parse(cht('cheat0_code = "1f1548:64"\n'))[0]
        self.assertTrue(g.enabled)

    def test_an_address_the_cpu_cannot_reach_is_refused(self) -> None:
        # Both Magical Chase files carry 1f0000f:0c, seven hex digits where
        # all 1027 other codes have six. 0x1F0000F is past 21 address lines.
        g = pce.parse(cht('cheat0_desc = "Infinite Lives"\n'
                          'cheat0_code = "1f0000f:0c"\n'))[0]
        self.assertEqual(g.codes, [])
        self.assertEqual(g.desc, "Infinite Lives")   # still listed, still named

    def test_a_value_wider_than_a_byte_is_refused(self) -> None:
        self.assertEqual(pce.parse(cht('cheat0_code = "1f1548:100"\n'))[0].codes,
                         [])

    def test_junk_is_refused_rather_than_guessed(self) -> None:
        self.assertIsNone(pce.parse_code("nonsense"))
        self.assertIsNone(pce.parse_code("1f1548"))
        self.assertIsNone(pce.parse_code("zz:64"))


class FormB(unittest.TestCase):
    def test_a_decimal_offset_becomes_a_cpu_address(self) -> None:
        g = pce.parse(FORM_B)[0]
        self.assertEqual(g.desc, "Infinite Energy")
        self.assertEqual([c.address for c in g.codes], [pce.WORK_RAM + 6148])
        self.assertEqual(g.codes[0].value, 10)

    def test_it_comes_out_in_the_same_shape_as_form_a(self) -> None:
        self.assertEqual(pce.parse(FORM_B)[0].codes[0].raw, "1f1804:0a")

    def test_rumble_keys_are_dropped_not_carried(self) -> None:
        raw = pce.parse(FORM_B)[0].codes[0].raw
        self.assertNotIn("rumble", raw)
        self.assertEqual(raw.count(":"), 1)

    def test_a_watch_only_row_writes_nothing(self) -> None:
        # cheat_type 0 is RetroArch's "disabled": the row watches an address to
        # fire a rumble. 70 rows are like this. Turning one into a poke would
        # invent a cheat: "Rumble on gold change" would pin your gold to 5.
        g = pce.parse(FORM_B.replace(b'cheat_type = "1"',
                                     b'cheat_type = "0"'))[0]
        self.assertEqual(g.codes, [])
        self.assertEqual(g.desc, "Infinite Energy")

    def test_a_partial_byte_is_not_guessed(self) -> None:
        # Two Wonder Momo rows are bit-level, memory_search_size 0.
        g = pce.parse(FORM_B.replace(b'memory_search_size = "3"',
                                     b'memory_search_size = "0"'))[0]
        self.assertEqual(g.codes, [])

    def test_a_row_with_no_value_writes_nothing(self) -> None:
        g = pce.parse(FORM_B.replace(b'cheat0_value = "10"\n', b""))[0]
        self.assertEqual(g.codes, [])

    def test_a_negative_offset_stored_as_unsigned_is_refused(self) -> None:
        # Bomberman 94 carries 18446744073709546426, which is -5190.
        g = pce.parse(FORM_B.replace(b'cheat0_address = "6148"',
                                     b'cheat0_address = "18446744073709546426"'))[0]
        self.assertEqual(g.codes, [])

    def test_an_unbalanced_quote_in_a_number_is_tolerated(self) -> None:
        # Veigues writes cheat1_value = ""255", and it is the second half of a
        # two part cheat whose first half parses.
        g = pce.parse(FORM_B.replace(b'cheat0_value = "10"',
                                     b'cheat0_value = ""255"'))[0]
        self.assertEqual([c.value for c in g.codes], [255])

    def test_the_repeat_family_expands(self) -> None:
        # Wonder Momo's "One hit kills bosses": address 1191, count 2, step 32.
        text = FORM_B.replace(b'cheat0_address = "6148"',
                              b'cheat0_address = "1191"\n'
                              b'cheat0_repeat_count = "2"\n'
                              b'cheat0_repeat_add_to_address = "32"\n'
                              b'cheat0_repeat_add_to_value = "0"')
        g = pce.parse(text)[0]
        self.assertEqual([c.address for c in g.codes],
                         [pce.WORK_RAM + 1191, pce.WORK_RAM + 1223])

    def test_a_repeat_count_of_one_changes_nothing(self) -> None:
        text = FORM_B.replace(b'cheat0_code = ""',
                              b'cheat0_code = ""\ncheat0_repeat_count = "1"')
        self.assertEqual(len(pce.parse(text)[0].codes), 1)

    def test_keys_out_of_order_still_read(self) -> None:
        # Form B is written alphabetically, so the description arrives after
        # the address and there is no stream order to lean on.
        lines = FORM_B.decode().splitlines()
        shuffled = "\n".join(reversed(lines)).encode()
        self.assertEqual(pce.parse(shuffled)[0].desc, "Infinite Energy")


class WorkRam(unittest.TestCase):
    def test_the_usual_addresses_are_inside_it(self) -> None:
        self.assertTrue(pce.in_work_ram(0x1F0000))
        self.assertTrue(pce.in_work_ram(0x1F1FFF))

    def test_the_supergrafx_range_is_addressable_and_outside(self) -> None:
        # 13 codes in the database sit here. A SuperGrafx has 32KB where this
        # has 8, and the core drops SuperGrafx, so they are unreachable.
        self.assertFalse(pce.in_work_ram(0x1F2000))
        self.assertFalse(pce.in_work_ram(0x1F2656))

    def test_they_are_carried_rather_than_dropped(self) -> None:
        g = pce.parse(cht('cheat0_code = "1f2656:08"\n'))[0]
        self.assertEqual([c.address for c in g.codes], [0x1F2656])


class RoundTrip(unittest.TestCase):
    """What is written has to read back as the same cheat."""

    def check(self, data: bytes) -> None:
        live = [g for g in pce.parse(data) if g.codes]
        back = pce.parse(writer.render(live).encode())
        self.assertEqual([writer.key_of(g) for g in back],
                         [writer.key_of(g) for g in live])
        self.assertTrue(all(g.enabled for g in back))

    def test_form_a(self) -> None:
        self.check(FORM_A)

    def test_form_b_becomes_form_a(self) -> None:
        self.check(FORM_B)
        self.assertIn("1f1804:0a", writer.render(pce.parse(FORM_B)))

    def test_the_written_form_is_what_the_database_already_uses(self) -> None:
        # Not cosmetic: the library file and the file we write have to spell
        # the same poke identically or nothing shows as already installed.
        self.assertEqual(pce.render_code(0x1F1548, 0x64), "1f1548:64")
        self.assertEqual(pce.render_code(0x1F0584, 0x01), "1f0584:01")


class ThroughCheatfile(unittest.TestCase):
    """The rest of the app only ever calls cheatfile."""

    def test_pce_is_decoded_not_carried(self) -> None:
        self.assertTrue(cheatfile.decoded("pce"))

    def test_it_routes_to_this_parser(self) -> None:
        g = cheatfile.parse(FORM_B, "pce")[0]
        self.assertEqual(g.codes[0].raw, "1f1804:0a")

    def test_it_has_exactly_one_mechanism(self) -> None:
        # Which is what collapses the Applied column: see ui.retune_applied.
        self.assertEqual(cheatfile.mechanisms("pce"), ("poke",))
        self.assertEqual(len(cheatfile.mechanisms("gbc")), 2)

    def test_every_code_is_a_poke(self) -> None:
        for g in cheatfile.parse(FORM_A, "pce"):
            for c in g.codes:
                self.assertEqual(cheatfile.applied_by(c, "pce"), "poke")

    def test_no_limit_is_claimed_for_a_core_that_does_not_exist(self) -> None:
        self.assertIsNone(cheatfile.limits("pce"))

    def test_the_game_boy_parser_would_get_it_confidently_wrong(self) -> None:
        """The whole reason cheatfile routes by platform.

        Handed a PC Engine file, chtparse does not fail. It reads the six hex
        digits of `1f1548` as a Game Genie code and reports a ROM patch of
        0x1F to $7154, dropping the `:64` entirely. A RAM poke of 0x64 to
        $1F1548 comes out as a ROM patch of 0x1F to $7154: wrong mechanism,
        wrong address, wrong value, and nothing anywhere says so.
        """
        import chtparse
        code = chtparse.parse(FORM_A)[0].codes[0]
        self.assertEqual(code.kind, "gg")
        self.assertEqual((code.address, code.value), (0x7154, 0x1F))

        ours = cheatfile.parse(FORM_A, "pce")[0].codes[0]
        self.assertEqual((ours.kind, ours.address, ours.value),
                         ("poke", 0x1F1548, 0x64))


class DiscsOnTheCard(unittest.TestCase):
    """A disc is a cue in the same Assets/pce folder as a HuCard.

    The Pocket has one platform for both. This app lists them as two systems,
    because they are two cheat corpora, so a walk of the shared folder has to
    file each file under its own system and the .bin under neither.
    """

    def setUp(self):
        import tempfile
        import card
        self.card = card
        self.root = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.root, True)
        self.pce = os.path.join(self.root, "Assets", "pce", "common")
        os.makedirs(self.pce)
        for name in ("Bonk.pce", "Rondo.cue", "Rondo.bin", "Rondo.cue.cht"):
            with open(os.path.join(self.pce, name), "wb") as f:
                f.write(b"x")

    def test_both_systems_exist_from_the_one_folder(self):
        ids = [p.id for p in self.card.Card(self.root).platforms()]
        self.assertIn("pce", ids)
        self.assertIn("pcecd", ids)

    def test_each_system_lists_only_its_own_files(self):
        c = self.card.Card(self.root)
        hucard = [g.name for g in c.games("pce")]
        disc = [g.name for g in c.games("pcecd")]
        self.assertEqual(hucard, ["Bonk"])
        self.assertEqual(disc, ["Rondo"])

    def test_the_bin_is_not_a_game(self):
        c = self.card.Card(self.root)
        names = [os.path.basename(g.path)
                 for pid in ("pce", "pcecd") for g in c.games(pid)]
        self.assertNotIn("Rondo.bin", names)

    def test_a_disc_cheat_file_follows_the_one_rule(self):
        c = self.card.Card(self.root)
        disc = c.games("pcecd")[0]
        self.assertEqual(disc.platform, "pcecd")
        self.assertEqual(os.path.basename(disc.cht_path), "Rondo.cue.cht")
        games, chts = c.scan("pcecd")
        self.assertIn(disc.cht_path, chts)

    def test_a_disc_reads_cheats_the_hucard_way(self):
        groups = cheatfile.parse(cht(
            'cheats = 1\ncheat0_desc = "Lives"\ncheat0_code = "1f008d:09"\n'),
            "pcecd")
        self.assertEqual(len(groups), 1)
        self.assertEqual(cheatfile.applied_by(groups[0].codes[0], "pcecd"),
                         "poke")
        self.assertTrue(cheatfile.decoded("pcecd"))

    def test_a_system_card_is_not_a_game(self):
        # bios_3_0_usa.pce is a .pce in the games folder. The core's manifest
        # names it and its alternates as slot 0's fixed files, so the list
        # leaves all of them out and keeps the real HuCard.
        import json
        for name in ("bios_3_0_usa.pce", "bios_2_0_jap.pce"):
            with open(os.path.join(self.pce, name), "wb") as f:
                f.write(b"x" * 16)
        cdir = os.path.join(self.root, "Cores", "kroy.PCE")
        os.makedirs(cdir)
        with open(os.path.join(cdir, "data.json"), "w") as f:
            json.dump({"data": {"data_slots": [{
                "name": "Cartridge", "id": 0, "required": True,
                "filename": "bios_3_0_usa.pce",
                "alternate_filenames": ["bios_3_0_jap.pce", "bios_2_0_jap.pce"],
                "extensions": ["pce", "sgx"]}]}}, f)
        with open(os.path.join(cdir, "core.json"), "w") as f:
            json.dump({"core": {"metadata": {"version": "0.9999"}}}, f)
        names = [g.name for g in self.card.Card(self.root).games("pce")]
        self.assertEqual(names, ["Bonk"])
        # And through fill(), which is the path the window takes.
        plat = [p for p in self.card.Card(self.root).platforms()
                if p.id == "pce"][0]
        self.card.Card(self.root).fill(plat)
        self.assertEqual([g.name for g in plat.games], ["Bonk"])

    def test_the_disc_has_no_platform_file_to_read(self):
        # /Platforms/pce.json is the HuCard's name; a disc keeps its own.
        os.makedirs(os.path.join(self.root, "Platforms"))
        with open(os.path.join(self.root, "Platforms", "pce.json"), "w") as f:
            f.write('{"platform": {"name": "TurboGrafx-16"}}')
        c = self.card.Card(self.root)
        self.assertEqual(c.platform_name("pce"), "TurboGrafx-16")
        self.assertEqual(c.platform_name("pcecd"), "PC Engine CD")


if __name__ == "__main__":
    unittest.main()
