# SPDX-License-Identifier: GPL-3.0-or-later
"""Cartridges you own, and the cheat file each one uses.

A cartridge is not a file on the card, so it never appears in the game list,
and in Play Cartridge mode the Pocket does not auto-load a cheat file named
after it either. You browse for one from the core menu instead, and the slot
remembers it. So the useful thing this can do is put a file where you can find
it, under a name you will recognise, and remember which cheats you chose.

The list lives in the same config directory as the other remembered choices,
outside the repo, so nothing here is lost when the checkout changes.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import prefs

LIST = os.path.join(os.path.dirname(prefs.CONFIG), "cartridges.json")

# Where the files go on the card. Its own folder so the core's file browser
# opens on your cartridges rather than on a few hundred ROMs.
CARD_DIR = "Cartridges"

# The systems a cartridge can be filed under, in the order they are shown.
#
# Not card.ENABLED, and the difference is the point. A system can be listed
# there and still have no cartridge path: Game Boy Advance and PC Engine are SD
# card only, and cartridges stay unsupported on both until their cores support
# them.
#
# Keeping a system out of this tuple is what makes its cartridge path
# unreachable rather than merely unused, so this tuple is the enforcement and
# not a display order that something else could contradict.
PLATFORMS = ("gbc", "gb")
DEFAULT_PLATFORM = "gbc"


@dataclass
class Cartridge:
    """Quacks like card.Game, so the rest of the app treats it as one."""
    name: str
    platform: str
    card_root: str = ""

    @property
    def path(self) -> str:
        """Identity for the remembered-source table. Not a real file."""
        return f"cart:{self.platform}:{self.name}"

    @property
    def cht_path(self) -> str:
        return os.path.join(self.card_root, "Assets", self.platform, "common",
                            CARD_DIR, self.name + ".cht")

    @property
    def subdir(self) -> str:
        return os.path.dirname(self.cht_path)


def _load() -> list[dict]:
    try:
        data = json.load(open(LIST))
        return data.get("cartridges", [])
    except Exception:                                        # noqa: BLE001
        return []


def _save(rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(LIST), exist_ok=True)
    tmp = LIST + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"cartridges": rows}, f, indent=2)
    os.replace(tmp, LIST)


def all(card_root: str = "") -> list[Cartridge]:
    return [Cartridge(r["name"], r.get("platform", DEFAULT_PLATFORM), card_root)
            for r in sorted(_load(), key=lambda r: r["name"].lower())]


def grouped(cartridges: list[Cartridge]) -> list[tuple[str, list[int]]]:
    """(platform, positions) in display order, skipping systems with none.

    Positions rather than the cartridges themselves, because the caller lists
    them in one flat list and addresses rows by index into it. Handing back
    objects invited matching them up by identity, which silently broke the
    moment the list was rebuilt.
    """
    out = []
    for pid in PLATFORMS:
        members = [i for i, c in enumerate(cartridges) if c.platform == pid]
        if members:
            out.append((pid, members))
    return out


def add(name: str, platform: str = DEFAULT_PLATFORM) -> bool:
    """False if that name is already listed.

    Raises for a system with no cartridge path, the same way set_platform
    does. The two were not symmetric: a cartridge could not be *moved* to
    Game Boy Advance but could be *created* there, and a row written that way
    is filed under a system PLATFORMS does not list, so grouped() never yields
    it and it disappears from the list while still occupying its name.
    """
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}")
    name = name.strip()
    if not name:
        return False
    rows = _load()
    if any(r["name"].lower() == name.lower() for r in rows):
        return False
    rows.append({"name": name, "platform": platform})
    _save(rows)
    return True


def set_platform(name: str, platform: str) -> bool:
    """File a cartridge under the other system. False if nothing changed.

    The remembered cheat source is keyed by platform, so it is carried over
    rather than left behind: a cartridge filed under the wrong system and
    then corrected should not also lose the file you picked for it.

    The cheat file already on the card is not moved. Its directory is per
    platform, so the old one is stale after this, exactly as it is after a
    removal, and for the same reason: this app does not delete things off the
    card that it did not just write.
    """
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}")
    rows = _load()
    changed = False
    for r in rows:
        if r["name"].lower() == name.lower() and r.get(
                "platform", DEFAULT_PLATFORM) != platform:
            old = r.get("platform", DEFAULT_PLATFORM)
            r["platform"] = platform
            changed = True
            source = prefs.get_source(f"cart:{old}:{r['name']}")
            prefs.set_source(f"cart:{old}:{r['name']}", None)
            if source:
                prefs.set_source(f"cart:{platform}:{r['name']}", source)
    if changed:
        _save(rows)
    return changed


def remove(name: str) -> bool:
    """Drop a cartridge from the list. False if it was not listed.

    Matched the way add() rejects duplicates, case insensitively, so the two
    cannot disagree about whether a name is already there.
    """
    rows = _load()
    keep = [r for r in rows if r["name"].lower() != name.lower()]
    if len(keep) == len(rows):
        return False
    _save(keep)
    for p in PLATFORMS:
        prefs.set_source(f"cart:{p}:{name}", None)
    return True
