#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Check the Game Genie decode against real ROM bytes.
#
# PLAN.md S9 asks that the decode be verified against a reference rather than
# written from memory. The only complete implementation reachable was Genesis
# Plus GX's decode_cheat() (gx/gui/cheats.c); SMS Power!'s page 403s and
# archive.org is blocked, so there is no second document to corroborate it.
#
# This is the corroboration instead, and it is better than a second document:
# a Game Genie compare byte is the ORIGINAL byte at the patched address, so a
# correct decode predicts the ROM. Run against a ROM set and the codes agree
# or they do not.
#
#   python3 tools/cheats/ggcht.py <romdir> <chtdir>
#
# Only addresses below 0x4000 are counted. Above that the Z80 address is
# bank-switched, so no fixed ROM offset exists to compare against and the test
# says nothing: measured rates were 95% in slot 0, 63% in slot 1 and 29% in
# slot 2, which is that effect and not a decode that degrades with address.

import os, re, sys, glob, collections


def regroup(code):
    """Split a cheat's code field into individual codes.

    924 codes in libretro's Game Gear corpus join the three groups of one code
    with '+' where '-' belongs: '058+BA8+E66' is one 9-digit code, not three.
    A further 502 use '+' between whole codes, which is genuine. Splitting on
    '+' first shatters the former, so split on both and regroup in threes.
    """
    parts = re.split(r"[+\-]", code.strip())
    if not all(re.fullmatch(r"[0-9A-Fa-f]{3}", p) for p in parts):
        return []
    return ["".join(parts[i:i + 3]) for i in range(0, len(parts), 3)]


def ror2(v):
    return ((v >> 2) | ((v & 3) << 6)) & 0xFF


def decode(code):
    """A 9-digit Game Genie code to (address, replace, compare).

    Digits are numbered 0..8 with the dashes removed. The compare byte is
    obfuscated and the address has one inverted nibble; the replacement value
    is plain.

      replace  d0 d1
      address  (d5 ^ 0xF) << 12 | d2 << 8 | d3 << 4 | d4
      compare  ror2(d6 << 4 | d8) ^ 0xBA

    d7 is discarded. That is not a transcription slip: keeping d6,d7 instead
    scores 5% against real ROM bytes where this scores 95%, and the two other
    pairings and the undisguised value score 0%.
    """
    d = [int(c, 16) for c in code]
    if len(d) != 9:
        return None
    return (((d[5] ^ 0xF) << 12) | (d[2] << 8) | (d[3] << 4) | d[4],
            (d[0] << 4) | d[1],
            ror2((d[6] << 4) | d[8]) ^ 0xBA)


def main(romdir, chtdir):
    roms = {os.path.splitext(os.path.basename(p))[0]: p
            for p in glob.glob(os.path.join(romdir, "*.gg"))}
    seen = collections.Counter()
    hit = collections.Counter()

    for cp in sorted(glob.glob(os.path.join(chtdir, "*.cht"))):
        stem = re.sub(r" \((Game Genie|Action Replay|Pro Action Replay)\)$", "",
                      os.path.basename(cp)[:-4])
        if stem not in roms:
            continue
        rom = open(roms[stem], "rb").read()
        text = open(cp, encoding="utf-8", errors="replace").read()
        for m in re.finditer(r'cheat\d+_code\s*=\s*"([^"]+)"', text):
            for code in regroup(m.group(1)):
                got = decode(code)
                if not got:
                    continue
                addr, _replace, compare = got
                if addr >= 0x4000 or addr >= len(rom):
                    continue
                seen[stem] += 1
                if rom[addr] == compare:
                    hit[stem] += 1

    total = sum(seen.values())
    good = sum(hit.values())
    if not total:
        print("no code matched a ROM in that directory", file=sys.stderr)
        return 1

    for game in sorted(seen):
        n, k = seen[game], hit[game]
        print(f"  {k:3d}/{n:-3d}  {game}" + ("" if n == k else "   <-- misses"))
    clean = sum(1 for g in seen if seen[g] == hit[g])
    print(f"\n{good}/{total} codes, {clean}/{len(seen)} games fully clean")

    # A wrong algorithm fails evenly across every game. Failures that cluster
    # into a few titles are those titles' codes being written against a
    # different dump, so the per-game count is the number that matters.
    return 0 if clean * 4 >= len(seen) * 3 else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__.strip().split("\n\n")[0], file=sys.stderr)
        print("usage: ggcht.py <romdir> <chtdir>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
