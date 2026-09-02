# SPDX-License-Identifier: GPL-3.0-or-later
"""GameHacking.org cheat archives, as MiSTer ships them.

One zip per game, named after the No-Intro ROM. Inside it, one `.gg` file per
cheat, named after the cheat, holding a run of 16-byte records. Each record is
**already** the 128-bit word `gba_cheats` consumes, so there is nothing to
decode: `.chtbin` is a 16-byte `GBAC` header followed by exactly these records.
An import is a selection and a header, and no arithmetic at all.

    Metroid - Zero Mission (USA).zip
        0 Game Time.gg                    32 bytes, two entries
        100 Completion Rate.gg            16 bytes, one entry
        Always Get Best Ending.gg         16 bytes, one entry

**Record layout, measured rather than assumed.** Four 32-bit little endian
words, most significant first: `[127:96] [95:64] [63:32] [31:0]`. That is what
`pocket-gba/tools/sim/fixtures/mister_007.words` documents, and it is the order
that puts 257 of 298 of Zero Mission's addresses inside EWRAM, IWRAM or IO.
The other order puts none of them there.

**The remaining 14% are not a bug here.** gamehacking.org's encoder does not
filter, so an encrypted GameShark v3 or Action Replay v3 code is run through
the plain decoder and emitted as a word that pokes nothing. `pocket-gba`'s
`docs/CHEATS.md` records the same thing from the other direction. Those codes
are kept, marked unusable, and never silently dropped: a cheat that will not
work is a different thing from a cheat that is missing.

**These files are other people's work.** The zip comment credits the people who
found the codes, and `attribution()` reads it so the app can show it. It is
carried, never stripped.
"""
from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field

# One entry. The same 16 bytes in the zip, in the file the core loads, and in
# the RTL's `cheat_in`.
RECORD = 16

# A zip this size is not a cheat archive. The real ones are tens of kilobytes;
# the cap is far above that and only refuses something absurd.
MAX_ZIP = 8 * 1024 * 1024

SUFFIX = ".gg"

# GBA only. The format is `gba_cheats`'s word, so there is nothing to apply to
# a Game Boy or a PC Engine, and offering it there would be a lie.
PLATFORMS = ("gba",)


@dataclass(frozen=True)
class Code:
    """One 128-bit word, shaped like the codes everything downstream holds.

    `raw` is the word as 32 hex digits. It is the identity `writer.key_of`
    uses, so a cheat keeps its identity across a re-import, and it is what a
    person would compare against the file if they ever had to.

    `address` and `value` are filled in because they can be, which lets the
    existing summary show something useful, but nothing is decoded back into
    code text: that direction does not exist and inventing it would be a
    guess.
    """
    raw: str
    word: int
    kind: str = "mister"
    address: int | None = None
    value: int | None = None
    compare: int | None = None
    bank: int | None = None

    @property
    def optype(self) -> int:
        return (self.word >> 96) & 0xF

    @property
    def usable(self) -> bool:
        """The address lands somewhere a GBA can be written.

        EWRAM, IWRAM and IO. An encrypted code decoded as plaintext lands
        essentially anywhere, and almost never in one of those.
        """
        a = self.address or 0
        return (0x02000000 <= a <= 0x0203FFFF
                or 0x03000000 <= a <= 0x03007FFF
                or 0x04000000 <= a <= 0x040003FE)


@dataclass
class Group:
    """One `.gg` file: a named cheat and the entries it costs."""
    index: int
    codes: list = field(default_factory=list)
    desc: str | None = None
    enabled: bool = False

    @property
    def usable(self) -> bool:
        """Any entry that pokes something real. All-or-nothing is the core's
        rule for fitting a cheat, but a group of pure garbage is worth saying
        so about rather than offering."""
        return any(c.usable for c in self.codes)


def _decode(record: bytes) -> Code:
    w = [int.from_bytes(record[i:i + 4], "little")
         for i in range(0, RECORD, 4)]
    word = (w[0] << 96) | (w[1] << 64) | (w[2] << 32) | w[3]
    return Code(raw=f"{word:032X}", word=word,
                address=(word >> 64) & 0x0FFFFFFF, value=word & 0xFFFFFFFF)


def looks_like_one(path: str) -> bool:
    """Cheap enough to ask before offering a file. Reads the listing only."""
    if not path.lower().endswith(".zip"):
        return False
    try:
        if os.path.getsize(path) > MAX_ZIP:
            return False
        with zipfile.ZipFile(path) as zf:
            entries = [i for i in zf.infolist()
                       if i.filename.lower().endswith(SUFFIX)]
            return bool(entries) and all(i.file_size % RECORD == 0
                                         and i.file_size > 0
                                         for i in entries)
    except (OSError, zipfile.BadZipFile):
        return False


def attribution(path: str) -> str:
    """Who found these codes, from the zip's own comment. "" if it has none."""
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.comment.decode("utf-8", "replace").strip()
    except (OSError, zipfile.BadZipFile):
        return ""


def read(path: str) -> list[Group]:
    """Every cheat in one archive, in the order the zip lists them.

    Order is kept because it is load-bearing downstream: a conditional is a
    compare entry immediately followed by the entry it guards, and adjacency
    is how `gba_cheats` expresses that. Sorting here would silently reattach
    a condition to a different cheat.
    """
    groups: list[Group] = []
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if not info.filename.lower().endswith(SUFFIX):
                continue
            data = zf.read(info)
            if not data or len(data) % RECORD:
                say_bad(info.filename, len(data))
                continue
            codes = [_decode(data[i:i + RECORD])
                     for i in range(0, len(data), RECORD)]
            desc = os.path.splitext(os.path.basename(info.filename))[0]
            groups.append(Group(index=len(groups), codes=codes, desc=desc))
    return groups


# A code token in a .cht written from an archive: one 128-bit word as 32 hex
# digits. Deliberately not a shape a libretro GBA code can take -- those are
# 8+8 hex pairs -- so the two can never be confused for each other.
_WORD = re.compile(r"^[0-9A-Fa-f]{32}$")
_LINE = re.compile(
    rb"^\s*cheat(\d+)_(desc|code|enable)\s*=\s*(.*?)\s*$", re.M)


def parse_cht(data: bytes) -> list | None:
    """Groups from a .cht this module wrote, or None if it is not one.

    The archive's words go into the .cht as 32 hex digits so the file beside
    the .chtbin still says what is installed. The libretro parser drops a
    token of that shape without a word, which is how a written selection came
    back empty and failed its own read-back check. This reads them.

    None, not an exception, when the file is an ordinary libretro one: this is
    a "is it mine" question asked before the normal parser runs.
    """
    fields: dict[int, dict[str, str]] = {}
    for m in _LINE.finditer(data):
        idx = int(m.group(1))
        key = m.group(2).decode()
        val = m.group(3).decode("utf-8", "replace").strip().strip('"\'')
        fields.setdefault(idx, {})[key] = val
    if not fields:
        return None
    tokens = [t for f in fields.values()
              for t in (f.get("code") or "").split("+") if t]
    if not tokens or not all(_WORD.match(t) for t in tokens):
        return None
    groups = []
    for n, (idx, f) in enumerate(sorted(fields.items())):
        codes = [Code(raw=t.upper(), word=int(t, 16),
                      address=(int(t, 16) >> 64) & 0x0FFFFFFF,
                      value=int(t, 16) & 0xFFFFFFFF)
                 for t in (f.get("code") or "").split("+") if t]
        groups.append(Group(index=n, codes=codes, desc=f.get("desc"),
                            enabled=(f.get("enable", "")).lower() == "true"))
    return groups


def say_bad(name: str, size: int) -> None:
    import say
    say.err(f"{name}: {size} bytes is not a whole number of "
            f"{RECORD}-byte entries; skipped")
