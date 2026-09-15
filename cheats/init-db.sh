#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Check out the same cheat directories the app fetches through db.DIRS.
# SG-1000 accepts local cheats but has no libretro directory.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
SUB=external/libretro-database

cd "$REPO"
git submodule update --init --depth 1 "$SUB"
git -C "$SUB" sparse-checkout set --no-cone \
    "cht/Nintendo - Game Boy/" "cht/Nintendo - Game Boy Color/" \
    "cht/Nintendo - Game Boy Advance/" \
    "cht/NEC - PC Engine - TurboGrafx 16/" \
    "cht/NEC - PC Engine CD - TurboGrafx-CD/" \
    "cht/Sega - Game Gear/" "cht/Sega - Master System - Mark III/"
echo "$(find "$SUB/cht" -name '*.cht' | wc -l) cheat files in $SUB/cht"
