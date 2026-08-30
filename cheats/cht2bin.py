#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert libretro GBA `.cht` files into the packed `.chtbin` the core loads.

    tools/cheats/cht2bin.py game.gba.cht            -> game.gba.chtbin
    tools/cheats/cht2bin.py -d out/ *.cht
    cat game.cht | tools/cheats/cht2bin.py - -o game.chtbin

Why this exists
---------------
`cheat_loader.sv` parses `.cht` ASCII on the FPGA, and that parser is what
stopped the design fitting: 441 ALMs of tokeniser and hex decode that the
fitter then amplified into 1,285 ALMs of growth (docs/HANDOFF.md, runs C to N).
Doing the parse here leaves the on-chip loader a byte counter and a shift
register, because the file holds exactly the 128-bit words `gba_cheats`
consumes, in the order it consumes them.

Why this is a wrapper and not a parser
--------------------------------------
`gbacht.py` is the reference model the RTL was verified against, word for word,
over all 513 files of the libretro GBA corpus. It is therefore not "a parser we
could reuse", it is *the* parse, already validated, and the only safe move is
to keep it and add packing around it. Everything this file does beyond calling
it is the layout in docs/CHEATBIN.md.

What the format is, in one sentence
-----------------------------------
A 16-byte header, `"GBAC"` and version 1 and a uint16 entry count, followed by
that many 16-byte entries, each one a 128-bit `gba_cheats` word stored
little-endian. Entry order is significant: a conditional is a compare entry
immediately followed by the entry it guards, so nothing here sorts, dedupes or
reorders what the parse produced.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

try:
    import gbacht
except ImportError:                 # imported from outside tools/cheats
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import gbacht

MAGIC = b"GBAC"
VERSION = 1
HEADER_SIZE = 16
ENTRY_SIZE = 16

# gba_cheats has 32 slots (CHEATCOUNT). More than that in the file is not a
# parse error, it is the converter failing to make a choice the hardware then
# has to make blind, so the choice is made here and reported.
MAX_ENTRIES = gbacht.MAX_ENTRIES

# Bits 63:32 and 127:104 are not read by gba_cheats and the format requires
# them to be zero, so that a future revision can use them and an old file stays
# readable. Checked rather than masked off: a set bit means the word did not
# come from gbacht.Entry.word, and quietly clearing it would hide that.
RESERVED_MASK = (0xFFFFFFFF << 32) | (((1 << 24) - 1) << 104)


def header(entry_count: int) -> bytes:
    """The 16-byte header. See docs/CHEATBIN.md.

    The magic is an interlock, not decoration. The previous format *was* a
    plain `.cht`, so a stale one landing in the slot is a real scenario, and a
    loader that shifted ASCII into the cheat table would poke the game with
    whatever the letters happened to decode to.
    """
    if not 0 <= entry_count <= 0xFFFF:
        raise ValueError(f"entry count {entry_count} does not fit a uint16")
    return (MAGIC + bytes((VERSION, 0))
            + entry_count.to_bytes(2, "little") + bytes(8))


def pack(words: list[int]) -> bytes:
    """Header plus one 16-byte little-endian entry per word."""
    if len(words) > MAX_ENTRIES:
        raise ValueError(f"{len(words)} entries exceeds the {MAX_ENTRIES} "
                         f"gba_cheats can hold")
    out = bytearray(header(len(words)))
    for i, w in enumerate(words):
        if w >> 128:
            raise ValueError(f"entry {i} is wider than 128 bits")
        if w & RESERVED_MASK:
            raise ValueError(f"entry {i} sets reserved bits: {w:032x}")
        out += w.to_bytes(ENTRY_SIZE, "little")
    return bytes(out)


def unpack(blob: bytes) -> list[int]:
    """The inverse of pack(), for tests and for inspecting a converted file.

    Follows the loader's rules rather than being strict: wrong magic or version
    means zero entries, and a truncated final entry is discarded rather than
    padded out. A wrong file must behave as no cheats, never as garbage cheats.
    """
    if len(blob) < HEADER_SIZE or blob[:4] != MAGIC or blob[4] != VERSION:
        return []
    count = int.from_bytes(blob[6:8], "little")
    count = min(count, (len(blob) - HEADER_SIZE) // ENTRY_SIZE, MAX_ENTRIES)
    return [int.from_bytes(blob[HEADER_SIZE + i * ENTRY_SIZE:
                                HEADER_SIZE + (i + 1) * ENTRY_SIZE], "little")
            for i in range(count)]


@dataclass
class Result:
    blob: bytes
    cheats: int = 0             # cheats that made it into the file
    entries: int = 0            # 128-bit words written
    dropped: int = 0            # entries cut by the file's own 32-entry cap
    reasons: dict = field(default_factory=dict)   # gbacht's drop counters

    def summary(self, name: str, out: str) -> str:
        s = (f"{name}: {self.cheats} cheats, {self.entries} entries, "
             f"{self.dropped} dropped at the {MAX_ENTRIES}-entry cap, "
             f"{len(self.blob)} bytes -> {out}")
        if self.reasons:
            s += ("\n  not converted: "
                  + ", ".join(f"{k}={v}"
                              for k, v in sorted(self.reasons.items())))
        return s


def convert(data: bytes, parse_limit: int = MAX_ENTRIES) -> Result:
    """Parse `.cht` bytes and pack the words that come out.

    Only enabled cheats reach the file, and that is gbacht's doing, not ours:
    its `flush()` drops a group whose `cheatN_enable` key said anything other
    than true, and a cheat with no enable key at all counts as on. Re-checking
    `Group.enabled` here would either be dead code or, if it ever disagreed,
    two rules for the same question.

    `parse_limit` is the budget gbacht fills entries into. It exists so a
    test can reach the cap enforced here, which is otherwise dead code,
    because gbacht already refuses to hand back more than 32 entries. Cheats
    it sheds for want of room show up in `reasons` as "no room", not in
    `dropped`, which counts only what this cap cut.
    """
    groups, st = gbacht.parse(data, max_entries=parse_limit)
    words = gbacht.words(groups)                # order is load-bearing
    cheats, dropped = len(groups), 0
    if len(words) > MAX_ENTRIES:
        # Cutting at exactly 32 could split a conditional pair, leaving a
        # compare entry last in the table where its skip_next lands on an
        # unrelated cheat, and losing the write it guarded. Whole cheats go
        # instead, which is the same all-or-nothing rule gbacht applies when
        # it runs out of room.
        while len(words) > MAX_ENTRIES:
            dropped += len(groups[-1].entries)
            words = words[:-len(groups[-1].entries)]
            groups = groups[:-1]
        cheats = len(groups)
    return Result(pack(words), cheats, len(words), dropped, dict(st.dropped))


def out_path(src: str, outdir: str | None) -> str:
    """`foo.gba.cht` -> `foo.gba.chtbin`, which is the name APF asks for.

    The Pocket clones data slot 7's filename from slot 0 and appends the slot
    extension, so only the last extension may change.
    """
    base = os.path.splitext(src)[0] + ".chtbin"
    return os.path.join(outdir, os.path.basename(base)) if outdir else base


def convert_one(src: str, dst: str | None, outdir: str | None) -> int:
    """One input file to one output file. Returns a process exit status."""
    try:
        data = sys.stdin.buffer.read() if src == "-" else open(src, "rb").read()
    except OSError as e:
        print(f"cht2bin: {src}: {e.strerror or e}", file=sys.stderr)
        return 1
    try:
        res = convert(data)
    except Exception as e:                      # noqa: BLE001
        print(f"cht2bin: {src}: {e}", file=sys.stderr)
        return 1

    if res.dropped:
        print(f"cht2bin: {src}: {res.dropped} entries past the "
              f"{MAX_ENTRIES}-entry table were dropped, along with the cheats "
              f"they belong to", file=sys.stderr)

    # Piped input has no name to derive an output from, so without -o it goes
    # to stdout and the tool can sit in the middle of a pipe.
    if dst is None and src != "-":
        dst = out_path(src, outdir)
    try:
        if dst is None:
            sys.stdout.buffer.write(res.blob)
            sys.stdout.buffer.flush()
        else:
            with open(dst, "wb") as f:
                f.write(res.blob)
    except OSError as e:
        print(f"cht2bin: {dst or '<stdout>'}: {e.strerror or e}",
              file=sys.stderr)
        return 1
    print(res.summary(src, dst or "<stdout>"), file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Convert libretro .cht cheat files to packed .chtbin.")
    ap.add_argument("inputs", nargs="+", metavar="input.cht",
                    help="`.cht` files, or `-` for stdin")
    ap.add_argument("-o", metavar="output.chtbin",
                    help="output file; one input only (default: the input "
                         "with its extension replaced by .chtbin)")
    ap.add_argument("-d", "--outdir", metavar="DIR",
                    help="write outputs into DIR, keeping their names")
    args = ap.parse_args(argv)

    if args.o and len(args.inputs) > 1:
        ap.error("-o takes a single input")
    if args.o and args.outdir:
        ap.error("-o and --outdir are alternatives")
    if args.outdir:
        os.makedirs(args.outdir, exist_ok=True)

    # Every file is attempted even after one fails, because a batch conversion
    # of a cheat library should not stop at the first unreadable file.
    return max(convert_one(src, args.o, args.outdir) for src in args.inputs)


if __name__ == "__main__":
    raise SystemExit(main())
