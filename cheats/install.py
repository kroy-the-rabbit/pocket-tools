#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Install libretro cheat files next to ROMs on a Pocket SD card.

APF names data slot 7 by taking the slot-0 filename and *appending* this slot's
extension, so `Zelda.gbc` picks up `Zelda.gbc.cht` with no menu interaction.
This finds the matching file in the libretro database, copies it into place, and
prints which menu toggle ends up controlling which cheat.

    tools/cheats/install.py /run/media/$USER/pocket/Assets/gbc/common
    tools/cheats/install.py "…/common/Zelda.gbc" --dry-run
    tools/cheats/install.py "…/common/Zelda.gbc" --pick 2

Which cheats are on is decided by the file: each cheat carries an `enable` key,
and the core reads it. The menu has a single global switch. Cheats installed by
this tool are written enabled.
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import json
import shutil
import sys
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(os.path.dirname(HERE), "cheatgui"))
import chtparse  # noqa: E402
import db        # noqa: E402

ROM_EXT = {".gb", ".gbc"}
CORE_FOR_EXT = {".gbc": "budude2.GBC", ".gb": "budude2.GB"}


def norm(name: str) -> str:
    """Strip extension, region/dump tags and punctuation for fuzzy matching."""
    name = os.path.splitext(os.path.basename(name))[0]
    name = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", name)
    name = re.sub(r"[^a-z0-9]+", " ", name.lower())
    return " ".join(name.split())


def db_files() -> list[str]:
    if not db.available():
        sys.exit(f"no cheat database at {db.db_dir()}.\n"
                 "Fetch one with the picker's Update button, or "
                 "cheats/init-db.sh in a checkout.")
    root = db.db_dir()
    return [os.path.join(d, f)
            for d, _, fs in os.walk(root) for f in fs if f.endswith(".cht")]


def rank(rom: str, cands: list[str]) -> list[tuple[float, str]]:
    target = norm(rom)
    scored = []
    for p in cands:
        cand = norm(p)
        score = difflib.SequenceMatcher(None, target, cand).ratio()
        if cand == target:
            score = 1.0
        elif cand.startswith(target) or target.startswith(cand):
            score = max(score, 0.95)
        scored.append((score, p))
    scored.sort(key=lambda t: (-t[0], len(t[1])))
    return scored


def describe(path: str, ext: str) -> None:
    groups = chtparse.parse(open(path, "rb").read())
    if not groups:
        print("    (no decodable codes in this file)")
        return
    for g in groups:
        codes = ", ".join(f"{c.address:04X}={c.value:02X}" for c in g.codes[:4])
        if len(g.codes) > 4:
            codes += f", +{len(g.codes) - 4} more"
        print(f"    [{'on ' if g.enabled else 'off'}] "
              f"{g.desc or '(no description)'}  [{codes}]")
    on = sum(1 for g in groups if g.enabled)
    print(f"    ({len(groups)} cheats, {on} enabled; limit is {chtparse.MAX_GROUPS})")


def final_groups(src: str, only: list[int] | None, top: int) -> list:
    """The cheats that will end up in the installed file, in their final order.

    Selection uses the 1-based numbers this tool prints, which count only
    decodable cheats, so what you pick is what you get. Indices are renumbered
    to match the file that gets written, because those indices are the mask bits
    the menu toggles.
    """
    groups = chtparse.parse(open(src, "rb").read())
    if not (only or top):
        return groups
    picked = ([g for n in only for g in groups if g.index == n - 1] if only
              else groups[:top])
    for i, g in enumerate(picked):
        g.index = i
    return picked


def render_cht(groups: list) -> str:
    lines = [f"cheats = {len(groups)}", ""]
    for i, g in enumerate(groups):
        codes = "+".join(c.raw for c in g.codes)
        desc = (g.desc or f"Cheat {i + 1}").replace('"', "'")
        lines += [f'cheat{i}_desc = "{desc}"',
                  f'cheat{i}_code = "{codes}"',
                  f"cheat{i}_enable = true", ""]
    return "\n".join(lines)


def sd_root(path: str) -> Optional[str]:
    """Walk up from a ROM to the SD card root (the folder holding Assets/Cores)."""
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if all(os.path.isdir(os.path.join(d, x)) for x in ("Assets", "Cores")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="+", help="ROM files, or folders holding ROMs")
    ap.add_argument("--dry-run", action="store_true", help="show matches, copy nothing")
    ap.add_argument("--pick", type=int, default=0, metavar="N",
                    help="use the Nth best match (1 = best) instead of the top one")
    ap.add_argument("--min-score", type=float, default=0.72,
                    help="reject matches below this similarity (default 0.72)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing .cht")
    ap.add_argument("--top", type=int, default=0, metavar="N",
                    help="write only the first N cheats, so they all get toggles")
    ap.add_argument("--only", default="", metavar="LIST",
                    help="write only these cheat numbers, e.g. 3,7,9 (in that order)")
    args = ap.parse_args()

    roms: list[str] = []
    for t in args.targets:
        if os.path.isdir(t):
            roms += [os.path.join(t, f) for f in sorted(os.listdir(t))
                     if os.path.splitext(f)[1].lower() in ROM_EXT]
        elif os.path.splitext(t)[1].lower() in ROM_EXT:
            roms.append(t)
        else:
            print(f"skipping {t}: not a .gb/.gbc ROM or a folder")
    if not roms:
        sys.exit("no ROMs found")

    cands = db_files()
    installed = skipped = 0
    for rom in roms:
        ext = os.path.splitext(rom)[1].lower()
        # APF appends the slot extension to the slot-0 filename (parameters
        # bit 2), so the file it looks for is "<rom filename>.cht", not the
        # ROM name with its extension replaced.
        dst = rom + ".cht"
        print(f"\n{os.path.basename(rom)}")
        scored = rank(rom, cands)
        idx = max(0, args.pick - 1)
        if idx >= len(scored) or scored[idx][0] < args.min_score:
            best = scored[0] if scored else (0.0, "")
            print(f"    no confident match (best {best[0]:.2f}: "
                  f"{os.path.basename(best[1])})")
            for s, p in scored[1:4]:
                print(f"      alt {s:.2f}: {os.path.basename(p)}")
            skipped += 1
            continue
        score, src = scored[idx]
        print(f"    match {score:.2f}: {os.path.basename(src)}")
        for s, p in scored[idx + 1:idx + 3]:
            print(f"      alt {s:.2f}: {os.path.basename(p)}  (--pick to choose)")
        describe(src, ext)
        if os.path.exists(dst) and not args.force and not args.dry_run:
            print(f"    {os.path.basename(dst)} already exists, use --force")
            skipped += 1
            continue
        sel = [int(x) for x in args.only.split(",") if x.strip()] if args.only else None
        groups = final_groups(src, sel, args.top)
        if sel or args.top:
            print(f"    trimmed to {len(groups)} cheats:")
            for i, g in enumerate(groups):
                print(f"      Cheat {i + 1}: {g.desc or '(no description)'}")
        if not args.dry_run:
            if sel or args.top:
                open(dst, "w").write(render_cht(groups))
            else:
                shutil.copyfile(src, dst)
            print(f"    -> {dst}")
        if not args.dry_run:
            installed += 1

    print(f"\n{installed} installed, {skipped} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
