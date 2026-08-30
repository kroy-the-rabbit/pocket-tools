#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Launch the cheat GUI from its own venv, creating it on first run.
# Everything used is in the Python standard library, so the venv stays empty;
# it exists so nothing is ever installed into the host Python.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$HERE/.venv"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "creating venv at $VENV"
  python3 -m venv "$VENV"
fi
if ! "$VENV/bin/python" -c "import tkinter" 2>/dev/null; then
  echo "tkinter is not available to $VENV/bin/python." >&2
  echo "On Fedora: sudo dnf install python3-tkinter, then delete $VENV and retry." >&2
  exit 1
fi
exec "$VENV/bin/python" "$HERE" "$@"
