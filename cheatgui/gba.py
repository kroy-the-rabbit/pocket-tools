# SPDX-License-Identifier: GPL-3.0-or-later
"""Game Boy Advance cheat codes.

CodeBreaker and GameShark v1/v2 codes, decoded by `gbacht`, which is the
reference model the core's own converter parses with and is checked against all
513 files in the libretro Game Boy Advance directory. This module is the
adapter between that and the shapes the rest of the app expects; the decoding
itself is not here and should not be.

Two things make this system different from the other three.

**The core does not read text.** Game Boy, Game Boy Color and PC Engine all
read a `.cht` off the card directly. The GBA core cannot: its cheat engine sits
in a design at 95 % logic utilisation, and an on-FPGA ASCII parser measured 441
ALMs but grew the design by 1,285 and cost 0.54 ns of setup timing, which is
the difference between a core that runs and one that does not exist. So the
parse happens here, and what lands on the card is `.chtbin`, a 16-byte header
and one 16-byte entry per code. `writer.py` writes both that and the `.cht` it
came from; see there for why both.

**A cheat's size is not its code count.** Everywhere else one code is one slot
in the core's store. Here one code becomes one 128-bit *entry*, and the store
holds 32 of them, but a conditional code is a compare entry followed by the
entry it guards, so it spends two. That is why this module hands out entries as
codes rather than counting them separately: it keeps the meter, the limit check
and the store honest with a single number, and the number is the one the
hardware counts.

Codes the engine cannot express are dropped by `gbacht` rather than guessed at,
and it says why. Encrypted codes are the interesting case: GameShark v3, Action
Replay v3 and post-`9` CodeBreaker codes are enciphered with a per-game seed and
are shaped exactly like raw ones, so they are rejected by plausibility - a raw
code's address lands in EWRAM, IWRAM or IO once masked to 28 bits, and an
enciphered word almost never does. A cheat left with no usable code keeps its
description and loses its codes, which is what `model.Entry.placeholder`
already means: shown, greyed, not pickable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import cht2bin
import gbacht

# The cheat table in gba_cheats.vhd, as entries rather than cheats. Every cheat
# spends at least one and a conditional spends two, so this is the only limit
# worth showing: a selection that fits it cannot overflow anything else.
MAX_ENTRIES = gbacht.MAX_ENTRIES


@dataclass
class Code:
    """One decoded entry, shaped like `ggdecode.Cheat` so the UI needs no
    special case.

    `kind` is always "poke". `gba_cheats` is a poker, not a read override: on
    vblank it waits for the memory bus to go idle, pauses the CPU and writes
    each entry through the debug bus. There is no Game Genie for this machine
    and nothing here patches a ROM read.

    `raw` is `gbacht`'s canonical name for the entry, not the file's own
    spelling, because the file's spelling cannot be recovered - `+` separates
    both the halves of one code and one code from the next. What it guarantees
    instead is what `writer.key_of` needs: rendering these back into a `_code`
    value and reparsing yields the same entries with the same names.
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


def _code(entry) -> Code:
    return Code(entry.raw, "poke", entry.address, entry.value)


NO_LIMIT = gbacht.NO_LIMIT


def parse(data: bytes, max_groups: int = NO_LIMIT) -> list:
    """Cheat groups from a `.cht` file.

    Read in `browse` mode, which is the difference between what this file
    contains and what the core would run: cheats that are switched off come
    back switched off rather than omitted, and a cheat nothing could be made of
    comes back with its description and no codes, which is the greyed,
    unpickable row `model.Entry.placeholder` already describes.

    `max_entries` is deliberately not the core's 32 either. Reading a file to
    choose from is not reading it to run: a libretro file often holds hundreds
    of codes, and stopping at the store's size would make everything past the
    first couple of dozen invisible and unpickable. The limit is applied when a
    selection is written, by `pack()` and by `writer.check`.
    """
    groups, _ = gbacht.parse(data, max_entries=NO_LIMIT,
                             max_group_entries=NO_LIMIT, browse=True)
    out = []
    for g in groups:
        if len(out) >= max_groups:
            break
        out.append(Group(len(out), [_code(e) for e in g.entries],
                         g.desc, g.enabled))
    return out


def applied_by(code) -> str:
    """How the core makes one code take effect. Always the same answer."""
    return "poke"


def pack(groups: list) -> bytes:
    """The `.chtbin` for a selection: what the core actually reads.

    Order is load-bearing and nothing here may sort or dedupe. A conditional is
    a compare entry immediately followed by the entry it guards, and `gba_cheats`
    expresses that as adjacency: a failed compare sets `skip_next`, which
    suppresses whatever entry comes after it. Reordering silently reattaches a
    condition to a different cheat.
    """
    words = []
    for g in groups:
        for c in g.codes:
            words.append(_word(c))
    return cht2bin.pack(words)


def _word(code) -> int:
    """The 128-bit word for one code, back from its canonical name.

    Decoding the name rather than carrying the word keeps one decoder in the
    design. `parse` built the name from an entry; feeding it back through the
    same pair decoder must give that entry again, and the tests hold it to that
    over the whole database.
    """
    t1, _, t2 = code.raw.partition("+")
    entry, why = gbacht.decode_pair(t1, t2)
    if entry is None:
        raise ValueError(f"{code.raw}: {why}")
    return entry.word
