"""Money guard. Fail closed: no readable zero means do not click.

The guard is the only thing standing between a broken token and a real charge,
so an unreadable total is treated as "not free", never as "probably free".

Judgement is scoped to *today's* charge. A trial dialog legitimately mentions
money that is not due today ("then $19.99/month, starting <date>"), so a bare
"any $0.00 anywhere" test is not enough either way: it used to pass a dialog
that charged $199.99 today next to a $0.00 tax line. Amounts anchored to a
due-today label decide first. Unanchored non-zero amounts then pass in two
tiers of evidence: when the dialog itself shows today is free (a zero amount,
or a free/trial word), it is enough that every price is future-scoped; with
no such evidence (the "Upcoming charges" shape of layers 2-4) every price
must be preceded by an explicit start marker ("starting", "then", "after").
A bare billing period is not a deferral: "$19.99/month · Billed monthly" is
a paid dialog and fails.
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
# Penanda mulai masa depan yang *eksplisit*: nominal yang didahului kata ini
# belum ditagih karena masa harganya belum mulai ("Starting May 8, 2027
# $13.99"). Lebih ketat dari _FUTURE_HINT: satuan berkala (/month, /year)
# dan "renews" tidak termasuk — itu siklus tagihan biasa, bukan penundaan.
_START_HINT = re.compile(
    r"starting|starts\s+on|then\b|after\b|mulai\s+tanggal",
    re.IGNORECASE,
)
# "You won't be charged today" adalah janji *tidak* menagih; label di
# belakang negasi seperti ini tidak menambatkan nominal apa pun.
_NEGATED = re.compile(r"(?:won'?t|will\s+not|never|not)\s+be\s*$|no\s+charge\s*$", re.IGNORECASE)
# "Today $0.00": kata today telanjang tepat sebelum nominal juga menambatkan
# nominal itu ke hari ini, walau tanpa label "due/total" di depannya.
_TODAY_WORD = re.compile(r"\btoday\b", re.IGNORECASE)
# Tapi today yang didahului penanda rentang bukan label tagihan: pada
# "starting today …" / "free until today" kata itu menandai kapan harga
# berkala mulai/berakhir, bukan tagihan hari ini.
_BARE_SKIP = re.compile(r"(?:starting|until)\s*$", re.IGNORECASE)


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
    label_spans: list[tuple[int, int]] = []
    for label in TODAY_LABEL.finditer(flat):
        label_spans.append((label.start(), label.end()))
        if _NEGATED.search(flat[max(0, label.start() - 30):label.start()]):
            continue
        after = [h for h in hits if label.end() <= h[0] <= label.end() + 48]
        if after:
            found.append(after[0][2])
        before = [h for h in hits if label.start() - 24 <= h[1] <= label.start()]
        if before:
            found.append(before[-1][2])
    # "Today $X": today telanjang menambatkan nominal terdekat sesudahnya.
    # Lewati today yang sudah jadi bagian label di atas (sudah dihitung)
    # dan yang didahului penanda rentang ("starting"/"until").
    for word in _TODAY_WORD.finditer(flat):
        if any(start <= word.start() < end for start, end in label_spans):
            continue
        if _BARE_SKIP.search(flat[max(0, word.start() - 30):word.start()]):
            continue
        after = [h for h in hits if word.end() <= h[0] <= word.end() + 24]
        if after:
            found.append(after[0][2])
    return found


def _future_scoped(flat: str, hit: tuple[int, int, str]) -> bool:
    start, end, _raw = hit
    return bool(_FUTURE_HINT.search(flat[max(0, start - 48):end + 48]))


def _start_scoped(flat: str, hit: tuple[int, int, str]) -> bool:
    """True when an explicit start marker precedes the amount.

    Only the text *before* the amount counts: the marker announces when
    this price begins, so it stands in front of it ("Starting May 8, 2027
    $13.99/month"). A marker after the amount describes something else.
    """
    start, _end, _raw = hit
    return bool(_START_HINT.search(flat[max(0, start - 64):start]))


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
    # Tanpa tambatan hari ini, nominal bukan-nol hanya lolos sebagai harga
    # masa depan — dalam dua tingkat bukti:
    #   Tier A: dialog memuat bukti hari ini gratis (ada nominal nol, atau
    #     kata free/trial). Cukup setiap nominal terikat konteks masa depan
    #     (_FUTURE_HINT, termasuk satuan berkala & "renews").
    #   Tier B: tanpa nol dan tanpa kata free — bentuk "Upcoming charges"
    #     layer 2-4. Setiap nominal wajib didahului penanda mulai eksplisit
    #     (_start_scoped); satuan berkala saja tidak cukup, karena dialog
    #     berbayar polos pun berbentuk "$19.99/month · Billed monthly".
    has_free_evidence = len(nonzero) < len(hits) or bool(FREE_WORD.search(flat))
    if has_free_evidence and all(_future_scoped(flat, h) for h in nonzero):
        return Verdict(True, amounts, "nominal bukan nol hanya harga masa depan")
    if not has_free_evidence and all(_start_scoped(flat, h) for h in nonzero):
        return Verdict(True, amounts, "semua nominal terikat penanda mulai masa depan")
    return Verdict(False, amounts, "ada nominal bukan nol tanpa label hari ini")
