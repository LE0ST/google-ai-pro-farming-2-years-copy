"""Money guard. Fail closed: no readable zero means do not click.

The guard is the only thing standing between a broken token and a real charge,
so an unreadable total is treated as "not free", never as "probably free".

Judgement is scoped to *today's* charge. A trial dialog legitimately mentions
money that is not due today ("then $19.99/month, starting <date>"), so a bare
"any $0.00 anywhere" test is not enough either way: it used to pass a dialog
that charged $199.99 today next to a $0.00 tax line. Amounts anchored to a
due-today label decide first; unanchored non-zero amounts only pass when they
are visibly future prices (per month/year, starting later, a renewal).
"""

from __future__ import annotations

import re

MONEY = re.compile(r"(?:US\s?\$|USD|\$|Rp\.?|€)\s?[\d.,]+", re.IGNORECASE)
ZERO = re.compile(r"(?:US\s?\$|USD|\$|Rp\.?|€)\s?0(?:[.,]00)?\b", re.IGNORECASE)
FREE_WORD = re.compile(r"\b(free|gratis)\b|\b0\s?%|\btrial\b", re.IGNORECASE)

# Label yang menambatkan nominal ke tagihan *hari ini*: "Due today $0.00",
# "$0.00 due today", "Total today …", "tagihan hari ini …".
TODAY_LABEL = re.compile(
    r"(due\s+today|total\s+today|charged\s+today|pay\s+today|amount\s+due|"
    r"today'?s\s+(?:total|charge)|total\s+due|tagihan\s+hari\s+ini)",
    re.IGNORECASE,
)
# Konteks yang membuat nominal bukan-nol jelas harga masa depan, bukan tagihan
# hari ini: tarif berkala, tanggal mulai, perpanjangan.
_FUTURE_HINT = re.compile(
    r"/\s?(?:month|mo|year|yr)\b|per\s+(?:month|year)|starting|starts\s+on|"
    r"then\b|after\s+(?:the\s+)?(?:trial|free)|renews|renewal|mulai\s+tanggal",
    re.IGNORECASE,
)
# "You won't be charged today" adalah janji *tidak* menagih; label di
# belakang negasi seperti ini tidak menambatkan nominal apa pun.
_NEGATED = re.compile(r"(?:won'?t|will\s+not|never|not)\s+be\s*$|no\s+charge\s*$", re.IGNORECASE)


class Verdict:
    __slots__ = ("safe", "amounts", "reason")

    def __init__(self, safe: bool, amounts: list[str], reason: str) -> None:
        self.safe = safe
        self.amounts = amounts
        self.reason = reason

    def __repr__(self) -> str:
        return f"Verdict(safe={self.safe}, amounts={self.amounts}, reason={self.reason!r})"


def _is_zero(raw: str) -> bool:
    """True when every digit of the amount is 0 ("$0.00", "Rp0", "$0,00")."""
    digits = re.sub(r"\D", "", raw)
    return bool(digits) and set(digits) == {"0"}


def _today_amounts(flat: str, hits: list[tuple[int, int, str]]) -> list[str]:
    """Amounts anchored to a due-today label: the nearest one on either side.

    Only the nearest amount counts — in flattened dialog text the next line
    ("then $19.99/month") follows within a few dozen characters and must not
    be mistaken for today's total.
    """
    found: list[str] = []
    for label in TODAY_LABEL.finditer(flat):
        if _NEGATED.search(flat[max(0, label.start() - 30):label.start()]):
            continue
        after = [h for h in hits if label.end() <= h[0] <= label.end() + 48]
        if after:
            found.append(after[0][2])
        before = [h for h in hits if label.start() - 24 <= h[1] <= label.start()]
        if before:
            found.append(before[-1][2])
    return found


def _future_scoped(flat: str, hit: tuple[int, int, str]) -> bool:
    start, end, _raw = hit
    return bool(_FUTURE_HINT.search(flat[max(0, start - 48):end + 48]))


def judge(text: str) -> Verdict:
    flat = " ".join(text.split())
    if not flat:
        return Verdict(False, [], "modal kosong / tak terbaca")
    hits = [(m.start(), m.end(), m.group(0)) for m in MONEY.finditer(flat)]
    amounts = [h[2] for h in hits]

    today = _today_amounts(flat, hits)
    if today:
        if any(not _is_zero(a) for a in today):
            return Verdict(False, amounts, "tagihan hari ini bukan nol")
        return Verdict(True, amounts, "total hari ini nol")

    if not amounts:
        if FREE_WORD.search(flat):
            return Verdict(True, amounts, "tidak ada nominal, ada kata free/trial")
        return Verdict(False, amounts, "tidak ada nominal yang bisa dibaca")

    nonzero = [h for h in hits if not _is_zero(h[2])]
    if not nonzero:
        return Verdict(True, amounts, "terbaca total nol")
    if all(_future_scoped(flat, h) for h in nonzero) and (
        len(nonzero) < len(hits) or FREE_WORD.search(flat)
    ):
        return Verdict(True, amounts, "nominal bukan nol hanya harga masa depan")
    return Verdict(False, amounts, "ada nominal bukan nol tanpa label hari ini")
