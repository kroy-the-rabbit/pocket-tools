# Cutting a release

Signed tags on `main` build Linux x86_64, Windows x64 and macOS arm64
artifacts in CI. The workflow uploads them to a **draft** release. Download,
verify and sign the artifacts locally before publishing the draft.

Use `v0.9999.YYYYMMDD` with the UTC publication date. Never reuse a published
date for different binaries. The workflow stamps the tag's version into the
app; a checkout keeps `0.0.0-dev`.

## Signing

Commits, tags and release artifacts use the maintainer's normal key:
`7268DF1E6F75DA7731A46B65888C35858FEACF72` (`Kroy <kroy@kroy.io>`).
Its public key is in `KEYS`. The previous Pocket Cheats Release Signing key
remains in that file to verify older releases. The personal private key is
not uploaded to or imported by CI.

1. Run the checks below, review `.github/release-notes.md`, commit and merge
   onto `main`. Push main and the signed dated tag.
2. Wait for all three CI builds and the draft upload to finish. Download the
   draft's assets and verify the CI `SHA256SUMS` before signing.
3. Record the source commit and CI run in `BUILD.json`. Export the normal
   public key as `RELEASE-KEY.asc`. Generate `SHA256SUMS` over the binaries,
   provenance and public key; sign that manifest as `SHA256SUMS.asc` and each
   artifact as `<filename>.sig` with the normal key.
4. Verify every signature and checksum, upload the final files to the draft,
   and publish it. Download again and verify the published files.

## Release files

| File | Contents |
|---|---|
| `pocket-tools-<version>-linux-x86_64` | Standalone Linux binary |
| `pocket-tools-<version>-windows-x64.exe` | Standalone Windows binary |
| `pocket-tools-<version>-macos-arm64.zip` | Pocket Tools.app, Apple Silicon |
| `BUILD.json` | Source and CI build provenance |
| `RELEASE-KEY.asc` | Normal signing public key |
| `SHA256SUMS`, `SHA256SUMS.asc` | Checksums and detached signature |
| `<filename>.sig` | Detached signature for each artifact |

The cheat database is fetched at runtime, not bundled. Windows can be smoke
tested under Wine with `make wine-test`; this does not qualify native Windows
behavior. macOS is built and package-checked; native testing is separate.

## macOS is not signed

The macOS builds carry no Apple Developer ID and are not notarized. They are
ad-hoc signed, which is the difference between "Gatekeeper warns" and "the
process is killed on launch" on Apple Silicon; it is not a statement about
who built them. Users have to clear the quarantine attribute by hand, and
[INSTALL.md](INSTALL.md) says how.

Fixing it needs a paid Apple Developer account. The workflow already has the
signing and notarization steps and turns them on the moment all six secrets
exist, so nothing here changes when they do:

| Secret | Value |
|---|---|
| `APPLE_CERTIFICATE_P12_BASE64` | Developer ID Application certificate, base64 |
| `APPLE_CERTIFICATE_PASSWORD` | its password |
| `APPLE_TEAM_ID` | the team id |
| `APPLE_API_KEY_P8_BASE64` | App Store Connect API key, base64 |
| `APPLE_API_KEY_ID` | its key id |
| `APPLE_API_ISSUER_ID` | its issuer id |

Windows binaries are unsigned in the Authenticode sense as well, and
SmartScreen will say so on a new release until it has seen enough downloads.
That needs a separate code signing certificate and is not set up either. The
GPG signature is what verifies these builds on every platform.

## Before tagging

```sh
make test          # the parser self-test and the GUI tests
make dist          # the build the workflow will do, locally
make sync-check    # the shared parser is still in step with the core
```

`sync-check` matters. `cheats/chtparse.py` is a copy of the core's reference
model, and if the two drift the picker shows something the hardware will not
do. See [../cheats/README.md](../cheats/README.md).
