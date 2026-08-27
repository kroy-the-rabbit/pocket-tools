# SPDX-License-Identifier: GPL-3.0-or-later
"""The library: its layout, and the index that is allowed to be thrown away.

Nothing here touches a real config directory or a real library. The point of
most of these is the same one: index.json is a cache, so every way of losing it
-- absent, truncated, hand-mangled, written by another version -- has to read as
empty and rebuild, and never as an exception or as half a file.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "cheatgui"))
sys.path.insert(1, os.path.join(ROOT, "cheats"))


class Env(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp.name, "config")
        # prefs reads the config path at import time, so it and everything that
        # asks it are imported after the environment is set.
        import importlib
        import prefs
        import library_dir
        self.prefs = importlib.reload(prefs)
        self.lib = importlib.reload(library_dir)
        self.root = os.path.join(self.tmp.name, "library")

    def tearDown(self) -> None:
        if self.saved is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self.saved
        self.tmp.cleanup()

    def write(self, place: str, name: str, data: bytes) -> str:
        full = os.path.join(self.root, place, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)
        return full


# ---------------------------------------------------------------- the choice --
class ChoiceTest(Env):
    def test_there_is_no_library_until_one_is_chosen(self):
        """The app invents nothing. A default here would be a guess."""
        self.assertIsNone(self.lib.path())
        self.assertFalse(self.lib.chosen())

    def test_a_chosen_library_is_remembered(self):
        self.lib.set_path(self.root)
        self.assertEqual(self.lib.path(), self.root)
        self.assertTrue(self.lib.chosen())

    def test_the_path_is_stored_absolute(self):
        """It outlives the session, so a relative path would mean elsewhere."""
        self.prefs.set_library("~/somewhere/roms")
        stored = self.prefs.get_library()
        self.assertTrue(os.path.isabs(stored), stored)
        self.assertNotIn("~", stored)

    def test_forgetting_a_library_leaves_the_files_alone(self):
        self.lib.create(self.root)
        self.lib.set_path(self.root)
        self.lib.set_path(None)
        self.assertIsNone(self.lib.path())
        self.assertTrue(os.path.isdir(self.lib.roms_dir(self.root)))


# ---------------------------------------------------------------- the layout --
class LayoutTest(Env):
    def test_the_layout_is_the_one_the_plan_fixed(self):
        """Pinned because filing dumps into a different shape is a migration."""
        self.assertEqual(self.lib.SUBDIRS, ("roms", "cart-dumps", "saves"))
        self.assertEqual(self.lib.INDEX, "index.json")

    def test_creating_makes_every_directory(self):
        self.assertEqual(self.lib.create(self.root), self.root)
        for sub in self.lib.SUBDIRS:
            self.assertTrue(os.path.isdir(os.path.join(self.root, sub)), sub)
        self.assertTrue(self.lib.ready(self.root))

    def test_creating_twice_is_harmless(self):
        self.lib.create(self.root)
        self.write("roms", "Zelda (USA).gb", b"bytes")
        self.lib.create(self.root)
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "roms", "Zelda (USA).gb")))

    def test_a_missing_directory_means_the_library_is_not_ready(self):
        self.lib.create(self.root)
        os.rmdir(self.lib.saves_dir(self.root))
        self.assertFalse(self.lib.ready(self.root))

    def test_there_are_no_per_system_directories(self):
        """The extension names the system; the path must not say it twice."""
        self.lib.create(self.root)
        for pid in ("gb", "gbc", "gba"):
            self.assertNotIn(pid, self.lib.SUBDIRS)
            self.assertFalse(os.path.isdir(
                os.path.join(self.lib.roms_dir(self.root), pid)))

    def test_saves_is_made_and_then_left_alone(self):
        """Named now so nothing above it moves later. Nothing writes in it."""
        self.lib.create(self.root)
        self.write("roms", "Zelda (USA).gb", b"bytes")
        self.write("cart-dumps", "ZELDA.gb", b"bytes")
        self.lib.save(self.root, self.lib.rebuild(self.root))
        self.assertEqual(os.listdir(self.lib.saves_dir(self.root)), [])

    def test_the_index_lives_in_the_library_not_with_the_config(self):
        """Copying the library has to copy its index, so it travels with it."""
        self.assertEqual(self.lib.index_path(self.root),
                         os.path.join(self.root, "index.json"))
        self.assertNotIn(os.path.dirname(self.prefs.CONFIG),
                         self.lib.index_path(self.root))


# --------------------------------------------------------------- the hashing --
class HashTest(Env):
    def test_sha1_and_crc32_of_a_known_file(self):
        full = self.write("roms", "empty.gb", b"")
        sha1, crc32 = self.lib.hashes(full)
        self.assertEqual(sha1, "da39a3ee5e6b4b0d3255bfef95601890afd80709")
        self.assertEqual(crc32, "00000000")

    def test_crc32_is_eight_hex_digits_the_way_a_dat_writes_it(self):
        full = self.write("roms", "a.gb", b"a")
        _sha1, crc32 = self.lib.hashes(full)
        self.assertEqual(crc32, "e8b7be43")

    def test_a_file_longer_than_one_chunk_hashes_the_same_as_one_pass(self):
        import hashlib
        data = bytes(range(256)) * 8192            # 2 MB, so more than CHUNK
        full = self.write("roms", "big.gba", data)
        self.assertEqual(self.lib.sha1_of(full), hashlib.sha1(data).hexdigest())


# ----------------------------------------------------------------- the store --
class IndexTest(Env):
    def row(self, sha1: str = "a" * 40, **kw) -> "object":
        fields = dict(sha1=sha1, size=64, crc32="deadbeef", filed="2026-08-21")
        fields.update(kw)
        return self.lib.Row(**fields)

    def test_an_index_round_trips(self):
        self.lib.create(self.root)
        index = self.lib.Index()
        index.put(self.row(rom="Zelda (USA).gb", dump="ZELDA.gb",
                           title="Zelda (USA)", system="gb", region="USA",
                           clone_of=None))
        self.lib.save(self.root, index)

        back = self.lib.load(self.root)
        self.assertEqual(len(back), 1)
        row = back.get("a" * 40)
        self.assertEqual(row.rom, "Zelda (USA).gb")
        self.assertEqual(row.dump, "ZELDA.gb")
        self.assertEqual(row.title, "Zelda (USA)")
        self.assertEqual(row.system, "gb")
        self.assertEqual(row.region, "USA")
        self.assertEqual(row.size, 64)
        self.assertEqual(row.crc32, "deadbeef")
        self.assertEqual(row.filed, "2026-08-21")

    def test_rows_are_keyed_by_sha1_and_not_by_name(self):
        """Two cartridges both titling themselves ZELDA are two rows."""
        index = self.lib.Index()
        index.put(self.row("a" * 40, dump="ZELDA.gb"))
        index.put(self.row("b" * 40, dump="ZELDA.gb"))
        self.assertEqual(len(index), 2)
        self.assertIn("a" * 40, index)
        self.assertIn("b" * 40, index)

    def test_the_key_wins_over_a_body_that_disagrees(self):
        self.lib.create(self.root)
        with open(self.lib.index_path(self.root), "w") as f:
            json.dump({"version": self.lib.VERSION,
                       "dumps": {"a" * 40: {"sha1": "b" * 40, "size": 8}}}, f)
        self.assertEqual(self.lib.load(self.root).get("a" * 40).sha1, "a" * 40)

    def test_the_file_says_which_version_wrote_it(self):
        self.lib.create(self.root)
        self.lib.save(self.root, self.lib.Index())
        with open(self.lib.index_path(self.root)) as f:
            self.assertEqual(json.load(f)["version"], self.lib.VERSION)

    def test_only_rows_go_in(self):
        """The one way to record something new is to add a field to Row."""
        index = self.lib.Index()
        with self.assertRaises(TypeError):
            index.put({"sha1": "a" * 40})

    def test_a_decision_written_in_by_hand_does_not_survive_a_load(self):
        """Observations are disposable; decisions live in prefs. Keys we do
        not know are dropped rather than carried, so nothing can come to
        depend on one that a rebuild could never put back."""
        self.lib.create(self.root)
        with open(self.lib.index_path(self.root), "w") as f:
            json.dump({"version": self.lib.VERSION,
                       "dumps": {"a" * 40: {"size": 8, "crc32": "deadbeef",
                                            "rejected": True}}}, f)
        row = self.lib.load(self.root).get("a" * 40)
        self.assertFalse(hasattr(row, "rejected"))
        self.lib.save(self.root, self.lib.load(self.root))
        with open(self.lib.index_path(self.root)) as f:
            self.assertNotIn("rejected", json.load(f)["dumps"]["a" * 40])

    def test_dropping_a_row(self):
        index = self.lib.Index()
        index.put(self.row())
        self.assertTrue(index.drop("a" * 40))
        self.assertFalse(index.drop("a" * 40))
        self.assertEqual(len(index), 0)

    def test_a_row_names_both_of_its_files(self):
        row = self.row(rom="Zelda (USA).gb", dump="ZELDA.gb")
        self.assertEqual(row.rom_path(self.root),
                         os.path.join(self.root, "roms", "Zelda (USA).gb"))
        self.assertEqual(row.dump_path(self.root),
                         os.path.join(self.root, "cart-dumps", "ZELDA.gb"))

    def test_a_row_with_no_original_has_no_path_for_one(self):
        self.assertIsNone(self.row(rom="Zelda (USA).gb").dump_path(self.root))


# --------------------------------------------------------- writing it safely --
class AtomicTest(Env):
    def test_a_write_leaves_no_tmp_behind(self):
        self.lib.create(self.root)
        self.lib.save(self.root, self.lib.Index())
        self.assertNotIn("index.json.tmp", os.listdir(self.root))

    def test_a_crashed_write_does_not_corrupt_the_old_index(self):
        """The replace is the last step, so a failure before it changes
        nothing. Simulated by writing the temporary file and stopping."""
        self.lib.create(self.root)
        index = self.lib.Index()
        index.put(self.lib.Row(sha1="a" * 40, size=64, crc32="deadbeef",
                               filed="2026-08-21", rom="Zelda (USA).gb"))
        self.lib.save(self.root, index)

        with open(self.lib.index_path(self.root) + ".tmp", "w") as f:
            f.write('{"version": 1, "dumps": {"b')      # truncated mid-write

        back = self.lib.load(self.root)
        self.assertEqual(len(back), 1)
        self.assertEqual(back.get("a" * 40).rom, "Zelda (USA).gb")

    def test_a_leftover_tmp_is_not_mistaken_for_a_dump(self):
        self.lib.create(self.root)
        self.write("roms", "Zelda (USA).gb.tmp", b"half a file")
        self.assertEqual(len(self.lib.rebuild(self.root)), 0)


# ---------------------------------------------------------- reading it badly --
class ReadTest(Env):
    def test_a_missing_index_reads_as_empty(self):
        self.lib.create(self.root)
        index = self.lib.load(self.root)
        self.assertEqual(len(index), 0)

    def test_a_library_that_does_not_exist_reads_as_empty(self):
        self.assertEqual(len(self.lib.load("/nowhere/at/all")), 0)

    def test_an_unreadable_index_reads_as_empty(self):
        self.lib.create(self.root)
        with open(self.lib.index_path(self.root), "w") as f:
            f.write("this is not json")
        self.assertEqual(len(self.lib.load(self.root)), 0)

    def test_an_index_from_an_older_version_reads_as_empty(self):
        """Version 0 is a file written before the field existed. The rows are
        not guessed at: the library can be asked again, and a cache that
        guesses is how it starts disagreeing with what it caches."""
        self.lib.create(self.root)
        with open(self.lib.index_path(self.root), "w") as f:
            json.dump({"dumps": {"a" * 40: {"size": 8}}}, f)
        self.assertEqual(len(self.lib.load(self.root)), 0)

    def test_an_index_from_a_later_version_reads_as_empty(self):
        self.lib.create(self.root)
        with open(self.lib.index_path(self.root), "w") as f:
            json.dump({"version": self.lib.VERSION + 1,
                       "dumps": {"a" * 40: {"size": 8}}}, f)
        self.assertEqual(len(self.lib.load(self.root)), 0)

    def test_an_index_with_no_rows_reads_as_empty(self):
        self.lib.create(self.root)
        with open(self.lib.index_path(self.root), "w") as f:
            json.dump({"version": self.lib.VERSION}, f)
        self.assertEqual(len(self.lib.load(self.root)), 0)


# --------------------------------------------------------------- the rebuild --
class RebuildTest(Env):
    def file_one(self, data: bytes, rom: str, dump: str) -> None:
        """The ordinary flow's two files: the canonical copy and the original."""
        self.write("roms", rom, data)
        self.write("cart-dumps", dump, data)

    def test_deleting_the_index_loses_nothing_but_time(self):
        self.lib.create(self.root)
        self.file_one(b"zelda bytes", "Zelda (USA).gb", "ZELDA.gb")
        self.file_one(b"wario bytes", "Wario Land 3 (World).gbc", "WARIO3.gb")

        first = self.lib.rebuild(self.root)
        self.lib.save(self.root, first)
        os.remove(self.lib.index_path(self.root))

        self.assertEqual(len(self.lib.load(self.root)), 0)
        again = self.lib.rebuild(self.root)
        self.assertEqual(again.to_dict(), first.to_dict())

    def test_rebuild_from_an_empty_index_reproduces_the_rows(self):
        self.lib.create(self.root)
        self.file_one(b"zelda bytes", "Zelda (USA).gb", "ZELDA.gb")
        index = self.lib.rebuild(self.root)

        self.assertEqual(len(index), 1)
        row = next(iter(index))
        self.assertEqual(row.rom, "Zelda (USA).gb")
        self.assertEqual(row.dump, "ZELDA.gb")
        self.assertEqual(row.size, len(b"zelda bytes"))
        self.assertEqual(row.sha1, self.lib.sha1_of(
            os.path.join(self.root, "roms", "Zelda (USA).gb")))

    def test_one_dump_with_two_files_is_one_row(self):
        """Same bytes under two names is the ordinary case, not a collision."""
        self.lib.create(self.root)
        self.file_one(b"same bytes", "Zelda (USA).gb", "ZELDA.gb")
        self.assertEqual(len(self.lib.rebuild(self.root)), 1)

    def test_two_cartridges_with_the_same_name_are_two_rows(self):
        """The collision the whole feature exists for: ZELDA.gb twice, and
        only a hash can tell them apart."""
        self.lib.create(self.root)
        self.write("roms", "Zelda (USA).gb", b"link's awakening")
        self.write("roms", "Zelda DX (USA).gbc", b"link's awakening dx")
        index = self.lib.rebuild(self.root)
        self.assertEqual(len(index), 2)
        self.assertEqual(sorted(r.rom for r in index),
                         ["Zelda (USA).gb", "Zelda DX (USA).gbc"])

    def test_the_saves_directory_is_not_walked(self):
        self.lib.create(self.root)
        self.write("saves", "ZELDA/2026-08-21.sav", b"a save")
        self.assertEqual(len(self.lib.rebuild(self.root)), 0)

    def test_an_unknown_index_rebuilds_itself_on_open(self):
        self.lib.create(self.root)
        self.file_one(b"zelda bytes", "Zelda (USA).gb", "ZELDA.gb")
        with open(self.lib.index_path(self.root), "w") as f:
            f.write("{ mangled")

        index = self.lib.open_index(self.root)
        self.assertEqual(len(index), 1)
        # and it was written back, so the next run does not hash again
        self.assertEqual(self.lib.load(self.root).to_dict(), index.to_dict())

    def test_open_index_keeps_a_good_one(self):
        self.lib.create(self.root)
        index = self.lib.Index()
        index.put(self.lib.Row(sha1="a" * 40, size=1, crc32="deadbeef",
                               filed="2026-08-21", rom="Nothing On Disk.gb"))
        self.lib.save(self.root, index)
        self.assertEqual(self.lib.open_index(self.root).to_dict(),
                         index.to_dict())

    def test_enrichment_is_handed_in_rather_than_imported(self):
        """This module knows where files live and nothing about what is in
        them. The DAT lookup is prong 2's, and arrives as an argument."""
        import dataclasses
        self.lib.create(self.root)
        self.file_one(b"zelda bytes", "ZELDA.gb", "ZELDA.gb")

        def name_it(row):
            return dataclasses.replace(row, title="Zelda (USA)", system="gb")

        row = next(iter(self.lib.rebuild(self.root, name_it)))
        self.assertEqual(row.title, "Zelda (USA)")
        self.assertEqual(row.system, "gb")

    def test_enrichment_round_trips_through_the_file(self):
        import dataclasses
        self.lib.create(self.root)
        self.file_one(b"zelda bytes", "Zelda (USA).gb", "ZELDA.gb")
        index = self.lib.rebuild(self.root, lambda r: dataclasses.replace(
            r, title="Zelda (USA)", system="gb", clone_of="Zelda (World)"))
        self.lib.save(self.root, index)
        self.assertEqual(self.lib.load(self.root).to_dict(), index.to_dict())

    def test_an_empty_library_rebuilds_to_nothing(self):
        self.lib.create(self.root)
        self.assertEqual(len(self.lib.rebuild(self.root)), 0)

    def test_rebuilding_a_library_that_is_not_there_says_nothing_is(self):
        self.assertEqual(len(self.lib.rebuild("/nowhere/at/all")), 0)


if __name__ == "__main__":
    unittest.main()
