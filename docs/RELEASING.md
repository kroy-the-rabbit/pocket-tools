# Cutting a release

Tag it and the workflow does the rest: it builds the picker for Linux, macOS
and Windows, signs the result, and attaches everything to the GitHub release.

```sh
git tag -s v1.0.0 -m "v1.0.0"
git push origin v1.0.0
```

The tag drives the version. `.github/workflows/release.yml` strips the leading
`v` and rewrites `VERSION` in `cheatgui/version.py` before building, so a
downloaded binary names the tag it came from and a run out of a checkout says
`0.0.0-dev` instead.

## What comes out

| File | What it is |
|---|---|
| `pocket-tools-<version>-linux-x86_64` | one binary, run it |
| `pocket-tools-<version>-macos-arm64.zip` | `Pocket Tools.app`, Apple Silicon |
| `pocket-tools-<version>-windows-x64.exe` | one binary, run it |
| `SHA256SUMS` | checksums of all of the above |
| `SHA256SUMS.asc` | detached signature over `SHA256SUMS` |
| `<file>.sig` | detached signature per artifact |

Apple Silicon only on macOS. GitHub retired the Intel runners, and an Intel
build is not worth keeping a second macOS job alive for; an Intel Mac runs it
from a checkout, which [INSTALL.md](INSTALL.md) covers.

Linux is the artifact that gets used. Windows is smoke tested under Wine by
`make wine-test`, which starts the exe on a virtual display, checks that it
drew something rather than merely staying alive, and exposes a fixture card as
a drive letter so the Windows-only drive enumeration is exercised. macOS is
built, signed and shape checked and nothing more.

Both [INSTALL.md](INSTALL.md) and the README say exactly that, and should keep
saying it until somebody has launched them on the real thing.

Nothing is bundled but Python and Tk. The cheat database is fetched by the app
on first run, so no third-party cheat content ships in a release and the
artifacts stay under 20 MB.

## Signing

Two different things are called signing here and only one of them is set up.

**GPG, over the artifacts.** This is the signature this project offers, on
every platform, and it is what [INSTALL.md](INSTALL.md) tells people to check.
The public key is `KEYS` at the root of this repository.

**Apple notarization, for macOS.** Not set up. See below.

### First time: make the key

```sh
packaging/make-release-key.sh
```

It creates a signing key for this project alone, writes the public half to
`KEYS` for committing, and leaves the private half in `.release-key/` with the
three `gh secret set` commands to run. Back that directory up offline and
delete it afterwards.

A key of its own rather than the one that signs your commits, because this half
lives in a GitHub secret. A compromise of the repository or of a workflow reaches
whatever that secret holds, and the cost of revoking a key that only ever signed
releases is one release.

### Secrets

| Secret | Value |
|---|---|
| `GPG_PRIVATE_KEY` | armored private key, base64 with no line wrapping |
| `GPG_KEY_ID` | the fingerprint |
| `GPG_PASSPHRASE` | the passphrase, if the key has one |

Absent, signing is skipped rather than failed: the release still carries
`SHA256SUMS`, the workflow logs a warning, and a fork can cut a build without
holding your key. Check for that warning on any release you meant to sign.

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
