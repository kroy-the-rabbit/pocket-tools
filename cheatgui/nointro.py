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

The third flavour, **DB Export, is not a DAT and does not parse**: its zip
holds an `.xml` rather than a `.dat`, and the file inside carries both a
`<header>` and a `<datafile>` element at the top level, which is not
well-formed XML, and it is four times the size for fields identification does
not use. It still loads as empty, but it no longer loads as *nothing*:
`Dat.problem` names it. Someone who went to the right page, picked the right
system and pressed the third of three buttons has made one small mistake, and
"no Game Boy data loaded" tells them none of that.

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
expand. A file that fails any of that is an empty result, not an exception,
carrying a `Problem` that says which failure it was.

Standard library only, and nothing here touches Tk or the network.
"""
from __future__ import annotations

import enum
import hashlib
import os
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, field, replace
from xml.parsers import expat

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

# expat's number for a second top-level element, which is the whole of what is
# wrong with a DB Export as far as a parser is concerned. Looked up by name so
# that the 9 it happens to be is not written down here.
JUNK_AFTER_DOCUMENT = expat.errors.codes[
    expat.errors.XML_ERROR_JUNK_AFTER_DOC_ELEMENT]


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


class Problem(enum.Enum):
    """Why a load came back holding nothing, for the callers that must say.

    A `Dat` is falsy whatever the reason and no caller is obliged to read
    this, which is what keeps the contract intact: an unreadable file is still
    not an exception, and `if not dat` still tells a lookup everything it
    needs.

    It is an enum rather than a sentence because this module cannot know
    whether the sentence lands in a dialog, in a one-line status bar or in a
    log, and prose written here would be wrapped wrong in at least one of
    them. The distinction belongs here; the wording belongs to the UI, which
    is the only side that knows its own width.

    DB_EXPORT is the member this enum exists for. Every other reason is the
    user's file being wrong; that one is the user being right about the site,
    the system and the day, and pressing the third of three buttons. The UI
    can then name the mistake and the fix - this is the DB Export, take the
    DAT or the Parent-Clone DAT instead - which is a sentence it could not
    write from an empty result.

    DAMAGED is deliberately kept apart from it. A file that fails to parse for
    some other reason is a broken or truncated download, and sending that user
    back to press a different button would be worse than saying nothing.
    """
    NONE = "none"                  # loaded, or empty with nothing to say
    MISSING = "missing"            # not there, unreadable, or refused by size
    DB_EXPORT = "db export"        # the third button: not a DAT, and not XML
    DAMAGED = "damaged"            # XML that does not parse, for other reasons
    NOT_A_DAT = "not a dat"        # parses, but the root is not <datafile>
    WRONG_SYSTEM = "wrong system"  # a real DAT, for a system this app has not


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
    `if not dat` is how the caller tells. `problem` is how it tells them
    apart, and NONE on an empty one means a datafile that parsed and simply
    held nothing this module could index.
    """
    system: str = ""
    name: str = ""                    # as the file's own header gives it
    version: str = ""                 # No-Intro's dated version string
    flavour: str = ""
    entries: dict[str, Entry] = field(default_factory=dict)
    problem: Problem = Problem.NONE

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
        dat = self.take(path, system)
        return dat.system if dat else None

    def take(self, path: str, system: str = "") -> Dat:
        """`add()`, with the reason for a refusal still attached.

        Two methods rather than one because the two callers want different
        things. Anything walking a directory of downloads wants the system and
        nothing else, and `add()` stays exactly what it was for it. A caller
        that has to explain a refusal to a person wants the Dat instead,
        because the moment `Problem` is worth reading is the moment a file is
        refused.

        A refused file always comes back falsy, whatever was wrong with it, so
        that the explaining caller has one shape to handle and not two.
        """
        dat = load(path, system)
        if not dat:
            return dat
        if not dat.system:
            # This one parsed and is somebody's real DAT; it is only not ours.
            # Emptied on the way out so that a refusal looks the same here as
            # it does for a file that could not be read at all. The caller can
            # hand it back with a system if it knows better than the header.
            return replace(dat, entries={}, problem=Problem.WRONG_SYSTEM)
        self.dats[dat.system] = dat
        return dat

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
    one who handed over the wrong file. `Dat.problem` says which of those it
    was; nothing here raises, and nothing here writes the sentence about it.
    """
    source = _read(path)
    if source.data is None:
        return Dat(system=system, problem=Problem.MISSING)
    try:
        root = ET.fromstring(source.data)
    except ET.ParseError as error:
        return Dat(system=system, problem=_unparsable(source, error))
    if root.tag != "datafile":
        return Dat(system=system, problem=Problem.NOT_A_DAT)
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


@dataclass(frozen=True)
class _Source:
    """The bytes to parse, and the one thing their container knew about them.

    `xml_only` is a zip that offered an `.xml` and no `.dat` at all. It says
    nothing on its own - the member is read either way, because refusing a DAT
    over its extension would be the same obnoxious pedantry as refusing one
    over its flavour - but it is half of what identifies a DB Export, and it
    is free to notice while the member is being picked.
    """
    data: bytes | None
    xml_only: bool = False


def _read(path: str) -> _Source:
    """The XML inside a downloaded zip, or a bare file's contents."""
    try:
        if zipfile.is_zipfile(path):
            return _unzip(path)
        if os.path.getsize(path) > MAX_DAT:
            return _Source(None)
        with open(path, "rb") as f:
            return _Source(f.read())
    except (OSError, zipfile.BadZipFile):
        return _Source(None)


def _unzip(path: str) -> _Source:
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
            return _Source(None)
        # Biggest first, so a zip holding the DAT next to a readme still finds
        # the DAT. The declared size decides whether to decompress at all.
        pick = max(wanted, key=lambda i: i.file_size)
        if pick.file_size > MAX_DAT:
            return _Source(None)
        xml_only = bool(named) and not any(
            i.filename.lower().endswith(".dat") for i in named)
        return _Source(zf.read(pick), xml_only)


def _unparsable(source: _Source, error: ET.ParseError) -> Problem:
    """Tell the wrong download apart from the broken one.

    The DB Export is known by two marks, either of which is enough and both of
    which are cheap: its zip holds a lone `.xml` where a `.dat` was expected,
    and its document opens with `<header>` where a DAT opens with `<datafile>`.
    The real 2026-08-27 Game Boy download carries both, and a copy the user
    extracted by hand still carries the second.

    Neither mark is consulted until the file has already failed to parse, and
    then only for the single expat error a second top-level element causes. A
    DAT that somebody rezipped as `.xml` parses perfectly well and never
    reaches here; a truncated download fails with a different error and is
    reported as what it is. Being wrong in this direction would send a user
    back to the download page over a file that was merely cut short, so the
    test stays the narrow one.

    The lone `.xml` is allowed to stand on its own because No-Intro ships an
    `.xml` for exactly one of the three buttons, and it is the check that
    would survive them reordering the two top-level elements.
    """
    if error.code != JUNK_AFTER_DOCUMENT:
        return Problem.DAMAGED
    if source.xml_only or _first_element(source.data or b"") == "header":
        return Problem.DB_EXPORT
    return Problem.DAMAGED


def _first_element(data: bytes) -> str:
    """The document element's tag, out of a file that does not parse.

    A pull parser gets it because a DB Export is well-formed right up to the
    point where it stops being one: the `<header>` opens and closes, and only
    the `<datafile>` after it is the error. Events already queued survive that
    failure, so the tag is still there to be read once the exception is
    caught. Both calls are guarded because either of them can be the one that
    raises, depending on where expat is holding the buffer when it gives up.
    """
    parser = ET.XMLPullParser(events=("start",))
    try:
        parser.feed(data)
    except ET.ParseError:
        pass
    try:
        for _, element in parser.read_events():
            return element.tag
    except ET.ParseError:
        pass
    return ""


def _hex(value: str) -> str:
    """A hash or checksum as this module keys it: lowercase, no whitespace.

    The real files are already lowercase throughout, but other tools that
    write this schema are not, and a case difference would read as a mismatch.
    """
    return value.strip().lower()
