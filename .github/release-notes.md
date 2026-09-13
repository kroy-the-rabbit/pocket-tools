Pocket Tools now reads, selects and writes Game Gear cheats, including
Game Genie ROM patches and Pro Action Replay RAM writes. The Cores window
installs Game Gear from `kroy-the-rabbit/openfpga-GG-cheats` and offers
Game.com as an optional core, with its external BIOS requirement shown.

GBA support matches the cartridge core release: CodeBreaker ROM writes are
recognized as patches, IWRAM address decoding is corrected, and the picker
writes both `.cht` and `.chtbin`. Text files supply names to the new overlay;
binary files preserve compatibility with the older `v0.9999` core. The app's
GBA game picker still manages SD ROMs; cartridge cheats are selected through
the core's menu.

Automatic update suggestions now preserve core versions that are known to
be unpublished local builds. Users can still select a release explicitly
through Cores.

Validation: 569 tests passed on a virtual display, with three optional
external-corpus tests skipped. All six shared parser/converter files match
their core repositories. The Linux PyInstaller build passed locally.
