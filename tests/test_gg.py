# SPDX-License-Identifier: GPL-3.0-or-later
"""Game Gear cheat codes: both kinds, the skip rules, and writing back.

The decoding is the core's own gg2bin and ggcht, so these check that gg.py
splits a file the way gg2bin.model does and that a written file reads back as
the same cheats.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
import cheatlib                                              # noqa: E402
import db                                                    # noqa: E402
import model                                                 # noqa: E402
import prefs                                                 # noqa: E402

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

    def test_all_sega_platforms_use_the_reference_decoder(self):
        for pid in ("gg", "sms", "sg1000"):
            with self.subTest(platform=pid):
                groups = cheatfile.parse(LIB, pid)
                self.assertEqual(groups, gg.parse(LIB))
                self.assertTrue(cheatfile.decoded(pid))
                self.assertEqual(cheatfile.mechanisms(pid), ("poke", "patch"))
                self.assertEqual(cheatfile.limits(pid), (32, 32))
                self.assertEqual(cheatfile.applied_by(groups[0].codes[0], pid),
                                 "poke")
                self.assertEqual(cheatfile.applied_by(groups[1].codes[0], pid),
                                 "patch")
                self.assertEqual(cheatfile.parse(LIB, pid, max_groups=1),
                                 groups[:1])
                self.assertEqual(groups[3].codes, [])
                self.assertIsNone(writer.compiled_path("/x/Game.cht", pid))

    def test_sega_capacity_is_shared_between_pokes_and_patches(self):
        poke, genie = gg.parse(LIB)[:2]
        for pid in ("gg", "sms", "sg1000"):
            with self.subTest(platform=pid):
                self.assertEqual(writer.check([poke, genie] * 16, pid), [])
                self.assertIn("33 codes selected, the core stores 32",
                              writer.check([poke, genie] * 16 + [poke], pid))


class SegaGames(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for target, attr, value in (
                (cheatlib, "LOCAL", str(self.root / "local")),
                (db, "db_dir", lambda: str(self.root / "database")),
                (prefs, "CONFIG", str(self.root / "prefs.json"))):
            patcher = patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        cheatlib.refresh()
        self.addCleanup(cheatlib.refresh)

    def put(self, path, data=b"ROM"):
        full = self.root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)
        return str(full)

    def test_scan_separates_platforms_without_requiring_a_corpus(self):
        for pid, ext in (("gg", "gg"), ("sms", "sms"), ("sg1000", "sg")):
            base = f"Assets/{pid}/common/nested"
            self.put(f"{base}/Game.{ext}")
            self.put(f"{base}/Other.{ext.upper()}")
            self.put(f"{base}/Game.{ext}.cht", LIB)
            self.put(f"{base}/ignore.sav")
            self.put(f"{base}/wrong.gba")
        c = card.Card(str(self.root))
        platforms = {p.id: c.fill(p) for p in c.platforms()}
        self.assertEqual(set(platforms), {"gg", "sms", "sg1000"})
        for pid, plat in platforms.items():
            with self.subTest(platform=pid):
                self.assertEqual([g.name for g in plat.games], ["Game", "Other"])
                self.assertTrue(all(g.platform == pid for g in plat.games))
                self.assertTrue(plat.has_cheats(plat.games[0]))
                self.assertFalse(plat.has_cheats(plat.games[1]))
                self.assertEqual(plat.name, card.DISPLAY[pid])
        self.assertNotIn("sg1000", card.SUPPORTED)
        self.assertNotIn("", db.DIRS)

    def test_sms_and_gg_search_only_their_own_database(self):
        paths = {}
        for pid in ("sms", "gg"):
            paths[pid] = self.put(f"database/{card.SUPPORTED[pid]}/Game.cht", LIB)
        for pid, path in paths.items():
            self.assertEqual(cheatlib.files_for(pid), (path,))
        self.assertEqual(cheatlib.files_for("sg1000"), ())

    def test_sg1000_can_open_without_any_database(self):
        game = card.Game(self.put("Assets/sg1000/common/Flicky.sg"), "sg1000")
        view = model.load(game)
        self.assertIsNone(view.source)
        self.assertEqual(view.entries, [])
        self.assertEqual(view.alternates, [])

    def test_sg1000_can_find_local_cheats_without_a_database(self):
        game = card.Game(self.put("Assets/sg1000/common/Flicky.sg"), "sg1000")
        source = self.put("local/Flicky.cht", LIB)
        view = model.load(game)
        self.assertEqual(view.source, source)
        self.assertEqual(len(view.entries), 7)
        self.assertEqual(view.enabled, [])

    def test_sg1000_explicit_and_installed_cheats_survive_reload(self):
        game = card.Game(self.put("Assets/sg1000/common/Flicky.sg"), "sg1000")
        source = self.put("outside/Chosen.cht", LIB)
        view = model.load(game, source=source)
        view.entries[0].enabled = True
        view.entries[1].enabled = True
        self.assertEqual(view.applied_counts, (1, 1))
        save = Path(self.put("Saves/sg1000/common/Flicky.sav", b"keep save"))
        self.assertEqual(view.save(), (2, 2, False))
        installed = model.load(game)
        self.assertIsNone(installed.source)
        self.assertEqual([e.desc for e in installed.enabled],
                         ["Infinite Lives", "Genie joined with plus"])
        self.assertTrue(all(not e.in_library for e in installed.entries))
        self.assertEqual(installed.applied_counts, (1, 1))
        self.assertEqual(save.read_bytes(), b"keep save")
        self.assertEqual(Path(game.path).read_bytes(), b"ROM")
        self.assertFalse(Path(game.path + ".chtbin").exists())


class WriteBack(unittest.TestCase):
    def test_rendered_file_reads_back_as_the_same_cheats(self):
        picked = [g for g in gg.parse(LIB) if g.codes]
        back = cheatfile.parse(writer.render(picked).encode(), "gg")
        self.assertEqual([writer.key_of(g) for g in back],
                         [writer.key_of(g) for g in picked])
        self.assertTrue(all(g.enabled for g in back))


if __name__ == "__main__":
    unittest.main()
