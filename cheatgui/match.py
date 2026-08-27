# SPDX-License-Identifier: GPL-3.0-or-later
"""Match a ROM filename to its entry in the libretro cheat database.

Names differ in region tags, revision markers and punctuation, so compare a
normalized form and rank by similarity. The caller can always override, and an
override is remembered (see prefs.py).
"""
from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass
from functools import lru_cache

import cheatlib

# Tags that say nothing about which game this is.
_PAREN = re.compile(r"\([^)]*\)|\[[^]]*\]")
_JUNK = re.compile(r"[^a-z0-9]+")


def _stem(name: str) -> str:
    name = os.path.splitext(os.path.basename(name))[0]
    if name.endswith(".gb") or name.endswith(".gbc"):        # "x.gbc.cht" stems
        name = os.path.splitext(name)[0]
    return name


def normalize(name: str) -> str:
    """Title only: region and dump tags removed."""
    return " ".join(_JUNK.sub(" ", _PAREN.sub(" ", _stem(name)).lower()).split())


def normalize_full(name: str) -> str:
    """Title plus the tags, so variants of one game can be told apart."""
    return " ".join(_JUNK.sub(" ", _stem(name).lower()).split())


@dataclass
class Candidate:
    score: float          # similarity of the titles alone
    detail: float         # similarity including region and variant tags
    path: str

    @property
    def local(self) -> bool:
        """One of yours, rather than from the libretro database."""
        return cheatlib.is_local(self.path)

    @property
    def name(self) -> str:
        return os.path.splitext(os.path.basename(self.path))[0]


@lru_cache(maxsize=8)
def _index(files: tuple[str, ...]) -> tuple[tuple[str, str, str], ...]:
    """(path, title, title-with-tags) for every candidate, computed once.

    The database does not change between clicks, but this used to run two
    regex substitutions over all three thousand filenames on every single game
    you selected, which is most of what selecting one cost.
    """
    return tuple((p, normalize(p), normalize_full(p)) for p in files)


def rank(rom_name: str, platform: str, limit: int = 8) -> list[Candidate]:
    """Best cheat files for a ROM, most likely first.

    Titles alone decide the primary score, because that is what identifies the
    game. Dozens of files then tie at 1.0 for a popular title (a Game Genie set,
    a GameShark set, one per region), so the tags break the tie: a ROM tagged
    "(USA, Australia)" should land on the cheat file with the same tags rather
    than on whichever name happens to be shortest.

    Selecting a game cost a third of a second, nearly all of it here, comparing
    a name against three thousand others twice each. Two things make that
    cheaper without changing a single result:

    * difflib offers two upper bounds on its own ratio that cost far less than
      computing it. Once `limit` candidates are in hand, anything whose best
      conceivable score is below the worst one held cannot appear in the
      answer, so it is never compared properly. Exact and prefix matches are
      checked first, because those override the ratio and an upper bound says
      nothing about them.
    * The tag score only ever breaks ties between equal title scores, so it is
      computed for the contenders at the end rather than for everything.

    Both were got wrong before. Pruning the *kept* list dropped candidates that
    tie, and reusing difflib's cached index by swapping its two sequences
    quietly changes the number it returns, because its ratio is not symmetric.
    The test compares this against the straightforward version over every game
    on a real card.
    """
    target = normalize(rom_name)
    target_full = normalize_full(rom_name)

    # Target first, candidate second: that order is not arbitrary, difflib's
    # ratio is not symmetric.
    m = difflib.SequenceMatcher(None, target, "")

    kept: list[tuple[float, str, str]] = []      # (score, path, tags-form)
    threshold = 0.0
    for path, cand, cand_full in _index(cheatlib.files_for(platform)):
        if cand == target:
            score = 1.0
        elif cand.startswith(target) or target.startswith(cand):
            m.set_seq2(cand)
            score = max(m.ratio(), 0.95)
        else:
            m.set_seq2(cand)
            # Upper bounds, cheapest first. Strictly below the worst kept
            # score means it cannot tie either, so it cannot appear.
            if threshold and (m.real_quick_ratio() < threshold
                              or m.quick_ratio() < threshold):
                continue
            score = m.ratio()

        kept.append((score, path, cand_full))
        if len(kept) >= limit * 4:
            kept.sort(key=lambda k: -k[0])
            cut = limit * 2
            # Never cut through a group of equal scores. A popular title ties
            # dozens of files at 1.0 and the tags decide between them later;
            # dropping some of them here would decide it by whichever the
            # directory listing happened to yield first.
            while cut < len(kept) and kept[cut][0] == kept[cut - 1][0]:
                cut += 1
            del kept[cut:]
            threshold = kept[-1][0]

    md = difflib.SequenceMatcher(None, target_full, "")

    def detail(cand_full: str) -> float:
        md.set_seq2(cand_full)
        return md.ratio()

    scored = [Candidate(score, detail(cand_full), path)
              for score, path, cand_full in kept]
    # Yours wins an otherwise exact tie: if you wrote a file for this ROM, that
    # is the one you meant, whatever the database also happens to have.
    scored.sort(key=lambda c: (-c.score, -c.detail, not c.local, len(c.name)))
    return scored[:limit]


def best(rom_name: str, platform: str, threshold: float = 0.72) -> Candidate | None:
    top = rank(rom_name, platform, limit=1)
    if top and top[0].score >= threshold:
        return top[0]
    return None
