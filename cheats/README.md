# Shared with the cores

Four files here are **copies, kept byte-identical**, from two core repos:

| Copy | Original | What it is there |
|---|---|---|
| `chtparse.py` | `pocket-gbc` | the reference model the RTL cheat parser is checked against, over all 2456 Game Boy and Game Boy Color files in the libretro database |
| `ggdecode.py` | `pocket-gbc` | Game Genie and GameShark decoding for the same |
| `gbacht.py` | `pocket-gba` | the same job for Game Boy Advance, over all 513 files |
| `cht2bin.py` | `pocket-gba` | packs decoded entries into the `.chtbin` that core reads |

So: fix a parser bug in the core repo, run its test suite, then copy the file
here. `make sync-check` compares them byte for byte and says which have
drifted. It looks for the repos at `../pocket-gbc` and `../pocket-gba`;
`POCKET_GBC_REPO` and `POCKET_GBA_REPO` point it elsewhere.

They are copied rather than imported because each core repo is a fork of
someone else's work and may be PR'd upstream, so this app cannot be a
dependency of either, and a checkout of one should not require the others.

Drift matters in one specific way, and it is worse for Game Boy Advance than
for the others. Everywhere else the picker decides what to show, and what each
cheat will do, by parsing files the same way the core does; if the two disagree
the app confidently shows something the hardware will not do. On Game Boy
Advance the app does not merely model the core's parser, it *replaces* it -
the core reads packed entries because it has no room to parse text - so a bug
in `gbacht.py` here is not a display bug, it is the cheat.

The rest of this directory is host tooling that belongs with the app:

| | |
|---|---|
| `cht` | front end for your own cheat files (`list`, `new`, `add`, `check`) |
| `checkrom.py` | verify Game Genie compare bytes against a real ROM |
| `install.py` | the original command line installer, before the GUI existed |
| `init-db.sh` | fetch the libretro cheat database |
