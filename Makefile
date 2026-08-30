# SPDX-License-Identifier: GPL-3.0-or-later
# The venv, not the host interpreter. run.sh creates it and it stays empty,
# because everything this app uses is in the standard library; the point is
# that nothing is ever installed into the system Python. Override with
# PY=... only when you know why.
VENV = cheatgui/.venv/bin/python
PY  ?= $(if $(wildcard $(VENV)),$(VENV),python3)

.PHONY: gui list cheatdb sync-check test dist clean help

help:                     ## this list
	@grep -hE '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

gui:                      ## the picker
	cheatgui/run.sh

list:                     ## same data, printed, no window
	cheatgui/run.sh --list $(ARGS)

cheatdb:                  ## the cheat database as a git submodule
	cheats/init-db.sh

sync-check:               ## are the shared parsers still in step with the core?
	@cheats/sync-check.sh

# The GUI tests drive real widgets, so they need a display. Under a headless
# session, run them as: xvfb-run -a make test
test: sync-check          ## parser self-test and the GUI tests
	$(PY) -m compileall -q cheatgui cheats tests
	$(PY) cheats/ggdecode.py --test
	$(PY) -W ignore::ResourceWarning -m unittest discover -s tests -v

# PyInstaller is the one thing the app itself does not need, so it goes in a
# venv of its own rather than into the system Python, which on most
# distributions now refuses to be written to at all.
BUILDVENV = build/venv

dist: $(BUILDVENV)/bin/pyinstaller   ## build the binary for this platform
	$(BUILDVENV)/bin/pyinstaller --clean --noconfirm \
		--distpath dist --workpath build/pyi packaging/pocket-tools.spec
	@ls -l dist/

# certifi is a build-time dependency, not a run-time one: it supplies the CA
# bundle a frozen binary carries because it cannot trust the build machine's
# OpenSSL paths to exist elsewhere. A checkout uses the system store.
$(BUILDVENV)/bin/pyinstaller:
	$(PY) -m venv $(BUILDVENV)
	$(BUILDVENV)/bin/pip install --quiet --upgrade pip pyinstaller certifi

WINEIMAGE ?= localhost/pocket-wine:1
EXE ?= $(wildcard dist/*.exe)

wine-image:               ## container with Wine, for the Windows smoke test
	podman build --security-opt label=disable -t $(WINEIMAGE) \
		-f packaging/Containerfile.wine packaging

# Proves the Windows build starts, draws, and finds a card by drive letter.
# Not a substitute for Windows: Eject calls a shell verb Wine does not have.
#   make wine-test EXE=path/to/pocket-tools-x.y.z-windows-x64.exe
wine-test: wine-image     ## run the Windows build under Wine
	@test -n "$(EXE)" || { echo "set EXE=path/to/the.exe"; exit 1; }
	mkdir -p build/wine build/wine/card/Cores build/wine/card/Platforms
	printf '{"platform":{"name":"Game Boy Color"}}' \
		> build/wine/card/Platforms/gbc.json
	mkdir -p "build/wine/card/Assets/gbc/common"
	podman run --rm --security-opt label=disable \
		-v "$(abspath $(EXE)):/exe/app.exe:ro" \
		-v "$(CURDIR)/build/wine/card:/card:ro" \
		-v "$(CURDIR)/build/wine:/out" \
		$(WINEIMAGE) /exe/app.exe 35

clean:                    ## remove build output
	rm -rf build dist
