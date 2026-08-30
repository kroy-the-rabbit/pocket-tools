# SPDX-License-Identifier: GPL-3.0-or-later
"""The libretro cheat database: where it is, how old it is, and fetching it.

The app is useless without it, and a packaged build has no checkout to fall
back on, so it fetches its own copy into the user's data directory. That copy
is what a released binary uses; a checkout of this repo can still use the git
submodule, and POCKET_CHEAT_DB overrides both.

Only the directories in DIRS are fetched, about 3400 files and 16 MB.
The whole repository is a 177 MB tarball and 830 MB checked out, nearly all of
it systems this core cannot run, so the files are taken one at a time from the
CDN at a pinned commit rather than cloning anything. That also means no git on
the machine and no submodule on Windows.

Everything here is standard library and none of it touches Tk. The GUI runs
fetch() on a thread and reports progress through the callback.
"""
from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import card

REPO = "libretro/libretro-database"
API = f"https://api.github.com/repos/{REPO}"
RAW = f"https://raw.githubusercontent.com/{REPO}"

# The libretro directory names, which are also the names on disk. Taken from
# card rather than repeated, because a directory fetched that nothing reads is
# wasted download and one read that nothing fetches is a system that is
# permanently empty. Turning a system off in card.ENABLED turns it off here.
# SUPPORTED rather than ENABLED, because the two are no longer the same tuple:
# the cartridge dumper can be switched on as a platform without being a system
# libretro has cheat files for, and asking ENABLED for its directory would be a
# KeyError the first time somebody enabled it.
DIRS = tuple(card.SUPPORTED.values())

# Enough to keep the link busy without looking like a scrape. The files average
# 5 KB, so this is latency bound rather than bandwidth bound.
WORKERS = 16
TIMEOUT = 30
UA = {"User-Agent": "pocket-cheats"}


class Cancelled(Exception):
    """Raised out of fetch() when the caller asked it to stop."""


_ctx = None


def ssl_context() -> ssl.SSLContext:
    """A context that can actually verify GitHub from a packaged build.

    PyInstaller bundles the build machine's OpenSSL, and OpenSSL looks for the
    trust store at a path compiled into it. A binary built on Ubuntu therefore
    goes looking in Ubuntu's location on every machine that runs it, finds
    nothing on a Fedora or a Windows one, and every fetch dies with
    CERTIFICATE_VERIFY_FAILED. It worked from a checkout because that used the
    host's own Python, which is exactly why it was not caught.

    So a released build carries its own CA bundle, which is also the only way
    to have one on Windows and macOS. From a checkout, where certifi is not
    installed and not wanted, the system store is right and is used.
    """
    global _ctx
    if _ctx is None:
        try:
            import certifi
            _ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:                                    # noqa: BLE001
            _ctx = ssl.create_default_context()
    return _ctx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBMODULE = os.path.join(ROOT, "external", "libretro-database", "cht")


def store() -> str:
    """The app's own copy, the one a packaged build fetches and updates."""
    return os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "pocket-cheats", "libretro")


def store_cht() -> str:
    return os.path.join(store(), "cht")


def state_file() -> str:
    return os.path.join(store(), "state.json")


def _populated(cht: str) -> bool:
    """A directory only counts if it actually holds cheat files.

    An empty submodule directory exists as soon as the submodule is
    registered, so its presence says nothing.
    """
    if not os.path.isdir(cht):
        return False
    for d in DIRS:
        full = os.path.join(cht, d)
        if os.path.isdir(full) and any(f.endswith(".cht")
                                       for f in os.listdir(full)):
            return True
    return False


def db_dir() -> str:
    """The cht directory in use, whether or not anything is in it.

    Explicit override first, then the copy this app fetched, then the git
    submodule for people working in a checkout. The fetched copy wins over the
    submodule because it is the one the Update button maintains.
    """
    forced = os.environ.get("POCKET_CHEAT_DB")
    if forced:
        return forced
    if _populated(store_cht()):
        return store_cht()
    if _populated(SUBMODULE):
        return SUBMODULE
    return store_cht()


def available() -> bool:
    return _populated(db_dir())


def missing_dirs(cht: str | None = None) -> list[str]:
    """Directories this database should have and does not.

    A copy fetched before a system was added holds nothing for it, and its
    recorded commit says nothing about that. Without this check such a copy
    can report itself current on the strength of a sha alone, and the system
    that was added stays permanently empty.
    """
    cht = cht or db_dir()
    return [d for d in DIRS
            if not os.path.isdir(os.path.join(cht, d))
            or not any(f.endswith(".cht") for f in os.listdir(os.path.join(cht, d)))]


def count_files(cht: str | None = None) -> int:
    cht = cht or db_dir()
    n = 0
    for d in DIRS:
        full = os.path.join(cht, d)
        if os.path.isdir(full):
            n += sum(1 for f in os.listdir(full) if f.endswith(".cht"))
    return n


# ------------------------------------------------------------------ versions --
def _get_json(url: str, timeout: int = TIMEOUT):
    req = urllib.request.Request(
        url, headers={**UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as f:
        return json.load(f)


def local_state() -> dict | None:
    """What we have: commit, its date, when it was fetched, how many files.

    Returns None when there is no database at all. A submodule checkout has no
    state file, so its commit is read from git; if that fails the files are
    still usable and only the version is unknown.
    """
    cht = db_dir()
    if not _populated(cht):
        return None

    absent = missing_dirs(cht)
    if os.path.abspath(cht) == os.path.abspath(store_cht()):
        try:
            with open(state_file()) as f:
                st = json.load(f)
            st["files"] = count_files(cht)
            st["source"] = "fetched"
            st["comparable"] = bool(st.get("sha"))
            st["missing"] = absent
            return st
        except Exception:                                    # noqa: BLE001
            return {"sha": None, "date": None, "fetched": None,
                    "files": count_files(cht), "source": "fetched",
                    "comparable": False, "missing": absent}

    # A submodule checkout. Its HEAD is the whole repository's head, which
    # moves for systems this core cannot run, so it is not comparable with
    # remote_state(). Ask git for the newest commit that touched these two
    # directories instead, which is the same question remote_state() asks.
    # A shallow clone may hold no such commit, and then the version is simply
    # unknown: say so rather than inventing a comparison.
    st = {"sha": None, "date": None, "fetched": None,
          "files": count_files(cht), "source": "submodule", "comparable": False,
          "missing": absent}
    repo = os.path.dirname(os.path.abspath(cht))

    def git(*args) -> str | None:
        try:
            return subprocess.run(["git", "-C", repo, *args], capture_output=True,
                                  text=True, timeout=10, check=True).stdout.strip()
        except Exception:                                    # noqa: BLE001
            return None

    # init-db.sh clones at depth 1, and in a shallow clone every path looks as
    # though HEAD introduced it: `log -- <dir>` returns HEAD whatever it really
    # touched. The answer is then the repository head, which is not the
    # question remote_state() asks, so nothing is comparable.
    shallow = git("rev-parse", "--is-shallow-repository") == "true"
    paths = [] if shallow else ["--", *(f"cht/{d}" for d in DIRS)]
    out = (git("log", "-1", "--format=%H%n%cI", *paths) or "").split()
    if len(out) == 2:
        st["sha"], st["date"] = out[0], out[1]
        st["comparable"] = not shallow
    return st


def remote_state(timeout: int = TIMEOUT) -> dict:
    """Upstream's newest commit that touched any of the Game Boy directories.

    Not the repository head. That moves several times a week for systems the
    Pocket has no core for, and comparing against it would report an update
    every time somebody edited a PlayStation cheat file.
    """
    best = None
    for d in DIRS:
        url = (f"{API}/commits?per_page=1&path="
               + urllib.parse.quote(f"cht/{d}", safe=""))
        commits = _get_json(url, timeout)
        if not commits:
            continue
        c = commits[0]
        date = c["commit"]["committer"]["date"]
        if best is None or date > best["date"]:
            best = {"sha": c["sha"], "date": date}
    if best is None:
        raise RuntimeError("upstream reported no commits for either directory")
    return best


def up_to_date(local: dict | None, remote: dict) -> bool:
    """True only when the two are describing the same thing.

    A version we cannot compare is never reported as up to date, and never as
    out of date either: describe() says it is unknown.
    """
    if local and local.get("missing"):
        return False          # a system it holds nothing for is not current
    return bool(local and local.get("comparable") and local.get("sha")
                and local["sha"] == remote.get("sha"))


def comparable(local: dict | None) -> bool:
    return bool(local and local.get("comparable") and local.get("sha"))


def describe(local: dict | None, remote: dict | None = None) -> str:
    """One line for the status bar."""
    if local is None:
        return "cheat database: not fetched yet, press Update"
    files = local.get("files", 0)
    when = _day(local.get("date")) or "unknown version"
    src = "" if local.get("source") == "fetched" else ", submodule"
    line = f"cheat database: {files} files, {when}{src}"
    absent = local.get("missing") or []
    if absent:
        # Named, because "incomplete" on its own does not say what is missing
        # or hint that pressing Update is what fixes it.
        short = ", ".join(short_name(d) for d in absent)
        return line + f"  nothing for {short}: press Update"
    if remote is None:
        return line
    latest = _day(remote.get("date")) or "unknown"
    if not comparable(local):
        return line + f"  (upstream: {latest}; Update fetches this app's copy)"
    if up_to_date(local, remote):
        return line + "  up to date"
    return line + f"  update available: {latest}"


def short_name(directory: str) -> str:
    """A libretro directory without its manufacturer, for a one line report.

    Every one of them is "<maker> - <system>", so the maker is dropped rather
    than any particular maker being special-cased. This used to strip the
    literal "Nintendo - ", which left "NEC - PC Engine - TurboGrafx 16" whole
    and made the line run off the end of the bar.
    """
    _maker, sep, rest = directory.partition(" - ")
    return rest if sep and rest else directory


def day(iso: str | None) -> str | None:
    """The date part of an ISO timestamp. A commit's time of day is noise."""
    return iso.split("T")[0] if iso else None


_day = day        # the internal name, kept so describe() reads the same


# ------------------------------------------------------------------- fetching --
def _tree(sha: str, timeout: int) -> dict[str, str]:
    """Blob paths to their names, for both directories, at one commit."""
    root = _get_json(f"{API}/git/trees/{sha}", timeout)
    cht = next((e for e in root["tree"] if e["path"] == "cht"), None)
    if cht is None:
        raise RuntimeError("no cht directory at that commit")
    level = _get_json(f"{API}/git/trees/{cht['sha']}", timeout)
    out: dict[str, list[str]] = {}
    for d in DIRS:
        node = next((e for e in level["tree"] if e["path"] == d), None)
        if node is None:
            raise RuntimeError(f"no {d!r} directory at that commit")
        listing = _get_json(f"{API}/git/trees/{node['sha']}", timeout)
        if listing.get("truncated"):
            raise RuntimeError(f"{d}: listing truncated by the API")
        out[d] = [e["path"] for e in listing["tree"]
                  if e["type"] == "blob" and e["path"].endswith(".cht")]
    return out


def _download(sha: str, d: str, name: str, dest: str, timeout: int) -> None:
    url = (f"{RAW}/{sha}/cht/" + urllib.parse.quote(d) + "/"
           + urllib.parse.quote(name))
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout,
                                context=ssl_context()) as f:
        data = f.read()
    with open(dest, "wb") as f:
        f.write(data)


def fetch(progress=None, cancelled=None, timeout: int = TIMEOUT) -> dict:
    """Replace the local copy with upstream's. Returns the new local state.

    Downloads into a temporary directory and swaps it in only once every file
    has arrived, so a fetch interrupted halfway leaves the database that was
    already working exactly as it was.

    progress(done, total, message) is called from the worker thread.
    cancelled() is polled between files; return True to abort.
    """
    def say(done, total, msg):
        if progress:
            progress(done, total, msg)

    def stop() -> bool:
        return bool(cancelled and cancelled())

    say(0, 0, "asking upstream what is current")
    remote = remote_state(timeout)
    sha = remote["sha"]

    say(0, 0, "listing the cheat files")
    tree = _tree(sha, timeout)
    total = sum(len(v) for v in tree.values())
    if not total:
        raise RuntimeError("upstream listed no cheat files")

    # Alongside the live copy so the swap at the end is a rename, not a copy
    # across filesystems.
    tmp = tempfile.mkdtemp(prefix="fetch-", dir=_ensure(store()))
    try:
        jobs = []
        for d, names in tree.items():
            os.makedirs(os.path.join(tmp, d), exist_ok=True)
            jobs += [(d, n) for n in names]

        done = 0
        say(done, total, f"fetching {total} cheat files")
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futures = [ex.submit(_download, sha, d, n,
                                 os.path.join(tmp, d, n), timeout)
                       for d, n in jobs]
            try:
                for fut in as_completed(futures):
                    fut.result()          # re-raises whatever the download hit
                    done += 1
                    if done % 25 == 0 or done == total:
                        say(done, total, f"fetching {total} cheat files")
                    if stop():
                        raise Cancelled()
            except BaseException:
                for f2 in futures:
                    f2.cancel()
                raise

        got = sum(len(os.listdir(os.path.join(tmp, d))) for d in tree)
        if got != total:
            raise RuntimeError(f"fetched {got} files, expected {total}")

        say(total, total, "installing")
        _swap(tmp, store_cht())
        tmp = None
    finally:
        if tmp and os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)

    st = {"sha": sha, "date": remote["date"],
          "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
          "files": total, "source": "fetched", "comparable": True}
    _ensure(store())
    tmpf = state_file() + ".tmp"
    with open(tmpf, "w") as f:
        json.dump(st, f, indent=2)
    os.replace(tmpf, state_file())
    return st


def _ensure(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def _swap(new: str, dest: str) -> None:
    """Put `new` where `dest` is, keeping the old one until the new one lands."""
    _ensure(os.path.dirname(dest))
    old = dest + ".old"
    if os.path.isdir(old):
        shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(dest):
        os.replace(dest, old)
    try:
        os.replace(new, dest)
    except OSError:
        if os.path.isdir(old):
            os.replace(old, dest)
        raise
    shutil.rmtree(old, ignore_errors=True)
