# SPDX-License-Identifier: GPL-3.0-or-later
"""Tk front end: pick a card, a system, a game, tick cheats, send to the Pocket.

Three panes, left to right: systems on the card, games in the selected system,
cheats for the selected game. The tick state of the cheat list is exactly what
will be written, and what is already on the card starts ticked.

Each cheat also shows how the core makes it take effect, because the two ways
do not behave the same. A GameShark code is written into RAM once a frame, so
the value is really there; a Game Genie code overrides the CPU's read, which is
what a ROM patch needs. See docs/CHEATS.md.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import card as card_mod
import carts
import cheatfile
import cheatlib
import core as core_mod
import db
import dumps
import library
import match
import mister
import meter
import model
import nointro
import prefs
import say
import reveal
import timing
import version
import work
import emulator
import writer

TICK, UNTICK = "☑", "☐"
CARTS = "carts"        # iid of the Cartridges row in the systems pane
# Filed cartridge dumps. Its own row rather than a heading under Cartridges,
# because the two are opposites and sharing a category would say they are
# alike: a played cartridge has no identity and the app takes your word for
# what it is, while a dump was hashed and identified and nothing about it is
# taken on trust.
SHELF = "shelf"        # iid of the Cartridge dumps row
GROUP = "sys:"         # iid prefix of a system heading in the cartridge pane

# The colours, in one place, because they mean the same thing in every window
# and were once picked per dialog until two of them disagreed about whether a
# broken boot ROM was good news. Green is never a failure: it is for a state
# worth acting on that is not one.
FAULT = "#a00"         # broken, and nothing works until it is fixed
LESSER = "#b35c00"     # wrong, or in the way, but not fatal
READY = "#0a6"         # worth acting on, and not broken
IDLE = "#999"          # nothing to do here
QUIET = "#666"         # ordinary prose


# ------------------------------------------------- opening a directory --
def holding(path: str | None) -> str | None:
    """The directory the app offers to open for a path it names.

    Always the containing directory and never the file itself. Revealing a
    file means a different argument on every platform for no useful
    difference, and the answer to "did it land" is the directory listing --
    which is also what somebody wants open when they go looking for the other
    files beside it.
    """
    return os.path.dirname(os.path.abspath(path)) if path else None


def open_button(parent, path: str | None, *, text="Open", width=7):
    """A button that opens one fixed directory, or None if it would not work.

    None rather than a disabled button, because a directory that is not there
    and a machine with no file manager both stay that way for as long as this
    window is up: a sandboxed build should quietly show nothing rather than
    offer a button that fails. The path is still on screen either way, and
    Copy path is what covers a build that cannot open one.
    """
    if not reveal.openable(path):
        return None
    return ttk.Button(parent, text=text, width=width,
                      command=lambda: reveal.directory(path or ""))


def open_button_for(parent, get_path, *, text="Open", width=7):
    """The same button where the directory follows a selection.

    Here the button is drawn whenever the machine has a file manager at all
    and `retune_open` greys it for a row whose directory is not there yet. A
    button that vanished and came back as the selection moved down a table
    would be worse than one that goes grey, and unlike the fixed case the
    answer changes while the window is open.
    """
    if not reveal.available():
        return None
    return ttk.Button(parent, text=text, width=width,
                      command=lambda: reveal.directory(get_path() or ""))


def retune_open(btn, path: str | None) -> None:
    """Grey a selection-following Open button when there is nothing to open."""
    if btn is not None:
        btn.state(["!disabled"] if reveal.openable(path) else ["disabled"])


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=8)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)

        self.card: card_mod.Card | None = None
        # Whether the cartridge dump surface is on screen. It follows one
        # thing: is the dumper core on the card. Installing that core is the
        # act of asking for the feature, and nothing here installs it unasked,
        # so there is no separate setting to keep in step with it. Recomputed
        # from every survey by refresh_dumps().
        self.dumps_on = False
        # What the card's dump directory holds, counted at the last scan. Set
        # before the first scan so the surface can be painted at any time.
        self.waiting = dumps.Waiting()
        self.platforms: list[card_mod.Platform] = []
        self.platform: card_mod.Platform | None = None
        self.worker = work.Worker(master)
        # The database fetch gets its own runner: it takes about a minute, and
        # card reads must not queue behind it.
        self.dbjob = work.Job(master)
        # Reads the whole card in the background, a system at a time, starting
        # with whichever one you are looking at. See start_prefetch.
        self.prefetch = work.Job(master)
        # The core check and the core install: network bound, and neither may
        # queue behind the database fetch or the card read.
        self.corejob = work.Job(master)
        # Hashing the cartridge dumps, and importing one. Seventeen dumps with a
        # 16 MB one among them is seconds of reading, and a run of it on the Tk
        # thread would freeze the window in a way that is indistinguishable
        # from a crash. Its own runner so it cannot queue behind the database.
        self.dumpjob = work.Job(master)
        # The No-Intro data. Which DATs to use is a decision, so prefs
        # remembers them and the library keeps a copy; reload_dats() fills
        # this in at every start. It used to live only as long as the window,
        # on the reasoning that a DAT is somebody's own download and the app
        # never copies one. The app copies things now, and the cost of not
        # remembering was a run that could identify nothing until the three
        # files had been found again.
        self.catalog = nointro.Catalog()
        self.ready: dict[str, list[int]] = {}   # platform id -> cheat counts
        self.shelf_sizes: dict[str, int] = {}  # imported dump name -> its size
        self.wanted: str | None = None          # the system to read next
        self.working: Working | None = None     # the modal, while it is up
        self._working_after = None
        self.remote: dict | None = None       # upstream's version, once known
        self.dbjob_kind = ""                  # "check" or "update", for Stop
        self.survey: core_mod.Survey | None = None   # cores and boot ROMs
        # Newest release per repository, once known. The cores no longer come
        # from one: Game Boy ships both from openfpga-GBC-cheats, PC Engine
        # will ship from its own.
        self.releases: dict[str, dict] | None = None
        self.corejob_kind = ""                # "check" or "install", for Stop
        self.games: list[card_mod.Game] = []
        self.view: model.GameView | None = None

        self._build()
        # Before the card scan finishes, so the dumps window can never be
        # opened against an empty catalogue.
        self.reload_dats()
        self.rescan()
        self.check_db()
        self.check_core()

    # ---------------------------------------------------------------- layout --
    def _build(self) -> None:
        self.columnconfigure(0, weight=1, minsize=170)
        self.columnconfigure(1, weight=3, minsize=280)
        self.columnconfigure(2, weight=5, minsize=380)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Pocket SD card:").grid(row=0, column=0, padx=(0, 6))
        self.card_label = ttk.Label(top, text="scanning...", foreground="#666")
        self.card_label.grid(row=0, column=1, sticky="w")
        # On the card line rather than the core line, because what it acts on
        # is the card: the dumper writes into /Assets and this reads what it
        # left. Next to Eject for the same reason, and before it, since going
        # through the dumps is what somebody does before pulling the card.
        self.dumps_btn = ttk.Button(top, text="Cartridge dumps...", width=18,
                                    command=self.show_dumps, state="disabled")
        self.dumps_btn.grid(row=0, column=2, padx=(0, 4))
        self.card_open = open_button_for(
            top, lambda: self.card.root if self.card else None)
        if self.card_open is not None:
            self.card_open.grid(row=0, column=3, padx=(0, 4))
            retune_open(self.card_open, None)   # no card until one is found
        self.rescan_btn = ttk.Button(top, text="Rescan", command=self.rescan)
        self.rescan_btn.grid(row=0, column=4)
        self.eject_btn = ttk.Button(top, text="Eject", width=7,
                                    command=self.eject, state="disabled")
        self.eject_btn.grid(row=0, column=5, padx=(4, 0))
        # A second line under the path, because the first one is a path and
        # appending to it would make the two read as one string. Gridded here
        # and shown or hidden by apply_dumps_surface(), so the window has no
        # row that exists only to be empty.
        self.waiting_label = ttk.Label(top, text="", foreground=READY)

        self.systems = self._tree(1, 0, ("count",), {"#0": "System", "count": "ROMs"},
                                  {"#0": 130, "count": 50})
        self.systems.bind("<<TreeviewSelect>>", self.on_system)

        mid = ttk.Frame(self)
        mid.grid(row=1, column=1, sticky="nsew", padx=(0, 4))
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)
        self.gamelist = self._tree_in(mid, ("cheats",),
                                      {"#0": "Game", "cheats": "On"},
                                      {"#0": 240, "cheats": 40})
        self.gamelist.bind("<<TreeviewSelect>>", self.on_game)

        cartbar = ttk.Frame(mid)
        cartbar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.add_btn = ttk.Button(cartbar, text="Add cartridge...", width=16,
                                  command=self.add_cart, state="disabled")
        self.add_btn.pack(side="left")
        self.del_btn = ttk.Button(cartbar, text="Remove", width=9,
                                  command=self.remove_cart, state="disabled")
        self.del_btn.pack(side="left", padx=4)
        self.move_btn = ttk.Button(cartbar, text="Move to...", width=11,
                                   command=self.move_cart, state="disabled")
        self.move_btn.pack(side="left")
        # Assets/<system>/common/Cartridges/ on the card, which is where the
        # core's Load Cheats browser looks and the one directory a cartridge
        # user has to find by hand. Greyed rather than hidden while nothing is
        # selected, since the answer changes with every click in the pane.
        self.copy_btn = ttk.Button(cartbar, text="Copy to card", width=13,
                                   command=self.copy_to_card, state="disabled")
        self.copy_btn.pack(side="left", padx=(4, 0))
        # Only built when this machine has an mGBA. A Play button that cannot
        # play is worse than no button: it reads as the feature being broken
        # rather than absent, and the fix is an install this app has no
        # business performing.
        self.play_btn = (
            ttk.Button(cartbar, text="Play", width=6, state="disabled",
                       command=self.play_dump)
            if emulator.available() else None)
        if self.play_btn is not None:
            self.play_btn.pack(side="left", padx=(4, 0))
        self.saves_btn = ttk.Button(cartbar, text="Saves...", width=9,
                                    command=self.show_saves, state="disabled")
        self.saves_btn.pack(side="left", padx=(4, 0))
        self.cart_open = open_button_for(cartbar, self.cart_dir)
        if self.cart_open is not None:
            self.cart_open.pack(side="left", padx=(4, 0))
            retune_open(self.cart_open, None)

        right = ttk.Frame(self)
        right.grid(row=1, column=2, sticky="nsew", padx=(8, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        src = ttk.Frame(right)
        src.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        src.columnconfigure(0, weight=1)
        self.source_label = ttk.Label(src, text="", foreground="#666")
        self.source_label.grid(row=0, column=0, sticky="w")
        self.source_btn = ttk.Button(src, text="Change source...",
                                     command=self.change_source, state="disabled")
        self.source_btn.grid(row=0, column=1)

        cols = ("desc", "how", "codes")
        self.cheats = ttk.Treeview(right, columns=cols, show="tree headings",
                                   selectmode="none")
        self.cheats.heading("#0", text="")
        self.cheats.heading("desc", text="Cheat")
        self.cheats.heading("how", text="Applied")
        self.cheats.heading("codes", text="Addresses")
        self.cheats.column("#0", width=34, stretch=False, anchor="center")
        self.cheats.column("desc", width=230)
        self.cheats.column("how", width=64, stretch=False, anchor="center")
        self.cheats.column("codes", width=160)
        self.cheats.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(right, orient="vertical", command=self.cheats.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.cheats.configure(yscrollcommand=sb.set)
        self.cheats.tag_configure("extra", foreground="#0a6")
        self.cheats.tag_configure("dead", foreground="#999")
        self.cheats.bind("<Button-1>", self.on_click)

        # What this says depends on the system, because the systems do not
        # agree on how many ways there are. See retune_applied.
        self.applied_note = ttk.Label(right, foreground="#666", text="",
                                      wraplength=520)
        self.applied_note.grid(row=2, column=0, columnspan=2, sticky="w",
                               pady=(4, 0))
        self.applied_platform = ""

        bottom = ttk.Frame(right)
        bottom.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        bottom.columnconfigure(0, weight=1)
        self.meter = meter.Meter(bottom, writer.MAX_CODES)
        self.meter_platform = ""
        self.meter.grid(row=0, column=0, sticky="w")
        self.status = ttk.Label(bottom, text="")
        self.status.grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Button(bottom, text="None", width=6,
                   command=lambda: self.set_all(False)).grid(row=0, column=1, padx=2)
        ttk.Button(bottom, text="All", width=5,
                   command=lambda: self.set_all(True)).grid(row=0, column=2, padx=2)
        self.save_btn = ttk.Button(bottom, text="Send to Pocket", width=16,
                                   command=self.save, state="disabled")
        self.save_btn.grid(row=0, column=3, padx=(8, 0))

        self._build_corebar()
        self._build_dbbar()
        self.apply_dumps_surface()

    def _build_corebar(self) -> None:
        """Which core is on the card, and whether it can actually run.

        Above the database line rather than below it because it is the more
        fundamental of the two: an out of date cheat database writes cheats
        that are merely old, and a missing core makes every button in this
        window a no-op that looks like it worked.
        """
        bar = ttk.Frame(self)
        bar.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        bar.columnconfigure(0, weight=1)
        self.core_label = ttk.Label(bar, text="Pocket core: checking...",
                                    foreground="#666")
        self.core_label.grid(row=0, column=0, sticky="w")
        self.core_bar = ttk.Progressbar(bar, length=180, mode="determinate")
        # Both buttons on the first row, next to each other. One per line put
        # three buttons in a column down the bottom right corner, each of them
        # a long way from the line it acts on, and they read as belonging to
        # the status area rather than to the core.
        self.bios_btn = ttk.Button(bar, text="Boot ROMs...", width=13,
                                   command=self.show_roms, state="disabled")
        self.bios_btn.grid(row=0, column=2, padx=(6, 0))
        self.core_btn = ttk.Button(bar, text="Cores...", width=13,
                                   command=self.install_core, state="disabled")
        self.core_btn.grid(row=0, column=3, padx=(4, 0))
        self.bios_label = ttk.Label(bar, text="", foreground="#666")
        self.bios_label.grid(row=1, column=0, columnspan=4, sticky="w",
                             pady=(2, 0))

    def _build_dbbar(self) -> None:
        """Which cheat database is in use, how old it is, and updating it."""
        bar = ttk.Frame(self)
        bar.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        bar.columnconfigure(0, weight=1)
        self.db_label = ttk.Label(bar, text="cheat database: checking...",
                                  foreground="#666")
        self.db_label.grid(row=0, column=0, sticky="w")
        self.db_bar = ttk.Progressbar(bar, length=180, mode="determinate")
        self.db_btn = ttk.Button(bar, text="Update", width=8,
                                 command=self.update_db)
        self.db_btn.grid(row=0, column=3, padx=(6, 0))
        # The database is a cache in a directory nobody would guess at, and
        # the one question anybody asks about it -- did the update actually
        # land -- is answered by looking in it. Selection-following rather
        # than fixed because the directory does not exist until the first
        # fetch makes it, and that happens with this window open.
        self.db_open = open_button_for(bar, db.store)
        if self.db_open is not None:
            self.db_open.grid(row=0, column=4, padx=(4, 0))
        # Next to the database version, since the two things a bug report
        # needs are which build this is and which cheat files it was reading.
        ttk.Label(bar, text=version.label(), foreground="#888").grid(
            row=0, column=2, padx=(12, 0), sticky="e")

    def _tree(self, row: int, col: int, cols, heads, widths) -> ttk.Treeview:
        frame = ttk.Frame(self)
        frame.grid(row=row, column=col, sticky="nsew", padx=(0, 4))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        return self._tree_in(frame, cols, heads, widths)

    def _tree_in(self, frame, cols, heads, widths) -> ttk.Treeview:
        t = ttk.Treeview(frame, columns=cols, show="tree headings")
        for k, v in heads.items():
            t.heading(k, text=v)
        for k, v in widths.items():
            t.column(k, width=v, stretch=(k == "#0"))
        t.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(frame, orient="vertical", command=t.yview)
        sb.grid(row=0, column=1, sticky="ns")
        t.configure(yscrollcommand=sb.set)
        return t

    # ----------------------------------------------------------------- cards --
    def rescan(self) -> None:
        """Find the card and read its games. The reading happens off-thread."""
        self.systems.delete(*self.systems.get_children())
        self.gamelist.delete(*self.gamelist.get_children())
        self.cheats.delete(*self.cheats.get_children())
        self.view = None
        self.platforms = []
        self.save_btn.state(["disabled"])
        self.source_btn.state(["disabled"])
        self.source_label.config(text="")

        if self.prefetch.busy():
            self.prefetch.cancel()
        self.close_working()
        self.ready.clear()
        self.wanted = None
        self.survey = None
        self.refresh_dumps()
        self.refresh_core_label()
        self.eject_btn.state(["disabled"])
        self.dumps_btn.state(["disabled"])
        retune_open(self.card_open, None)
        self.card_label.config(text="scanning...", foreground="#666")
        self.status.config(text="reading the card", foreground="#000")
        self.rescan_btn.state(["disabled"])
        self.worker.submit(self._scan, self._scanned, "scan")

    @staticmethod
    def _scan():
        """Worker thread: no widgets touched here."""
        with timing.stage("find_cards"):
            cards = card_mod.find_cards()
        if not cards:
            return None
        with timing.stage("list the systems"):
            plats = cards[0].platforms()
        return cards, plats

    def _scanned(self, result, err) -> None:
        self.rescan_btn.state(["!disabled"])
        if err is not None:
            self.card_label.config(text="could not read the card", foreground="#a00")
            self.status.config(text=str(err), foreground="#a00")
            return
        if result is None:
            self.card = None
            self.card_label.config(
                text="no Pocket card found (needs Cores/ and Platforms/)",
                foreground="#a00")
            self.status.config(text="Insert the card and press Rescan")
            return

        cards, platforms = result
        self.card = cards[0]
        self.ready.clear()
        self.eject_btn.state(["!disabled"])
        self.dumps_btn.state(["!disabled"])
        retune_open(self.card_open, self.card.root)
        self.platforms = platforms
        extra = f"  (+{len(cards) - 1} more)" if len(cards) > 1 else ""
        self.card_label.config(text=f"{self.card.root}  [{self.card.label}]{extra}",
                               foreground="#060")
        # Said on the card line rather than left for somebody to go looking
        # for, because the dumper writes into /Assets and nothing else in this
        # window ever mentions that directory. Counted, not surveyed: this runs
        # on every scan and a survey hashes every byte of a 32 MB cartridge.
        root = library.path()
        self.waiting = dumps.waiting(
            self.card.root, root, library.load(root) if root else None)
        self.show_waiting()
        for i, p in enumerate(self.platforms):
            # Blank rather than 0 until the system has been read: nobody has
            # counted yet, and 0 would be a claim that there are none.
            self.systems.insert("", "end", iid=str(i), text=p.name,
                                values=(len(p.games) if p.scanned else "",))
        # Cartridges are not files on the card, so they are listed separately.
        self.systems.insert("", "end", iid=CARTS, text="Cartridges",
                            values=(len(carts.all()),))
        if self.dumps_on:
            self.systems.insert("", "end", iid=SHELF, text="Cartridge dumps",
                                values=(len(self.shelf()),))
        self.status.config(text="reading the card...")
        self.start_prefetch()

    def eject(self) -> None:
        """Flush and unmount, so the card can be pulled without losing a write.

        Writing to the card already syncs, but a sync is not an unmount: the
        filesystem is still mounted and the kernel may still have metadata to
        write back. This is the same eject the desktop does.
        """
        if self.card is None:
            return
        card = self.card
        self.eject_btn.state(["disabled"])
        self.rescan_btn.state(["disabled"])
        self.status.config(text="unmounting...", foreground="#000")
        self.worker.submit(card.unmount, self._ejected, "eject")

    def _ejected(self, message, err) -> None:
        self.rescan_btn.state(["!disabled"])
        if err is not None:
            # Almost always a file still open on the card, and the tool's own
            # message names what. Nothing is forced: that is the failure this
            # whole app exists to avoid.
            self.eject_btn.state(["!disabled"])
            self.status.config(text=f"could not eject: {err}", foreground="#a00")
            messagebox.showwarning("Eject", f"The card was not unmounted.\n\n{err}")
            return
        self.card = None
        self.waiting = dumps.Waiting()
        self.platforms = []
        self.platform = None
        self.games = []
        self.view = None
        self.survey = None
        self.refresh_dumps()
        self.refresh_core_label()
        self.systems.delete(*self.systems.get_children())
        self.gamelist.delete(*self.gamelist.get_children())
        self.cheats.delete(*self.cheats.get_children())
        for b in (self.save_btn, self.source_btn, self.del_btn, self.add_btn,
                  self.move_btn, self.dumps_btn):
            b.state(["disabled"])
        retune_open(self.card_open, None)
        retune_open(self.cart_open, None)
        self.source_label.config(text="")
        self.meter.set(0)
        self.card_label.config(text="card unmounted, safe to remove",
                               foreground="#060")
        self.status.config(text=str(message), foreground="#060")

    # -------------------------------------------------------------- No-Intro --
    def reload_dats(self) -> None:
        """Put the remembered DATs back into the catalog. Synchronous.

        Loaded into the catalog this window already holds rather than into a
        fresh one that replaces it. Every window that reads the catalog is
        handed the object, not asked for it again, so replacing it leaves the
        dumps window looking at the empty catalogue it captured earlier.

        On the Tk thread because the whole job is 0.3 s: all three DATs,
        measured. The first version of this put it on a background runner
        against a guess that tens of megabytes of XML would freeze the window.
        That guess cost the wrong runner API, which raised on every start, and
        a race for nothing.

        A path that has gone is dropped from the list rather than reported:
        the file was somebody's download, it is allowed to disappear, and the
        dumps window already says which systems are missing.
        """
        want = [p for p in prefs.get_dats() if os.path.exists(p)]
        if not want:
            if prefs.get_dats():
                prefs.set_dats([])       # every one of them has gone
            return
        kept = []
        for path in want:
            try:
                if self.catalog.take(path):
                    kept.append(path)
                else:
                    say.err(f"{os.path.basename(path)} could not be read")
            except Exception as e:                           # noqa: BLE001
                say.err(f"{os.path.basename(path)}: {e}")
        if kept != prefs.get_dats():
            prefs.set_dats(kept)

    # -------------------------------------------------------------- database --
    def check_db(self) -> None:
        """Ask upstream what is current, without downloading anything.

        Two API calls. It runs at startup and again after an update, and a
        failure is not worth a dialog: being offline is not an error, it just
        means the comparison cannot be made.
        """
        self.refresh_db_label()
        if self.dbjob.busy():
            return
        self.dbjob_kind = "check"
        self.dbjob.start(lambda report, cancelled: db.remote_state(timeout=10),
                         None, self._db_checked)

    def _db_checked(self, remote, err) -> None:
        self.dbjob_kind = ""
        if err is None:
            self.remote = remote
        self.refresh_db_label(
            note="" if err is None else "  (could not reach upstream)")

    def refresh_db_label(self, note: str = "") -> None:
        local = db.local_state()
        text = db.describe(local, self.remote) + note
        stale = self.remote is not None and not db.up_to_date(local, self.remote)
        self.db_label.config(
            text=text, foreground="#a00" if local is None else
            ("#960" if stale else "#666"))
        retune_open(self.db_open, db.store())

    def update_db(self) -> None:
        """Check first, then fetch only if there is something to fetch.

        The check doubles as the retry for a failed startup check, which is why
        there is no separate button for it.
        """
        if self.dbjob_kind == "update":
            self.dbjob.cancel()
            self.status.config(text="stopping the update...", foreground="#000")
            return
        if self.dbjob.busy():
            # The startup check is two API calls and nearly done; nothing is
            # gained by cancelling it and it is not what Stop means.
            self.status.config(text="still checking, try again in a moment",
                               foreground="#000")
            return

        def body(report, cancelled):
            report(0, 0, "asking upstream what is current")
            remote = db.remote_state()
            if db.up_to_date(db.local_state(), remote):
                return ("current", remote)
            return ("fetched", db.fetch(progress=report, cancelled=cancelled),
                    remote)

        if not self.dbjob.start(body, self._db_progress, self._db_done):
            return
        self.dbjob_kind = "update"
        self.db_btn.config(text="Stop")
        self.db_bar.grid(row=0, column=1, padx=(8, 0))
        self.db_bar.config(value=0, maximum=100)

    def _db_progress(self, done: int, total: int, message: str) -> None:
        if total:
            self.db_bar.config(mode="determinate", maximum=total, value=done)
            self.db_label.config(text=f"{message}  {done}/{total}",
                                 foreground="#666")
        else:
            self.db_bar.config(mode="indeterminate", value=0)
            self.db_label.config(text=message, foreground="#666")

    def _db_done(self, result, err) -> None:
        self.dbjob_kind = ""
        self.db_btn.config(text="Update")
        self.db_bar.grid_remove()
        if isinstance(err, db.Cancelled):
            self.status.config(text="update stopped, the database is unchanged",
                               foreground="#000")
            self.refresh_db_label()
            return
        if err is not None:
            self.refresh_db_label(note="  (update failed)")
            self.status.config(text=f"could not update: {err}", foreground="#a00")
            messagebox.showerror("Cheat database",
                                 f"The database was not updated.\n\n{err}\n\n"
                                 "Whatever was there before is untouched.")
            return

        if result[0] == "current":
            self.remote = result[1]
            self.refresh_db_label()
            self.status.config(text="cheat database is already up to date",
                               foreground="#060")
            return

        _, state, remote = result
        self.remote = remote
        # The index is built from the files that were just replaced.
        cheatlib.refresh()
        self.refresh_db_label()
        self.status.config(
            text=f"cheat database updated: {state['files']} files, "
                 f"{db.day(state['date'])}", foreground="#060")
        if self.view is not None:
            self.on_game()

    # ---------------------------------------------------------------- the core --
    def check_core(self) -> None:
        """Ask the core's release page what is current. One API call.

        Runs at startup alongside the database check, and again after an
        install. Being offline is not an error: the card half of the report
        still says which core is installed and whether its boot ROMs are
        there, which is the half that decides whether the Pocket works.
        """
        if self.corejob.busy():
            return
        self.corejob_kind = "check"
        self.corejob.start(
            lambda report, cancelled: core_mod.all_latest(timeout=10),
            None, self._core_checked)

    def _core_checked(self, releases, err) -> None:
        self.corejob_kind = ""
        if err is None:
            self.releases = releases
        self.refresh_core_label(
            note="" if err is None else "  (could not reach the release page)")

    def refresh_core_label(self, note: str = "") -> None:
        """Both core lines, from whatever of the two halves is known."""
        if self.survey is None and (self.card is not None or self.worker.busy
                                    or self.prefetch.busy()):
            # A card that has not been read yet is not a card without a core,
            # and saying so for the second it takes reads as bad news.
            self.core_label.config(text="Pocket core: reading the card...",
                                   foreground="#666")
        else:
            text, bad = core_mod.describe(self.survey, self.releases)
            self.core_label.config(text=text + note,
                                   foreground="#a00" if bad else "#666")

        roms, rbad = core_mod.describe_roms(self.survey)
        self.bios_label.config(text=roms, foreground="#a00" if rbad else "#666")
        self.bios_btn.state(["!disabled"] if roms else ["disabled"])

        if self.corejob_kind == "install":
            return
        # Enabled whenever there is a card to talk about, including offline
        # and including when everything is current. The dialog behind it is
        # worth opening to see what is installed, and a button that is disabled
        # without saying why was the old version's worst habit.
        self.core_btn.config(text="Cores...")
        self.core_btn.state(["disabled"] if self.survey is None
                            else ["!disabled"])

    def install_core(self) -> None:
        """Put the current release on the card, once the user has said so."""
        if self.corejob_kind == "install":
            self.corejob.cancel()
            self.status.config(text="stopping the install...", foreground="#000")
            return
        if self.corejob.busy():
            self.status.config(text="still checking the release, try again in "
                                    "a moment", foreground="#000")
            return
        if self.card is None or self.survey is None:
            return

        # The dialog is the confirmation. It shows what is on the card against
        # what is available, per core, and hands back exactly what was ticked -
        # which is the part the old yes/no box could not do, because it was
        # confirming a decision this method had already made.
        todo = CoresDialog(self, self.survey, self.releases).result
        if not todo:
            return

        rels = self.releases
        root = self.card.root

        def body(report, cancelled):
            written = core_mod.install(root, rels, cores=todo,
                                       progress=report, cancelled=cancelled)
            return written, core_mod.survey(root)

        if not self.corejob.start(body, self._core_progress, self._core_done):
            return
        self.corejob_kind = "install"
        # Unmounting or rescanning the card halfway through writing a core to
        # it is the one thing that could leave the Pocket with a core that
        # loads and does not run.
        self.eject_btn.state(["disabled"])
        self.rescan_btn.state(["disabled"])
        self.core_btn.config(text="Stop")
        self.core_bar.grid(row=0, column=1, padx=(8, 0))
        self.core_bar.config(value=0, maximum=100)

    def _core_progress(self, done: int, total: int, message: str) -> None:
        if total:
            self.core_bar.config(mode="determinate", maximum=total, value=done)
        else:
            self.core_bar.config(mode="determinate", maximum=100, value=0)
        self.core_label.config(text=message, foreground="#666")

    def _core_done(self, result, err) -> None:
        self.corejob_kind = ""
        self.core_bar.grid_remove()
        self.core_btn.config(text="Cores...")
        self.rescan_btn.state(["!disabled"])
        if self.card is not None:
            self.eject_btn.state(["!disabled"])
        if isinstance(err, core_mod.Cancelled):
            self.status.config(text="install stopped, the core is unchanged",
                               foreground="#000")
            self.refresh_core_label()
            return
        if err is not None:
            self.refresh_core_label(note="  (install failed)")
            self.status.config(text=f"could not install the core: {err}",
                               foreground="#a00")
            pages = "\n".join(core_mod.releases_page(r)
                              for r in core_mod.repos())
            messagebox.showerror(
                "Pocket core",
                f"The core was not installed.\n\n{err}\n\n"
                "Whatever was on the card before is untouched. You can also "
                f"install it by hand from\n{pages}")
            return

        written, found = result
        self.survey = found
        if self.card is not None:
            self.card.sync()
        self.refresh_dumps()
        self.refresh_core_label()
        self.status.config(
            text=f"core installed: {len(written)} entries written. "
                 "Eject before pulling the card.", foreground="#060")
        if found.problems():
            # Installing the core is half of it. A card with no boot ROM shows
            # the core in the menu and then refuses to start anything, which
            # reads as a broken install rather than a missing file.
            self.show_roms()

    def show_roms(self) -> None:
        """What each core needs, whether it is there, and where it goes.

        The same dialog whether it was asked for from the core bar or opened
        by an install that finished onto a card with a boot ROM missing. The
        second is the case it exists for and the one nobody goes looking for.
        """
        if self.survey is None:
            return
        RomsDialog(self, self.survey)

    # ----------------------------------------------------------------- panes --
    def on_system(self, _evt=None) -> None:
        sel = self.systems.selection()
        if not sel:
            return
        self.add_btn.state(["!disabled"] if sel[0] == CARTS else ["disabled"])
        self.del_btn.state(["disabled"])
        self.move_btn.state(["disabled"])
        self.copy_btn.state(["disabled"])
        if sel[0] == SHELF:
            # Same reason as the cartridges branch below: this fills the game
            # pane synchronously, so a platform read still in flight would
            # repaint it afterwards while self.games still held the dumps.
            self.platform = None
            self.show_shelf()
            return
        if sel[0] == CARTS:
            # Retire any platform read still in flight. show_carts() fills the
            # game pane synchronously, so a result arriving after it would
            # otherwise repaint the pane with that platform's ROMs while
            # self.games still held the cartridges: every row then indexed the
            # wrong object, and Remove silently did nothing.
            self.platform = None
            self.show_carts()
            return
        plat = self.platforms[int(sel[0])]
        if (plat is self.platform and plat.scanned
                and self.gamelist.get_children()):
            # Already shown. Re-selecting the same system must not read it
            # again: writing the ROM count back into the selected row makes Tk
            # reissue <<TreeviewSelect>>, and without this that lands straight
            # back here and reads the card in a loop.
            return
        self.platform = plat
        self.games = []
        self.gamelist.delete(*self.gamelist.get_children())
        self.cheats.delete(*self.cheats.get_children())
        self.view = None
        self.save_btn.state(["disabled"])
        self.source_btn.state(["disabled"])
        self.source_label.config(text="")
        # Reading is the background pass's job. Ask it for this one next, and
        # show whatever it already has.
        self.wanted = plat.id
        if plat.id in self.ready:
            self.fill_pane(plat)
        else:
            self.status.config(text=f"reading {plat.name}...",
                               foreground="#000")

    def apply_ready_pane(self) -> None:
        """Draw the selected system once the card has been read."""
        if self.platform is not None and self.platform.id in self.ready:
            self.fill_pane(self.platform)

    # ------------------------------------------------------------ reading --
    def start_prefetch(self) -> None:
        """Read the whole card in the background, a system at a time.

        Reading a system on a cold card costs seconds: about two and a half to
        walk it and nearly eight more to open the cheat file beside every game
        that has one. Doing all three up front left the window unusable for
        twenty-seven seconds. Doing them on demand was worse, because the wait
        moved to every time you clicked a system, which is when you are
        actually looking at it.

        So it happens up here, off the Tk thread, starting with whichever
        system is selected and moving to that one whenever the selection
        changes. Clicking a system you have already visited costs nothing, and
        clicking one the pass has not reached yet says so and fills in when it
        arrives. Warm, the whole thing takes a fraction of a second and none
        of this is visible.
        """
        if self.card is None or self.prefetch.busy():
            return
        card = self.card
        plats = list(self.platforms)
        ready = self.ready
        # Only raise the modal if this is actually going to take a moment. A
        # card the desktop has already walked is read in milliseconds, and a
        # dialog that flashes up and vanishes is worse than none.
        self._working_after = self.after(
            400, lambda: self.show_working(len(plats) + 1))

        def body(report, cancelled):
            # First, because it is a handful of files and it decides whether
            # anything else in this window can have an effect at all.
            with timing.stage("survey the cores"):
                found = core_mod.survey(card.root)
            steps = len(plats) + 1
            report(1, steps, core_mod.STEP)
            done: set[str] = set()
            while len(done) < len(plats):
                if cancelled():
                    return found
                # Whatever the user is looking at goes next.
                nxt = next((p for p in plats
                            if p.id == self.wanted and p.id not in done), None)
                if nxt is None:
                    nxt = next(p for p in plats if p.id not in done)
                if not nxt.scanned:
                    with timing.stage("walk one system", nxt.name):
                        card.fill(nxt)
                with timing.stage("count installed cheats",
                                  f"{nxt.name}, {len(nxt.games)} games"):
                    counts = [
                        len(model.writer.load_installed(g.cht_path, nxt.id))
                        if nxt.has_cheats(g) else 0 for g in nxt.games]
                ready[nxt.id] = counts
                done.add(nxt.id)
                report(len(done) + 1, steps, nxt.id)
            return found

        self.prefetch.start(body, self._prefetched, self._prefetch_done)

    def show_working(self, total: int) -> None:
        self._working_after = None
        if self.prefetch.busy() and self.working is None:
            self.working = Working(self, total, on_cancel=self.stop_working)

    def stop_working(self) -> None:
        """Give up on the rest of the card. What was read is kept."""
        self.prefetch.cancel()

    def close_working(self) -> None:
        if self._working_after is not None:
            self.after_cancel(self._working_after)
            self._working_after = None
        if self.working is not None:
            self.working.close()
            self.working = None

    def _prefetched(self, done: int, total: int, pid: str) -> None:
        """One system finished. Tk thread.

        Nothing is drawn yet on purpose: a window that fills in a pane at a
        time looks ready while half of it is not, and clicking into it gets
        you a list that changes under you.
        """
        if pid == core_mod.STEP:
            message = "Checked which core is installed"
        else:
            name = next((p.name for p in self.platforms if p.id == pid), pid)
            message = f"Read {name}"
        if self.working is not None:
            self.working.step(done, total, message)
        self.status_hint(f"reading the card... {done} of {total} steps")

    def _prefetch_done(self, result, err) -> None:
        self.close_working()
        if err is not None:
            self.status.config(text=f"could not read the card: {err}",
                               foreground="#a00")
            return
        if result is not None:
            self.survey = result
            self.refresh_dumps()
            self.refresh_core_label()
        self.apply_ready()
        # Show the first system now that everything behind it is real.
        if self.platforms and not self.systems.selection():
            self.systems.selection_set("0")
        self.status_hint("")

    def status_hint(self, text: str) -> None:
        """Say what the background pass is doing, without talking over a game."""
        if self.view is None:
            if text:
                self.status.config(text=text, foreground="#666")
            elif self.platform is not None and self.platform.scanned:
                self.status.config(
                    text=f"{len(self.platform.games)} games, "
                         f"{len(self.platform.cheat_files)} with cheats",
                    foreground="#000")

    def apply_ready(self) -> None:
        """Show whatever the background pass has finished. Tk thread."""
        for i, p in enumerate(self.platforms):
            if p.scanned:
                self.systems.item(str(i), text=p.name,
                                  values=(len(p.games),))
        plat = self.platform
        if (plat is not None and plat.id in self.ready
                and len(self.gamelist.get_children()) != len(plat.games)):
            self.fill_pane(plat)

    def fill_pane(self, plat) -> None:
        """Draw one system's games, with the cheat counts already read."""
        counts = self.ready.get(plat.id) or [0] * len(plat.games)
        # Bound to the list fill() produced, not the empty one a Platform
        # starts with: fill() replaces the list rather than filling it, and
        # binding early left every row indexing an empty list, so clicking a
        # game silently did nothing.
        self.games = plat.games
        with timing.stage("clear the game pane"):
            self.gamelist.delete(*self.gamelist.get_children())
        self.move_btn.state(["disabled"])
        with timing.stage("fill the game pane", f"{len(plat.games)} rows"):
            for i, (g, n) in enumerate(zip(plat.games, counts)):
                self.gamelist.insert("", "end", iid=str(i), text=g.name,
                                     values=(n if n else "",))
        self.status_hint("")

    def platform_name(self, pid: str) -> str:
        """What the card calls a system, falling back to the bare id.

        The systems pane already shows these names, and a cartridge listed
        under one should say the same thing rather than a second name for it.
        """
        for p in self.platforms:
            if p.id == pid:
                return p.name
        return pid.upper()

    def show_carts(self) -> None:
        """The cartridges you have listed, grouped by the system each is for.

        Rows keep indexing self.games, so the group rows get an iid that is
        not a number and selected_game() rejects them for free.
        """
        root = self.card.root if self.card else ""
        self.games = carts.all(root)
        self.gamelist.delete(*self.gamelist.get_children())
        self.cheats.delete(*self.cheats.get_children())
        self.view = None
        self.save_btn.state(["disabled"])
        self.source_btn.state(["disabled"])
        self.del_btn.state(["disabled"])
        self.move_btn.state(["disabled"])
        self.source_label.config(text="")
        retune_open(self.cart_open, None)

        for pid, positions in carts.grouped(self.games):
            gid = GROUP + pid
            self.gamelist.insert(
                "", "end", iid=gid, open=True,
                text=f"{self.platform_name(pid)}  ({len(positions)})",
                tags=("group",))
            for i in positions:
                c = self.games[i]
                n = len(model.writer.load_installed(c.cht_path, c.platform))
                self.gamelist.insert(gid, "end", iid=str(i), text=c.name,
                                     values=(n if n else "",))
        self.status.config(
            text=f"{len(self.games)} cartridges" if self.games else
                 "no cartridges listed yet, press Add", foreground="#000")

    def shelf(self) -> list:
        """Every dump in the library, as the card ROM each is destined to be.

        A `card.Game` and not a type of its own, because that is exactly what
        one of these becomes the moment it is copied across: same name, same
        platform, same place the Pocket looks for its cheat file. Presenting it
        as the thing it will be means `model.load()`, the matcher and Send to
        Pocket all work on it with no special case anywhere, and Copy to card
        is the one step that makes it true.

        The path is where the ROM would go whether or not it is there yet:
        /Assets/cartdumps/<system>/, not the system's common/ root. See
        card.CARTDUMPS for why, and for what it costs.
        """
        root = library.path()
        if not root or self.card is None:
            return []
        out = []
        for row in library.load(root):
            if not row.rom or not row.system:
                continue        # unidentified: there is no name to file under
            out.append(card_mod.Game(
                os.path.join(card_mod.cartdumps_dir(self.card.root, row.system),
                             row.rom), row.system))
        return sorted(out, key=lambda g: (g.platform, g.name.lower()))

    def on_card(self, game) -> str:
        """"same", "other" or "no": is this dump already sitting on the card?

        Compared by size and not by name alone. A ROM already on the card
        under the same canonical name is usually the same dump copied across
        earlier, and treating it as such is right - but it is not guaranteed,
        and the old check could not tell the two apart. Getting that wrong is
        not cosmetic: it disables Copy to card, so the dump can never be put
        there, and the cheats matched to it are then attached to somebody
        else's file.

        Size rather than a hash because this runs for every row every time the
        pane is drawn, and hashing a shelf of Game Boy Advance ROMs off a card
        over USB would cost the same half minute the dumps window already has
        to warn people about. Size catches a different game; the byte
        comparison that matters guards the delete, not this.
        """
        try:
            there = os.path.getsize(game.path)
        except OSError:
            return "no"
        want = self.shelf_sizes.get(game.name)
        return "same" if want is None or want == there else "other"

    def show_shelf(self) -> None:
        """The imported dumps, grouped by system, and whether each is on the card.

        Importing a dump used to be the end of the road: the copy lived in a
        directory on the computer and nothing in this window could see it, so
        the cheats it had been carefully matched to could never be attached to
        anything. This is the way back.
        """
        if not self.dumps_on:
            return
        self.games = self.shelf()
        root = library.path()
        self.shelf_sizes = {}
        if root:
            for r in library.load(root):
                if r.rom:
                    self.shelf_sizes[os.path.splitext(r.rom)[0]] = r.size
        # Its own tag rather than borrowing the heading one: a dump that is
        # only in the library is dimmer because there is nothing on the card
        # for its cheats to sit beside yet, which is a different thing from a
        # row that is not a game at all.
        self.gamelist.tag_configure("offcard", foreground=IDLE)
        self.gamelist.tag_configure("clash", foreground=LESSER)
        self.gamelist.delete(*self.gamelist.get_children())
        self.cheats.delete(*self.cheats.get_children())
        self.view = None
        for b in (self.save_btn, self.source_btn, self.del_btn, self.move_btn):
            b.state(["disabled"])
        self.source_label.config(text="")
        retune_open(self.cart_open, None)

        by_platform: dict[str, list[int]] = {}
        for i, g in enumerate(self.games):
            by_platform.setdefault(g.platform, []).append(i)
        here = clash = 0
        for pid, positions in by_platform.items():
            gid = GROUP + pid
            self.gamelist.insert(
                "", "end", iid=gid, open=True,
                text=f"{self.platform_name(pid)}  ({len(positions)})",
                tags=("group",))
            for i in positions:
                g = self.games[i]
                state = self.on_card(g)
                here += state == "same"
                clash += state == "other"
                n = len(model.writer.load_installed(g.cht_path, g.platform))
                self.gamelist.insert(
                    gid, "end", iid=str(i),
                    text=g.name + ("   (a different file of that name is on "
                                   "the card)" if state == "other" else ""),
                    values=(n if n else "",),
                    tags=("clash",) if state == "other"
                    else () if state == "same" else ("offcard",))
        if not self.games:
            self.status.config(
                text="nothing imported yet: press Cartridge dumps... on the card "
                     "line to read what is on the card", foreground="#000")
        else:
            # Kept short deliberately: this is a one-line label in a fixed
            # area, and the first version of it ran off the end of the window
            # exactly where it explained what the dimming meant.
            note = (f"{len(self.games)} imported, {here} on the card. Dimmed "
                    "ones are not: select one, then Copy to card.")
            if clash:
                note += f"  {clash} clash by name; copying asks."
            self.status.config(text=note,
                               foreground=LESSER if clash else "#000")

    def show_saves(self) -> None:
        """The save reads kept for the selected dump."""
        game = self.selected_game()
        root = library.path()
        if game is None or not root:
            return
        wanted = os.path.basename(game.path)
        row = next((r for r in library.load(root) if r.rom == wanted), None)
        if row is None:
            messagebox.showinfo(
                "Saves", f"{wanted} is not in the library index.")
            return
        SavesDialog(self, root, row)

    def play_dump(self) -> None:
        """Play the selected dump in mGBA, with its newest save read.

        The library copy, not the card copy, and a scratch copy of that: the
        emulator writes to the save as it plays and neither the library nor an
        SD card is somewhere it may do so.
        """
        game = self.selected_game()
        root = library.path()
        if game is None or not root:
            return
        rom = os.path.join(library.roms_dir(root), os.path.basename(game.path))
        if not os.path.exists(rom):
            messagebox.showerror(
                "Play", f"{os.path.basename(rom)} is not in the library "
                        "any more.")
            return
        row = next((r for r in library.load(root)
                    if r.rom == os.path.basename(game.path)), None)
        save = None
        reads = dumps.cartsave_reads(root, row) if row else []
        if reads and row.rom:
            save = os.path.join(library.cartsave_dir(root, row.rom), reads[-1])
        try:
            emulator.play(rom, save)
        except emulator.NoEmulator as e:
            messagebox.showinfo("Play", str(e))
            return
        except OSError as e:
            messagebox.showerror("Play", f"could not start mGBA:\n\n{e}")
            return
        self.status.config(
            text=(f"{game.name} launched"
                  + (f" with its save from {os.path.splitext(reads[-1])[0]}."
                     if save else ", with no save.")),
            foreground="#060")

    def copy_to_card(self) -> None:
        """Put an imported dump back on the card, under the name it earned.

        Written beside the ROMs the app already reads, so the Pocket can load
        it and so the cheats matched to it have something to be attached to.
        Through a temporary file and a replace, the way every other write here
        goes: a half-copied ROM that the Pocket would try to boot is worse than
        no ROM at all.
        """
        game = self.selected_game()
        root = library.path()
        if game is None or not root or self.card is None:
            return
        src = os.path.join(library.roms_dir(root), os.path.basename(game.path))
        if not os.path.exists(src):
            messagebox.showerror(
                "Cartridge dumps",
                f"{os.path.basename(src)} is not in the library any more.")
            return
        state = self.on_card(game)
        if state == "same":
            messagebox.showinfo("Cartridge dumps",
                                f"{game.name} is already on the card.")
            return
        if state == "other" and not messagebox.askyesno(
                "Copy to card",
                f"A different file called {os.path.basename(game.path)} is "
                "already on the card.\n\nReplace it with your dump? The one "
                "on the card is overwritten and is not backed up anywhere; "
                "your dump stays in the library either way.", parent=self):
            return
        tmp = game.path + ".part"
        try:
            os.makedirs(os.path.dirname(game.path), exist_ok=True)
            shutil.copyfile(src, tmp)
            os.replace(tmp, game.path)
        except OSError as e:
            for leftover in (tmp,):
                try:
                    os.remove(leftover)
                except OSError:
                    pass
            messagebox.showerror("Cartridge dumps",
                                 f"could not copy it to the card:\n\n{e}")
            return
        # The save goes with it. A cartridge dump without its save is a fresh
        # start on somebody's own cartridge, which is not what taking the save
        # off it was for. Reported rather than silent when there is none: most
        # dumps have no save, and a line saying so is how you find out that
        # the one you cared about did not come across.
        saved = ""
        wanted = os.path.basename(game.path)
        row = next((r for r in library.load(root) if r.rom == wanted), None)
        if row is not None and row.cartsaves:
            where, why = dumps.restore_save(root, row, game.path)
            saved = (f"  Save restored as {os.path.basename(where)}."
                     if where else f"  The save did not go across: {why}")
        # Emphatically not `self.ready.pop(platform)`. That was the obvious
        # way to say "this system's list is out of date" and it left the pane
        # permanently empty: nothing re-reads a system on the strength of the
        # cache being dropped, so selecting it afterwards cleared the list,
        # said "reading Game Boy..." and waited for a pass that never came.
        # The card genuinely has to be read again for a new file to be seen,
        # and Rescan is what does that.
        self.status.config(
            text=f"{game.name} copied to the card.{saved} Press Rescan to "
                 f"see it under {self.platform_name(game.platform)}.",
            foreground="#060")
        self.refresh_shelf()

    def add_cart(self) -> None:
        """Name it and say which system it is for.

        The system used to be assumed to be Game Boy Color, which was right
        often enough to be quietly wrong the rest of the time: it decides
        which directory the cheat file goes in on the card.
        """
        # If a group row is selected, that system is the obvious default.
        sel = self.gamelist.selection()
        preset = carts.DEFAULT_PLATFORM
        if sel and sel[0].startswith(GROUP):
            preset = sel[0][len(GROUP):]
        elif isinstance(self.selected_game(), carts.Cartridge):
            preset = self.selected_game().platform

        result = CartDialog(self, preset, self.platform_name).result
        if result is None:
            return
        name, plat = result
        if not carts.add(name, plat):
            messagebox.showinfo("Cartridges", f"{name} is already listed.")
            return
        self.after_cart_change(name)

    def after_cart_change(self, select: str | None = None) -> None:
        """Redraw the pane and put the selection back on a named cartridge."""
        self.systems.item(CARTS, values=(len(carts.all()),))
        self.show_carts()
        if select is None:
            return
        for i, c in enumerate(self.games):
            if c.name == select:
                self.gamelist.see(str(i))
                self.gamelist.selection_set(str(i))
                break

    def move_cart(self) -> None:
        """Offer the systems this cartridge is not already listed under.

        A menu rather than a button that names one destination. It was the
        latter while there were two systems, where moving is a flip; adding
        Game Boy Advance made that quietly wrong, because "the other one" is
        whichever came first in the list and nothing could ever be moved to
        the third. It also does not fit on a button: the label rendered as
        "Move to Game B".
        """
        cart = self.selected_game()
        if not isinstance(cart, carts.Cartridge):
            return
        others = [p for p in carts.PLATFORMS if p != cart.platform]
        if not others:
            return

        menu = tk.Menu(self, tearoff=0)
        for pid in others:
            menu.add_command(label=self.platform_name(pid),
                             command=lambda p=pid: self.do_move(cart, p))
        x = self.move_btn.winfo_rootx()
        y = self.move_btn.winfo_rooty() + self.move_btn.winfo_height()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def do_move(self, cart, pid: str) -> None:
        if not messagebox.askyesno(
                "Cartridges",
                f"File {cart.name} under {self.platform_name(pid)}?\n\n"
                "The cheat file goes in that system's folder from now on. "
                "Any file already written under "
                f"{self.platform_name(cart.platform)} is left where it is."):
            return
        carts.set_platform(cart.name, pid)
        self.after_cart_change(cart.name)

    def remove_cart(self) -> None:
        """Remove whatever is selected: a listed cartridge, or a library dump.

        One button because it is one idea -- take this out of the list I am
        looking at -- and the two lists are never both on screen. What is at
        stake differs enormously, so the confirmation is written per case
        rather than shared.
        """
        sel = self.systems.selection()
        if sel and sel[0] == SHELF:
            self.remove_from_library()
            return
        cart = self.selected_game()
        if not isinstance(cart, carts.Cartridge):
            return
        if not messagebox.askyesno(
                "Cartridges",
                f"Remove {cart.name} from the list?\n\n"
                "The cheat file already on the card is left alone."):
            return
        carts.remove(cart.name)
        self.after_cart_change()

    def show_dumps(self) -> None:
        """The cartridge dumps window, for the card that is in.

        The reading happens here rather than inside the window because it is
        slow and it must not be done on the Tk thread: hashing a real card of
        32 dumps, one of them 16 MB, over USB on exFAT measured 28 seconds,
        and a window that stops answering for that long is indistinguishable
        from one that has crashed. So it goes through the same modal the card
        read uses, and the dumps window opens on the answer.

        The catalog is held on the App rather than rebuilt here so that DATs
        loaded once stay loaded for as long as the app is up. There is nowhere
        to remember them: a DAT is the user's own download, the app never
        copies one, and prefs keeps decisions rather than files.
        """
        if not self.dumps_on or self.card is None or self.dumpjob.busy():
            return
        root = self.card.root

        def body(report, cancelled):
            # dumps.scan() in one call would give no progress, and this is the
            # one place the app has to say how far along it is. The filtering
            # is scan()'s own, so a file the core did not write is skipped
            # here for the same reason it is there.
            base = dumps.dump_dir(root)
            try:
                names = sorted(os.listdir(base))
            except OSError:
                return []
            names = [n for n in names if dumps.is_dump(n)
                     and os.path.isfile(os.path.join(base, n))]
            found = []
            for i, name in enumerate(names):
                if cancelled():
                    return found
                report(i, len(names), name)
                one = dumps.read(os.path.join(base, name))
                if one is not None:
                    found.append(one)
            return found

        self.working = Working(self, 1, on_cancel=self.dumpjob.cancel)
        self.working.step(0, 1, "reading the cartridge dumps")
        self.dumpjob.start(body, self._dumps_progress, self._dumps_read)

    def _dumps_progress(self, done: int, total: int, message: str) -> None:
        if self.working is not None:
            self.working.step(done, total, message)

    def _dumps_read(self, found, err) -> None:
        if self.working is not None:
            self.working.destroy()
            self.working = None
        if err is not None:
            messagebox.showerror("Cartridge dumps",
                                 f"the card could not be read.\n\n{err}")
            return
        if self.card is not None:
            DumpsDialog(self, self.card.root, self.catalog, found or [])
            self.refresh_shelf()

    def refresh_shelf(self) -> None:
        """The imported-dump count, after anything that could have changed it."""
        if not self.systems.exists(SHELF):
            return
        self.systems.item(SHELF, values=(len(self.shelf()),))
        sel = self.systems.selection()
        if sel and sel[0] == SHELF:
            self.show_shelf()

    # -------------------------------------------------- the dump surface --
    def refresh_dumps(self) -> None:
        """Recompute whether the dump surface belongs on screen.

        Called after anything that changes what the card is carrying: a scan,
        an install, an eject. The answer is never remembered between cards,
        because it is a fact about the card in the slot.
        """
        before = self.dumps_on
        self.dumps_on = core_mod.dumper_installed(self.survey)
        if self.dumps_on != before:
            self.apply_dumps_surface()

    def apply_dumps_surface(self) -> None:
        """Put the cartridge dump surface on screen, or take it off.

        Off means gone, not greyed. A disabled button is a promise that the
        thing exists and is temporarily unavailable, and for somebody who does
        not have the dumper core that is neither true nor useful.

        Applied live rather than at the next start, because a setting that
        needs a restart to take effect reads as one that did not work.
        """
        if self.dumps_on:
            self.dumps_btn.grid(row=0, column=2, padx=(0, 4))
            self.dumps_btn.state(["!disabled"] if self.card else ["disabled"])
            self.show_waiting()
            # Before the Open button, which is where _build put it: pack with
            # no anchor appends, and the bar would come back reordered.
            if self.cart_open is not None:
                self.copy_btn.pack(side="left", padx=(4, 0),
                                   before=self.cart_open)
                self.saves_btn.pack(side="left", padx=(4, 0),
                                    before=self.cart_open)
            else:
                self.copy_btn.pack(side="left", padx=(4, 0))
                self.saves_btn.pack(side="left", padx=(4, 0))
            if self.card is not None and not self.systems.exists(SHELF):
                self.systems.insert("", "end", iid=SHELF,
                                    text="Cartridge dumps",
                                    values=(len(self.shelf()),))
            return
        self.dumps_btn.grid_forget()
        self.waiting_label.grid_forget()
        self.copy_btn.pack_forget()
        self.saves_btn.pack_forget()
        if not self.systems.exists(SHELF):
            return
        showing = self.systems.selection() == (SHELF,)
        self.systems.delete(SHELF)
        if showing:
            # The pane still holds the dumps, and self.games still indexes
            # them. Emptying both is the only honest state: the category they
            # came from is gone.
            self.games = []
            self.view = None
            self.gamelist.delete(*self.gamelist.get_children())
            self.cheats.delete(*self.cheats.get_children())
            for b in (self.save_btn, self.source_btn, self.del_btn,
                      self.move_btn, self.copy_btn):
                b.state(["disabled"])
            self.source_label.config(text="")
            self.status.config(text="", foreground="#000")

    def show_waiting(self) -> None:
        """Put the count of what the card is carrying under the card path.

        Hidden when the card carries nothing, rather than shown saying zero:
        an empty output directory is the ordinary state of a card nobody has
        dumped to, and a line reporting it every time would be noise.
        """
        note = self.waiting.note() if self.card and self.dumps_on else ""
        if note:
            self.waiting_label.config(text=note)
            self.waiting_label.grid(row=1, column=1, columnspan=5, sticky="w",
                                    pady=(2, 0))
        else:
            self.waiting_label.grid_forget()

    def remove_from_library(self) -> None:
        """Delete an imported dump: the ROM, the original, and its saves.

        The card is not touched. A dump still on the card is what makes this
        recoverable, and the confirmation says whether that is the case,
        because "you can dump it again" and "this is the only copy" are not
        the same decision.
        """
        game = self.selected_game()
        root = library.path()
        if game is None or not root:
            return
        wanted = os.path.basename(game.path)
        index = library.load(root)
        row = next((r for r in index if r.rom == wanted), None)
        if row is None:
            messagebox.showinfo("Remove", f"{wanted} is not in the index.")
            return
        card_root = self.card.root if self.card else ""
        loss = dumps.what_removing_costs(root, row, card_root)
        parts = []
        if loss.rom:
            parts.append(f"the ROM, {os.path.basename(loss.rom)}")
        if loss.dump:
            parts.append(f"the original, {os.path.basename(loss.dump)}")
        if loss.saves:
            parts.append(f"{loss.saves} save read"
                         f"{'s' if loss.saves > 1 else ''}")
        if not parts:
            messagebox.showinfo("Remove", "There is nothing left to remove.")
            return
        fate = ("The cartridge dump is still on the card, so this can be "
                "imported again." if loss.on_card else
                "The card does not have this dump any more, so nothing else "
                "holds it. Removing it here ends it.")
        if not messagebox.askyesno(
                "Remove from library",
                f"{row.rom}\n\nThis deletes " + ", ".join(parts) + ".\n\n"
                + fate + "\n\nRemove it?",
                default="no", icon="warning"):
            return
        problems = dumps.forget_dump(root, row, index)
        if problems:
            messagebox.showwarning("Remove from library", "\n".join(problems))
        self.refresh_shelf()
        self.show_shelf()
        self.status.config(text=f"{row.rom} removed from the library",
                           foreground="#000")

    def cart_dir(self) -> str | None:
        """Where the selected cartridge's cheat file goes on the card."""
        cart = self.selected_game()
        return cart.subdir if isinstance(cart, carts.Cartridge) else None

    def selected_game(self):
        """The object for the selected row, or None if there is no live one.

        The pane and self.games are filled from two places, one of them a
        worker callback, so a row index is checked rather than trusted.
        """
        sel = self.gamelist.selection()
        if not sel:
            return None
        try:
            idx = int(sel[0])
        except ValueError:
            return None
        return self.games[idx] if 0 <= idx < len(self.games) else None

    def on_game(self, _evt=None) -> None:
        game = self.selected_game()
        retune_open(self.cart_open, self.cart_dir())
        if game is None:
            # A system heading, or nothing. Neither is something to act on.
            self.del_btn.state(["disabled"])
            self.move_btn.state(["disabled"])
            return
        is_cart = isinstance(game, carts.Cartridge)
        sel = self.systems.selection()
        on_shelf = bool(sel) and sel[0] == SHELF
        # Removing works on both lists now: a listed cartridge, or an
        # imported dump on the shelf.
        self.del_btn.state(["!disabled"] if is_cart or on_shelf
                           else ["disabled"])
        self.move_btn.state(["!disabled"] if is_cart else ["disabled"])
        self.copy_btn.state(
            ["!disabled"] if sel and sel[0] == SHELF and not is_cart
            and self.on_card(game) != "same" else ["disabled"])
        if self.play_btn is not None:
            # Playable whether or not it is on the card: the library copy is
            # what gets played, and putting it on the card is a separate act.
            self.play_btn.state(["!disabled"] if sel and sel[0] == SHELF
                                and not is_cart else ["disabled"])
        self.saves_btn.state(["!disabled"] if sel and sel[0] == SHELF
                             and not is_cart else ["disabled"])
        self.status.config(text="loading...", foreground="#000")

        def load():
            with timing.stage("load a game", game.name[:40]):
                return model.load(game)

        self.worker.submit(load, self._loaded, "load")

    def _loaded(self, view, err) -> None:
        if isinstance(err, cheatlib.MissingDatabase):
            # Not worth a dialog. This is the state a freshly downloaded build
            # starts in, it is not a failure, and the fix is one button away.
            self.status.config(
                text="no cheat database yet: press Update, at the bottom",
                foreground="#a00")
            return
        if err is not None:
            messagebox.showerror("Cheats", f"Could not read cheats:\n{err}")
            self.status.config(text="")
            return
        self.view = view
        self.refresh_cheats()
        self.source_btn.state(["!disabled"])
        self.save_btn.state(["!disabled"])

    def retune_save_button(self, v) -> None:
        removing = not v.enabled and os.path.exists(v.game.cht_path)
        self.save_btn.config(
            text="Remove from Pocket" if removing else "Send to Pocket",
            width=19 if removing else 16)

    def retune_meter(self, platform: str) -> None:
        """The code store is the core's, so its size follows the system."""
        if platform == self.meter_platform:
            return
        self.meter_platform = platform
        got = cheatfile.limits(platform)
        self.meter.set_limit(got[1] if got else None)

    def retune_applied(self, platform: str) -> None:
        """Show the Applied column only where there is more than one answer.

        Game Boy has two mechanisms and the difference between them is the
        whole of the cartridge warning, so it gets a column. PC Engine has one:
        every published cheat for it is a RAM poke, and a column repeating that
        down every row is noise dressed as information. The fact is stated once
        underneath instead, which is also where it belongs for a system whose
        codes cannot be read at all.
        """
        if platform == self.applied_platform:
            return
        self.applied_platform = platform
        ways = cheatfile.mechanisms(platform)
        if len(ways) >= 2:
            self.cheats.heading("how", text="Applied")
            self.cheats.column("how", width=64, minwidth=40, stretch=False)
            self.applied_note.config(text=(
                "Applied: written = the value is put into RAM each frame, so "
                "the game can still clamp it.  patched = the CPU's read is "
                "overridden."))
            return
        # Collapse it rather than leave an empty column with a heading over it.
        self.cheats.heading("how", text="")
        self.cheats.column("how", width=0, minwidth=0, stretch=False)
        if ways == ("poke",):
            self.applied_note.config(text=(
                "Every cheat here is written into RAM once a frame, so the "
                "game's own logic still sees the value and can clamp it. This "
                "system has no read-override codes."))
        else:
            self.applied_note.config(text=(
                "The codes for this system are carried exactly as written. "
                "Nothing here claims to know what any of them does."))

    def refresh_cheats(self) -> None:
        v = self.view
        with timing.stage("clear the cheat pane"):
            self.cheats.delete(*self.cheats.get_children())
        if v is None:
            self.meter.set(0)
            return
        if v.source:
            marks = []
            if cheatlib.is_local(v.source):
                marks.append("yours")
            if v.pinned:
                # otherwise a remembered choice silently beats a file you just
                # wrote, and there is nothing on screen to say why
                marks.append("pinned")
            mark = ("  (" + ", ".join(marks) + ")") if marks else ""
            label = "source: " + os.path.basename(v.source) + mark
        else:
            label = "no matching cheat file found"
        self.source_label.config(text=label)
        with timing.stage("fill the cheat pane", f"{len(v.entries)} rows"):
            for i, e in enumerate(v.entries):
                tags = []
                if not e.in_library:
                    tags.append("extra")
                if e.placeholder:
                    tags.append("dead")
                desc = e.desc + ("   (already installed)" if not e.in_library
                                 else "")
                self.cheats.insert("", "end", iid=str(i),
                                   text=TICK if e.enabled else UNTICK,
                                   values=(desc, e.applied,
                                           e.summary or "no usable code"),
                                   tags=tuple(tags))
        self.update_status()

    def update_status(self) -> None:
        v = self.view
        if v is None:
            self.meter.set(0)
            return
        self.retune_meter(v.platform)
        self.retune_applied(v.platform)
        codes = sum(len(e.group.codes) for e in v.enabled)
        self.meter.set(codes)
        written, patched = v.applied_counts
        msg = f"{len(v.enabled)} of {len(v.entries)} cheats on"
        if len(cheatfile.mechanisms(v.platform)) >= 2 and (written or patched):
            msg += f" ({written} written, {patched} patched)"
        if not cheatfile.decoded(v.platform):
            msg += "   codes carried as written; nothing reads them yet"
        elif not core_mod.released(v.platform, self.releases):
            # Readable codes and a correct file, and still nothing on the
            # handheld that will act on it. Worth saying, since everything
            # else on screen looks exactly like a system that works.
            msg += (f"   the {self.platform_name(v.platform)} core is not "
                    "released yet")
        # Ticking nothing and sending is how you take cheats off a game, and
        # it works, but "Send to Pocket" does not read like a deletion. The
        # button says which of the two it is about to do.
        self.retune_save_button(v)

        problems = list(v.problems)
        # On a cartridge you cannot check the revision, and the two kinds of
        # code fail differently when it is wrong: a Game Genie patch carries a
        # compare byte and simply never fires, while a GameShark code is a real
        # write to an address that may hold something else entirely.
        if isinstance(v.game, carts.Cartridge) and written:
            problems.append(f"{written} written codes: unverifiable on a cartridge")
        if problems:
            msg += "   " + "; ".join(problems)
        self.status.config(text=msg, foreground="#a00" if problems else "#000")

    # --------------------------------------------------------------- editing --
    def on_click(self, event) -> None:
        if self.view is None:
            return
        row = self.cheats.identify_row(event.y)
        if not row:
            return
        entry = self.view.entries[int(row)]
        if entry.placeholder:
            self.status.config(
                text="that cheat has no usable code (a XX-style modifier)",
                foreground="#a00")
            return
        entry.enabled = not entry.enabled
        self.cheats.item(row, text=TICK if entry.enabled else UNTICK)
        self.update_status()

    def set_all(self, on: bool) -> None:
        if self.view is None:
            return
        for e in self.view.entries:
            if not e.placeholder:
                e.enabled = on
        self.refresh_cheats()

    def change_source(self) -> None:
        if self.view is None:
            return
        Chooser(self, self.view)

    def save(self) -> None:
        v = self.view
        if v is None:
            return
        problems = v.problems
        if problems and not messagebox.askyesno(
                "Cheats", "\n".join(problems) + "\n\nWrite anyway?"):
            return
        # An empty selection deletes the file. That is correct, since the file
        # is the state, but it is a deletion and "Send to Pocket" does not
        # read like one: ask.
        if not v.enabled and os.path.exists(v.game.cht_path):
            if not messagebox.askyesno(
                    "Remove from Pocket",
                    f"Remove {os.path.basename(v.game.cht_path)} from the card?"
                    "\n\nNothing is ticked, so this game will have no cheats."
                    " A copy is kept beside it as .cht.bak."):
                return
        def write():
            result = v.save()
            if self.card:
                self.card.sync()      # can take seconds on a slow card
            return result

        self.save_btn.state(["disabled"])
        self.status.config(
            text="removing from the card..." if not v.enabled
                 else "writing to the card...", foreground="#000")
        self.worker.submit(write, self._saved, "save")

    def _saved(self, result, err) -> None:
        self.save_btn.state(["!disabled"])
        if err is not None:
            messagebox.showerror("Cheats", f"Could not write:\n{err}")
            self.status.config(text="")
            return
        cheats, codes, removed = result
        sel = self.gamelist.selection()
        if sel:
            self.gamelist.item(sel[0], values=(cheats if cheats else "",))
        self.status.config(
            text="removed the cheat file from the card (.cht.bak kept)"
                 if removed else
                 f"wrote {cheats} cheats / {codes} codes to the card",
            foreground="#060")


class Working(tk.Toplevel):
    """Modal "reading the card" window, shown while the card is being read.

    Reading a cold card takes upwards of fifteen seconds and no amount of
    rearranging makes that work go away: it is the card, over USB, on exFAT,
    with nothing cached. Mounting it before the app starts only feels fast
    because the desktop has already walked it on your behalf.

    So the wait is stated rather than hidden. Filling the panes as each system
    arrived was worse in practice: the window looked ready while half of it
    was not, and clicking into it got you a pane that changed under you.
    """

    def __init__(self, app, total: int, on_cancel=None) -> None:
        super().__init__(app)
        self.title("Reading the card")
        self.transient(app.winfo_toplevel())
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", lambda: None)

        body = ttk.Frame(self, padding=16)
        body.grid(sticky="nsew")
        self.label = ttk.Label(body, width=46, anchor="w",
                               text="Reading the card...")
        self.label.grid(row=0, column=0, sticky="w")
        self.bar = ttk.Progressbar(body, length=320, mode="determinate",
                                   maximum=max(1, total))
        self.bar.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.note = ttk.Label(body, foreground="#666", anchor="w", width=46,
                              text="A card that has just been inserted is slow "
                                   "to read the first time.")
        self.note.grid(row=2, column=0, sticky="w", pady=(8, 0))
        if on_cancel is not None:
            ttk.Button(body, text="Stop", command=on_cancel).grid(
                row=3, column=0, sticky="e", pady=(12, 0))

        self.update_idletasks()
        self.centre(app.winfo_toplevel())
        self.grab_set()

    def centre(self, over) -> None:
        try:
            x = over.winfo_rootx() + (over.winfo_width() - self.winfo_width()) // 2
            y = over.winfo_rooty() + (over.winfo_height() - self.winfo_height()) // 3
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:                                    # noqa: BLE001
            pass

    def step(self, done: int, total: int, message: str) -> None:
        self.bar.config(maximum=max(1, total), value=done)
        if message:
            self.label.config(text=message)

    def close(self) -> None:
        try:
            self.grab_release()
        except Exception:                                    # noqa: BLE001
            pass
        self.destroy()


class CartDialog(tk.Toplevel):
    """Name a cartridge and say which system it is for.

    Its own window rather than simpledialog.askstring, because the system is
    not optional detail: it decides which folder on the card the cheat file
    goes in, and the core's file browser opens on that folder.
    """

    def __init__(self, app, preset: str, name_of) -> None:
        super().__init__(app)
        self.result: tuple[str, str] | None = None
        self.title("Add cartridge")
        self.transient(app)
        self.resizable(False, False)
        self.columnconfigure(0, weight=1)

        body = ttk.Frame(self, padding=10)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        ttk.Label(body, justify="left", text=(
            "Name it exactly as the ROM is named, including the region and\n"
            "revision tags. That name is the whole of the matching:\n\n"
            "    Legend of Zelda, The - Link's Awakening DX (USA, Europe) (Rev 2)"
        )).grid(row=0, column=0, sticky="w")

        self.entry = ttk.Entry(body, width=64)
        self.entry.grid(row=1, column=0, sticky="ew", pady=(8, 10))

        systems = ttk.LabelFrame(body, text="System", padding=6)
        systems.grid(row=2, column=0, sticky="ew")
        self.platform = tk.StringVar(value=preset)
        for i, pid in enumerate(carts.PLATFORMS):
            ttk.Radiobutton(systems, text=name_of(pid), value=pid,
                            variable=self.platform).grid(row=0, column=i,
                                                         padx=(0, 12), sticky="w")
        ttk.Label(body, foreground="#666", wraplength=440, justify="left", text=(
            "This decides which folder the cheat file goes in on the card. "
            "Get it wrong and the core's Load Cheats browser will not be "
            "looking where the file is."
        )).grid(row=3, column=0, sticky="w", pady=(8, 0))

        row = ttk.Frame(body)
        row.grid(row=4, column=0, sticky="e", pady=(12, 0))
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="right",
                                                                  padx=(6, 0))
        ttk.Button(row, text="Add", command=self.ok).pack(side="right")

        self.bind("<Return>", lambda _e: self.ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.entry.focus_set()
        self.grab_set()
        self.wait_window(self)

    def ok(self) -> None:
        name = self.entry.get().strip()
        if not name:
            self.entry.focus_set()
            return
        self.result = (name, self.platform.get())
        self.destroy()


class CoresDialog(tk.Toplevel):
    """Every core this app writes for, what the card has, and what to install.

    This was a single "Install core" button and a yes/no box listing whatever
    the app had decided to write. That worked while there was one repository
    and two cores that always shipped together. There are four cores now, from
    three repositories, released at different times and at different versions,
    and one of them has no release at all - so "install the core" stopped being
    one question with one answer.

    A row per core, ticked the same way cheats are ticked in the main window
    and for the same reason: this is a list of things to pick, and the app
    already has a way of showing one. A row that cannot be picked is greyed and
    says why, which is the case the old button could only express by going grey
    itself without explaining.
    """

    def __init__(self, app, survey, rels: dict | None) -> None:
        super().__init__(app)
        self.result: list | None = None
        self.title("Pocket cores")
        self.transient(app)
        self.resizable(False, False)
        self.columnconfigure(0, weight=1)

        body = ttk.Frame(self, padding=12)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        ttk.Label(body, text="Cores on this card").grid(
            row=0, column=0, sticky="w")
        ttk.Label(body, foreground="#666",
                  text=survey.root if survey else "no card").grid(
            row=1, column=0, sticky="w", pady=(1, 8))

        cols = ("card", "avail")
        self.tree = ttk.Treeview(body, columns=cols, show="tree headings",
                                 selectmode="none", height=len(core_mod.CORES))
        self.tree.heading("#0", text="Core")
        self.tree.heading("card", text="On the card")
        self.tree.heading("avail", text="Available")
        self.tree.column("#0", width=210, stretch=False)
        self.tree.column("card", width=150, stretch=False, anchor="center")
        self.tree.column("avail", width=185, stretch=False, anchor="center")
        self.tree.grid(row=2, column=0, sticky="ew")
        # Same three meanings the cheat list gives these: unavailable, and
        # worth drawing the eye to.
        self.tree.tag_configure("dead", foreground="#999")
        self.tree.tag_configure("behind", foreground="#0a6")
        self.tree.bind("<Button-1>", self.toggle)

        self.rows: dict[str, list] = {}
        behind = set()
        if survey and rels:
            behind = {c.id for c in core_mod.outdated(survey.versions, rels)}

        for c in core_mod.CORES:
            have = survey.versions.get(c.id) if survey else None
            rel = core_mod.release_for(c, rels) if rels else None
            asset = core_mod.asset_for(c, rels) if rels else None

            # Why a row cannot be picked, in the column that would otherwise be
            # blank. "No release yet" and "offline" are different things, and
            # the difference decides whether waiting will help.
            if asset is not None:
                # The version, not the tag. The two columns exist to be
                # compared, and "1.4.0-cheats.9" against "v1.4.0-cheats.9"
                # reads as a difference when there is none.
                avail = rel["version"]
            elif rels is None:
                avail = "offline"
            elif c.repo is None:
                avail = "not released yet"
            else:
                avail = "no release yet"

            # Ticked by default when it is behind, because it is what the
            # button used to do and what anyone opening this almost always
            # wants. Never a core already at the released version: putting one
            # back is a repair, so it is offered rather than assumed.
            on = c.id in behind and asset is not None
            tag = "dead" if asset is None else ("behind" if on else "")
            self.tree.insert("", "end", iid=c.id,
                             text=f"  {TICK if on else UNTICK}  {c.title}",
                             values=(have or "not installed", avail),
                             tags=(tag,) if tag else ())
            self.rows[c.id] = [c, on, asset]

        self.note = ttk.Label(body, foreground="#666", wraplength=560,
                              justify="left", text="")
        self.note.grid(row=3, column=0, sticky="w", pady=(10, 0))

        row = ttk.Frame(body)
        row.grid(row=4, column=0, sticky="e", pady=(14, 0))
        ttk.Button(row, text="Close", command=self.destroy).pack(
            side="right", padx=(6, 0))
        self.go = ttk.Button(row, text="Install", command=self.ok)
        self.go.pack(side="right")

        # After the button exists: the note says what Install would do and
        # turns it off when that is nothing, so it cannot run before there is
        # a button to turn off.
        self.retune_note()

        self.bind("<Escape>", lambda _e: self.destroy())
        self.go.focus_set()
        self.grab_set()
        self.wait_window(self)

    # ------------------------------------------------------------- behaviour --
    def chosen(self) -> list:
        return [c for c, on, asset in self.rows.values() if on and asset]

    def toggle(self, event) -> None:
        """Click handler: work out which row was hit, then act on it."""
        iid = self.tree.identify_row(event.y)
        if iid:
            self.toggle_row(iid)

    def toggle_row(self, iid: str) -> None:
        row = self.rows[iid]
        c, on, asset = row
        if asset is None:
            self.note.config(foreground="#a00", text=(
                f"There is nothing to install for {c.title} yet. "
                f"{self.tree.set(iid, 'avail').capitalize()}."))
            return
        row[1] = on = not on
        self.tree.item(iid, text=f"  {TICK if on else UNTICK}  {c.title}",
                       tags=("behind",) if on else ())
        self.retune_note()

    def retune_note(self) -> None:
        """One line about what pressing Install would do, not a wall of text.

        The paragraph this replaced said the same three things whatever was on
        screen, so it was read once and then never again.
        """
        picked = self.chosen()
        self.go.state(["!disabled"] if picked else ["disabled"])
        if not picked:
            self.note.config(foreground="#666", text=(
                "Nothing selected. Tick a core to install or reinstall it; "
                "reinstalling is worth doing if one was interrupted mid-copy, "
                "because it reads as the right version and does not run."))
            return
        names = ", ".join(f"Cores/{c.id}" for c in picked)
        self.note.config(foreground="#666", text=(
            f"Install writes {names} and the platform entries that go with "
            "them. Your ROMs, saves, cheat files and boot ROMs are not "
            "touched. Eject the card from the main window afterwards, before "
            "pulling it out."))

    def ok(self) -> None:
        picked = self.chosen()
        if not picked:
            return
        self.result = picked
        self.destroy()


class RomsDialog(tk.Toplevel):
    """Every boot ROM the installed cores need, and where each one goes.

    This was a message box, which was the right amount of work for one line of
    text and the wrong container for a table. A message box renders in a
    proportional font, so the spaces that made the columns made ragged text
    instead; it wrapped to its own width, so a long entry broke wherever Tk
    chose and paths broke mid-path; nothing separated one entry from the next,
    so four boot ROMs read as one paragraph; and the path, the only text in it
    anybody has to act on, could not be selected. The Cores dialog had the same
    complaint made of it and answered it with a Treeview, so this answers it
    the same way: a row per boot ROM, columns that are columns, and the prose
    wrapped here at a width we chose rather than at one Tk picked.

    It reports and never repairs. A boot ROM is copyrighted console code; this
    app names it, sizes it, says where it goes, and will do nothing else.
    """

    # Wide enough for the longest line of prose without a second thought, and
    # narrow enough that the eye does not lose the start of the next line.
    PROSE = 96

    def __init__(self, app, survey: core_mod.Survey) -> None:
        super().__init__(app)
        self.survey = survey
        self.title("Boot ROMs")
        self.transient(app)
        self.resizable(False, False)
        self.columnconfigure(0, weight=1)

        body = ttk.Frame(self, padding=12)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        ttk.Label(body, text="Boot ROMs for the cores on this card").grid(
            row=0, column=0, sticky="w")
        ttk.Label(body, foreground="#666", text=survey.root).grid(
            row=1, column=0, sticky="w", pady=(1, 8))

        cols = ("state", "size", "where")
        self.tree = ttk.Treeview(body, columns=cols, show="tree headings",
                                 selectmode="browse",
                                 height=max(len(survey.roms), 1))
        self.tree.heading("#0", text="Boot ROM")
        self.tree.heading("state", text="On the card")
        self.tree.heading("size", text="Size wanted")
        self.tree.heading("where", text="Directory")
        self.tree.column("#0", width=190, stretch=False)
        self.tree.column("state", width=170, stretch=False, anchor="center")
        self.tree.column("size", width=110, stretch=False, anchor="center")
        # The one column that stretches. A long card path widens the dialog
        # through the label under the table, and a fixed last column answers
        # that with dead white space to the right of it.
        self.tree.column("where", width=260, stretch=True)
        self.tree.grid(row=2, column=0, sticky="ew")
        # The same three meanings the cheat list and the Cores dialog give
        # these colours: a fault, something worth drawing the eye to, and a row
        # there is nothing to do about. A missing file and a file of the wrong
        # size fail differently - one is not there, the other is there and
        # wrong, and the second is the one people stare at without seeing - so
        # they do not share a colour.
        # Both of these stop the core running, so neither is green. Green is
        # what CoresDialog uses for an update being available, which is a state
        # worth acting on and not a fault; borrowing it here would say a boot
        # ROM of the wrong size is fine. Amber separates the two failures
        # without claiming one of them is harmless.
        self.tree.tag_configure("missing", foreground="#a00")
        self.tree.tag_configure("wrong", foreground="#b35c00")
        self.tree.tag_configure("ok", foreground="#999")

        # iid -> the whole path to copy. For a file that is there, where it was
        # found, which is not always where we would have put it: the core's own
        # directory counts too. For one that is not, where it should go.
        self.paths: dict[str, str] = {}
        for n, r in enumerate(survey.roms):
            iid = f"{r.core.id}:{r.rom.filename}:{n}"
            size = f"{r.rom.size} bytes" if r.rom.size else "any size"
            if r.path is None:
                state, tag = "missing", "missing"
                full = os.path.join(survey.root, r.where)
            elif r.wrong_size:
                # Both numbers, because "wrong size" on its own leaves the file
                # being compared against nothing.
                state, tag = f"wrong size: {r.size} bytes", "wrong"
                full = r.path
            else:
                state, tag = "present", "ok"
                full = r.path
            rel = os.path.relpath(full, survey.root)
            self.paths[iid] = full
            self.tree.insert("", "end", iid=iid, text=f"  {r.rom.filename}",
                             values=(state, size, os.path.dirname(rel)),
                             tags=(tag,))
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.show_path())

        # Wrapped here rather than by Tk. wraplength hands the break point to
        # the widget's width, which is how the message box ended up breaking a
        # path in half; a fixed column is a decision that survives resizing,
        # theming and a different font.
        self.prose = ttk.Label(body, foreground="#666", justify="left",
                               text=textwrap.fill(self.advice(), self.PROSE))
        self.prose.grid(row=3, column=0, sticky="w", pady=(10, 0))

        self.note = ttk.Label(body, foreground="#666", text="")
        self.note.grid(row=4, column=0, sticky="w", pady=(6, 0))

        row = ttk.Frame(body)
        row.grid(row=5, column=0, sticky="e", pady=(14, 0))
        ttk.Button(row, text="Close", command=self.destroy).pack(
            side="right", padx=(6, 0))
        self.copy_btn = ttk.Button(row, text="Copy path",
                                   command=self.copy_path)
        self.copy_btn.pack(side="right")
        # Beside Copy path rather than replacing it: a sandboxed or headless
        # build has no file manager to call, and the path still has to be
        # gettable there. Greyed rather than absent, because the answer changes
        # as the selection moves and a button that came and went would be worse
        # than one that goes dull. Assets/<platform>/common/ may not exist for
        # a missing boot ROM, and looking at a directory must never be what
        # creates it - openable() is what refuses that.
        self.open_btn = open_button_for(row, lambda: holding(self.selected()))
        if self.open_btn is not None:
            self.open_btn.pack(side="right", padx=(0, 4))

        # A selection to start with, so Copy path means something without a
        # click, and the first thing wrong rather than the first thing listed.
        bad = [i for i in self.tree.get_children()
               if self.tree.tag_has("missing", i)
               or self.tree.tag_has("wrong", i)]
        first = (bad or list(self.tree.get_children()))
        if first:
            self.tree.selection_set(first[0])
            self.tree.focus(first[0])
        self.show_path()

        self.bind("<Escape>", lambda _e: self.destroy())
        self.copy_btn.focus_set()
        self.grab_set()
        self.wait_window(self)

    # ------------------------------------------------------------- behaviour --
    def advice(self) -> str:
        """The prose above the table: what these are and why we do not ship one.

        Two sentences that never change, and one that does, because "four are
        present" and "one of them is not there" want different next steps and
        the difference is the reason the dialog opened.
        """
        text = ("A boot ROM is the code the console runs before the game does. "
                "It is copyrighted, so it is not in the core and it is not in "
                "this app: dump it from your own hardware or supply your own "
                "copy. ")
        if not self.survey.roms:
            return text + ("None of the cores on this card need one, so there "
                           "is nothing to put anywhere.")
        if not self.survey.problems():
            return text + "Every one of these is where the core looks for it."
        return text + ("Put each missing file in the directory listed for it, "
                       "under the card. The core will not start a game "
                       "without it.")

    def selected(self) -> str | None:
        sel = self.tree.selection()
        return self.paths[sel[0]] if sel else None

    def show_path(self) -> None:
        """The whole path of the selected row, unbroken, under the table."""
        path = self.selected()
        self.copy_btn.state(["!disabled"] if path else ["disabled"])
        retune_open(getattr(self, "open_btn", None), holding(path))
        self.note.config(foreground=QUIET, text=path or "")

    def copy_path(self) -> None:
        """Put the selected path on the clipboard.

        The message box could not do this, and the path was the only thing in
        it worth having. It also covers the cases where showing a directory in
        a file manager is not available at all - a Flatpak or Snap build, a
        machine being driven over ssh - so it stays even once Open exists.
        """
        path = self.selected()
        if path is None:
            return
        self.clipboard_clear()
        self.clipboard_append(path)
        self.note.config(foreground="#060", text=f"copied  {path}")


DATOMATIC = "https://datomatic.no-intro.org/"


def download_dirs() -> list[str]:
    """Where a browser is likely to have put a download, most likely first.

    XDG_DOWNLOAD_DIR is asked for first because a desktop that has been told
    where downloads go is telling the truth, and ~/Downloads is only the
    default it usually holds. Both are checked because the setting is often
    absent and the directory is there anyway.
    """
    out = []
    xdg = os.environ.get("XDG_DOWNLOAD_DIR")
    if xdg:
        out.append(os.path.expanduser(xdg))
    for name in ("Downloads", "Desktop"):
        out.append(os.path.expanduser(os.path.join("~", name)))
    seen, keep = set(), []
    for d in out:
        real = os.path.abspath(d)
        if real not in seen and os.path.isdir(real):
            seen.add(real)
            keep.append(real)
    return keep


def native_open(parent, title: str) -> str | None:
    """A file chooser, preferring the desktop's own over Tk's.

    Tk's X11 chooser is its own creation and looks like nothing else on the
    machine. Where the desktop ships a real one -- kdialog on KDE, zenity
    almost everywhere else -- handing the job over costs one subprocess and
    gets a dialog that behaves the way every other dialog on that desktop
    does. Anything unexpected falls back to Tk rather than to nothing.
    """
    if sys.platform not in ("win32", "darwin"):
        for cmd, args in (("kdialog", ["--getopenfilename",
                                       os.path.expanduser("~"),
                                       "*.zip *.dat *.xml|DAT files"]),
                          ("zenity", ["--file-selection", "--title", title,
                                      "--file-filter=DAT files | "
                                      "*.zip *.dat *.xml",
                                      "--file-filter=Every file | *"])):
            if shutil.which(cmd) is None:
                continue
            try:
                done = subprocess.run([cmd, *args], capture_output=True,
                                      text=True, timeout=300)
            except (OSError, subprocess.SubprocessError):
                break        # it is there and it did not work; use Tk's
            if done.returncode == 0 and done.stdout.strip():
                return done.stdout.strip()
            if done.returncode in (1, 2):
                return None  # the user cancelled, which is an answer
            break
    return filedialog.askopenfilename(
        parent=parent, title=title,
        filetypes=[("A DAT, or the zip it came in", "*.zip *.dat *.xml"),
                   ("Every file", "*")]) or None


class DumpsDialog(tk.Toplevel):
    """The cartridge dumps on a card, as a list you work in.

    The dumper writes a flat pile of files named from a fixed header offset,
    so `ZELDA.gb` is Link's Awakening and `ZELDA_DIN__AZ7E.gbc` is Oracle of
    Seasons with four bytes of manufacturer code stuck to it. This window is
    where those become names that mean something.

    It is a list and not a wizard. The first version asked about one dump at a
    time behind a chain of modals, which over a card of thirty-two dumps was a
    hundred and thirty clicks, and it showed only what was still on the card -
    so every answered dump vanished and a card that had imported perfectly looked
    like one where nothing had happened. Tick what you want and press the
    button that says what will happen to it. Nothing is written for a row that
    is not ticked, which is the part of "nothing is bulk" that was worth
    keeping: the app still never decides on its own what to do with a dump.

    Every state has a way out of it. That was the other fault: once a SHA-1 was
    in the index the verdict was IMPORTED, IMPORTED was not actionable, and a dump
    whose card copy was still there could never be cleared - the window called
    it finished and greyed everything. Turning one down was worse, because
    REJECTED did the same and there was no way to change your mind.
    """

    STATE = {
        dumps.Verdict.IMPORT:         ("ready", "ready"),
        dumps.Verdict.COLLIDES:     ("name taken", "lesser"),
        dumps.Verdict.IMPORTED:     ("in the library", "idle"),
        dumps.Verdict.SAVE_ONLY:    ("save to keep", "ready"),
        dumps.Verdict.REJECTED:     ("ignored", "idle"),
        dumps.Verdict.MISSING:      ("imported copy gone", "lesser"),
        dumps.Verdict.UNIDENTIFIED: ("not in any DAT", "lesser"),
        dumps.Verdict.UNREADABLE:   ("cannot be read", "fault"),
    }

    def __init__(self, app, card_root: str, catalog, found=None) -> None:
        super().__init__(app)
        self.app = app
        self.card_root = card_root
        self.catalog = catalog
        # Hashed once, by whoever opened this window, and kept. Re-reading the
        # card after every answer would cost the whole scan again -- 28 seconds
        # for a real card of 32 dumps over USB -- and nothing an answer changes
        # is on the card: the bytes of a dump do not move because it was imported.
        self.found = list(dumps.scan(card_root) if found is None else found)
        self.proposals: dict[str, dumps.Proposal] = {}
        # Saves with no dump of their own on the card, keyed by path like the
        # proposals are, so one tick set covers both kinds of row.
        self.stray: dict[str, tuple] = {}
        self.index: library.Index | None = None
        self.title("Cartridge dumps")
        self.transient(app)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        body = ttk.Frame(self, padding=12)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        ttk.Label(body, text="Dumps on this card").grid(
            row=0, column=0, sticky="w")
        ttk.Label(body, foreground=QUIET, text=dumps.dump_dir(card_root)).grid(
            row=1, column=0, sticky="w", pady=(1, 8))

        cols = ("state", "name", "save", "cheats")
        self.tree = ttk.Treeview(body, columns=cols, show="tree headings",
                                 selectmode="browse", height=12)
        self.tree.heading("#0", text="On the card")
        self.tree.heading("state", text="Status")
        self.tree.heading("name", text="Name in the library")
        self.tree.heading("save", text="Save")
        self.tree.heading("cheats", text="Cheats")
        self.tree.column("#0", width=210, stretch=False)
        self.tree.column("state", width=120, stretch=False, anchor="center")
        self.tree.column("name", width=340, stretch=True)
        self.tree.column("save", width=90, stretch=False, anchor="center")
        self.tree.column("cheats", width=210, stretch=False)
        self.tree.grid(row=2, column=0, sticky="nsew")
        self.tree.tag_configure("fault", foreground=FAULT)
        self.tree.tag_configure("lesser", foreground=LESSER)
        self.tree.tag_configure("ready", foreground=READY)
        self.tree.tag_configure("idle", foreground=IDLE)
        self.tree.bind("<Button-1>", self.click)
        self.tree.bind("<<TreeviewSelect>>", self.on_pick)

        self.lib_label = ttk.Label(body, foreground=QUIET, justify="left")
        self.lib_label.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.dat_label = ttk.Label(body, foreground=QUIET, justify="left")
        self.dat_label.grid(row=4, column=0, sticky="w", pady=(2, 0))
        self.detail = ttk.Label(body, foreground=QUIET, justify="left")
        self.detail.grid(row=5, column=0, sticky="w", pady=(8, 0))

        row = ttk.Frame(body)
        row.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(row, text="Close", command=self.destroy).pack(side="right")
        # Named for what they do to what is ticked. "File it" said nothing:
        # file it where, as what?
        self.add_btn = ttk.Button(row, text="Add to library", width=15,
                                  command=self.add_ticked, state="disabled")
        self.add_btn.pack(side="right", padx=(0, 4))
        self.saves_btn = ttk.Button(row, text="Saves...", width=9,
                                    command=self.show_saves, state="disabled")
        self.saves_btn.pack(side="right", padx=(0, 4))
        self.clear_btn = ttk.Button(row, text="Clear from card", width=15,
                                    command=self.clear_ticked,
                                    state="disabled")
        self.clear_btn.pack(side="right", padx=(0, 4))
        self.cheat_btn = ttk.Button(row, text="Cheats...", width=10,
                                    command=self.show_cheat, state="disabled")
        self.cheat_btn.pack(side="right", padx=(0, 4))
        self.no_btn = ttk.Button(row, text="Ignore", width=13,
                                 command=self.set_ignored, state="disabled")
        self.no_btn.pack(side="right", padx=(0, 4))
        ttk.Button(row, text="All", width=5,
                   command=lambda: self.tick_all(True)).pack(side="left")
        ttk.Button(row, text="None", width=6,
                   command=lambda: self.tick_all(False)).pack(
            side="left", padx=(4, 0))
        ttk.Button(row, text="Add DAT...", width=11,
                   command=self.add_dat).pack(side="left", padx=(12, 0))
        ttk.Button(row, text="Library...", width=11,
                   command=self.pick_library).pack(side="left", padx=(4, 0))
        self.lib_open = open_button_for(row, library.path)
        if self.lib_open is not None:
            self.lib_open.pack(side="left", padx=(4, 0))

        self.refill()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.wait_window(self)

    # ------------------------------------------------------------- the list --
    def root_dir(self) -> str:
        return library.path() or ""

    def refill(self, keep: set[str] | None = None) -> None:
        """Recompute every proposal and redraw. Writes nothing.

        The ticks are carried across, because a redraw happens after every
        action and losing the selection each time would make working through a
        card by hand impossible.
        """
        keep = self.ticked() if keep is None else keep
        self.tree.delete(*self.tree.get_children())
        self.proposals.clear()
        self.stray.clear()
        root = self.root_dir()
        index = library.load(root) if root else None
        # Held for save_cell(), which is called once per row and must not
        # re-read the index each time.
        self.index = index

        for dump in self.found:
            identity = dumps.identify(dump, self.catalog)
            prop = (dumps.propose(dump, identity, root, index) if root
                    else dumps.Proposal(dump=dump, identity=identity, root="",
                                        rom_name=identity.name))
            self.proposals[dump.path] = prop
            state, tag = self.STATE.get(prop.verdict, ("?", "idle"))
            # One label for the only thing that matters once the library
            # holds the bytes: the file on the card is a second copy of them
            # and can go. Which verdict got it there -- imported cleanly, or
            # imported and later refused -- is a distinction about the past,
            # and putting it in this column buried the answer to "can I
            # delete this yet".
            if prop.verdict is dumps.Verdict.SAVE_ONLY:
                pass            # the save outranks the ROM being redundant
            elif self.clearable(prop):
                state, tag = "duplicate", "ready"
            tick = TICK if dump.path in keep else UNTICK
            self.tree.insert("", "end", iid=dump.path,
                             text=f"{tick} {dump.name}",
                             values=(state,
                                     prop.rom_name or identity.name or "",
                                     self.save_cell(prop),
                                     self.cheat_of(prop).name or "-"),
                             tags=(tag,))
        # Saves whose cartridge is imported but whose own bytes are not. They
        # have no dump on the card to hang off -- clearing the ROM is the
        # ordinary end of importing one -- so without a row of their own they
        # are unreachable, and the line on the main window telling you to come
        # here to import them is a lie.
        for save, row, held in (dumps.matched_card_saves(self.card_root, root,
                                                          index)
                                if root and index else []):
            self.stray[save.path] = (save, row)
            tick = TICK if save.path in keep else UNTICK
            state = "duplicate" if held else "save to import"
            self.tree.insert("", "end", iid=save.path,
                             text=f"{tick} {save.name}",
                             values=(state, row.rom or "",
                                     f"{save.size:,} bytes", "-"),
                             tags=("ready",))

        rows = list(self.tree.get_children())
        if rows and not self.tree.selection():
            self.tree.selection_set(rows[0])
            self.tree.focus(rows[0])
        self.describe_library(root, index)
        self.dat_label.config(text=dumps.dat_note(self.catalog))
        retune_open(self.lib_open, root)
        self.on_pick()

    def describe_library(self, root: str, index) -> None:
        """Where the library is and how much is in it.

        The count is the whole point of the line. Without it a window that had
        just imported thirty-three dumps looked exactly like one that had
        imported nothing, because those rows are gone from the card and this list
        is about the card.
        """
        if not root:
            self.lib_label.config(
                text="No library chosen yet. Press Library... to choose "
                     "where dumps are kept. Nothing is added until you do.",
                foreground=LESSER)
            return
        held = len(index) if index is not None else 0
        self.lib_label.config(
            text=f"Library: {root}   -   holds {held} "
                 f"dump{'' if held == 1 else 's'}",
            foreground=QUIET)

    def save_cell(self, prop) -> str:
        """What the Save column says for one dump.

        Three states worth telling apart: no save was read off this cartridge,
        one is sitting on the card waiting, and one is already in the library.
        A cartridge with no save RAM at all is the ordinary case and says
        nothing rather than "none", which would read as a failure to find one.
        """
        root = self.root_dir()
        row = self.index.get(prop.dump.sha1) if self.index else None
        held = len(dumps.cartsave_reads(root, row)) if root and row else 0
        if prop.save is None:
            return f"{held} kept" if held else ""
        if held:
            return f"on card, {held} kept"
        return "on card"

    def cheat_of(self, prop) -> dumps.Cheat:
        """The cheat file this dump maps to, pinned or matched."""
        return dumps.cheat(prop.identity, self.rom_path(prop))

    def rom_path(self, prop) -> str | None:
        """Where the canonical copy is, or would be. The cheat override key.

        Keyed on the canonical name whether or not the dump has been imported
        yet, so a choice made before adding it still applies afterwards. That
        is only safe because the name is canonical: the core's own names
        collide and this one cannot.
        """
        root = self.root_dir()
        if not root or not prop.rom_name:
            return None
        return os.path.join(library.roms_dir(root), prop.rom_name)

    def clearable(self, prop) -> bool:
        """The library holds this dump, and the card still has its own copy.

        Deliberately not a question about the verdict. Asking for IMPORTED was
        the same dead end in a smaller form: a dump that is in the library and
        has since been ignored reads as REJECTED, and the card copy - which
        is provably redundant, because cart-dumps holds the same bytes - could
        not be cleared. What makes this safe is the library actually holding
        the file, which is what is checked, and the byte comparison that runs
        immediately before the delete.
        """
        if prop.row is None:
            return False
        kept = prop.row.dump_path(self.root_dir())
        return bool(kept) and os.path.exists(kept) \
            and os.path.exists(prop.dump.path)

    # ------------------------------------------------------------ selecting --
    def click(self, evt) -> None:
        """A click in the first column is a tick; anywhere else selects."""
        iid = self.tree.identify_row(evt.y)
        if not iid or self.tree.identify_region(evt.x, evt.y) == "heading":
            return
        if self.tree.identify_column(evt.x) == "#0":
            self.flip(iid)

    def flip(self, iid: str) -> None:
        text = self.tree.item(iid, "text")
        self.tree.item(iid, text=(UNTICK if text.startswith(TICK) else TICK)
                       + text[1:])
        self.on_pick()

    def tick_all(self, on: bool) -> None:
        """Tick everything a button could act on, or nothing."""
        for iid in self.tree.get_children():
            prop = self.proposals.get(iid)
            if prop is None:
                # A save row. Always worth acting on: it is either an import
                # or, once the library holds it, a clear.
                worth = iid in self.stray
            else:
                worth = prop.actionable or self.clearable(prop)
            text = self.tree.item(iid, "text")
            want = TICK if (on and worth) else UNTICK
            self.tree.item(iid, text=want + text[1:])
        self.on_pick()

    def ticked(self) -> set[str]:
        return {i for i in self.tree.get_children()
                if str(self.tree.item(i, "text")).startswith(TICK)}

    def chosen(self, test) -> list:
        """The ticked dumps a test accepts.

        Rows that are not dumps are skipped rather than indexed. The tree
        also holds save rows, whose iids are not in `proposals`, and looking
        one up here raised a KeyError that killed the whole button refresh:
        every button kept whatever state it last had, so ticking a save did
        nothing visible at all.
        """
        return [self.proposals[i] for i in sorted(self.ticked())
                if i in self.proposals and test(self.proposals[i])]

    def strays(self) -> list:
        """The ticked save rows, as (Save, Row)."""
        return [self.stray[i] for i in sorted(self.ticked())
                if i in self.stray]

    def picked(self) -> dumps.Proposal | None:
        sel = self.tree.selection()
        return self.proposals.get(sel[0]) if sel else None

    def on_pick(self, _evt=None) -> None:
        prop = self.picked()
        sel = self.tree.selection()
        if sel and sel[0] in self.stray:
            self.detail.config(text=self.describe_save(*self.stray[sel[0]]))
        else:
            self.detail.config(text=self.describe(prop) if prop else "")
        saves = self.strays()
        addable = len(self.chosen(lambda p: p.actionable)) + sum(
            1 for sv, row in saves
            if not dumps.same_saved_bytes(sv, self.root_dir(), row))
        clearing = len(self.chosen(self.clearable)) + sum(
            1 for sv, row in saves
            if dumps.same_saved_bytes(sv, self.root_dir(), row))
        refusing = len(self.chosen(
            lambda p: p.verdict is not dumps.Verdict.REJECTED))
        rejected = len(self.chosen(
            lambda p: p.verdict is dumps.Verdict.REJECTED))
        self.add_btn.config(text=f"Add to library ({addable})" if addable
                            else "Add to library")
        self.add_btn.state(["!disabled"] if addable and self.root_dir()
                           else ["disabled"])
        self.clear_btn.config(text=f"Clear from card ({clearing})" if clearing
                              else "Clear from card")
        self.clear_btn.state(["!disabled"] if clearing else ["disabled"])
        # One button for both directions, because they are the same decision
        # and a rejection with no way back was the bug that made this window
        # feel stuck.
        self.no_btn.config(text="Stop ignoring" if rejected and not refusing
                           else "Ignore")
        self.no_btn.state(["!disabled"] if (refusing or rejected)
                          else ["disabled"])
        self.cheat_btn.state(["!disabled"]
                             if prop is not None and prop.identity.matched
                             and self.root_dir() else ["disabled"])
        # Saves are a property of an imported dump, so this needs the row
        # rather than the identity: a dump not in the library has no reads.
        self.saves_btn.state(["!disabled"] if prop is not None
                             and prop.row is not None and self.root_dir()
                             else ["disabled"])

    def show_saves(self) -> None:
        """The save reads kept for the picked dump."""
        prop = self.picked()
        root = self.root_dir()
        if prop is None or prop.row is None or not root:
            return
        SavesDialog(self.app, root, prop.row)

    def describe_save(self, save, row) -> str:
        """The detail line for a save with no dump of its own on the card."""
        held = dumps.same_saved_bytes(save, self.root_dir(), row)
        lines = [f"{save.name}   {save.size:,} bytes"]
        lines.append(f"The save the dumper read off {row.rom}.")
        if held:
            lines.append("The library already holds these bytes, so the file "
                         "on the card is a second copy and Clear from card "
                         "deletes it.")
        else:
            lines.append("Not in the library yet. Add to library keeps it as "
                         "a dated read, and does not touch the card.")
        return "\n".join(textwrap.fill(x, 100) for x in lines)

    def describe(self, prop: dumps.Proposal) -> str:
        d = prop.dump
        lines = [f"{d.name}   {d.size:,} bytes   sha1 {d.sha1[:12]}..."]
        if prop.verdict is dumps.Verdict.UNIDENTIFIED:
            lines.append(
                "No loaded DAT has these bytes. It could be a bad dump, a "
                "revision the DAT does not carry, or a reproduction "
                "cartridge. Nothing here can tell those apart, so no name is "
                "suggested.")
        elif prop.verdict is dumps.Verdict.UNREADABLE:
            lines.append(prop.note or "Something is in the way of reading it.")
        elif prop.verdict is dumps.Verdict.MISSING:
            lines.append(
                "The index lists this dump, but the copy in the library is "
                "gone. Reported rather than tidied away: this app does not "
                "delete what it did not just write.")
        elif prop.verdict is dumps.Verdict.SAVE_ONLY:
            lines.append(
                "The ROM is already in the library. The save beside it on the "
                "card is one the library has never seen, and Add to library "
                "keeps it as a dated read without touching the ROM. Nothing "
                "else will pick it up: a save only ever arrives with its "
                "cartridge, and this cartridge is already done.")
        elif self.clearable(prop):
            lines.append(
                "A duplicate. The library already holds these bytes, so the "
                "file on the card is a second copy of them and Clear from "
                "card deletes it. The bytes are compared again immediately "
                "before the delete, so a card swapped in the meantime is "
                "refused rather than acted on.")
        elif prop.verdict is dumps.Verdict.IMPORTED:
            lines.append("Already in the library, byte for byte.")
        elif prop.verdict is dumps.Verdict.REJECTED:
            lines.append("Ignored, so it is not offered again. Tick it and "
                         "the Ignore button becomes Stop ignoring.")
        else:
            lines.append(f"Adds to the library as  {prop.rom_name}")
            lines.append(f"and keeps the original  cart-dumps/{prop.dump_name}")
            if prop.collides:
                lines.append("That name is taken by different bytes. Adding "
                             "this one will ask which you want to keep.")
        return "\n".join(textwrap.fill(x, 100) for x in lines)

    # --------------------------------------------------------------- acting --
    def add_ticked(self) -> None:
        """Add every ticked dump to the library, and say what happened."""
        root = self.root_dir()
        todo = self.chosen(lambda p: p.actionable)
        saves = self.strays()
        if not root or not (todo or saves):
            return
        added, failed, cleared = 0, [], []
        for save, row in saves:
            if dumps.same_saved_bytes(save, root, row):
                continue        # already held; ticking it means "clear it"
            path, why = dumps.import_cartridge_save(save, row, root)
            if path:
                added += 1
            else:
                failed.append(f"{save.name}: {why}")
        for prop in todo:
            choice = dumps.Choice.KEEP_BOTH
            if prop.collides:
                choice = CollisionDialog(self, prop).result
                if choice is None:
                    continue
            index = library.load(root)
            done_one = dumps.commit(prop, index, choice=choice)
            if done_one.discarded:
                continue
            if not done_one.ok:
                failed.append(f"{prop.dump.name}: {done_one.problem}")
                continue
            library.save(root, index)
            added += 1
        self.report(f"{added} added to the library", failed)
        self.refill(keep=set())

    def clear_ticked(self) -> None:
        """Delete the card's copy of dumps the library already holds.

        Byte for byte against the copy in cart-dumps immediately before each
        delete, so a card swapped for another one refuses rather than losing a
        file. This is the only destructive thing in the window.
        """
        todo = self.chosen(self.clearable)
        # A save is clearable on the same terms as a dump: the library holds
        # these exact bytes, so the file on the card is the second copy.
        saves = [(sv, row) for sv, row in self.strays()
                 if dumps.same_saved_bytes(sv, self.root_dir(), row)]
        if not (todo or saves):
            return
        every = [p.dump.name for p in todo] + [sv.name for sv, _ in saves]
        names = "\n".join(every[:12])
        more = f"\nand {len(every) - 12} more" if len(every) > 12 else ""
        if not messagebox.askyesno(
                "Clear from card",
                f"Delete {len(every)} file{'' if len(every) == 1 else 's'} from "
                f"the card?\n\n{names}{more}\n\nThe library already holds each "
                "of these, and every one is compared byte for byte before it "
                "goes.", parent=self):
            return
        gone, failed = 0, []
        for sv, row in saves:
            kept = dumps.same_saved_bytes(sv, self.root_dir(), row)
            if not kept:
                failed.append(f"{sv.name}: the library copy could not be found")
                continue
            if not dumps.same_bytes(sv.path, kept):
                failed.append(f"{sv.name}: does not match the library copy")
                continue
            try:
                os.remove(sv.path)
                gone += 1
            except OSError as e:
                failed.append(f"{sv.name}: {e}")
        for prop in todo:
            kept = prop.row.dump_path(self.root_dir())
            done = dumps.remove_from_card(
                dumps.Import(prop, ok=True, dump_path=kept, verified=True))
            if done.removed:
                gone += 1
                self.found = [d for d in self.found
                              if d.path != prop.dump.path]
            else:
                failed.append(f"{prop.dump.name}: {done.problem}")
        self.report(f"{gone} cleared from the card", failed)
        self.refill(keep=set())

    def set_ignored(self) -> None:
        """Ignore the ticked dumps, or stop ignoring them."""
        rejected = self.chosen(lambda p: p.verdict is dumps.Verdict.REJECTED)
        others = self.chosen(lambda p: p.verdict is not dumps.Verdict.REJECTED)
        for prop in others:
            dumps.reject(prop.dump)
        if not others:
            for prop in rejected:
                dumps.unreject(prop.dump)
        self.refill()

    def show_cheat(self) -> None:
        """Which cheat file this dump maps to, and a way to change it."""
        prop = self.picked()
        if prop is None or not prop.identity.matched:
            return
        CheatDialog(self, prop, self.rom_path(prop))
        self.refill()

    def report(self, done: str, failed: list[str]) -> None:
        """Say what happened. The list itself cannot: those rows are gone."""
        if failed:
            messagebox.showwarning(
                "Cartridge dumps",
                f"{done}.\n\nThese did not:\n\n" + "\n".join(failed[:12]),
                parent=self)
            return
        # The main window's status line when there is one. Asked for rather
        # than assumed: this dialog is built directly in tests and from a
        # parent that is not the App, and a missing status bar is not a reason
        # to lose the work that was just done.
        bar = getattr(self.app, "status", None)
        if bar is not None:
            bar.config(text=done, foreground="#060")

    def pick_library(self) -> None:
        """Choose where dumps are kept, and remember it."""
        chosen = filedialog.askdirectory(
            parent=self, title="Where should cartridge dumps be kept?",
            mustexist=True)
        if not chosen:
            return
        library.set_path(chosen)
        library.create(chosen)
        self.refill()

    def add_dat(self) -> None:
        """The No-Intro window, which finds the downloads rather than asking."""
        if DatDialog(self, self.catalog).loaded:
            self.refill()

    @staticmethod
    def why(dat, path: str) -> str:
        """Name the mistake and the fix, rather than reporting a blank."""
        name = os.path.basename(path)
        if dat.problem is nointro.Problem.DB_EXPORT:
            return (f"{name} is the DB Export, which carries its data in a "
                    "different form this app cannot read.\n\n"
                    "Go back to the same page and take the DAT, or the "
                    "Parent-Clone DAT. Either one works; Parent-Clone covers "
                    "a few hundred more unlicensed and aftermarket "
                    "cartridges.")
        if dat.problem is nointro.Problem.WRONG_SYSTEM:
            return (f"{name} is a DAT, but not for a system this app handles. "
                    "It wants Game Boy, Game Boy Color or Game Boy Advance.")
        if dat.problem is nointro.Problem.MISSING:
            return f"{name} is not there any more."
        if dat.problem is nointro.Problem.NOT_A_DAT:
            return f"{name} does not hold a DAT at all."
        return (f"{name} could not be read. If the download was interrupted, "
                "fetching it again is the fix.")


class CheatDialog(tk.Toplevel):
    """The cheat file a dump maps to, and every other one it could.

    A dump gets its cheats by default, because by the time it is identified it
    has a name the matcher was built for: `match.best()` is hopeless at
    ZELDA.gb and good at "Legend of Zelda, The - Link's Awakening (USA,
    Europe)". Renaming is what makes the matcher the app already has work on
    it, and this window is where that answer can be looked at and changed.

    The choice is keyed on the canonical name rather than on the dump's, and
    is remembered in prefs exactly as it is for a ROM on the card - it is a
    decision, and decisions do not live in an index that a rebuild discards.
    """

    def __init__(self, app, prop, rom_path: str | None) -> None:
        super().__init__(app)
        self.prop = prop
        self.rom_path = rom_path
        self.title("Cheats for this dump")
        self.transient(app)
        self.columnconfigure(0, weight=1)

        body = ttk.Frame(self, padding=12)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        ttk.Label(body, text=prop.identity.name or prop.dump.name).grid(
            row=0, column=0, sticky="w")
        current = dumps.cheat(prop.identity, rom_path)
        self.now = ttk.Label(body, foreground=QUIET, justify="left")
        self.now.grid(row=1, column=0, sticky="w", pady=(2, 8))

        cols = ("score", "where")
        self.tree = ttk.Treeview(body, columns=cols, show="tree headings",
                                 selectmode="browse", height=9)
        self.tree.heading("#0", text="Cheat file")
        self.tree.heading("score", text="Match")
        self.tree.heading("where", text="From")
        self.tree.column("#0", width=430, stretch=True)
        self.tree.column("score", width=70, stretch=False, anchor="center")
        self.tree.column("where", width=110, stretch=False, anchor="center")
        self.tree.grid(row=2, column=0, sticky="nsew")
        self.tree.tag_configure("on", foreground=READY)

        row = ttk.Frame(body)
        row.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(row, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(row, text="Use this one", width=13,
                   command=self.use).pack(side="right", padx=(0, 4))
        # Clearing the pin is not the same as picking nothing: it puts the
        # dump back under whatever the matcher says today, which is the right
        # answer once a cheat file with a better name turns up in an update.
        self.clear_btn = ttk.Button(row, text="Use the match", width=13,
                                    command=self.unpin)
        self.clear_btn.pack(side="right", padx=(0, 4))

        self.fill(current)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.wait_window(self)

    def fill(self, current) -> None:
        self.tree.delete(*self.tree.get_children())
        pinned = bool(self.rom_path) and bool(prefs.get_source(self.rom_path))
        self.now.config(
            text=("Using " + current.name + ("  (pinned by you)" if pinned else
                  "  (from its clone parent)" if current.via_parent else
                  "  (matched)")) if current else
                 (current.problem or "No cheat file matches this one."),
            foreground=QUIET if current else LESSER)
        self.clear_btn.state(["!disabled"] if pinned else ["disabled"])
        try:
            found = match.rank(self.prop.identity.name or "",
                               self.prop.identity.system or "", limit=12)
        except cheatlib.MissingDatabase:
            found = []
        for cand in found:
            self.tree.insert(
                "", "end", iid=cand.path, text=cand.name,
                values=(f"{cand.score:.2f}", "yours" if cand.local
                        else "libretro"),
                tags=("on",) if current and cand.path == current.path else ())
        if current and current.path in self.tree.get_children():
            self.tree.selection_set(current.path)

    def use(self) -> None:
        sel = self.tree.selection()
        if sel and self.rom_path:
            dumps.set_cheat(self.rom_path, sel[0])
            self.fill(dumps.cheat(self.prop.identity, self.rom_path))

    def unpin(self) -> None:
        if self.rom_path:
            dumps.set_cheat(self.rom_path, None)
            self.fill(dumps.cheat(self.prop.identity, self.rom_path))


class SavesDialog(tk.Toplevel):
    """Every save read kept for one cartridge, and what can be done with one.

    A cartridge can be read more than once and the reads can differ without
    either being wrong: a dead battery returns volatile content, and one
    cartridge here returned three different sets of score digits with an
    identical header every time. So reads are kept, dated, and never
    overwritten, and this window is where you choose between them.

    Removing one is offered, and reluctantly. A save cannot be re-made once
    the battery is gone, so the confirmation says whether the same bytes are
    still on the card and refuses to pretend otherwise when they are not.
    That is the difference between a delete you can undo by reading the card
    again and one that ends the file. An earlier version refused to delete at
    all, which only meant a wrongly filed save had to be removed from a shell.
    """

    def __init__(self, app, root: str, row: library.Row) -> None:
        super().__init__(app)
        self.app = app
        self.root_dir = root
        self.row = row
        self.title("Saves for this cartridge")
        self.transient(app)
        self.columnconfigure(0, weight=1)

        body = ttk.Frame(self, padding=12)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        ttk.Label(body, text=row.rom or row.dump or "").grid(
            row=0, column=0, sticky="w")
        folder = library.cartsave_dir(root, row.rom) if row.rom else ""
        ttk.Label(body, foreground=QUIET, text=folder).grid(
            row=1, column=0, sticky="w", pady=(1, 8))

        # browse, not extended: restoring is writing one file to one place,
        # and a multiple selection would be an invitation to ask for
        # something the card cannot hold.
        cols = ("name", "where", "on", "size", "sha1")
        self.tree = ttk.Treeview(body, columns=cols, show="tree headings",
                                 selectmode="browse", height=8)
        self.tree.heading("#0", text="Read on")
        self.tree.heading("name", text="Name")
        self.tree.heading("where", text="Read by")
        self.tree.heading("on", text="On card")
        self.tree.heading("size", text="Bytes")
        self.tree.heading("sha1", text="SHA-1")
        self.tree.column("#0", width=130, stretch=False)
        self.tree.column("name", width=180, stretch=True)
        self.tree.column("where", width=80, stretch=False, anchor="center")
        self.tree.column("on", width=70, stretch=False, anchor="center")
        self.tree.column("size", width=80, stretch=False, anchor="e")
        self.tree.column("sha1", width=150, stretch=False)
        self.tree.grid(row=2, column=0, sticky="nsew")
        self.tree.tag_configure("live", foreground=READY)
        self.tree.tag_configure("adrift", foreground=LESSER)

        self.note = ttk.Label(body, foreground=QUIET, justify="left")
        self.note.grid(row=3, column=0, sticky="w", pady=(8, 0))

        bar = ttk.Frame(body)
        bar.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.grab_btn = ttk.Button(bar, text="Read from card", width=15,
                                   command=self.from_card, state="disabled")
        self.grab_btn.pack(side="left")
        self.card_btn = ttk.Button(bar, text="Restore to card", width=16,
                                   command=self.to_card, state="disabled")
        self.card_btn.pack(side="left", padx=(4, 0))
        self.play_btn = (ttk.Button(bar, text="Open in mGBA", width=14,
                                    command=self.play, state="disabled")
                         if emulator.available() else None)
        if self.play_btn is not None:
            self.play_btn.pack(side="left", padx=(4, 0))
        self.name_btn = ttk.Button(bar, text="Name...", width=9,
                                   command=self.rename, state="disabled")
        self.name_btn.pack(side="left", padx=(4, 0))
        self.drop_btn = ttk.Button(bar, text="Remove...", width=11,
                                   command=self.forget, state="disabled")
        self.drop_btn.pack(side="left", padx=(4, 0))
        open_btn = open_button_for(bar, lambda: folder or None)
        if open_btn is not None:
            open_btn.pack(side="left", padx=(4, 0))
            retune_open(open_btn, folder or None)
        ttk.Button(bar, text="Close", width=8,
                   command=self.destroy).pack(side="right")

        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.picked())
        # Double click is the one gesture everybody already tries on a list of
        # things they want to open.
        self.tree.bind("<Double-1>", lambda _e: self.play())
        self.fill()

    # ------------------------------------------------------------- drawing --
    def fill(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.saves = {sv.name: sv for sv in dumps.cartsaves(self.root_dir,
                                                            self.row)}
        reads = list(self.saves)
        card_root = self.app.card.root if self.app.card else ""
        self.live = (dumps.live_read(self.root_dir, self.row, card_root)
                     if card_root else None)
        self.on_card = (dumps.card_save_of(card_root, self.row)
                        if card_root else None)
        for name, sv in self.saves.items():
            here = name == self.live
            self.tree.insert("", "end", iid=name, text=sv.day,
                             values=(sv.label, sv.where,
                                     "yes" if here else "",
                                     f"{sv.size:,}", sv.sha1[:12] + "..."),
                             tags=("live",) if here else ())
        self.pending = (dumps.card_saves_for(card_root, self.root_dir,
                                             self.row, library.load(self.root_dir))
                        if card_root else [])
        self.grab_btn.state(["!disabled"] if self.pending else ["disabled"])
        if reads:
            # The one on the card if it is one of these, because that is the
            # save you are actually playing. Otherwise the newest, which is
            # what everything else in the app reaches for.
            pick = self.live or reads[-1]
            self.tree.selection_set(pick)
            self.tree.focus(pick)
        self.say_where(reads)
        self.picked()

    def say_where(self, reads: list[str]) -> None:
        """The line above the buttons: where this cartridge's save stands."""
        if self.pending:
            kinds = sorted({library.CART if o == library.CART else "Pocket"
                            for _sv, o in self.pending})
            what = " and ".join("the dumper's read" if k == library.CART
                                else "a Pocket play session" for k in kinds)
            self.note.config(
                text=f"The card has {what} that the library does not hold. "
                     "Read from card keeps it.",
                foreground=LESSER)
        elif not reads:
            self.note.config(
                text="No save was read off this cartridge. Either it has no "
                     "save RAM, or it was dumped before saves were read.",
                foreground=QUIET)

    def picked(self) -> None:
        sel = self.tree.selection()
        # Restoring the read already on the card would write the bytes that
        # are there, so it is offered as nothing to do rather than as an act.
        self.card_btn.state(["!disabled"] if sel and sel[0] != self.live
                            else ["disabled"])
        if self.play_btn is not None:
            self.play_btn.state(["!disabled"] if sel else ["disabled"])
        self.name_btn.state(["!disabled"] if sel else ["disabled"])
        self.drop_btn.state(["!disabled"] if sel else ["disabled"])
        if not sel:
            return
        if self.pending:
            return                      # say_where has the more urgent thing
        sv = self.saves.get(sel[0])
        reads = list(self.saves)
        if sel[0] == self.live:
            what = " and is the one on the card"
        elif sel[0] == reads[-1]:
            what = " and is the newest"
        else:
            what = ""
        called = f'"{sv.label}", ' if sv and sv.label else ""
        self.note.config(text=f"{called}read on {sv.day if sv else sel[0]}"
                              f"{what}.", foreground=QUIET)

    def forget(self) -> None:
        """Delete one save read from the library, after saying what that costs."""
        sel = self.tree.selection()
        sv = self.saves.get(sel[0]) if sel else None
        if sv is None:
            return
        card_root = self.app.card.root if self.app.card else ""
        elsewhere = dumps.cartsave_elsewhere(sv, card_root, self.row)
        what = f"{sv.title}, {sv.size:,} bytes, read by the {sv.where}."
        if elsewhere:
            fate = ("The card still holds these exact bytes, at\n"
                    f"{elsewhere}\n\nso this can be read again.")
        else:
            fate = ("Nothing else holds these bytes. A save cannot be "
                    "re-made once the battery is gone.\n\n"
                    "This deletes it for good.")
        if not messagebox.askyesno("Remove save",
                                   f"{what}\n\n{fate}\n\nRemove it?",
                                   default="no", icon="warning", parent=self):
            return
        why = dumps.forget_cartsave(sv)
        if why:
            messagebox.showerror("Remove save", why)
            return
        self.fill()
        self.note.config(text=f"Removed {sv.name}", foreground=QUIET)

    def rename(self) -> None:
        """Give the picked save a name, or clear the one it has.

        Kept against the save's bytes rather than its filename, so it follows
        the read and not the slot: the file is dated and immutable, and the
        name is the only thing about it a person chose.
        """
        sel = self.tree.selection()
        sv = self.saves.get(sel[0]) if sel else None
        if sv is None:
            return
        text = simpledialog.askstring(
            "Name this save",
            f"Read on {sv.day}, {sv.size:,} bytes.\n\n"
            "A name for it, or empty to clear:",
            initialvalue=sv.label, parent=self)
        if text is None:
            return                      # cancelled, which is not "clear it"
        dumps.name_cartsave(sv, text)
        self.fill()

    def from_card(self) -> None:
        """Keep every save the card has for this cartridge and the library lacks.

        Both kinds if both are there, each filed under what wrote it. The
        dumper's read and a Pocket play session are separate records and are
        never merged: one is what the chip held and the other is padded to an
        emulated core's slot.
        """
        card_obj = self.app.card
        if card_obj is None:
            messagebox.showinfo("Read from card", "No card is mounted.")
            return
        kept, problems = dumps.read_card_saves(
            card_obj.root, self.root_dir, self.row,
            library.load(self.root_dir))
        if problems:
            messagebox.showwarning("Read from card", "\n".join(problems))
        self.fill()
        if kept:
            self.note.config(
                text=("Kept " + ", ".join(os.path.basename(k) for k in kept)),
                foreground=READY)

    def chosen_path(self) -> str | None:
        sel = self.tree.selection()
        if not sel or not self.row.rom:
            return None
        return os.path.join(library.cartsave_dir(self.root_dir, self.row.rom),
                            sel[0])

    # -------------------------------------------------------------- acting --
    def to_card(self) -> None:
        """Write the chosen read beside this dump's ROM on the card."""
        sel = self.tree.selection()
        card_obj = self.app.card
        if not sel or card_obj is None or not self.row.rom:
            messagebox.showinfo("Restore to card", "No card is mounted.")
            return
        pid = self.row.system or ""
        rom_on_card = os.path.join(card.cartdumps_dir(card_obj.root, pid),
                                   self.row.rom)
        if not os.path.exists(rom_on_card):
            messagebox.showinfo(
                "Restore to card",
                f"{self.row.rom} is not on the card yet. Copy the dump to "
                "the card first; the save goes beside it.")
            return
        where, why = dumps.restore_save(self.root_dir, self.row, rom_on_card,
                                        which=sel[0])
        if why:
            messagebox.showerror("Restore to card", why)
            return
        self.note.config(text=f"Written to {where}", foreground=READY)

    def play(self) -> None:
        """Launch mGBA on this dump with the chosen read."""
        rom = (os.path.join(library.roms_dir(self.root_dir), self.row.rom)
               if self.row.rom else "")
        if not rom or not os.path.exists(rom):
            messagebox.showerror("Play", "The ROM is not in the library.")
            return
        try:
            emulator.play(rom, self.chosen_path())
        except emulator.NoEmulator as e:
            messagebox.showinfo("Play", str(e))
        except OSError as e:
            messagebox.showerror("Play", f"could not start mGBA:\n\n{e}")


class DatDialog(tk.Toplevel):
    """The No-Intro DAT files, found where the browser left them.

    Asking somebody to go and find these in a file chooser was the wrong
    question. There are exactly three of them, their names are fixed by the
    site that issues them, and they land in the one directory a browser puts
    downloads in - so the app can look, say what it found, and let the answer
    be a tick rather than a filesystem expedition. Browse... is still here for
    a file kept somewhere else, and it hands the job to the desktop's own
    chooser where there is one.

    The data itself comes from No-Intro, who publish it and gate the download
    behind their own site, so Get DATs... opens that page rather than the app
    fetching anything. Pointing somebody at the source is the useful thing to
    do here: it is one click, it is always the current version, and it is
    credited to the people who compiled it.
    """

    # The prose is what sets this window's width, and the filenames in the
    # table are long: "Nintendo - Game Boy (Parent-Clone) (20260827-092427)"
    # is 51 characters before the extension. Wrapping wider costs nothing and
    # is what stops the column that tells two downloads apart from truncating.
    WIDTH = 112

    def __init__(self, app, catalog) -> None:
        super().__init__(app)
        self.catalog = catalog
        self.loaded = False              # did anything change while we were up
        self.rows: dict[str, tuple] = {}
        self.title("No-Intro data")
        self.transient(app)
        self.columnconfigure(0, weight=1)

        body = ttk.Frame(self, padding=12)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        ttk.Label(body, text="No-Intro DAT files").grid(
            row=0, column=0, sticky="w")
        ttk.Label(body, foreground=QUIET, justify="left", text=textwrap.fill(
            "Naming a dump correctly needs No-Intro's data. Get DATs... opens "
            "their download page: take the DAT, or the Parent-Clone DAT, for "
            "each system you dump, with the defaults left alone. Parent-Clone "
            "covers a few hundred more unlicensed and aftermarket cartridges. "
            "The DB Export is a different format and cannot be read.",
            self.WIDTH)).grid(row=1, column=0, sticky="w", pady=(2, 8))

        cols = ("system", "kind", "entries")
        self.tree = ttk.Treeview(body, columns=cols, show="tree headings",
                                 selectmode="none", height=7)
        self.tree.heading("#0", text="File")
        self.tree.heading("system", text="System")
        self.tree.heading("kind", text="What it is")
        self.tree.heading("entries", text="Entries")
        self.tree.column("#0", width=340, stretch=True)
        # "Nintendo - " on every row of a window that is only ever about
        # Nintendo handhelds is 11 characters of nothing, and it was pushing
        # the filename - the part that tells two downloads apart - out of view.
        self.tree.column("system", width=150, stretch=False)
        self.tree.column("kind", width=120, stretch=False, anchor="center")
        self.tree.column("entries", width=80, stretch=False, anchor="e")
        self.tree.grid(row=2, column=0, sticky="ew")
        self.tree.tag_configure("dead", foreground=IDLE)
        self.tree.tag_configure("bad", foreground=LESSER)
        self.tree.tag_configure("on", foreground=READY)
        self.tree.bind("<Button-1>", self.toggle)

        self.note = ttk.Label(body, foreground=QUIET, justify="left")
        self.note.grid(row=3, column=0, sticky="w", pady=(8, 0))

        row = ttk.Frame(body)
        row.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(row, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(row, text="Load ticked", width=12,
                   command=self.load).pack(side="right", padx=(0, 4))
        # Their page, opened in the browser, because the one thing this window
        # cannot do for you is the download itself.
        ttk.Button(row, text="Get DATs...", width=12,
                   command=self.get).pack(side="left")
        ttk.Button(row, text="Browse...", width=11,
                   command=self.browse).pack(side="left", padx=(4, 0))
        ttk.Button(row, text="Look again", width=11,
                   command=self.refill).pack(side="left", padx=(4, 0))

        self.refill()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.wait_window(self)

    # ------------------------------------------------------------ the list --
    def candidates(self) -> list[str]:
        """Files whose names say No-Intro issued them, newest name last.

        Matched on the name rather than opened, because a downloads directory
        holds archives that are megabytes each and nothing here is worth
        reading a ROM set to find out. No-Intro's filenames start with the
        system name, which is the same string the DAT header carries, so the
        test is the one `nointro.system_for` already makes.
        """
        found = []
        for d in download_dirs():
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            for n in names:
                if not n.lower().endswith((".zip", ".dat", ".xml")):
                    continue
                if nointro.system_for(n):
                    found.append(os.path.join(d, n))
        return found

    def refill(self) -> None:
        """Look again, and describe what is there. Loads nothing."""
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        for path in self.candidates():
            # Probed, not loaded: describing a file must not change what the app
            # is searching. Loading is what the tick is for.
            dat = nointro.load(path)
            live = self.catalog.get(nointro.system_for(os.path.basename(path)))
            already = bool(dat) and bool(live) and live.version == dat.version \
                and live.flavour == dat.flavour
            if dat:
                kind = "Parent-Clone" if dat.flavour == nointro.PARENT_CLONE \
                    else "DAT"
                system = self.plainly(dat.system)
                entries = f"{len(dat):,}"
                tag = "on" if already else ""
                tick = TICK if already else UNTICK
            else:
                kind = self.shortly(dat)
                system = self.plainly(
                    nointro.system_for(os.path.basename(path)))
                entries = ""
                tag = "bad"
                tick = " "
            self.rows[path] = (dat, already)
            self.tree.insert("", "end", iid=path,
                             text=f"{tick} {self.filename(path)}",
                             values=(system, kind, entries),
                             tags=(tag,) if tag else ())
        where = ", ".join(self.short(d) for d in download_dirs())
        if self.rows:
            self.note.config(text=textwrap.fill(
                f"Looked in {where}. Tick what you want and press Load "
                "ticked; a file already loaded is ticked and green.",
                self.WIDTH), foreground=QUIET)
        else:
            self.note.config(text=textwrap.fill(
                f"Nothing that looks like a No-Intro DAT in {where}. Press "
                "Get DATs... to fetch them, or Browse... if you keep them "
                "somewhere else.", self.WIDTH), foreground=LESSER)

    @staticmethod
    def filename(path: str) -> str:
        """The download's name, without the maker the next column repeats.

        Every one of these begins "Nintendo - ", which is 11 characters of the
        one thing that is the same on every row, in the column whose whole job
        is telling two downloads apart. The date stamp is the part that
        matters and it is at the other end.
        """
        name = os.path.basename(path)
        return name[len("Nintendo - "):] if name.startswith("Nintendo - ") \
            else name

    @staticmethod
    def plainly(system: str) -> str:
        """The system, without the maker every row would repeat."""
        full = nointro.SYSTEMS.get(system, "")
        return full[len("Nintendo - "):] if full else ""

    @staticmethod
    def short(path: str) -> str:
        home = os.path.expanduser("~")
        return "~" + path[len(home):] if path.startswith(home) else path

    @staticmethod
    def shortly(dat) -> str:
        """Two or three words for the table; the sentence is elsewhere."""
        return {nointro.Problem.DB_EXPORT: "DB Export",
                nointro.Problem.WRONG_SYSTEM: "another system",
                nointro.Problem.NOT_A_DAT: "no DAT inside",
                nointro.Problem.MISSING: "gone"}.get(dat.problem, "unreadable")

    # --------------------------------------------------------------- acting --
    def toggle(self, evt) -> None:
        """Tick a row, the way the cores dialog ticks a core."""
        iid = self.tree.identify_row(evt.y)
        if not iid or self.tree.identify_region(evt.x, evt.y) == "heading":
            return
        dat, _ = self.rows.get(iid, (None, False))
        if not dat:
            # A file that cannot be read is not a choice. Saying why beats a
            # tick that does nothing when it is pressed.
            messagebox.showwarning("No-Intro data",
                                   DumpsDialog.why(dat, iid) if dat is not None
                                   else f"{os.path.basename(iid)} cannot be "
                                        "read as a DAT.")
            return
        text = self.tree.item(iid, "text")
        self.tree.item(iid, text=(UNTICK if text.startswith(TICK) else TICK)
                       + text[1:])

    def ticked(self) -> list[str]:
        return [i for i in self.tree.get_children()
                if str(self.tree.item(i, "text")).startswith(TICK)]

    def load(self) -> None:
        """Load every ticked file into the catalog, and say what happened."""
        picked = self.ticked()
        if not picked:
            return
        bad = []
        for path in picked:
            if not self.catalog.take(path):
                bad.append(os.path.basename(path))
            else:
                self.loaded = True
                self.keep(path)
        self.refill()
        if bad:
            messagebox.showwarning(
                "No-Intro data",
                "These could not be read:\n\n" + "\n".join(bad))
        else:
            self.destroy()

    def keep(self, path: str) -> None:
        """Copy a loaded DAT into the library and remember it for next time.

        Both halves matter. Remembering alone would point at a downloads
        folder, which is the least durable directory on any computer; copying
        alone would still leave the catalog empty at the next start.
        """
        root = library.path()
        if root:
            try:
                path = library.take_in(root, path, library.DATS)
            except OSError as e:
                say.err(f"could not copy {os.path.basename(path)} "
                        f"into the library: {e}")
        prefs.set_dats(prefs.get_dats() + [path])

    def browse(self) -> None:
        """One file from anywhere, through the desktop's own chooser."""
        path = native_open(self, "A No-Intro DAT, as downloaded")
        if not path:
            return
        dat = self.catalog.take(path)
        if not dat:
            messagebox.showwarning("No-Intro data",
                                   DumpsDialog.why(dat, path))
        else:
            self.loaded = True
            self.keep(path)
        self.refill()

    def get(self) -> None:
        """Open No-Intro's download page in the browser."""
        if not reveal.website(DATOMATIC):
            messagebox.showinfo(
                "No-Intro data",
                "Could not open a browser here. The address is:\n\n"
                + DATOMATIC)


class CollisionDialog(tk.Toplevel):
    """One name, two different files, and three answers with different costs.

    This happens constantly rather than rarely, because the core's own names
    collide by design: every cartridge titled ZELDA produces ZELDA.gb, and the
    second one silently overwrote the first on a real card before this app
    ever saw it. What is decided here is only the second kind of collision -
    the name this app is about to write is taken. The first kind, where the
    card already lost a dump, is a fact about the past and not a question.
    """

    def __init__(self, parent, prop: dumps.Proposal) -> None:
        super().__init__(parent)
        self.result: dumps.Choice | None = None
        self.title("A name is taken")
        self.transient(parent)
        self.resizable(False, False)

        body = ttk.Frame(self, padding=12)
        body.grid(row=0, column=0, sticky="nsew")
        standing = prop.rom_standing or prop.dump_standing
        ttk.Label(body, foreground=FAULT, text=textwrap.fill(
            f"{prop.rom_name} is already in your library, with different "
            "contents.", 78), justify="left").grid(row=0, column=0, sticky="w")

        facts = ttk.Frame(body)
        facts.grid(row=1, column=0, sticky="w", pady=(8, 8))
        if standing is not None:
            ttk.Label(facts, foreground=QUIET, font=("TkFixedFont",), text=(
                f"Already there   {standing.size:>12,} bytes   "
                f"sha1 {standing.sha1[:12] or '(unreadable)'}"
                + (f"   imported {standing.imported}" if standing.imported else "")
            )).grid(row=0, column=0, sticky="w")
        ttk.Label(facts, foreground=QUIET, font=("TkFixedFont",), text=(
            f"This dump       {prop.dump.size:>12,} bytes   "
            f"sha1 {prop.sha1[:12]}")).grid(row=1, column=0, sticky="w")

        # Each button says what it costs, because the three are not variations
        # on one answer: one keeps both files, one destroys a file, and one
        # writes nothing at all.
        keep_as = dumps.suffixed(prop.rom_name or "", prop.sha1)
        for i, (label, why, choice) in enumerate((
                ("Keep both", f"file this one as {keep_as}",
                 dumps.Choice.KEEP_BOTH),
                ("Replace", "file this one, then delete the old",
                 dumps.Choice.REPLACE),
                ("Discard", "leave the library alone; this dump stays on the "
                 "card", dumps.Choice.DISCARD))):
            line = ttk.Frame(body)
            line.grid(row=2 + i, column=0, sticky="ew", pady=(0, 4))
            ttk.Button(line, text=label, width=11,
                       command=lambda c=choice: self.answer(c)).pack(side="left")
            ttk.Label(line, foreground=QUIET, text=why).pack(
                side="left", padx=(8, 0))

        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.wait_window(self)

    def answer(self, choice: dumps.Choice) -> None:
        self.result = choice
        self.destroy()


class RemoveDialog(tk.Toplevel):
    """Offer to empty the card, once there is a verified copy to replace it.

    Emptying the card as dumps are imported is what makes the collision hazard
    survivable: the next dump of a differently-titled cartridge then has
    nothing to overwrite. It is still asked rather than assumed, because the
    answer is occasionally no and a card pulled at any point before Remove
    costs nothing worse than a dump that is still on it.

    Not a preference to be switched off, for the same reason.
    """

    def __init__(self, parent, imported: dumps.Import) -> None:
        super().__init__(parent)
        self.imported = imported
        self.removed = False
        self.title("Remove from the card?")
        self.transient(parent)
        self.resizable(False, False)

        body = ttk.Frame(self, padding=12)
        body.grid(row=0, column=0, sticky="nsew")
        ttk.Label(body, text="Backed up to").grid(row=0, column=0, sticky="w")
        # Shown, not described. Somebody about to agree to a deletion should be
        # able to go and look at the thing that replaces it before answering.
        ttk.Label(body, foreground=QUIET, text=imported.dump_path).grid(
            row=1, column=0, sticky="w", pady=(1, 0))
        btn = open_button(body, holding(imported.dump_path))
        if btn is not None:
            btn.grid(row=1, column=1, padx=(8, 0))
        ttk.Label(body, foreground=READY,
                  text="Verified byte for byte against the card.").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 10))

        name = os.path.basename(imported.proposal.dump.path)
        ttk.Label(body, text=f"Remove {name} from the card?").grid(
            row=3, column=0, sticky="w")
        row = ttk.Frame(body)
        row.grid(row=4, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(row, text="Remove", width=10,
                   command=self.remove).pack(side="right")
        ttk.Button(row, text="Keep", width=10,
                   command=self.destroy).pack(side="right", padx=(0, 4))

        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.wait_window(self)

    def remove(self) -> None:
        done = dumps.remove_from_card(self.imported)
        self.removed = done.removed
        if not done.removed:
            messagebox.showwarning("Cartridge dumps", done.problem or
                                   "nothing was removed")
        self.destroy()


class Chooser(tk.Toplevel):
    """Pick which cheat file a game uses, and remember it."""

    def __init__(self, app: App, view: model.GameView) -> None:
        super().__init__(app)
        self.app, self.view = app, view
        self.title("Cheat source")
        self.transient(app)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        ttk.Label(self, text=view.game.name, padding=8).grid(row=0, column=0,
                                                             sticky="w")
        self.list = tk.Listbox(self, width=78, height=12)
        self.list.grid(row=1, column=0, sticky="nsew", padx=8)
        for c in view.alternates:
            mark = "* " if c.local else "  "
            self.list.insert("end",
                             f"{mark}{c.score:.2f}  {os.path.basename(c.path)}")
        if view.alternates:
            self.list.selection_set(0)

        row = ttk.Frame(self, padding=8)
        row.grid(row=2, column=0, sticky="ew")
        ttk.Button(row, text="Use this", command=self.choose).pack(side="right")
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        # Not in the list above because nothing matches it: an archive is a
        # file you went and got, not a database entry, so it is chosen rather
        # than ranked.
        self.arc_btn = ttk.Button(row, text="From a GameHacking zip...",
                                  width=26, command=self.from_archive)
        self.arc_btn.pack(side="left")
        if view.platform not in mister.PLATFORMS:
            # The records are gba_cheats words. There is nothing to apply on a
            # Game Boy, and offering it would be a lie.
            self.arc_btn.state(["disabled"])

    def use(self, path: str) -> None:
        model.pin(self.view.game, path)
        self.app.view = model.load(self.view.game, source=path)
        self.app.refresh_cheats()
        self.destroy()

    def choose(self) -> None:
        sel = self.list.curselection()
        if not sel:
            return
        self.use(self.view.alternates[sel[0]].path)

    def from_archive(self) -> None:
        """Pin a GameHacking.org zip, the kind MiSTer ships, as the source."""
        path = filedialog.askopenfilename(
            parent=self, title="A GameHacking.org cheat archive",
            filetypes=[("Cheat archive", "*.zip"), ("Every file", "*")])
        if not path:
            return
        if not mister.looks_like_one(path):
            messagebox.showerror(
                "Cheat source",
                f"{os.path.basename(path)} is not a GameHacking.org archive.\n\n"
                "One holds .gg files, each a whole number of 16-byte entries.")
            return
        groups = mister.read(path)
        usable = sum(1 for g in groups if g.usable)
        credit = mister.attribution(path)
        note = (f"{len(groups)} cheats, {usable} usable.\n\n"
                + (credit + "\n\n" if credit else "")
                + "Codes that poke nothing are kept and shown as unusable: "
                  "the encoder does not filter encrypted codes, and a cheat "
                  "that will not work is not the same as one that is missing."
                  "\n\nUse this archive?")
        if not messagebox.askyesno("Cheat source", note, parent=self):
            return
        self.use(path)


def set_icon(root: tk.Tk) -> None:
    """The window and taskbar icon.

    Best effort on purpose: a missing or unreadable icon is not a reason to
    refuse to start, and Tk raises rather than falling back on its own.
    """
    try:
        images = [tk.PhotoImage(file=version.asset(n))
                  for n in ("icon-64.png", "icon.png")
                  if os.path.exists(version.asset(n))]
        if images:
            root._icons = images          # Tk keeps no reference of its own
            root.iconphoto(True, *images)
    except Exception:                                        # noqa: BLE001
        pass


def main() -> int:
    root = tk.Tk()
    root.title(version.title())
    set_icon(root)
    # Wide enough for a cheat description and its addresses without truncating
    # either, which 1080 was not: the description column was cutting names off
    # mid-word. Tall enough for a useful number of rows.
    root.geometry("1400x820")
    root.minsize(1100, 600)
    App(root)
    root.mainloop()
    return 0
