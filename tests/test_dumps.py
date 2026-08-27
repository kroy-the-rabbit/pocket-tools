# SPDX-License-Identifier: GPL-3.0-or-later
"""Cartridge dumps: reading a card, identifying what is on it, and filing it.

Nothing here touches a real card, a real library, a real config directory or a
real cheat database. The ROM images are a few bytes of header and some filler
built for these tests, and the DATs are synthetic XML in No-Intro's schema with
invented games in it -- their data is theirs and none of it is in this
repository. The one test that reads a real download is skipped unless that
download happens to be on the machine, and asserts nothing that would put its
contents in this file.

The filename shapes are real, though, and that is deliberate: ZELDA_DIN__AZ7E,
GBAZELDA_MC and MARIO_S_PICROSS are what a dumper core wrote onto a card, and
the header reader has to reproduce them out of the bytes or it is not reading
the same fifteen bytes the core read.
"""
from __future__ import annotations

import hashlib
import importlib
import os
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "cheatgui"))
sys.path.insert(1, os.path.join(ROOT, "cheats"))

import dumps                                                 # noqa: E402
import nointro                                               # noqa: E402


# --------------------------------------------------------------- the images --
def gb_rom(title: bytes, cgb: bool = False, code: bytes = b"",
           filler: bytes = b"\x01", size: int = 0x8000) -> bytes:
    """A Game Boy image with a real logo, so the header reader recognises it.

    The title field is written at its full sixteen bytes and NUL padded, which
    is what a cartridge does; the core reads fifteen of them and that is where
    the manufacturer code leaks into a filename.
    """
    data = bytearray(filler * size)
    data[dumps.GB_LOGO_AT:dumps.GB_LOGO_AT + 48] = dumps.GB_LOGO
    field = bytearray(b"\x00" * 16)
    field[:len(title)] = title
    if code:
        field[0x0B:0x0F] = code
    data[0x134:0x144] = field
    data[dumps.CGB_FLAG_AT] = 0xC0 if cgb else 0x00
    return bytes(data)


def gba_rom(title: bytes, code: bytes = b"AZ7E", filler: bytes = b"\x02",
            size: int = 0x8000) -> bytes:
    """A Game Boy Advance image whose header checksum actually adds up."""
    data = bytearray(filler * size)
    data[0xA0:0xAC] = title.ljust(12, b"\x00")[:12]
    data[0xAC:0xB0] = code
    data[dumps.GBA_FIXED_AT] = dumps.GBA_FIXED
    data[dumps.GBA_SUM_AT] = (-(sum(data[0xA0:dumps.GBA_SUM_AT]) + 0x19)) & 0xFF
    return bytes(data)


def sha1_of(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def crc_of(data: bytes) -> str:
    return "%08x" % zlib.crc32(data)


# ----------------------------------------------------------------- the DATs --
def dat_xml(system: str, games: list[dict]) -> str:
    """A Parent-Clone shaped DAT over the images handed in.

    Parent-Clone rather than Standard because the clone link is a name, which
    is what the cheat fallback needs; nointro normalises both to the same
    field, and its own tests cover the other flavour.
    """
    out = ['<?xml version="1.0"?>', "<datafile>", "  <header>",
           f"    <name>{system}</name>",
           "    <version>20260827-000000</version>", "  </header>"]
    for g in games:
        parent = f' cloneof="{g["parent"]}"' if g.get("parent") else ""
        data = g["data"]
        out.append(f'  <game name="{g["name"]}"{parent}>')
        out.append(f'    <rom name="{g["rom"]}" size="{len(data)}" '
                   f'crc="{g.get("crc") or crc_of(data)}" '
                   f'sha1="{sha1_of(data)}"/>')
        out.append("  </game>")
    out.append("</datafile>")
    return "\n".join(out) + "\n"


class Env(unittest.TestCase):
    """A temporary card, library, config and cheat database, and nothing real."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {k: os.environ.get(k) for k in
                      ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "POCKET_CHEAT_DB")}
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp.name, "config")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp.name, "data")
        os.environ["POCKET_CHEAT_DB"] = os.path.join(self.tmp.name, "cht")
        # Both read their path at import time, so they are reloaded after the
        # environment is set. Everything else asks them at call time and picks
        # the new answer up on its own.
        import cheatlib
        import prefs
        self.prefs = importlib.reload(prefs)
        self.cheatlib = importlib.reload(cheatlib)
        self.dumps = dumps
        self.card_root = os.path.join(self.tmp.name, "card")
        self.root = os.path.join(self.tmp.name, "library")
        import library
        self.lib = library
        os.makedirs(self.dumps.dump_dir(self.card_root), exist_ok=True)

    def tearDown(self) -> None:
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    # -- building a card --------------------------------------------------
    def put(self, name: str, data: bytes) -> str:
        """A file in the core's output directory on the card."""
        full = os.path.join(self.dumps.dump_dir(self.card_root), name)
        with open(full, "wb") as f:
            f.write(data)
        return full

    def catalog(self, **systems) -> nointro.Catalog:
        """A Catalog over synthetic DATs. Keys are gb, gbc, gba."""
        cat = nointro.Catalog()
        dats = os.path.join(self.tmp.name, "dats")
        os.makedirs(dats, exist_ok=True)
        for pid, games in systems.items():
            path = os.path.join(dats, pid + ".dat")
            with open(path, "w") as f:
                f.write(dat_xml(nointro.SYSTEMS[pid], games))
            self.assertEqual(cat.add(path), pid)
        return cat

    def index(self):
        return self.lib.load(self.root)

    def cheat_db(self, names: list[str], system: str = "gb") -> None:
        """Just enough libretro database for the matcher to have something."""
        full = os.path.join(self.tmp.name, "cht", nointro.SYSTEMS[system])
        os.makedirs(full, exist_ok=True)
        for name in names:
            with open(os.path.join(full, name + ".cht"), "w") as f:
                f.write('cheats = 1\n\ncheat0_desc = "One"\n'
                        'cheat0_code = "010CAAC6"\ncheat0_enable = false\n')
        self.cheatlib.refresh()

    def read(self, path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()


# ---------------------------------------------------------------- prong 1 --
class HeaderTest(unittest.TestCase):
    """The header, and the names the core built out of it.

    Every expected name here came off a real card. If these break, the app has
    stopped reading the same bytes the core read, and nothing downstream can
    corroborate anything.
    """

    def test_the_manufacturer_code_leaks_into_a_colour_title(self):
        """ZELDA_DIN__AZ7E is not a typo; it is fifteen bytes read as eleven."""
        rom = gb_rom(b"ZELDA DIN", cgb=True, code=b"AZ7E")
        head = dumps.header(rom)
        self.assertEqual(head.platform, "gbc")
        self.assertEqual(head.title, "ZELDA_DIN__AZ7E")
        self.assertEqual(head.code, "AZ7E")

    def test_a_title_that_fills_the_field_runs_into_the_code(self):
        rom = gb_rom(b"ZELDA NAYRU", cgb=True, code=b"AZ8E")
        self.assertEqual(dumps.header(rom).title, "ZELDA_NAYRUAZ8E")

    def test_padding_after_the_title_is_dropped(self):
        """Otherwise ZELDA.gb would have been written ZELDA__________.gb."""
        self.assertEqual(dumps.header(gb_rom(b"ZELDA")).title, "ZELDA")

    def test_a_space_and_an_apostrophe_both_become_underscores(self):
        self.assertEqual(dumps.header(gb_rom(b"MARIO'S PICROSS")).title,
                         "MARIO_S_PICROSS")

    def test_the_cgb_flag_names_the_core_s_platform(self):
        self.assertEqual(dumps.header(gb_rom(b"TETRIS")).platform, "gb")
        self.assertEqual(dumps.header(gb_rom(b"ZELDA", cgb=True)).platform,
                         "gbc")

    def test_a_game_boy_advance_header_is_read_from_its_own_offsets(self):
        head = dumps.header(gba_rom(b"GBAZELDA MC", b"BZME"))
        self.assertEqual(head.platform, "gba")
        self.assertEqual(head.title, "GBAZELDA_MC")
        self.assertEqual(head.code, "BZME")

    def test_an_underscore_in_the_header_survives(self):
        self.assertEqual(dumps.header(gba_rom(b"GOLDEN_SUN_A")).title,
                         "GOLDEN_SUN_A")

    def test_nothing_recognisable_is_no_platform_rather_than_a_guess(self):
        self.assertEqual(dumps.header(b"\x00" * 0x8000).platform, "")
        self.assertFalse(dumps.header(b"\x00" * 0x8000))

    def test_a_fixed_byte_alone_is_not_a_cartridge(self):
        """The header checksum is what stops 0x96 at 0xB2 being enough."""
        rom = bytearray(gba_rom(b"REAL"))
        rom[0xBD] ^= 0xFF
        self.assertEqual(dumps.header(bytes(rom)).platform, "")


class ScanTest(Env):
    def test_a_gitkeep_is_not_a_dump(self):
        """The real directory has one, and it is not a bad Game Boy image."""
        self.put("TETRIS.gb", gb_rom(b"TETRIS"))
        self.put(".gitkeep", b"")
        found = self.dumps.scan(self.card_root)
        self.assertEqual([d.name for d in found], ["TETRIS.gb"])

    def test_a_sidecar_is_not_a_dump_either(self):
        self.put("TETRIS.gb", gb_rom(b"TETRIS"))
        self.put("TETRIS.cart.json", b'{"title": "TETRIS"}')
        self.assertEqual([d.name for d in self.dumps.scan(self.card_root)],
                         ["TETRIS.gb"])

    def test_a_card_without_the_dumper_core_is_empty_not_an_error(self):
        self.assertEqual(self.dumps.scan(os.path.join(self.tmp.name, "nope")),
                         [])

    def test_hashing_is_the_identity_and_the_corroboration(self):
        rom = gb_rom(b"TETRIS")
        self.put("TETRIS.gb", rom)
        one = self.dumps.scan(self.card_root)[0]
        self.assertEqual(one.sha1, sha1_of(rom))
        self.assertEqual(one.crc32, crc_of(rom))
        self.assertEqual(one.size, len(rom))

    def test_the_extension_does_not_decide_the_platform(self):
        """The core wrote every dump .gb for its whole life so far."""
        self.put("GBAZELDA_MC.gb", gba_rom(b"GBAZELDA MC"))
        self.assertEqual(self.dumps.scan(self.card_root)[0].platform, "gba")

    def test_a_dump_renamed_by_hand_is_noticed_and_not_acted_on(self):
        self.put("my zelda.gb", gb_rom(b"ZELDA"))
        self.assertTrue(self.dumps.scan(self.card_root)[0].renamed)


# ---------------------------------------------------------------- prong 2 --
class IdentifyTest(Env):
    def setUp(self) -> None:
        super().setUp()
        self.yellow = gb_rom(b"POKEMON YELLOW", cgb=True, filler=b"\x11")
        self.gold = gb_rom(b"POKEMON GOLD", filler=b"\x22")
        self.minish = gba_rom(b"GBAZELDA MC", filler=b"\x33")

    def three_dats(self) -> nointro.Catalog:
        """The two Pokemon that sit on the wrong sides of the CGB flag."""
        return self.catalog(
            gb=[{"name": "Pokemon - Yellow Version (USA, Europe) "
                         "(CGB+SGB Enhanced)",
                 "rom": "Pokemon - Yellow Version (USA, Europe) "
                        "(CGB+SGB Enhanced).gb",
                 "data": self.yellow}],
            gbc=[{"name": "Pokemon - Gold Version (USA, Europe) (SGB Enhanced) "
                          "(GB Compatible)",
                  "rom": "Pokemon - Gold Version (USA, Europe) (SGB Enhanced) "
                         "(GB Compatible).gbc",
                  "data": self.gold}],
            gba=[{"name": "Legend of Zelda, The - The Minish Cap (USA)",
                  "rom": "Legend of Zelda, The - The Minish Cap (USA).gba",
                  "data": self.minish}])

    def dump_of(self, name: str, data: bytes):
        return self.dumps.read(self.put(name, data))

    def test_the_dat_that_holds_the_hash_decides_the_system(self):
        """The CGB flag is wrong in both directions and cannot be the answer.

        Yellow is colour-enhanced and lives in the Game Boy DAT; Gold is Game
        Boy compatible and lives in the Game Boy Color one. Both set the same
        header bit as the other's system would, so anything that asked the
        header would file both of them wrong.
        """
        cat = self.three_dats()
        yellow = self.dumps.identify(self.dump_of("POKEMON_YELLOW.gbc",
                                                 self.yellow), cat)
        gold = self.dumps.identify(self.dump_of("POKEMON_GOLD.gb", self.gold),
                                   cat)
        self.assertEqual(yellow.system, "gb")
        self.assertEqual(gold.system, "gbc")

    def test_the_header_and_the_answer_are_allowed_to_disagree(self):
        cat = self.three_dats()
        dump = self.dump_of("POKEMON_YELLOW.gbc", self.yellow)
        self.assertEqual(dump.platform, "gbc")
        self.assertEqual(self.dumps.identify(dump, cat).system, "gb")

    def test_the_name_comes_back_verbatim_with_its_extension(self):
        cat = self.three_dats()
        found = self.dumps.identify(self.dump_of("GBAZELDA_MC.gb",
                                                 self.minish), cat)
        self.assertEqual(found.name,
                         "Legend of Zelda, The - The Minish Cap (USA).gba")
        self.assertEqual(found.title,
                         "Legend of Zelda, The - The Minish Cap (USA)")
        self.assertEqual(found.region, "USA")

    def test_a_region_with_two_countries_in_it_stays_whole(self):
        self.assertEqual(
            self.dumps.region_of("Tetris (USA, Australia) (Rev 1)"),
            "USA, Australia")
        self.assertIsNone(self.dumps.region_of("Tetris"))

    def test_nothing_loaded_is_no_data_and_not_unknown(self):
        """Reporting it as unknown would blame the cartridge for a download."""
        dump = self.dump_of("TETRIS.gb", gb_rom(b"TETRIS"))
        found = self.dumps.identify(dump, nointro.Catalog())
        self.assertIs(found.outcome, nointro.Outcome.NO_DATA)

    def test_an_unknown_dump_says_what_was_searched(self):
        cat = self.three_dats()
        dump = self.dump_of("HOMEBREW.gb", gb_rom(b"HOMEBREW", filler=b"\x55"))
        found = self.dumps.identify(dump, cat)
        self.assertIs(found.outcome, nointro.Outcome.UNKNOWN)
        self.assertEqual(set(found.searched), {"gb", "gbc", "gba"})
        self.assertIn("Searched all three", self.dumps.dat_note(cat))

    def test_the_note_names_the_gap_when_one_dat_is_missing(self):
        cat = self.catalog(gb=[{"name": "Tetris (World)",
                                "rom": "Tetris (World).gb",
                                "data": gb_rom(b"TETRIS")}])
        note = self.dumps.dat_note(cat)
        self.assertIn("Nintendo - Game Boy Color", note)
        self.assertIn("No data loaded for", note)

    def test_a_disagreeing_size_is_a_mismatch_carrying_the_near_answer(self):
        rom = gb_rom(b"TETRIS")
        cat = self.catalog(gb=[{"name": "Tetris (World)",
                                "rom": "Tetris (World).gb", "data": rom}])
        # The DAT and the file agree on the hash and disagree on the CRC32,
        # which is the shape of a DAT or a file that is wrong about itself.
        entry = cat.get("gb").entries[sha1_of(rom)]
        cat.get("gb").entries[sha1_of(rom)] = nointro.Entry(
            entry.name, entry.game, entry.size, "deadbeef", entry.sha1)
        found = self.dumps.identify(self.dump_of("TETRIS.gb", rom), cat)
        self.assertIs(found.outcome, nointro.Outcome.MISMATCH)
        self.assertEqual(found.disagreed, ("crc32",))
        self.assertIsNotNone(found.entry)
        self.assertIsNone(found.name)          # not a name to be sure of

    def test_the_read_back_check_is_only_made_when_the_core_s_value_is_known(self):
        rom = gb_rom(b"TETRIS")
        dump = self.dump_of("TETRIS.gb", rom)
        self.assertIsNone(self.dumps.readback(dump, None))
        self.assertTrue(self.dumps.readback(dump, crc_of(rom)))
        self.assertTrue(self.dumps.readback(dump, crc_of(rom).upper()))
        self.assertFalse(self.dumps.readback(dump, "deadbeef"))


# ---------------------------------------------------------------- prong 3 --
class FileTest(Env):
    """Filing a dump: two copies, both verified, and the card left alone."""

    def setUp(self) -> None:
        super().setUp()
        self.rom = gb_rom(b"ZELDA")
        self.path = self.put("ZELDA.gb", self.rom)
        self.cat = self.catalog(gb=[{
            "name": "Legend of Zelda, The - Link's Awakening (USA, Europe)",
            "rom": "Legend of Zelda, The - Link's Awakening (USA, Europe).gb",
            "data": self.rom}])
        self.canonical = \
            "Legend of Zelda, The - Link's Awakening (USA, Europe).gb"

    def proposal(self, index=None, **kw):
        dump = self.dumps.read(self.path)
        index = self.index() if index is None else index
        return self.dumps.propose(dump, self.dumps.identify(dump, self.cat),
                                  self.root, index, **kw), index

    def test_a_clean_dump_proposes_both_names(self):
        prop, _ = self.proposal()
        self.assertIs(prop.verdict, self.dumps.Verdict.FILE)
        self.assertEqual(prop.rom_name, self.canonical)
        self.assertEqual(prop.dump_name, "ZELDA.gb")

    def test_committing_writes_the_canonical_copy_and_the_original(self):
        prop, index = self.proposal()
        filing = self.dumps.commit(prop, index)
        self.assertTrue(filing.ok, filing.problem)
        self.assertTrue(filing.verified)
        self.assertEqual(self.read(filing.rom_path), self.rom)
        self.assertEqual(os.path.basename(filing.rom_path), self.canonical)
        self.assertEqual(os.path.basename(filing.dump_path), "ZELDA.gb")
        self.assertEqual(self.read(filing.dump_path), self.rom)

    def test_committing_leaves_the_card_alone(self):
        """Nothing is removed until it is asked for, separately."""
        prop, index = self.proposal()
        self.dumps.commit(prop, index)
        self.assertTrue(os.path.exists(self.path))

    def test_the_row_records_what_the_dat_said(self):
        prop, index = self.proposal()
        row = self.dumps.commit(prop, index).row
        self.assertEqual(row.sha1, sha1_of(self.rom))
        self.assertEqual(row.system, "gb")
        self.assertEqual(row.region, "USA, Europe")
        self.assertEqual(row.title,
                         "Legend of Zelda, The - Link's Awakening (USA, Europe)")
        self.assertEqual(self.index().get(row.sha1).rom, self.canonical)

    def test_no_half_written_file_is_left_behind(self):
        prop, index = self.proposal()
        self.dumps.commit(prop, index)
        for place in (self.lib.roms_dir(self.root),
                      self.lib.dumps_dir(self.root)):
            self.assertEqual([n for n in os.listdir(place)
                              if n.endswith(".tmp")], [])

    def test_an_unidentified_dump_is_offered_nothing_automatic(self):
        """A bad dump, an uncatalogued revision and a repro all land here."""
        path = self.put("HOMEBREW.gb", gb_rom(b"HOMEBREW", filler=b"\x77"))
        dump = self.dumps.read(path)
        index = self.index()
        prop = self.dumps.propose(dump, self.dumps.identify(dump, self.cat),
                                  self.root, index)
        self.assertIs(prop.verdict, self.dumps.Verdict.UNIDENTIFIED)
        self.assertIsNone(prop.rom_name)
        filing = self.dumps.commit(prop, index)
        self.assertFalse(filing.ok)
        self.assertFalse(os.path.isdir(self.lib.roms_dir(self.root)))

    # -- the byte-for-byte gate -------------------------------------------
    def test_a_copy_that_does_not_compare_equal_is_not_kept(self):
        """Patched, because a copy that goes wrong cannot be arranged.

        What is being checked is the consequence: a failed comparison leaves
        no file under a name that means something, no .tmp beside it, and an
        index that does not claim the dump was filed.
        """
        prop, index = self.proposal()
        real = self.dumps.same_bytes
        self.dumps.same_bytes = lambda a, b: False
        try:
            filing = self.dumps.commit(prop, index)
        finally:
            self.dumps.same_bytes = real
        self.assertFalse(filing.ok)
        self.assertFalse(filing.verified)
        self.assertIn("did not match", filing.problem)
        self.assertEqual(os.listdir(self.lib.roms_dir(self.root)), [])
        self.assertNotIn(sha1_of(self.rom), self.index())

    def test_nothing_is_removed_from_the_card_unless_the_bytes_compare(self):
        """The strongest available check, in front of the only delete."""
        prop, index = self.proposal()
        filing = self.dumps.commit(prop, index)
        with open(filing.dump_path, "ab") as f:
            f.write(b"\x00")           # the library copy is now not the dump
        after = self.dumps.remove_from_card(filing)
        self.assertFalse(after.removed)
        self.assertIn("does not match", after.problem)
        self.assertTrue(os.path.exists(self.path))

    def test_the_card_original_goes_only_after_it_is_verified(self):
        prop, index = self.proposal()
        filing = self.dumps.commit(prop, index)
        after = self.dumps.remove_from_card(filing)
        self.assertTrue(after.removed, after.problem)
        self.assertFalse(os.path.exists(self.path))
        self.assertTrue(os.path.exists(after.dump_path))
        self.assertTrue(os.path.exists(after.rom_path))

    def test_a_card_pulled_before_the_answer_costs_nothing(self):
        prop, index = self.proposal()
        filing = self.dumps.commit(prop, index)
        os.remove(self.path)
        after = self.dumps.remove_from_card(filing)
        self.assertFalse(after.removed)
        self.assertIn("gone", after.problem)


class CollisionTest(Env):
    """One name, two different files. The only question the app asks."""

    def setUp(self) -> None:
        super().setUp()
        # Two cartridges that both title themselves ZELDA. This is the case
        # that lost a dump on a real card.
        self.plain = gb_rom(b"ZELDA", filler=b"\x01")
        self.dx = gb_rom(b"ZELDA", cgb=True, filler=b"\x02")
        self.cat = self.catalog(
            gb=[{"name": "Legend of Zelda, The - Link's Awakening (USA, Europe)",
                 "rom": "Zelda (USA).gb", "data": self.plain}],
            gbc=[{"name": "Legend of Zelda, The - Link's Awakening DX (USA)",
                  "rom": "Zelda (USA).gbc", "data": self.dx}])

    def file_one(self, data: bytes, name: str = "ZELDA.gb",
                 choice=None):
        path = self.put(name, data)
        dump = self.dumps.read(path)
        index = self.index()
        prop = self.dumps.propose(dump, self.dumps.identify(dump, self.cat),
                                  self.root, index)
        kw = {"choice": choice} if choice is not None else {}
        return prop, self.dumps.commit(prop, index, **kw), index

    def collide(self):
        """File the first, then present the second under the same name."""
        self.file_one(self.plain, "ZELDA.gb")
        os.remove(os.path.join(self.dumps.dump_dir(self.card_root), "ZELDA.gb"))
        path = self.put("ZELDA.gb", self.dx)
        dump = self.dumps.read(path)
        index = self.index()
        prop = self.dumps.propose(dump, self.dumps.identify(dump, self.cat),
                                  self.root, index)
        return prop, index, path

    def test_the_cores_own_names_collide_and_that_is_the_question(self):
        prop, _index, _path = self.collide()
        self.assertIs(prop.verdict, self.dumps.Verdict.COLLIDES)
        self.assertIsNotNone(prop.dump_standing)
        self.assertEqual(prop.dump_standing.sha1, sha1_of(self.plain))
        self.assertTrue(prop.dump_standing.filed)

    def test_keep_both_suffixes_with_the_hash_and_not_a_counter(self):
        """A counter records arrival order, which is not a fact about either."""
        prop, index, _ = self.collide()
        filing = self.dumps.commit(prop, index, self.dumps.Choice.KEEP_BOTH)
        self.assertTrue(filing.ok, filing.problem)
        short = sha1_of(self.dx)[:8]
        self.assertEqual(os.path.basename(filing.dump_path),
                         f"ZELDA [{short}].gb")
        self.assertNotIn("_2", filing.dump_path)
        # The first file is exactly where it was.
        first = os.path.join(self.lib.dumps_dir(self.root), "ZELDA.gb")
        self.assertEqual(self.read(first), self.plain)
        self.assertEqual(self.read(filing.dump_path), self.dx)

    def test_keep_both_is_stable_across_reruns(self):
        prop, index, _ = self.collide()
        self.dumps.commit(prop, index, self.dumps.Choice.KEEP_BOTH)
        self.assertEqual(sorted(os.listdir(self.lib.dumps_dir(self.root))),
                         ["ZELDA [%s].gb" % sha1_of(self.dx)[:8], "ZELDA.gb"])

    def test_replace_puts_the_new_file_in_before_the_old_one_goes(self):
        prop, index, _ = self.collide()
        filing = self.dumps.commit(prop, index, self.dumps.Choice.REPLACE)
        self.assertTrue(filing.ok, filing.problem)
        self.assertEqual(os.path.basename(filing.dump_path), "ZELDA.gb")
        self.assertEqual(self.read(filing.dump_path), self.dx)
        self.assertEqual(os.listdir(self.lib.dumps_dir(self.root)),
                         ["ZELDA.gb"])

    def test_replace_does_not_move_the_old_file_into_cart_dumps(self):
        """It was never a card original, and that directory means something."""
        prop, index, _ = self.collide()
        self.dumps.commit(prop, index, self.dumps.Choice.REPLACE)
        names = os.listdir(self.lib.dumps_dir(self.root))
        self.assertEqual(names, ["ZELDA.gb"])
        for name in os.listdir(self.lib.roms_dir(self.root)):
            self.assertNotIn("[", name)

    def test_discard_leaves_everything_and_the_dump_stays_on_the_card(self):
        prop, index, path = self.collide()
        filing = self.dumps.commit(prop, index, self.dumps.Choice.DISCARD)
        self.assertFalse(filing.ok)
        self.assertTrue(filing.discarded)
        self.assertTrue(os.path.exists(path))
        self.assertEqual(os.listdir(self.lib.dumps_dir(self.root)),
                         ["ZELDA.gb"])
        self.assertEqual(self.read(os.path.join(
            self.lib.dumps_dir(self.root), "ZELDA.gb")), self.plain)

    def test_discarding_is_not_rejecting_and_the_dump_comes_back(self):
        """A decision the user has not really made should be asked again."""
        prop, index, path = self.collide()
        self.dumps.commit(prop, index, self.dumps.Choice.DISCARD)
        self.assertFalse(self.dumps.rejected(prop.sha1))
        again = self.dumps.survey(self.card_root, self.root, self.cat)
        self.assertEqual([p.verdict for p in again.pending],
                         [self.dumps.Verdict.COLLIDES])

    def test_the_same_bytes_at_the_same_name_are_not_a_question(self):
        """The ordinary case when a cartridge is dumped twice."""
        self.file_one(self.plain, "ZELDA.gb")
        os.remove(os.path.join(self.dumps.dump_dir(self.card_root), "ZELDA.gb"))
        path = self.put("ZELDA.gb", self.plain)
        dump = self.dumps.read(path)
        # Asked against an empty index, so the answer comes from the files and
        # not from a row that happens to remember it.
        prop = self.dumps.propose(dump, self.dumps.identify(dump, self.cat),
                                  self.root, self.lib.Index())
        self.assertIs(prop.verdict, self.dumps.Verdict.FILE)
        before = os.stat(os.path.join(self.lib.dumps_dir(self.root),
                                      "ZELDA.gb")).st_ino
        filing = self.dumps.commit(prop, self.lib.Index())
        self.assertTrue(filing.ok, filing.problem)
        self.assertEqual(os.stat(filing.dump_path).st_ino, before)

    def test_a_file_that_cannot_be_read_is_never_overwritten(self):
        """The app cannot tell what it would be destroying, so it does not."""
        self.lib.create(self.root)
        # A directory where a file belongs: present, and unreadable as bytes.
        os.makedirs(os.path.join(self.lib.dumps_dir(self.root), "ZELDA.gb"))
        path = self.put("ZELDA.gb", self.plain)
        dump = self.dumps.read(path)
        index = self.index()
        prop = self.dumps.propose(dump, self.dumps.identify(dump, self.cat),
                                  self.root, index)
        self.assertIs(prop.verdict, self.dumps.Verdict.UNREADABLE)
        filing = self.dumps.commit(prop, index)
        self.assertFalse(filing.ok)
        self.assertTrue(os.path.isdir(
            os.path.join(self.lib.dumps_dir(self.root), "ZELDA.gb")))


class IdempotencyTest(Env):
    """Same bytes, same answer, no work. That is the whole rule."""

    def setUp(self) -> None:
        super().setUp()
        self.rom = gb_rom(b"TETRIS")
        self.path = self.put("TETRIS.gb", self.rom)
        self.cat = self.catalog(gb=[{"name": "Tetris (World) (Rev 1)",
                                     "rom": "Tetris (World) (Rev 1).gb",
                                     "data": self.rom}])

    def prop(self, index=None, **kw):
        dump = self.dumps.read(self.path)
        index = self.index() if index is None else index
        return self.dumps.propose(dump, self.dumps.identify(dump, self.cat),
                                  self.root, index, **kw), index

    def test_a_dump_already_filed_is_skipped_silently(self):
        prop, index = self.prop()
        self.dumps.commit(prop, index)
        again, _ = self.prop()
        self.assertIs(again.verdict, self.dumps.Verdict.FILED)
        self.assertTrue(again.quiet)
        self.assertFalse(again.actionable)

    def test_a_rejection_is_remembered_outside_the_index(self):
        """It cannot live in a cache a rebuild throws away and reproduces."""
        prop, _ = self.prop()
        self.dumps.reject(prop.dump)
        self.assertIsNone(self.lib.load(self.root).get(prop.sha1))
        self.assertIsNotNone(self.prefs.get_rejected(prop.sha1))
        again, _ = self.prop()
        self.assertIs(again.verdict, self.dumps.Verdict.REJECTED)
        self.assertTrue(again.quiet)

    def test_a_rejected_dump_is_re_offered_only_when_asked(self):
        prop, _ = self.prop()
        self.dumps.reject(prop.dump)
        asked, _ = self.prop(offer_rejected=True)
        self.assertIs(asked.verdict, self.dumps.Verdict.FILE)
        self.dumps.unreject(prop.sha1)
        after, _ = self.prop()
        self.assertIs(after.verdict, self.dumps.Verdict.FILE)

    def test_a_known_sha1_whose_file_is_gone_is_reported_not_deleted(self):
        """The app does not remove things it did not just write."""
        prop, index = self.prop()
        filing = self.dumps.commit(prop, index)
        os.remove(filing.rom_path)
        again, _ = self.prop()
        self.assertIs(again.verdict, self.dumps.Verdict.MISSING)
        self.assertIn("gone", again.note)
        self.assertTrue(os.path.exists(self.path))

    def test_re_filing_a_missing_copy_restores_its_recorded_name(self):
        prop, index = self.prop()
        filing = self.dumps.commit(prop, index)
        os.remove(filing.rom_path)
        again, index = self.prop()
        back = self.dumps.commit(again, index)
        self.assertTrue(back.ok, back.problem)
        self.assertEqual(back.rom_path, filing.rom_path)

    def test_the_day_the_library_first_saw_the_bytes_is_kept(self):
        prop, index = self.prop()
        first = self.dumps.commit(prop, index)
        stale = self.lib.Row(sha1=first.row.sha1, size=first.row.size,
                             crc32=first.row.crc32, filed="2020-01-01",
                             rom=first.row.rom, dump=first.row.dump)
        index.put(stale)
        self.lib.save(self.root, index)
        os.remove(first.rom_path)
        again, index = self.prop()
        back = self.dumps.commit(again, index)
        self.assertEqual(back.row.filed, "2020-01-01")

    def test_a_fully_processed_card_does_nothing_and_says_so(self):
        prop, index = self.prop()
        self.dumps.commit(prop, index)
        sweep = self.dumps.survey(self.card_root, self.root, self.cat)
        self.assertTrue(sweep.quiet)
        self.assertEqual(sweep.pending, [])
        self.assertIn("Nothing to do", sweep.summary())
        self.assertIn("already filed", sweep.summary())

    def test_an_empty_card_says_that_instead(self):
        os.remove(self.path)
        sweep = self.dumps.survey(self.card_root, self.root, self.cat)
        self.assertEqual(sweep.summary(), "No dumps on the card.")

    def test_a_survey_writes_nothing(self):
        self.dumps.survey(self.card_root, self.root, self.cat)
        self.assertFalse(os.path.exists(self.root))


# ---------------------------------------------------------------- prong 4 --
class CheatTest(Env):
    """The enrichment is what makes the matcher we already have work."""

    def setUp(self) -> None:
        super().setUp()
        self.parent_rom = gb_rom(b"WIDGET", filler=b"\x41")
        self.clone_rom = gb_rom(b"WIDGET", filler=b"\x42")
        self.cat = self.catalog(gb=[
            {"name": "Widget Quest (USA)", "rom": "Widget Quest (USA).gb",
             "data": self.parent_rom},
            {"name": "Widget Quest (Japan)", "rom": "Widget Quest (Japan).gb",
             "data": self.clone_rom, "parent": "Widget Quest (USA)"}])

    def identify(self, name: str, data: bytes):
        dump = self.dumps.read(self.put(name, data))
        return self.dumps.identify(dump, self.cat)

    def test_the_canonical_name_is_what_the_matcher_is_good_at(self):
        """match.best() is hopeless at WIDGET.gb and good at what the DAT says."""
        self.cheat_db(["Widget Quest (USA)"])
        found = self.dumps.cheat(self.identify("WIDGET.gb", self.parent_rom))
        self.assertTrue(found)
        self.assertEqual(found.name, "Widget Quest (USA)")
        self.assertFalse(found.via_parent)

    def test_a_clone_with_no_file_of_its_own_falls_back_to_its_parent(self):
        self.cheat_db(["Widget Quest (USA)"])
        found = self.dumps.cheat(self.identify("WIDGET2.gb", self.clone_rom))
        self.assertTrue(found)
        self.assertTrue(found.via_parent)
        self.assertEqual(found.name, "Widget Quest (USA)")

    def test_a_clone_with_its_own_file_does_not_use_the_parent(self):
        self.cheat_db(["Widget Quest (USA)", "Widget Quest (Japan)"])
        found = self.dumps.cheat(self.identify("WIDGET2.gb", self.clone_rom))
        self.assertEqual(found.name, "Widget Quest (Japan)")
        self.assertFalse(found.via_parent)

    def test_nothing_close_enough_is_said_rather_than_guessed(self):
        self.cheat_db(["Some Other Game (USA)"])
        found = self.dumps.cheat(self.identify("WIDGET.gb", self.parent_rom))
        self.assertFalse(found)
        self.assertIn("close enough", found.problem)

    def test_an_unidentified_dump_has_nothing_to_match_on(self):
        self.cheat_db(["Widget Quest (USA)"])
        found = self.dumps.cheat(self.identify(
            "MYSTERY.gb", gb_rom(b"MYSTERY", filler=b"\x43")))
        self.assertFalse(found)
        self.assertIn("not identified", found.problem)

    def test_a_missing_database_is_reported_rather_than_raised(self):
        found = self.dumps.cheat(self.identify("WIDGET.gb", self.parent_rom))
        self.assertFalse(found)
        self.assertIn("Update", found.problem)

    def test_an_override_is_remembered_exactly_as_it_is_for_a_rom(self):
        self.cheat_db(["Widget Quest (USA)", "Widget Quest (Japan)"])
        identity = self.identify("WIDGET.gb", self.parent_rom)
        rom_path = os.path.join(self.lib.roms_dir(self.root), identity.name)
        mine = os.path.join(self.tmp.name, "mine.cht")
        self.dumps.set_cheat(rom_path, mine)
        found = self.dumps.cheat(identity, rom_path)
        self.assertEqual(found.path, mine)
        self.assertTrue(found.chosen)
        self.assertEqual(self.prefs.get_source(rom_path), mine)
        self.dumps.set_cheat(rom_path, None)
        self.assertFalse(self.dumps.cheat(identity, rom_path).chosen)


# ------------------------------------------------------------- a real card --
class RealDatTest(unittest.TestCase):
    """The user's own downloads, if they happen to be there.

    No-Intro's data is theirs and none of it is in this repository, so this
    reads what is on the machine and asserts only about the shape of the
    answer. It skips when the files are absent, which is every machine but the
    one they were downloaded on.
    """

    def zips(self) -> list[str]:
        import glob
        return sorted(glob.glob(os.path.expanduser(
            "~/Downloads/Nintendo - Game Boy*.zip")))

    def test_a_real_download_loads_and_reports_which_systems_it_covers(self):
        found = self.zips()
        if not found:
            self.skipTest("no No-Intro download on this machine")
        cat = nointro.Catalog()
        for path in found:
            cat.add(path)
        if not cat.loaded():
            self.skipTest("nothing in ~/Downloads parsed as a DAT")
        note = dumps.dat_note(cat)
        self.assertIn("Searched", note)
        # An invented hash: absent from any real DAT, and the answer must be
        # unknown rather than no data now that something is loaded.
        for system in cat.loaded():
            self.assertIs(cat.lookup(system, "0" * 40).outcome,
                          nointro.Outcome.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
