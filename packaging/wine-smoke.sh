#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Start the Windows build under Wine on a virtual display, give it time to draw,
# and report whether it is still alive with a window. Screenshots land in /out.
#
#   wine-smoke <exe> [seconds]
set -uo pipefail
EXE="${1:?usage: wine-smoke <exe> [seconds]}"
WAIT="${2:-25}"
OUT=/out

# A card mounted at /card is exposed as a Windows drive letter, which is the
# one piece of Windows-only code in this app that could simply be wrong:
# card.py enumerates A: to Z: and asks GetVolumeInformationW for each label.
# Nothing else exercises that, and it cannot be exercised on Linux at all.
if [ -d /card ]; then
  ln -sfn /card "$WINEPREFIX/dosdevices/e:"
  echo "card:  /card mounted as E:"
fi

# Belt and braces: a lock left by an earlier run in this container, or baked
# into the image, silently stops Xvfb and leaves wine with no display.
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
Xvfb :99 -screen 0 1600x900x24 >/tmp/xvfb.log 2>&1 &
sleep 3
export DISPLAY=:99

if ! pgrep -x Xvfb >/dev/null; then
  echo "Xvfb did not start, so nothing below would mean anything:" >&2
  cat /tmp/xvfb.log >&2
  exit 2
fi

echo "wine:  $(wine --version)"
echo "exe:   $EXE"
echo "--- starting ---"
wine "$EXE" >/tmp/wine.log 2>&1 &
WINEPID=$!

for i in $(seq 1 "$WAIT"); do
  sleep 1
  if ! kill -0 "$WINEPID" 2>/dev/null; then
    echo "the launcher exited after ${i}s"
    break
  fi
done

# Wine's own process tree is what matters: the launcher can return while the
# app keeps running under wineserver.
echo "--- processes ---"
ps -eo comm | grep -iE "pocket|wine" | sort | uniq -c | sed 's/^/  /'

RUNNING=no
DREW=no
# Match on the exe actually given, not on a name baked in here: the Makefile
# mounts it as app.exe, and a hardcoded "pocket-cheats" reported a live app as
# dead. Wine also truncates the process name, so compare on a prefix.
BASE="$(basename "$EXE")"; BASE="${BASE%.exe}"
if pgrep -f "$(printf '%s' "$BASE" | cut -c1-12)" >/dev/null 2>&1; then
  RUNNING=yes
fi

mkdir -p "$OUT"
echo "--- screenshot ---"
if import -window root -display :99 "$OUT/wine-screenshot.png"; then
  identify "$OUT/wine-screenshot.png" | sed 's/^/  /'
  # A window that drew is not a uniform field of the root colour. One colour
  # means the process is up but nothing was ever painted, which is a failure
  # that looks exactly like success from the process table alone.
  COLOURS=$(magick "$OUT/wine-screenshot.png" -format %k info: 2>/dev/null \
            || convert "$OUT/wine-screenshot.png" -format %k info:)
  echo "  distinct colours: $COLOURS"
  if [ "${COLOURS:-1}" -ge 8 ]; then
    DREW=yes
  else
    echo "  nothing was drawn: the process is up but the window is blank"
  fi
else
  echo "  could not capture the display"
fi

if [ -d /card ]; then
  echo "--- did it find the card? ---"
  # The label is drawn in red when no card was found and green when one was,
  # so the pixels say which happened without reading any text.
  if magick "$OUT/wine-screenshot.png" -crop 700x24+0+38 +repage \
       -format "%[fx:mean.r>mean.g?1:0]" info: 2>/dev/null | grep -q 0; then
    echo "  the card line is not the no-card red"
  else
    echo "  the card line looks like the no-card red"
  fi
fi

echo "--- wine output ---"
sed 's/^/  /' /tmp/wine.log | head -40
echo "--- verdict ---"
echo "app process alive: $RUNNING"
echo "window drew:       $DREW"
[ "$RUNNING" = "yes" ] && [ "$DREW" = "yes" ]
