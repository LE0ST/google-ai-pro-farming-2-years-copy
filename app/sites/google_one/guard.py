"""Money guard. Fail closed: no readable zero means do not click.

The guard is the only thing standing between a broken token and a real charge,
so an unreadable total is treated as "not free", never as "probably free".
"""

from __future__ import annotations

import re

MONEY = re.compile(r"(?:US\s?\$|USD|\$|Rp\.?|€)\s?[\d.,]+", re.IGNORECASE)
ZERO = re.compile(r"(?:US\s?\$|USD|\$|Rp\.?|€)\s?0(?:[.,]00)?\b", re.IGNORECASE)
FREE_WORD = re.compile(r"\b(free|gratis)\b|\b0\s?%|\btrial\b", re.IGNORECASE)


class Verdict:
    __slots__ = ("safe", "amounts", "reason")

    def __init__(self, safe: bool, amounts: list[str], reason: str) -> None:
        self.safe = safe
        self.amounts = amounts
        self.reason = reason

    def __repr__(self) -> str:
        return f"Verdict(safe={self.safe}, amounts={self.amounts}, reason={self.reason!r})"


def judge(text: str) -> Verdict:
    flat = " ".join(text.split())
    amounts = MONEY.findall(flat)
    if not flat:
        return Verdict(False, [], "modal kosong / tak terbaca")
    if ZERO.search(flat):
        return Verdict(True, amounts, "terbaca total nol")
    if amounts:
        return Verdict(False, amounts, "ada nominal bukan nol")
    if FREE_WORD.search(flat):
        return Verdict(True, amounts, "tidak ada nominal, ada kata free/trial")
    return Verdict(False, amounts, "tidak ada nominal yang bisa dibaca")
