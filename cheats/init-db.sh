#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Check out just the Game Boy, Game Boy Color and Game Boy Advance cheat files
# from the libretro-database submodule. The full repo is ~830 MB checked out;
# the three directories we care about are ~15 MB.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
SUB=external/libretro-database

cd "$REPO"
git submodule update --init --depth 1 "$SUB"
git -C "$SUB" sparse-checkout set --no-cone \
    "cht/Nintendo - Game Boy/" "cht/Nintendo - Game Boy Color/" \
    "cht/Nintendo - Game Boy Advance/"
echo "$(find "$SUB/cht" -name '*.cht' | wc -l) cheat files in $SUB/cht"
