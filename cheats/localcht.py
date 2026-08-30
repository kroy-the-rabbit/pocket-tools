#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Your own cheat files, kept outside the libretro submodule.

The database is a git submodule: anything added to it is lost on the next
update and dirties the checkout meanwhile. Files here live in your data
directory instead, and the picker searches them first, so a file you wrote wins
an otherwise exact tie against the stock one.

Naming is the whole matching rule: call the file after the ROM and it will be
found. `Zelda (USA) (Rev 2).cht` matches `Zelda (USA) (Rev 2).gbc`.

    localcht.py list
    localcht.py new "Legend of Zelda, The - Link's Awakening DX (USA, Europe) (Rev 2)"
    localcht.py new "..." --from <a .cht to start from>
    localcht.py add "..." "999 Rupees" 9199ADC6+9109AEC6
    localcht.py check "..."            --rom <the ROM, to verify compare bytes>

Codes are whatever the core accepts: 8-digit GameShark, 6 or 9 digit Game
Genie, hyphens optional, several joined with '+' to make one cheat.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(os.path.dirname(HERE), "cheatgui"))
import cheatlib  # noqa: E402
import chtparse  # noqa: E402

NO_LIMIT = 1 << 30


def path_for(name: str) -> str:
    if not name.endswith(".cht"):
        name += ".cht"
    return os.path.join(cheatlib.local_dir(), name)


def read(path: str) -> list:
    if not os.path.exists(path):
        return []
    return chtparse.parse(open(path, "rb").read(), NO_LIMIT, NO_LIMIT)


def render(groups: list) -> str:
    lines = [f"cheats = {len(groups)}", ""]
    for i, g in enumerate(groups):
        desc = (g.desc or f"Cheat {i + 1}").replace('"', "'")
        lines += [f'cheat{i}_desc = "{desc}"',
                  f'cheat{i}_code = "{"+".join(c.raw for c in g.codes)}"',
                  f"cheat{i}_enable = {'true' if g.enabled else 'false'}", ""]
    return "\n".join(lines)


def show(groups: list, start: int = 0) -> None:
    for i, g in enumerate(groups, start):
        codes = " ".join(f"{c.address:04X}={c.value:02X}" for c in g.codes)
        how = {chtparse.applied_by(c) for c in g.codes}
        tag = "written" if how == {"poke"} else "patched" if how == {"patch"} else "mixed"
        print(f"  [{i}] {'on ' if g.enabled else 'off'} {tag:<8} "
              f"{(g.desc or '')[:40]:<42} {codes}")


def cmd_list(_args) -> int:
    d = cheatlib.local_dir()
    files = sorted(f for f in os.listdir(d) if f.endswith(".cht"))
    print(f"{d}\n")
    if not files:
        print("  (empty) - localcht.py new \"<ROM name>\" to start one")
        return 0
    for f in files:
        groups = read(os.path.join(d, f))
        print(f"  {len(groups):>3} cheats  {f}")
    return 0


def cmd_new(args) -> int:
    dst = path_for(args.name)
    if os.path.exists(dst) and not args.force:
        print(f"{dst} exists; --force to overwrite", file=sys.stderr)
        return 1
    if args.source:
        src = args.source
        if not os.path.exists(src):
            # a bare name: take it from the database
            hits = [p for p in cheatlib.files_for("gbc")
                    if os.path.basename(p).lower().startswith(src.lower())]
            if not hits:
                print(f"no cheat file starts with {src!r}", file=sys.stderr)
                return 1
            src = hits[0]
        shutil.copyfile(src, dst)
        groups = read(dst)
        print(f"copied {os.path.basename(src)} -> {dst}")
        print(f"{len(groups)} cheats, all kept as they were; edit and enable what you want")
    else:
        open(dst, "w").write("cheats = 0\n")
        print(f"created {dst}")
    return 0


def cmd_add(args) -> int:
    dst = path_for(args.name)
    groups = read(dst)
    parsed = chtparse.parse(
        f'cheat0_desc = "{args.desc}"\ncheat0_code = "{args.code}"\n'
        f"cheat0_enable = true\n".encode(), NO_LIMIT, NO_LIMIT)
    if not parsed or not parsed[0].codes:
        print(f"{args.code!r} has no usable code in it", file=sys.stderr)
        return 1
    groups.append(parsed[0])
    open(dst, "w").write(render(groups))
    print(f"{dst}: {len(groups)} cheats")
    show(read(dst)[-1:], len(groups) - 1)
    return 0


def cmd_check(args) -> int:
    dst = path_for(args.name)
    if not os.path.exists(dst):
        print(f"{dst} does not exist", file=sys.stderr)
        return 1
    groups = read(dst)
    print(f"{dst}: {len(groups)} cheats, "
          f"{sum(len(g.codes) for g in groups)} codes")
    show(groups)
    over = []
    if len(groups) > chtparse.MAX_GROUPS:
        over.append(f"{len(groups)} cheats, the core reads {chtparse.MAX_GROUPS}")
    n = sum(len(g.codes) for g in groups)
    if n > chtparse.MAX_CODES:
        over.append(f"{n} codes, the core stores {chtparse.MAX_CODES}")
    for o in over:
        print(f"  WARNING: {o}")
    if args.rom:
        print()
        import checkrom
        return checkrom.main([args.rom, dst])
    return 1 if over else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="what you have").set_defaults(fn=cmd_list)

    p = sub.add_parser("new", help="start a file for a ROM")
    p.add_argument("name")
    p.add_argument("--from", dest="source",
                   help="seed it from an existing .cht (path, or the start of a "
                        "database filename)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("add", help="append one cheat")
    p.add_argument("name")
    p.add_argument("desc")
    p.add_argument("code")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("check", help="show it, and verify against a ROM")
    p.add_argument("name")
    p.add_argument("--rom", help="verify Game Genie compare bytes against this ROM")
    p.set_defaults(fn=cmd_check)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
