"""Money guard. Fail closed: no readable zero means do not click.

The guard is the only thing standing between a broken token and a real charge,
so an unreadable total is treated as "not free", never as "probably free".

Judgement is scoped to *today's* charge. A trial dialog legitimately mentions
money that is not due today ("then $19.99/month, starting <date>"), so a bare
"any $0.00 anywhere" test is not enough either way: it used to pass a dialog
that charged $199.99 today next to a $0.00 tax line. Amounts anchored to a
due-today label decide first. Unanchored non-zero amounts then pass in two
tiers of evidence: when the dialog itself shows today's amount is zero, it is
enough that every other price is future-scoped; with
no such evidence (the "Upcoming charges" shape of layers 2-4) every price
must be preceded by an explicit start marker ("starting", "then", "after").
A bare billing period is not a deferral: "$19.99/month · Billed monthly" is
a paid dialog and fails.

One dialog shape needs a itemisation rule: in the layers 2-4 "Upcoming
charges" dialog the per-product prices ("Google AI Pro $19.99", "YouTube
Premium $11.99") are printed *after* the combined future total they belong
to ("Starting May 8, 2028 $31.98/month"), too far from any start marker to
pass the scoping tests on their own. Such an amount counts as future-scoped
only when it is part of an exact itemisation: at least two unexplained
amounts directly following a scoped total and summing to it to the cent,
with no other unexplained money after them. Anything else still fails.
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
# Explicit temporal qualifiers that can establish when an amount applies.
_FUTURE_PREFIX_HINT = re.compile(
    r"starting|starts\s+on|then\b|after\s+(?:the\s+)?(?:trial|free)|mulai\s+tanggal",
    re.IGNORECASE,
)
_FUTURE_SUFFIX_HINT = re.compile(
    r"starting|starts\s+on|after\s+(?:the\s+)?(?:trial|free)|renews|renewal|mulai\s+tanggal",
    re.IGNORECASE,
)
_RECURRING_RATE = re.compile(
    r"/\s?(?:month|mo|year|yr)\b|per\s+(?:month|year)", re.IGNORECASE
)
_CLAUSE_BOUNDARY = re.compile(r"[·•;|]|[.!?](?=\s)")
_CHARGE_LABEL = re.compile(
    r"\b(?:activation\s+fee|(?:processing|service|convenience)\s+fee|fee|"
    r"tax(?:es)?|amount\s+due|due\s+today|total\s+today|charged\s+today|"
    r"pay\s+today|today'?s\s+(?:total|charge))\b",
    re.IGNORECASE,
)
_LABEL_TO_AMOUNT = re.compile(r"^[\s:=,-]*(?:(?:of|is)\s+)?$", re.IGNORECASE)
_AMOUNT_TO_LABEL = re.compile(r"^[\s,:=-]*$")
# An explicit start marker before an amount is stronger evidence than a
# recurring period or renewal label, neither of which establishes deferral.
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


def _clause_span(flat: str, position: int) -> tuple[int, int]:
    """Return the separator-bounded text segment containing `position`."""
    start, end = 0, len(flat)
    for separator in _CLAUSE_BOUNDARY.finditer(flat):
        if separator.end() <= position:
            start = separator.end()
        elif separator.start() >= position:
            end = separator.start()
            break
    return start, end


def _has_unpriced_charge_label(
    flat: str, hits: list[tuple[int, int, str]]
) -> bool:
    """Fail closed when a charge-bearing label has no nearby amount in its clause."""
    for label in _CHARGE_LABEL.finditer(flat):
        if _NEGATED.search(flat[max(0, label.start() - 30):label.start()]):
            continue
        clause_start, clause_end = _clause_span(flat, label.start())
        clause_hits = [h for h in hits if clause_start <= h[0] < clause_end]
        before = [h for h in clause_hits if h[1] <= label.start()]
        after = [h for h in clause_hits if h[0] >= label.end()]
        if before and _AMOUNT_TO_LABEL.fullmatch(flat[before[-1][1]:label.start()]):
            continue
        if after and _LABEL_TO_AMOUNT.fullmatch(flat[label.end():after[0][0]]):
            continue
        return True
    return False


def _future_scoped(
    flat: str,
    hit: tuple[int, int, str],
    hits: list[tuple[int, int, str]] | None = None,
) -> bool:
    start, end, _raw = hit
    if hits is None:
        hits = [(m.start(), m.end(), m.group(0)) for m in MONEY.finditer(flat)]
    index = hits.index(hit)
    clause_start, clause_end = _clause_span(flat, start)
    previous = hits[index - 1] if index else None
    next_hit = hits[index + 1] if index + 1 < len(hits) else None
    before_start = max(clause_start, previous[1] if previous else clause_start)
    after_end = min(clause_end, next_hit[0] if next_hit else clause_end)
    before = flat[before_start:start]
    after = flat[end:after_end]

    # A previous recurring amount may carry its own start-date suffix. Do not
    # reuse that suffix for the next amount; an explicit later "then" starts a
    # new price. A start marker after a non-recurring component can still
    # introduce the current amount (for example, an itemised upcoming total).
    if previous and before_start == previous[1] and not _is_zero(previous[2]):
        rate = _RECURRING_RATE.search(before)
        if rate:
            then_markers = [
                marker
                for marker in re.finditer(r"\bthen\b", before, re.IGNORECASE)
                if marker.start() > rate.start()
            ]
            before = before[then_markers[-1].start():] if then_markers else ""

    return bool(
        _FUTURE_PREFIX_HINT.search(before)
        or _FUTURE_SUFFIX_HINT.search(after)
    )


def _start_scoped(flat: str, hit: tuple[int, int, str]) -> bool:
    """True when an explicit start marker precedes the amount.

    Only the text *before* the amount counts: the marker announces when
    this price begins, so it stands in front of it ("Starting May 8, 2027
    $13.99/month"). A marker after the amount describes something else.
    """
    start, _end, _raw = hit
    return bool(_START_HINT.search(flat[max(0, start - 64):start]))


def _value(raw: str) -> int | None:
    """Parse a money string to cents; None when the shape is unrecognised.

    Handles the formats the dialogs actually use: "$31.98", "$1,299.00"
    (comma = thousands) and "0,00" (comma = decimal). Anything exotic
    returns None so the itemisation rule simply does not apply.
    """
    token = re.search(r"[\d][\d.,]*", raw)
    if not token:
        return None
    num = token.group(0).rstrip(".,")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d{1,2})?", num):
        num = num.replace(",", "")
    elif re.fullmatch(r"\d+,\d{1,2}", num):
        num = num.replace(",", ".")
    elif not re.fullmatch(r"\d+(\.\d{1,2})?", num):
        return None
    try:
        return round(float(num) * 100)
    except ValueError:
        return None


def _components_explain(
    hits: list[tuple[int, int, str]], scoped: list[bool]
) -> bool:
    """True when every unexplained amount itemises one scoped total.

    `scoped` is parallel to `hits` (only non-zero entries are consulted).
    The unexplained non-zero amounts must all follow one scoped non-zero
    total, be the only non-zero amounts after it, number at least two, and
    sum to that total exactly. Zero amounts (tax lines) may interleave.
    """
    nonzero = [i for i, h in enumerate(hits) if not _is_zero(h[2])]
    unexplained = [i for i in nonzero if not scoped[i]]
    if not unexplained:
        return False
    first = unexplained[0]
    total_idx = None
    for i in reversed(nonzero):
        if i < first and scoped[i]:
            total_idx = i
            break
    if total_idx is None:
        return False
    after = [i for i in nonzero if i > total_idx]
    if after != unexplained or len(unexplained) < 2:
        return False
    total = _value(hits[total_idx][2])
    parts = [_value(hits[i][2]) for i in unexplained]
    if total is None or any(p is None for p in parts):
        return False
    return sum(parts) == total  # type: ignore[arg-type]


def judge(text: str) -> Verdict:
    flat = " ".join(re.sub(r"[\r\n]+", " · ", text).split())
    if not flat:
        return Verdict(False, [], "modal kosong / tak terbaca")
    hits = [(m.start(), m.end(), m.group(0)) for m in MONEY.finditer(flat)]
    amounts = [h[2] for h in hits]

    if _has_unpriced_charge_label(flat, hits):
        return Verdict(False, amounts, "label biaya tidak memiliki nominal yang terbaca")

    today = _today_amounts(flat, hits)
    if today:
        if any(not _is_zero(a) for a in today):
            return Verdict(False, amounts, "tagihan hari ini bukan nol")
        nonzero = [h for h in hits if not _is_zero(h[2])]
        if not nonzero:
            return Verdict(True, amounts, "total hari ini nol")
        if all(_future_scoped(flat, h, hits) for h in nonzero):
            return Verdict(True, amounts, "total hari ini nol; harga lain terikat ke masa depan")
        return Verdict(False, amounts, "ada biaya tambahan tanpa waktu masa depan yang jelas")

    if not amounts:
        return Verdict(False, amounts, "tidak ada nominal yang bisa dibaca")

    nonzero = [h for h in hits if not _is_zero(h[2])]
    if not nonzero:
        return Verdict(True, amounts, "terbaca total nol")
    # Without a due-today amount, positive prices pass only with future
    # evidence. With a zero amount, each positive price needs its own explicit
    # temporal qualifier. Without a zero amount or free wording (the "Upcoming
    # charges" shape), each positive price needs an explicit start marker;
    # a recurring period alone does not establish deferral.
    nonzero_idx = [i for i, h in enumerate(hits) if not _is_zero(h[2])]
    has_free_evidence = len(nonzero_idx) < len(hits) or bool(FREE_WORD.search(flat))
    if has_free_evidence:
        scoped = [_future_scoped(flat, h, hits) for h in hits]
        if all(scoped[i] for i in nonzero_idx):
            return Verdict(True, amounts, "nominal bukan nol hanya harga masa depan")
    else:
        scoped = [_start_scoped(flat, h) for h in hits]
        if all(scoped[i] for i in nonzero_idx):
            return Verdict(True, amounts, "semua nominal terikat penanda mulai masa depan")
    # Rincian komponen: nominal yang belum terjelaskan boleh jadi rincian
    # persis dari satu total masa depan ("$31.98" = "$19.99" + "$11.99").
    if _components_explain(hits, scoped):
        return Verdict(True, amounts, "nominal tak terikat adalah rincian total masa depan")
    return Verdict(False, amounts, "ada nominal bukan nol tanpa label hari ini")
