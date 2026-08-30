# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless view of what the GUI would show, for checking without a screen.

    tools/cheatgui/run.sh --list           every game
    tools/cheatgui/run.sh --list zelda     filtered by name
    tools/cheatgui/run.sh --list zelda -v  and each cheat, with how it applies

The `Nw/Mp` column counts codes written into RAM against codes applied as a
CPU read override.
"""
from __future__ import annotations

import os

import card as card_mod
import model
import say


def main(argv: list[str]) -> int:
    # -v also lists each enabled cheat and how the core applies it
    verbose = "-v" in argv
    cards = card_mod.find_cards()
    if not cards:
        say.out("no Pocket card found (needs Cores/ and Platforms/)")
        return 1
    c = cards[0]
    say.out(f"card: {c.root} [{c.label}]")
    want = [a for a in argv if not a.startswith("-")]
    for p in c.platforms():
        # platforms() no longer reads the systems, so ask for this one.
        c.fill(p)
        say.out(f"\n== {p.name} [{p.id}]  {len(p.games)} ROMs")
        for g in p.games:
            if want and not any(w.lower() in g.name.lower() for w in want):
                continue
            v = model.load(g)
            src = os.path.basename(v.source) if v.source else "NO MATCH"
            written, patched = v.applied_counts
            how = f"{written}w/{patched}p" if (written or patched) else "-"
            say.out(f"  {g.name[:52]:<54} {len(v.enabled):>2} on / "
                  f"{len(v.entries):>3} avail  {how:>8}  {src[:44]}")
            if verbose:
                for e in v.enabled:
                    say.out(f"        {e.applied:<8} {e.desc[:44]:<46} {e.summary}")
    return 0
