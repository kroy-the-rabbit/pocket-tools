# SPDX-License-Identifier: GPL-3.0-or-later
"""The per-game view the UI edits: cheat-database cheats plus what is installed.

A cheat file on the card may hold cheats that are not in the matched libretro
entry, because it was hand-written, taken from another source, or matched to a
different file last time. Those are shown too, already ticked, so that saving a
selection can never quietly discard cheats the user put there.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import cheatfile
import match
import prefs
import writer


@dataclass
class Entry:
    group: object            # chtparse.Group, or cheatfile.OpaqueGroup
    enabled: bool
    in_library: bool         # False = only present in the installed file
    platform: str = "gbc"    # decides whether the codes can be read at all

    @property
    def desc(self) -> str:
        return self.group.desc or "(no description)"

    @property
    def codes(self) -> str:
        return "+".join(c.raw for c in self.group.codes)

    @property
    def summary(self) -> str:
        """What the codes do, or failing that, what they say.

        For a system whose codes we cannot read there is nothing to summarise,
        so the codes themselves are shown. That is more use than a decoded
        address would be anyway, since it is the thing you would compare
        against wherever you found the cheat.
        """
        if not cheatfile.decoded(self.platform):
            return self.codes
        return " ".join(f"{c.address:04X}={c.value:02X}" for c in self.group.codes)

    @property
    def placeholder(self) -> bool:
        """Nothing usable: the libretro entry was a XX-style modifier.

        Only decodable systems can tell. Elsewhere a cheat is unusable only if
        it carried no code text at all, which parse_opaque already drops.
        """
        return not self.group.codes

    @property
    def applied(self) -> str:
        """How the core makes this cheat take effect, for the whole group.

        The two mechanisms behave differently enough to be worth showing. A
        written cheat puts the value where the game finds it by any route; an
        overridden read only satisfies reads the core can see, so a DMA copy or
        a cached value misses it.
        """
        if len(cheatfile.mechanisms(self.platform)) < 2:
            # One mechanism, or none we can name. A column carrying the same
            # word in every row says nothing, and on PC Engine every published
            # cheat is a RAM poke. The UI states that once, above the list.
            return ""
        kinds = {cheatfile.applied_by(c, self.platform) for c in self.group.codes}
        if not kinds:
            return ""
        if kinds == {"poke"}:
            return "written"
        if kinds == {"patch"}:
            return "patched"
        return "mixed"


@dataclass
class GameView:
    game: object             # card.Game
    source: str | None       # cheat file the entries came from
    entries: list[Entry]
    alternates: list         # match.Candidate
    pinned: bool = False     # source came from a remembered choice, not matching

    @property
    def enabled(self) -> list[Entry]:
        return [e for e in self.entries if e.enabled]

    @property
    def platform(self) -> str:
        return self.game.platform

    @property
    def applied_counts(self) -> tuple[int, int]:
        """(codes written into RAM, codes applied as a read override).

        Both zero where the codes cannot be read, which is not the same as a
        selection that does nothing: it is a selection we cannot say that about.
        """
        if not cheatfile.decoded(self.platform):
            return 0, 0
        written = patched = 0
        for e in self.enabled:
            for c in e.group.codes:
                if cheatfile.applied_by(c, self.platform) == "poke":
                    written += 1
                else:
                    patched += 1
        return written, patched

    @property
    def problems(self) -> list[str]:
        return writer.check([e.group for e in self.enabled], self.platform)

    def save(self) -> tuple[int, int]:
        return writer.write(self.game.cht_path, [e.group for e in self.enabled],
                            self.platform)


def load(game, source: str | None = None) -> GameView:
    """Build the view for one game, honouring a pinned source if there is one."""
    import timing
    with timing.stage("  match against the database"):
        alternates = match.rank(game.name, game.platform)
    pinned = False
    if source is None:
        source = prefs.get_source(game.path)
        if source and not os.path.exists(source):
            source = None
        pinned = source is not None
    if source is None:
        top = alternates[0] if alternates else None
        source = top.path if top and top.score >= 0.72 else None

    plat = game.platform
    with timing.stage("  read the matched cheat file"):
        lib = writer.load_library(source, plat) if source else []
    installed_groups = []
    if os.path.exists(game.cht_path):
        try:
            installed_groups = writer.load_library(game.cht_path, plat)
        except Exception:                                    # noqa: BLE001
            installed_groups = []
    installed_keys = {writer.key_of(g) for g in installed_groups}

    entries = [Entry(g, writer.key_of(g) in installed_keys, True, plat)
               for g in lib]
    lib_keys = {writer.key_of(g) for g in lib}
    # anything installed that the library file does not know about
    extra = [Entry(g, True, False, plat) for g in installed_groups
             if writer.key_of(g) not in lib_keys]
    return GameView(game, source, extra + entries, alternates, pinned)


def pin(game, cht_path: str | None) -> None:
    prefs.set_source(game.path, cht_path)
