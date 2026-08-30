# SPDX-License-Identifier: GPL-3.0-or-later
"""PC Engine / TurboGrafx-16 cheat codes.

Every published PC Engine cheat is a RAM poke. There is no Game Genie for this
machine and the libretro database holds no ROM patch for it, which inverts the
model the Game Boy side is built around: there, the read override is the
primary mechanism and the poker is the addition. Here the poker is the whole
feature and there is only one kind of code to show.

The database directory holds two shapes, and a file uses one or the other.
Counted over all 397 files on 2026-08-25:

  form A, 246 files   cheat0_code = "1f1548:64"
                      a hex CPU address and a hex byte, several joined by "+".
  form B, 151 files   cheat0_code = "" and the code in separate keys:
                      cheat0_address (decimal offset into work RAM),
                      cheat0_value, cheat0_cheat_type, cheat0_memory_search_size
                      and a pile of cheat0_rumble_* that mean nothing here.

Form B is RetroArch's own cheat-search format, the one its search UI writes.
The plan this was built from described it as 47 files all named "(Rumbles)";
that is wrong, and it matters. There are 151 of them and 104 carry ordinary
game names, so a parser that only handles form A silently shows well over a
third of the corpus as empty.

Two form B rows are dropped rather than converted, and both cases are real:

  cheat_type = "0"                RetroArch's "disabled". The row watches an
                                  address to fire a rumble and never writes
                                  anything. 70 rows. Converting one into a
                                  poke would invent a cheat the author did not
                                  write: "Rumble on gold change" with value 5
                                  would pin your gold to 5.
  memory_search_size != "3"       not a byte. 2 rows, both bit-level entries in
                                  Wonder Momo. This has no way to express a
                                  partial byte and will not guess one.

Dropped rows keep their description and lose their codes, which is exactly
what `model.Entry.placeholder` already means: shown, greyed, not pickable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# The 8KB work RAM at bank $F8. Every code in the database is a write into
# this, or into the 32KB a SuperGrafx has in its place.
WORK_RAM = 0x1F0000
WORK_RAM_END = 0x1F1FFF

# The HuC6280 drives 21 address lines, so nothing it can read or write lives
# above this. Two codes in the database do: `1f0000f:0c`, in both Magical
# Chase files, is seven hex digits where all 1027 others are six. It is not an
# address this machine has, so it is refused rather than carried.
MAX_ADDRESS = 0x1FFFFF

# The largest work RAM offset form B can sensibly name. A SuperGrafx has 32KB
# where a PC Engine has 8, and libretro's memory map covers the larger one, so
# offsets above 8KB are real and appear in 13 rows. Past 32KB they are not:
# Bomberman 94 carries 18446744073709546426, which is -5190 written as an
# unsigned 64-bit number, and points below work RAM rather than into it.
MAX_OFFSET = 0x7FFF

# A sanity bound on the repeat family below. Nothing in the database goes past
# 2; a row claiming thousands is corrupt, and expanding it would fill the cheat
# list with a single entry's worth of noise.
MAX_REPEAT = 256

# RetroArch's memory_search_size for one byte. The others describe 1, 2 and 4
# bit reads and 16 and 32 bit ones, none of which is a byte poke.
BYTE = "3"

# RetroArch's cheat_type for "set to value". 0 is "disabled", and the higher
# numbers are increment, decrement and the conditional run-next family, none of
# which the plain poke this produces can stand in for.
SET_TO_VALUE = "1"


@dataclass
class Code:
    """One poke. Shaped like `ggdecode.Cheat` so the UI needs no special case.

    `kind` is always "poke": that is the point of this module.
    """
    raw: str
    kind: str = "poke"
    address: Optional[int] = None
    value: Optional[int] = None
    compare: Optional[int] = None
    bank: Optional[int] = None


@dataclass
class Group:
    index: int
    codes: list = field(default_factory=list)
    desc: Optional[str] = None
    enabled: bool = True


def render_code(address: int, value: int) -> str:
    """The canonical text for one poke.

    Lowercase, unpadded address, two digit value, which is how every one of
    the 1029 form A codes in the database is already written. Writing a file
    back therefore reproduces it character for character, and a form B file
    converted by this comes out in the same form as its form A neighbours.

    This matters beyond tidiness: `writer.key_of` identifies a cheat by its
    code text, so a file we wrote and the library file it came from have to
    spell the same poke the same way or nothing shows as already installed.
    """
    return f"{address:x}:{value:02x}"


def parse_code(text: str) -> Optional[Code]:
    """One "addr:value" token, or None if it is not one."""
    addr, sep, val = text.strip().partition(":")
    if not sep:
        return None
    try:
        address = int(addr.strip(), 16)
        value = int(val.strip(), 16)
    except ValueError:
        return None
    if not 0 <= address <= MAX_ADDRESS or not 0 <= value <= 0xFF:
        return None
    return Code(render_code(address, value), "poke", address, value)


def in_work_ram(address: int) -> bool:
    """Whether a poke lands in the 8KB a PC Engine actually has.

    14 codes in the database sit between 0x1F2000 and 0x1F2656, which is inside
    the 32KB a SuperGrafx carries and outside the 8KB everything else does. The
    core drops SuperGrafx to buy the room the cheat engine needs, so those are
    addressable and unreachable at the same time. They are carried, because the
    file says what it says and the core does not exist yet to disagree, and
    this is here so a caller can ask.
    """
    return WORK_RAM <= address <= WORK_RAM_END


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _number(text: str) -> str:
    """A numeric field with any stray quoting taken off.

    `_unquote` only strips a matched pair, which is right for descriptions. One
    row in the database is written `cheat1_value = ""255"`, an unbalanced quote
    that leaves `"255` behind, and it is the second half of a two part cheat
    whose first half parses. Dropping half a cheat over a typo in the file is
    worse than being lenient about a character that cannot be part of a number.
    """
    return text.strip().strip("\"'")


def _rows(data: bytes) -> dict[int, dict[str, str]]:
    """Every `cheatN_<key> = value` line, gathered by N.

    Form B writes its keys in alphabetical order, so `cheat0_address` arrives
    before `cheat0_desc` and there is no stream order to rely on. Both forms
    are read this way rather than one line at a time.
    """
    out: dict[int, dict[str, str]] = {}
    for line in data.decode("utf-8", "replace").splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key.startswith("cheat"):
            continue
        rest = key[5:]
        digits = rest[:len(rest) - len(rest.lstrip("0123456789"))]
        if not digits or len(digits) >= len(rest) or rest[len(digits)] != "_":
            continue
        out.setdefault(int(digits), {})[rest[len(digits) + 1:]] = _unquote(value)
    return out


def _from_row(row: dict[str, str]) -> list[Code]:
    """The codes one cheat carries, in whichever form it wrote them."""
    text = row.get("code", "").strip()
    if text:
        codes = [parse_code(part) for part in text.split("+") if part.strip()]
        return [c for c in codes if c is not None]

    if "address" not in row or "value" not in row:
        return []
    if row.get("cheat_type", SET_TO_VALUE) != SET_TO_VALUE:
        return []                     # watches an address, never writes one
    if row.get("memory_search_size", BYTE) != BYTE:
        return []                     # not a byte, and this will not guess
    try:
        offset = int(_number(row["address"]), 10)
        value = int(_number(row["value"]), 10)
        # RetroArch's repeat family: write the value `count` times, stepping
        # the address and the value each round. Eleven rows carry it with a
        # count of 1, where it means nothing. One does not: Wonder Momo's "One
        # hit kills bosses" is count 2 stepping the address by 32, and reading
        # only the first half of it would half-apply the cheat.
        count = int(_number(row.get("repeat_count", "1")) or "1", 10)
        step_addr = int(_number(row.get("repeat_add_to_address", "0")) or "0", 10)
        step_value = int(_number(row.get("repeat_add_to_value", "0")) or "0", 10)
    except ValueError:
        return []
    if not 1 <= count <= MAX_REPEAT:
        return []

    out = []
    for i in range(count):
        at = offset + i * step_addr
        val = value + i * step_value
        if not 0 <= at <= MAX_OFFSET or not 0 <= val <= 0xFF:
            return []
        address = WORK_RAM + at
        out.append(Code(render_code(address, val), "poke", address, val))
    return out


def parse(data: bytes, max_groups: int = 1 << 30) -> list[Group]:
    """Cheat groups from a PC Engine file, in either form.

    A row with no usable code still becomes a group, keeping its description
    and carrying no codes, so the reason a cheat cannot be picked is visible
    rather than the cheat simply being absent from a list.
    """
    groups: list[Group] = []
    for _n, row in sorted(_rows(data).items()):
        if len(groups) >= max_groups:
            break
        if "code" not in row and "address" not in row:
            continue          # a stray cheatN_something with no code at all
        enable = row.get("enable")
        groups.append(Group(len(groups), _from_row(row), row.get("desc"),
                            enable is None
                            or enable.strip().lower() in ("true", "1")))
    return groups


def applied_by(_code) -> str:
    """Always a poke. Kept as a function so cheatfile can call it uniformly."""
    return "poke"
