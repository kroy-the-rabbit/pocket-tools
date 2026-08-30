# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage timings, for when the window goes quiet and nobody can say why.

Off unless POCKET_CHEATS_TIMING is set, and costs a perf_counter call either
way. It exists because "it is slow after a rescan" is a real report that no
amount of reading the code settled: the card scan measured instantly here and
the matching measured at a fifth of a second, neither of which is ten seconds,
so the answer has to come off the machine where it happens.

    POCKET_CHEATS_TIMING=1 pocket-cheats

Lines go to stderr, one per stage, longest offenders being the point.
A windowed build has no stderr, so they go through say and end up in its log.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager

import say

ON = bool(os.environ.get("POCKET_CHEATS_TIMING"))


@contextmanager
def stage(name: str, detail: str = ""):
    """Time a block and print it, if timing is on."""
    if not ON:
        yield
        return
    t = time.perf_counter()
    try:
        yield
    finally:
        ms = (time.perf_counter() - t) * 1000
        line = f"[timing] {ms:8.1f} ms  {name}"
        if detail:
            line += f"  ({detail})"
        say.err(line)


def note(message: str) -> None:
    if ON:
        say.err(f"[timing] {'':>8}     {message}")
