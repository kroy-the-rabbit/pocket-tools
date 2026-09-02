# SPDX-License-Identifier: GPL-3.0-or-later
"""Remembered choices, kept off the SD card so it stays clean."""
from __future__ import annotations

import json
import os

# The directory keeps the old name. Renaming it would orphan everybody's
# remembered choices, and a migration is a lot of risk for a tidier path.
CONFIG = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "pocket-cheats", "prefs.json")


def _load() -> dict:
    try:
        return json.load(open(CONFIG))
    except Exception:                                        # noqa: BLE001
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, CONFIG)


def get_library() -> str | None:
    """Where cartridge dumps are filed, or None until one has been chosen.

    There is no default. The cheat database picks its own directory because it
    is a cache nobody opens; the library is full of files someone will go
    looking for, so the app waits to be told rather than inventing a path and
    hoping it is the one they wanted.
    """
    return _load().get("library")


def set_library(path: str | None) -> None:
    """Remember a library. None forgets it; nothing on disk is touched.

    Stored absolute and with ~ already expanded, because this outlives the
    session that set it: a relative path remembered here would mean a different
    directory the next time the app is started from somewhere else.
    """
    data = _load()
    if path is None:
        data.pop("library", None)
    else:
        data["library"] = os.path.abspath(os.path.expanduser(path))
    _save(data)


def get_rejected(sha1: str) -> str | None:
    """The day a cartridge dump was ignored, or None if it never was.

    Keyed on SHA-1, like everything else about a dump, because a filename is
    exactly what collides: two cartridges that both title themselves ZELDA
    produce the same one, and rejecting the first would silently reject the
    second.

    This is here rather than in the library's index because it is a *decision*.
    The index is a cache that a rebuild reproduces by re-hashing files and
    asking the DAT again, and no amount of that recovers the fact that somebody
    said no. Anything that cannot survive deleting index.json belongs in this
    file with the app's other remembered choices.
    """
    return _load().get("rejected", {}).get(sha1)


def set_rejected(sha1: str, when: str | None) -> None:
    """Remember, or with None forget, that a dump is ignored.

    Forgetting is what re-offering a dump means, and it is a separate call
    because it has to be asked for: the point of the rejection is that the next
    run does not ask again.
    """
    data = _load()
    rejected = data.setdefault("rejected", {})
    if when is None:
        rejected.pop(sha1, None)
    else:
        rejected[sha1] = when
    _save(data)


def get_dats() -> list:
    """The No-Intro DATs to load at startup, in the order they were added.

    Remembered because loading them was a per-session chore: the catalog is
    built in memory and nothing wrote down which files went into it, so every
    run began with nothing identifiable and every dump came back UNIDENTIFIED
    until somebody went and found the three files again.
    """
    got = _load().get("dats", [])
    return [p for p in got if isinstance(p, str)]


def set_dats(paths: list) -> None:
    """Remember the DATs, in order, with duplicates dropped."""
    data = _load()
    seen, out = set(), []
    for p in paths:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    data["dats"] = out
    _save(data)


def all_sources() -> dict:
    """Every pinned cheat source, keyed as they are stored."""
    return dict(_load().get("sources", {}))


def set_source_key(key: str, cht_path: str) -> None:
    """Repoint one pin, by the key it is already stored under.

    Separate from set_source because that one derives the key from a ROM path,
    and repointing an existing pin must not risk deriving a different key and
    leaving the old one behind.
    """
    data = _load()
    data.setdefault("sources", {})[key] = cht_path
    _save(data)


def get_source(rom_path: str) -> str | None:
    """The cheat file the user pinned for this ROM, if any."""
    return _load().get("sources", {}).get(os.path.basename(rom_path))


def set_source(rom_path: str, cht_path: str | None) -> None:
    data = _load()
    sources = data.setdefault("sources", {})
    key = os.path.basename(rom_path)
    if cht_path is None:
        sources.pop(key, None)
    else:
        sources[key] = cht_path
    _save(data)


def get_save_name(sha1: str) -> str | None:
    """What the user called one save read, or None if they never said.

    Keyed on the save's own SHA-1, not on its filename and not on the
    cartridge, because the file is dated and immutable and the name is the one
    thing about it a person chose. Two reads of one cartridge are different
    bytes and get different keys; the same bytes arriving twice are the same
    read and keep the name already given.

    A decision, so it lives here rather than in the library's index. A rebuild
    re-hashes files and asks the DAT again; nothing it can do recovers the
    fact that this one is the save from before the boss.
    """
    return _load().get("save_names", {}).get(sha1)


def set_save_name(sha1: str, text: str | None) -> None:
    """Name a save read, or with None or an empty string forget the name."""
    data = _load()
    names = data.setdefault("save_names", {})
    text = (text or "").strip()
    if not text:
        names.pop(sha1, None)
    else:
        names[sha1] = text
    _save(data)
