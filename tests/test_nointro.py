# SPDX-License-Identifier: GPL-3.0-or-later
"""No-Intro DATs: the two flavours, the three outcomes, and the named refusal.

Every fixture here is synthetic XML written for these tests. No-Intro's data is
theirs and is not redistributed, so nothing in the repository is an excerpt of
it; the shapes below are the schema, with invented games in it. The two tests
that read a real download are skipped unless those downloads happen to be on
the machine, and assert nothing that would put their contents in this file.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "cheatgui"))
sys.path.insert(1, os.path.join(ROOT, "cheats"))

import nointro                                               # noqa: E402

# Three invented games, one of them a clone of another, expressed twice: once
# the way the Standard DAT says it and once the way Parent-Clone does. Same
# graph, and the point of the pair is that the parser must not care which
# arrived. The hashes are made up and never computed from anything.
STANDARD = """<?xml version="1.0"?>
<datafile>
\t<header>
\t\t<name>Nintendo - Game Boy</name>
\t\t<version>20260827-092427</version>
\t</header>
\t<game name="Widget Quest (World)" id="0001">
\t\t<description>Widget Quest (World)</description>
\t\t<rom name="Widget Quest (World).gb" size="131072" crc="0a0b0c0d"\
 md5="00000000000000000000000000000001"\
 sha1="1111111111111111111111111111111111111111" status="verified"/>
\t</game>
\t<game name="Widget Quest (Japan)" id="0002" cloneofid="0001">
\t\t<description>Widget Quest (Japan)</description>
\t\t<rom name="Widget Quest (Japan).gb" size="131072" crc="1a1b1c1d"\
 sha1="2222222222222222222222222222222222222222"/>
\t</game>
\t<game name="[BIOS] Sprocket Boot ROM (World)" id="0003">
\t\t<description>[BIOS] Sprocket Boot ROM (World)</description>
\t\t<rom name="[BIOS] Sprocket Boot ROM (World).bin" size="256" crc="2a2b2c2d"\
 sha1="3333333333333333333333333333333333333333"/>
\t</game>
</datafile>
"""

PARENT_CLONE = """<?xml version="1.0"?>
<datafile>
\t<header>
\t\t<name>Nintendo - Game Boy (Parent-Clone)</name>
\t\t<version>20260827-092427</version>
\t</header>
\t<game name="Widget Quest (World)">
\t\t<description>Widget Quest (World)</description>
\t\t<release name="Widget Quest (World)" region="EUR"/>
\t\t<release name="Widget Quest (World)" region="USA"/>
\t\t<rom name="Widget Quest (World).gb" size="131072" crc="0a0b0c0d"\
 md5="00000000000000000000000000000001"\
 sha1="1111111111111111111111111111111111111111" status="verified"/>
\t</game>
\t<game name="Widget Quest (Japan)" cloneof="Widget Quest (World)">
\t\t<description>Widget Quest (Japan)</description>
\t\t<rom name="Widget Quest (Japan).gb" size="131072" crc="1a1b1c1d"\
 sha1="2222222222222222222222222222222222222222"/>
\t</game>
\t<game name="[BIOS] Sprocket Boot ROM (World)">
\t\t<description>[BIOS] Sprocket Boot ROM (World)</description>
\t\t<rom name="[BIOS] Sprocket Boot ROM (World).bin" size="256" crc="2a2b2c2d"\
 sha1="3333333333333333333333333333333333333333"/>
\t</game>
</datafile>
"""

# A clone whose parent is not in the file. Two real Game Boy Advance entries
# do this, so it is a case the parser meets rather than one invented for it.
DANGLING = """<?xml version="1.0"?>
<datafile>
\t<header><name>Nintendo - Game Boy Advance</name></header>
\t<game name="Widget Quest Advance (Japan)" id="x001" cloneofid="9999">
\t\t<rom name="Widget Quest Advance (Japan).gba" size="4194304" crc="3a3b3c3d"\
 sha1="4444444444444444444444444444444444444444"/>
\t</game>
</datafile>
"""

# The third button on the same page, which is not a DAT and never parses. Two
# top-level elements is the whole of what is wrong with it: <header> closes,
# and the <datafile> that follows it is junk after the document element. The
# shape is No-Intro's; the game in it is invented, like every other here.
DB_EXPORT = """<?xml version="1.0" encoding="utf-8"?>
<header>
\t<version>20260827-092427</version>
\t<author>No-Intro</author>
</header>
<datafile>
\t<game name="Widget Quest (World)">
\t\t<archive number="1" clone="P" regparent="(WORLD PARENT)"\
 name="Widget Quest" region="World" languages="En" devstatus="Release"/>
\t\t<source>
\t\t\t<file forcename="Widget Quest (World).gb" size="131072"\
 crc="0a0b0c0d" sha1="1111111111111111111111111111111111111111"/>
\t\t</source>
\t</game>
</datafile>
"""

PARENT = "1111111111111111111111111111111111111111"
CLONE = "2222222222222222222222222222222222222222"
BOOTROM = "3333333333333333333333333333333333333333"


class DatFile:
    """A fixture on disk, as a zip or as the bare XML the user extracted."""

    def __init__(self, tmp: str, name: str, xml: str, zipped: bool = False,
                 ext: str = ".dat"):
        # The extension is a parameter because it is half of what tells a DB
        # Export apart: its zip holds an .xml where every DAT holds a .dat.
        self.path = os.path.join(tmp, name + (".zip" if zipped else ext))
        if zipped:
            with zipfile.ZipFile(self.path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(name + ext, xml)
        else:
            with open(self.path, "w") as f:
                f.write(xml)


class NointroTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def dat(self, xml: str, name: str = "Nintendo - Game Boy",
            zipped: bool = False, ext: str = ".dat") -> nointro.Dat:
        return nointro.load(
            DatFile(self.tmp.name, name, xml, zipped, ext).path)

    # ------------------------------------------------------------ flavours --
    def test_both_flavours_give_the_same_entries(self):
        standard = self.dat(STANDARD)
        parent_clone = self.dat(PARENT_CLONE, "Nintendo - Game Boy (PC)")
        self.assertEqual(standard.entries, parent_clone.entries)

    def test_the_flavour_is_recognised_from_the_games(self):
        self.assertEqual(self.dat(STANDARD).flavour, nointro.STANDARD)
        self.assertEqual(self.dat(PARENT_CLONE).flavour, nointro.PARENT_CLONE)

    def test_a_numeric_clone_link_resolves_to_the_parents_name(self):
        entries = self.dat(STANDARD).entries
        self.assertEqual(entries[CLONE].parent, "Widget Quest (World)")
        self.assertIsNone(entries[PARENT].parent)

    def test_a_named_clone_link_is_taken_as_it_stands(self):
        entries = self.dat(PARENT_CLONE).entries
        self.assertEqual(entries[CLONE].parent, "Widget Quest (World)")
        self.assertIsNone(entries[PARENT].parent)

    def test_a_clone_of_an_absent_parent_keeps_its_entry(self):
        dat = self.dat(DANGLING, "Nintendo - Game Boy Advance")
        entry = dat.entries["4444444444444444444444444444444444444444"]
        self.assertIsNone(entry.parent)
        self.assertEqual(entry.name, "Widget Quest Advance (Japan).gba")

    # ---------------------------------------------------------- the file in --
    def test_the_zip_is_read_as_downloaded(self):
        zipped = self.dat(STANDARD, zipped=True)
        self.assertEqual(len(zipped), 3)
        self.assertEqual(zipped.entries, self.dat(STANDARD).entries)

    def test_the_header_says_which_system_it_covers(self):
        self.assertEqual(self.dat(STANDARD).system, "gb")
        self.assertEqual(self.dat(PARENT_CLONE).system, "gb")
        self.assertEqual(self.dat(DANGLING, "gba").system, "gba")
        self.assertEqual(self.dat(STANDARD).version, "20260827-092427")

    def test_game_boy_color_is_not_read_as_game_boy(self):
        # "Nintendo - Game Boy Color" starts with "Nintendo - Game Boy", so a
        # first-match test would file every Color download under gb.
        self.assertEqual(nointro.system_for("Nintendo - Game Boy Color"), "gbc")
        self.assertEqual(
            nointro.system_for("Nintendo - Game Boy (Parent-Clone)"), "gb")
        self.assertEqual(nointro.system_for("Sega - Mega Drive"), "")

    def test_the_dat_filename_is_used_verbatim(self):
        # A boot ROM in the Game Boy Advance DAT ends .bin, and rebuilding the
        # name from the game plus the system's extension would rename it.
        entry = self.dat(STANDARD).entries[BOOTROM]
        self.assertEqual(entry.name, "[BIOS] Sprocket Boot ROM (World).bin")
        self.assertEqual(entry.game, "[BIOS] Sprocket Boot ROM (World)")

    # ------------------------------------------------------------ lookups ---
    def test_the_three_outcomes_are_told_apart(self):
        dat = self.dat(STANDARD)
        found = dat.lookup(PARENT, 131072, "0a0b0c0d")
        self.assertIs(found.outcome, nointro.Outcome.MATCH)
        self.assertEqual(found.name, "Widget Quest (World).gb")

        missing = dat.lookup("f" * 40, 131072, "0a0b0c0d")
        self.assertIs(missing.outcome, nointro.Outcome.UNKNOWN)
        self.assertIsNone(missing.entry)
        self.assertIsNone(missing.name)

        wrong = dat.lookup(PARENT, 999, "deadbeef")
        self.assertIs(wrong.outcome, nointro.Outcome.MISMATCH)
        self.assertEqual(wrong.disagreed, ("size", "crc32"))
        # The entry comes back anyway: what is worth saying is which game the
        # file nearly is.
        self.assertEqual(wrong.entry.game, "Widget Quest (World)")
        self.assertIsNone(wrong.name)

    def test_either_field_alone_is_a_mismatch(self):
        dat = self.dat(STANDARD)
        self.assertEqual(dat.lookup(PARENT, 999, "0a0b0c0d").disagreed, ("size",))
        self.assertEqual(dat.lookup(PARENT, 131072, "ffffffff").disagreed,
                         ("crc32",))

    def test_size_and_crc_are_optional(self):
        self.assertIs(self.dat(STANDARD).lookup(PARENT).outcome,
                      nointro.Outcome.MATCH)

    def test_case_does_not_make_a_mismatch(self):
        # The real files are lowercase throughout, but other tools that write
        # this schema are not.
        found = self.dat(STANDARD).lookup(PARENT.upper(), 131072, "0A0B0C0D")
        self.assertIs(found.outcome, nointro.Outcome.MATCH)

    # ------------------------------------------------------ what is loaded --
    def test_nothing_loaded_is_a_normal_state(self):
        catalog = nointro.Catalog()
        self.assertEqual(catalog.loaded(), ())
        self.assertEqual(catalog.missing(), ("gb", "gbc", "gba"))
        self.assertFalse(catalog.get("gbc"))
        self.assertEqual(len(catalog.get("gbc")), 0)

    def test_one_system_loaded_leaves_the_others_expressible(self):
        catalog = nointro.Catalog()
        self.assertEqual(catalog.add(DatFile(self.tmp.name, "Nintendo - Game Boy",
                                             STANDARD, True).path), "gb")
        self.assertEqual(catalog.loaded(), ("gb",))
        self.assertEqual(catalog.missing(), ("gbc", "gba"))

    def test_a_system_with_no_dat_is_not_an_unknown_dump(self):
        # Reporting NO_DATA as UNKNOWN would blame the cartridge for a
        # download the user has not made.
        catalog = nointro.Catalog()
        catalog.add(DatFile(self.tmp.name, "Nintendo - Game Boy",
                            STANDARD, True).path)
        self.assertIs(catalog.lookup("gbc", PARENT).outcome,
                      nointro.Outcome.NO_DATA)
        self.assertIs(catalog.lookup("gb", "f" * 40).outcome,
                      nointro.Outcome.UNKNOWN)
        self.assertIs(catalog.lookup("gb", PARENT).outcome,
                      nointro.Outcome.MATCH)

    def test_a_file_for_an_unknown_system_is_refused(self):
        catalog = nointro.Catalog()
        xml = STANDARD.replace("Nintendo - Game Boy", "Sega - Mega Drive")
        self.assertIsNone(catalog.add(DatFile(self.tmp.name, "md", xml).path))
        self.assertEqual(catalog.loaded(), ())
        # Unless the caller says what it is.
        self.assertEqual(
            catalog.add(DatFile(self.tmp.name, "md", xml).path, "gb"), "gb")

    # ------------------------------------------------- nothing raises here --
    def test_a_missing_file_is_an_empty_dat(self):
        dat = nointro.load(os.path.join(self.tmp.name, "never-downloaded.zip"))
        self.assertFalse(dat)
        self.assertEqual(len(dat), 0)
        self.assertIs(dat.lookup(PARENT).outcome, nointro.Outcome.UNKNOWN)

    def test_a_corrupt_file_is_an_empty_dat(self):
        for name, blob in (("truncated.dat", STANDARD[:200]),
                           ("notxml.dat", "this is not a DAT at all"),
                           ("empty.dat", ""),
                           ("wrongroot.dat", "<mame><game/></mame>")):
            with self.subTest(name):
                path = os.path.join(self.tmp.name, name)
                with open(path, "w") as f:
                    f.write(blob)
                self.assertFalse(nointro.load(path))

    def test_a_zip_that_is_not_a_zip_is_an_empty_dat(self):
        path = os.path.join(self.tmp.name, "half.zip")
        with open(path, "wb") as f:
            f.write(b"PK\x03\x04truncated")
        self.assertFalse(nointro.load(path))

    def test_a_directory_is_an_empty_dat(self):
        self.assertFalse(nointro.load(self.tmp.name))

    def test_an_external_entity_is_not_fetched(self):
        # Untrusted input: this is a download. ElementTree resolves no
        # external entity, so the file is refused rather than read.
        path = os.path.join(self.tmp.name, "xxe.dat")
        with open(path, "w") as f:
            f.write('<?xml version="1.0"?><!DOCTYPE d ['
                    '<!ENTITY x SYSTEM "file:///etc/passwd">]>'
                    "<datafile><game name='&x;'/></datafile>")
        self.assertFalse(nointro.load(path))

    def test_an_oversized_member_is_refused_before_it_is_read(self):
        path = os.path.join(self.tmp.name, "bomb.zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("bomb.dat", b"\0" * (nointro.MAX_DAT + 1))
        self.assertFalse(nointro.load(path))

    def test_a_game_without_a_usable_hash_is_skipped(self):
        xml = STANDARD.replace(
            'sha1="2222222222222222222222222222222222222222"', 'sha1=""')
        dat = self.dat(xml)
        self.assertEqual(len(dat), 2)
        self.assertIn(PARENT, dat.entries)

    # ---------------------------------------------------- the third button --
    def test_the_db_export_is_named_instead_of_being_nothing(self):
        # As downloaded: a zip holding one .xml, which does not parse. Both
        # marks are present, and the point is that the app can now say which
        # of the three buttons was pressed rather than "no Game Boy data".
        dat = self.dat(DB_EXPORT, zipped=True, ext=".xml")
        self.assertFalse(dat)
        self.assertIs(dat.problem, nointro.Problem.DB_EXPORT)

    def test_the_db_export_is_still_named_once_it_is_unpacked(self):
        # Extracted by hand, or rezipped under a .dat name by somebody's
        # tooling. The zip is gone and so is the extension, so this is the
        # header check on its own, which is the one that has to hold.
        for name, kwargs in (("extracted .xml", {"ext": ".xml"}),
                             ("renamed .dat", {}),
                             ("rezipped .dat", {"zipped": True})):
            with self.subTest(name):
                dat = self.dat(DB_EXPORT, **kwargs)
                self.assertFalse(dat)
                self.assertIs(dat.problem, nointro.Problem.DB_EXPORT)

    def test_a_broken_file_is_not_blamed_on_the_wrong_button(self):
        # None of these is a DB Export, and telling their owner to go and
        # fetch a different flavour would send them to the wrong place. A
        # truncated file and a doubled one both fail to parse; the second
        # fails with the same expat error the DB Export does, and is still
        # not one, because its document opens with <datafile>.
        for name, xml in (("truncated", STANDARD[:200]),
                          ("not xml at all", "this is not a DAT at all"),
                          ("empty", ""),
                          ("two datafiles", STANDARD + STANDARD)):
            with self.subTest(name):
                dat = self.dat(xml)
                self.assertFalse(dat)
                self.assertIs(dat.problem, nointro.Problem.DAMAGED)

    def test_a_dat_zipped_as_xml_is_still_a_dat(self):
        # The extension is a hint, not a verdict. A DAT that parses is loaded
        # whatever its member was called, exactly as before.
        dat = self.dat(STANDARD, zipped=True, ext=".xml")
        self.assertEqual(len(dat), 3)
        self.assertIs(dat.problem, nointro.Problem.NONE)

    # ------------------------------------------------ why a load was empty --
    def test_a_load_that_worked_has_nothing_to_explain(self):
        self.assertIs(self.dat(STANDARD).problem, nointro.Problem.NONE)
        self.assertIs(nointro.Dat().problem, nointro.Problem.NONE)
        self.assertIs(nointro.Catalog().get("gbc").problem,
                      nointro.Problem.NONE)

    def test_the_reasons_a_load_is_empty_are_told_apart(self):
        gone = os.path.join(self.tmp.name, "never-downloaded.zip")
        self.assertIs(nointro.load(gone).problem, nointro.Problem.MISSING)
        self.assertIs(self.dat("<mame><game/></mame>").problem,
                      nointro.Problem.NOT_A_DAT)
        self.assertIs(self.dat(DB_EXPORT).problem, nointro.Problem.DB_EXPORT)
        self.assertIs(self.dat("nonsense").problem, nointro.Problem.DAMAGED)

    def test_a_reason_does_not_change_what_an_empty_dat_is(self):
        # The contract the rest of the app leans on: still falsy, still
        # empty, still not an exception, and a lookup still says UNKNOWN.
        dat = self.dat(DB_EXPORT, zipped=True, ext=".xml")
        self.assertFalse(dat)
        self.assertEqual(len(dat), 0)
        self.assertEqual(dat.entries, {})
        self.assertIs(dat.lookup(PARENT).outcome, nointro.Outcome.UNKNOWN)

    def test_a_refusal_keeps_its_reason_on_the_way_through_the_catalog(self):
        catalog = nointro.Catalog()
        path = DatFile(self.tmp.name, "Nintendo - Game Boy",
                       DB_EXPORT, True, ".xml").path
        # add() answers the only question it ever answered.
        self.assertIsNone(catalog.add(path))
        self.assertEqual(catalog.loaded(), ())
        # take() is the same load, for the caller that has to explain itself.
        dat = catalog.take(path)
        self.assertFalse(dat)
        self.assertIs(dat.problem, nointro.Problem.DB_EXPORT)

    def test_a_dat_for_another_console_is_refused_as_that(self):
        # It parsed and it is somebody's real DAT; it is only not ours, and
        # saying "damaged" about it would be a lie.
        catalog = nointro.Catalog()
        xml = STANDARD.replace("Nintendo - Game Boy", "Sega - Mega Drive")
        dat = catalog.take(DatFile(self.tmp.name, "md", xml).path)
        self.assertFalse(dat)
        self.assertIs(dat.problem, nointro.Problem.WRONG_SYSTEM)
        self.assertEqual(catalog.loaded(), ())
        taken = catalog.take(DatFile(self.tmp.name, "md", xml).path, "gb")
        self.assertTrue(taken)
        self.assertIs(taken.problem, nointro.Problem.NONE)
        self.assertEqual(catalog.loaded(), ("gb",))

    # ------------------------------------------------------------- hashing --
    def test_one_pass_over_a_dump_gives_what_a_lookup_wants(self):
        path = os.path.join(self.tmp.name, "DUMP.gb")
        with open(path, "wb") as f:
            f.write(b"cartridge bytes" * 1000)
        got = nointro.digest(path)
        self.assertEqual(got.size, 15000)
        self.assertEqual(got.sha1, "76d8b92f073eef78a15c5eb90371699f23a3f201")
        self.assertEqual(got.crc32, "21edaa90")

    def test_hashing_something_unreadable_gives_nothing(self):
        self.assertIsNone(nointro.digest(
            os.path.join(self.tmp.name, "not-there.gb")))

    def test_a_dump_identifies_itself_end_to_end(self):
        path = os.path.join(self.tmp.name, "WIDGET.gb")
        with open(path, "wb") as f:
            f.write(b"cartridge bytes" * 1000)
        got = nointro.digest(path)
        xml = STANDARD.replace(
            'name="Widget Quest (World).gb" size="131072" crc="0a0b0c0d"',
            'name="Widget Quest (World).gb" size="%d" crc="%s"'
            % (got.size, got.crc32)).replace(PARENT, got.sha1)
        found = self.dat(xml).lookup(got.sha1, got.size, got.crc32)
        self.assertIs(found.outcome, nointro.Outcome.MATCH)
        self.assertEqual(found.name, "Widget Quest (World).gb")


# The user's own downloads, if they are there. This is the check that the
# fixtures above are the right shape; it must never become a requirement,
# because a checkout has no right to No-Intro's files and most machines
# running these tests will not have them.
DOWNLOADS = os.path.expanduser("~/Downloads")
REAL = [n for n in (sorted(os.listdir(DOWNLOADS)) if os.path.isdir(DOWNLOADS)
                    else [])
        if n.startswith("Nintendo - Game Boy") and n.endswith(".zip")
        and "DB Export" not in n]
DB_EXPORTS = [n for n in (sorted(os.listdir(DOWNLOADS))
                          if os.path.isdir(DOWNLOADS) else [])
              if n.startswith("Nintendo - ") and n.endswith(".zip")
              and "DB Export" in n]


@unittest.skipUnless(REAL, "no No-Intro downloads on this machine")
class RealDownloadTest(unittest.TestCase):
    def test_the_downloads_parse_into_a_catalog(self):
        catalog = nointro.Catalog()
        for name in REAL:
            system = catalog.add(os.path.join(DOWNLOADS, name))
            self.assertIn(system, nointro.SYSTEMS, name)
            dat = catalog.get(system)
            self.assertGreater(len(dat), 1000)
            self.assertIn(dat.flavour, (nointro.STANDARD, nointro.PARENT_CLONE))
            # SHA-1 is the key precisely because it is the one hash that is
            # always there; a gap would silently shrink the index.
            self.assertTrue(all(len(k) == 40 for k in dat.entries))
            self.assertTrue(all(e.size and e.crc32 for e in dat.entries.values()))
            self.assertIs(dat.problem, nointro.Problem.NONE)


@unittest.skipUnless(DB_EXPORTS, "no No-Intro DB Export on this machine")
class RealDbExportTest(unittest.TestCase):
    def test_the_real_db_export_is_recognised_as_one(self):
        # The fixture above is a shape written from the plan. This is the
        # check that the shape is the one the site actually hands out.
        for name in DB_EXPORTS:
            with self.subTest(name):
                dat = nointro.load(os.path.join(DOWNLOADS, name))
                self.assertFalse(dat)
                self.assertIs(dat.problem, nointro.Problem.DB_EXPORT)


if __name__ == "__main__":
    unittest.main()
