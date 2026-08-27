# Cartridge dumps: identifying, naming and filing what the dumper wrote

**Status: plan. Nothing here is built.** This is app work only. Nothing in this
document changes the dumper core, and nothing here writes to a cartridge. The
core reads cartridges and writes files to the SD card; this app reads those
files and tidies up after them. That split is not ours to renegotiate — it is
stated in `pocket-cartridge/docs/COMPANION-APP-PLAN.md` and repeated here
because every decision below follows from it.

## Two different things called "cartridge"

The app already has a cartridge feature and this is not it. Keeping them
separate in the head is most of the work of keeping them separate in the code.

**Cartridge play** is what `carts.py` does today. A cartridge is in the slot,
the Pocket is playing it, and no file on the card represents it. You type its
name yourself, the app files a `.cht` under `Assets/<system>/common/Cartridges/`,
and you browse to that file from the core menu. Nothing is identified: the app
never sees the cartridge and takes your word for what it is. Game Boy and Game
Boy Color only.

**Cartridge dumps** are what this document is about. The dumper core has read a
cartridge and written a ROM image to the card. That image is bytes, so it can be
hashed, identified, named correctly, and matched to a cheat file the same way a
ROM on the card is. Nothing is taken on trust.

The difference that matters: **a dump has an identity and a played cartridge
does not.** Everything below exists because of that.

## What the core actually leaves us

Not what its file-format document describes. These are the observed facts, and
the app has to be right about them rather than about the spec.

- Dumps land **flat** in `/Assets/carttools/common/`, not in the `Dumps/`,
  `Saves/`, `Metadata/` tree `FILE-FORMATS.md` lays out. That tree is planned;
  nothing has written it.
- **There is no sidecar.** `.cart.json` is fully specified and nothing emits it.
  Every field the app wants — title, mapper, sizes, checksums — comes from the
  ROM bytes or comes from nowhere.
- **The filename is close to worthless.** It is the cartridge title read from a
  fixed header offset, sanitised to `A-Z a-z 0-9 space _ -`. On a Game Boy Color
  cartridge the code reads fifteen bytes where the title is eleven, so four
  bytes of manufacturer code land in the name: `ZELDA_DIN__AZ7E.gb`.
- **The extension is a hint and not an authority.** This was written when the
  core wrote every dump as `.gb`. It no longer does: on a card dumped 2026-08-26
  and -27 all seventeen dumps carry `.gb`, `.gbc` or `.gba`, and all seventeen
  agree with the extension No-Intro gives the same file. That is worth knowing
  and it is not worth trusting. The core derives Game Boy from Game Boy Color
  by reading the CGB flag at `0x143`, and **No-Intro's split is an editorial
  judgement rather than a header bit** - the Game Boy DAT holds *Pokemon -
  Yellow Version ... (CGB+SGB Enhanced)* and the Game Boy Color DAT holds
  *Pokemon - Gold Version (USA, Europe) (SGB Enhanced) (GB Compatible)*, so the
  flag is wrong in both directions. Neither cartridge is on this card, which is
  not the same as there being no such case. Hash the bytes and let the DAT that
  contains the hash decide; see *Cheats, and why the enrichment is what makes
  it work*.
- **Collisions destroy data and the app cannot prevent it.** Link's Awakening
  and Link's Awakening DX both title themselves `ZELDA`, both produce
  `ZELDA.gb`, and the second silently overwrote the first on a real card. The
  documented `_2`/`_3` suffix scheme has never shipped.

That last one sets the app's real job. The core cannot list a directory, so it
cannot deduplicate, cannot rename, and cannot recover from a mess. **The app is
the only component that can, and it is also the only component that ever sees
the bytes after they land on the card.**

## The DAT: what to download, and why we do not ship it

Identification needs No-Intro's data. **We do not redistribute it.** It is their
work, their terms, and their site gates downloads; bundling a copy would be
taking it. The app asks for a file the user fetched themselves, and says so
plainly when it is missing.

From <https://datomatic.no-intro.org/>, Download section, **defaults left
alone**, one file per system:

| System |
|---|
| Nintendo - Game Boy |
| Nintendo - Game Boy Color |
| Nintendo - Game Boy Advance |

Three separate downloads: No-Intro treats them as three systems, and the split
is real — every Game Boy Color title checked against the Game Boy DAT is absent
from it, and found in the Game Boy Color one. Each arrives as a zip containing one `.dat`, which is XML. The app
should accept the zip as downloaded, not require the user to extract it.

All three are wanted before the feature is much use, but any one of them works
on its own for the systems it covers. A missing DAT should read as "no Game Boy
Color data loaded", not as a failure.

**Take either the Standard or the Parent-Clone DAT.** The site offers both plus
a DB Export, and people will click whichever button they land on. Refusing a
perfectly good file over a flavour label would be obnoxious, and the app can
tell them apart in one line: Parent-Clone names the parent in `cloneof`,
Standard points at it with a numeric `cloneofid` that resolves against each
game's `id`. Both express the same graph; the second needs one dictionary built
while parsing.

Measured on the real Game Boy exports of 2026-08-27, both at the same version.
\* The 1963 regions are `<release>` elements rather than games: they spread over
1657 of the 2295 entries, led by Japan 810, USA 571 and Europe 502.

| | entries | crc/md5/sha1 | sha256 | clone link | regions |
|---|---|---|---|---|---|
| Standard | 2001 | 100% | 96% | `cloneofid`, numeric | none |
| Parent-Clone | 2295 | 100% | 0% | `cloneof`, by name | 1963* |
| DB Export | 2333 | 100% | 100% | `clone` + `regparent` | full blocks |

The 294-entry gap is not a format difference. Compared by hash, Parent-Clone
holds 298 entries Standard does not and Standard holds 4 that Parent-Clone does
not, and the 298 are aftermarket and unlicensed homebrew — *Linea, La (World)
(Aftermarket) (Unl)*, *14 Juillet*, *Sam Mallard*. Parent-Clone is the wider
net, so **prefer it if you dump homebrew**, and otherwise it does not matter.

DB Export carries much more — development status, categories, languages,
licensed and aftermarket flags — in a different schema at four times the size,
and none of it is needed to identify or name a dump.

**But it cannot be read at all, and the app has to say so.** This was written
as "a later enrichment, not a requirement", which was too generous. Checked
against the real 2026-08-27 download, a DB Export fails for two independent
reasons:

- **It is not a `.dat`.** The zip holds one `.xml`, so anything looking for a
  DAT member finds nothing and stops before parsing.
- **It is not well-formed XML.** The file has *two* top-level elements,
  `<header>` followed by `<datafile>`, and any conforming parser refuses it:
  `junk after document element: line 9, column 0`. The 2333 figure in the table
  above comes from counting `<game ` in the text, not from parsing, and the
  hash coverage claimed for that row should be read the same way.

So the flavour that "does not matter" quietly does. Today the loader returns
nothing and the user is told "no Game Boy data loaded", which is true and
useless: they downloaded a file from the right page, for the right system, on
the right day, and the app implies they did not.

**Recognising it is two cheap checks** — a lone `.xml` member where a `.dat`
was expected, or a document whose first element is `<header>` rather than
`<datafile>` — and the message has to name the mistake and the fix: this is the
DB Export, download the DAT or the Parent-Clone DAT instead. Refusing a file is
fine. Refusing it without saying which of the three buttons to press instead is
the thing to avoid, and it is the same instinct as the sentence above about not
being obnoxious over a flavour label.

**Match on SHA-1.** It is the only hash present in every entry of every flavour.
sha256 is 96% of the Standard Game Boy DAT, 41% of Game Boy Color, 30% of Game
Boy Advance, and entirely absent from Parent-Clone, so anything keyed on it
fails on real data rather than failing loudly.

An entry looks like this, and the shape is the whole reason this is tractable:

```xml
<game name="[BIOS] ... Boot ROM (Japan) (En)"
      cloneof="[BIOS] ... Boot ROM (World) (Rev 1)">
  <rom name="....gb" size="256" crc="c2f5cc97" sha1="8bd501e3..." status="verified"/>
</game>
```

`<rom name>` is `<game name>` plus the extension. **Identifying a dump is
therefore the same operation as naming it** — there is no naming convention to
implement, no tag ordering to get right. The DAT hands over the filename.

**Use that name verbatim, extension included.** Do not rebuild it from the game
name and the system, because the extension is not a property of the system: the
Game Boy Advance DAT contains three entries ending `.bin` and two ending `.gbc`,
all of them boot ROMs rather than cartridges. Taking the name as given costs
nothing and handles those without a special case.

## Identity, and a verification the core cannot do

The app computes SHA-1 over the ROM bytes and looks it up. Three outcomes, and
the third is not an error:

- **match** — one entry. The dump is that game, and its canonical name is known.
- **unknown** — no entry. A bad dump, a revision the DAT lacks, or a
  reproduction cartridge. The app cannot tell these apart and must not pretend
  to. It says unknown and offers nothing automatic.
- **mismatch** — size or CRC32 disagree with the entry the SHA-1 found. Should
  not happen; if it does, something is wrong with the file or the DAT.

The app also computes **CRC32**, which the core displayed on screen at dump
time. If they disagree, bytes changed between the FPGA and the file on the card.
That is read-back verification, which the dumper explicitly cannot do — its
checksums cover bytes *leaving* the reader, and there is no flush confirmation
that anything reached the card. The app is the only place this check can happen,
it costs one pass over a file already being read, and it should be reported
whenever the core's CRC32 is known.

Two GB dumps were corrupted once by a mechanism that never reproduced and is
not proven fixed. An index built from card contents can be wrong even where
every on-device check passed. Nothing here should present identification as
proof the dump is good.

## The store

**Not SQLite.** One versioned JSON file, `index.json`, written with the same
atomic replace `prefs.py` uses, holding a row per dump keyed by SHA-1.

It lives **in the library, not with the config**, unlike `prefs.json` and
`cartridges.json`. The library is what it describes, so the two travel together:
copying the library copies its index, and moving the library to another machine
does not strand it. That is the export and the backup — there is no separate
feature, because the file is already a plain file in the directory the user
already keeps.

It is a **cache, not a source of truth.** The rule from the companion-app plan
is that the file on the card is the state and the app must not keep a private
database the card can disagree with. Here that means: **deleting `index.json`
must lose nothing but time.** A rebuild walks the library, re-hashes what it
finds, and asks the DAT again. Anything that cannot survive that — an approval,
a rejection, a user's cheat override — is a decision, not an observation, and
decisions belong in `prefs.py` where the app already keeps them.

That split is the whole design of the store. Observations are disposable and
recomputable; decisions are small, and kept.

A row is keyed by **SHA-1**, never by filename. `prefs.get_source()` keys on
basename today, which is exactly the thing that collides across two different
cartridges; the dump index cannot reuse it.

`cartridges.json` gains a version field before anything else touches it. It has
none, and the existing plan flags migration as an open question.

## The library

**On the computer, chosen once, remembered in `prefs.py`.** The app does not
invent a path and does nothing until one is set: `~/.local/share/pocket-cheats/`
holds the cheat database, but that is a cache the user never opens, and these
are files they will want to find.

It is not only for dumps. Save backup is coming to the dumper, and when it
arrives the app's job is dated, immutable, off-card copies — many per cartridge,
kept forever, never auto-deleted. That is a different shape from ROMs, where
there is one file per cartridge and re-dumping produces the same bytes. **The
layout has to have room for saves now, or filing every dump twice is a migration
later.**

```
<library>/
    roms/                     canonical No-Intro names, extension and all
    cart-dumps/               originals, under the names the core gave them
    saves/                    dated, immutable, one directory per cartridge
    index.json                the store; delete it and it rebuilds
```

**No per-system directories.** The extension already tells a cartridge dump
apart, and after enrichment it is right: all 2295 entries in the Game Boy DAT
name their rom `.gb`, all 2038 in Game Boy Color `.gbc`, and 3528 of 3533 in
Game Boy Advance `.gba` — the five exceptions being boot ROMs, which nobody
dumps out of a cartridge slot. The canonical name carries the extension, so
renaming the dump is what fixes it, including the core's current habit of
writing Game Boy Advance dumps as `.gb`.

Splitting by system would encode that fact a second time, in the path, where it
could disagree with the filename. One authority is better than two, and the
extension is the one the rest of the world already reads.

`cart-dumps` is separate because those names are *not* trustworthy — that is the
whole point of keeping them. `saves` is separate because a save belongs to a
cartridge and there will be many of them for one ROM.

Nothing under `saves/` is built by this plan — the core cannot back a save up
yet. The directory is named here so that when it can, nothing above it moves.

**What goes inside it is a later plan, deliberately.** The top level has to be
settled now, because filing dumps into a layout that later needs a `saves/`
alongside them is a migration. The shape *within* `saves/` does not: nothing
writes there, so there is nothing to migrate, and the sensible time to decide
whether a cartridge's backups sit in a directory of their own or under a flat
dated naming scheme is when there is a real `.sav` from a real cartridge to look
at. Deciding it now would be guessing about a file format that does not exist,
and the guess would be load-bearing by the time anyone could check it.

## The one-click flow

For each dump, in order, with nothing happening until it is approved:

1. Hash it. SHA-1 for identity, CRC32 for corroboration.
2. Read the header for platform. Not the extension — GBA dumps are `.gb` today.
3. Look up the SHA-1 in that system's DAT.
4. **Copy** to the canonical name under `roms/`.
5. **Move the original** to `cart-dumps`, keeping the core's own filename, and
   removing it from the card only after the copy is verified and confirmed, so the flat pile in `/Assets/carttools/common/` empties as it is
   processed and what remains on the card is what still needs attention.
6. Enrich: record the No-Intro name, region, clone parent, hashes and sizes.
7. Match to a cheat file and offer it.

Both destinations are on the computer, so step 5 crosses a filesystem: it is a
copy followed by a delete, not a rename, and it is not atomic.

**Nothing is removed from the card until a byte-for-byte comparison passes.**
Not a re-hash — the bytes, compared. A hash match is a statement about a digest;
this is the only destructive step in the feature, and the check in front of it
should be the strongest one available rather than the cheapest. Both files are
already being read, and the comparison stops at the first difference.

Then the app says where the copy went and asks:

```
Backed up to  <library>/cart-dumps/ZELDA.gb          [Open]
Verified byte for byte against the card.

Remove ZELDA.gb from the card?                [Keep]  [Remove]
```

The path is shown, not described, and **[Open]** opens the containing directory
in the file manager — the same per-platform shell command shape `card.py`
already uses for eject. Someone about to agree to a deletion should be able to
go and look at the thing that replaces it, in one click, before they answer.

The directory, not the file. Revealing the file itself means a different
argument on every platform for no useful difference: the answer to "did it
land" is the directory listing, and the directory is also what the user wants
open when they go looking for the other dumps they have already filed. This is
not a dumps feature; see *Opening a directory, everywhere* below.

The confirmation is not a preference to be switched off. It is asked because the
answer is occasionally no, and a card pulled at any point before **[Remove]**
costs nothing worse than a dump that is still on it.

That leaves two files per dump: the canonical one you use, and the original
under the name the core gave it. The second is provenance — it is the evidence
of what the dumper actually produced, and it is the only thing that can settle a
later argument about whether a bad name came from the core or from us. For a
32 MB Game Boy Advance cartridge it is also 32 MB, twice.

Step 5 is what makes the collision hazard survivable. Emptying the card as dumps
are filed means the next dump of a differently-titled cartridge has nothing to
overwrite. It also means the archive outlives the card, which reformatting or
losing the card does not.

## Cheats, and why the enrichment is what makes it work

`match.best(rom_name, platform)` already matches a ROM filename to a libretro
cheat file by fuzzy title, and libretro's cheat filenames follow No-Intro's
naming. It is good at `Legend of Zelda, The - Link's Awakening (USA, Europe)`
and hopeless at `ZELDA.gb`.

So the enrichment is not cosmetic. **Renaming the dump is what makes the
existing matcher work on it**, with no new matching code:

```
ZELDA.gb  ->  SHA-1  ->  "Legend of Zelda, The - Link's Awakening (USA, Europe)"
          ->  match.best()  ->  the cheat file, already correct
```

The DAT that matched also says which system the dump is, which is the other
thing `match.best()` needs. Game Boy and Game Boy Color are one platform to the
dumper and two cheat directories to libretro, and **the ROM header cannot tell
them apart.** That is not a shortcut being taken; it is a fact about how
No-Intro splits the two, and it is worth stating because the obvious approach is
wrong.

The tempting answer is the CGB flag at `0x143`, which says whether a cartridge
is Game Boy Color enhanced. It does not answer this question. No-Intro's split
is an editorial judgement about which system a game is *for*, and cartridges
that run on both machines land on both sides of it. The two DATs say so in their
own entry names:

```
Game Boy        Pokemon - Yellow ... (CGB+SGB Enhanced)
Game Boy Color  Pokemon - Gold Version (USA, Europe) (SGB Enhanced) (GB Compatible)
```

Yellow is a Game Boy game that is colour-enhanced. Gold is a Game Boy Color game
that is Game Boy compatible. Both run on both machines, both would set the same
bit, and they are in different DATs. A header bit cannot reproduce a decision
that was never made from the header.

So the lookup goes across every DAT that is loaded, and the one that contains
the hash decides. A dump is in exactly one of them. That single answer gives the
canonical name, the extension and the libretro cheat directory together, and
there is no second mechanism that could disagree with it.

This also means the app should say which DATs it has when it reports an unknown
dump. "Not found" means something different with one DAT loaded than with three,
and the user is the only one who can fix the difference.

A dump gets its cheat mapped **by default**, because by the time it is matched
it has a name the matcher was built for. Where a clone has no cheat file of its
own, the parent's name is the obvious second attempt — `cloneof` in a
Parent-Clone DAT, `cloneofid` resolved against `id` in a Standard one.

**That fallback is narrower than it sounds, and it is worth knowing why.** The
first test written for it failed, and the code was right: `match.normalize()`
strips parenthesised tags, so *Widget Quest (Japan)* and *Widget Quest (USA)*
are the same string to the matcher, and the parent's cheat file is already
found on the clone's own name. A **regional** clone — which is most of them —
never reaches the fallback at all.

What reaches it is a clone that was **retitled**, where the difference is in
the words rather than the tags: Probotector against Contra, or any of the
Japanese releases that were renamed for export. Those are the cases `cloneof`
earns its place on, and they are a small minority of the 876 clone links in the
Parent-Clone Game Boy DAT.

Both paths are pinned by tests, the regional one deliberately: if `normalize()`
ever stops stripping tags, the fallback would quietly start carrying every
regional clone, and that test is what would notice.

The user can always override, and an override is remembered, exactly as it is
for a ROM today.

## Approval, one at a time

Nothing is bulk. Each dump is presented on its own with what was found, what
would be done, and what it would be called, and nothing is written until it is
approved. A dump that identifies cleanly still asks.

The reason is step 5. Moving a file is not something to do to a pile at once on
the strength of a fuzzy match, and the failure mode of a wrong automatic answer
here is a dump filed under another game's name — which is exactly the confusion
the whole feature exists to remove.

Rejecting leaves everything untouched and records the rejection, so the next run
does not ask again.

## Doing nothing twice

Idempotency is keyed on SHA-1, and it is the reason the index exists at all:

- A dump whose SHA-1 is already filed is skipped, silently. Re-dumping the same
  cartridge produces the same bytes and needs no second decision.
- A dump previously rejected is skipped, and re-offered only when asked.
- A dump whose SHA-1 is known but whose file is gone is reported, not deleted.
  The app does not remove things it did not just write.
- Re-running over a fully processed card does nothing and says so.

Same bytes, same answer, no work. That is the whole rule.

## When something is already there

Two different things get called a collision, and only one of them is a decision.

**The card already lost a dump.** Two cartridges titled the same, the second
overwrote the first, and this happened before the app ever saw the card. There
is nothing to recover: the bytes are gone and no flow brings them back. The app
can notice — the surviving file's SHA-1 identifies one game and not the other —
and it should say so plainly, once, and then stop talking about it. It is a fact
about the past, not a question.

**The name the app is about to write is taken.** This is the decision, and it
happens constantly rather than rarely, because the core's own names collide by
design: every `ZELDA.gb` original lands in `cart-dumps` under the same name.

First, hash what is already there. That settles most of it without asking:

- **Same SHA-1.** The same bytes are already filed. Nothing to do, nothing to
  ask, and this is the ordinary case when a cartridge is dumped twice. Skip.
- **Unreadable.** Report it and move on. The app does not overwrite a file it
  could not read, because it cannot tell what it would be destroying.
- **Different SHA-1.** Two files, one name, different contents. Ask.

Three answers, and the app should say what each one costs:

```
Zelda (USA).gb is already in your library, with different contents.

  Already there   1 048 576 bytes   sha1 8bd501e3...   filed 2026-08-21
  This dump       1 048 576 bytes   sha1 a4f9c210...

  [Keep both]   file this one as Zelda (USA) [a4f9c210].gb
  [Replace]     file this one, then delete the old
  [Discard]     leave the library alone; this dump stays on the card
```

**Keep both** is the default, and the suffix is a short SHA-1 prefix rather than
`_2`. The core's own file-format notes warn that a consumer must not read `_2`
as meaning a second revision rather than a second cartridge, and they are right:
a counter records the order files arrived, which is not a fact about either
file. A hash prefix is a fact about the contents, it is stable across machines
and reruns, and two files that differ can never be given the same one.

**Replace** writes the new file first and deletes the old only once the new one
is in place — the same order as everything else here, for the same reason. The
old file is not moved to `cart-dumps`; it was never a card original, and putting
it there would put an untrustworthy name next to names that mean something.

**Discard** leaves the library untouched and leaves the dump on the card, which
means it will be offered again next time. That is deliberate: discarding is not
the same as rejecting, and a decision the user has not really made should come
back rather than be remembered as settled.

If the user has told the app to stop asking about a particular dump, that is a
rejection, and it lives in `prefs.py` with the other decisions rather than in
the index.

## Opening a directory, everywhere

**Anywhere the app names a location, it should offer to open it.** The app
already knows a dozen paths and shows several of them as text the user is
expected to copy out and paste into a file manager. That is a chore the app can
do in one click, and every one of these is a place someone eventually needs to
look:

| Where | Path |
|---|---|
| Cheat database | `db.store()` |
| Your own cheat files | `cheatlib.local_dir()` |
| Settings and remembered choices | `prefs.CONFIG` |
| The cartridge list | `carts.LIST` |
| The log, on a windowed build | `say.log_path()` |
| The SD card | the card root |
| Cores on the card | `Cores/<id>` |
| Cartridge cheat files on the card | `Assets/<system>/common/Cartridges/` |
| Where a boot ROM goes | `Assets/<platform>/common/` |
| The library, `cart-dumps`, `saves` | new, above |

One small module, used by all of them. It takes a directory, opens it, and never
raises: failing to open a file manager is not worth an exception, in the same
way and for the same reason that `say.py` will not raise over a line it cannot
print.

Three traps worth writing down before anyone implements it:

- **`explorer.exe` returns a non-zero exit code on success.** Treating that as
  failure is the classic way to get a working feature that reports an error.
- **A sandboxed build has no file manager to call.** Under Flatpak or Snap the
  platform command may not exist or may be intercepted by a portal; the button
  should quietly not appear rather than appear and fail.
- **A path that does not exist yet.** The library before it is chosen, the log
  before anything is written. Offer the button only for a directory that is
  there, and never create one as a side effect of being asked to look at it.

## The boot ROM dialog, and the docs that describe it

Adjacent work, not dump work, and in here because it lands on the same pages and
needs the same new module. It can go in before prong 0 or alongside it.

### What the docs claim that the app does not do

- **`README.md` lists three boot ROMs and there are four.** The table under
  *Boot ROMs* has `gbc_bios.bin`, `gb_bios.bin` and `sgb_boot.bin`, and stops.
  `gba_bios.bin` goes in `Assets/gba/common/` and is 16384 bytes; it belongs in
  that table. The PC Engine needs none, and the table should say so rather than
  leave it out, because an absence in a list of four cores reads as an
  oversight instead of an answer.
- **`docs/CHEATGUI.md` contradicts itself about how many cores there are.** It
  says the card is read "only for the two ids the app knows about" and then,
  eight lines later, "There are four cores now, from three repositories". The
  first is left over from when that was true. The reasoning it carries is still
  right - a well used card holds a hundred cores and opening every one to find
  ours costs seconds over USB - so the sentence needs its number corrected, not
  its argument replaced.
- **`docs/INSTALL.md` describes the core bar without naming the button.** It
  says the second line "names any boot ROM the core needs and your card does
  not have", which is the label; **Boot ROMs...** beside it is what shows the
  whole list, and the page never mentions it. It also never names a file, so
  someone reading only INSTALL has no way to know what to go and find.

### The GBA boot ROM is reported by accident

It does appear, and it appears for the wrong reason.

`wanted()` reads the installed core's `data.json` and keeps the slots that have
both a fixed `filename` and `required`. The GBA core's slot 4 is
`gba_bios.bin`, 16384 bytes, **`required: false`** - where the Game Boy and
Game Boy Color cores both mark theirs `required: true`. So the filter keeps
nothing, `found` is empty, and `found or core.bios` falls through to the table
in `core.py`, which happens to hold the right file. Delete that table entry and
the GBA boot ROM silently stops being reported on a card that has the core
installed.

The core is wrong about itself: `pocket-gba/README.md` says of the same file
that "the core will not start a game without it". Fixing `required` is a change
to that repository and is not this plan's to make. What is this plan's to make
is not depending on the mistake:

**Take the union, not the fallback.** `wanted()` should return the core's
declared fixed-filename slots *plus* the entries in `core.bios`, deduplicated by
filename. That keeps the reason the lookup exists - a core that starts wanting a
new file is reported correctly by an app that predates it - while a core that
mismarks a file we already know about cannot drop it. The cost is that a core
which legitimately stops needing a boot ROM keeps being asked for one until the
table is edited; the table is ours, and that is a one-line edit against a
failure mode that otherwise looks like a working install and then refuses to
start anything.

### The dialog itself

`show_roms()` is `messagebox.showwarning("Boot ROMs", rom_advice(survey))`. A Tk
message box was the right amount of work for one line of text and is the wrong
container for a table. Concretely, what is wrong with it:

- **The columns do not line up.** `rom_advice()` pads with spaces -
  `MISSING  gbc_bios.bin  (GBC BIOS, 2304 bytes)` - and a message box renders in
  a proportional font, so the padding produces ragged text rather than columns.
- **Tk decides where the lines break.** The box wraps to its own width, so a
  long entry breaks at an arbitrary point and the continuation starts hard
  against the left margin, at the same indent as the next entry. Paths break
  mid-path.
- **There are no delimiters.** One blank line separates the prose from the list
  and nothing separates the entries from each other, so four boot ROMs across
  eight lines read as one paragraph.
- **The path cannot be selected.** It is the only text in the box anybody needs
  to act on, and it has to be retyped.
- **It does not look like the rest of the app**, which is the same complaint
  that got the Cores dialog rebuilt.

**Rebuild it as a `Toplevel`, on the CoresDialog pattern.** That dialog is the
model and already solves all five: a `ttk.Treeview`, one row per boot ROM,
columns for the file, its state, the size wanted and the directory it goes in.
Tags for the two failures, the way `dead` and `behind` are used there and the
way the cheat list uses colour for the same three meanings - missing, wrong
size, present. Prose stays prose, in a `ttk.Label` we wrap ourselves at a fixed
width rather than one Tk wraps for us.

Two buttons the message box could not have:

- **Open** - the containing directory, through `reveal.py` from *Opening a
  directory, everywhere*. For a missing file that is `Assets/<platform>/common/`,
  which may not exist yet; the button is offered only when it does, and never
  creates it.
- **Copy path** - for the sandboxed and headless cases where opening a file
  manager is not available, and for anyone who wants the path in a terminal.

`rom_advice()` composes a display string and has no other caller, so it is
replaced rather than reused: the dialog should read `Survey.roms` directly and
render each `RomState` into a row. `describe_roms()` stays as it is - it feeds
the one-line label on the core bar, which is a different job and is not broken.

The automatic popup after an install with problems (`install_core()` calls
`show_roms()` when `found.problems()`) opens the same dialog. That path is why
the dialog exists at all: a core installed onto a card with no boot ROM shows up
in the Pocket's menu and then refuses to start anything, which reads as a broken
install rather than a missing file.

## The core bar line has outgrown one line

Not dump work either, and in here for the same reason as the boot ROM dialog:
it is the same bar, it is the same kind of fault, and the dumping core is what
tips it over.

`core.describe()` builds one string naming every installed core and its version,
then appends whether anything is out of date. Measured against a real card
carrying all four cores:

| State | Length |
|---|---|
| offline, no release data | 94 |
| everything current | 106 |
| three of four behind | 149 |

(That last figure is conservative. It was taken with a synthetic `9.9.9`
upstream tag; with the release versions the four repositories actually carry it
is 157.)

That last one is `Pocket core: kroy.GBC 1.4.0-cheats.10, kroy.GB
1.4.0-cheats.10, kroy.GBA 0.6.4, kroy.PCE 0.2.2  update available: 9.9.9
(kroy.GB, kroy.GBA)`, in a single-line `ttk.Label` in a grid cell, in a window
whose minimum width is 1100 pixels.

**The dumper is the fifth core and it is already on the card.** That same card
carries `kroy.CartTools 0.0.1.41e8d8a`, which is thirty characters with its
separator and pushes the worst case past 190. The line does not wrap; it runs
out of window.

The length is the symptom. The fault is that one label answers two questions
that both grow with the number of cores: *what have I got* and *is any of it
stale*. The first is a table, and the app already has that table - `CoresDialog`
shows a row per core with what the card has beside what is available, which is
strictly better than the same data comma-separated. Only the second question
belongs on a status bar.

So the bar should stop enumerating and say what needs attention:

```
Pocket core: 5 installed, all up to date
Pocket core: 5 installed, 2 updates available
Pocket core: not installed. Nothing written here has any effect until it is.
```

Length no longer grows with the number of cores, the **Cores...** button beside
it already opens the thing that answers "which two", and the version strings -
which are the long part, and are `1.4.0-cheats.10` and `0.0.1.41e8d8a` - move to
the column that exists to be compared against another version.

Two things to keep while changing it:

- **The three states are different and must stay different.** No card, no core
  installed, and cores installed are distinct answers, and "not installed"
  keeps its sentence explaining that nothing written has any effect. That
  sentence is the most important text in the window on a stock card.
- **`bad` still drives the colour.** The label goes red on a problem, and
  whatever replaces the string must return the same flag.

The core ids also stop being shown, which is a small improvement in itself:
`kroy.GBC` is a directory name, and the dialog already gives the cores their
titles.

**Built.** `describe()` counts rather than enumerates, and `ui.py` needed no
change at all — `refresh_core_label()` already took `(text, bad)` and coloured
on `bad`. Measured in the same states:

| State | Was | Now |
|---|---|---|
| four cores, offline | 94 | 24 |
| four cores, current | 106 | 40 |
| four cores, three behind | 157 | 45 |
| five cores, three behind | 187 | 45 |
| no core installed | 76 | 76, sentence intact |

The fifth core now costs one digit where it cost thirty characters, which is the
whole point: the line stopped being a function of how many cores there are.

## The corpus to hold it to

There is a real card, and everything above can be checked against it rather than
argued about. `/Assets/carttools/common/` on it holds seventeen dumps written by
the core on 2026-08-26 and -27, and **all seventeen identify against the
No-Intro DATs by SHA-1, with no near-misses and no ambiguity.** That is the
acceptance test for prongs 1 to 3: same card, same seventeen answers.

| On the card | What the DAT calls it |
|---|---|
| `BOMBERMAN_GB.gb` | Bomberman GB (Japan) (SGB Enhanced) |
| `BOMBER_BOY.gb` | Bomber Boy (Japan) (En) |
| `GBAZELDA_MC.gba` | Legend of Zelda, The - The Minish Cap (USA) |
| `GOLDEN_SUN_A.gba` | Golden Sun (USA, Europe) |
| `MARIOLAND2.gb` | Super Mario Land 2 - 6-tsu no Kinka (Japan) (Rev 2) |
| `MARIO_S_PICROSS.gb` | Mario no Picross (Japan) (SGB Enhanced) |
| `MOGURANYA.gb` | Moguranya (Japan) (SGB Enhanced) |
| `OTHELLO.gb` | Othello (Japan) (En) |
| `SANGOKUSHI.gb` | Sangokushi - Game Boy Ban (Japan) |
| `SUPER_MARIOLAND.gb` | Super Mario Land (World) (Rev 1) |
| `TETRIS.gb` | Tetris (World) (Rev 1) |
| `TETRIS_FLASH.gb` | Tetris Flash (Japan) (SGB Enhanced) |
| `TETRIS_PLUS.gb` | Tetris Plus (Japan) (SGB Enhanced) |
| `UNO2SMALL_WORLD.gb` | Uno 2 - Small World (Japan) (SGB Enhanced) |
| `ZELDA.gb` | Legend of Zelda, The - Link's Awakening (USA, Europe) |
| `ZELDA_DIN__AZ7E.gbc` | Legend of Zelda, The - Oracle of Seasons (USA, Australia) |
| `ZELDA_NAYRUAZ8E.gbc` | Legend of Zelda, The - Oracle of Ages (USA, Australia) |

What it proves, and what it does not:

- **The enrichment argument holds.** `match.best()` is hopeless at `ZELDA.gb`
  and `GBAZELDA_MC.gba` and good at what the right-hand column says. Renaming
  is what makes the existing matcher work; nothing here needs new matching.
- **The fifteen-byte title read is real and visible.** `ZELDA_DIN__AZ7E.gbc`
  carries four bytes of manufacturer code, exactly as described above.
- **`.gitkeep` is in that directory.** The reader must skip what is not a dump
  rather than assume everything in the directory is one.
- **The three DATs are each necessary.** Two of these are only in Game Boy
  Advance, two only in Game Boy Color, thirteen only in Game Boy. Loading one
  DAT identifies a fraction of a real card.
- **It does not prove the collision flow.** These seventeen have seventeen
  distinct names, so the case *When something is already there* exists to handle
  has not happened here. It has to be tested against files made for the purpose.
- **It does not prove the corrupt-dump path.** Every one of these is a clean
  read. The two GB dumps corrupted once by a mechanism that never reproduced are
  not on this card, and **unknown** remains a path that must be exercised
  deliberately rather than waited for.

The DAT measurements in *The DAT* above were re-checked against the same files
on 2026-08-27 and are exact: Game Boy 2001 entries with SHA-1 on all of them and
sha256 on 1920, Parent-Clone 2295 with 876 clone links and no sha256 at all,
Game Boy Color 2038, Game Boy Advance 3533.

**None of this data may be committed.** The corpus lives on the user's card and
the DATs in their downloads; tests that use them skip when they are absent.

## Where it attaches

**One rename first.** `library.py` was the libretro cheat index, and this plan
uses "library" for something else entirely - the directory on the computer where
dumps and saves are filed. Two modules a single underscore apart, describing
unrelated things, is a bug waiting to be written, so the cheat index becomes
`cheatlib.py` and the new module takes `library.py`. The word then means one
thing in the code and the same thing in the interface.

| Module | Change |
|---|---|
| `card.py` | `carttools` into `KNOWN`; into `ENABLED` behind the dumper being public |
| `carts.py` | version field first; `Cartridge` gains optional ROM hash, game code, dump basename |
| `dumps.py` *(new)* | read `/Assets/carttools/common/`, hash, identify, file |
| `library.py` *(new)* | the library path, its layout, and `index.json` |
| `cheatlib.py` | was `library.py`; renamed so the new module could have the word |
| `reveal.py` *(new)* | open a directory in the file manager; used app-wide |
| `nointro.py` *(new)* | parse a DAT of either flavour from its zip, index by SHA-1 |
| `match.py` | unchanged — it gets a canonical name and does what it already does |
| `writer.py` | its atomic write pattern is the model for filing a dump |
| `core.py` | `wanted()` takes the union, not the fallback; `rom_advice()` retires |
| `ui.py` | the approval view, and the boot ROM dialog; the Cores dialog is the model for both |
| `README.md`, `docs/` | the boot ROM table, the core count, the button INSTALL never names |
| `tests/` | `test_dumps.py`, `test_nointro.py`, pinned against real files a core wrote |

## Prongs

Each stands alone and leaves the app working.

0. **Choose a library.** The path, the layout, `index.json` and its rebuild.
   Nothing else can start without somewhere to put things, and getting the
   layout right before there are files in it is the difference between a
   decision and a migration.
1. **Read.** Walk the directory, hash, read headers, list what is there. No
   writes. Answers "what is on this card" before anything moves.
2. **Identify.** DAT loading, the missing-DAT message, SHA-1 lookup, and the
   CRC32 cross-check. Still no writes.
3. **File.** The index, the copy, the move, idempotency. The first prong that
   touches the card, and the one to hold to real files.
4. **Cheats.** Auto-mapping through the existing matcher, clone-parent fallback.
5. **Approve.** The one-at-a-time view. Last, because until the first four are
   right there is nothing worth approving.

Two more that are not part of that sequence and do not wait on it:

A. **The docs.** The boot ROM table, the core count that contradicts itself two
   paragraphs later, and the button INSTALL describes without naming. No code.
B. **The boot ROM dialog.** The `wanted()` union first, because it is what makes
   the dialog's GBA row load-bearing rather than lucky, then the `Toplevel`.
   Its **Open** button wants `reveal.py`, so it lands after that module exists
   or grows the button once it does.

## Gated, and out of scope

**Saves and RTC are not built in the core.** Save backup, save restore, GBA
SRAM/Flash/EEPROM and RTC are all "not started" upstream. No `.sav` has ever
been written by the dumper. Nothing here should ship UI for them, and platform
gating follows `card.py`'s `KNOWN`/`ENABLED` pattern driven by what upstream has
verified, not by what code paths exist.

**Sidecars.** When `.cart.json` starts being written, it is corroboration, never
a requirement. A dump must remain usable with its metadata deleted.

This is not a ROM manager, not a launcher, and not a second Pocket Sync.

## Open questions

None outstanding for the work this plan covers.

The save layout inside `saves/` is deferred rather than open: see *The library*.
It is decided when the dumper can produce a save and there is a real file to
decide against.
