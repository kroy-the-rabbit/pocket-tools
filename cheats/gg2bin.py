#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# gg2bin - a libretro .cht to the .chtbin that cheat_binloader.sv consumes
#
# The parse is on the host for the reason pocket-gba measured: its on-FPGA
# ASCII parser fitted at 441 ALMs but grew that design by 1,285 and cost
# 0.54 ns of setup. Everything that used to be a decision in RTL is a byte in
# a file here. rtl/gg/cheat_binloader.sv is the other half of the contract and
# documents the format; this writes it.
#
#   python3 tools/cheats/gg2bin.py in.cht out.chtbin
#   python3 tools/cheats/gg2bin.py --all in.cht out.chtbin
#
# ------------------------------------------------------------ what it emits --
#
# Only cheats whose `cheatN_enable` says so, because the behaviour contract
# every sibling holds is that which cheats are on comes from the file and not
# from a menu. libretro ships the whole corpus with enable = false, so a
# straight conversion of an unedited file is an empty table, and that is
# correct rather than a bug: the core is meant to boot with nothing on.
# --all overrides it, for testing a decode against hardware without editing a
# file first.
#
# "true" and "1" are on, a missing key is on, everything else is off. That is
# rtl/gg/cheat_loader.sv's rule, and the two have to agree or a .cht and the
# .chtbin made from it would behave differently on the same core.
#
# ------------------------------------------------------------- the two kinds --
#
# Game Genie, XXX-XXX-XXX, a ROM read override. 3,585 in libretro's Game Gear
# corpus, plus 10 of the six-digit XXX-XXX form which is the same minus the
# compare. Decoded by ggcht.decode, which is checked against real ROM
# bytes; see that file.
#
# Pro Action Replay, xxAAAA-DD, a work RAM poke. 3,058 in the corpus. The
# leading two digits are 00 in every one of the 3,291 found, and 3,191 of the
# 3,195 that are not the placeholder 0000-0000 address C000-DFFF, which is the
# 8 KB of work RAM. So the shape is a constant prefix, a Z80 address and a
# byte, and cheat_poker takes the low 13 bits of the address.
#
# A code carrying X or ? placeholders is a "modifier" whose value the player
# was meant to choose. About a hundred of them. There is nothing to write, so
# they are skipped with a warning rather than guessed at.
#
# Fifteen files carry a character that is neither hex nor a placeholder, the
# letter O typed for a zero being most of them. Those are skipped too. O for 0
# is an obvious enough typo to be tempting, but a corrected address that is
# wrong writes into a running game once a frame, and the cost of skipping is
# one cheat that does nothing.

import re
import sys

from ggcht import decode as genie_decode

MAGIC = b"GGCH"
VERSION = 1
MAX_CODES = 32  # CODES' MAX_CODES, fixed by system.vhd's component declaration


def split_codes(field):
    """One `cheatN_code` field to a list of individual codes.

    The corpus joins groups with either '-' or '+' and is not consistent: 924
    codes write one 9-digit Game Genie code as '058+BA8+E66', while 502 use
    '+' to separate whole codes. Splitting on '+' alone shatters the former.
    So split on both, then regroup by the group width: threes are Game Genie,
    fours are Pro Action Replay.
    """
    parts = [p for p in re.split(r"[+\-]", field.strip().upper()) if p]
    if not parts:
        return []
    if not all(re.fullmatch(r"[0-9A-FX?]+", p) for p in parts):
        return None
    widths = {len(p) for p in parts}
    if widths == {3}:
        step = 3
    elif widths == {4}:
        step = 2
    else:
        # Mixed widths cannot be regrouped without guessing where one code
        # ends, and a wrong guess pokes an arbitrary address once a frame.
        return None
    return ["".join(parts[i:i + step]) for i in range(0, len(parts), step)]


def par_decode(code):
    """xxAAAA-DD, dashes already stripped, to (address, value)."""
    if len(code) != 8:
        return None
    return int(code[2:6], 16), int(code[6:8], 16)


def encode(addr, value, compare=None, poke=False):
    """One 16-byte entry. Field positions are cheat_binloader.sv's."""
    word = value & 0xFF
    word |= (addr & 0xFFFF) << 64
    if compare is not None:
        word |= (compare & 0xFF) << 32
        word |= 1 << 96
    if poke:
        word |= 1 << 127
    return word.to_bytes(16, "little")


def model(text, enable_all=False):
    """The ordered entry list and the accepted cheats' names.

    rtl/gg/cheat_loader.sv is the reference implementation of this; tools/sim
    diffs the two over the whole libretro corpus. An entry is
    ("G", address, replace, compare or None) or ("P", address, value), in the
    order the RTL pushes them.
    """
    entries = []
    titles = []
    warnings = []

    descs = dict(re.findall(r'cheat(\d+)_desc\s*=\s*"([^"]*)"', text))
    codes = dict(re.findall(r'cheat(\d+)_code\s*=\s*"([^"]*)"', text))
    enables = dict(re.findall(r"cheat(\d+)_enable\s*=\s*(\w+)", text))

    for n in sorted(codes, key=int):
        name = descs.get(n, f"cheat{n}")
        # A cheat with no enable key at all is on, which is what a hand-written
        # file listing nothing but codes wants, and is what the RTL does: it has
        # nothing to withdraw a staged cheat with. libretro writes the key for
        # every cheat in all 818 files, so the corpus never reaches this.
        if not enable_all and enables.get(n, "true").lower() not in ("true", "1"):
            continue

        parts = split_codes(codes[n])
        if parts is None:
            warnings.append(
                f"{name}: not two or three groups of hex, skipped ({codes[n]})")
            continue

        got = []
        for code in parts:
            if "X" in code or "?" in code:
                warnings.append(f"{name}: needs a player-chosen value, skipped ({code})")
                continue
            if len(code) == 9:
                d = genie_decode(code)
                if not d:
                    warnings.append(f"{name}: bad Game Genie code, skipped ({code})")
                    continue
                addr, replace, compare = d
                got.append(("G", addr, replace, compare))
            elif len(code) == 6:
                # The six-digit form is the nine-digit one without the compare
                # group, so it replaces unconditionally.
                d = [int(c, 16) for c in code]
                addr = ((d[5] ^ 0xF) << 12) | (d[2] << 8) | (d[3] << 4) | d[4]
                got.append(("G", addr, (d[0] << 4) | d[1], None))
            elif len(code) == 8:
                d = par_decode(code)
                if not d:
                    warnings.append(f"{name}: bad Pro Action Replay code, skipped ({code})")
                    continue
                addr, value = d
                if not 0xC000 <= addr <= 0xDFFF:
                    warnings.append(f"{name}: poke outside work RAM, skipped ({code} -> {addr:04X})")
                    continue
                got.append(("P", addr, value))
            else:
                warnings.append(f"{name}: unrecognised code length, skipped ({code})")

        if got:
            entries.extend(got)
            titles.append(name)

    # The ceiling truncates mid-cheat. Nothing here couples one entry to the
    # next, so half a cheat is half a cheat and not a hazard; see the RTL.
    if len(entries) > MAX_CODES:
        warnings.append(f"{len(entries)} entries, {MAX_CODES} is the ceiling; dropped the rest")
        entries = entries[:MAX_CODES]

    return entries, titles, warnings


def convert(text, enable_all=False):
    entries, _titles, warnings = model(text, enable_all)
    blob = b"".join(
        encode(e[1], e[2], compare=e[3]) if e[0] == "G"
        else encode(e[1], e[2], poke=True)
        for e in entries)
    header = MAGIC + bytes([VERSION, 0]) + len(entries).to_bytes(2, "little") + bytes(8)
    return header + blob, len(entries), warnings


def main(argv):
    enable_all = "--all" in argv
    argv = [a for a in argv if a != "--all"]
    if len(argv) != 3:
        print("usage: gg2bin.py [--all] <in.cht> <out.chtbin>", file=sys.stderr)
        return 2

    with open(argv[1], encoding="utf-8", errors="replace") as fh:
        blob, count, warnings = convert(fh.read(), enable_all)

    with open(argv[2], "wb") as fh:
        fh.write(blob)

    for w in warnings:
        print(f"  warning: {w}", file=sys.stderr)
    print(f"{count} entries, {len(blob)} bytes")
    if count == 0 and not enable_all:
        print("no cheat in that file is enabled; --all converts every one",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
