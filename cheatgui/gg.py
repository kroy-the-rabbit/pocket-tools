# SPDX-License-Identifier: GPL-3.0-or-later
"""Game Gear cheat codes.

  Game Genie         XXX-XXX-XXX or XXX-XXX: ROM read override, "patch".
  Pro Action Replay  00AA-AADD: work RAM write at C000-DFFF, "poke".

Decoded through `gg2bin` and `ggcht`, copies of the core's reference model.
Skipped as the core skips them: mixed group widths, `X` or `?` placeholders,
pokes outside work RAM. A cheat with no usable code keeps its description.
`raw` is dashed and uppercase so a written file reads back the same.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import gg2bin

# The core's CODES table: every code, of either kind, spends one entry.
MAX_CODES = gg2bin.MAX_CODES

WORK_RAM = (0xC000, 0xDFFF)


@dataclass
class Code:
    """One code, shaped like `ggdecode.Cheat` so the UI needs no special case."""
    raw: str
    kind: str = "poke"
    address: Optional[int] = None
    value: Optional[int] = None
    compare: Optional[int] = None
    bank: Optional[int] = None


@dataclass
class Group:
    index: int
    codes: list = field(default_factory=list)
    desc: Optional[str] = None
    enabled: bool = True


def _dashed(code: str, width: int) -> str:
    return "-".join(code[i:i + width] for i in range(0, len(code), width))


def decode(code: str) -> Optional[Code]:
    """One regrouped code, dashes already stripped, or None if the core skips it."""
    if "X" in code or "?" in code:
        return None
    if len(code) == 9:
        d = gg2bin.genie_decode(code)
        if not d:
            return None
        addr, replace, compare = d
        return Code(_dashed(code, 3), "patch", addr, replace, compare)
    if len(code) == 6:
        d = [int(c, 16) for c in code]
        addr = ((d[5] ^ 0xF) << 12) | (d[2] << 8) | (d[3] << 4) | d[4]
        return Code(_dashed(code, 3), "patch", addr, (d[0] << 4) | d[1])
    if len(code) == 8:
        d = gg2bin.par_decode(code)
        if not d:
            return None
        addr, value = d
        if not WORK_RAM[0] <= addr <= WORK_RAM[1]:
            return None
        return Code(_dashed(code, 4), "poke", addr, value)
    return None


def codes_of(text: str) -> list[Code]:
    """The codes the core takes from one `cheatN_code` value."""
    parts = gg2bin.split_codes(text)
    if not parts:
        return []
    return [c for c in (decode(p) for p in parts) if c is not None]


def parse(data: bytes, max_groups: int = 1 << 30) -> list[Group]:
    """Cheat groups from a Game Gear `.cht`, in file order.

    Keys are gathered the way `gg2bin.model` gathers them. A cheat with no
    `_enable` key is on.
    """
    text = data.decode("utf-8", "replace")
    descs = dict(re.findall(r'cheat(\d+)_desc\s*=\s*"([^"]*)"', text))
    codes = dict(re.findall(r'cheat(\d+)_code\s*=\s*"([^"]*)"', text))
    enables = dict(re.findall(r"cheat(\d+)_enable\s*=\s*(\w+)", text))

    groups: list[Group] = []
    for n in sorted(codes, key=int):
        if len(groups) >= max_groups:
            break
        on = enables.get(n, "true").lower() in ("true", "1")
        groups.append(Group(len(groups), codes_of(codes[n]), descs.get(n), on))
    return groups


def applied_by(code) -> str:
    """Game Genie patches a ROM read; Pro Action Replay writes RAM."""
    return getattr(code, "kind", "")
