# PC Engine / TurboGrafx-16: what to expect from the core

**Status: the app side is built and shipped. The core is still in progress, so
nothing written for PC Engine is read by anything yet.** Core work lives in
`~/Desktop/repos/pocket-pcengine`, forked from `vanfanel/openfpga-pcengine`,
with its own plan in that repo's `docs/PLAN.md`.

**Corrections since this was first written.** The corpus was re-counted in full
on 2026-08-25, all 397 files rather than a directory listing plus a dozen read
closely, and three claims below were wrong in ways that would have shaped the
core's parser badly:

| Claimed | Actual |
|---|---|
| 350 form A, 47 form B | **246 form A, 151 form B** |
| form B is "all named (Rumbles)" | **104 of the 151 carry ordinary game names.** The suffix is not a filter |
| every sampled address is inside work RAM | **16 are not**, see §3 |

The sections below are corrected. `cheatgui/pce.py` is the implementation and
`tests/test_pce.py` pins every case in it against a real row from the corpus.

## The short version, and the one surprise

PC Engine is the third system this app will write for and the least like the
first two. **Every published PC Engine cheat is a RAM poke. None is a ROM
patch.** There is no Game Genie for this machine in the libretro database.

That inverts the mental model the Game Boy work built. On GB/GBC the read
override is the primary mechanism and the poker is the addition; here the
override is nearly dead weight and the poker *is* the feature. Wherever the app
distinguishes the two, PC Engine has only one value.

Evidence: all 397 files were parsed, every one of the 1224 codes in them read.
No code anywhere lands in ROM space, so the claim above holds. What the first
pass got wrong was the addresses: 1208 sit inside `0x1F0000`-`0x1F1FFF`, the 8KB
work RAM at bank `$F8`, 13 sit just past it in the range a SuperGrafx has, and 3
are not addresses this machine has at all. §3 has each case.

---

## 1. Platform contract (`card.py`)

**Done, with one change.** The platforms are now a `KNOWN` table plus an
`ENABLED` tuple, because Game Boy Advance was switched off at the same time:

```python
KNOWN = {
    "gb":  ("Nintendo - Game Boy", ".gb"),
    "gbc": ("Nintendo - Game Boy Color", ".gbc"),
    "gba": ("Nintendo - Game Boy Advance", ".gba"),
    "pce": ("NEC - PC Engine - TurboGrafx 16", ".pce"),   # libretro's own name
}
ENABLED = ("gb", "gbc", "pce")

SUPPORTED = {p: KNOWN[p][0] for p in ENABLED}
ROM_EXT = {KNOWN[p][1] for p in ENABLED}
```

`db.DIRS` is derived from the same tuple, so a system cannot be fetched and not
read, or read and not fetched.

`Game.cht_path` needs no change: the cheat slot's filename is cloned from slot 0
with the extension appended, so it is `<rom>.pce.cht` beside the ROM, exactly
the convention the Game Boy cores use.

**Three things deliberately absent:**

* **`.sgx` is not in `ROM_EXT`.** The core drops SuperGrafx entirely to buy the
  ALM headroom the cheat engine needs. A `.sgx` file on the card will not run
  correctly, so offering to write cheats for one would be a lie.
* **`NEC - PC Engine SuperGrafx`** is a real libretro directory. Do not map it.
* **`NEC - PC Engine CD - TurboGrafx-CD`** is mapped, as the system `pcecd`.
  The PCE core's `v0.9999.d5d93c8` reads discs from cue plus bin, slot 100
  `Disc (cue)` and 101 `Disc data`, and cheats were verified on a disc on
  hardware. One disc has been tested, Rondo; the core's release notes say so.

## PC Engine CD, how it is wired

A disc and a HuCard share the Pocket's `pce` platform and its `Assets/pce`
folder. They are two systems here because they are two cheat corpora, and a
HuCard cheat matched to a disc would be a wrong match, not a near miss.

| | |
|---|---|
| system id | `pcecd`, this app's, not the Pocket's. `card.FOLDER` maps it to `Assets/pce` |
| the game | the `.cue`. `card.SYSTEM_OF_EXT` files `.cue` under `pcecd` and `.pce` under `pce` from the one walk; the `.bin` is disc data and is listed under neither |
| cheat file | `<name>.cue.cht`, the one rule every system here follows. The core's cheat slot is picked by hand, so the name is a convention, and one convention is kept rather than a second invented |
| parser | `pce.py`, unchanged. All 136 files in the directory parse, 667 codes, every one inside the 8KB work RAM `in_work_ram()` checks |
| search | its own directory only, `cheatlib.SEARCH["pcecd"]` |
| display name | `card.DISPLAY`, not `/Platforms/pce.json`, which names the HuCard |
| save | the core writes `Saves/pce/common/<cue name>.sav`; `card.save_path` derives the same from the cue path |
| System Card | slot 0 carries `bios_3_0_usa.pce` with four `alternate_filenames`. `core.wanted()` reads both, and `boot_roms()` accepts any of them |

**Not settled, and not claimed:** whether `(SCD)` and `(ACD)` titles in the
directory run on the core is a core question. The files are offered because
the directory is; the core's own notes say what it plays.

## 2. Database contract (`db.py`)

**Done.** `DIRS` is derived from `card.ENABLED`. The PC Engine directory holds
**397 files**; with Game Boy Advance's 513 removed at the same time, a fetch is
now 2853 files rather than 2969.

The `(Rumbles)` suffix is not what separates the two forms. 47 files carry it
and 151 are form B, so the suffix is a naming habit rather than a format.

The incidental break was real and is fixed: the missing-directory report trimmed
names with `d.replace("Nintendo - ", "")`, which left
`NEC - PC Engine - TurboGrafx 16` whole and ran the line off the end of the bar.
`db.short_name` now drops any `"<maker> - "` prefix.

## 3. Code format (`cheatfile.py`)

Two forms live in the same directory, and a file uses one or the other.

**Form A, 246 files.** The Beetle PCE style, a hex CPU address and a hex byte:

```
cheat0_desc = "Infinite Energy"
cheat0_code = "1f1548:64"
cheat0_enable = false
```

`1f1548` is a full 21-bit CPU address. `64` is the byte to write.

**Form B, 151 files, of which 104 carry ordinary game names.** RetroArch's
native cheat-search form. `cheat0_code` is present and **empty**, which matters:
a parser that keys on the presence of `_code` finds it and then finds nothing
in it.

```
cheat0_address = "1412"
cheat0_value = "1"
cheat0_cheat_type = "1"
cheat0_memory_search_size = "3"
cheat0_big_endian = "false"
cheat0_handler = "1"
cheat0_rumble_type = "0"          # ... and eight more rumble_* keys
```

Here `cheat0_address` is a **decimal offset into work RAM**, not a CPU address,
and `memory_search_size = 3` means one byte. The conversion is:

    cpu_address = 0x1F0000 + int(cheat0_address)

Checked: `1412` decimal is `0x584`, giving `0x1F0584`, inside work RAM. The
`rumble_*` keys are meaningless to a Pocket and should be dropped, not carried.

**Done: `DECODED` has `"pce"`,** and the decoder is `cheatgui/pce.py`. Carrying
verbatim was considered and is not viable, because form B has no code text to
carry: `parse_opaque` keys on a non-empty `_code` and would show 151 files as
empty.

Four kinds of row cannot become a poke and are listed with their description
and no codes, which is the existing `placeholder` treatment: greyed and not
pickable.

| Rows | Why |
|---:|---|
| 70 | `cheat_type = 0`, RetroArch's "disabled". Watches an address to fire a rumble, writes nothing. Converting one would invent a cheat: "Rumble on gold change" value 5 would pin gold to 5 |
| 2 | `memory_search_size = 0`, bit-level, both in Wonder Momo |
| 1 | no `_value` key at all, also Wonder Momo |
| 3 | an address the machine does not have: Bomberman 94's `18446744073709546426` is -5190 as unsigned 64-bit, and both Magical Chase files carry `1f0000f:0c`, seven hex digits past the HuC6280's 21 address lines |

Two more findings the core's parser will want:

* **The repeat family is real.** `repeat_count` with `repeat_add_to_address`
  appears on twelve rows. Eleven have a count of 1 and mean nothing by it. One
  does not: Wonder Momo's "One hit kills bosses" is count 2 stepping by 32, and
  reading only the first half half-applies it.
* **13 codes sit between 0x1F2000 and 0x1F2656.** That is inside the 32KB a
  SuperGrafx has and outside the 8KB this core keeps, so they are addressable
  and unreachable at once. They are carried rather than dropped;
  `pce.in_work_ram` is how to ask. **Open question for the core:** should the
  poker cover 8KB and silently ignore these, or should the app refuse to write
  them?

One tolerance was needed. Veigues writes `cheat1_value = ""255"`, an unbalanced
quote, and it is the second half of a two part cheat whose first half parses.
Numeric fields are stripped of stray quotes rather than losing half a cheat.

**`LIMITS` stays absent.** The Game Boy figures come from `cheatcodes.sv`. The
PC Engine ceiling depends on a poker table that has not been written, and
putting a number on screen that nothing checks is the thing that module
explicitly refuses to do. The meter counts rather than measures.

## 4. The consequence for the UI

The **Applied** column exists to separate Game Genie codes from GameShark ones,
because on a cartridge that distinction is the difference between safe and
dangerous. On PC Engine every code is the dangerous kind, and there is no
cartridge to make it worse.

**Done: the column collapses.** `cheatfile.MECHANISMS` says how many ways a
system's core has, `ui.retune_applied` acts on it, and the single fact is
stated in the note under the list instead. The save-corruption warning in the
README still applies in full and arguably more so, since a RAM poke is
precisely the failure mode it describes.

The status line also says "the PC Engine core is not released yet" on every
game, from `core.released()`. Everything else on screen looks exactly like a
system that works, which is the problem that line exists to solve.

## 5. Core install contract (`core.py`)

**Done, both changes.**

```python
Core("kroy.PCE", "pce", "PC Engine", "kroy.PCE_", None, ())
```

* **`REPO` moved onto `Core`**, and `latest()` is per repository.
  `all_latest()` returns one release per repository and a repository with no
  release answers 404, which is treated as an answer rather than a failure, so
  being offline still reads as being offline.
* **`repo=None` means there is nothing to install.** The core is listed so a
  hand-built copy on a card is reported with its version, and `outdated()`
  never returns it, so no button offers to fetch something that does not exist.
  Fill the repository in when the first tag lands.
* **An empty `bios` is a supported state.** `describe_roms` says nothing at all
  rather than "0 present", which read like a fault.

**Directory naming.** Upstream ships as `agg23.PC Engine`, with a space in the
directory name. The fork renames to `kroy.PCE`, both to match `kroy.GBC` and so
the app never handles the space. A card may still carry the upstream core
alongside the fork; `installed()` looks for known ids, so it will simply not see
it, which is the right behaviour.

## 6. What the core will and will not do

| | |
|---|---|
| ROM source | **SD card only** |
| Cartridge | **never.** `cartridge_adapter` stays `-1` |
| SuperGrafx | removed |
| CD | not supported upstream either |
| Cheat file | `<rom>.pce.cht` beside the ROM |
| Mechanism | RAM poke into 8KB work RAM at `0x1F0000` |
| On-screen cheat list | planned, but explicitly optional |

The cartridge point matters more than it looks. Analogue does ship a
TurboGrafx-16 adapter and openFPGA cores genuinely can read physical carts, as
this app's own Game Boy support proves. The PC Engine core still will not: the
adapter's signalling is undocumented and a HuCard needs more lines than the
Game Boy scheme spends. So for `pce`, every cartridge path in this app is dead
code, not merely unused. The red status line, the revision warnings and the
bit-9 browsed-filename fallback should be **skipped for this platform**, not
left to evaluate to nothing.

## 7. Open items

1. Are there PC Engine cheats in the wild, outside the libretro database, that
   patch ROM rather than RAM? If not, the core's existing Game Genie block
   could be dropped for the ALMs, and this app never needs the distinction.
2. What poker table size will the core carry? That fixes `LIMITS`.
3. Do any form B files disagree with their form A twin for the same game? The
   47 `(Rumbles)` files look like derived duplicates; if they are, the app
   should prefer one and not show both.
4. Does the Pocket's own PC Engine platform JSON name the system "PC Engine" or
   "TurboGrafx-16"? `DISPLAY` is only the pre-card guess, but it should match
   what the card says for the common case. Upstream's `pkg/Platforms/pce.json`
   says `"PC Engine"`.
