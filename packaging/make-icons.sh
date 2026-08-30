#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Render assets/icon.svg into the raster forms each platform insists on.
# Committed, because CI has no renderer and a build should not need one.
#
#   assets/icon.png       512, the Tk window icon and anything web
#   assets/icon-64.png    64, the small Tk icon and a favicon fallback
#   assets/favicon.ico    16/32/48
#   assets/icon.ico       Windows executable icon
#   assets/icon.iconset/  the PNGs macOS's iconutil turns into an .icns
#   assets/icon.icns      macOS bundle icon, only where iconutil exists
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SVG="$HERE/assets/icon.svg"
OUT="$HERE/assets"

render() {  # render <size> <path>
  if command -v rsvg-convert >/dev/null; then
    rsvg-convert -w "$1" -h "$1" "$SVG" -o "$2"
  elif command -v magick >/dev/null; then
    magick -background none -density 384 "$SVG" -resize "${1}x${1}" "$2"
  else
    convert -background none -density 384 "$SVG" -resize "${1}x${1}" "$2"
  fi
}

render 512 "$OUT/icon.png"
render 64  "$OUT/icon-64.png"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
for s in 16 32 48 128 256; do render "$s" "$tmp/$s.png"; done
if command -v magick >/dev/null; then
  magick "$tmp/16.png" "$tmp/32.png" "$tmp/48.png" "$OUT/favicon.ico"
  magick "$tmp/16.png" "$tmp/32.png" "$tmp/48.png" "$tmp/128.png" "$tmp/256.png" \
         "$OUT/icon.ico"
else
  convert "$tmp/16.png" "$tmp/32.png" "$tmp/48.png" "$OUT/favicon.ico"
  convert "$tmp/16.png" "$tmp/32.png" "$tmp/48.png" "$tmp/128.png" "$tmp/256.png" \
          "$OUT/icon.ico"
fi

# macOS wants an .icns and only macOS ships the tool that makes one, so the
# PNGs it needs are rendered and committed here. The mac build runs iconutil
# over them; it needs no renderer of its own, which the runners do not have.
# ImageMagick will happily write a file named .icns that is a PNG inside, so
# it is not used for this.
set="$OUT/icon.iconset"; mkdir -p "$set"
for s in 16 32 128 256 512; do
  render "$s" "$set/icon_${s}x${s}.png"
  render $((s * 2)) "$set/icon_${s}x${s}@2x.png"
done

if command -v iconutil >/dev/null; then
  iconutil -c icns "$set" -o "$OUT/icon.icns"
else
  echo "iconutil not here (macOS only); the iconset is committed for it"
fi

ls -l "$OUT"
