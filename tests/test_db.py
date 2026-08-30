# SPDX-License-Identifier: GPL-3.0-or-later
"""The cheat database: which copy is in use, and how its version is reported.

No network and no widgets. The version comparison is the part worth pinning:
it is easy to make it say something confident and wrong, and it did, reporting
"update available" for a checkout that was newer than the commit it was being
compared against.
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

import db                                                    # noqa: E402


def total(per_dir: int) -> int:
    """How many files populate() writes. Derived, so adding a system to
    db.DIRS does not mean editing a pile of hardcoded counts."""
    return per_dir * len(db.DIRS)


def populate(cht: str, per_dir: int = 3) -> None:
    for d in db.DIRS:
        full = os.path.join(cht, d)
        os.makedirs(full, exist_ok=True)
        for i in range(per_dir):
            with open(os.path.join(full, f"Game {i}.cht"), "w") as f:
                f.write('cheat0_code = "010CAAC6"\n')


class Env(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {k: os.environ.get(k)
                      for k in ("XDG_DATA_HOME", "POCKET_CHEAT_DB")}
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp.name, "data")
        os.environ.pop("POCKET_CHEAT_DB", None)
        # The developer's own checkout has a populated submodule, and db_dir()
        # would fall back to it and quietly make "no database" tests pass for
        # the wrong reason. Point it somewhere that does not exist; the
        # fallback itself is tested in SubmoduleFallback.
        self.submodule = db.SUBMODULE
        db.SUBMODULE = os.path.join(self.tmp.name, "no-submodule", "cht")

    def tearDown(self) -> None:
        db.SUBMODULE = self.submodule
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()


class WhichCopy(Env):
    def test_nothing_fetched(self):
        self.assertFalse(db.available())
        self.assertIsNone(db.local_state())
        self.assertEqual(db.db_dir(), db.store_cht())

    def test_the_fetched_copy_is_used(self):
        populate(db.store_cht())
        self.assertTrue(db.available())
        self.assertEqual(db.db_dir(), db.store_cht())
        self.assertEqual(db.count_files(), total(3))

    def test_an_empty_directory_is_not_a_database(self):
        """A registered but un-checked-out submodule is an empty directory."""
        for d in db.DIRS:
            os.makedirs(os.path.join(db.store_cht(), d), exist_ok=True)
        self.assertFalse(db.available())

    def test_the_override_wins(self):
        populate(db.store_cht())
        other = os.path.join(self.tmp.name, "elsewhere")
        populate(other, per_dir=1)
        os.environ["POCKET_CHEAT_DB"] = other
        self.assertEqual(db.db_dir(), other)
        self.assertEqual(db.count_files(), total(1))


class SubmoduleFallback(Env):
    def test_used_when_nothing_has_been_fetched(self):
        db.SUBMODULE = os.path.join(self.tmp.name, "checkout", "cht")
        populate(db.SUBMODULE, per_dir=4)
        self.assertTrue(db.available())
        self.assertEqual(db.db_dir(), db.SUBMODULE)
        self.assertEqual(db.count_files(), total(4))

    def test_the_fetched_copy_beats_it(self):
        """Update maintains the fetched copy, so that is the one it must use."""
        db.SUBMODULE = os.path.join(self.tmp.name, "checkout", "cht")
        populate(db.SUBMODULE, per_dir=4)
        populate(db.store_cht(), per_dir=1)
        self.assertEqual(db.db_dir(), db.store_cht())
        self.assertEqual(db.count_files(), total(1))


class Versions(Env):
    REMOTE = {"sha": "b" * 40, "date": "2026-08-01T20:07:21Z"}

    def state(self, **kw) -> dict:
        populate(db.store_cht())
        st = {"sha": "a" * 40, "date": "2026-03-14T00:00:00Z",
              "fetched": "2026-03-15T00:00:00Z", "files": total(3),
              "source": "fetched", "comparable": True}
        st.update(kw)
        os.makedirs(db.store(), exist_ok=True)
        with open(db.state_file(), "w") as f:
            json.dump(st, f)
        return db.local_state()

    def test_behind(self):
        local = self.state()
        self.assertFalse(db.up_to_date(local, self.REMOTE))
        self.assertIn("update available: 2026-08-01",
                      db.describe(local, self.REMOTE))

    def test_current(self):
        local = self.state(sha=self.REMOTE["sha"], date=self.REMOTE["date"])
        self.assertTrue(db.up_to_date(local, self.REMOTE))
        self.assertIn("up to date", db.describe(local, self.REMOTE))

    def test_an_uncomparable_version_claims_nothing(self):
        """A checkout that cannot answer the question upstream was asked.

        A shallow submodule is the real case: in one, every path looks as
        though HEAD introduced it, so the newest commit "touching" the cheat
        directories is just the repository head. Saying "update available" on
        that is worse than saying nothing, because the checkout may well be
        newer than the commit it is being compared against, which is exactly
        what it reported before.
        """
        db.SUBMODULE = os.path.join(self.tmp.name, "checkout", "cht")
        populate(db.SUBMODULE)                   # not a git repository at all
        local = db.local_state()
        self.assertEqual(local["source"], "submodule")
        self.assertFalse(local["comparable"])
        self.assertFalse(db.up_to_date(local, self.REMOTE))
        text = db.describe(local, self.REMOTE)
        self.assertNotIn("update available", text)
        self.assertNotIn("up to date", text)
        self.assertIn("upstream: 2026-08-01", text)

    def test_a_fetched_copy_is_always_comparable(self):
        """Its sha is the one we downloaded from, so it means the same thing."""
        local = self.state(comparable=False)     # a lie in the file
        self.assertTrue(local["comparable"])     # and it is not believed

    def test_a_copy_missing_a_system_is_never_current(self):
        """Fetched before a system existed, so it holds nothing for it.

        Its recorded commit says nothing about that, and if the newest commit
        upstream happened to be one it already had, a sha comparison alone
        would call it current and that system would stay empty forever.
        """
        local = self.state(sha=self.REMOTE["sha"], date=self.REMOTE["date"])
        self.assertTrue(db.up_to_date(local, self.REMOTE))   # while complete

        import shutil
        shutil.rmtree(os.path.join(db.store_cht(), db.DIRS[-1]))
        local = db.local_state()
        self.assertEqual(local["missing"], [db.DIRS[-1]])
        self.assertFalse(db.up_to_date(local, self.REMOTE))
        text = db.describe(local, self.REMOTE)
        self.assertIn("nothing for", text)
        self.assertIn("press Update", text)
        self.assertNotIn("up to date", text)

    def test_an_empty_directory_counts_as_missing(self):
        local = self.state()
        import shutil
        gone = os.path.join(db.store_cht(), db.DIRS[-1])
        shutil.rmtree(gone)
        os.makedirs(gone)
        self.assertEqual(db.local_state()["missing"], [db.DIRS[-1]])

    def test_a_complete_copy_reports_nothing_missing(self):
        self.assertEqual(self.state()["missing"], [])

    def test_no_database_at_all(self):
        self.assertIn("not fetched yet", db.describe(None, self.REMOTE))

    def test_a_missing_state_file_is_not_a_crash(self):
        populate(db.store_cht())
        local = db.local_state()
        self.assertEqual(local["files"], total(3))
        self.assertFalse(local["comparable"])
        self.assertIn("unknown version", db.describe(local))


class Swap(Env):
    def test_swap_replaces_the_whole_directory(self):
        dest = db.store_cht()
        populate(dest, per_dir=5)
        new = os.path.join(self.tmp.name, "incoming")
        populate(new, per_dir=2)
        db._swap(new, dest)
        self.assertEqual(db.count_files(dest), total(2))
        self.assertFalse(os.path.exists(new))
        self.assertFalse(os.path.exists(dest + ".old"))

    def test_swap_into_nothing(self):
        new = os.path.join(self.tmp.name, "incoming")
        populate(new, per_dir=2)
        db._swap(new, db.store_cht())
        self.assertEqual(db.count_files(), total(2))


if __name__ == "__main__":
    unittest.main()
