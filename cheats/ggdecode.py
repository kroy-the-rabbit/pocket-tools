#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Decode Game Boy cheat codes (Game Genie and GameShark) to address/value.

Game Genie, after SameBoy's Core/cheats.c (the reference the RTL parser in
P2 must match):

    ABC-DEF-GHI  (9 digits; 6-digit ABC-DEF has no compare byte)
    value   = AB
    address = {~F, C, D, E}         i.e. rot-right CDEF by one nibble, flip top nibble
    compare = rotr2(GI) ^ 0xBA      H is a filler digit and ignored
    ROM only: address must be < 0x8000

GameShark:

    TTVVAAAA
    TT = type (01 = any bank RAM write; otherwise low nibble = SRAM bank)
    VV = value, AAAA = address little-endian

Also prints the 129-bit `gg_code` word the CODES module takes, and the
`define block used by the P1 smoke test.
"""
import re
import sys
from dataclasses import dataclass
from typing import Optional

HEX = re.compile(r"^[0-9A-Fa-f]+$")


@dataclass
class Cheat:
    kind: str            # "gg" or "gs"
    address: int
    value: int
    compare: Optional[int] = None   # gg only
    bank: Optional[int] = None      # gs only; None = any bank
    raw: str = ""                   # the token as written in the file

    def gg_code_word(self) -> int:
        """129-bit CODES shift-in word: {1, flags[31:0], addr[31:0], cmp[31:0], data[31:0]}."""
        use_cmp = 1 if self.compare is not None else 0
        return (1 << 128) | (use_cmp << 96) | (self.address << 64) | ((self.compare or 0) << 32) | self.value

    def p1_defines(self) -> str:
        return "\n".join([
            f"`define P1_GG_ADDR   16'h{self.address:04X}",
            f"`define P1_GG_DATA    8'h{self.value:02X}",
            f"`define P1_GG_CMP     8'h{(self.compare or 0):02X}",
            f"`define P1_GG_USECMP  1'b{1 if self.compare is not None else 0}",
        ])


def decode_game_genie(code: str) -> Cheat:
    digits = code.replace("-", "").strip()
    if len(digits) not in (6, 9) or not HEX.match(digits):
        raise ValueError(f"not a Game Genie code: {code!r}")
    d = [int(c, 16) for c in digits]
    value = (d[0] << 4) | d[1]
    address = ((d[5] ^ 0xF) << 12) | (d[2] << 8) | (d[3] << 4) | d[4]
    if address > 0x7FFF:
        raise ValueError(f"Game Genie address {address:#06x} is not in ROM: {code!r}")
    compare = None
    if len(digits) == 9:
        raw = (d[6] << 4) | d[8]            # digit 7 (index 7) is filler
        compare = (((raw >> 2) | (raw << 6)) & 0xFF) ^ 0xBA
    return Cheat("gg", address, value, compare)


def encode_game_genie(address: int, value: int, compare: Optional[int] = None, filler: int = 0) -> str:
    """Inverse of decode_game_genie, for tests."""
    if not 0 <= address <= 0x7FFF:
        raise ValueError("ROM addresses only")
    a = [(address >> s) & 0xF for s in (12, 8, 4, 0)]
    digits = [value >> 4, value & 0xF, a[1], a[2], a[3], a[0] ^ 0xF]
    if compare is not None:
        raw = compare ^ 0xBA
        raw = ((raw << 2) | (raw >> 6)) & 0xFF   # rotl2 undoes rotr2
        digits += [raw >> 4, filler, raw & 0xF]
    s = "".join(f"{x:X}" for x in digits)
    return "-".join([s[0:3], s[3:6]] + ([s[6:9]] if compare is not None else []))


def decode_gameshark(code: str) -> Cheat:
    digits = code.strip()
    if len(digits) != 8 or not HEX.match(digits):
        raise ValueError(f"not a GameShark code: {code!r}")
    t = int(digits[0:2], 16)
    value = int(digits[2:4], 16)
    address = int(digits[6:8] + digits[4:6], 16)   # little-endian
    bank = None if t == 0x01 else (t & 0xF)
    return Cheat("gs", address, value, None, bank)


def decode(code: str) -> Cheat:
    stripped = code.replace("-", "")
    if len(stripped) == 8 and "-" not in code:
        return decode_gameshark(code)
    return decode_game_genie(code)


def _selftest() -> None:
    # Structural cases worked by hand from the reference algorithm.
    c = decode_game_genie("000-00F-000")          # F inverted -> top nibble 0
    assert (c.address, c.value) == (0x0000, 0x00), c
    c = decode_game_genie("123-45A")              # 6 digits, no compare; ~A = 5
    assert (c.address, c.value, c.compare) == (0x5345, 0x12, None), c
    c = decode_game_genie("ABC-DEF-GHI".replace("G", "0").replace("H", "5").replace("I", "0"))
    assert c.address == 0x0CDE and c.value == 0xAB and c.compare == (0x00 ^ 0xBA), c
    # compare: raw 0xFF -> rotr2 = 0xFF -> ^0xBA = 0x45
    c = decode_game_genie("000-00F-F0F")
    assert c.compare == 0x45, hex(c.compare)
    # raw 0x01 -> rotr2 = 0x40 -> ^0xBA = 0xFA
    c = decode_game_genie("000-00F-001")
    assert c.compare == 0xFA, hex(c.compare)
    # filler digit ignored
    assert decode_game_genie("000-00F-0A1").compare == decode_game_genie("000-00F-071").compare
    # RAM addresses rejected
    try:
        decode_game_genie("000-000-000")          # F=0 -> top nibble 0xF -> 0xF000
        raise AssertionError("should reject")
    except ValueError:
        pass
    # round trip over the whole space of (addr nibble pattern, value, compare)
    import random
    rng = random.Random(1)
    for _ in range(20000):
        a, v = rng.randrange(0x8000), rng.randrange(256)
        cmp = rng.choice([None, rng.randrange(256)])
        s = encode_game_genie(a, v, cmp, filler=rng.randrange(16))
        d = decode_game_genie(s)
        assert (d.address, d.value, d.compare) == (a, v, cmp), (s, d)
    # GameShark
    g = decode_gameshark("010138CD")
    assert (g.address, g.value, g.bank) == (0xCD38, 0x01, None), g
    g = decode_gameshark("91FF0BD2")
    assert (g.address, g.value, g.bank) == (0xD20B, 0xFF, 1), g
    # CODES word layout
    w = decode_game_genie("000-00F-000").gg_code_word()
    assert w >> 128 == 1 and (w >> 96) & 1 == 1
    print("selftest ok")


if __name__ == "__main__":
    if len(sys.argv) == 1 or sys.argv[1] == "--test":
        _selftest()
        sys.exit(0)
    for arg in sys.argv[1:]:
        c = decode(arg)
        print(f"{arg}: {c.kind.upper()} addr={c.address:#06x} value={c.value:#04x}"
              + (f" compare={c.compare:#04x}" if c.compare is not None else "")
              + (f" bank={c.bank}" if c.bank is not None else ""))
        if c.kind == "gg":
            print(c.p1_defines())
