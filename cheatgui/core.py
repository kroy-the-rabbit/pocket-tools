# SPDX-License-Identifier: GPL-3.0-or-later
"""The Pocket core that reads the files this app writes.

This app writes `.cht` files and nothing else. A stock Pocket core ignores
them completely, so on a card without the cheat core every button here is a
no-op that looks like it worked. That was the single worst first-run footgun,
and a link in a README does not fix it: the card is right there, the app can
already see which cores are on it, and it can put the current one there.

Two things are checked, and they fail in different ways:

  the core     out of date or absent. Fetched from the release page, verified
               to look like a Pocket core, and unpacked onto the card.
  the boot ROM absent. Named, sized, and located, and that is all. They are
               copyrighted console code; this app does not carry them, will
               not fetch them, and says where yours has to go.

The release zips are not signed, so what is checked is what can be: the
download comes over a verified connection from the release the API named, and
the archive is refused unless every path in it lands inside the card and it
holds the core it claims to. See _members().

Standard library only, and nothing here touches Tk. The GUI runs install() on
a work.Job and reports progress through the callback.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass

from db import UA, ssl_context          # one CA story for the whole app

API = "https://api.github.com/repos"

TIMEOUT = 30
CHUNK = 64 * 1024

# A Pocket core zip is a couple of megabytes. Anything wildly past that is not
# one, and unpacking it onto somebody's card to find out is the wrong order to
# do things in.
MAX_ZIP = 64 * 1024 * 1024

# Where a half-finished install lives while it is being written. On the card
# so that putting the files in place is a rename rather than a copy, dotted so
# the Pocket's own menus pass over it, and removed either way.
# Old name kept on purpose: it is a staging directory on somebody's card
# and a rename would strand one left behind by an interrupted install.
STAGING = ".pocket-cheats-install"

# What the card-reading pass calls this step in its progress report. Not a
# platform id, so nothing can mistake it for one.
STEP = "cores"


class Cancelled(Exception):
    """Raised out of install() when the caller asked it to stop."""


@dataclass(frozen=True)
class Rom:
    """A file the core needs and this app must not supply.

    A boot ROM is copyrighted console code. The core declares which ones it
    wants and how big each has to be; the most this app will ever do is tell
    you that yours is not there.
    """
    filename: str
    size: int
    what: str
    # Other names the core accepts in the same slot, from the manifest's
    # `alternate_filenames`. Any one of them satisfies the slot; `filename` is
    # the one to recommend when none is there. The PC Engine core's System
    # Card is the case: it asks for bios_3_0_usa.pce and takes four others.
    alternates: tuple[str, ...] = ()


@dataclass(frozen=True)
class Core:
    """One Pocket core: a directory under /Cores and a zip in a release."""
    id: str                     # the /Cores directory name, "<author>.<name>"
    platform: str               # the Pocket platform id it runs
    title: str                  # what to call it in a sentence
    asset: str                  # start of its filename in the release
    repo: str | None            # where its releases come from, None if none yet
    bios: tuple[Rom, ...] = ()  # what it needs, when it is not installed yet
    optional: bool = False      # belongs to a feature that is off by default


GBC_REPO = "kroy-the-rabbit/openfpga-GBC-cheats"
GBA_REPO = "kroy-the-rabbit/openfpga-GBA-cheats"
PCE_REPO = "kroy-the-rabbit/openfpga-pcengine-cheats"
CARTTOOLS_REPO = "kroy-the-rabbit/openfpga-carttools"

# The cores this app writes cheat files for.
#
# The repository is per core rather than per app because it stopped being one
# repository: openfpga-GBC-cheats releases both Game Boy cores together, and PC
# Engine ships from a fork of a different core entirely, and Game Boy Advance
# from a fork of mincer-ray/openfpga-GBA, which is a third.
#
# A core with `repo=None` has no release to install yet. It is still listed,
# because a card may already carry a hand-built copy and reporting its version
# and its boot ROMs is useful; nothing offers to install or update it.
#
# The boot ROMs listed are what this app knows a core needs. They are the whole
# answer for a card with no core on it yet, and once one is installed they are
# unioned with what its own data.json declares, so a core that starts wanting a
# different file says so itself and a core that mismarks one we already know
# about cannot drop it. See wanted(). An empty tuple is a real answer, not a
# missing one: a PC Engine HuCard needs no boot ROM. A PC Engine CD needs a
# System Card, and the core says so itself, in slot 0's `filename` and
# `alternate_filenames`, so it is not written here.
CORES = (
    Core("kroy.GBC", "gbc", "Game Boy Color", "kroy.GBC_", GBC_REPO,
         (Rom("gbc_bios.bin", 2304, "GBC BIOS"),)),
    Core("kroy.GB", "gb", "Game Boy", "kroy.GB_", GBC_REPO,
         (Rom("gb_bios.bin", 256, "DMG BIOS"),
          Rom("sgb_boot.bin", 256, "SGB BIOS"))),
    # This one had no release for a while, and nothing here had to change when
    # it got one: released() asks the release map rather than this field, so a
    # core is offered for install the moment a tag lands at the other end.
    Core("kroy.GBA", "gba", "Game Boy Advance", "kroy.GBA_", GBA_REPO,
         (Rom("gba_bios.bin", 16384, "GBA BIOS"),)),
    # Upstream ships as "agg23.PC Engine", with a space in the directory name.
    # The fork renames to kroy.PCE, to match the others and so nothing here has
    # to handle the space. A card carrying the upstream core alongside is not
    # seen, which is right: it is a different core and reads no cheat files.
    #
    # The fork's v0.2.0 shipped as "kroy.PC Engine" and so matched nothing here
    # and reported as unreleased; v0.2.1 renames it. Its manifest check now
    # fails on a directory name with a space, so that cannot recur silently.
    Core("kroy.PCE", "pce", "PC Engine", "kroy.PCE_", PCE_REPO, ()),
    # The dumper, and the only optional core. It writes no cheat files and
    # reads none: it reads cartridges and writes ROM images to the card, which
    # is the other half of the cartridge dump feature. It is surveyed and
    # offered like every other core, because the installer is where anyone
    # looks for a core. What `optional` changes is that an absent one is never
    # counted as an update, so it is never ticked for you, and the dump surface
    # in the window follows whether it is actually on the card. It needs no
    # boot ROM, because it runs no games.
    Core("kroy.CartTools", "carttools", "Cartridge Tools", "kroy.CartTools_",
         CARTTOOLS_REPO, (), optional=True),
)


DUMPER = "kroy.CartTools"


def dumper_installed(sv: "Survey | None") -> bool:
    """Whether the card carries the cartridge dumper.

    The dump surface follows this and nothing else. There is no preference of
    its own, because the feature is the other half of that core: a card
    without it produces no dumps, and a card with it was given the core on
    purpose. Installing the core is the act of opting in, and it is an install
    nothing here performs unasked.
    """
    return bool(sv and sv.versions.get(DUMPER))


def repos() -> tuple[str, ...]:
    """Every repository the cores come from, once each, in listed order."""
    out: list[str] = []
    for c in CORES:
        if c.repo and c.repo not in out:
            out.append(c.repo)
    return tuple(out)


def releases_page(repo: str) -> str:
    return f"https://github.com/{repo}/releases"


def released(platform: str, rels: dict[str, dict] | None = None) -> bool:
    """Whether anything on this system has a core you can actually install.

    The question a caller is really asking is "will anything on the handheld
    act on the file I just wrote", so a repository with nothing published at it
    counts as no. Game Boy Advance is exactly that case: its repo exists and its
    CI publishes on a tag, and until one is pushed there is no core to install.
    It answers False, and starts answering True on its own.

    A core may also have no repository at all, which answers False the same way
    but never changes by itself. Every core listed here has one now - the PC
    Engine was the last without - so that branch is reached only if one is
    added, which is why the tests build a registry to cover it.

    Without `rels` this can only fall back to whether a repository is named,
    which is the best guess available before the release check has answered.
    Pass the release map when you have it.
    """
    mine = [c for c in CORES if c.platform == platform]
    if rels is None:
        return any(c.repo for c in mine)
    return any(release_for(c, rels) for c in mine)


# ------------------------------------------------------------------ the card --
def installed(root: str) -> dict[str, str | None]:
    """Core id -> the version on the card, or None when it is not there.

    Reads only the cores this app knows about. A well used card has a hundred
    of them and opening every core.json to find two costs seconds over USB.
    """
    out: dict[str, str | None] = {}
    for c in CORES:
        path = os.path.join(root, "Cores", c.id, "core.json")
        try:
            with open(path) as f:
                out[c.id] = json.load(f)["core"]["metadata"]["version"]
        except Exception:                                    # noqa: BLE001
            out[c.id] = None
    return out


def wanted(root: str, core: Core) -> tuple[Rom, ...]:
    """Every fixed-name file this core needs: what it declares, and the table.

    An installed core's data.json lists every slot it loads. A slot with a
    fixed `filename` is one the Pocket fills without asking, which means it is
    one you had to put there yourself; the browsable slots (the cartridge, the
    save) have no filename and are not this. Reading it rather than trusting
    the table above means a core that adds a boot ROM is reported correctly by
    a copy of this app that predates it.

    The two are unioned rather than the table being a fallback, because a core
    can be wrong about itself. The Game Boy Advance core declared gba_bios.bin
    `required: false` where both Game Boy cores mark theirs true, while its own
    README said the core will not start a game without it. That manifest is
    fixed now, but every card carrying a release built before the fix still
    has the old declaration on it, and under a fallback such a card keeps
    nothing, drops through to the table, and gets the right answer by luck:
    delete the table entry and the GBA boot ROM silently stops being reported.
    The union keeps the reason the lookup exists while leaving a core that
    mismarks a file we already know about unable to drop it.

    The cost is that a core which legitimately stops needing a boot ROM keeps
    being asked for one until the table is edited. The table is ours and that
    is a one-line edit, against a failure mode that otherwise looks like a
    working install and then refuses to start anything.
    """
    path = os.path.join(root, "Cores", core.id, "data.json")
    try:
        with open(path) as f:
            slots = json.load(f)["data"]["data_slots"]
    except Exception:                                        # noqa: BLE001
        return core.bios
    table = {r.filename: r for r in core.bios}
    found: list[Rom] = []
    for s in slots:
        name = s.get("filename")
        if not name or not s.get("required"):
            continue
        # Where the core and the table disagree about a size, the core wins.
        # `required` is a claim about whether a file matters, which is the sort
        # of thing a core is careless about; `size_exact` is what the framework
        # itself checks when it fills the slot, so a file that does not match
        # the installed core's number will not load however sure the table is.
        # The table is also a snapshot of what the core wanted when this app
        # shipped, and the card is carrying the version being run.
        #
        # A core that names no size has not disagreed, it has said nothing, so
        # the table's number stands rather than being read as "any size".
        size = int(s.get("size_exact") or 0)
        if not size and name in table:
            size = table[name].size
        alts = tuple(a for a in (s.get("alternate_filenames") or [])
                     if isinstance(a, str) and a and a != name)
        found.append(Rom(name, size, s.get("name") or name, alts))
    named = {r.filename for r in found}
    found.extend(r for r in core.bios if r.filename not in named)
    return tuple(found)


def fixed_names(root: str, platform: str) -> frozenset[str]:
    """Every fixed-name file any core for this platform wants, by basename.

    A System Card is a .pce file in the games folder, and it is not a game.
    The card scan asks this so it can leave such files out of the list: the
    core declares them, this app knows them, and offering cheats for a boot
    ROM would be a lie. Names come from wanted(), so a core that is not
    installed still contributes its table entry, and one that is installed
    contributes what its manifest says, alternates included.
    """
    out: set[str] = set()
    for c in CORES:
        if c.platform != platform:
            continue
        for rom in wanted(root, c):
            out.add(rom.filename)
            out.update(rom.alternates)
    return frozenset(out)


def rom_dirs(root: str, core: Core) -> list[str]:
    """Where the Pocket looks for this core's fixed-name files.

    `common` is shared by every core for the platform, and is where the core's
    own install notes tell you to put the boot ROM. A core-specific directory
    is the other place the framework accepts, so a file already sitting there
    counts as present; it is not what gets recommended.
    """
    base = os.path.join(root, "Assets", core.platform)
    return [os.path.join(base, "common"), os.path.join(base, core.id)]


@dataclass
class RomState:
    core: Core
    rom: Rom
    path: str | None        # where it was found, None when it was not
    size: int               # its size on the card, 0 when absent

    @property
    def ok(self) -> bool:
        return self.path is not None and not self.wrong_size

    @property
    def wrong_size(self) -> bool:
        return bool(self.path and self.rom.size and self.size != self.rom.size)

    @property
    def where(self) -> str:
        """The path to recommend, relative to the card, for a missing file."""
        return os.path.join("Assets", self.core.platform, "common",
                            self.rom.filename)


def boot_roms(root: str) -> list[RomState]:
    """Every fixed-name file the installed cores need, and whether it is there.

    Only for cores that are actually on the card. A boot ROM for a core you
    have not installed is not missing, it is irrelevant, and listing it would
    make the one that does matter harder to see.
    """
    here = installed(root)
    out: list[RomState] = []
    for c in CORES:
        if here.get(c.id) is None:
            continue
        dirs = rom_dirs(root, c)
        for rom in wanted(root, c):
            state = RomState(c, rom, None, 0)
            # The named file first, in every directory, then each alternate:
            # any of them fills the slot, and the recommended one wins when
            # more than one is present.
            for name in (rom.filename, *rom.alternates):
                for d in dirs:
                    p = os.path.join(d, name)
                    try:
                        state.size = os.path.getsize(p)
                        state.path = p
                        break
                    except OSError:
                        continue
                if state.path:
                    break
            out.append(state)
    return out


@dataclass
class Survey:
    """What one look at the card found. Built off the Tk thread."""
    root: str
    versions: dict[str, str | None]
    roms: list[RomState]

    def any_core(self) -> bool:
        return any(v for v in self.versions.values())

    def problems(self) -> list[RomState]:
        return [r for r in self.roms if not r.ok]


def survey(root: str) -> Survey:
    """Which cores are on this card and whether they can run. A few files."""
    return Survey(root, installed(root), boot_roms(root))


# --------------------------------------------------------------- the release --
def latest(repo: str, timeout: int = TIMEOUT) -> dict | None:
    """One repository's newest release, or None when it has none yet.

    A repository with no release answers 404, which is an answer rather than a
    failure. Every core in CORES has a tag now, PC Engine included, so nothing
    here is expected to answer 404 today; the branch stays because a fork can
    lose its releases and because a new core is added with none.
    Anything else raises, so being offline still reads as being offline.
    """
    req = urllib.request.Request(
        f"{API}/{repo}/releases/latest",
        headers={**UA, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl_context()) as f:
            rel = json.load(f)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    tag = rel.get("tag_name") or ""
    return {
        "repo": repo,
        "tag": tag,
        # core.json carries the tag without its leading v, which is what an
        # installed version is compared against.
        "version": tag[1:] if tag.startswith("v") else tag,
        "page": rel.get("html_url") or releases_page(repo),
        "assets": {a["name"]: a["browser_download_url"]
                   for a in rel.get("assets") or []},
    }


def all_latest(timeout: int = TIMEOUT) -> dict[str, dict]:
    """The newest release of every repository the cores come from.

    Keyed by repository. A repository with no release is simply absent, so
    everything downstream asks the same question of it as of one this app has
    never heard of.
    """
    out = {}
    for repo in repos():
        rel = latest(repo, timeout)
        if rel is not None:
            out[repo] = rel
    return out


def release_for(core: Core, rels: dict[str, dict]) -> dict | None:
    return rels.get(core.repo) if core.repo else None


def asset_for(core: Core, rels: dict[str, dict]) -> str | None:
    rel = release_for(core, rels)
    if rel is None:
        return None
    for name, url in sorted(rel.get("assets", {}).items()):
        if name.startswith(core.asset) and name.endswith(".zip"):
            return url
    return None


def outdated(versions: dict[str, str | None],
             rels: dict[str, dict]) -> list[Core]:
    """The cores that would change if the newest releases were installed.

    Absent counts, and so does any version that is not the released one:
    a card carrying a newer local build is still not carrying this release,
    and saying "up to date" about it would be a guess at which is newer.

    A core with no release is never in here. There is nothing to install.
    """
    out = []
    for c in CORES:
        rel = release_for(c, rels)
        if rel is None or not rel.get("version"):
            continue
        # An optional core that is not on the card is not out of date, it is
        # simply not installed. Saying otherwise would put "update available"
        # on the status line for a core nobody asked for, and would tick it
        # by default in the installer, which is the one thing an optional
        # install must never do. Once it is on the card it updates like any
        # other core.
        if c.optional and versions.get(c.id) is None:
            continue
        if versions.get(c.id) != rel["version"] and asset_for(c, rels):
            out.append(c)
    return out


def describe(sv: Survey | None,
             rels: dict[str, dict] | None) -> tuple[str, bool]:
    """One line for the status bar, and whether it is bad news.

    This named every installed core and its version before it said anything
    about staleness, which meant one non-wrapping label in a grid cell was
    answering two questions that both grow with the number of cores: what
    have I got, and is any of it stale. Measured against a real card with
    four cores on it that came to 94 characters offline, 106 with everything
    current and 149 with three of the four behind, and the cartridge dumper
    is a fifth core carrying `kroy.CartTools 0.0.1.41e8d8a`, which is another
    thirty and puts the worst case past 190 in a window whose minimum width
    is 1100 pixels. The line does not wrap. It ran out of window.

    The first question is a table and CoresDialog already draws it, a row per
    core with what the card has beside what is available, which is strictly
    better than the same data comma-separated. Only the second question
    belongs on a status bar, so this counts rather than enumerates and its
    length stops depending on how many cores this app knows about. The
    Cores... button sits next to the label and answers "which ones".
    """
    if sv is None:
        return "Pocket core: no card", False
    n = sum(1 for v in sv.versions.values() if v)
    if not n:
        # The most important sentence in the window on a stock card, and the
        # reason this state is worded rather than counted: every button in
        # this app writes a file that nothing on the handheld will read, and
        # a bare "0 installed" does not say that to anyone.
        return ("Pocket core: not installed. Nothing written here has any "
                "effect until it is."), True
    line = f"Pocket core: {n} installed"
    if not rels:
        # No release data, because nobody has asked yet or because the ask
        # could not reach GitHub. The count with no verdict after it is the
        # only honest ending: "all up to date" is a claim this app has not
        # checked and would be wrong exactly when it matters, and spelling
        # the gap out - "update status unknown" - reports the network as a
        # fault with the card and earns the red that belongs to a real one.
        # A machine that has not asked does not know, and saying only what it
        # does know reads as neither a problem nor a clean bill of health.
        # When the ask was tried and failed the caller has better information
        # than this function does, and ui.py appends its own "(could not
        # reach the release page)" to say so; there is nothing to add here.
        return line, False
    behind = outdated(sv.versions, rels)
    if not behind:
        return line + ", all up to date", False
    # Exactly the cores CoresDialog would offer to write, counted instead of
    # named. "1 updates available" is the kind of wrong that makes a careful
    # line look generated, so the plural is agreed with the number.
    s = "" if len(behind) == 1 else "s"
    return f"{line}, {len(behind)} update{s} available", True


def describe_roms(sv: Survey | None) -> tuple[str, bool]:
    if sv is None or not sv.any_core() or not sv.roms:
        # No line rather than "0 present". A core that needs no boot ROM is a
        # real thing, not a card with something missing: the PC Engine has
        # none, and saying "0 present" about it reads like a fault.
        return "", False
    bad = sv.problems()
    if not bad:
        return f"boot ROMs: {len(sv.roms)} present", False
    missing = [r for r in bad if r.path is None]
    wrong = [r for r in bad if r.wrong_size]
    parts = []
    if missing:
        parts.append("missing " + ", ".join(r.rom.filename for r in missing))
    if wrong:
        parts.append("wrong size: " + ", ".join(r.rom.filename for r in wrong))
    return ("boot ROMs: " + "; ".join(parts)
            + ". The core will not start a game without them."), True


# ---------------------------------------------------------------- installing --
def _fetch(url: str, say, stop, timeout: int) -> bytes:
    """Download one zip into memory, reporting bytes as they arrive."""
    req = urllib.request.Request(url, headers=UA)
    buf = io.BytesIO()
    with urllib.request.urlopen(req, timeout=timeout,
                                context=ssl_context()) as f:
        total = int(f.headers.get("Content-Length") or 0)
        if total > MAX_ZIP:
            raise RuntimeError(f"that download is {total} bytes, which is not "
                               "a Pocket core")
        while True:
            if stop():
                raise Cancelled()
            chunk = f.read(CHUNK)
            if not chunk:
                break
            buf.write(chunk)
            if buf.tell() > MAX_ZIP:
                raise RuntimeError("that download is too big to be a Pocket core")
            say(buf.tell(), total)
    return buf.getvalue()


def _owned(parts: list[str], core: Core) -> bool:
    """Whether a release zip is allowed to carry this path at all.

    Staying inside the card is not enough. The card also holds ROMs, saves,
    cheat files and boot ROMs, and a core release has no business naming any
    of them. So the shapes are listed rather than the dangerous ones excluded:
    an archive that names anything else is refused whole, because a core
    release that reaches outside its own directories is not a core release.

      Cores/<this core>/...   the core itself
      Platforms/...           the platform name and its image, shared
      Assets/<its platform>/  its own asset tree, and see _place: nothing
                              already there is overwritten
      <a top-level file>      instructions.txt and its like, same rule

    Note what is absent: another core's directory, another platform's assets,
    Saves, and anything nested at the root.
    """
    if parts[:2] == ["Cores", core.id]:
        return True
    if parts[0] == "Platforms":
        return True
    if parts[:2] == ["Assets", core.platform]:
        return True
    return len(parts) == 1


def _members(zf: zipfile.ZipFile, core: Core) -> list[zipfile.ZipInfo]:
    """The files in the zip, once it has been checked to be the right one.

    A zip decides its own paths, and this one is unpacked onto the root of
    somebody's SD card. An entry naming an absolute path, a drive, or a parent
    directory would land outside the card entirely, so the whole archive is
    refused rather than that entry skipped: an archive containing one is not a
    core release with a flaw in it, it is not a core release.
    """
    out = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        parts = name.split("/")
        if (name.startswith("/") or ".." in parts or ":" in parts[0]
                or any(p in ("", ".") for p in parts)):
            raise RuntimeError(f"the release zip names {info.filename!r}, "
                               "which is not inside the card")
        if not _owned(parts, core):
            raise RuntimeError(
                f"the release zip names {info.filename!r}, which is not a "
                f"path the {core.title} core owns")
        out.append(info)
    if not any(i.filename.replace("\\", "/") == f"Cores/{core.id}/core.json"
               for i in out):
        raise RuntimeError(f"that zip holds no Cores/{core.id}, so it is not "
                           f"the {core.title} core")
    return out


def _place(staged: str, root: str, core: Core) -> list[str]:
    """Move an unpacked release from the staging directory onto the card.

    The core's own directory is swapped whole, because a core is its .rbf_r
    and its json files together and half of each is not a core. Everything
    else, the platform name and its image, is replaced file by file: those
    directories are shared with every other core on the card.
    """
    written = []
    live = os.path.join(root, "Cores", core.id)
    new = os.path.join(staged, "Cores", core.id)
    old = live + ".old"

    for base, _dirs, files in os.walk(staged):
        rel = os.path.relpath(base, staged)
        if rel == "." or rel.split(os.sep)[:2] == ["Cores", core.id]:
            continue
        os.makedirs(os.path.join(root, rel), exist_ok=True)
        for f in files:
            dest = os.path.join(root, rel, f)
            # Platform entries belong to the core and are replaced. Everything
            # else out here is the user's: an asset directory holds their ROMs
            # and boot ROMs, so a file that is already there is left alone
            # rather than overwritten. A core needing to change one of those
            # is a thing to do deliberately, not a side effect of an update.
            if rel.split(os.sep)[0] != "Platforms" and os.path.exists(dest):
                continue
            os.replace(os.path.join(base, f), dest)
            written.append(os.path.join(rel, f))

    os.makedirs(os.path.dirname(live), exist_ok=True)
    if os.path.isdir(old):
        shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(live):
        os.replace(live, old)
    try:
        os.replace(new, live)
    except OSError:
        if os.path.isdir(old):
            os.replace(old, live)
        raise
    shutil.rmtree(old, ignore_errors=True)
    written.append(os.path.join("Cores", core.id))
    return written


def install(root: str, rels: dict[str, dict], cores=None, progress=None,
            cancelled=None, timeout: int = TIMEOUT) -> list[str]:
    """Put the release on the card. Returns what was written.

    Each core is downloaded whole, checked, unpacked into a staging directory
    on the card and only then moved into place, so an install that fails or is
    stopped leaves the core that was already working exactly as it was.

    Saves, ROMs, cheat files and boot ROMs are never touched: the release zips
    contain none of those paths, and _members() refuses an archive that names
    anything outside the card.
    """
    def say(done, total, msg):
        if progress:
            progress(done, total, msg)

    def stop() -> bool:
        return bool(cancelled and cancelled())

    todo = list(cores if cores is not None else outdated(installed(root), rels))
    if not todo:
        return []

    staging = os.path.join(root, STAGING)
    shutil.rmtree(staging, ignore_errors=True)

    written: list[str] = []
    try:
        for n, core in enumerate(todo, 1):
            url = asset_for(core, rels)
            if url is None:
                raise RuntimeError(f"the release has no zip for {core.id}")
            here = f"{core.title} core ({n} of {len(todo)})"
            say(0, 0, f"fetching the {here}")
            data = _fetch(url, lambda got, tot: say(got, tot,
                                                    f"fetching the {here}"),
                          stop, timeout)
            if stop():
                raise Cancelled()

            say(0, 0, f"checking the {here}")
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                members = _members(zf, core)
                need = sum(m.file_size for m in members)
                free = shutil.disk_usage(root).free
                if free < need * 2:
                    raise RuntimeError(
                        f"{need // 1024} KB to write and {free // 1024} KB "
                        "free on the card")
                dest = os.path.join(staging, core.id)
                shutil.rmtree(dest, ignore_errors=True)
                say(0, len(members), f"unpacking the {here}")
                for i, m in enumerate(members, 1):
                    if stop():
                        raise Cancelled()
                    zf.extract(m, dest)
                    if i % 4 == 0 or i == len(members):
                        say(i, len(members), f"unpacking the {here}")

            say(0, 0, f"installing the {here}")
            written += _place(dest, root, core)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return written
