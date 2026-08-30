# Pocket tools

The desktop side of a set of Analogue Pocket cores. It reads your SD card, picks
cheats for the games on it, installs and updates the cores, and files the
cartridge dumps they produce.

This app is original work. It writes files that other people's cores read, and
it contains none of their code. What it is built around is the libretro cheat
database, which it fetches rather than ships, and a Game Genie decoder that
follows SameBoy's algorithm. See [Credits](#credits).

> **Use at your own risk. Cheats can corrupt save files.**
>
> A cheat is not a setting, it is a write into the memory of a running game. A
> code aimed at an address that means something else in your copy overwrites
> whatever is there, and games build their save data out of that same memory,
> so the damage gets written into your save at the next save point. This is
> most dangerous on a cartridge, where the save lives in the cartridge and
> nothing on the SD card is a backup of it.
>
> Back your saves up before using cheats on anything you care about, and read
> [Cartridges](#cartridges-read-this-part) before using them on one.

Three panes: the systems on the card, the games in each, and the cheats for the
selected game. Tick what you want and press **Send to Pocket**. The file next to
the ROM *is* the state, so what you see is what the handheld will do.

![The picker, with a cartridge selected and its cheats listed](docs/screenshot.png)

That is a cartridge rather than a ROM, which is why the status line is red: nine
of the ticked cheats are GameShark codes, and on a cartridge whose revision you
cannot check those are the dangerous kind. The **Applied** column says which is
which, and [Cartridges](#cartridges-read-this-part) explains why it matters.

## The set

This app is one of five projects that work together. It is the only one that
runs on a computer; the other four are cores that run on the handheld.

| | | |
|---|---|---|
| **pocket-tools** | this app | reads the card, picks cheats, installs cores, files dumps |
| [openfpga-GBC-cheats](https://github.com/kroy-the-rabbit/openfpga-GBC-cheats) | Game Boy, Game Boy Color | cheats on a ROM or on a real cartridge |
| [openfpga-GBA-cheats](https://github.com/kroy-the-rabbit/openfpga-GBA-cheats) | Game Boy Advance | cheats on a ROM, SD card only |
| [openfpga-pcengine-cheats](https://github.com/kroy-the-rabbit/openfpga-pcengine-cheats) | PC Engine, TurboGrafx-16 | cheats on a ROM, SD card only |
| [openfpga-carttools](https://github.com/kroy-the-rabbit/openfpga-carttools) | GB, GBC, GBA | dumps cartridges, does not play them. Optional: install it and the dump features appear |

The three cheat cores are forks that add a cheat engine to somebody else's core,
and each keeps its own install notes and its own credits. CartTools is a
subtraction from one. None of them is stock, and stock Pocket cores cannot read
a cheat file, so the card needs one of these installed or nothing this app
writes has any effect.

**Cores...** in the app will fetch and install any of them onto the card for
you. It asks each repository what its latest release is rather than being told
here, so a newly tagged core appears without this list having to be edited.

## What works

| | |
|---|---|
| Picking cheats for Game Boy, Game Boy Color, Game Boy Advance, PC Engine | **works** |
| Reading your ticks back off the card next time | **works** |
| Installing and updating the cores | **works** |
| Reporting which boot ROMs the card is missing | **works** |
| Fetching the libretro cheat database | **works** |
| Ejecting the card safely | **works** |
| Cheats for a physical cartridge you play | **works**, Game Boy and Game Boy Color only, and read the warning |
| Filing cartridge dumps by SHA-1 against a No-Intro DAT | **works**, and appears only with the CartTools core installed |
| Copying a filed dump back to the card | **works**, part of the same feature |
| Linux and Windows | **works**, used against a real Pocket card end to end |
| macOS | **works**, but not notarized: one command to get past Gatekeeper the first time |
| Fetching boot ROMs for you | never. They are copyrighted and that is your problem to solve |
| Shipping any of the cheat database | never. It is CC-BY-SA-4.0 and is fetched at run time |

## Versions

The five projects in this set share one version number, and this app takes its
from the cores. The set is at **0.9999**. The next release is 0.99991, then
0.99992, and so on: each one adds to the tail rather than climbing toward a
round number. Nothing here reaches 1.0, because 1.0 is a claim to be finished
and none of this is.

## Get the app

Download a build from the
[releases page](https://github.com/kroy-the-rabbit/pocket-tools/releases), or
run it from a checkout:

```sh
make cheatdb          # optional: the cheat database as a git submodule
make gui              # or: cheatgui/run.sh
make list ARGS=zelda  # same data, printed, no window
```

Full install notes, and how to check the signature on a download, are in
[docs/INSTALL.md](docs/INSTALL.md).

Python 3.10 or newer with tkinter, if you are running from source. Everything
used is in the standard library, so the venv `run.sh` makes stays empty; it
exists so nothing is ever installed into the host Python.

## Installing a core from here

The **Pocket core** line, above the database bar, says which core the card is
carrying and whether it is the released one:

```
Pocket core: 4 installed, all up to date
Pocket core: 4 installed, 1 update available
Pocket core: not installed. Nothing written here has any effect until it is.
Pocket core: no card
```

It counts rather than lists, so the line stays the same length however many
cores you have. Which core is at which version is in **Cores...**, where there
is room for a column.

**Cores...** opens a list of every core this app writes for, with the version on
the card beside the version available, and a tick box per core. What is behind
is ticked for you; everything else is yours to choose.

A row you cannot tick says why in the column that would otherwise be blank, and
the three reasons are different things: *no release yet* is a core whose
repository is real and has not been tagged, *not released yet* is one with no
repository at all, and *offline* is you being offline. Only the last one is
fixed by trying again.

Installing writes `Cores/<id>` and the platform entries that go with the cores
you picked, and nothing else. That is enforced twice rather than promised.

An archive is refused whole, not filtered, if it names anything the core it
claims to be does not own: a path outside the card, another core's directory,
another platform's assets, `Saves`, or any directory at the root that is not
`Cores`, `Platforms` or its own `Assets`. And of what remains, only its own
`Cores/<id>` and the shared `Platforms` entries are allowed to replace anything.
A file already sitting in an asset directory is left alone, so a ROM or a boot
ROM you put there cannot be overwritten by an install even if a release started
carrying one. Each core is downloaded whole, checked, and staged on the card
before any of it is moved into place, so an install that fails or is stopped
leaves the core you already had exactly as it was. **Eject** afterwards, before
you pull the card out.

A core already at the released version is offered but not ticked. One copied
half way reads as the right version and does not run, and putting it back is the
fix, so reinstalling stays available without being suggested.

## Boot ROMs

The cheat cores load a boot ROM before they start a game and will not run
without one. Those are copyrighted: they are not in the cores, they are not in
this app, and this app will not fetch them. What it will do is tell you which of
them your card is missing, on the second line of the core bar, with **Boot
ROMs...** for the whole list and where each one goes:

| File | Goes in | Size |
|---|---|---|
| `gbc_bios.bin` | `Assets/gbc/common/` | 2304 bytes |
| `gb_bios.bin` | `Assets/gb/common/` | 256 bytes |
| `sgb_boot.bin` | `Assets/gb/common/` | 256 bytes |
| `gba_bios.bin` | `Assets/gba/common/` | 16384 bytes |

The PC Engine core is not in that table because it needs no boot ROM at all, and
neither does CartTools. That is an answer rather than an oversight: there is
nothing for you to find, and the app never reports anything missing for them.

Dump them from your own hardware, or supply your own copies. A file of the wrong
size is reported separately from a missing one, because that is the failure that
looks like a working install and then refuses to start anything.

The list is the union of two things: what the installed core declares in its
own `data.json`, and a table in the app. Reading `data.json` means a core that
starts wanting a different file is reported correctly by a copy of the app that
predates it. The table is there because a core can be wrong about itself, and a card can be
carrying a build that is: a core that mismarks a boot ROM as optional cannot
drop it from the list this way.

## The cheat database

The app needs the libretro cheat database: the four directories for the systems
it writes for, about 3400 files. It has none on first run: press **Update** in
the bar along the bottom and it fetches one, roughly 16 MB and a minute. It
comes from
[libretro/libretro-database](https://github.com/libretro/libretro-database) and
is CC-BY-SA-4.0; none of it is shipped with this app.

That bar is also the version display. It says how many files you have and what
they are dated, and it checks upstream on startup, so you can see at a glance
whether there is anything newer:

```
cheat database: 2456 files, 2026-08-01  up to date
cheat database: 2456 files, 2026-03-14  update available: 2026-08-01
cheat database: not fetched yet, press Update
```

The comparison is against the newest upstream commit that touched those four
directories, not the repository head, which moves several times a week for
systems these cores cannot run. **Update** checks first and only downloads if
there is something to download. An update that fails or is stopped leaves the
database you already had exactly as it was.

Cheat files of your own go in `~/.local/share/pocket-cheats/cht/`, outside the
database, so an update cannot lose them. They are searched first.

## Ejecting

**Eject** flushes writes and unmounts the card. Writing already syncs, but a
sync is not an unmount, and a card pulled between the two can lose the write. If
something still has the card open the app says so and leaves it mounted; it does
not force anything.

---

# Cartridges: read this part

Cheats work on a real cartridge. Everything below is about what that costs you,
because the cartridge path is the one where this tool cannot check your work and
where getting it wrong has consequences a ROM does not.

**On a cartridge, identifying the right cheat file is your job, and nothing in
this tool or in the core can do it for you or tell you that you got it wrong.**

## Why the cartridge path is different

A cartridge is not a file on the card. The app never sees it, so:

* It **never appears in the game list.** You add it yourself: the **Cartridges**
  entry in the systems pane, **Add cartridge...**, then type the name and say
  which system it is for. Your cartridges are then listed under **Game Boy** and
  **Game Boy Color** headings, and **Move to...** refiles one that went in under
  the wrong heading.
* **The system is not cosmetic.** It decides which folder on the card the cheat
  file is written to, `Assets/<system>/common/Cartridges/`. Choose the wrong one
  and the core's **Load Cheats** browser will not be looking where the file is.
* **The name you type is the whole of the matching.** The picker matches cheat
  files by filename. Type `Zelda` and you will be offered files for every Zelda
  ever released on the system. Nothing reads the cartridge.
* **The Pocket will not load the file by name either.** In Play Cartridge mode
  APF does not load a slot named after slot 0, so `<name>.cht` is not picked up
  automatically. Use **Load Cheats** in the core menu to browse for it once; the
  slot remembers it for later launches. The files go in their own folder,
  `/Assets/<platform>/common/Cartridges/`, so that browser opens on your
  cartridges instead of a few hundred ROMs.

With a ROM on the card, none of this applies. The app reads the actual file,
matches it, tells you which file it picked, and you can check a Game Genie
compare byte against the ROM itself. A cartridge gives you none of that.

**Add cartridge...** offers Game Boy and Game Boy Color and nothing else. Game
Boy Advance and PC Engine are SD card only: cartridges are unsupported on both
until their cores support them.

## What goes wrong, and how

You cannot tell a cartridge's revision from the outside. The label does not say,
and two carts that look identical can hold different builds with different
memory layouts. A cheat published for one revision is aimed at an address that
means something else on another. The two kinds of code then fail in completely
different ways.

### Game Genie codes fail safely

A Game Genie code carries a **compare byte**, and the core only applies the
patch when the byte already at that address matches. On the wrong revision it
never matches, so the code loads, reads as enabled, and does nothing at all.
Disappointing, not dangerous.

### GameShark codes do not

A GameShark code is a **real write into work RAM**, made once a frame, with
nothing to check against. On the wrong revision that address holds some other
variable, and the core writes over it regardless. From there:

* **Crashes.** The byte you are overwriting may be a pointer, a counter the
  game's own logic depends on, or a state machine's state. Overwritten every
  frame, forever.
* **Corrupted saves.** This is the one worth being careful about. A game builds
  its save data out of the same work RAM the code is writing into. Corrupt the
  wrong byte and the game will happily write the result into your save at the
  next save point, and it is a real cartridge, so that is the only copy.

**There is no undo.** In Play Cartridge mode the save lives in the cartridge's
own battery-backed RAM. The core reads and writes it over the edge connector and
does not copy it to the SD card, so nothing on the card is a backup of it and a
savestate is not one either. If the save matters, dump the cartridge before you
put a GameShark code on it. This is not advice about this app, it is advice
about writing into the RAM of a game you cannot restore.

The app marks this where it can. The status line warns when a cartridge
selection contains written codes, and the **Applied** column says `written` or
`patched` for every cheat, so you can see which kind you are about to send
before you send it. It cannot tell you whether the address is right, because it
cannot see the cartridge.

## Doing it properly

1. **Name the cartridge exactly as the ROM is named**, including the region and
   revision tags: `Legend of Zelda, The - Link's Awakening DX (USA, Europe) (Rev 2)`.
   That name is what the matching has to work with, and a name without a
   revision tag matches a file for some other revision just as readily.
2. **Prefer Game Genie codes.** They fail silently instead of destructively, and
   for a cartridge whose revision you are guessing at, that is the whole
   argument.
3. **Check the compare bytes if you have a dump of that exact cartridge:**

   ```sh
   cheats/cht check "Zelda (USA) (Rev 2)" --rom /path/to/dump.gbc
   ```

   This is the only real verification available. It says which Game Genie codes
   match the ROM, and in which bank. A code that matches nothing will never
   fire; a code that matches is aimed where its author meant.
4. **Back the save up first**, if the game has one you care about.
5. **Check it took.** Tick **Show cheats** in the core menu. The Game Boy cores
   draw the names of the enabled cheats over the picture, headed with the counts
   and with `CARTRIDGE` or `ROM FILE`. An empty overlay means the file never
   loaded, which is a different problem from a wrong code.

If a game misbehaves, turn the cheats off before assuming the cartridge is at
fault: **Cheats enabled** in the core menu is a single global switch.

---

# Cartridge dumps, which are the opposite case

Everything above is about a cartridge you are *playing*. There is a second thing
also called a cartridge, and it is worth keeping the two apart because they are
opposites.

**A cartridge you play has no identity.** The Pocket is running it, no file on
the card represents it, you type its name yourself, and the app takes your word
for what it is. That is the whole reason the section above is a warning.

**A cartridge dump does have one.** The
[CartTools](https://github.com/kroy-the-rabbit/openfpga-carttools) core reads a
cartridge and writes a ROM image to the card. That is bytes, so it can be
hashed, identified, named correctly and matched to a cheat file with nothing
taken on trust.

## It follows the card

**Everything below appears when the CartTools core is on your card, and is gone
when it is not.** There is no setting to find: installing that core is the act
of asking for the feature, this app never installs it unasked, and a card
without it produces no dumps for any of this to read.

The core itself is always offered. **Cores...** lists it and reports its version
whether or not you have it, like every other core, because the place anybody
looks for a core is the core installer. What it never does is tick it for you: a
core you have not installed is not a core that has fallen behind.

With no dumper on the card, none of the surface below exists: no **Cartridge
dumps...** button, no **Cartridge dumps** category, no **Add DAT...**, no
scanning of the card for dumps and no reading of your library.

## What it is for

**Cartridge dumps...** on the card line opens that list. What it is for is the
names: the core reads a title out of a fixed header offset and sanitises it, so
Link's Awakening arrives as `ZELDA.gb`, Oracle of Seasons as
`ZELDA_DIN__AZ7E.gbc` with four bytes of manufacturer code stuck to it, and two
cartridges that title themselves the same overwrite each other. The core cannot
list a directory, so it cannot notice any of that. The app can, because it is
the only part that ever sees the bytes after they land.

It needs No-Intro's data to do it. **Add DAT...** lists the files it finds where
your browser puts downloads, says which system and flavour each one is, and has
a button that opens No-Intro's download page. Take the DAT or the Parent-Clone
DAT for each system; the DB Export is a different format and cannot be read.
Nothing is fetched for you and nothing is bundled.

Then it is a list you work in. Tick what you want and press the button that says
what happens to it:

| Button | What it does |
|---|---|
| **Add to library** | copies the dump to your library under its real name, and keeps the core's original beside it as evidence of what was actually produced |
| **Clear from card** | deletes the card's copy, and only after comparing it byte for byte against the one in your library |
| **Cheats...** | which libretro cheat file the dump maps to, every alternative, and a way to pin a different one |
| **Turn down** | pass it over. It doubles as **Offer again**, because a decision you can never take back is a trap rather than a decision |

Nothing is written for a row you did not tick, and identification is by SHA-1
against the DAT rather than by the filename or the extension. That matters more
than it looks: the core tells Game Boy from Game Boy Color using the CGB flag in
the header, and No-Intro's split between those two systems is an editorial
judgement rather than a header bit. Pokemon Yellow is colour-enhanced and lives
in the Game Boy set; Pokemon Gold is Game Boy compatible and lives in the Game
Boy Color one. The hash is the only thing that gets both right.

**Your library lives on your computer, not on the card**, and you choose where.
Keeping the card copy is never required, but nothing is deleted from it until
the replacement exists and has been compared byte for byte, and the comparison
is made again immediately before the delete, so a card swapped in between
refuses instead of losing a file.

## Getting a dump back onto the card

Filing a dump copies it **off** the card, which left it somewhere the rest of
the app could not see: the cheats it had just been matched to had nothing to be
attached to. **Cartridge dumps** in the systems pane is the way back. It is its
own category rather than a heading under **Cartridges**, because the two are
opposites and filing them together would say they were alike.

It lists everything in your library, grouped by system. **A dimmed row is one
that is not on the card**; a normal one is already there. Select a dimmed row
and press **Copy to card**, and the ROM is written to
`Assets/cartdumps/<system>/` under its real No-Intro name, which is what makes
the cheat matcher find the right file without being asked. **Send to Pocket**
then writes its cheats into the same folder, beside the ROM, like any other.

Your dumps go in one place of their own rather than loose among the hundreds of
ROMs already in `common/`, where the only thing marking them as yours would be
their date. It sits at the top of `Assets/` because the Pocket's file browser is
rooted there, and **Back** at the top of the list walks up to it, and it filters
by what the core can load, so all three cheat cores reach the same `cartdumps`
and each sees only its own systems inside.

The trade is that a copied-back dump is **not** listed under its own system in
this app, because that list is built by walking `Assets/<system>/`. It is listed
under **Cartridge dumps**, which is where you went looking for it.

Game Boy, Game Boy Color and Game Boy Advance all dump. This is a different
thing from the cartridge *play* support above, which is Game Boy and Game Boy
Color only.

---

# PC Engine / TurboGrafx-16 came last

The core is released and verified working, so a file written for a PC Engine
game takes effect like any other. Two things about this system still differ from
the rest:

* **The codes are read, not carried.** Every published PC Engine cheat is a RAM
  poke, and this app decodes all 397 files in the libretro directory, both of
  the two shapes they come in. What you tick is what gets written, in the same
  form the database already uses.
* **There is no code store meter here yet.** The core does fix a store size,
  32 codes, and stops committing beyond it, so a meter is buildable and simply
  is not built. Until it is, a selection over 32 codes is truncated by the core
  without this app saying so.

Three things are deliberately absent, and none of them is an oversight:

* **SuperGrafx.** `.sgx` files are not listed. The core drops SuperGrafx to buy
  the room its cheat engine needs, so those ROMs will not run correctly on it.
* **PC Engine CD.** Not supported by the core this one forks, and not planned.
* **Cartridges.** Unsupported until the core supports them. SD card only.

[docs/CHEATGUI.md](docs/CHEATGUI.md) has the detail, including the two code
shapes and why running either through the Game Boy parser produces confident
nonsense.

---

# Game Boy Advance writes two files

The core is released and verified working. **Game Boy Advance is SD card only**:
cheats go beside a ROM on the card, and cartridges are unsupported until the
core supports them.

It is also the one system where **the file you pick from is not the file the
handheld reads**, and that is worth understanding before you look in the folder
and think something went wrong.

* **Two files land beside the ROM.** `Game.gba.cht`, which holds the cheats you
  ticked and their descriptions, and `Game.gba.chtbin`, which is those same
  cheats packed into the 128-bit entries the core loads. The `.cht` is the one
  this app reads back, so it is what makes your ticks come back next time. The
  `.chtbin` is the one the hardware reads. Deleting either by hand will confuse
  one of the two; use the app, or delete both.
* **Why not just the `.cht`.** The core cannot parse text. Its cheat engine went
  into a design that was already at 90 % logic utilisation, and an ASCII parser
  on the FPGA cost more setup timing than the design had left, so the parse
  moved here. That is also why the conversion is worth trusting: it is tested on
  a desktop against the libretro directory rather than inferred from a handheld
  with no console.
* **Codes are read, not carried.** CodeBreaker and GameShark v1/v2, decoded.
  What cannot be decoded is dropped rather than guessed at, and the row stays in
  the list, greyed, with its description.
* **Encrypted codes do not work, and cannot be made to.** GameShark v3, Action
  Replay v3 and CodeBreaker codes after a `9` line are enciphered with a
  per-game seed, and they are shaped exactly like ordinary ones. They are
  rejected by plausibility: a real code's address lands in the machine's RAM, an
  enciphered word almost never does. A few real codes will be refused this way
  and a few enciphered ones will slip through as pokes at nothing.
* **The store holds 32 entries**, and a conditional code spends two of them
  because the engine expresses "if" as a compare entry followed by the entry it
  guards. The meter counts entries for this system, which is why a cheat can
  cost more than one.

## What it shows

Each cheat says how the core applies it, because the two ways do not behave the
same. A GameShark code is written into RAM once a frame; a Game Genie code
overrides the CPU's read, which is what a ROM patch needs. The cores' own
`docs/CHEATS.md` has the detail.

That distinction is what the cartridge section above turns on, and it is worth
knowing for ROMs too: a written cheat puts the value where the game finds it by
any route, so the game's own logic still sees it and can clamp it.

---

## Why this is not in a core repository

Each core lives in its own repository, and some of them may be PR'd upstream. A
desktop app that reads SD cards has no business in that diff. This app and the
Game Boy core share a cheat file parser; see [cheats/README.md](cheats/README.md)
for how that copy is kept honest.

## Documentation

| | |
|---|---|
| [docs/CHEATGUI.md](docs/CHEATGUI.md) | the full guide, including the PC Engine code shapes |
| [docs/INSTALL.md](docs/INSTALL.md) | install notes and how to check a download's signature |
| [docs/RELEASING.md](docs/RELEASING.md) | releasing, signing, and the state of macOS notarization |
| [cheats/README.md](cheats/README.md) | the parser shared with the Game Boy core, and how it is kept in step |

## Development

```sh
make test          # parser self-test and the GUI tests
make dist          # build the binary for this platform
make sync-check    # is the shared parser still in step with the core?
```

## Credits

This app writes cheat files for other people's cores. It contains none of their
code, but it exists because of them.

| | |
|---|---|
| [budude2/openfpga-GBC](https://github.com/budude2/openfpga-GBC) | the Pocket Game Boy and Game Boy Color core this was written for |
| [Gameboy_MiSTer](https://github.com/MiSTer-devel/Gameboy_MiSTer) | which that is a port of, carrying Till Harbaum's 2015 copyright and later contributors' |
| [agg23/openfpga-pcengine](https://github.com/agg23/openfpga-pcengine) | the Pocket PC Engine core the cheat fork starts from, GPL-2.0 |
| [vanfanel/openfpga-pcengine](https://github.com/vanfanel/openfpga-pcengine) | fixes merged into that port before the cheat fork was taken |
| [TurboGrafx16_MiSTer](https://github.com/MiSTer-devel/TurboGrafx16_MiSTer) | which that is a port of, by srg320 and greyrogue |
| [Torlus/FPGAPCE](https://github.com/Torlus/FPGAPCE) | Gregory Estrade's original, released into the public domain |
| [mincer-ray/openfpga-GBA](https://github.com/mincer-ray/openfpga-GBA) | the Pocket Game Boy Advance core, GPL-2.0 |
| [GBA_MiSTer](https://github.com/MiSTer-devel/GBA_MiSTer) | which that is a port of, GPL-2.0 |
| [Rai/openfpga-GBA](https://github.com/Rai/openfpga-GBA) | the cartridge-support branch CartTools was cut down from |
| [SameBoy](https://github.com/LIJI32/SameBoy) | `Core/cheats.c`, the reference the Game Genie decoder follows. Expat (MIT) license, copyright Lior Halphon |
| [libretro/libretro-database](https://github.com/libretro/libretro-database) | the cheat files themselves |
| [No-Intro](https://no-intro.org/) | the reference data a cartridge dump is identified against |
| [Analogue openFPGA](https://www.analogue.co/developer) | the Pocket framework the cores are built on |

## Trademarks

Not affiliated with, authorised by or endorsed by Analogue, Inc. "Analogue",
"Pocket" and "openFPGA" are theirs. They are used here only to say which
hardware this software is for, which is the one thing a name like that can
honestly do.

## License

This app is GPL-3.0-or-later. The full text is in [LICENSE](LICENSE), and every
source file carries an SPDX header saying the same.

`cheats/chtparse.py` and `cheats/ggdecode.py` are copies, kept byte identical,
of the reference parser from
[openfpga-GBC-cheats](https://github.com/kroy-the-rabbit/openfpga-GBC-cheats),
which is GPL-3.0-or-later as part of that core; this app is built around them.
The Game Genie decoding in `ggdecode.py` follows the algorithm in SameBoy's
`Core/cheats.c` rather than copying its code. SameBoy is under the Expat (MIT)
license, copyright Lior Halphon.

**The cheat database is not distributed here.**
[libretro/libretro-database](https://github.com/libretro/libretro-database) is
licensed **CC-BY-SA-4.0**, and no part of it is in this repository or in any
release of this app: it is fetched from upstream at run time, into your own data
directory. A cheat file this app writes to your card is a selection taken from
those files, so if you pass one on, CC-BY-SA-4.0 is the license it came under
and attribution and share-alike are what it asks for.

The Pocket cores are separate works under their own terms: per-file notices for
the Game Boy one, GPL-2.0 for the PC Engine, Game Boy Advance and CartTools
ones, over an original that its author put in the public domain. Nothing from
any of them is included here.
