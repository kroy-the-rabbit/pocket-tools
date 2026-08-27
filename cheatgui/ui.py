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
import textwrap
import tkinter as tk
from tkinter import messagebox, ttk

import card as card_mod
import carts
import cheatfile
import cheatlib
import core as core_mod
import db
import meter
import model
import timing
import version
import work
import writer

TICK, UNTICK = "☑", "☐"
CARTS = "carts"        # iid of the Cartridges row in the systems pane
GROUP = "sys:"         # iid prefix of a system heading in the cartridge pane


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=8)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)

        self.card: card_mod.Card | None = None
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
        self.ready: dict[str, list[int]] = {}   # platform id -> cheat counts
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
        self.rescan_btn = ttk.Button(top, text="Rescan", command=self.rescan)
        self.rescan_btn.grid(row=0, column=2)
        self.eject_btn = ttk.Button(top, text="Eject", width=7,
                                    command=self.eject, state="disabled")
        self.eject_btn.grid(row=0, column=3, padx=(4, 0))

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
        self.refresh_core_label()
        self.eject_btn.state(["disabled"])
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
        self.platforms = platforms
        extra = f"  (+{len(cards) - 1} more)" if len(cards) > 1 else ""
        self.card_label.config(text=f"{self.card.root}  [{self.card.label}]{extra}",
                               foreground="#060")
        for i, p in enumerate(self.platforms):
            # Blank rather than 0 until the system has been read: nobody has
            # counted yet, and 0 would be a claim that there are none.
            self.systems.insert("", "end", iid=str(i), text=p.name,
                                values=(len(p.games) if p.scanned else "",))
        # Cartridges are not files on the card, so they are listed separately.
        self.systems.insert("", "end", iid=CARTS, text="Cartridges",
                            values=(len(carts.all()),))
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
        self.platforms = []
        self.platform = None
        self.games = []
        self.view = None
        self.survey = None
        self.refresh_core_label()
        self.systems.delete(*self.systems.get_children())
        self.gamelist.delete(*self.gamelist.get_children())
        self.cheats.delete(*self.cheats.get_children())
        for b in (self.save_btn, self.source_btn, self.del_btn, self.add_btn,
                  self.move_btn):
            b.state(["disabled"])
        self.source_label.config(text="")
        self.meter.set(0)
        self.card_label.config(text="card unmounted, safe to remove",
                               foreground="#060")
        self.status.config(text=str(message), foreground="#060")

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

        The systems pane already shows these names, and a cartridge filed
        under one should say the same thing rather than a second name for it.
        """
        for p in self.platforms:
            if p.id == pid:
                return p.name
        return pid.upper()

    def show_carts(self) -> None:
        """The cartridges you have listed, filed under the system each is for.

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
        """Offer the systems this cartridge is not already filed under.

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
        if game is None:
            # A system heading, or nothing. Neither is something to act on.
            self.del_btn.state(["disabled"])
            self.move_btn.state(["disabled"])
            return
        is_cart = isinstance(game, carts.Cartridge)
        self.del_btn.state(["!disabled"] if is_cart else ["disabled"])
        self.move_btn.state(["!disabled"] if is_cart else ["disabled"])
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
        # An "Open" button goes to the left of Copy path, showing the selected
        # row's directory in the file manager through reveal.py. It is not here
        # yet because that module is not: it belongs beside Copy path rather
        # than replacing it, since a sandboxed or headless build has no file
        # manager to call and the path still has to be gettable there. When it
        # lands it calls reveal.open_dir() on os.path.dirname of the selected
        # path, and is offered only for a directory that already exists -
        # Assets/<platform>/common/ may not, and looking at a directory must
        # never be what creates it.

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
        self.note.config(foreground="#666", text=path or "")

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

    def choose(self) -> None:
        sel = self.list.curselection()
        if not sel:
            return
        path = self.view.alternates[sel[0]].path
        model.pin(self.view.game, path)
        self.app.view = model.load(self.view.game, source=path)
        self.app.refresh_cheats()
        self.destroy()


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
