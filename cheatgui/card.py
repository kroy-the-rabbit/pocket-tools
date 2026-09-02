# SPDX-License-Identifier: GPL-3.0-or-later
"""Find and read an Analogue Pocket SD card.

The Pocket's layout is fixed: platforms live under /Assets/<platform id>/, cores
under /Cores/, and each platform's display name comes from
/Platforms/<platform id>.json. Nothing here writes; see writer.py for that.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field

# The cartridge dumper's id, which is a platform id on the card in the sense
# that it owns /Assets/<id>/ and nothing more. Named here because card.py is
# where the app keeps what it knows about a card's layout; see dumps.py for
# what is in that directory.
DUMPER = "carttools"

# Where a filed dump goes when it is copied back: /Assets/cartdumps/<system>/.
#
# One folder at the top of Assets, split by system inside it, and not a folder
# under each system's common/. The Pocket's file browser is rooted at /Assets
# and filters by the data slot's extensions -- the "Back" entry at the top of
# the list walks up to Assets itself -- so every core can reach this, and the
# GB core browsing here sees "[Cartridge]: GB, GBC files". Verified on the real
# card, against Cores/kroy.GB/data.json, after the opposite was assumed and was
# wrong.
#
# Not common/ itself because a dump landing loose among the hundreds of ROMs
# already there is findable only by date, which is how the first version of
# this lost 33 of them in plain sight.
#
# The cost is that scan() walks /Assets/<pid> and so does not see these: a
# copied-back dump is not listed under its system. It does not need to be. It
# is listed under Cartridge dumps, on_card() stats this path directly, and
# show_shelf() reads each cht file rather than asking a scan whether it exists.
CARTDUMPS = "cartdumps"


def cartdumps_dir(root: str, pid: str) -> str:
    """The folder on the card holding imported dumps for one system."""
    return os.path.join(root, "Assets", CARTDUMPS, pid)


def cartsaves_dir(root: str, pid: str) -> str:
    """Where the Pocket keeps save RAM for the dumps in cartdumps_dir().

    /Saves mirrors /Assets below the top level, so a ROM at
    Assets/cartdumps/<pid>/<name>.<ext> saves to
    Saves/cartdumps/<pid>/<name>.sav. Not inferred from the APF documentation:
    the Pocket created these three by itself, from dumps copied back by this
    app, and they are what a restored save has to be named to be picked up.

        Assets/cartdumps/gbc/Super Mario Bros. Deluxe (Europe) (Rev 2).gbc
        Saves/cartdumps/gbc/Super Mario Bros. Deluxe (Europe) (Rev 2).sav
    """
    return os.path.join(root, "Saves", CARTDUMPS, pid)


def save_beside(rom_path: str) -> str:
    """The save file the Pocket would write for a ROM sitting at `rom_path`.

    The mechanism only, and it is deliberately not a decision about where a
    dump goes. Imported dumps have one destination, cartdumps_dir(), and this
    is called on a path already under it. The mirror is general because the
    Pocket's is -- any Assets subdirectory saves to the matching Saves one --
    but nothing here may use that to put a dump anywhere else.
    """
    head, tail = os.path.split(rom_path)
    parts = head.split(os.sep)
    for i, part in enumerate(parts):
        if part == "Assets":
            parts[i] = "Saves"
            break
    else:
        raise ValueError(f"not a path under Assets: {rom_path}")
    return os.path.join(os.sep.join(parts), os.path.splitext(tail)[0] + ".sav")

# Pocket platform id -> the libretro cheat database directory for it, and the
# ROM extension that goes with it. Everything the app knows how to handle is
# here whether or not it is switched on; ENABLED below decides what it offers.
KNOWN = {
    "gb":  ("Nintendo - Game Boy", ".gb"),
    "gbc": ("Nintendo - Game Boy Color", ".gbc"),
    "gba": ("Nintendo - Game Boy Advance", ".gba"),
    "pce": ("NEC - PC Engine - TurboGrafx 16", ".pce"),
    # The dumper. Both fields are empty because neither exists for it: it is
    # not a system you play, so libretro has no cheat directory for it, and its
    # output carries .gb, .gbc or .gba rather than an extension of its own. The
    # two derived sets below drop an entry with nothing in the field they need,
    # so switching this on cannot quietly make every extensionless file on the
    # card look like a ROM or list a dumper as a system with games.
    DUMPER: ("", ""),
}

# The systems the app actually offers, in the order they are listed.
#
# **Game Boy Advance is back on.** It was switched off because its core had no
# cheat data slot and its codes could not be decoded, so it offered a system, a
# game list and a set of checkboxes that could do nothing in either direction.
# Both halves of that have since stopped being true: the core defines slot 7,
# and gbacht decodes CodeBreaker and GameShark against the whole libretro
# directory. What it does not do is read text on the handheld, so this is the
# one system where what lands on the card is not the file that was picked from.
# See gba.py, and writer.py for the two files.
#
# **PC Engine is on**, and its core is released and verified working, so a file
# written for a PC Engine game takes effect like any other: see pce.py.
#
# `.sgx` is deliberately not in KNOWN. The PC Engine core drops SuperGrafx to
# buy the room its cheat engine needs, so a SuperGrafx ROM will not run
# correctly on it and offering to write cheats for one would be a lie. For the
# same reason the libretro SuperGrafx and PC Engine CD directories are not
# mapped: this core runs neither.
#
# **The dumper is not in ENABLED**, and that is not about whether it is
# released. `carttools` is in KNOWN because the app knows the id and reads the
# directory it owns. A system in this tuple is one the app writes cheat files
# for, and the dumper reads none and writes none: it produces ROM images. Its
# dumps reach the app through dumps.py and the Cartridge dumps category, not
# through this list. See core.CORES, where it is a core like any other.
ENABLED = ("gb", "gbc", "gba", "pce")

# An entry with nothing in the field a set needs is dropped rather than carried
# as an empty string. An empty extension would match every file on the card
# that has none, and an empty cheat directory would put a dumper in the list of
# systems to browse for games.
SUPPORTED = {p: KNOWN[p][0] for p in ENABLED if KNOWN[p][0]}
ROM_EXT = {KNOWN[p][1] for p in ENABLED if KNOWN[p][1]}

# What to call each system before the card has been asked. The card carries
# its own names in /Platforms/<id>.json, and reading those three small files
# cost 2.6 seconds on a cold card, in front of an empty window, to arrive at
# the names below. So these are used immediately and the card's own are read
# afterwards, in the background, in case it disagrees.
DISPLAY = {
    "gb":  "Game Boy",
    "gbc": "Game Boy Color",
    "gba": "Game Boy Advance",
    "pce": "PC Engine",
}

# Folders skipped when listing games. Romhacks are usually pre-patched variants
# of a ROM that is already in the list, and they do not match anything in the
# cheat database, so they only add noise. Nothing is hidden from the card, only
# from this tool.
SKIP_DIRS = {"romhacks"}


class EjectError(Exception):
    """The card could not be unmounted; the message says why."""


@dataclass
class Game:
    path: str                 # absolute path to the ROM
    platform: str             # Pocket platform id, e.g. "gbc"

    @property
    def name(self) -> str:
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def cht_path(self) -> str:
        """Where APF looks for this ROM's cheat file.

        A data slot whose filename is cloned from slot 0 gets this slot's
        extension *appended*, so it is "<rom filename>.cht", not the ROM name
        with its extension swapped.
        """
        return self.path + ".cht"

    @property
    def subdir(self) -> str:
        """Folder below the platform's asset root, for display ("" at the top)."""
        root = os.path.join(os.path.dirname(self.path))
        return root


@dataclass
class Platform:
    id: str
    name: str
    games: list[Game] = field(default_factory=list)
    # Absolute paths of the .cht files found beside those ROMs, from the same
    # directory walk. Asking the filesystem per game instead is one stat each,
    # and on a card over USB that is slow enough to freeze the window.
    cheat_files: frozenset[str] = frozenset()
    # False until something has actually walked this system's directory. An
    # unscanned platform is not an empty one, and the difference matters:
    # empty means "no ROMs", unscanned means "nobody has looked yet".
    scanned: bool = False

    def has_cheats(self, game: "Game") -> bool:
        return game.cht_path in self.cheat_files


@dataclass
class Card:
    root: str
    label: str = ""

    def platforms(self) -> list[Platform]:
        """Which systems are on the card. Deliberately does not read them.

        Walking all three took 27 seconds on a real card that had just been
        mounted, and the window sat empty and unusable for every one of them.
        The tree is only a few hundred files; it is exFAT over USB with a cold
        cache, where each one costs tens of milliseconds however few of them
        there are.

        So this answers the cheap question, which systems exist, and the
        expensive one is asked per system by fill() when somebody actually
        looks at it. You then wait for the system you picked rather than for
        all of them, and only the first time.
        """
        out = []
        for pid in sorted(SUPPORTED):
            adir = os.path.join(self.root, "Assets", pid)
            if not os.path.isdir(adir):
                continue
            out.append(Platform(pid, DISPLAY.get(pid, pid.upper())))
        return out

    def fill(self, plat: Platform) -> Platform:
        """Read one system's ROMs and cheat files. Slow on a cold card.

        Never call this on the Tk thread. It is the same object back, so the
        caller can hand it straight to whatever draws it.
        """
        if not plat.scanned:
            # The card's own name for the system, now that we are reading it
            # anyway and not holding up the window.
            plat.name = self.platform_name(plat.id)
            plat.games, plat.cheat_files = self.scan(plat.id)
            plat.scanned = True
        return plat

    def platform_name(self, pid: str) -> str:
        """What this card calls a system. Reads a file, so not on first paint."""
        path = os.path.join(self.root, "Platforms", f"{pid}.json")
        try:
            with open(path) as f:
                return json.load(f)["platform"]["name"]
        except Exception:                                    # noqa: BLE001
            return DISPLAY.get(pid, pid.upper())

    def scan(self, pid: str) -> tuple[list[Game], frozenset[str]]:
        """ROMs and the cheat files beside them, from one walk of the tree.

        Both come out of the same os.walk deliberately. The directory listing
        already names every file, so asking the filesystem again whether each
        ROM has a .cht costs one stat per game and tells us nothing new. On a
        card read over USB with a cold cache that is hundreds of blocking calls
        on the UI thread, which is exactly what it looks like: a dead window.
        """
        adir = os.path.join(self.root, "Assets", pid)
        found: list[Game] = []
        chts: set[str] = set()
        for dirpath, dirs, files in os.walk(adir):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in ROM_EXT:
                    found.append(Game(os.path.join(dirpath, f), pid))
                elif ext == ".cht":
                    chts.add(os.path.join(dirpath, f))
        found.sort(key=lambda g: g.name.lower())
        return found, frozenset(chts)

    def games(self, pid: str) -> list[Game]:
        return self.scan(pid)[0]

    def sync(self) -> None:
        """Push writes out of the page cache. Windows does this on close."""
        if os.name != "nt":
            subprocess.run(["sync"], check=False)

    def device(self) -> str | None:
        """The block device this card is mounted from, on Linux."""
        try:
            out = subprocess.run(
                ["findmnt", "-rn", "-o", "SOURCE", "--target", self.root],
                capture_output=True, text=True, timeout=5, check=True).stdout
        except Exception:                                    # noqa: BLE001
            return None
        return out.strip().splitlines()[0] if out.strip() else None

    def unmount(self) -> str:
        """Flush writes and unmount the card. Returns what to tell the user.

        Raises EjectError with the tool's own message if it could not be done,
        which is usually a file still open on the card, and that message names
        the process. Nothing here forces anything: a card yanked mid-write is
        the failure this whole tool exists to avoid.
        """
        self.sync()

        if sys.platform == "darwin":
            attempts = [["diskutil", "unmount", self.root]]
        elif os.name == "nt":
            drive = os.path.splitdrive(os.path.abspath(self.root))[0]
            if not drive:
                raise EjectError(f"{self.root} is not on a drive letter")
            # The shell's own Eject verb, the same one Explorer uses. There is
            # no supported command line equivalent.
            attempts = [["powershell", "-NoProfile", "-Command",
                         "$sh = New-Object -comObject Shell.Application; "
                         f"$sh.Namespace(17).ParseName('{drive}')"
                         ".InvokeVerb('Eject')"]]
        else:
            # udisks first: it is what the desktop uses, needs no privilege for
            # removable media, and powers down the reader afterwards. Plain
            # umount is the fallback for a card mounted by hand or in fstab.
            dev = self.device()
            attempts = []
            if dev:
                attempts.append(["udisksctl", "unmount", "-b", dev])
            attempts.append(["umount", self.root])

        problems = []
        for cmd in attempts:
            try:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=30)
            except FileNotFoundError:
                problems.append(f"{cmd[0]}: not installed")
                continue
            except subprocess.TimeoutExpired:
                problems.append(f"{cmd[0]}: timed out")
                continue
            if r.returncode == 0:
                return f"{self.root} unmounted, safe to remove"
            msg = (r.stderr or r.stdout or "").strip().splitlines()
            problems.append(f"{cmd[0]}: {msg[-1] if msg else 'failed'}")

        raise EjectError("; ".join(problems) or "could not unmount")


def looks_like_card(path: str) -> bool:
    """A Pocket card always has both of these; a muOS or plain ROM card does not."""
    return all(os.path.isdir(os.path.join(path, d)) for d in ("Cores", "Platforms"))


def _linux_mounts() -> list[tuple[str, str]]:
    """(mount point, label) for the places a card gets mounted on Linux."""
    try:
        # Timeout on purpose. This runs off the Tk thread but still blocks the
        # scan, and findmnt hangs on an unresponsive mount; without it the
        # pane sits empty with nothing on screen to say why.
        out = subprocess.run(["findmnt", "-rn", "-o", "TARGET,LABEL"],
                             capture_output=True, text=True, check=True,
                             timeout=5).stdout
    except Exception:                                        # noqa: BLE001
        return []
    found = []
    for line in out.splitlines():
        parts = line.split(" ", 1)
        target = parts[0]
        label = parts[1].strip() if len(parts) > 1 else ""
        if target.startswith(("/run/media/", "/media/", "/mnt/")):
            found.append((target, label))
    return found


def _macos_mounts() -> list[tuple[str, str]]:
    """Everything under /Volumes. The volume name is the label."""
    found = []
    try:
        names = os.listdir("/Volumes")
    except OSError:
        return found
    for name in sorted(names):
        path = os.path.join("/Volumes", name)
        if os.path.isdir(path):
            found.append((path, name))
    return found


def _windows_mounts() -> list[tuple[str, str]]:
    """Every drive letter that answers. The volume label needs no extra call.

    Reading the label is a best effort: a card with none is still a card, and
    on Windows the letter is what people recognize anyway.
    """
    import string
    found = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if not os.path.isdir(root):
            continue
        label = ""
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(261)
            if ctypes.windll.kernel32.GetVolumeInformationW(
                    ctypes.c_wchar_p(root), buf, ctypes.sizeof(buf),
                    None, None, None, None, 0):
                label = buf.value
        except Exception:                                    # noqa: BLE001
            pass
        found.append((root, label or letter + ":"))
    return found


def mounts() -> list[tuple[str, str]]:
    """Candidate volumes for this platform, before any of them are inspected."""
    if sys.platform == "darwin":
        return _macos_mounts()
    if os.name == "nt":
        return _windows_mounts()
    return _linux_mounts()


def find_cards() -> list[Card]:
    """Mounted volumes that look like a Pocket card.

    POCKET_CARD overrides the search with an explicit path, for a card that
    mounts somewhere unusual and for testing against a fixture tree.

    Each platform is asked a different question, because the answer lives
    somewhere different: findmnt on Linux, /Volumes on macOS, drive letters on
    Windows. What makes a card a card is the same everywhere, and
    looks_like_card() is the only thing that decides it.
    """
    forced = os.environ.get("POCKET_CARD")
    if forced:
        return [Card(forced, "POCKET_CARD")] if looks_like_card(forced) else []
    return [Card(path, label) for path, label in mounts()
            if looks_like_card(path)]
