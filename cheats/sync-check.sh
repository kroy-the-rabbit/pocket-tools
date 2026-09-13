#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Some files here are copies. The originals live in the core repos, where each
# is the reference model its RTL is verified against over the whole libretro
# directory for that system; this app is only a consumer of them. If a copy
# drifts, the picker will show something the core will not do.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPOS="$(cd "$HERE/../.." && pwd)"

# file:repo. One entry per core that owns a parser, because each core is a
# separate fork of a separate upstream and none is a dependency of this app.
COPIES=(
  "chtparse.py:pocket-gbc"
  "ggdecode.py:pocket-gbc"
  "gbacht.py:pocket-gba"
  "cht2bin.py:pocket-gba"
  "ggcht.py:pocket-gg"
  "gg2bin.py:pocket-gg"
)

rc=0
for entry in "${COPIES[@]}"; do
  f="${entry%%:*}"
  name="${entry##*:}"
  # POCKET_GBC_REPO, POCKET_GBA_REPO and POCKET_GG_REPO override the locations.
  var="POCKET_$(echo "${name#pocket-}" | tr '[:lower:]' '[:upper:]')_REPO"
  core="${!var:-$REPOS/$name}"

  if [[ ! -d "$core/tools/cheats" ]]; then
    echo "  skipped: $f  ($name not found at $core, set $var)"
    continue
  fi
  if cmp -s "$HERE/$f" "$core/tools/cheats/$f"; then
    echo "  in step: $f  <- $name"
  else
    echo "  DRIFTED: $f  <- $name  (the core copy is authoritative)"
    echo "           diff $HERE/$f $core/tools/cheats/$f"
    rc=1
  fi
done
exit $rc
