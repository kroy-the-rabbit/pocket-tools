# SPDX-License-Identifier: GPL-3.0-or-later
"""Index of the libretro cheat database, restricted to the systems we support.

This was `library.py` until the cartridge dumps needed that word for the
place on the computer where they are filed. The two are unrelated: this one
knows about `.cht` files, and `library.py` knows about ROMs and saves.
"""
from __future__ import annotations

import os
from functools import lru_cache

import card as card_mod
import db

# Your own cheat files. The libretro database is replaced wholesale by an
# update, so anything added there is lost the next time you press Update; this
# lives outside it and is searched first, so a file you wrote wins ties against
# the stock one of the same name. Name it after the ROM, exactly as the ROM is
# named, and it will match: the picker compares filenames.
LOCAL = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "pocket-cheats", "cht")


def local_dir() -> str:
    os.makedirs(LOCAL, exist_ok=True)
    return LOCAL


def is_local(path: str) -> bool:
    return os.path.abspath(path).startswith(os.path.abspath(LOCAL))


# Which database directories to search for a given Pocket platform, best first.
#
# Game Boy and Game Boy Color share: plenty of GBC releases are filed under
# Game Boy and the reverse, because they are "GB Compatible", and the ROM on
# the card gives no hint which. Game Boy Advance does not share with either.
# Its codes are a different language, so a Game Boy file matched to a GBA ROM
# would not be a near miss, it would be nonsense. See cheatfile.py.
#
# PC Engine shares with nothing, and it has two neighbours it might look like
# it should. libretro ships a SuperGrafx directory, which this core will never
# run because it drops SuperGrafx for ALM room. It also ships a PC Engine CD
# directory, and that one is a matter of timing rather than of never: the core
# reads discs on `cd-streaming` but ships no release that does. When it does,
# CD gets its own id here rather than joining this tuple, because a HuCard
# cheat and a CD cheat are written against different games and a near miss
# across them would be a wrong match, not a helpful one.
SEARCH = {
    "gbc": ("gbc", "gb"),
    "gb":  ("gb", "gbc"),
    "gba": ("gba",),
    "pce": ("pce",),
}
# A system switched off in card.ENABLED disables itself here: _files_for drops
# any id that is not in card.SUPPORTED, so an entry above for a system that is
# not offered resolves to no directories rather than to a stale one.


class MissingDatabase(Exception):
    pass


@lru_cache(maxsize=None)
def _files_for(platform: str, db_dir: str, generation: int) -> tuple[str, ...]:
    """Cheat files for a Pocket platform id.

    Both Game Boy directories are searched for either platform: plenty of GBC
    releases are filed under Game Boy (and vice versa) because they are
    "GB Compatible", and the ROM on the card gives no hint which.

    `generation` is not read. It is in the key so that refresh() can retire the
    cache after an update, which replaces the files in place and so leaves the
    path, and every other part of the key, exactly as it was.
    """
    if not os.path.isdir(db_dir):
        raise MissingDatabase(
            f"{db_dir} not found. Press Update to fetch the cheat database.")
    dirs = list(SEARCH.get(platform, (platform,)))
    dirs = [card_mod.SUPPORTED[p] for p in dirs if p in card_mod.SUPPORTED]
    out: list[str] = []
    # yours first: an exact-name tie should land on the file you wrote
    if os.path.isdir(LOCAL):
        for dirpath, _sub, files in os.walk(LOCAL):
            for f in sorted(files):
                if f.endswith(".cht"):
                    out.append(os.path.join(dirpath, f))
    for d in dirs:
        full = os.path.join(db_dir, d)
        if not os.path.isdir(full):
            continue
        for f in sorted(os.listdir(full)):
            if f.endswith(".cht"):
                out.append(os.path.join(full, f))
    return tuple(out)


_generation = 0


def files_for(platform: str) -> tuple[str, ...]:
    return _files_for(platform, db.db_dir(), _generation)


def refresh() -> None:
    """Forget the index. Call after an update, or after adding a file of yours."""
    global _generation
    _generation += 1


def available() -> bool:
    return db.available()
