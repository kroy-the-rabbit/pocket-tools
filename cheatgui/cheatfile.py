# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading a .cht file for a particular system.

The file format is the same everywhere, libretro's `cheatN_desc/_code/_enable`.
What the codes inside it *mean* is not, and that is the whole of this module.

Game Boy and Game Boy Color codes are decoded by `chtparse`, which is the
reference model the core's RTL parser is verified against. Game Boy Advance
codes are a different language: CodeBreaker and GameShark, written as an eight
digit address and a four or eight digit value joined with `+`.

Handing a GBA file to the Game Boy parser does not fail, which is the reason
this module exists. `3300786D+00FF` is a CodeBreaker code; the Game Boy parser
sees eight hex digits, reads them as a GameShark code, and reports a write of
`0x00` to `$6D78`. The `+00FF` is four digits, matches nothing, and is dropped.
Every code in the file comes out looking plausible and meaning nothing, and a
file written back from that has lost half of itself.

So each system has its own reader, and the choice of reader is the whole of
this module. `gba.py` reads Game Boy Advance and `pce.py` reads PC Engine; what
those two have in common, and do not share with Game Boy, is that there is only
one kind of code, because neither machine has a Game Genie. See MECHANISMS.

A system whose codes cannot be decoded is carried verbatim instead: listed,
picked and written back exactly as it came, with nothing claimed about what any
of it does. Nothing uses that path today. It is kept because refusing to guess
is the right answer for the next system, and it is what Game Boy Advance was
carried by until its core defined a format.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import chtparse
import gba as gba_mod
import pce as pce_mod

# Systems whose codes can be read rather than merely carried. Game Boy and
# Game Boy Color go through chtparse, which is the model the core's RTL parser
# is verified against. PC Engine goes through pce.py: its codes are a single
# unambiguous form of RAM poke, documented there, and every file in the
# libretro directory decodes and writes back unchanged. Game Boy Advance goes
# through gba.py, which adapts `gbacht` - the same relationship chtparse has to
# the Game Boy core, except that on this system the decoder is what produces
# the file the hardware reads rather than a model of a parser inside it.
DECODED = ("gb", "gbc", "gba", "pce")

# How many ways a system's core can make a code take effect.
#
# Game Boy has two and the difference is worth a column, because on a cartridge
# it is the difference between a code that fails safely and one that does not.
# PC Engine has one: every published cheat for it is a RAM poke and there is no
# Game Genie for the machine. A column carrying the same word in every row is
# noise, so callers ask this rather than assuming two. Absent means unknown,
# which is not the same as one.
MECHANISMS = {
    "gb":  ("poke", "patch"),
    "gbc": ("poke", "patch"),
    "gba": ("poke",),
    "pce": ("poke",),
}

# What the core can hold. No limit is claimed for a system whose core has not
# defined one; making a number up would put it on screen with nothing checking
# it. The Game Boy figures come from cheatcodes.sv.
#
# Game Boy Advance counts entries, and gba.py hands out one code per entry so
# that this stays a pair of plain numbers. Both are 32 because both are the
# same 32: gba_cheats has one table, every cheat spends at least one slot of
# it, and a conditional spends two.
LIMITS = {
    "gb":  (chtparse.MAX_GROUPS, chtparse.MAX_CODES),
    "gbc": (chtparse.MAX_GROUPS, chtparse.MAX_CODES),
    "gba": (gba_mod.MAX_ENTRIES, gba_mod.MAX_ENTRIES),
}

# Reading a file to choose from is not reading it to run. The core takes the
# first 32 codes; a libretro file often holds hundreds, and truncating here
# made everything past the first couple of dozen invisible and unpickable.
NO_LIMIT = 1 << 30


def decoded(platform: str) -> bool:
    """True when the codes for this system can be read, not merely carried."""
    return platform in DECODED


def mechanisms(platform: str) -> tuple[str, ...]:
    """The ways this system's core applies a code. () when that is not known."""
    return MECHANISMS.get(platform, ())


def limits(platform: str) -> Optional[tuple[int, int]]:
    """(cheats, codes) the core can hold, or None if that is not known."""
    return LIMITS.get(platform)


# --------------------------------------------------------------- opaque form --
@dataclass
class OpaqueCode:
    """One code we can carry but not read.

    Shaped like `ggdecode.Cheat` so that everything downstream, which only ever
    wants `.raw`, does not have to know which kind it is holding. `address` and
    `value` are None rather than zero: a zero would be displayed.
    """
    raw: str
    kind: str = "opaque"
    address: Optional[int] = None
    value: Optional[int] = None
    compare: Optional[int] = None
    bank: Optional[int] = None


@dataclass
class OpaqueGroup:
    index: int
    codes: list = field(default_factory=list)
    desc: Optional[str] = None
    enabled: bool = True


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_opaque(data: bytes, max_groups: int = NO_LIMIT) -> list:
    """Read the file's structure without reading its codes.

    Same rules chtparse follows for which keys count: only `_desc`, `_code` and
    `_enable`, only when `=` follows, each `_code` starting a new cheat, and a
    cheat with no `_enable` key at all defaulting to on. The value of `_code` is
    kept as written, split on `+` into the pieces the file joined, so writing it
    back reproduces it character for character.
    """
    groups: list[OpaqueGroup] = []
    desc: Optional[str] = None

    for line in data.decode("utf-8", "replace").splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.endswith("_desc"):
            desc = _unquote(value)
        elif key.endswith("_code"):
            if len(groups) >= max_groups:
                break
            codes = [OpaqueCode(part.strip())
                     for part in _unquote(value).split("+") if part.strip()]
            if not codes:
                continue
            groups.append(OpaqueGroup(len(groups), codes, desc, True))
            desc = None
        elif key.endswith("_enable") and groups:
            groups[-1].enabled = _unquote(value).strip().lower() in ("true", "1")
    return groups


# ---------------------------------------------------------------- the reader --
def parse(data: bytes, platform: str, max_groups: int = NO_LIMIT) -> list:
    """Cheat groups from a file, read the way this system's codes work."""
    if platform == "gba":
        return gba_mod.parse(data, max_groups=max_groups)
    if platform == "pce":
        return pce_mod.parse(data, max_groups=max_groups)
    if decoded(platform):
        return chtparse.parse(data, max_codes=NO_LIMIT, max_groups=max_groups)
    return parse_opaque(data, max_groups=max_groups)


def applied_by(code, platform: str) -> str:
    """How the core makes one code take effect, or "" when that is not known."""
    if platform == "gba":
        return gba_mod.applied_by(code)
    if platform == "pce":
        return pce_mod.applied_by(code)
    if not decoded(platform):
        return ""
    return chtparse.applied_by(code)
