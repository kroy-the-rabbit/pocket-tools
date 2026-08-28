# Cheat picker

A small desktop app for choosing which cheats go on the Pocket card.

```sh
make gui                              # or: cheatgui/run.sh
cheatgui/run.sh --list          # same data, printed, no window
cheatgui/run.sh --list zelda -v # filtered, and every cheat listed
```

Set `POCKET_CARD=/path/to/card` to point the tool at an explicit directory
instead of searching the mounted volumes. What gets searched depends on the
platform, because the answer lives somewhere different on each: `findmnt` on
Linux, `/Volumes` on macOS, drive letters on Windows. What makes a card a card
is the same everywhere, a directory holding both `Cores/` and `Platforms/`.

`run.sh` creates `cheatgui/.venv` on first run. Everything used is in the
Python standard library, so the venv stays empty; it exists so nothing is ever
installed into the host Python.

Only one window runs at a time; a second launch is refused and names the one
that is already open. Two windows on the same card each hold their own idea of
what is installed, and since **Send to Pocket** writes exactly what is ticked,
whichever saved last would silently win while the other still showed its stale
ticks. `--list` is exempt, being read only, so you can query the card from a
terminal with the window open.

## Reading the card takes as long as it takes

A card that has just been inserted is slow to read the first time: about
fifteen seconds for three systems on exFAT over USB with nothing cached. That
is the card, not the app, and no amount of rearranging removes it. Mounting
before starting the app only feels instant because the desktop has already
walked it for you.

So it is stated rather than hidden. Reading happens behind a modal that says
what it is doing and how far along it is, and the window opens fully populated
rather than filling in a pane at a time. Filling in progressively was tried and
was worse: the window looked ready while half of it was not, and clicking into
it got you a list that changed under you. **Stop** gives up on the rest and
keeps whatever was read.

Warm, the whole thing takes a few milliseconds and the modal never appears.

Reading the card happens off the Tk thread. A cold walk of `/Assets` over USB
takes seconds, and doing it inline froze the window mid-click, which is
indistinguishable from a crash even though it recovers. Scanning, listing a
system, loading a game and writing all run in a worker; the newest request
wins, so clicking through the game list faster than the card can answer leaves
the pane showing what you asked for last rather than whichever read finished
last.

If the window ever stops responding, `kill -USR1 <pid>` prints the stack of
whatever it is blocked on to its stderr. That is how both of these were
found.

## The Pocket core, and the boot ROMs it needs

The bar above the database one is the more fundamental of the two. A stock
Pocket core ignores cheat files, so on a card without the cheat core every
button in this window is a no-op that looks like it worked.

```
Pocket core: kroy.GBC 1.4.0-cheats.9, kroy.GB 1.4.0-cheats.9  up to date
Pocket core: kroy.GBC 1.4.0-cheats.8  update available: 1.4.0-cheats.9 (kroy.GBC)
Pocket core: not installed. Nothing written here has any effect until it is.
```

Which core is on the card is read from its own `Cores/<id>/core.json`, and only
for the four ids the app knows about: a well used card carries a hundred cores
and opening every one of those to find four costs seconds over USB. That read
is the first step of the card-reading pass, so it happens behind the same modal
as everything else rather than adding a freeze of its own.

**Cores...** opens a dialog rather than acting. It was a button that decided
what to install and a yes/no box that confirmed the decision, which held while
there was one repository and two cores that always shipped together. There are
four cores now, from three repositories, released at different times and at
different versions, so "install the core" stopped being one question with one
answer. The dialog shows a row per core -
what the card has, what is available, and whether it can be installed at all -
and hands back exactly what was ticked. A row that cannot be ticked says which
kind of nothing it is: no tag on a real repository, no repository yet, or no
network.

Installing fetches those releases and unpacks them onto the card. Each
zip is downloaded whole, checked, and extracted into a staging directory on the
card, and only then moved into place: the core's own directory is swapped whole
because a core is its `.rbf_r` and its json files together, and `Platforms/` is
merged file by file because it is shared with every other core on the card.
Nothing is written over the live core until the replacement is complete, so an
install that fails or is stopped leaves the one you had working. **Eject** and
**Rescan** are disabled while it runs; unmounting the card halfway through
writing a core is the one thing here that could leave the Pocket with a core
that loads and does not run.

The zips are not signed, so what is checked is what can be checked: the
download comes over a verified connection from the release the API named, and
an archive is refused outright if any path in it would land outside the card or
if it does not hold the core it claims to.

The second line is the boot ROMs. The core loads one before it starts a game
and will not run without it, and those are copyrighted: not in the core,
not in this app, and never fetched by it. What the app does is name the file,
its size and the folder it goes in, which is `Assets/<platform>/common/`. A
file of the wrong size is reported separately from a missing one, because that
is the failure that looks like a working install and then refuses to start
anything.

That list comes from the installed core's own `data.json`, from the slots that
carry a fixed `filename` and are marked required. The browsable slots, the
cartridge and the save, have no fixed filename and are not files you supply;
reporting either as missing would tell every user their card was broken. Only
cores that are actually installed are checked, because a boot ROM for a core
you do not have is not missing, it is irrelevant.

## The cheat database

The picker needs the libretro cheat database and does not ship with one. The bar
along the bottom says what it has and what upstream has:

```
cheat database: 2456 files, 2026-08-01  up to date
cheat database: 2456 files, 2026-03-14  update available: 2026-08-01
cheat database: not fetched yet, press Update
```

**Update** checks upstream first and downloads only if there is something to
download, so pressing it when you are current costs two API calls rather than
2456 files. The download runs on its own thread with the count on screen, so
the panes stay usable while it goes; **Stop** aborts it. A fetch that fails or
is stopped changes nothing, because the files land in a temporary directory and
are only swapped in once every one of them has arrived.

The comparison is against the newest upstream commit that touched the two Game
Boy directories, not the repository head. The head moves several times a week
for systems the Pocket has no core for, and comparing against it would report an
update every time somebody edited a PlayStation cheat file.

Three places are searched, in order: `POCKET_CHEAT_DB` if it is set, the copy
the app fetched into `~/.local/share/pocket-cheats/libretro/`, and the
`external/libretro-database` submodule in a checkout. A submodule is a shallow
clone, and in one of those every path looks as though HEAD introduced it, so its
version cannot be compared with upstream; the bar says so rather than inventing
an answer.

## Ejecting the card

**Eject** syncs and then unmounts. Writing to the card already syncs, but a sync
is not an unmount: the filesystem is still mounted and the kernel may still have
metadata to write back, and a card pulled between the two can lose the write
that the sync was for.

It uses `udisksctl` on Linux and falls back to `umount`, `diskutil unmount` on
macOS, and the shell's own Eject verb on Windows, which is what Explorer uses.
If something still has the card open it says so and leaves the card mounted.
Nothing is forced: a card yanked mid-write is the failure this whole tool exists
to avoid.

## What it shows

Three panes: the systems on the card, the games in the selected system, and the
cheats for the selected game. Tick the ones you want and press **Send to
Pocket**.

**Game Boy**, **Game Boy Color** and **PC Engine** are listed. An NES or SNES
core on the same card ignores cheat files entirely, so offering checkboxes
there would be a lie.

Which systems are offered is one tuple, `card.ENABLED`. Everything else follows
from it: the database directories fetched, the ROM extensions recognised, the
directories searched for a match. A system listed there and fetched but never
read would be wasted download; one read but never fetched would be permanently
empty, so they come from the same place and cannot drift.

**Game Boy Advance was switched off for a while.** The core it was written
against had no cheat data slot at all, so nothing written beside a GBA ROM was
ever read, and its codes could not be decoded either: a system, a game list and
a set of checkboxes that could do nothing in either direction, plus 513
database files fetched for it. Both halves have since stopped being true - the
fork defines slot 7, and `gbacht` decodes CodeBreaker and GameShark - so it is
back, with one property none of the others has: see below.

## PC Engine is the other way round from Game Boy

**Every published PC Engine cheat is a RAM poke. None is a ROM patch.** There is
no Game Genie for this machine. On Game Boy the read override is the primary
mechanism and the poker is the addition; here there is only the poker.

That is why the **Applied** column disappears when you select a PC Engine game.
A column carrying the same word in every row is noise dressed as information,
so the fact is stated once underneath instead. `cheatfile.MECHANISMS` is what
decides this, and `ui.retune_applied` is what acts on it.

The 397 files in the libretro directory come in two shapes, and a file uses one
or the other. Counted in full on 2026-08-25:

| | | |
|---|---:|---|
| form A | 246 files | `cheat0_code = "1f1548:64"`, a hex CPU address and a hex byte, several joined by `+` |
| form B | 151 files | `cheat0_code = ""` and the code in `cheat0_address` (a **decimal** offset into work RAM), `cheat0_value`, `cheat0_cheat_type` and a pile of `cheat0_rumble_*` |

Form B is RetroArch's own cheat-search format. It matters because a parser that
only handles form A shows well over a third of the directory as empty, and 104
of those 151 files carry ordinary game names rather than the `(Rumbles)` suffix
you might filter on. `cheatgui/pce.py` reads both and writes both back in form
A, which is what the core's loader will read.

Four kinds of row are listed with their description and no codes, so they show
greyed rather than silently vanishing:

* **70 rows with `cheat_type = 0`**, RetroArch's "disabled". The row watches an
  address to fire a rumble and never writes anything. Converting one into a
  poke would invent a cheat: "Rumble on gold change" with value 5 would pin
  your gold to 5.
* **2 bit-level rows** in Wonder Momo, `memory_search_size = 0`. There is no
  way to express a partial byte here and it will not be guessed.
* **1 row with no value at all**, also Wonder Momo.
* **3 rows with an impossible address.** Bomberman 94 carries
  `18446744073709546426`, which is -5190 written as an unsigned 64-bit number;
  both Magical Chase files carry `1f0000f:0c`, seven hex digits where all 1027
  other codes have six, and past the 21 address lines the HuC6280 has.

Two things it does carry that look odd and are not. The **repeat family**
(`repeat_count`, `repeat_add_to_address`) is expanded: eleven rows use it with
a count of 1, where it means nothing, and one does not. Wonder Momo's "One hit
kills bosses" is a count of 2 stepping the address by 32, and reading only the
first half would half-apply it. And **13 codes sit between 0x1F2000 and
0x1F2656**, which is inside the 32KB a SuperGrafx carries and outside the 8KB
everything else does; they are addressable and, on a core that drops SuperGrafx,
unreachable. `pce.in_work_ram` is how to ask.

There is no code store meter for PC Engine, only a count. The core has not
fixed a poker table size, and a number on screen that no hardware agrees with
is worse than none.

## Game Boy Advance is the one system we compile for

Every other core reads the `.cht` off the card. The GBA core cannot: its cheat
engine went into a design already at 90 % logic utilisation, and an ASCII
parser on the FPGA measured 441 ALMs but grew the design by 1,285 and cost
0.54 ns of setup timing, which is the difference between a core that runs and
one that does not exist. So the parse happens here and the core reads packed
128-bit entries.

That inverts the usual relationship. Everywhere else this app *models* the
core's parser and drift means the display is wrong; here it *is* the parser,
and drift means the cheat is wrong. `cheats/README.md` says so where the copies
live.

Two files land beside the ROM, and `writer.py` is the only place that knows it:

    Game.gba.cht      the cheats, their descriptions and their enable flags.
                      The state file: it is what the app reads back to know
                      what is ticked, and what everything else in the app
                      already understands.
    Game.gba.chtbin   the same cheats as a 16-byte header and one 16-byte
                      entry each. What the hardware reads, and the only thing
                      it reads - slot 7 accepts that extension and no other.

The `.cht` beside it is therefore inert as far as the core is concerned, which
is what makes keeping it safe. It is not the stray `.cht` the core's own docs
warn about; that one is a file copied to the card *instead* of being converted,
and the `.chtbin` header magic exists so that renaming one loads zero cheats
rather than shifting ASCII into the cheat table.

Ordering is the part worth being careful about, and `writer.write` does it
deliberately: the compiled file is written last and removed first, so that the
moment the hardware's behaviour changes is never ahead of the record of why.

One more thing differs. A cheat's cost is not its code count. One code becomes
one 128-bit entry, the table holds 32, and a conditional code spends two
because `gba_cheats` expresses "if" as a compare entry immediately followed by
the entry it guards. `gba.py` hands out one code per entry so the meter and the
limit check stay honest with a single number, and nothing anywhere may sort or
reorder them: adjacency is the conditional.

## Codes we cannot read are carried, not guessed

This is what happens for a system with no decoder, which today is none of the
ones listed. It is written down because it is the rule the next system arrives
under, and because Game Boy Advance was carried this way until its core defined
a format.

GBA cheats are a different language from Game Boy ones. A CodeBreaker code is
an eight digit address and a four digit value joined with `+`, like
`3300786D+00FF`; a Game Boy GameShark code is eight digits meaning something
else entirely.

Handing such a file to the Game Boy parser does not fail, which is the whole
problem. It sees eight hex digits, reads them as a GameShark code, and reports
a write of `0x00` to `$6D78`, an address that is not in the code at all. The
`+00FF` is four digits, matches nothing, and is dropped. Every cheat in the
file comes out looking plausible and meaning nothing, and a file written back
from that has lost half of itself.

PC Engine is the same trap with different numbers: `1f1548:64` is a poke of
0x64 into work RAM, and the Game Boy parser reads the six hex digits as a Game
Genie code and reports a ROM patch of 0x1F to `$7154`. Wrong mechanism, wrong
address, wrong value, and nothing anywhere says so. That is why `cheatfile.py`
routes by platform rather than running one parser over everything, and why
`tests/test_pce.py` pins that exact wrong answer.

So a file we cannot decode is carried verbatim instead. You can list, pick and
send it, and what lands on the card is character for character what the
database had. What you do not get is anything this app would have to invent:
the **Applied** column, and a code store meter. Matching is also restricted to
that system's own directory: Game Boy and Game Boy Color share a search,
because a GBC release filed under Game Boy is a near miss worth catching, and
so is `pce` restricted to its own, since libretro also ships SuperGrafx and
PC Engine CD directories that this core runs neither of.

The **Applied** column says how the core makes each cheat take effect, because
the two ways do not behave the same:

| | |
|---|---|
| `written` | a GameShark code: the value is written into RAM once a frame, so the game's own logic still sees it and can clamp it |
| `patched` | the CPU's read is overridden. Right for Game Genie, which patches ROM, and the fallback for a GameShark code aimed somewhere the core cannot write |
| `mixed` | one cheat holding both |

A written cheat puts the value where the game would find it by any route, not
just on the one read the core can see, which is what the codes were written
against. `docs/CHEATS.md` has the detail, and the status line totals the codes
each way for the current selection.

This says nothing about whether a cheat's *value* suits your save. A code that
sets health to sixteen hearts draws sixteen hearts either way.

## Cartridges

**The full warning is in the [README](../README.md#cartridges-read-this-part),
and it is worth reading before you send anything to a cartridge.** The short of
it: identifying the right cheat file for a cartridge is your job, nothing here
can check it, and a GameShark code aimed at the wrong revision is a real write
into work RAM that can crash the game or end up in its save.

A cartridge is not a file on the card, so it never shows up in the game list.
The **Cartridges** entry in the systems pane is a list you keep yourself:
**Add cartridge...**, name it as the ROM is named so cheat files match, say
which system it is for, and it behaves like any other game from there.

Your cartridges are filed under **Game Boy** and **Game Boy Color** headings,
each showing how many are under it; a system you own nothing for is not shown
at all. The heading is a heading, not a game: selecting one leaves **Remove**
and **Move** greyed out, because there is nothing there to act on.

The system a cartridge is filed under is not cosmetic. It decides which folder
on the card the cheat file goes in, and the core's **Load Cheats** browser
opens on that folder, so a cartridge under the wrong heading writes its file
where nothing will look for it. It used to be assumed to be Game Boy Color,
which was right often enough to be quietly wrong the rest of the time, so the
Add dialog asks. **Move to...** refiles one afterwards and carries the
remembered cheat source with it, since correcting the system should not also
lose the file you picked. The file already written under the old system is left
where it is, exactly as **Remove** leaves it. The list is
`~/.config/pocket-cheats/cartridges.json`, outside the repo; which cheat file
each one uses is remembered alongside everything else, and **Change source...**
repoints it. **Remove** drops a cartridge from the list and forgets which file
it used, and leaves any cheat file already on the card alone.

**Send to Pocket** writes to `/Assets/<platform>/common/Cartridges/<name>.cht`.
It goes in its own folder so that the core's file browser opens on your
cartridges instead of a few hundred ROMs: in Play Cartridge mode the Pocket does
not auto-load a cheat file named after the cartridge, so you browse for it once
from the core menu and the slot remembers it.

Be careful which codes you send. You cannot check which revision a cartridge is
from the outside, and the two kinds of code fail differently when you guess
wrong. A Game Genie code carries a compare byte, so on the wrong revision it
never fires. A GameShark code has no such check: it is a real write to an
address that may hold something else entirely on that revision, and it can
corrupt a save. The status line warns when a cartridge selection contains
written codes, and `cheats/cht check --rom` verifies compare bytes
against a dump if you have one.

## Cartridge dumps are the other kind of cartridge

The section above is about a cartridge in the slot, which the app never sees.
This is about one the dumper core has already read into a file, which it sees
entirely. Everything that makes the first case a warning is absent here, and
the reason is one sentence: **a dump has an identity and a played cartridge
does not.**

`dumps.py` is the engine and draws nothing. `ui.DumpsDialog` is the window.

### What the core actually leaves

Not what its format document describes; these are the observed facts of a real
card. Dumps land flat in `/Assets/carttools/common/`, there is no sidecar, and
the filename is close to worthless: it is the cartridge title read from a fixed
header offset and sanitised, so a Game Boy Color cartridge whose title is
eleven bytes has four bytes of manufacturer code appended - `ZELDA_DIN__AZ7E`.
Two cartridges that title themselves the same produce the same filename and the
second overwrites the first, on the card, before the app is involved. The core
cannot list a directory, so it cannot deduplicate, rename, or recover from any
of that.

Everything the app does here follows from being the only component that sees
the bytes after they land.

### Identification is by hash, and only by hash

SHA-1 over the file, looked up across every loaded DAT, and the DAT that holds
it decides. Not the extension and not the header, which both look like they
would work:

The core does now write `.gb`, `.gbc` and `.gba` correctly, and on a real card
all thirty-two agreed with the extension No-Intro gives the same file. That is
worth knowing and not worth trusting, because the core derives Game Boy from
Game Boy Color by reading the CGB flag at `0x143`, and **No-Intro's split is an
editorial judgement rather than a header bit**. The Game Boy set holds *Pokemon
- Yellow Version ... (CGB+SGB Enhanced)* and the Game Boy Color set holds
*Pokemon - Gold Version (USA, Europe) (SGB Enhanced) (GB Compatible)*, so the
flag is wrong in both directions.

The DAT also hands over the filename: `<rom name>` is the game name plus the
extension, taken verbatim, which is why identifying a dump and naming it are
the same operation. There is no naming convention to implement.

CRC32 is computed in the same pass and compared with what the core displayed at
dump time. That is a read-back check the dumper cannot make - its own checksums
cover bytes leaving the reader, and nothing confirms they reached the card.

### The DAT files, which are not ours to ship

`nointro.py` reads either the DAT or the Parent-Clone DAT, from the zip as
downloaded, and normalises both to one representation so nothing downstream
branches on flavour. It indexes on SHA-1 because that is the only hash present
in every entry of every flavour; sha256 is 96% of the Standard Game Boy set,
41% of Game Boy Color and 30% of Game Boy Advance, and absent from Parent-Clone
entirely.

The DB Export cannot be read at all, for two independent reasons: its zip holds
an `.xml` rather than a `.dat`, and the XML has two top-level elements so no
conforming parser will take it. That is reported as itself rather than as a
blank, because "no data loaded" is true and useless to somebody who went to the
right page and downloaded a real file. A file that merely fails to parse is
reported as damaged instead - two valid DATs concatenated give the same parser
error as a DB Export and are told apart on the document's first element.

**Add DAT...** lists what it finds where the browser puts downloads and has a
button that opens No-Intro's page. Nothing is fetched and nothing is bundled.

### The library, and what is disposable in it

On the computer, chosen once, remembered in `prefs.py`:

```
<library>/
    roms/         canonical No-Intro names, extension and all
    cart-dumps/   originals, under the names the core gave them
    saves/        nothing writes here yet; the dumper cannot back a save up
    index.json    the store; delete it and it rebuilds
```

`index.json` is one versioned JSON file written with the same atomic replace
`prefs.py` uses, keyed on SHA-1 and never on filename - the filename is exactly
what collides. It lives in the library rather than with the config, so copying
the library copies its index and there is no separate backup feature to build.

It is a cache. **Deleting it must lose nothing but time**: a rebuild walks the
library, re-hashes, and asks the DAT again. Anything that cannot survive that
is a decision rather than an observation and lives in `prefs.py` - which is
where a rejection and a pinned cheat file go. `library.Row` is frozen with a
closed field list so a decision cannot be filed into the index by accident.

### Nothing bulk, and no state without an exit

The window is a list. Tick rows, press the button that names what happens.
Nothing is written for a row that is not ticked, which is the part that
matters: the app never decides on its own what to do with a dump, because the
failure mode of a wrong automatic answer is a dump filed under another game's
name.

It was a wizard first, one dump at a time behind a chain of modals, and that
was wrong twice over. It cost four clicks a dump, which over a card of
thirty-two is a hundred and thirty. Worse, the list shows what is *on the
card*, so every answered dump vanished from it - a card that filed perfectly
looked like one where nothing had happened, and the only fix for that is to
say what the library holds, which the window now does.

**Every state has a way out of it.** This was the real fault and it stranded a
real file. Once a SHA-1 is in the index the verdict is `FILED`, `FILED` is not
actionable, and so a dump whose card copy was still there could never be
cleared - the window called it finished and greyed every button. Turning one
down was the same trap with no way back at all. So clearing the card copy is
now offered whenever *the library provably holds the same bytes*, whatever the
verdict says, and turning a dump down doubles as offering it again.

### The one destructive step

Clearing a dump from the card is the only thing here that deletes anything, and
the check in front of it is the strongest available rather than the cheapest:
the bytes, compared, stopping at the first difference. Not a re-hash, which is
a statement about a digest.

Written out rather than handed to `filecmp`, which caches its answer against
each file's stat signature - two files of the same length with the same
timestamp compare equal without being read, which is the wrong kind of thing to
put in front of a delete. The comparison is also made again immediately before
the delete rather than trusted from the copy, because minutes and a human
decision sit between them and the card is removable.

### Cheats come free, because the name does

`match.best()` is hopeless at `ZELDA.gb` and good at `Legend of Zelda, The -
Link's Awakening (USA, Europe)`, so the enrichment is what makes the matcher the
app already has work on a dump. No new matching code exists.

Where a clone has no cheat file of its own the parent's name is the second
attempt - but that is narrower than it sounds. `match.normalize()` strips
parenthesised tags, so a *regional* clone already finds its parent's file under
its own name and never reaches the fallback. What reaches it is a clone that
was retitled, which is a small minority of the clone links in a DAT. Both paths
are pinned by tests, the regional one deliberately: if `normalize()` ever stops
stripping tags, the fallback would quietly start carrying every regional clone.

**Cheats...** shows the match, every ranked alternative with its score and
whether it is yours or libretro's, and pins one if you want a different answer.
The pin is keyed on the canonical name - safe only because that name is
canonical, since the core's own names collide and this one cannot.

## How it decides things

**The file on the card is the state.** There is no separate database. Opening a
game reads the `.cht` already sitting next to the ROM and ticks those cheats, so
what you see is what the Pocket will do.

**Ticking nothing removes the file.** That is how you take cheats off a game,
and it is what the file being the state means: no cheats, no file. Because
"Send to Pocket" does not read like a deletion, the button says
**Remove from Pocket** instead whenever that is what pressing it will do, it
asks before doing it, and the copy it leaves behind is `.cht.bak`.

**Only ticked cheats are written.** The core reads the first 32 cheats in a file
whatever their enable flag says, so handing it a 100-cheat libretro file would
truncate before reaching the one you wanted. Writing just the selection avoids
the limit, and the bar above the status line shows how much of the store the
selection uses: it fills as you tick, ambers near 32 and turns red past it,
saying how many codes will not fit. It counts codes rather than cheats because
every cheat carries at least one code, so the code store always fills first.
Going over is otherwise silent: the core parses until the store is full and
ignores the rest, so the cheats past the limit load, read as enabled, and do
nothing.

**Cheats it does not recognise are kept.** If the card holds a cheat that is not
in the matched libretro file (hand-written, or from another source), it is shown
in green marked *already installed* and starts ticked, so saving cannot quietly
throw away work.

**Your own cheat files.** An update replaces the libretro database wholesale, so
anything added inside it is lost the next time you press Update. Put yours in
`~/.local/share/pocket-cheats/cht/` instead, which is outside it, named after
the ROM exactly as the ROM is named, and the picker finds them. They are searched first, so a file you
wrote wins an otherwise exact tie, and the source line marks it *(yours)*.

```sh
cheats/cht list
cheats/cht new "Zelda (USA) (Rev 2)" --from "Zelda (USA)"   # start from a stock file
cheats/cht add "Zelda (USA) (Rev 2)" "999 Rupees" 9199ADC6+9109AEC6
cheats/cht check "Zelda (USA) (Rev 2)" --rom /path/to/rom.gbc
```

`check --rom` is the one worth using on anything hand-entered: it verifies each
Game Genie compare byte against that ROM and says which bank it matches, so a
code copied from a site that targets a different revision is caught before it
reaches the card rather than silently never firing.

A remembered choice beats matching, so if a file of yours is not being picked
up, the source line will say *(pinned)*; **Change source...** repoints it.

**Matching prefers the same release.** Titles alone decide the match, which
leaves dozens of files tied for a popular game, so the region and variant tags
break the tie: a ROM tagged `(USA, Australia)` lands on the cheat file with the
same tags rather than on whichever name is shortest. **Change source...** picks a
different file and remembers it in `~/.config/pocket-cheats/prefs.json`.

Cheats whose codes are `XX`-style placeholders are greyed out and cannot be
ticked: they carry no usable value, and the core drops them.

**The whole file is listed, not the part the core would read.** The core takes
the first 32 codes, but that is a limit on what you can send, not on what you
can choose between: a libretro file may hold hundreds of cheats, and Pokemon
Red's holds 518. Reading the file through the core's own limits, which is what
this did at first, showed 3 of them. The status line warns if a selection
exceeds what the core can hold.

`romhacks/` folders are skipped. They hold pre-patched variants of a ROM that is
already in the list and match nothing in the cheat database, so they only add
duplicates. Nothing is hidden from the card, only from this tool; change
`SKIP_DIRS` in `card.py` to list them again.

## Where its output goes

The Linux build is a console program: `--list`, `--check-db` and the timing
lines print wherever you ran it. The Windows and macOS builds are windowed, and
a windowed process has no console, so Python hands it a `sys.stdout` and a
`sys.stderr` of `None`. Writing to that raises rather than being quietly
dropped, and for several releases it did: `faulthandler.enable()`, there to
print a stack when the window stops repainting, checked stderr at startup,
found `None`, and killed the Windows binary before its window ever opened.

So nothing prints directly any more. Every line outside the GUI goes through
`say.py`, which writes to the first of these that exists:

- the interpreter's own stream, which is a checkout or the Linux build, and is
  the ordinary case;
- the console the exe was launched from. Windows does not hand a windowed
  process its parent's console, so the app asks for it, and
  `pocket-cheats.exe --list` from cmd prints into that cmd;
- a log file beside the database the app fetched,
  `~/.local/share/pocket-cheats/libretro/pocket-cheats.log`, so a crash dump
  from a double clicked build can still be read afterwards. It starts over once
  it passes half a megabyte;
- nowhere, quietly, which is all a missing debug line should ever cost.

That last one is why `--check-db` gets special treatment. It exists to be read
back to somebody in a bug report, so when there is nowhere to print it -- a
windowed build, double clicked rather than launched from a terminal -- it puts
the same report in a message box:

```
version:  1.4.0 (packaged)
database: C:\Users\you\.local\share\pocket-cheats\libretro\cht
local:    2456 files, 2026-08-01
ca store: ...\certifi\cacert.pem (bundled)
upstream: 8f2c1ab9de 2026-08-01
verdict:  upstream reachable and verified
```

The CA store line is there because a packaged build carries its own trust store
rather than the machine's, and a failure to reach upstream is otherwise
impossible to tell apart from a failure to verify it.

## Safety

**Cheats can corrupt save files, and this app cannot tell you when one will.**
It checks that a cheat file is well formed and that the core can hold it. It
has no way to check that a code is right for your copy of a game. A GameShark
code is a write into work RAM, the same memory a game builds its save data
from, so a code meant for another revision overwrites something else and the
result reaches your save at the next save point. Back up saves you care about
before turning cheats on, and see the cartridge section above, where there is
no backup to take.

Writes go only to a directory that has both `Cores/` and `Platforms/`, so a muOS
or plain ROM card cannot be mistaken for a Pocket card. An existing cheat file is
copied to `.cht.bak` before being replaced, the new file is parsed back and
checked before the call returns, and the card is synced afterwards. Eject it
normally when you are done.
