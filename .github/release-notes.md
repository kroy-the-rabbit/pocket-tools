## Master System, SG-1000 and Game Gear dumps

Pocket Tools now recognizes **Game Gear, Master System and SG-1000** as
separate platforms. **Cores...** offers each Sega package only when the core
release contains that platform's ZIP. Installation preserves existing ROMs,
BIOS files and saves.

- **Master System:** scan `.sms` ROMs, find cheats in the Master System
  database, decode them and write named `.cht` files beside the ROM.
- **SG-1000:** scan `.sg` ROMs and use installed or local `.cht` files through
  **Change source... > Browse .cht...**. There is no downloadable SG-1000
  cheat database, and SG-1000 cheats have not been verified on hardware.
- **Sega cheats:** Game Genie ROM patches and Pro Action Replay RAM writes
  share the core's 32-code limit. Game Gear and Master System use separate
  database directories and the same decoder as the core.
- **Game Gear dumps:** recognize and import dumps produced by an installed
  CartTools core, identify them with the Sega - Game Gear No-Intro DAT, and
  file them in the library.

Use `Assets/gg/common/` for `.gg`, `Assets/sms/common/` for `.sms` and
`Assets/sg1000/common/` for `.sg`. None of the Sega packages needs a BIOS.
Use **Update** if an older cheat database is missing Master System.

The desktop cartridge list still covers GB/GBC. GBA and supported Game Gear
cartridges use a cheat file selected through the core's own menu. Game Gear
cartridge saves are not working; see each core's notes for tested cartridges
and save behavior.

Available builds: Linux x86_64, Windows x64 and macOS arm64. The macOS app is
not notarized; see [installation instructions](https://github.com/kroy-the-rabbit/pocket-tools/blob/main/docs/INSTALL.md).
The cheat database is fetched at runtime and is not bundled.

### Verify the download

Artifacts use the normal Kroy key, fingerprint
`7268DF1E6F75DA7731A46B65888C35858FEACF72`. Import `KEYS` from this
repository, then verify the signed checksum manifest:

```sh
gpg --import KEYS
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum -c SHA256SUMS
```

Cheats can corrupt saves. Back up saves you care about before using them.
