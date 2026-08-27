# SPDX-License-Identifier: GPL-3.0-or-later
"""Remembered choices, kept off the SD card so it stays clean."""
from __future__ import annotations

import json
import os

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
    """The day a cartridge dump was turned down, or None if it never was.

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
    """Remember, or with None forget, that a dump was turned down.

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
