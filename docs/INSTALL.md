# Installing the picker

> **How much each build has actually been exercised**, so you can judge what
> you are downloading:
>
> * **Linux** is the one that gets used day to day. Built, run, and used
>   against a real Pocket card.
> * **Windows** has been run on Windows: it reads a card, writes cheats,
>   installs the core and ejects. It is also smoke tested under Wine on every
>   check, which is what catches a build that will not start at all.
> * **macOS** has been run on macOS and works. It is not notarized, so
>   Gatekeeper refuses it on first launch until you run the one command below.
>
> Reports from any of the three are still welcome; a card is not a card.

One file per platform, on the
[releases page](https://github.com/kroy-the-rabbit/pocket-tools/releases).
Nothing is installed and nothing is written outside your home directory: the
app keeps its settings in `~/.config/pocket-cheats/` and its copy of the cheat
database in `~/.local/share/pocket-cheats/`.

**Cheats can corrupt save files.** A code aimed at the wrong revision of a game
overwrites memory the game saves from. Back up saves you care about first, and
read the cartridge section of the README before using cheats on a cartridge,
where the save is on the cartridge and nothing on the card is a copy of it.

On first run there is no cheat database. Press **Update** in the bar along the
bottom and it fetches one, about 14 MB and a minute. That bar is also where you
see which version you have and whether upstream has a newer one.

Above it is the **Pocket core** bar, which is the one that decides whether any
of this has an effect at all. A stock Pocket core ignores cheat files, so with
the card in, press **Cores...**, which lists every core this app writes for
with what your card has beside what is available. Anything out of date is
ticked already; press Install. The second line of that bar names any boot ROM
the core needs and your card does not have, and **Boot ROMs...** beside it
opens the whole list, with the size each file has to be and the folder it goes
in. Those are copyrighted and you have to supply them yourself; the app never
fetches one. The [boot ROM table in the README](../README.md#boot-roms) names
every file, so you can go and find them before the card is ever plugged in.
**Eject** when you are done.

## Verify what you downloaded first

The releases are signed. Checking that takes ten seconds and is the only thing
that distinguishes a build from this repository from a file that merely has the
same name.

```sh
# once: the public key, from this repository
gpg --import KEYS

# check the fingerprint against a source that is not this repository
gpg --fingerprint "Pocket Cheats Release Signing"
# C72E 94F3 D71E AD3D 41C9  A520 D6A3 B4CE 5A76 405D

# the signature covers the whole manifest
gpg --verify SHA256SUMS.asc SHA256SUMS

# and the manifest covers the files
sha256sum -c SHA256SUMS          # macOS: shasum -a 256 -c SHA256SUMS
```

`sha256sum -c` complains about files you did not download. That is expected;
what matters is that the line for the file you did download says `OK`.

## Linux

```sh
chmod +x pocket-cheats-*-linux-x86_64
./pocket-cheats-*-linux-x86_64
```

Built on Ubuntu 22.04, so it needs glibc 2.35 or newer. On anything older, or
on a non-x86_64 machine, run from a checkout instead: see the README.

The **Eject** button uses `udisksctl` and falls back to `umount`. `udisksctl`
comes with the desktop and needs no privilege for removable media; without it,
a card mounted by hand may need unmounting by hand.

## Windows

Run on Windows and confirmed working against a real card, through reading it,
writing cheats, installing the core and ejecting. Every build is also smoke
tested under Wine, which is what catches one that will not start at all;
`packaging/wine-smoke.sh` is what does that, and `make wine-test` runs it
against a build.

Run the `.exe`. There is no installer.

SmartScreen will say the publisher is unknown, because these builds carry no
Authenticode certificate. "More info" then "Run anyway". Verify the GPG
signature above if you want an actual assurance about where the file came from,
which is more than a code signing certificate would tell you anyway.

If it will not start, or you want to know which cheat database it is using,
run `pocket-cheats.exe --check-db` from cmd: the exe has no console of its own
but attaches to the one that launched it. Double clicked, it shows the same
report in a box. Either way it is also written to
`%USERPROFILE%\.local\share\pocket-cheats\libretro\pocket-cheats.log`,
along with the stack of any hard crash.

**Eject** uses the same shell command Explorer's own eject does, which is the
one part the Wine smoke test cannot stand in for. It has been run on Windows
and unmounts the card. If it fails it says so and leaves the card mounted.

## macOS

**These builds are not notarized.** They carry no Apple Developer ID, so
Gatekeeper refuses them on first launch with "Apple could not verify ... is
free of malware". That message is about the absence of a signature, not about
anything found in the file.

Unzip, then either:

```sh
xattr -dr com.apple.quarantine "Pocket Cheats.app"
open "Pocket Cheats.app"
```

or right-click the app, choose **Open**, and confirm at the prompt. Both do the
same thing: they clear the quarantine flag the browser set. After that it opens
normally.

Verify the GPG signature before you do that. Clearing quarantine on a file you
have not checked is exactly the thing Gatekeeper is trying to stop.

**Apple Silicon only.** There is no Intel build: GitHub retired the Intel
runners it would be built on. An Intel Mac runs it from a checkout instead, at
the bottom of this page.

macOS asks for permission to read removable volumes the first time the app
looks at an SD card. Refusing it leaves the card invisible to the app.

## Running from a checkout

Useful on a platform with no build, on an older distribution, or to change
something.

```sh
git clone https://github.com/kroy-the-rabbit/pocket-tools
cd pocket-tools
make gui
```

Python 3.10 or newer with tkinter. On Fedora `sudo dnf install python3-tkinter`,
on Debian and Ubuntu `sudo apt install python3-tk`. Everything else the app uses
is in the standard library, and `run.sh` makes an empty venv so nothing is ever
installed into your system Python.

A checkout can also take the cheat database as a git submodule instead of
fetching it, which is what `make cheatdb` does. The app uses whichever it
finds, its own copy first; `POCKET_CHEAT_DB=/path/to/cht` overrides both.
