# SPDX-License-Identifier: GPL-3.0-or-later
"""Where cartridge dumps are filed, and the disposable index that describes them.

The library is a directory on the computer, chosen once and remembered in
prefs. The app does not invent a path: the cheat database lives under
~/.local/share because it is a cache nobody opens, and these are ROMs and
backups someone will go looking for with a file manager. So until a path is
set, everything here answers "not yet" rather than guessing.

The layout is fixed, and it is fixed now rather than when the first file
arrives, because filing dumps into a shape that later needs another directory
alongside them is a migration:

    <library>/
        roms/          canonical No-Intro names, extension and all
        cart-dumps/    originals, under the names the core gave them
        saves/         dated, immutable, one directory per cartridge
        index.json     the store; delete it and it rebuilds

There are no per-system directories. The canonical name carries the extension
and after enrichment the extension is right, so a gb/gbc/gba split would state
the system a second time, in the path, where it could disagree with the
filename. One authority is better than two, and the extension is the one the
rest of the world already reads.

Nothing in this module writes under saves/. The directory is created so that
when the dumper learns to back a save up nothing above it moves; what goes
inside it is deliberately a later decision, made against a real .sav rather
than guessed at now.

index.json lives in the library rather than beside prefs.json, because the
library is what it describes and the two should travel together: copying the
library copies its index, and moving the library to another machine does not
strand it. That is the whole of the export and the backup story.

**The index is a cache, not a source of truth.** Deleting it must lose nothing
but time, and rebuild() is what makes that true: it walks the library, re-hashes
what it finds, and produces the same rows. Everything a Row can hold is
therefore an *observation* -- something the bytes on disk can say again. A
*decision* -- an approval, a rejection, a cheat file the user overrode -- cannot
survive that walk, so it belongs in prefs.py with the app's other remembered
choices. Row is frozen with a fixed field list and from_dict() drops keys it
does not know, so a decision filed in here by mistake is gone by the next load
instead of quietly becoming load-bearing.

Rows are keyed by SHA-1, never by filename. prefs.get_source() keys on basename,
which is exactly what collides when two different cartridges both title
themselves ZELDA, and that collision is most of the reason this feature exists.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import zlib
from dataclasses import dataclass, replace
from typing import Callable, Iterator

import prefs
import say

# The index format. Bumped when a row means something different from what an
# older build would read it as -- not when a field is merely added, since an
# unknown key is dropped on load and a missing one reads as None.
VERSION = 1

ROMS = "roms"
CART_DUMPS = "cart-dumps"
SAVES = "saves"

# Created together, so a library is never half a library. saves/ is in here
# despite nothing writing to it: an empty directory is the cheapest possible way
# to promise that its contents will not have to move later.
SUBDIRS = (ROMS, CART_DUMPS, SAVES)

INDEX = "index.json"

# How much of a file to read at a time. Game Boy Advance cartridges reach 32 MB
# and the hash is computed over every byte, twice in the ordinary flow.
CHUNK = 1024 * 1024


# ------------------------------------------------------------------ the path --
def path() -> str | None:
    """The library directory, or None until the user has chosen one."""
    return prefs.get_library()


def set_path(root: str | None) -> None:
    """Remember a library. None forgets it; nothing on disk is touched."""
    prefs.set_library(root)


def chosen() -> bool:
    return path() is not None


def roms_dir(root: str) -> str:
    return os.path.join(root, ROMS)


def dumps_dir(root: str) -> str:
    return os.path.join(root, CART_DUMPS)


def saves_dir(root: str) -> str:
    return os.path.join(root, SAVES)


def index_path(root: str) -> str:
    return os.path.join(root, INDEX)


def create(root: str) -> str:
    """Make the layout, and hand back the root. Doing it twice is harmless.

    Called before anything is filed rather than at the moment a path is chosen,
    so that pointing the app at a directory is not itself a write.
    """
    for sub in SUBDIRS:
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    return root


def ready(root: str) -> bool:
    """True if the layout is there. A library missing a directory is not one."""
    return all(os.path.isdir(os.path.join(root, sub)) for sub in SUBDIRS)


# --------------------------------------------------------------- the hashing --
def hashes(file_path: str) -> tuple[str, str]:
    """SHA-1 and CRC32 of a file, in one pass over it.

    Both at once because the file is large and is already being read. SHA-1 is
    the identity: it is the only hash present in every entry of every No-Intro
    DAT flavour. CRC32 is corroboration -- the dumper core displayed one on
    screen at dump time, and a disagreement means the bytes changed between the
    FPGA and the card, which is a read-back check the core itself cannot do.

    CRC32 comes back as eight lowercase hex digits, the form the DAT writes it
    in, so comparing the two is a string comparison rather than a conversion
    somebody can get wrong.
    """
    sha = hashlib.sha1()
    crc = 0
    with open(file_path, "rb") as f:
        while chunk := f.read(CHUNK):
            sha.update(chunk)
            crc = zlib.crc32(chunk, crc)
    return sha.hexdigest(), f"{crc & 0xFFFFFFFF:08x}"


def sha1_of(file_path: str) -> str:
    return hashes(file_path)[0]


# ----------------------------------------------------------------- the store --
@dataclass(frozen=True)
class Row:
    """One dump, keyed by the SHA-1 of its bytes.

    Every field is an observation: re-reading the library reproduces it. The
    ordinary flow leaves two files for one dump -- the canonical copy under
    roms/ and the card original under cart-dumps/, the same bytes and so the
    same SHA-1 -- which is why one row names both rather than there being a row
    per file.

    The last four fields are what a DAT said about this SHA-1. They are None
    until something asks it, and asking again is all a rebuild has to do to get
    them back. Nothing here is a decision; see the module docstring.
    """
    sha1: str
    size: int
    crc32: str
    filed: str
    rom: str | None = None          # name under roms/, canonical
    dump: str | None = None         # name under cart-dumps/, the core's own
    title: str | None = None        # the No-Intro game name
    system: str | None = None       # the DAT that held it: gb, gbc, gba
    region: str | None = None
    clone_of: str | None = None     # the parent's name, for the cheat fallback

    def rom_path(self, root: str) -> str | None:
        return os.path.join(roms_dir(root), self.rom) if self.rom else None

    def dump_path(self, root: str) -> str | None:
        return os.path.join(dumps_dir(root), self.dump) if self.dump else None

    def to_dict(self) -> dict:
        return {"sha1": self.sha1, "size": self.size, "crc32": self.crc32,
                "filed": self.filed, "rom": self.rom, "dump": self.dump,
                "title": self.title, "system": self.system,
                "region": self.region, "clone_of": self.clone_of}

    @classmethod
    def from_dict(cls, sha1: str, d: dict) -> "Row":
        """A row as read back, with keys we do not know dropped.

        The SHA-1 comes from the key rather than from the body, so the two
        cannot disagree about which dump this is. Unknown keys are dropped
        rather than carried, which is what stops a decision written in here by
        hand, or by a later mistake, from surviving to be relied on.
        """
        return cls(sha1=sha1,
                   size=int(d.get("size") or 0),
                   crc32=d.get("crc32") or "",
                   filed=d.get("filed") or "",
                   rom=d.get("rom"), dump=d.get("dump"),
                   title=d.get("title"), system=d.get("system"),
                   region=d.get("region"), clone_of=d.get("clone_of"))


class Index:
    """The rows, in memory: a dict keyed by SHA-1, with a version on the file."""

    def __init__(self, rows: dict[str, Row] | None = None) -> None:
        # No version attribute: an Index in memory is always this build's
        # schema, because load() refuses to make one out of any other. Carrying
        # a number that can only ever hold one value invites reading it as
        # though it could hold another.
        self.rows: dict[str, Row] = dict(rows or {})

    def __len__(self) -> int:
        return len(self.rows)

    def __contains__(self, sha1: str) -> bool:
        return sha1 in self.rows

    def __iter__(self) -> Iterator[Row]:
        """Rows in SHA-1 order, so walking the index is stable across runs."""
        return iter([self.rows[k] for k in sorted(self.rows)])

    def get(self, sha1: str) -> Row | None:
        return self.rows.get(sha1)

    def put(self, row: Row) -> None:
        """File a row. Only a Row goes in, and that is the point.

        There is deliberately no put_field and no extras dict: the only way to
        record something new about a dump is to add a field to Row, and adding
        one to Row is where somebody has to notice whether it survives a
        rebuild. Anything that does not is a decision and belongs in prefs.
        """
        if not isinstance(row, Row):
            raise TypeError("the index holds Row objects, nothing else")
        self.rows[row.sha1] = row

    def drop(self, sha1: str) -> bool:
        return self.rows.pop(sha1, None) is not None

    def to_dict(self) -> dict:
        return {"version": VERSION,
                "dumps": {k: self.rows[k].to_dict() for k in sorted(self.rows)}}


def load(root: str) -> Index:
    """The index in this library, or an empty one.

    A missing, unreadable or unrecognised file all read as empty rather than
    raising. That is not leniency for its own sake: the file is a cache, an
    empty index costs a rebuild and nothing else, and there is no state here
    worth taking the app down over.
    """
    try:
        with open(index_path(root)) as f:
            data = json.load(f)
    except FileNotFoundError:
        return Index()
    except Exception:                                        # noqa: BLE001
        say.err(f"{index_path(root)} is unreadable; it will be rebuilt")
        return Index()
    if not isinstance(data, dict):
        return Index()

    version = data.get("version", 0)
    if version != VERSION:
        # Older or newer, the honest answer is the same one. Rows written to a
        # schema this build does not know are not worth guessing at when the
        # library itself can be asked again, and guessing is how a cache starts
        # disagreeing with the thing it caches. A version of 0 is a file written
        # before the field existed, which only a hand-edit can produce.
        say.err(f"{INDEX} is version {version}, not {VERSION}; "
                "it will be rebuilt")
        return Index()

    dumps = data.get("dumps")
    if not isinstance(dumps, dict):
        return Index()
    rows = {sha1: Row.from_dict(sha1, body)
            for sha1, body in dumps.items() if isinstance(body, dict)}
    return Index(rows)


def save(root: str, index: Index) -> str:
    """Write the index, atomically, and hand back its path.

    The same .tmp-then-os.replace prefs.py uses: a reader sees either the whole
    old file or the whole new one, and a crash mid-write leaves the old index
    intact rather than a truncated one that would read as empty.
    """
    os.makedirs(root, exist_ok=True)
    dest = index_path(root)
    tmp = dest + ".tmp"
    with open(tmp, "w") as f:
        json.dump(index.to_dict(), f, indent=2)
    os.replace(tmp, dest)
    return dest


# --------------------------------------------------------------- the rebuild --
def _files(root: str, place: str) -> Iterator[tuple[str, str]]:
    """(name, full path) under one of the library's directories.

    Walked rather than listed, with the name kept relative to that directory, so
    a file somebody tucked into a subdirectory of their own is still seen. The
    layout is flat, but the index reports what is there and not what ought to
    be. Dotfiles and half-written .tmp files are skipped: neither is a dump.
    """
    base = os.path.join(root, place)
    for dirpath, subdirs, files in os.walk(base):
        subdirs[:] = sorted(d for d in subdirs if not d.startswith("."))
        for name in sorted(files):
            if name.startswith(".") or name.endswith(".tmp"):
                continue
            full = os.path.join(dirpath, name)
            yield os.path.relpath(full, base).replace(os.sep, "/"), full


def rebuild(root: str, enrich: Callable[[Row], Row] | None = None) -> Index:
    """Walk the library, re-hash it, and hand back a fresh index.

    This is what makes deleting index.json cost time and nothing else, so it
    reads only the files and never the old index. Anything the result cannot
    express was never safe to keep here in the first place.

    `enrich` is the DAT lookup, handed in rather than imported, because this
    module knows where files live and nothing about what is in them. It takes a
    row and returns one; identification arriving later is exactly why the four
    enrichment fields default to None instead of being required.

    saves/ is not walked. A save is not a dump, it has no row, and the shape of
    that directory is not decided yet.
    """
    index = Index()
    for place, field in ((ROMS, "rom"), (CART_DUMPS, "dump")):
        for name, full in _files(root, place):
            try:
                sha1, crc32 = hashes(full)
                stat = os.stat(full)
            except OSError as e:
                # A file that cannot be read is reported and skipped, never
                # dropped from the library and never written over: the app does
                # not act on bytes it could not see.
                say.err(f"cannot read {full}: {e}")
                continue
            filed = datetime.date.fromtimestamp(stat.st_mtime).isoformat()
            row = index.get(sha1)
            if row is None:
                row = Row(sha1=sha1, size=stat.st_size, crc32=crc32, filed=filed)
            elif filed < row.filed:
                # The earliest of a dump's files is when it entered the library,
                # which is the date the collision dialog shows.
                row = replace(row, filed=filed)
            if getattr(row, field) is None:
                row = replace(row, **{field: name})
            # else: two files, same directory, same bytes. They are copies of
            # each other, so the first by name stands for both and the duplicate
            # needs no row of its own.
            index.put(row)
    if enrich is not None:
        for row in list(index):
            index.put(enrich(row))
    return index


def open_index(root: str, enrich: Callable[[Row], Row] | None = None) -> Index:
    """The index, rebuilt and written when there was not a usable one.

    The ordinary entry point. A library whose index was deleted, or which was
    filled by hand, comes back describing itself rather than empty.
    """
    index = load(root)
    if not index and os.path.isdir(root):
        index = rebuild(root, enrich)
        if index:
            # An empty library is not written to. There is nothing to cache,
            # and an index.json is not something to create as a side effect of
            # being asked a question.
            save(root, index)
    return index
