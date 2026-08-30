#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Parse libretro .cht cheat files the same way src/gb/cheat_loader.sv does.

This is the golden reference for the RTL parser: the testbench feeds both this
and cheat_loader.sv the same bytes and compares the emitted codes.

Format (libretro-database/cht):

    cheats = 28

    cheat0_desc = "Infinite Health (3 Hearts)"
    cheat0_code = "010CAAC6"
    cheat0_enable = false

One cheat may carry several codes joined by '+' ("01XXADC6+010YAEC6"); they
share a single on/off toggle, so a cheat is one *group* and each group gets one
mask bit. Codes containing placeholder letters (XX, YY) are not valid hex and
are dropped; a group with no valid codes consumes no mask bit.

The parser only looks at values of keys ending in `_code`, never at free text.
Tokenizing the whole file would misread hex-looking words in descriptions
("Decade", "Facade", "Beaded" are all valid 6-digit Game Genie codes).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Iterator, Optional

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from ggdecode import Cheat, decode_game_genie, decode_gameshark  # noqa: E402

MAX_CODES = 32          # CODES entries; must match cheatcodes.sv
MAX_GROUPS = 32         # cheat groups (mask bits)

# Where src/gb/cheat_poker.sv can write. Must match CODES.pokeable() in
# src/gb/cheatcodes.sv: work RAM and high RAM, nothing else.
POKE_REGIONS = ((0xC000, 0xDFFF), (0xFF80, 0xFFFE))


def pokeable(address: int) -> bool:
    """True if cheat_poker can reach this address."""
    return any(lo <= address <= hi for lo, hi in POKE_REGIONS)


def applied_by(code) -> str:
    """How the core makes one code take effect.

    "poke"  - a GameShark RAM write, performed once a frame at vblank, so the
              game's own logic still sees the value and can clamp it.
    "patch" - the CPU's read is overridden. Right for Game Genie, which patches
              ROM, and the fallback for a GameShark code the poker cannot reach.
    """
    return "poke" if code.kind == "gs" and pokeable(code.address) else "patch"


@dataclass
class Group:
    index: int
    codes: list[Cheat] = field(default_factory=list)
    desc: Optional[str] = None
    enabled: bool = True      # from cheatN_enable; no key at all means on


def decode_token(tok: str) -> Optional[Cheat]:
    """Decode one whitespace/'+'-delimited token, or None if it isn't a code."""
    n = len(tok)
    try:
        if n == 8:
            c = decode_gameshark(tok)
        elif n in (6, 9):
            c = decode_game_genie(tok)
        else:
            return None
    except ValueError:
        return None
    c.raw = tok
    return c


def parse(data: bytes, max_codes: int = MAX_CODES,
          max_groups: int = MAX_GROUPS) -> list[Group]:
    """Byte-for-byte model of src/gb/cheat_loader.sv.

    Written as the same flat state machine as the RTL (rolling 7-byte keyword
    window, one token buffer, emit-on-delimiter) so the two can be compared line
    by line, not just by result.

    The limits default to the core's, because that is what makes this a model of
    the RTL. Raise them to read a file as a library rather than as something the
    core is about to run: a libretro file can hold hundreds of cheats, and
    stopping at 32 codes hides the rest from anyone choosing between them.
    """
    groups: list[Group] = []
    hist = b""
    # A keyword only counts once '=' follows it. Matching the bare characters is
    # not enough: `_code` is a substring of `notes_codecs`, and a comment saying
    # `# _code means "Facade"` would otherwise arm the collector and emit a
    # phantom Game Genie patch out of the text after it.
    pend_code = pend_desc = pend_enable = False
    armed_code = armed_desc = armed_enable = False
    in_str = collecting = False
    tok = ""
    tok_ovf = False
    cur_group = 0
    group_has_code = False
    last_group_ok = False
    code_count = 0
    group_count = 0
    last_desc: Optional[str] = None
    desc_buf: Optional[list[str]] = None
    cur: Optional[Group] = None

    for byte in data:
        ch = chr(byte)
        if collecting:
            if ch in "0123456789abcdefABCDEF":
                if len(tok) == 9:
                    tok_ovf = True
                else:
                    tok += ch
            elif ch == "-":
                pass                                  # cosmetic separator
            else:
                cheat = None if tok_ovf else decode_token(tok)
                room = code_count < max_codes and cur_group < max_groups
                if cheat and room:
                    assert cur is not None
                    cur.codes.append(cheat)
                    code_count += 1
                    group_has_code = True
                tok, tok_ovf = "", False
                if ch in ('"', "\n"):
                    collecting = False
                    if group_has_code:
                        assert cur is not None
                        cur.desc = last_desc
                        groups.append(cur)
                        cur_group += 1
                        group_count += 1
                        last_group_ok = True
                    else:
                        last_group_ok = False
                    cur, group_has_code = None, False
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
            # the value of a `cheatN_enable` key: the first word after it
            if last_group_ok and cur_group != 0 and ch not in ("t", "T", "1"):
                groups[cur_group - 1].enabled = False
            armed_enable = False
            pend_code = pend_desc = pend_enable = False
            hist = b""
        elif ch == '"':
            hist = b""
            pend_code = pend_desc = pend_enable = False
            if armed_code:
                collecting = True
                tok, tok_ovf = "", False
                group_has_code = False
                cur = Group(cur_group)
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
                # Only now is it a key. Whatever was pending becomes armed.
                armed_code, armed_desc, armed_enable = (
                    pend_code, pend_desc, pend_enable)
                pend_code = pend_desc = pend_enable = False
            elif ch not in (" ", "\t"):
                # Anything else between the keyword and '=' means it was not a
                # key, just those characters inside a longer word or a comment.
                pend_code = pend_desc = pend_enable = False

    assert group_count == len(groups)
    return groups


def enable_mask(groups: list[Group]) -> int:
    """The 32-bit mask the RTL drives from the file's enable keys."""
    m = 0
    for g in groups:
        if g.enabled:
            m |= 1 << g.index
    return m


def flatten(groups: list[Group]) -> Iterator[tuple[int, Cheat]]:
    """(group index, code) in the order the RTL shifts them into CODES."""
    for g in groups:
        for c in g.codes:
            yield g.index, c


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    for path in argv[1:]:
        groups = parse(open(path, "rb").read())
        print(f"== {path}")
        for g in groups:
            print(f"  [{g.index}] {'ON ' if g.enabled else 'off'} "
                  f"{g.desc or '(no description)'}")
            for c in g.codes:
                extra = f" compare={c.compare:#04x}" if c.compare is not None else ""
                print(f"        {c.kind.upper()} addr={c.address:#06x} value={c.value:#04x}{extra}")
        print(f"  {len(groups)} groups, {sum(len(g.codes) for g in groups)} codes, "
              f"enable_mask={enable_mask(groups):#010x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
