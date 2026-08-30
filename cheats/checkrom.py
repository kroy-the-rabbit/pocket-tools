#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check that a Game Genie code's compare byte matches the ROM it is aimed at.

A Game Genie code carries a compare byte, and the patch only fires when the
byte already at that address matches. Codes published for one revision of a
game therefore go silent on another, with no error and nothing to see: the
cheat is loaded, enabled, and simply never triggers. This says whether the
compare byte is present in the ROM at all, and in which bank.

    tools/cheats/checkrom.py <rom> <cht>
    tools/cheats/checkrom.py <rom> 006-EFB-3BE C97DFB087

Addresses below $4000 live in the fixed bank 0. $4000-$7FFF is the switchable
window, so every bank is a candidate and a code is usable if any of them holds
the compare byte.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chtparse  # noqa: E402

BANK = 0x4000


def banks_matching(rom: bytes, address: int, compare: int) -> list[int]:
    """Banks whose byte at this address equals the compare byte."""
    if address < BANK:                       # fixed bank 0
        return [0] if address < len(rom) and rom[address] == compare else []
    off = address - BANK
    hits = []
    for bank in range(len(rom) // BANK):
        pos = bank * BANK + off
        if pos < len(rom) and rom[pos] == compare:
            hits.append(bank)
    return hits


def report(rom: bytes, desc: str, code) -> bool:
    raw = code.raw
    if code.kind != "gg" or code.compare is None:
        where = "RAM" if code.address >= 0xA000 else "ROM"
        print(f"  --   {raw:<12} {desc[:40]:<42} {where} write, no compare byte")
        return True
    hits = banks_matching(rom, code.address, code.compare)
    mark = "ok" if hits else "MISS"
    where = (f"bank {hits[0]}" if len(hits) == 1
             else f"{len(hits)} banks" if hits else "no bank")
    print(f"  {mark:<4} {raw:<12} {desc[:40]:<42} "
          f"${code.address:04X} expects {code.compare:02X}, found in {where}")
    return bool(hits)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    rom = open(argv[0], "rb").read()
    print(f"rom: {os.path.basename(argv[0])}  {len(rom)} bytes, "
          f"{len(rom) // BANK} banks")

    items: list[tuple[str, object]] = []
    for arg in argv[1:]:
        if os.path.exists(arg):
            for g in chtparse.parse(open(arg, "rb").read()):
                for c in g.codes:
                    items.append((g.desc or "", c))
        else:
            c = chtparse.decode_token(arg.replace("-", ""))
            if c is None:
                print(f"  ??   {arg}: not a code")
                continue
            items.append(("", c))

    bad = 0
    for desc, code in items:
        if not report(rom, desc, code):
            bad += 1
    print()
    print(f"{len(items) - bad}/{len(items)} codes match this ROM"
          + ("" if not bad else f", {bad} will never fire"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
