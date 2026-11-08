"""Discover where an account actually is, then decide.

Neither the database step nor the settings page tells the truth reliably: the step
column drifts, and Google never shows an end date until a subscription is already
cancelled. What *is* reliable is the projected term in a live offers dialog — open
one with a known layer armed and read the date back.

    no dates at all            -> untouched, start at layer 1
    term ≈ today + 12 months   -> start at layer 2
    term ≈ today + 18 months   -> start at layer 3
    term ≈ today + 24 months   -> nothing to stack, go straight to cancel
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

LA = ZoneInfo("America/Los_Angeles")

# months already granted -> (first layer still worth submitting, human label)
STAGES = [
    (0, "layer 1 (kosong)"),
    (12, "layer 2 (12 bulan)"),
    (18, "layer 3 (18 bulan)"),
    (24, "selesai (24 bulan)"),
]


def today() -> date:
    """Google bills in the browser timezone, which the runner pins to LA."""
    return datetime.now(LA).date()


def add_months(base: date, months: int) -> date:
    year = base.year + (base.month - 1 + months) // 12
    month = (base.month - 1 + months) % 12 + 1
    return date(year, month, min(base.day, calendar.monthrange(year, month)[1]))


def months_from(base: date, target: date) -> int:
    delta = (target.year - base.year) * 12 + (target.month - base.month)
    if target.day < base.day - 3:
        delta -= 1
    return delta


@dataclass
class Stage:
    start_layer: int          # 1..3, or 4 meaning "already full"
    have_months: int
    label: str

    @property
    def done(self) -> bool:
        return self.start_layer == 4


def stage_for(months: int, slack: int = 1) -> Stage:
    for threshold, label in reversed(STAGES):
        if months >= threshold - slack:
            return Stage(1 if threshold == 0 else min(4, threshold // 6), months, label)
    return Stage(1, months, f"{months} bulan")
