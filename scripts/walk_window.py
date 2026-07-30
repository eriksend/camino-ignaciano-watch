"""Shared walk-window logic.

The walk dates drive notification tiering in more than one script, so they live
here rather than being duplicated (and drifting) across them. Change them once.
"""
from __future__ import annotations

from datetime import date, timedelta

WALK_START = date(2027, 4, 19)
WALK_END = date(2027, 5, 20)

# A restriction or hazard is worth a push slightly before departure too — by then
# it is actionable (re-plan a stage, move a booking) rather than mere trivia.
LEAD_DAYS = 21


def in_walk_window(today: date) -> bool:
    return WALK_START <= today <= WALK_END


def near_walk(today: date, lead_days: int = LEAD_DAYS) -> bool:
    """True inside the walk window or within lead_days before it."""
    return (WALK_START - timedelta(days=lead_days)) <= today <= WALK_END
