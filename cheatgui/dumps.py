# SPDX-License-Identifier: GPL-3.0-or-later
"""What the dumper core left on the card: reading it, naming it, importing it.

The core reads a cartridge and writes a ROM image into
`/Assets/carttools/common/`, flat, with no sidecar and under a name taken from
a fixed offset in the cartridge header. That name is close to worthless -- it
is not the game's name, it carries stray manufacturer-code bytes, and two
different cartridges routinely produce the same one, which is how a real card
lost a dump. This module is the other half of that: it hashes what is there,
asks No-Intro what it is, and files it under the name the rest of the world
uses.

**Nothing here opens a window or asks a question.** Everything about a dump is
computed first and handed over as a `Proposal`; a caller presents it, gets an
answer, and only then calls `commit()`. That split is not tidiness. The one
destructive step in the feature is removing a file from the card, and it has to
be possible to show the user exactly what would happen before anything has
happened.

The order of the flow, and the reason for each step:

1. `scan()` walks the directory, skipping what is not a dump, and hashes each
   file. SHA-1 is the identity; CRC32 is corroboration.
2. `identify()` looks the SHA-1 up **across every loaded DAT**. Not the DAT the
   extension suggests, and not the one the header suggests -- see below.
3. `propose()` says what would be written, what is already there, and whether
   there is anything to ask.
4. `commit()` writes, byte-for-byte verified.
5. `remove_from_card()` is the only thing that deletes, and it compares the
   bytes again immediately before it does.
6. `cheat()` maps the canonical name to a libretro cheat file through the
   matcher the app already has.

**The hash decides the system, not the header and not the extension.** The core
derives Game Boy from Game Boy Color by reading the CGB flag at 0x143, and
No-Intro's split between the two is an editorial judgement about which machine a
game is *for* rather than a header bit. Pokemon Yellow is CGB-enhanced and sits
in the Game Boy DAT; Pokemon Gold is GB-compatible and sits in the Game Boy
Color DAT. Both would set the same flag and they are in different files, so a
header bit cannot reproduce the decision. Looking the SHA-1 up in all of them
and letting the one that contains it answer gives the canonical name, the
extension and the libretro cheat directory in a single answer that nothing else
can contradict. The header is still read, because it is the only thing that can
say anything at all about a dump the DATs do not have.

**Observations here, decisions in prefs.** Everything this module records about
a dump goes in `library.Index`, which is a cache that a rebuild reproduces. A
rejection is a decision -- no walk of the library can recover it -- so it lives
in `prefs.py` with the app's other remembered choices, and so does a cheat file
the user pinned.

Standard library only, and nothing here touches Tk or the network.
"""
from __future__ import annotations

import datetime
import enum
import os
import re
import shutil
from dataclasses import dataclass, field, replace

import card
import cheatlib
import library
import match
import nointro
import prefs
import say

# Where the core writes, relative to the card root. Flat, not the Dumps/Saves/
# Metadata tree its own file-format document lays out: that tree is planned and
# nothing has ever written it, and this module has to be right about the card
# rather than about the spec.
DUMP_DIR = ("Assets", card.DUMPER, "common")

# Enough of the front of a file to hold any header this reads. The Game Boy
# header ends at 0x14F and the Game Boy Advance one at 0xBF.
HEADER = 0x200

# Nothing smaller than this can carry a header, so it is not a dump whatever it
# is called. The smallest real cartridge is 32 KB; this is deliberately far
# below that, because refusing a file is a judgement and the cheap version of
# that judgement should only catch things that are certainly not ROMs.
MIN_DUMP = HEADER

# Extensions that are certainly not a ROM image. There is no allow-list of
# .gb/.gbc/.gba on purpose: the core got extensions wrong for its whole life so
# far, and an allow-list would refuse exactly the dumps this feature exists to
# rename. Dotfiles are skipped separately, which is what keeps .gitkeep out --
# the real directory has one.
NOT_DUMPS = (".json", ".txt", ".md", ".log", ".tmp", ".ini", ".sav")

# A save is not a dump and never goes through identification: it has no header,
# no No-Intro entry and no canonical name of its own. It is listed separately,
# and it is paired to a dump by stem because that is the one thing the core
# guarantees about the two files it writes for one cartridge.
SAVE_EXT = ".sav"

# The 48 bytes at 0x104 of every Game Boy and Game Boy Color cartridge. The
# boot ROM compares them before it will run anything, so a file that has them
# is a Game Boy image and a file that does not is not.
GB_LOGO = bytes.fromhex(
    "ceed6666cc0d000b03730083000c000d"
    "0008111f8889000edccc6ee6ddddd999"
    "bbbb67636e0eecccdddc999fbbb9333e"
)

GB_LOGO_AT = 0x104
GB_TITLE_AT = 0x134
# Fifteen, not eleven and not sixteen. This is what the core reads, and reading
# the same width is the only way to reproduce the names it wrote: on a Game Boy
# Color cartridge the title is eleven bytes and the four after it are the
# manufacturer code, which is why a real dump is called ZELDA_DIN__AZ7E.gbc.
GB_TITLE_LEN = 15
CGB_FLAG_AT = 0x143

GBA_TITLE_AT = 0xA0
GBA_TITLE_LEN = 12
GBA_CODE_AT = 0xAC
GBA_CODE_LEN = 4
# The one byte every Game Boy Advance cartridge fixes, checked by the BIOS.
GBA_FIXED_AT = 0xB2
GBA_FIXED = 0x96
GBA_SUM_AT = 0xBD

# Read in blocks for the byte-for-byte comparison and the copy. A Game Boy
# Advance cartridge reaches 32 MB and every byte of it goes through here.
CHUNK = 1024 * 1024

# What the core keeps in a filename. Everything else, including the space, the
# hyphen and the NUL padding, becomes an underscore.
#
# The hyphen used to be kept here, following the specification in
# pocket-cartridge's docs/FILE-FORMATS.md rather than the core. A real card
# settled it: dump_path_gen.sv's sanitize keeps A-Z0-9 and turns everything
# else into "_", so the cartridge titled DQM2-R is written as
# DQM2_R_____BQLJ.gbc, and keeping the hyphen made this derive DQM2-R_____BQLJ
# and report the file as renamed by hand. This reproduces the core, because
# reproducing the core is the only thing it is for.
_KEEP = re.compile(r"[^A-Za-z0-9_]")

# No-Intro puts the region in the first parenthesised group of a name; the ones
# after it are revision and enhancement tags. "(USA, Australia)" comes first,
# "(Rev 1)" and "(SGB Enhanced)" do not.
_FIRST_GROUP = re.compile(r"\(([^)]*)\)")


# -------------------------------------------------------------- the headers --
@dataclass(frozen=True)
class Header:
    """What the ROM bytes say about themselves.

    `platform` is the core's own derivation and is a **hint**, never the
    answer: see the module docstring on why the DAT that holds the hash is the
    only thing allowed to decide which system a dump belongs to. It matters for
    a dump no DAT has, which is the one case where there is nothing else to
    say.
    """
    platform: str = ""     # gb, gbc, gba, or "" when nothing recognised it
    title: str = ""        # the stem the core would have written
    code: str = ""         # game or manufacturer code, when the header has one

    def __bool__(self) -> bool:
        return bool(self.platform)


def core_stem(raw: bytes) -> str:
    """The filename stem the core makes out of a header title field.

    Trailing padding goes first, because a Game Boy title is NUL-filled to its
    full width and keeping it would turn ZELDA into ZELDA__________. Padding
    *inside* the field stays and becomes underscores, which is the whole reason
    a real dump is named ZELDA_DIN__AZ7E: the two NULs between the title and
    the manufacturer code are in the middle of what the core read.

    The core's own notes say it keeps spaces, and every name on a real card has
    an underscore where a space belongs, so this reproduces the card rather
    than the note. It is only ever used to corroborate, so where the two differ
    it costs a comparison and nothing else.
    """
    text = raw.rstrip(b"\x00 \xff")
    return _KEEP.sub("_", text.decode("ascii", "replace")).rstrip("_") or ""


def header(data: bytes) -> Header:
    """Read a platform out of the front of a ROM image.

    The Game Boy logo is checked first because it is the strongest signal
    available -- 48 bytes the boot ROM itself compares -- and because a Game Boy
    Advance image will not have it at that offset.
    """
    if len(data) > CGB_FLAG_AT and _is_gb(data):
        cgb = data[CGB_FLAG_AT] in (0x80, 0xC0)
        raw = data[GB_TITLE_AT:GB_TITLE_AT + GB_TITLE_LEN]
        # The manufacturer code only exists on a cartridge that shortened its
        # title to eleven bytes to make room for it, which is what the CGB flag
        # says. Reading it off a DMG cartridge would return four bytes of title.
        code = (data[0x13F:0x143].decode("ascii", "replace").strip("\x00 ")
                if cgb else "")
        return Header("gbc" if cgb else "gb", core_stem(raw), code)
    if len(data) >= 0xC0 and _is_gba(data):
        raw = data[GBA_TITLE_AT:GBA_TITLE_AT + GBA_TITLE_LEN]
        code = data[GBA_CODE_AT:GBA_CODE_AT + GBA_CODE_LEN].decode(
            "ascii", "replace").strip("\x00 ")
        return Header("gba", core_stem(raw), code)
    return Header()


def read_header(path: str) -> Header:
    """The header of a file on disk. An empty Header if it cannot be read."""
    try:
        with open(path, "rb") as f:
            return header(f.read(HEADER))
    except OSError as e:
        say.err(f"cannot read {path}: {e}")
        return Header()


def _is_gb(data: bytes) -> bool:
    return data[GB_LOGO_AT:GB_LOGO_AT + len(GB_LOGO)] == GB_LOGO


def _is_gba(data: bytes) -> bool:
    """The fixed byte and the header checksum together.

    The fixed byte alone would call any file with 0x96 at offset 0xB2 a
    cartridge. The checksum covers the 29 bytes the BIOS reads and is what the
    hardware itself refuses a cartridge over, so the two together are as strong
    as this can get without carrying Nintendo's 156-byte logo around.
    """
    if data[GBA_FIXED_AT] != GBA_FIXED:
        return False
    total = 0
    for b in data[0xA0:GBA_SUM_AT]:
        total += b
    return (-(total + 0x19)) & 0xFF == data[GBA_SUM_AT]


# ------------------------------------------------------------ prong 1: read --
@dataclass(frozen=True)
class Dump:
    """One file the core left on the card, as read. Nothing is written yet."""
    path: str
    name: str
    size: int
    sha1: str
    crc32: str
    header: Header = Header()

    @property
    def platform(self) -> str:
        """The header's guess, which identification is allowed to overrule."""
        return self.header.platform

    @property
    def stem(self) -> str:
        return os.path.splitext(self.name)[0]

    @property
    def renamed(self) -> bool:
        """The file is not called what the core would have called it.

        Weak evidence and offered as nothing more: it catches a dump somebody
        renamed by hand, and it says nothing at all about the case it looks
        like it should catch, because two cartridges that both title themselves
        ZELDA produce the same stem whichever of them overwrote the other.
        """
        return bool(self.header.title) and self.stem != self.header.title


def dump_dir(card_root: str) -> str:
    return os.path.join(card_root, *DUMP_DIR)


def is_dump(name: str) -> bool:
    """Whether a directory entry is worth hashing.

    A dotfile is never a dump, and that is not hypothetical: the real
    directory holds a .gitkeep, and treating it as a dump would put an empty
    file through identification and report it as unknown.
    """
    if name.startswith("."):
        return False
    return os.path.splitext(name)[1].lower() not in NOT_DUMPS


def read(path: str) -> Dump | None:
    """Hash and read one file. None if it is not a dump or cannot be read."""
    d = nointro.digest(path)
    if d is None:
        say.err(f"cannot read {path}")
        return None
    if d.size < MIN_DUMP:
        return None
    return Dump(path=path, name=os.path.basename(path), size=d.size,
                crc32=d.crc32, sha1=d.sha1, header=read_header(path))


def scan(card_root: str) -> list[Dump]:
    """Every dump in the core's output directory, hashed, in name order.

    Only that directory and only its own files: the core writes flat, and
    descending into subdirectories somebody made would pick up whatever they
    put there. A missing directory is an empty list, not an error -- a card
    without the dumper core installed is a normal card.
    """
    base = dump_dir(card_root)
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    found = []
    for name in names:
        full = os.path.join(base, name)
        if not is_dump(name) or not os.path.isfile(full):
            continue
        one = read(full)
        if one is not None:
            found.append(one)
    return found


@dataclass(frozen=True)
class Save:
    """One .sav the core left on the card. Not hashed, and not identified.

    A save has no header to read and no DAT to look it up in, so nothing here
    can say which game it belongs to on its own evidence. `stem` is the only
    link back to a cartridge, and it is the core's own basename rather than a
    canonical name.
    """
    path: str
    name: str
    size: int

    @property
    def stem(self) -> str:
        return os.path.splitext(self.name)[0]


def scan_saves(card_root: str) -> list[Save]:
    """Every .sav in the core's output directory, in name order. No hashing.

    Separate from scan() rather than a flag on it, because the two answer
    different questions and only one of them is expensive: this one is allowed
    to run on every card scan, and scan() is not.
    """
    base = dump_dir(card_root)
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    found = []
    for name in names:
        if name.startswith("."):
            continue
        if os.path.splitext(name)[1].lower() != SAVE_EXT:
            continue
        full = os.path.join(base, name)
        if not os.path.isfile(full):
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            say.err(f"cannot read {full}")
            continue
        found.append(Save(path=full, name=name, size=size))
    return found


@dataclass(frozen=True)
class Waiting:
    """What the card is carrying, counted without reading any of it.

    Deliberately cheap and deliberately dumb: it counts directory entries so
    that the main window can say there is something here on every scan. It
    knows nothing about what is already imported, so it is a prompt to look and
    never a claim that there is work to do. survey() is what actually answers
    that, and it hashes every byte to do it.
    """
    dumps: int = 0
    saves: int = 0
    # How many of those saves the library does not hold. None when nobody
    # asked, which is not the same as zero and must not read as "all done".
    new_saves: int | None = None

    @property
    def any(self) -> bool:
        return bool(self.dumps or self.saves)

    def note(self) -> str:
        """One line for the main window, or "" when the card carries nothing.

        It used to end "to import them" whatever the state was, so a card
        whose every save was already in the library still read as a card with
        work on it. Dumps are still only counted, because hashing a shelf of
        32 MB ROMs on every scan is the half minute this line exists to avoid;
        saves are small enough to answer properly.
        """
        if not self.any:
            return ""
        parts = []
        if self.dumps:
            parts.append(f"{self.dumps} cartridge dump"
                         f"{'s' if self.dumps > 1 else ''}")
        if self.saves:
            parts.append(f"{self.saves} save{'s' if self.saves > 1 else ''}")
        line = " and ".join(parts) + " on the card."
        if self.dumps or self.new_saves is None:
            return line + " Cartridge dumps... to import them."
        if self.new_saves:
            return (f"{line} {self.new_saves} not in the library yet. "
                    "Cartridge dumps... to import them.")
        return line + " All of them are in the library already."


def waiting(card_root: str, root: str = "",
            index: "library.Index | None" = None) -> Waiting:
    """Count what is in the core's output directory.

    Reads no ROM. With a library it also hashes the saves, which are a few
    kilobytes each and worth reading so the line can say whether any of them
    is actually work.
    """
    base = dump_dir(card_root)
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return Waiting()
    dumps = saves = 0
    for name in names:
        if name.startswith("."):
            continue
        if not os.path.isfile(os.path.join(base, name)):
            continue
        if os.path.splitext(name)[1].lower() == SAVE_EXT:
            saves += 1
        elif is_dump(name):
            dumps += 1
    new = None
    if root and index is not None:
        new = sum(1 for sv, row, held in matched_card_saves(card_root, root,
                                                            index)
                  if not held)
    return Waiting(dumps=dumps, saves=saves, new_saves=new)


# -------------------------------------------------------- prong 2: identify --
@dataclass(frozen=True)
class Identity:
    """What the DATs say a dump is, and which of them were asked."""
    outcome: nointro.Outcome
    system: str = ""                     # the DAT that held it: gb, gbc, gba
    entry: nointro.Entry | None = None
    disagreed: tuple[str, ...] = ()      # the fields that did, on a mismatch
    searched: tuple[str, ...] = ()       # the systems loaded when this was asked

    @property
    def matched(self) -> bool:
        return self.outcome is nointro.Outcome.MATCH

    @property
    def name(self) -> str | None:
        """The canonical filename, extension and all, when there is one.

        Verbatim from the DAT rather than rebuilt from the game name and the
        system: the Game Boy Advance DAT holds entries ending .bin and .gbc,
        and taking the name as given handles them without a special case.
        """
        return self.entry.name if self.matched and self.entry else None

    @property
    def title(self) -> str | None:
        return self.entry.game if self.matched and self.entry else None

    @property
    def parent(self) -> str | None:
        """The clone parent's game name, which is the cheat fallback."""
        return self.entry.parent if self.matched and self.entry else None

    @property
    def region(self) -> str | None:
        return region_of(self.entry.game) if self.matched and self.entry else None


def region_of(game: str) -> str | None:
    """The region out of a No-Intro game name, or None.

    Read off the name because neither flavour that parses carries it as a field
    an entry keeps: Standard has no region at all, and Parent-Clone puts it in
    `<release>` elements that say the same thing the name already does. The
    name is also where every other tool reads it from.
    """
    found = _FIRST_GROUP.search(game or "")
    return found.group(1) if found else None


def identify(dump: Dump, catalog: nointro.Catalog) -> Identity:
    """Look a dump up in every DAT that is loaded.

    Every DAT, in a fixed order, and not the one the extension or the header
    points at. A SHA-1 is in exactly one of them, so asking all three cannot be
    ambiguous, and asking only the one the header suggested would file Pokemon
    Yellow and Pokemon Gold on the wrong sides of a split that was never made
    from the header. See the module docstring.

    A mismatch is carried back rather than searched past: it means a file whose
    hash a DAT knows but whose size or CRC32 disagrees, which should not
    happen, and the useful thing to say is which game it nearly is.
    """
    loaded = catalog.loaded()
    if not loaded:
        return Identity(nointro.Outcome.NO_DATA)
    near: Identity | None = None
    for system in loaded:
        result = catalog.lookup(system, dump.sha1, dump.size, dump.crc32)
        if result.outcome is nointro.Outcome.MATCH:
            return Identity(result.outcome, system, result.entry,
                            result.disagreed, loaded)
        if result.outcome is nointro.Outcome.MISMATCH and near is None:
            near = Identity(result.outcome, system, result.entry,
                            result.disagreed, loaded)
    return near or Identity(nointro.Outcome.UNKNOWN, searched=loaded)


def dat_note(catalog: nointro.Catalog) -> str:
    """What the app has loaded, in a sentence fit to sit under an unknown.

    "Not found" means something different with one DAT loaded than with three,
    and the user is the only one who can fix the difference, so an unknown
    dump is never reported without saying what was searched.
    """
    loaded = catalog.loaded()
    missing = catalog.missing()
    if not loaded:
        return ("No No-Intro data loaded. Nothing can be identified until a "
                "DAT is added for at least one system.")
    have = ", ".join(nointro.SYSTEMS[p] for p in loaded)
    if not missing:
        return f"Searched all three DATs: {have}."
    gap = ", ".join(nointro.SYSTEMS[p] for p in missing)
    return f"Searched {have}. No data loaded for {gap}."


def readback(dump: Dump, core_crc32: str | None) -> bool | None:
    """Whether the CRC32 the core displayed still describes the file.

    None when the core's value is not known, which is every dump today: there
    is no sidecar and the number was only ever on the handheld's screen. When
    it is known this is worth doing, because the dumper's own checksums cover
    the bytes *leaving* the reader and it never confirms anything reached the
    card. A disagreement means the bytes changed between the FPGA and the file,
    and this app is the only component that can notice.

    It costs nothing: the CRC32 came off the same pass that produced the SHA-1.
    """
    if not core_crc32:
        return None
    return core_crc32.strip().lower().zfill(8) == dump.crc32


# ------------------------------------------------------------ prong 3: file --
class Verdict(enum.Enum):
    """What the app would do with a dump, before anybody is asked."""
    IMPORT = "import"                  # identified, the names are free, go ahead
    COLLIDES = "collides"          # a name is taken by different bytes: ask
    IMPORTED = "imported"          # this SHA-1 is already in the library: skip
    SAVE_ONLY = "save only"        # the ROM is in; the save beside it is not
    REJECTED = "rejected"          # ignored before: skip, re-offer on ask
    MISSING = "missing"            # known SHA-1, but its imported copy is gone
    UNIDENTIFIED = "unidentified"  # no DAT has it; offer nothing automatic
    UNREADABLE = "unreadable"      # something is in the way and cannot be read


class Choice(enum.Enum):
    """The three answers to a collision, and what each one costs.

    KEEP_BOTH is the default and suffixes the new file with a short SHA-1
    prefix. Not `_2`: a counter records the order files arrived, which is not a
    fact about either of them, and a consumer would be entitled to read it as a
    second revision of one cartridge rather than a second cartridge.

    DISCARD is not a rejection. It leaves the library alone and leaves the dump
    on the card, so it is offered again next time, because a decision the user
    has not really made should come back rather than be remembered as settled.
    """
    KEEP_BOTH = "keep both"
    REPLACE = "replace"
    DISCARD = "discard"


@dataclass(frozen=True)
class Standing:
    """A file already sitting at a name a dump would be written to."""
    path: str
    sha1: str = ""        # "" when the file could not be read
    size: int = 0
    imported: str = ""    # the day it arrived, for the collision dialog

    @property
    def readable(self) -> bool:
        return bool(self.sha1)


@dataclass(frozen=True)
class Proposal:
    """Everything about one dump, computed. Nothing has been written.

    This is what an approval view draws. `verdict` says whether there is a
    question, `rom_name` and `dump_name` say what would be written where, and
    the two `Standing`s say what is in the way. `commit()` turns it into files.
    """
    dump: Dump
    identity: Identity
    root: str                                   # the library
    rom_name: str | None = None                 # name under roms/, canonical
    dump_name: str | None = None                # name under cart-dumps/
    save: Save | None = None                    # the .sav beside it, if any
    verdict: Verdict = Verdict.UNIDENTIFIED
    rom_standing: Standing | None = None
    dump_standing: Standing | None = None
    row: library.Row | None = None              # what the index already says
    note: str = ""

    @property
    def sha1(self) -> str:
        return self.dump.sha1

    @property
    def actionable(self) -> bool:
        """True if committing this would write something."""
        return self.verdict in (Verdict.IMPORT, Verdict.COLLIDES,
                                Verdict.MISSING, Verdict.SAVE_ONLY)

    @property
    def collides(self) -> bool:
        return self.verdict is Verdict.COLLIDES

    @property
    def quiet(self) -> bool:
        """True if this dump is settled and the user should not see it."""
        return self.verdict in (Verdict.IMPORTED, Verdict.REJECTED)

    def rom_path(self) -> str | None:
        return (os.path.join(library.roms_dir(self.root), self.rom_name)
                if self.rom_name else None)

    def dump_path(self) -> str | None:
        return (os.path.join(library.dumps_dir(self.root), self.dump_name)
                if self.dump_name else None)


@dataclass(frozen=True)
class Import:
    """What commit() did, or why it did not."""
    proposal: Proposal
    ok: bool = False
    row: library.Row | None = None
    rom_path: str = ""
    dump_path: str = ""
    cartsave_path: str = ""     # the save read, under cartsaves/, if there was one
    verified: bool = False      # every byte written was compared with the card
    removed: bool = False       # the card original has been deleted
    discarded: bool = False     # the user chose Discard; nothing was touched
    problem: str = ""


def suffixed(name: str, sha1: str) -> str:
    """The keep-both name: the same name with a short SHA-1 prefix in it.

    A fact about the contents rather than about the order of arrival. It is
    stable across machines and reruns, and two files that differ can never be
    given the same one.
    """
    stem, ext = os.path.splitext(name)
    return f"{stem} [{sha1[:8]}]{ext}"


def rejected(sha1: str) -> bool:
    return prefs.get_rejected(sha1) is not None


def reject(dump: Dump | str) -> None:
    """Remember that a dump is ignored, so the next run does not offer it.

    In prefs and not in the index, because it is a decision: a walk of the
    library re-hashes files and asks the DAT again, and no amount of that will
    tell you the user said no.
    """
    sha1 = dump if isinstance(dump, str) else dump.sha1
    prefs.set_rejected(sha1, _today())


def unreject(dump: Dump | str) -> None:
    """Forget a rejection, so the dump is offered again."""
    sha1 = dump if isinstance(dump, str) else dump.sha1
    prefs.set_rejected(sha1, None)


def propose(dump: Dump, identity: Identity, root: str, index: library.Index,
            *, offer_rejected: bool = False, save: Save | None = None
            ) -> Proposal:
    """Work out what would happen to one dump. Reads files; writes none.

    The order of the checks is the order of the cheapest honest answer. A
    rejection is asked first because the user already said no and nothing after
    it can change that; the index is asked next because a dump imported before
    needs no second decision; and only then does anything look at the library's
    directories, which is where the reads are.
    """
    row = index.get(dump.sha1)

    if not offer_rejected and rejected(dump.sha1):
        return Proposal(dump, identity, root, save=save, verdict=Verdict.REJECTED, row=row,
                        note="ignored before")

    if row is not None and (row.rom or row.dump):
        gone = [p for p in (row.rom_path(root), row.dump_path(root))
                if p and not os.path.exists(p)]
        # A dump whose ROM is already imported still has something to do if
        # the save beside it on the card is a read the library has not seen.
        # A cartridge is re-dumped precisely to catch a save that changed, and
        # skipping on the ROM's SHA-1 alone would throw that away silently.
        if not gone:
            # Two answers, and the difference is the save. The ROM is in the
            # library either way; SAVE_ONLY says the save read beside it on
            # the card is one the library has never seen, which is the whole
            # of what is left to do for this cartridge.
            done = not _unseen_save(save, root, row.rom)
            return Proposal(dump, identity, root, save=save, rom_name=row.rom,
                            dump_name=row.dump, row=row,
                            verdict=Verdict.IMPORTED if done
                            else Verdict.SAVE_ONLY,
                            note="" if done else "its save is not in the "
                                                 "library yet")

    # The library's own names win over the DAT's when there is a row, because a
    # dump imported under a keep-both suffix has to be restored under that suffix
    # and not under the plain canonical name, which belongs to another file.
    rom_name = (row.rom if row and row.rom else None) or identity.name
    dump_name = (row.dump if row and row.dump else None) or dump.name

    if not rom_name:
        # Unknown is not an error and it is not a guess either: a bad dump, a
        # revision No-Intro has not catalogued and a reproduction cartridge all
        # arrive here, nothing can tell them apart, and so nothing automatic is
        # offered.
        return Proposal(dump, identity, root, save=save, verdict=Verdict.UNIDENTIFIED,
                        row=row, note=identity.outcome.value)

    rom_standing = _standing(os.path.join(library.roms_dir(root), rom_name))
    dump_standing = _standing(os.path.join(library.dumps_dir(root), dump_name))
    standings = [s for s in (rom_standing, dump_standing) if s is not None]

    verdict = Verdict.MISSING if row is not None else Verdict.IMPORT
    note = "imported before, and its copy in the library is gone" if row else ""

    if any(not s.readable for s in standings):
        # Report it and move on. Overwriting a file that could not be read
        # means destroying something whose contents are unknown, which is the
        # one thing this feature must never do.
        verdict = Verdict.UNREADABLE
        note = "a file already at that name could not be read"
    elif any(s.sha1 != dump.sha1 for s in standings):
        verdict = Verdict.COLLIDES
        note = "that name is taken, by different bytes"

    return Proposal(dump, identity, root, save=save, rom_name=rom_name,
                    dump_name=dump_name, verdict=verdict,
                    rom_standing=rom_standing, dump_standing=dump_standing,
                    row=row, note=note)


def commit(proposal: Proposal, index: library.Index,
           choice: Choice = Choice.KEEP_BOTH) -> Import:
    """Write the dump into the library. Does not touch the card.

    Two files land for one dump: the canonical copy under roms/, which is the
    one you use, and the card original under cart-dumps/ keeping the name the
    core gave it, which is the evidence of what the dumper actually produced
    and the only thing that can settle a later argument about whether a bad
    name came from the core or from us.

    Removing the original from the card is deliberately not done here. It is
    the only destructive step in the feature, it is asked separately, and a
    card pulled at any point before `remove_from_card()` costs nothing worse
    than a dump that is still on it.
    """
    if choice is Choice.DISCARD:
        # Nothing is written and nothing is remembered. Discarding is not
        # rejecting: the dump stays on the card and comes back next time.
        return Import(proposal, ok=False, discarded=True,
                      problem="discarded; the dump stays on the card")
    if not proposal.actionable:
        return Import(proposal, ok=False,
                      problem=f"nothing to do: {proposal.verdict.value}")

    root = proposal.root
    library.create(root)
    dump = proposal.dump

    written: dict[str, str] = {}
    verified = True
    problem = ""
    for field, base, folder, standing in (
            ("rom", proposal.rom_name, library.roms_dir(root),
             proposal.rom_standing),
            ("dump", proposal.dump_name, library.dumps_dir(root),
             proposal.dump_standing)):
        if not base:
            continue
        name, needed, why = _destination(folder, base, dump.sha1, standing,
                                         choice)
        if why:
            problem = why
            break
        written[field] = name
        if not needed:
            # The same bytes are already there. This is the ordinary case when
            # a cartridge is dumped twice, and it is not worth a write.
            continue
        why = _place(dump.path, os.path.join(folder, name))
        if why:
            problem = why
            verified = False
            del written[field]
            break

    # The save goes in last and only once the ROM has a name, because the
    # directory it lands in is named after that name. A save that fails to
    # copy does not fail the import: the ROM is the thing being imported and
    # it is already on disk, so the honest result is an imported dump that
    # says its save did not make it.
    cartsave_path = ""
    if proposal.save is not None and written.get("rom"):
        cartsave_path, why = _place_cartsave(proposal.save, root,
                                             written["rom"])
        if why and not problem:
            problem = why

    row = row_for(dump, proposal.identity,
                    rom=written.get("rom"), dump_name=written.get("dump"),
                    was=proposal.row,
                    cartsaves=(os.path.splitext(written["rom"])[0]
                               if cartsave_path else
                               (proposal.row.cartsaves if proposal.row
                                else None)))
    if written:
        # Recorded even when the second copy failed. The index is an
        # observation of what is on disk, so half an import is more truthful
        # than none, and a rebuild would say exactly the same thing.
        index.put(row)
        library.save(root, index)

    rom_path = (os.path.join(library.roms_dir(root), written["rom"])
                if "rom" in written else "")
    dump_path = (os.path.join(library.dumps_dir(root), written["dump"])
                 if "dump" in written else "")
    return Import(proposal, ok=not problem and len(written) == 2,
                  row=row if written else None,
                  rom_path=rom_path, dump_path=dump_path,
                  cartsave_path=cartsave_path,
                  verified=verified and bool(written), problem=problem)


def match_save(save: Save, index: library.Index) -> library.Row | None:
    """The imported dump a stranded save belongs to, or None.

    A save is paired to the ROM beside it on the card, and that fails the
    moment the ROM is cleared from the card while its save is left, which is
    the ordinary end state of importing a cartridge. The index still knows:
    it records the name the core gave the ROM, and the core gives both files
    of one cartridge the same stem.

    This is how ZEROMISSIONE.sav finds Metroid - Zero Mission (USA,
    Australia).gba with no .gba anywhere near it.
    """
    for row in index:
        if row.dump and os.path.splitext(row.dump)[0] == save.stem:
            return row
    return None


def unseen_cartridge_saves(card_root: str, root: str,
                           index: library.Index) -> list[tuple[Save, library.Row]]:
    """Saves on the card whose cartridge is imported but whose bytes are not.

    The dumper's own output, read from where the dumper writes it, which is
    the whole point and is what an earlier version of this got wrong: it read
    /Saves/cartdumps instead, where the Pocket keeps what an emulated core
    wrote, and filed a 64 KB padded emulator save as a 32 KiB cartridge read.
    """
    out = []
    for save in scan_saves(card_root):
        row = match_save(save, index)
        if row is not None and _unseen_save(save, root, row.rom):
            out.append((save, row))
    return out


def matched_card_saves(card_root: str, root: str,
                       index: library.Index) -> list[tuple[Save, library.Row, str]]:
    """Every save on the card that belongs to an imported dump.

    (save, its dump's row, the library copy of these bytes or "").

    All of them, not only the ones the library has never seen. A save whose
    bytes are held is still on the card and still worth a row, for exactly
    the reason a duplicate dump is: that row is the only place the card copy
    can be cleared from. Listing only the unseen ones made a save vanish the
    moment it was imported, leaving the file on the card and no way to reach
    it.
    """
    out = []
    for save in scan_saves(card_root):
        row = match_save(save, index)
        if row is not None:
            out.append((save, row, same_saved_bytes(save, root, row)))
    return out


def import_cartridge_save(save: Save, row: library.Row,
                          root: str) -> tuple[str, str]:
    """Keep one save the dumper wrote. (path, problem)."""
    if not row.rom:
        return "", "that dump has no name in the library"
    return _place_cartsave(save, root, row.rom, library.CART)


def _unseen_save(save: Save | None, root: str,
                 rom_name: str | None) -> bool:
    """True if `save` holds bytes this cartridge has no read of yet."""
    if save is None or not rom_name:
        return False
    try:
        sha1 = library.sha1_of(save.path)
    except OSError:
        return False
    folder = library.cartsave_dir(root, rom_name)
    for name in library.cartsave_reads(root, rom_name):
        try:
            if library.sha1_of(os.path.join(folder, name)) == sha1:
                return False
        except OSError:
            continue
    return True


def _place_cartsave(save: Save, root: str, rom_name: str,
                    origin: str = library.CART) -> tuple[str, str]:
    """Copy one save read into cartsaves/<rom>/<day>.sav. (path, problem).

    Immutable: an existing file at that path is compared rather than written
    over, so re-importing the same card twice costs a comparison, and two
    reads that differ on one day are both kept under separate names. Nothing
    here ever overwrites a save, because a save is the one file in this
    library that cannot be re-derived from anything else.
    """
    try:
        sha1 = library.sha1_of(save.path)
    except OSError as e:
        return "", f"cannot read {save.name}: {e}"
    dest = library.cartsave_dest(root, rom_name, _today(), sha1, origin)
    if os.path.exists(dest):
        return dest, ""
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
    except OSError as e:
        return "", f"cannot make the save directory: {e}"
    why = _place(save.path, dest)
    return ("", why) if why else (dest, "")


def remove_from_card(imported: Import) -> Import:
    """Delete the card original, and only after comparing the bytes again.

    The only destructive step in the feature, so the check in front of it is
    the strongest one available rather than the cheapest: the bytes, compared,
    not a re-hash. A hash match is a statement about a digest.

    The comparison is done here rather than trusted from `commit()` because the
    two are minutes apart -- there is a person deciding in between -- and the
    card is removable. If it went away in the meantime, or came back as a
    different card, this refuses instead of deleting.
    """
    if not imported.ok or not imported.dump_path:
        return replace(imported, problem=imported.problem or "nothing was imported")
    source = imported.proposal.dump.path
    if not os.path.exists(source):
        return replace(imported, removed=False, problem="the card file is gone")
    if not same_bytes(source, imported.dump_path):
        return replace(imported, removed=False,
                       problem=f"{imported.dump_path} does not match the card; "
                               "nothing was removed")
    try:
        os.remove(source)
    except OSError as e:
        return replace(imported, removed=False, problem=f"cannot remove: {e}")
    return replace(imported, removed=True, problem="")


def cartsave_reads(root: str, row: library.Row) -> list[str]:
    """The save reads kept for one imported dump, newest last."""
    return library.cartsave_reads(root, row.rom) if row.rom else []


@dataclass(frozen=True)
class CartSave:
    """One save read in the library, as a thing rather than as a filename.

    `label` is the only field a person chose and the only one not derivable
    from the file, which is why it comes from prefs and everything else comes
    from the bytes. `title` is what to put in front of somebody: the label
    when there is one, the day it was read when there is not.
    """
    path: str
    name: str                 # the filename under cartsaves/<rom>/
    sha1: str
    size: int
    day: str                  # the date in the filename, not the file's mtime
    origin: str = library.CART
    label: str = ""

    @property
    def from_cartridge(self) -> bool:
        return self.origin == library.CART

    @property
    def where(self) -> str:
        """One word for the column: what wrote these bytes."""
        return "cartridge" if self.from_cartridge else "Pocket"

    @property
    def title(self) -> str:
        return self.label or self.day

    @property
    def named(self) -> bool:
        return bool(self.label)


def cartsaves(root: str, row: library.Row) -> list[CartSave]:
    """Every save read kept for one dump, oldest first, named where named.

    Unreadable files are skipped rather than reported as empty ones: a save
    whose bytes cannot be read has no identity, and giving it a blank SHA-1
    would let two of them collide into one record.
    """
    if not row.rom:
        return []
    folder = library.cartsave_dir(root, row.rom)
    out = []
    for name in library.cartsave_reads(root, row.rom):
        full = os.path.join(folder, name)
        try:
            sha1 = library.sha1_of(full)
            size = os.path.getsize(full)
        except OSError as e:
            say.err(f"cannot read {full}: {e}")
            continue
        day, origin = library.cartsave_parts(name)
        out.append(CartSave(path=full, name=name, sha1=sha1, size=size,
                            day=day, origin=origin,
                            label=prefs.get_save_name(sha1) or ""))
    return out


def name_cartsave(save: CartSave, text: str | None) -> None:
    """Name a save read, or with None forget the name."""
    prefs.set_save_name(save.sha1, text)


def restore_save(root: str, row: library.Row, rom_on_card: str,
                 which: str = "") -> tuple[str, str]:
    """Put a save read back beside a dump on the card. (path, problem).

    This is the reverse of the import, and the whole point of taking the save
    off the cartridge: the Pocket reads Saves/cartdumps/<pid>/<name>.sav for a
    ROM at Assets/cartdumps/<pid>/<name>.<ext>, so a save written there is the
    cartridge's own save, in the emulated core, under the name the ROM was
    imported as.

    `which` names one of cartsave_reads(); the newest is used when it is
    empty. Through a temporary file and a replace, because a half-written save
    the Pocket then loads is worse than no save.
    """
    reads = cartsave_reads(root, row)
    if not reads:
        return "", "no save was imported for this dump"
    name = which or reads[-1]
    if name not in reads:
        return "", f"{name} is not one of this dump's save reads"
    src = os.path.join(library.cartsave_dir(root, row.rom), name)
    dest = card.save_beside(rom_on_card)
    # Refuse a size mismatch rather than warn about one. A save is read back
    # into a fixed region and a file of the wrong length is not a worse save,
    # it is a different cartridge's: the sizes come in a handful of discrete
    # steps and two that disagree cannot both be right for one game. Only
    # checked when something is already there, because the first restore has
    # nothing to disagree with.
    if os.path.exists(dest):
        try:
            there, here = os.path.getsize(dest), os.path.getsize(src)
        except OSError as e:
            return "", f"cannot compare with the save on the card: {e}"
        if there != here:
            return "", (f"{name} is {here:,} bytes and the save already on "
                        f"the card is {there:,}. Nothing was written: a save "
                        "of the wrong length belongs to a different game.")
    tmp = dest + ".part"
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return "", f"could not write the save to the card: {e}"
    return dest, ""


def card_save_of(card_root: str, row: library.Row) -> str | None:
    """The save the Pocket keeps for this dump, if there is one.

    The Pocket writes it as you play, so this file is the live one and the
    library's reads are history. It is the same path restore_save() writes to,
    asked from the other direction.
    """
    if not row.rom or not row.system:
        return None
    rom_on_card = os.path.join(card.cartdumps_dir(card_root, row.system),
                               row.rom)
    try:
        path = card.save_beside(rom_on_card)
    except ValueError:
        return None
    return path if os.path.isfile(path) else None


def live_read(root: str, row: library.Row, card_root: str) -> str | None:
    """Which of this cartridge's reads is the one on the card, by hash.

    Answered from the bytes rather than from a note kept somewhere, because
    the Pocket writes that file without telling anybody and a note would go
    stale the first time you played. None means the card's save matches no
    read in the library, which is the state worth acting on: it is progress
    that exists nowhere else.
    """
    live = card_save_of(card_root, row)
    if live is None or not row.rom:
        return None
    try:
        sha1 = library.sha1_of(live)
    except OSError:
        return None
    folder = library.cartsave_dir(root, row.rom)
    for name in library.cartsave_reads(root, row.rom):
        try:
            if library.sha1_of(os.path.join(folder, name)) == sha1:
                return name
        except OSError:
            continue
    return None


def card_saves_for(card_root: str, root: str, row: library.Row,
                   index: library.Index) -> list[tuple[Save, str]]:
    """Saves on the card for one cartridge that the library does not hold.

    Both kinds, each labelled, because the card carries two and they are not
    the same thing: what the dumper read off the chip, in
    /Assets/carttools/common, and what an emulated core wrote while somebody
    played, in /Saves/cartdumps. Reading only the second one is the defect
    this function exists to make impossible to repeat.
    """
    out: list[tuple[Save, str]] = []
    if not row.rom:
        return out
    for save in scan_saves(card_root):
        # By SHA-1, not by object identity: `index` is loaded fresh here and
        # `row` came from somewhere else, so the two are never the same Row
        # even when they describe the same dump.
        hit = match_save(save, index)
        if hit is not None and hit.sha1 == row.sha1 \
                and _unseen_save(save, root, row.rom):
            out.append((save, library.CART))
    live = card_save_of(card_root, row)
    if live is not None:
        played = Save(path=live, name=os.path.basename(live),
                      size=os.path.getsize(live))
        if _unseen_save(played, root, row.rom):
            out.append((played, library.POCKET))
    return out


def read_card_saves(card_root: str, root: str, row: library.Row,
                    index: library.Index) -> tuple[list[str], list[str]]:
    """Keep every card save this cartridge is missing. (paths, problems)."""
    kept, problems = [], []
    for save, origin in card_saves_for(card_root, root, row, index):
        path, why = _place_cartsave(save, root, row.rom, origin)
        if path:
            kept.append(path)
        else:
            problems.append(f"{save.name}: {why}")
    return kept, problems


def capture_save(root: str, row: library.Row,
                 card_root: str) -> tuple[str, str]:
    """Take the card's current save into the library as a read. (path, note).

    The other half of restore_save(). A save restored to the card and then
    played on is no longer any read the library holds, and nothing else would
    ever bring that state back: the Pocket does not write to the library and
    the cartridge it came off has moved on. Dated like every other read and
    never overwriting one, so capturing twice in a day keeps both.

    An unchanged save is not an error and writes nothing. It is the ordinary
    case for a card that has been read but not played.
    """
    live = card_save_of(card_root, row)
    if live is None:
        return "", "no save for this dump on the card"
    if not row.rom:
        return "", "this dump has no name in the library"
    already = live_read(root, row, card_root)
    if already:
        return "", f"the card's save is already kept, as {already}"
    save = Save(path=live, name=os.path.basename(live),
                size=os.path.getsize(live))
    path, why = _place_cartsave(save, root, row.rom, library.POCKET)
    return (path, "") if path else ("", why)


# ------------------------------------------------- taking things back out --
#
# The library is a safety copy, so removing from it is the one direction with
# no undo. Both functions below therefore say what would be lost before they
# are called, and neither guesses: `elsewhere` is answered by hashing what is
# actually on the card rather than by assuming a card copy still exists.
#
# There was no way to do this at all until a save was filed wrongly and had to
# be deleted from a shell. A library you can only add to is not a library.


def cartsave_elsewhere(save: CartSave, card_root: str,
                       row: library.Row) -> str:
    """A path on the card holding these exact bytes, or "".

    Answered by hashing, because "there is a copy on the card" is exactly the
    sort of thing that is true right up until somebody swaps the card.
    """
    if not card_root:
        return ""
    # The two places a save for this cartridge can be: what the dumper wrote,
    # and what an emulated core wrote. Named explicitly rather than searched,
    # so this cannot quietly match some other cartridge's identical bytes.
    candidates = []
    if row.dump:
        candidates.append(os.path.join(
            dump_dir(card_root), os.path.splitext(row.dump)[0] + SAVE_EXT))
    played = card_save_of(card_root, row)
    if played:
        candidates.append(played)
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            if library.sha1_of(path) == save.sha1:
                return path
        except OSError:
            continue
    return ""


def forget_cartsave(save: CartSave) -> str:
    """Delete one save read and the name given to it. "" or a problem.

    The name goes with the file. Leaving it behind would mean a later read
    with the same bytes silently inheriting a name nobody gave it.
    """
    try:
        os.remove(save.path)
    except OSError as e:
        return f"cannot remove {save.name}: {e}"
    prefs.set_save_name(save.sha1, None)
    folder = os.path.dirname(save.path)
    try:
        if not os.listdir(folder):
            os.rmdir(folder)
    except OSError:
        pass                    # a directory that will not go is not a fault
    return ""


@dataclass(frozen=True)
class Loss:
    """What removing one imported dump from the library would destroy."""
    rom: str = ""
    dump: str = ""
    saves: int = 0
    on_card: bool = False       # the card still holds the dump's own bytes

    @property
    def anything(self) -> bool:
        return bool(self.rom or self.dump or self.saves)


def what_removing_costs(root: str, row: library.Row,
                        card_root: str = "") -> Loss:
    """What `forget_dump` would delete, for a confirmation to quote."""
    rom = row.rom_path(root)
    dump = row.dump_path(root)
    on_card = False
    if card_root and row.dump:
        on_card = os.path.isfile(os.path.join(dump_dir(card_root), row.dump))
    return Loss(rom=rom if rom and os.path.isfile(rom) else "",
                dump=dump if dump and os.path.isfile(dump) else "",
                saves=len(cartsave_reads(root, row)),
                on_card=on_card)


def forget_dump(root: str, row: library.Row,
                index: library.Index) -> list[str]:
    """Remove one dump from the library entirely. A list of problems.

    The ROM, the original under cart-dumps, every save read kept for it, and
    the index row. The card is not touched: this is about the library, and a
    dump still on the card is the thing that makes removing it recoverable.
    """
    problems = []
    for save in cartsaves(root, row):
        why = forget_cartsave(save)
        if why:
            problems.append(why)
    for path in (row.rom_path(root), row.dump_path(root)):
        if not path or not os.path.exists(path):
            continue
        try:
            os.remove(path)
        except OSError as e:
            problems.append(f"cannot remove {os.path.basename(path)}: {e}")
    index.drop(row.sha1)
    library.save(root, index)
    return problems


def same_saved_bytes(save: Save, root: str,
                     row: library.Row) -> str:
    """The library's copy of this save's bytes, or "" if it holds none.

    Named rather than searched: only this cartridge's own reads are looked
    at, so a delete can never be justified by some other game's file that
    happens to hash the same.
    """
    if not row.rom:
        return ""
    try:
        want = library.sha1_of(save.path)
    except OSError:
        return ""
    folder = library.cartsave_dir(root, row.rom)
    for name in library.cartsave_reads(root, row.rom):
        path = os.path.join(folder, name)
        try:
            if library.sha1_of(path) == want:
                return path
        except OSError:
            continue
    return ""


def same_bytes(a: str, b: str) -> bool:
    """True if two files hold identical bytes, stopping at the first difference.

    Written out rather than handed to filecmp because filecmp caches its answer
    against each file's stat signature, and a cached answer is the wrong kind
    of thing to put in front of a delete: a file replaced within the same
    second by one of the same length would compare equal without being read.

    A file that cannot be read is False, never an exception. Refusing to delete
    is always a safe answer.
    """
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                x = fa.read(CHUNK)
                y = fb.read(CHUNK)
                if x != y:
                    return False
                if not x:
                    return True
    except OSError as e:
        say.err(f"cannot compare {a} with {b}: {e}")
        return False


def row_for(dump: Dump, identity: Identity, rom: str | None = None,
            dump_name: str | None = None,
            was: library.Row | None = None,
            cartsaves: str | None = None) -> library.Row:
    """The index row for an imported dump. Observations only.

    Every field here is something a rebuild can ask again: the hashes off the
    bytes, the names off the directory listing, the last four off the DAT.
    There is deliberately nowhere in here to put an approval or a rejection --
    Row is a closed frozen field list for that reason, and a decision written
    into it would vanish at the next rebuild instead of merely being wrong.
    """
    return library.Row(
        sha1=dump.sha1, size=dump.size, crc32=dump.crc32,
        # The earliest date the library ever saw these bytes. Re-importing a
        # dump whose copy went missing is not the day it entered the library.
        imported=(was.imported if was and was.imported else _today()),
        rom=rom, dump=dump_name, cartsaves=cartsaves,
        title=identity.title, system=identity.system or None,
        region=identity.region, clone_of=identity.parent)


def _standing(path: str) -> Standing | None:
    """What is already at a destination, hashed. None if nothing is."""
    if not os.path.exists(path):
        return None
    d = nointro.digest(path)
    if d is None:
        return Standing(path)
    try:
        when = datetime.date.fromtimestamp(os.stat(path).st_mtime).isoformat()
    except OSError:
        when = ""
    return Standing(path, d.sha1, d.size, when)


def _destination(folder: str, base: str, sha1: str, standing: Standing | None,
                 choice: Choice) -> tuple[str, bool, str]:
    """(name to write, whether to write it, problem).

    Replace keeps the name and lets `_place` put the new file in before the old
    one goes; keep-both moves aside to a name made from the hash.
    """
    if standing is None:
        return base, True, ""
    if not standing.readable:
        return base, False, f"{standing.path} could not be read"
    if standing.sha1 == sha1:
        return base, False, ""
    if choice is Choice.REPLACE:
        return base, True, ""
    name = suffixed(base, sha1)
    other = _standing(os.path.join(folder, name))
    if other is None:
        return name, True, ""
    if other.readable and other.sha1 == sha1:
        return name, False, ""
    # Two different files sharing eight hex digits of SHA-1. Not worth a
    # counter to work around, and worth refusing rather than overwriting.
    return name, False, f"{name} is taken by something else"


def _place(src: str, dest: str) -> str:
    """Copy src to dest, verified byte for byte. "" on success, else why.

    Through a .tmp alongside the destination, then one os.replace, for two
    reasons. A half-written file is never visible under a name that means
    something, and `library.rebuild` skips .tmp files, so a copy interrupted by
    a crash leaves nothing the index will later claim is a dump. It is also
    what makes Replace safe: the new file is complete and verified before the
    old one stops existing, which one os.replace does in a single step.

    The comparison reads back through the page cache, so it proves the copy is
    complete and not truncated rather than proving the platter agrees. fsync
    first so that what it read is at least what was handed to the filesystem.
    """
    tmp = dest + ".tmp"
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(src, "rb") as fin, open(tmp, "wb") as fout:
            while block := fin.read(CHUNK):
                fout.write(block)
            fout.flush()
            os.fsync(fout.fileno())
    except OSError as e:
        _discard(tmp)
        return f"cannot copy {src}: {e}"
    if not same_bytes(src, tmp):
        _discard(tmp)
        return f"{dest} did not match {src}; nothing was written"
    try:
        os.replace(tmp, dest)
    except OSError as e:
        _discard(tmp)
        return f"cannot put {dest} in place: {e}"
    return ""


def _discard(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _today() -> str:
    return datetime.date.today().isoformat()


# ---------------------------------------------------------- prong 4: cheats --
@dataclass(frozen=True)
class Cheat:
    """The libretro cheat file a dump maps to, and how it was arrived at."""
    path: str | None = None
    score: float = 0.0
    via_parent: bool = False    # matched on the clone parent, not on this name
    chosen: bool = False        # the user pinned it; nothing was matched
    problem: str = ""

    @property
    def name(self) -> str:
        return os.path.splitext(os.path.basename(self.path))[0] if self.path \
            else ""

    def __bool__(self) -> bool:
        return self.path is not None


def cheat(identity: Identity, rom_path: str | None = None) -> Cheat:
    """The cheat file for an identified dump, mapped by default.

    This is the whole argument for the enrichment, and it needs no new matching
    code. `match.best()` is hopeless at ZELDA.gb and good at "Legend of Zelda,
    The - Link's Awakening (USA, Europe)", so renaming the dump is what makes
    the matcher the app already has work on it:

        ZELDA.gb -> SHA-1 -> the canonical name -> match.best() -> the file

    The DAT that matched also says which system the dump is, which is the other
    thing the matcher needs, and it is the only thing that can say it: Game Boy
    and Game Boy Color are one platform to the dumper and two cheat
    directories to libretro, and the ROM header cannot tell them apart.

    Where a clone has no cheat file of its own the parent's name is the obvious
    second attempt, since libretro names its files after No-Intro and a regional
    clone frequently has no file under its own name.
    """
    if rom_path:
        pinned = prefs.get_source(rom_path)
        if pinned:
            # Remembered exactly as it is for a ROM today, keyed on the
            # canonical basename. That key is safe here precisely because the
            # name is canonical: the core's own names collide and this one
            # cannot.
            return Cheat(path=pinned, chosen=True)
    if not identity.matched or not identity.name or not identity.system:
        return Cheat(problem="not identified, so there is nothing to match on")
    try:
        found = match.best(identity.name, identity.system)
        if found is None and identity.parent:
            found = match.best(identity.parent, identity.system)
            if found is not None:
                return Cheat(path=found.path, score=found.score,
                             via_parent=True)
    except cheatlib.MissingDatabase as e:
        return Cheat(problem=str(e))
    if found is None:
        return Cheat(problem="no cheat file is a close enough match")
    return Cheat(path=found.path, score=found.score)


def set_cheat(rom_path: str, cht_path: str | None) -> None:
    """Pin a cheat file to an imported dump, or forget the pin.

    A decision, so it goes where the app keeps decisions, through the same call
    a ROM on the card uses. Nothing about it belongs in the index.
    """
    prefs.set_source(rom_path, cht_path)


# ---------------------------------------------------------------- the sweep --
@dataclass
class Survey:
    """One pass over a card, computed and ready to be presented."""
    card_root: str
    root: str
    proposals: list[Proposal]
    note: str = ""            # which DATs were searched
    # Saves on the card with no ROM of the same stem beside them. Surfaced
    # rather than swallowed: it means the ROM was imported and its card copy
    # removed while the save was left, which is recoverable, and guessing
    # which cartridge it belongs to is not this module's business.
    unpaired: list[Save] = field(default_factory=list)

    @property
    def pending(self) -> list[Proposal]:
        """The ones worth putting in front of somebody, in order."""
        return [p for p in self.proposals if not p.quiet]

    @property
    def quiet(self) -> bool:
        return not self.pending

    def counted(self, verdict: Verdict) -> int:
        return sum(1 for p in self.proposals if p.verdict is verdict)

    def summary(self) -> str:
        """One line saying what this pass found.

        A fully processed card does nothing, and says so: that sentence is the
        point of the index, and silence would read as a failure to look.
        """
        total = len(self.proposals)
        if not total:
            return "No dumps on the card."
        if self.quiet:
            done = self.counted(Verdict.IMPORTED)
            turned = self.counted(Verdict.REJECTED)
            parts = [f"{done} already imported"] if done else []
            if turned:
                parts.append(f"{turned} ignored")
            return (f"Nothing to do: {total} dump{'s' if total > 1 else ''}, "
                    + " and ".join(parts) + ".")
        return (f"{len(self.pending)} of {total} "
                f"dump{'s' if total > 1 else ''} need attention.")


def survey(card_root: str, root: str, catalog: nointro.Catalog,
           index: library.Index | None = None, *,
           offer_rejected: bool = False) -> Survey:
    """Read a card and work out what would happen to everything on it.

    Nothing is written, including the index, which is loaded rather than
    rebuilt: this is the question an approval view asks before it draws
    anything, and asking it should not have side effects.
    """
    if index is None:
        index = library.load(root)
    # Paired by stem, which is the only link the core leaves between the two
    # files it writes for one cartridge. A save whose stem matches no ROM is
    # not paired to anything and is reported by unpaired_saves() instead of
    # being attached to whichever dump happens to be nearest.
    saves = {sv.stem: sv for sv in scan_saves(card_root)}
    proposals = [propose(d, identify(d, catalog), root, index,
                         offer_rejected=offer_rejected, save=saves.get(d.stem))
                 for d in scan(card_root)]
    paired = {p.save.stem for p in proposals if p.save is not None}
    return Survey(card_root, root, proposals, dat_note(catalog),
                  unpaired=[sv for stem, sv in sorted(saves.items())
                            if stem not in paired])
