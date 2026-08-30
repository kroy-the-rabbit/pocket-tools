# SPDX-License-Identifier: GPL-3.0-or-later
"""A bar showing how much of the core's code store a selection uses.

The core holds 32 cheats and 32 codes. Every cheat carries at least one code,
so the code count is always the tighter of the two: a selection that fits the
code store cannot overflow the cheat store, and one bar says everything.

It matters because going over is silent. The core parses the file until the
store is full and ignores the rest, so the cheats past the limit are loaded,
enabled, and do nothing at all. This is where that becomes visible, before the
card is written rather than on the handheld.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

TRACK = "#d9d9d9"
FILL = "#0a6"
NEAR = "#c80"
OVER = "#a00"


class Meter(ttk.Frame):
    """`limit` wide, filled to `used`. Amber as it fills, red once it cannot.

    A limit of None means the core for this system has not defined one. The
    bar then counts rather than measures: there is nothing to be a fraction
    of, and drawing one against a made-up number would be worse than drawing
    none at all.
    """

    def __init__(self, master, limit, width: int = 150, height: int = 10):
        super().__init__(master)
        self.set_limit(limit)
        self.w, self.h = width, height
        self.canvas = tk.Canvas(self, width=width, height=height,
                                highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0)
        self.label = ttk.Label(self, text="")
        self.label.grid(row=0, column=1, padx=(6, 0))
        self.set(0)

    def set_limit(self, limit) -> None:
        """None where the core has no published limit for this system."""
        self.limit = None if limit is None else max(1, limit)

    def set(self, used: int) -> None:
        c = self.canvas
        c.delete("all")
        c.create_rectangle(0, 0, self.w, self.h, fill=TRACK, outline="")

        if self.limit is None:
            # Nothing to be a fraction of. Count, and leave the bar empty
            # rather than drawing a fill against a number nobody published.
            self.label.config(text=f"{used} codes", foreground="#666")
            return

        over = used - self.limit
        colour = OVER if over > 0 else (NEAR if used >= self.limit * 0.8 else FILL)
        filled = round(self.w * min(used, self.limit) / self.limit)
        if used:
            # never draw a filled bar as empty: one code should still show
            c.create_rectangle(0, 0, max(2, filled), self.h, fill=colour,
                               outline="")

        text = f"{used} / {self.limit} codes"
        if over > 0:
            text += f"   {over} will not fit"
        self.label.config(text=text, foreground=OVER if over > 0 else "#000")
