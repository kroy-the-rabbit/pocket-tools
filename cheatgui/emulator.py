# SPDX-License-Identifier: GPL-3.0-or-later
"""Play an imported dump, with one of its saves, in mGBA.

This is the check a checksum cannot do. A ROM's own header and global
checksums prove the ROM read back intact; save RAM carries no checksum at all,
so the only way to know a save is good is to let the game read it and look at
what it says. If the file select shows your name and your hours, the read was
good.

**The emulator never sees an original.** mGBA writes to the .sav as it plays,
so handing it the library copy would let a test modify the thing it is
testing, and handing it the card would let it write to an SD card that may
still be the only place a dump exists. Both are copied into a scratch
directory first and the scratch directory is what gets mounted.

**The save is renamed to the ROM's stem.** mGBA looks for `<rom stem>.sav`
beside the ROM and nothing else, which is the same rule the Pocket follows for
Saves/cartdumps, so one naming rule serves both and neither needs the file to
have arrived under that name.

The runner is whichever mGBA this machine has, looked for in order: a host
binary, a flatpak, then the podman image the cartridge repo's play-dump.sh
builds. Nothing here builds an image. An image build is minutes of somebody
else's CPU and is not something a Play button should start.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import say

# The container the cartridge work already uses. Named rather than built: if
# it is absent the answer is a sentence, not a build.
IMAGE = "localhost/pocket-emu:1"
IN_IMAGE = "/usr/games/mgba"
FLATPAK_ID = "io.mgba.mGBA"

# Host binaries, in the order they are worth preferring. mgba-qt has the menus
# and the save-state browser; mgba is the SDL build.
HOST_BINARIES = ("mgba-qt", "mgba")


class NoEmulator(Exception):
    """No mGBA of any kind was found, with a sentence saying what to do."""


def _host() -> str | None:
    for name in HOST_BINARIES:
        found = shutil.which(name)
        if found:
            return found
    return None


def _flatpak() -> bool:
    if not shutil.which("flatpak"):
        return False
    try:
        out = subprocess.run(["flatpak", "info", FLATPAK_ID],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def _image() -> bool:
    if not shutil.which("podman"):
        return False
    try:
        out = subprocess.run(["podman", "image", "exists", IMAGE],
                             capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def available() -> str:
    """Which runner would be used: "host", "flatpak", "podman", or "".

    Cheap enough to ask before drawing a button, so a Play button that cannot
    play is never drawn in the first place.
    """
    if _host():
        return "host"
    if _flatpak():
        return "flatpak"
    if _image():
        return "podman"
    return ""


def why_not() -> str:
    """One sentence for somebody who has no mGBA."""
    return ("No mGBA was found. Install it from your distribution, or "
            f"`flatpak install {FLATPAK_ID}`, or build the container the "
            "cartridge repo uses with tools/podman/play-dump.sh.")


def _stage(rom: str, save: str | None) -> tuple[str, str]:
    """Copy the ROM and its save into a scratch directory. (dir, rom name)."""
    work = tempfile.mkdtemp(prefix="pocket-play-")
    base = os.path.basename(rom)
    shutil.copyfile(rom, os.path.join(work, base))
    if save:
        stem = os.path.splitext(base)[0]
        shutil.copyfile(save, os.path.join(work, stem + ".sav"))
    return work, base


def _cookie(work: str) -> str | None:
    """An X cookie the container can use, or None if there is no display.

    $XAUTHORITY holds an entry tied to this machine's hostname, which a
    container does not share, so the cookie does not match and the X server
    refuses the connection. Rewriting the address family to FamilyWild (ffff)
    makes it host-independent. This copy is ours: the real cookie is untouched
    and `xhost` is never called, so the desktop's access control is unchanged.
    """
    if not os.environ.get("DISPLAY") or not shutil.which("xauth"):
        return None
    path = os.path.join(work, "xauth")
    try:
        open(path, "wb").close()
        listing = subprocess.run(
            ["xauth", "nlist", os.environ["DISPLAY"]],
            capture_output=True, text=True, timeout=10)
        if listing.returncode != 0 or not listing.stdout:
            return None
        wild = "".join("ffff" + line[4:] + "\n"
                       for line in listing.stdout.splitlines() if line)
        merge = subprocess.run(["xauth", "-f", path, "nmerge", "-"],
                               input=wild, text=True, capture_output=True,
                               timeout=10)
        if merge.returncode != 0:
            return None
        os.chmod(path, 0o644)
    except (OSError, subprocess.SubprocessError):
        return None
    return path


def _podman_command(work: str, base: str) -> list[str]:
    """The container invocation, with the two things X needs to work.

    --security-opt label=disable: the X socket is labelled user_tmp_t and a
    container runs as container_t, which SELinux denies write on. The symptom
    without it is "unable to open display" and nothing else. This is
    per-container; no boolean is set and nothing system-wide changes.

    SDL_AUDIODRIVER=dummy: there is no sound device in the container and ALSA
    otherwise buries every real message under a screenful of its own.
    """
    return [
        "podman", "run", "--rm", "-i", "--security-opt", "label=disable",
        "-e", f"DISPLAY={os.environ.get('DISPLAY', '')}",
        "-e", "XAUTHORITY=/work/xauth",
        "-e", "XDG_RUNTIME_DIR=/tmp/xdg",
        "-e", "SDL_AUDIODRIVER=dummy",
        "-v", "/tmp/.X11-unix:/tmp/.X11-unix",
        "-v", f"{work}:/work",
        "--userns=keep-id",
        IMAGE, IN_IMAGE, f"/work/{base}",
    ]


def play(rom: str, save: str | None = None) -> subprocess.Popen:
    """Launch mGBA on a copy of `rom`, with a copy of `save` if given.

    Returns the process. The scratch directory is deliberately not cleaned up
    here: mGBA is still running out of it, and whatever it writes to the save
    while you play is the only record of the session. It lands under the
    system temporary directory and is named pocket-play-*.
    """
    runner = available()
    if not runner:
        raise NoEmulator(why_not())
    work, base = _stage(rom, save)
    target = os.path.join(work, base)
    if runner == "host":
        cmd = [_host(), target]
    elif runner == "flatpak":
        cmd = ["flatpak", "run", f"--filesystem={work}", FLATPAK_ID, target]
    else:
        if _cookie(work) is None and os.environ.get("DISPLAY"):
            say.err("could not make an X cookie; the container may not draw")
        cmd = _podman_command(work, base)
    say.out(f"playing {base} with {runner}")
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True)
