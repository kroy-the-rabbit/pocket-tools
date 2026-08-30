#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Parse libretro GBA `.cht` files into the 128-bit words `gba_cheats` eats.

This is the golden reference for `src/fpga/core/cheat_loader.sv`: the testbench
feeds both this and the RTL the same bytes and compares the words they emit.
Written as the same flat state machine as the RTL so the two can be read side
by side rather than only compared by result.

The consumer
------------
MiSTer's `rtl/gba_cheats.vhd` takes one 128-bit word per entry, latched on a
rising edge of `cheat_on`. There is no documentation for the layout; it is read
off that RTL, and cross-checked byte for byte against the pre-encoded cheat
files in MiSTer-devel/Cheats_MiSTer (`tools/sim/fixtures/mister_007.txt`).

    bits 31:0     replacement value, and the operand of a compare entry
    bits 63:32    not read by the module
    bits 91:64    28-bit GBA bus address, word aligned
    bits 95:92    not read
    bits 99:96    optype
    bits 103:100  byte enables for the four bytes of the value

Every access the module makes is 32-bit: it reads the word at bits 91:64,
replaces the bytes selected by the byte-enable nibble, and writes it back. Byte
and halfword codes are therefore encoded by aligning the address down to a word
boundary and shifting the value into its lane.

Optypes, and a trap in them
---------------------------
An entry whose optype is not ALWAYS performs no write. It reads the word,
zeroes the bytes outside the byte mask, compares the result against bits 31:0,
and on failure sets `skip_next`, which suppresses the *following* entry in the
module's 32-slot table. A conditional code is therefore a PAIR of entries, in
that order, and both count against the 32-entry budget.

The names in `gba_cheats.vhd` do not all describe what the code does. Read off
the comparisons in its CHEAT_TEST state, the effective meaning of each optype
is "run the next entry if":

    0  always            (this entry is itself a write)
    1  mem == operand    named OPTYPE_EQUALS
    2  mem >  operand    named OPTYPE_GREATER
    3  mem >= operand    named OPTYPE_LESS       <-- name is inverted
    4  mem <  operand    named OPTYPE_GREATER_EQ <-- name is inverted
    5  mem <= operand    named OPTYPE_LESS_EQ
    6  mem != operand    named OPTYPE_NOT_EQ
    F  empty slot

Optypes 3 and 4 are swapped relative to their constant names: OPTYPE_LESS skips
when `oldvalue < cheatdata`, which means the guarded entry runs when memory is
greater or equal. The numbers below are the effective ones, so a "less than"
code emits 4 and a "greater or equal" code emits 3.

Code text
---------
A GBA `.cht` code value holds a run of hex tokens separated by '+', spaces or
':'. They pair up two at a time:

    8 digits then 4 digits    CodeBreaker      AAAAAAAA VVVV
    8 digits then 8 digits    GameShark v1/v2  AAAAAAAA VVVVVVVV
    12 digits                 CodeBreaker, written without a separator
    16 digits                 GameShark, written without a separator

The top nibble of the first word is the code type, and the low 28 bits are the
address. Type semantics follow mGBA's `src/gba/cheats/codebreaker.c` and
`gameshark.c`.

A third dialect, and how it is told apart
-----------------------------------------
Lists exported for GameShark SP and Action Replay v3 write an 8+8 code whose
top nibble is 0 but which is not a v1/v2 8-bit assign:

    0WAAAAAA VVVVVVVV    W = 0 one byte, 2 two bytes, 4 four bytes
                         AAAAAA is an offset into EWRAM, not a bus address

The width is in the second nibble rather than the type, and the address is 24
bits of EWRAM offset that the hardware's 256 KB mirroring folds down. Nothing
in the file says which dialect a code is in, and the two overlap: `02002AEA
00000050` is a valid v1/v2 8-bit assign *and* a valid halfword assign here.

So this dialect is only ever tried on a code the v1/v2 rules have already
rejected. That keeps every code that decodes today decoding the same way, and
confines the new reading to words that were being thrown away.

It is worth having because these exports are most of what a user downloads by
hand. In the gamehacking.org list for The Minish Cap, the same cheats appear in
both dialects, which is what pinned the format:

    00202AEA 000000A0   and   32002AEA 00A0     Infinite Health
    02202B00 000003E7   and   82002B00 03E7     999 Rupees

What is rejected, and why it has to be
--------------------------------------
GameShark v3, Action Replay v3 and CodeBreaker codes past a `9` (CB_ENCRYPT)
line are encrypted with a per-game seed, and a `.cht` file gives no indication
which encoding a code uses. Encrypted codes are indistinguishable from raw ones
by shape alone: they are eight hex digits and eight more, exactly like a raw
GameShark code.

They are rejected by plausibility. A raw code's address, once masked to 28
bits, lands in EWRAM, IWRAM or IO; an encrypted word is uniformly random and
almost never does. Types that `gba_cheats` cannot express (OR, AND, ADD, fills,
ROM patches, button tests, pointer chains) are rejected outright.

This matters. gamehacking.org's encoder, whose output ships as
MiSTer-devel/Cheats_MiSTer, does *not* filter: its GBA files contain entries
with addresses like `0b070768` and `0f0e1320`, which are encrypted codes run
through a raw decoder. Those are live pokes at nothing, into a 32-entry table
that has room for real cheats.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Optional

# The table in gba_cheats.vhd. A conditional costs two of these.
MAX_ENTRIES = 32
# Entries buffered for one cheat while it waits to learn whether it is enabled.
# libretro writes `_enable` after `_code`, so a cheat cannot be emitted as it is
# parsed. Making the buffer the same depth as the table means the buffer is
# never the binding limit: whatever a cheat can hold, the table could have.
MAX_GROUP_ENTRIES = MAX_ENTRIES

OPT_ALWAYS = 0x0
OPT_EQ = 0x1
OPT_GT = 0x2
OPT_GE = 0x3        # named OPTYPE_LESS in the VHDL; see the module docstring
OPT_LT = 0x4        # named OPTYPE_GREATER_EQ in the VHDL
OPT_LE = 0x5
OPT_NE = 0x6

# Where a 32-bit debug-bus write from gba_cheats is both meaningful and safe.
# BIOS and ROM are not writable, SRAM at 0x0E000000 is a byte-wide bus that a
# 32-bit access misreads, and VRAM/OAM/palette are rewritten by the game every
# frame after the vblank poke lands, so a code there does nothing anyway.
REGIONS = (
    (0x2000000, 0x203FFFF),   # EWRAM, 256 KB
    (0x3000000, 0x3007FFF),   # IWRAM, 32 KB
    (0x4000000, 0x40003FE),   # IO
)


def address_is_real(addr: int) -> bool:
    return any(lo <= addr <= hi for lo, hi in REGIONS)


@dataclass
class Entry:
    """One 128-bit word."""
    optype: int
    bytemask: int
    address: int          # word aligned, 28 bits
    value: int            # already shifted into its byte lane
    kind: str = ""        # "cb" / "gs", for reporting only
    # The two hex words this entry was decoded from, canonical rather than
    # verbatim. The original spelling cannot be recovered: `+` separates the
    # two halves of one code *and* one code from the next, and the tokenizer
    # ignores separators entirely, so "3300786D+00FF" and "3300786D 00FF" and
    # "3300786D00FF" all arrive here identically. What callers need is not the
    # file's spelling but a stable name for the entry, so that a file written
    # from these codes reads back as the same cheats it was written from.
    raw: str = ""

    @property
    def word(self) -> int:
        return ((self.bytemask & 0xF) << 100 | (self.optype & 0xF) << 96
                | (self.address & 0x0FFFFFFF) << 64 | (self.value & 0xFFFFFFFF))

    def __eq__(self, other):
        return isinstance(other, Entry) and self.word == other.word

    def __repr__(self):
        return (f"Entry({self.raw or '?'} opt={self.optype:x} "
                f"bm={self.bytemask:x} "
                f"addr={self.address:08x} val={self.value:08x})")


def _lane(addr: int, width: int, value: int) -> Optional[Entry]:
    """Align `addr` down to a word and shift `value` into its byte lane.

    Returns None for a misaligned halfword or word, which no real code uses and
    which the byte-enable nibble cannot express.
    """
    if width == 1:
        if value & ~0xFF:
            return None
        off = addr & 3
        return Entry(OPT_ALWAYS, 1 << off, addr & ~3, (value & 0xFF) << (8 * off))
    if width == 2:
        if addr & 1 or value & ~0xFFFF:
            return None
        off = addr & 2
        return Entry(OPT_ALWAYS, 0x3 << off, addr & ~3, (value & 0xFFFF) << (8 * off))
    if width == 4:
        if addr & 3:
            return None
        return Entry(OPT_ALWAYS, 0xF, addr, value & 0xFFFFFFFF)
    return None


def _cond(addr: int, value: int, optype: int) -> Optional[Entry]:
    """A 16-bit compare entry. Same lane placement as a 16-bit write.

    gba_cheats zeroes the bytes outside the mask before comparing the whole
    32-bit word, so putting the operand in its lane keeps the ordering
    comparisons correct as well as the equality ones.
    """
    e = _lane(addr, 2, value)
    if e is None:
        return None
    e.optype = optype
    return e


# ------------------------------------------------------------- CodeBreaker --
# Types from mGBA's enum GBACodeBreakerType. Only the ones gba_cheats can
# express are decoded; the rest are dropped rather than approximated.
#   0 GAME_ID, 1 HOOK          no memory effect, silently ignored
#   2 OR_2, 6 AND_2, E ADD_2   read-modify-write ops the module cannot do
#   4 FILL, 5 FILL_LIST        span several lines
#   9 ENCRYPT                  everything after it in the file is encrypted
#   D IF_SPECIAL               keypad test
#   F IF_AND                   bit test
CB_COND = {0x7: OPT_EQ, 0xA: OPT_NE, 0xB: OPT_GT, 0xC: OPT_LT}


def decode_codebreaker(op1: int, op2: int) -> tuple[Optional[Entry], str]:
    """Decode one CodeBreaker line. Returns (entry, disposition)."""
    kind = op1 >> 28
    addr = op1 & 0x0FFFFFFF
    if kind in (0x0, 0x1):
        return None, "ignored"           # game id and hook: no memory effect
    if kind == 0x9:
        return None, "encrypt"           # poisons the rest of the file
    if kind == 0x3:
        e = _lane(addr, 1, op2 & 0xFFFF)
    elif kind == 0x8:
        e = _lane(addr, 2, op2)
    elif kind in CB_COND:
        e = _cond(addr, op2, CB_COND[kind])
    else:
        return None, "unsupported"
    if e is None or not address_is_real(e.address):
        return None, "rejected"
    e.kind = "cb"
    return e, "ok"


# --------------------------------------------------------------- GameShark --
# Types from mGBA's enum GBAGameSharkType, for raw (v1/v2, unencrypted) codes.
#   3 ASSIGN_LIST, 6 PATCH, 8 BUTTON, E IF_RANGE, F HOOK   not expressible
GS_COND = {0: OPT_EQ, 1: OPT_NE, 2: OPT_LE, 3: OPT_GE}


def decode_gameshark(op1: int, op2: int) -> tuple[Optional[Entry], str]:
    kind = op1 >> 28
    addr = op1 & 0x0FFFFFFF
    if kind == 0x0:
        # 8-bit write. mGBA's own detector docks a code whose operand has bits
        # above the width it claims; here that is a hard reject, because it is
        # the main thing separating a raw code from an encrypted word. It is
        # not a return, because the SP/v3 reading below gets what falls out.
        e = None if op2 & 0xFFFFFF00 else _lane(addr, 1, op2 & 0xFF)
    elif kind == 0x1:
        if op2 & 0xFFFF0000:
            return None, "rejected"
        e = _lane(addr, 2, op2 & 0xFFFF)
    elif kind == 0x2:
        e = _lane(addr, 4, op2)
    elif kind == 0xD:
        if op1 == 0xDEADFACE:
            return None, "encrypt"       # reseeds: the rest of the file is v1
        if op2 & 0xFFCF0000:
            return None, "rejected"
        e = _cond(addr, op2 & 0xFFFF, GS_COND[(op2 >> 20) & 3])
    else:
        return None, "unsupported"
    if e is None or not address_is_real(e.address):
        # Type 0 is the only nibble the SP/v3 dialect uses, so it is the only
        # one worth a second reading before the code is thrown away.
        if kind == 0x0:
            return decode_arv3(op1, op2)
        return None, "rejected"
    e.kind = "gs"
    return e, "ok"


# ------------------------------------------------ GameShark SP / AR v3 --
# The width lives in the second nibble instead of the type, and the address is
# an EWRAM offset rather than a bus address. See "A third dialect" in the module
# docstring for why this is only ever tried after the v1/v2 reading has failed.
ARV3_WIDTH = {0x0: 1, 0x2: 2, 0x4: 4}

# EWRAM is 256 KB and the bus mirrors it the whole way across 0x02000000 to
# 0x02FFFFFF, so an exporter is free to write any mirror of an address and the
# hardware lands on the same byte. Folding here means the word this emits is
# the canonical one, which is what the RTL and gba_cheats are compared against.
EWRAM_BASE = 0x2000000
EWRAM_MASK = 0x3FFFF


def decode_arv3(op1: int, op2: int) -> tuple[Optional[Entry], str]:
    """Decode one `0WAAAAAA VVVVVVVV` code, or say why not."""
    if op1 >> 28 != 0x0:
        return None, "unsupported"
    width = ARV3_WIDTH.get((op1 >> 24) & 0xF)
    if width is None:
        return None, "unsupported"
    # The same width check the v1/v2 types get, and for the same reason: an
    # operand wider than the code claims is the cheapest tell of a random word.
    if op2 >> (8 * width):
        return None, "rejected"
    e = _lane((op1 & 0xFFFFFF & EWRAM_MASK) + EWRAM_BASE, width, op2)
    if e is None or not address_is_real(e.address):
        return None, "rejected"
    e.kind = "ar"
    return e, "ok"


def decode_pair(t1: str, t2: str) -> tuple[Optional[Entry], str]:
    """Decode one (first word, second word) token pair."""
    op1 = int(t1, 16)
    if len(t2) == 4:
        return decode_codebreaker(op1, int(t2, 16))
    if len(t2) == 8:
        return decode_gameshark(op1, int(t2, 16))
    return None, "unsupported"


# ------------------------------------------------------------------ groups --
@dataclass
class Group:
    index: int
    entries: list[Entry] = field(default_factory=list)
    desc: Optional[str] = None
    enabled: bool = True      # from cheatN_enable; no key at all means on
    truncated: bool = False   # more entries than the table could ever hold


@dataclass
class Stats:
    """Counters the core exposes on the bridge, plus reporting-only extras."""
    byte_count: int = 0
    entry_count: int = 0      # words actually pushed to gba_cheats
    group_count: int = 0      # cheats actually pushed
    dropped: dict = field(default_factory=dict)

    def drop(self, why: str) -> None:
        self.dropped[why] = self.dropped.get(why, 0) + 1


HEX = "0123456789abcdefABCDEF"


# A budget large enough not to be one. Reading a file to choose from is not
# reading it to run: a picker has to show every cheat in a libretro file, and
# stopping at the store's size would make everything past the first couple of
# dozen invisible and unpickable. The limit belongs where the selection is
# written, not where the file is read.
NO_LIMIT = 1 << 30


def parse(data: bytes, max_entries: int = MAX_ENTRIES,
          max_group_entries: int = MAX_GROUP_ENTRIES,
          browse: bool = False
          ) -> tuple[list[Group], Stats]:
    """Decode a `.cht` into cheats the engine can run.

    `browse` changes the question from "what will the core run" to "what does
    this file contain", which is what a picker needs and a converter must not
    have. Two kinds of cheat are returned that are otherwise dropped:

    - **Cheats whose `_enable` key says false.** A converted file holds exactly
      the cheats that should run, and gba_cheats runs every entry it is handed
      regardless of any flag, so carrying a disabled one would turn it on. A
      picker offers to change "off", and a libretro file has almost everything
      off, so without this it shows a nearly empty list.
    - **Cheats no code of which survived.** A converter has nothing to write
      for one. A picker still has something to show: the description is what
      the user recognises, and a row that is present and greyed says "this
      cheat cannot be expressed" where a missing row says nothing at all.

    Both come back with the fact recorded rather than acted on: `enabled` says
    what the key said, and a cheat with nothing usable has an empty `entries`.
    """
    """Model of src/fpga/core/cheat_loader.sv.

    The lexer is the GB core's, unchanged: a rolling seven byte window matches
    `_code`, `_desc` and `_enable`, and a keyword only counts as a key once '='
    follows it. Free text is never tokenized. That last rule is not fussiness;
    `_code` is a substring of `notes_codecs`, and a comment reading
    `# _code means "Facade"` would otherwise emit a patch out of a description.

    What is new here is the back end. A cheat's entries are buffered rather
    than emitted as they are parsed, because whether the cheat is on is only
    known once `cheatN_enable` has been read, and libretro writes that key
    after the codes it applies to. The buffer is flushed when the enable key
    arrives, when the next cheat starts, or at end of file.
    """
    groups: list[Group] = []
    st = Stats()

    hist = b""
    pend_code = pend_desc = pend_enable = False
    armed_code = armed_desc = armed_enable = False
    in_str = collecting = False
    desc_buf: Optional[list[str]] = None
    last_desc: Optional[str] = None

    tok = ""
    tok_ovf = False
    op1: Optional[str] = None       # first word of a pair, waiting for the second

    cur: Optional[Group] = None     # the cheat being collected
    pending: Optional[Group] = None # collected, waiting on its enable key
    cond_at: Optional[int] = None   # index in cur.entries of an unpaired condition
    group_done = False              # this cheat is closed to further entries
    encrypted = False               # a CB_ENCRYPT / DEADFACE line was seen

    def flush() -> None:
        """Commit the cheat waiting on its enable key."""
        nonlocal pending
        if pending is None:
            return
        g, pending = pending, None
        if not g.enabled and not browse:
            st.drop("disabled")
            return
        if not g.entries and not browse:
            return
        # A cheat is all or nothing. Pushing the part of it that fits would
        # apply half a cheat, and could leave a condition as the last entry in
        # the table, where its skip_next suppresses an unrelated cheat. Cheats
        # are taken in file order, and a later, smaller one can still fit.
        if g.truncated or len(g.entries) > max_entries - st.entry_count:
            st.drop("no room")
            return
        g.index = len(groups)
        groups.append(g)
        st.entry_count += len(g.entries)
        st.group_count += 1

    def close_group() -> None:
        """End of a `_code` value: park the cheat until its enable is known."""
        nonlocal cur, cond_at, group_done
        if cur is not None:
            # A condition with nothing after it to guard is dropped along with
            # everything the same cheat put after it.
            if cond_at is not None:
                del cur.entries[cond_at:]
            cur.desc = last_desc
            if cur.entries or browse:
                if not cur.entries:
                    st.drop("no codes")
                flush()             # in case a previous cheat is still parked
                pending_set(cur)
            else:
                st.drop("no codes")
        cur, cond_at, group_done = None, None, False

    def pending_set(g: Group) -> None:
        nonlocal pending
        pending = g

    def add(e: Entry) -> None:
        """Append one decoded entry to the cheat being collected."""
        nonlocal cond_at, group_done
        assert cur is not None
        if group_done:
            return
        if cond_at is not None and e.optype != OPT_ALWAYS:
            # Two conditions in a row. gba_cheats' skip_next suppresses exactly
            # one entry, so a chain cannot be expressed; the alternative is a
            # write that runs when it should not.
            del cur.entries[cond_at:]
            st.drop("condition chain")
            cond_at, group_done = None, True
            return
        if len(cur.entries) >= max_group_entries:
            # More entries than the table could ever hold. Mark it so the
            # flush drops the whole cheat rather than the part that fits.
            st.drop("cheat too long")
            cur.truncated = True
            group_done = True
            return
        cur.entries.append(e)
        if e.optype != OPT_ALWAYS:
            cond_at = len(cur.entries) - 1
        else:
            cond_at = None

    def take(t: str) -> None:
        """One complete hex token, paired up with its neighbour."""
        nonlocal op1, encrypted
        n = len(t)
        if op1 is not None:
            if n in (4, 8):
                e, why = decode_pair(op1, t)
                if e is not None:
                    e.raw = f"{op1.upper()}+{t.upper()}"
                op1 = None
                if why == "encrypt":
                    encrypted = True
                elif e is not None and not encrypted:
                    add(e)
                elif why != "ignored":
                    st.drop("encrypted" if encrypted else why)
                return
            op1 = None                  # unusable partner: resynchronise
        if n == 8:
            op1 = t
        elif n in (12, 16):
            take(t[:8])
            take(t[8:])
        elif n:
            st.drop("bad token")

    for byte in data:
        st.byte_count += 1
        ch = chr(byte)
        if collecting:
            if ch in HEX:
                if len(tok) == 16:
                    tok_ovf = True
                else:
                    tok += ch
            else:
                if tok and not tok_ovf:
                    take(tok)
                elif tok_ovf:
                    st.drop("bad token")
                    op1 = None
                tok, tok_ovf = "", False
                if ch in ('"', "\n"):
                    collecting = False
                    op1 = None
                    close_group()
        elif in_str:
            if desc_buf is not None:
                if ch == '"':
                    last_desc = "".join(desc_buf)
                    desc_buf = None
                else:
                    desc_buf.append(ch)
            if ch == '"':
                in_str = False
        elif armed_enable and (ch.isalpha() or ch.isdigit()):
            if pending is not None and ch not in ("t", "T", "1"):
                pending.enabled = False
            flush()
            armed_enable = False
            pend_code = pend_desc = pend_enable = False
            hist = b""
        elif ch == '"':
            hist = b""
            pend_code = pend_desc = pend_enable = False
            if armed_code:
                flush()             # the previous cheat had no enable key: on
                collecting = True
                tok, tok_ovf, op1 = "", False, None
                cur = Group(0)
                cond_at, group_done = None, False
            else:
                in_str = True
                if armed_desc:
                    desc_buf = []
            armed_code = armed_desc = armed_enable = False
        else:
            hist = (hist + bytes([byte]))[-7:]
            if hist == b"_enable":
                pend_enable, pend_code, pend_desc = True, False, False
                armed_code = armed_desc = armed_enable = False
            elif hist[-5:] == b"_code":
                pend_code, pend_desc, pend_enable = True, False, False
                armed_code = armed_desc = armed_enable = False
            elif hist[-5:] == b"_desc":
                pend_desc, pend_code, pend_enable = True, False, False
                armed_code = armed_desc = armed_enable = False
            elif ch == "=":
                armed_code, armed_desc, armed_enable = (
                    pend_code, pend_desc, pend_enable)
                pend_code = pend_desc = pend_enable = False
            elif ch not in (" ", "\t"):
                pend_code = pend_desc = pend_enable = False

    if collecting:                  # value never closed: end of file ends it
        if tok and not tok_ovf:
            take(tok)
        close_group()
    flush()
    return groups, st


def words(groups: list[Group]) -> list[int]:
    """The 128-bit words, in the order the RTL pushes them into gba_cheats."""
    return [e.word for g in groups for e in g.entries]


def entries(groups: list[Group]) -> list[Entry]:
    return [e for g in groups for e in g.entries]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    for path in argv[1:]:
        groups, st = parse(open(path, "rb").read())
        print(f"== {path}")
        for g in groups:
            print(f"  [{g.index}] {g.desc or '(no description)'}"
                  f"{'  (truncated)' if g.truncated else ''}")
            for e in g.entries:
                print(f"        {e.kind} {e!r}  {e.word:032x}")
        print(f"  {st.group_count} cheats, {st.entry_count} entries, "
              f"{st.byte_count} bytes")
        if st.dropped:
            print("  dropped: " + ", ".join(f"{k}={v}" for k, v in
                                            sorted(st.dropped.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
