# SPDX-License-Identifier: GPL-3.0-or-later
"""No-Intro's DAT files: what a dumped cartridge is, and what to call it.

The dumper core writes a ROM image to the card under a name read out of the
cartridge header, which is not the game's name and is not even unique. This
module turns the bytes back into an identity: hash the file, look the SHA-1 up
in the DAT the user downloaded, and the entry hands over the canonical
filename along with it. Identifying a dump and naming it are the same lookup.

**We do not ship the data.** It is No-Intro's work and their terms, so the app
reads a file the user fetched from <https://datomatic.no-intro.org/> and says
plainly when there is none. Nothing here downloads anything.

What comes down from that site is one zip per system holding one XML file, so
`load()` takes the zip as it arrives; a `.dat` the user extracted anyway works
too. Two of the three flavours on offer parse, and both are welcome, because
people click whichever button they land on:

  Standard      names the clone parent with a numeric `cloneofid` that
                resolves against each game's `id`.
  Parent-Clone  names it directly in `cloneof`, and carries no `id` at all.

Same graph, said two ways, so both are normalised to `Entry.parent` holding
the parent's game name and callers never learn which file they were given.
Measured on the Game Boy exports of 2026-08-27, both at the same version: 2001
entries against 2295, agreeing on every field of the 1997 they share. The
extra 298 are aftermarket and homebrew, so Parent-Clone is the wider net.

The third flavour, **DB Export, is not a DAT and does not parse**: its file has
both a `<header>` and a `<datafile>` element at the top level, which is not
well-formed XML, and it is four times the size for fields identification does
not use. It loads as empty, like any other file that is not a DAT.

**Everything is keyed on SHA-1.** It is the only hash present in every entry of
every flavour: sha256 covers 96% of the Standard Game Boy DAT, 41% of Game Boy
Color, 30% of Game Boy Advance, and none of Parent-Clone at all, so an index
built on it would fail quietly on real data.

The files are 0.8-1.4 MB and a few thousand entries each. They are parsed once
into a dict and never walked again.

Hostile input is the user's own download, but it arrives over the internet, so
it is treated as untrusted: the archive is refused above a size ceiling before
anything is decompressed, and `xml.etree.ElementTree` resolves no external
entities (it raises on them) while expat caps how far an internal one may
expand. A file that fails any of that is an empty result, not an exception.

Standard library only, and nothing here touches Tk or the network.
"""
from __future__ import annotations

import enum
import hashlib
import os
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, field

# Pocket platform id -> the system name No-Intro publishes under. Written out
# rather than derived from card.KNOWN, which maps the same ids to libretro's
# cheat directories: the two registries agree on these strings today only
# because libretro named its directories after No-Intro's systems, and this
# module should not break if either side renames one.
SYSTEMS = {
    "gb":  "Nintendo - Game Boy",
    "gbc": "Nintendo - Game Boy Color",
    "gba": "Nintendo - Game Boy Advance",
}

# The two flavours that parse. Which one was loaded is recorded for the sake of
# a status line; nothing in a lookup depends on it.
STANDARD = "standard"
PARENT_CLONE = "parent-clone"

# The biggest of these observed is 3.5 MB uncompressed. A ceiling this far
# above that refuses a decompression bomb without ever refusing a real DAT, and
# it is checked against the header before a byte is decompressed.
MAX_DAT = 64 * 1024 * 1024

CHUNK = 1024 * 1024


class Outcome(enum.Enum):
    """What a lookup found. Only the first of these is good news.

    UNKNOWN is not an error. It means the SHA-1 is not in the DAT, which
    covers a bad dump, a revision No-Intro has not catalogued, and a
    reproduction cartridge. Nothing here can tell those apart, so the app must
    not offer a guess dressed up as an answer.

    NO_DATA is the fourth because loading is per-system and partial: with no
    Game Boy Color DAT loaded, a Game Boy Color dump was never looked for, and
    reporting that as UNKNOWN would blame the cartridge for a missing download.
    """
    MATCH = "match"
    UNKNOWN = "unknown"
    MISMATCH = "mismatch"
    NO_DATA = "no data"


@dataclass(frozen=True)
class Entry:
    """One catalogued dump, reduced to what an identification needs."""
    name: str                 # canonical filename, verbatim from <rom name>
    game: str                 # the game's name, without an extension
    size: int
    crc32: str                # eight lowercase hex digits
    sha1: str                 # forty lowercase hex digits
    parent: str | None = None  # game name of the clone parent, if it is one


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    entry: Entry | None = None
    disagreed: tuple[str, ...] = ()   # the fields that did, on a MISMATCH

    @property
    def name(self) -> str | None:
        """The canonical filename, when there is one to be sure of."""
        return self.entry.name if self.outcome is Outcome.MATCH else None


@dataclass(frozen=True)
class Dat:
    """One system's catalogue, indexed by SHA-1.

    An empty one is a normal object rather than a failure: a missing download,
    a corrupt file and a system nobody has fetched yet all arrive here, and
    `if not dat` is how the caller tells.
    """
    system: str = ""
    name: str = ""                    # as the file's own header gives it
    version: str = ""                 # No-Intro's dated version string
    flavour: str = ""
    entries: dict[str, Entry] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def lookup(self, sha1: str, size: int | None = None,
               crc32: str | None = None) -> Result:
        """Identify one dump. Pass size and CRC32 to have them checked.

        The entry is carried back on a mismatch as well as on a match, because
        the useful thing to say is which game the file nearly is.
        """
        entry = self.entries.get(_hex(sha1))
        if entry is None:
            return Result(Outcome.UNKNOWN)
        disagreed = []
        if size is not None and size != entry.size:
            disagreed.append("size")
        if crc32 is not None and _hex(crc32).zfill(8) != entry.crc32:
            disagreed.append("crc32")
        if disagreed:
            return Result(Outcome.MISMATCH, entry, tuple(disagreed))
        return Result(Outcome.MATCH, entry)


@dataclass
class Catalog:
    """The DATs loaded so far, which is normally not all of them.

    All three systems are wanted before the feature is much use, and any one of
    them is useful on its own for what it covers, so nothing here treats a gap
    as an error. `missing()` is what a status line asks.
    """
    dats: dict[str, Dat] = field(default_factory=dict)

    def add(self, path: str, system: str = "") -> str | None:
        """Load a downloaded file. Returns the system it covers, or None.

        The system is read out of the DAT's own header, so the user can hand
        over the three zips in any order without saying what each one is. It
        is only guessed from the header: a file whose header names a system
        this app does not handle is refused rather than filed under a system
        it might not be, unless the caller says which one it is.
        """
        dat = load(path, system)
        if not dat or not dat.system:
            return None
        self.dats[dat.system] = dat
        return dat.system

    def get(self, system: str) -> Dat:
        return self.dats.get(system) or Dat(system=system)

    def loaded(self) -> tuple[str, ...]:
        return tuple(s for s in SYSTEMS if self.dats.get(s))

    def missing(self) -> tuple[str, ...]:
        return tuple(s for s in SYSTEMS if not self.dats.get(s))

    def lookup(self, system: str, sha1: str, size: int | None = None,
               crc32: str | None = None) -> Result:
        dat = self.dats.get(system)
        if not dat:
            return Result(Outcome.NO_DATA)
        return dat.lookup(sha1, size, crc32)


@dataclass(frozen=True)
class Digest:
    """What one pass over a dump yields, and what a lookup wants."""
    size: int
    crc32: str
    sha1: str


def digest(path: str) -> Digest | None:
    """Hash a dump. None if it cannot be read.

    Both hashes come off a single pass, because the file is being read anyway
    and the CRC32 is not optional: the core displayed one at dump time, over
    the bytes leaving the reader, and never confirmed they reached the card.
    Comparing the two is a read-back check nothing else in the system can do.
    """
    size = 0
    crc = 0
    sha = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            while True:
                block = f.read(CHUNK)
                if not block:
                    break
                size += len(block)
                crc = zlib.crc32(block, crc)
                sha.update(block)
    except OSError:
        return None
    return Digest(size, "%08x" % crc, sha.hexdigest())


def load(path: str, system: str = "") -> Dat:
    """Parse a downloaded DAT, from its zip or from the bare XML.

    Anything that is not a DAT this module understands - missing, unreadable,
    not XML, the wrong flavour, the wrong system - comes back as an empty Dat.
    A user who has not downloaded anything yet is in a normal state, and so is
    one who handed over the wrong file.
    """
    data = _bytes(path)
    if data is None:
        return Dat(system=system)
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return Dat(system=system)
    if root.tag != "datafile":
        return Dat(system=system)
    return _index(root, system)


def system_for(name: str) -> str:
    """The platform id a DAT's header name belongs to, or "".

    Longest match wins, because "Nintendo - Game Boy Color" starts with
    "Nintendo - Game Boy" and the shorter one would swallow it. Trailing
    flavour tags such as "(Parent-Clone)" are what makes this a prefix test
    rather than an equality one.
    """
    hits = [p for p, prefix in SYSTEMS.items() if name.startswith(prefix)]
    return max(hits, key=lambda p: len(SYSTEMS[p]), default="")


def _index(root: ET.Element, system: str) -> Dat:
    """Walk the games once, building the index and the id map together.

    The id map exists only for the Standard flavour, where the parent link is
    a number that resolves against another game's `id`. It is built on the way
    past rather than in a second pass, which is the whole cost of accepting
    both flavours.
    """
    entries: dict[str, Entry] = {}
    names: dict[str, str] = {}     # game id -> game name, Standard only
    pending: list[tuple[str, str]] = []   # (sha1, parent id) to resolve after
    by_name = False

    for game in root.iterfind("game"):
        name = game.get("name") or ""
        game_id = game.get("id")
        if game_id:
            names[game_id] = name
        parent = game.get("cloneof")
        parent_id = game.get("cloneofid")
        by_name = by_name or bool(parent)
        # Every rom is indexed rather than only the first. No entry in any of
        # the five real files has more than one, but a game element is allowed
        # several and dropping them silently would be the wrong failure.
        for rom in game.iterfind("rom"):
            sha1 = _hex(rom.get("sha1") or "")
            if len(sha1) != 40:
                continue
            entries[sha1] = Entry(
                # The DAT's filename is used exactly as given, extension and
                # all. It is not the game name plus the system's extension:
                # the Game Boy Advance DAT holds three entries ending .bin and
                # two ending .gbc, all of them boot ROMs.
                name=rom.get("name") or name,
                game=name,
                size=int(rom.get("size") or 0),
                crc32=_hex(rom.get("crc") or "").zfill(8),
                sha1=sha1,
                parent=parent,
            )
            if parent_id:
                pending.append((sha1, parent_id))

    for sha1, parent_id in pending:
        parent = names.get(parent_id)
        # A dangling id is real: two Game Boy Advance entries point at parents
        # the file does not contain. That costs the clone link and nothing
        # else, so the entry is kept unparented rather than dropped.
        if parent:
            entries[sha1] = _reparent(entries[sha1], parent)

    # The flavour is what the file did, not what its header called itself. A
    # named parent settles it; failing that, ids are what only Standard
    # carries, and every one of its 2001 Game Boy games has one.
    name = root.findtext("header/name") or ""
    return Dat(
        system=system or system_for(name),
        name=name,
        version=root.findtext("header/version") or "",
        flavour=PARENT_CLONE if by_name else (STANDARD if names else ""),
        entries=entries,
    )


def _reparent(entry: Entry, parent: str) -> Entry:
    return Entry(entry.name, entry.game, entry.size, entry.crc32, entry.sha1,
                 parent)


def _bytes(path: str) -> bytes | None:
    """The XML inside a downloaded zip, or a bare file's contents."""
    try:
        if zipfile.is_zipfile(path):
            return _unzip(path)
        if os.path.getsize(path) > MAX_DAT:
            return None
        with open(path, "rb") as f:
            return f.read()
    except (OSError, zipfile.BadZipFile):
        return None


def _unzip(path: str) -> bytes | None:
    with zipfile.ZipFile(path) as zf:
        members = [
            i for i in zf.infolist()
            if not i.is_dir()
            # Resource forks from a zip somebody rebuilt on a macOS desktop.
            # They sit alongside the real member and are not XML.
            and not i.filename.startswith("__MACOSX/")
            and not os.path.basename(i.filename).startswith("._")
        ]
        named = [i for i in members
                 if i.filename.lower().endswith((".dat", ".xml"))]
        wanted = named or members
        if not wanted:
            return None
        # Biggest first, so a zip holding the DAT next to a readme still finds
        # the DAT. The declared size decides whether to decompress at all.
        pick = max(wanted, key=lambda i: i.file_size)
        if pick.file_size > MAX_DAT:
            return None
        return zf.read(pick)


def _hex(value: str) -> str:
    """A hash or checksum as this module keys it: lowercase, no whitespace.

    The real files are already lowercase throughout, but other tools that
    write this schema are not, and a case difference would read as a mismatch.
    """
    return value.strip().lower()
