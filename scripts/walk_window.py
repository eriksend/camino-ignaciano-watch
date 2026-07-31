"""Shared walk-window logic.

The walk dates drive notification tiering in more than one script, so they live
here rather than being duplicated (and drifting) across them. Change them once.
"""
from __future__ import annotations

from datetime import date, timedelta

WALK_START = date(2027, 4, 20)   # matches the planner's own start:"2027-04-20"
# Deliberately LATER than the nominal finish. At one stage per day the 27 stages
# end 2027-05-16, but the planner already flags stages 9, 13, 21 and 25 for
# splitting (the 30.6-39.3 km days) and those four alone push the finish to exactly
# 2027-05-20. Add the intended rest days and the real finish is 05-23 to 05-26.
# WALK_END gates in_walk_window(), which decides alert-vs-quiet and a 24h-vs-weekly
# reminder cadence — so setting it to the nominal finish would drop the monitor out
# of walk mode while still on the Catalan stages, the highest-risk stretch.
WALK_END = date(2027, 5, 27)

# A restriction or hazard is worth a push slightly before departure too — by then
# it is actionable (re-plan a stage, move a booking) rather than mere trivia.
LEAD_DAYS = 21


def in_walk_window(today: date) -> bool:
    return WALK_START <= today <= WALK_END


def near_walk(today: date, lead_days: int = LEAD_DAYS) -> bool:
    """True inside the walk window or within lead_days before it."""
    return (WALK_START - timedelta(days=lead_days)) <= today <= WALK_END
