"""Change memory for API-derived conditions (FWI, Pla ALFA, fire clusters).

WHY THIS EXISTS

`fetch_sources.py` only emits when a page's hash changes. The API-derived scripts
had no equivalent: they re-emitted every run for as long as a condition held. A
sustained ALFA level 3 or a hot FWI spell would therefore produce several
near-identical findings per day — alert-tier during the walk, i.e. flooding the
push digest exactly when it must be signal. And the routine's own dedup
(`sha1(url + title)`, with a constant URL for these sources) failed both ways:
a date-bearing title floods, a stable title collides and a genuinely worse day is
silently dropped.

THE RULES

- Emit when the *fingerprint* changes. Fingerprints describe decision-relevant
  state (a danger class, a level, "closed") and must never contain a timestamp,
  a raw float, a count or a free-text list — all of which churn while the
  underlying condition is unchanged.
- Emit immediately when severity RISES, even inside a suppression window. This is
  what makes suppression safe: an escalation can never be swallowed.
- Emit a periodic reminder while a condition persists, so a three-week closure
  does not go silent. The interval tightens as the walk approaches.
- Suppression is never invisible: every call updates the entry, and the ledger is
  committed, so a suppressed-but-active condition still shows in the daily diff
  and can be rendered as a standing "active conditions" panel.

The ledger is keyed by condition, not by URL, because these conditions all share
one constant URL — which is precisely why URL-based dedup could not work.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone

from walk_window import WALK_END, WALK_START, near_walk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_FILE = os.path.join(ROOT, "state", "conditions.json")

# Reminder cadence for an UNCHANGED but still-active condition.
REMIND_HOURS_FAR = 168.0    # weekly while the walk is far off
REMIND_HOURS_NEAR = 72.0    # every 3 days once inside near_walk()
REMIND_HOURS_WALKING = 24.0  # daily while actually walking


def load(path: str = LEDGER_FILE) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save(ledger: dict, path: str = LEDGER_FILE) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=2, sort_keys=True)


def remind_hours(today: date) -> float:
    if WALK_START <= today <= WALK_END:
        return REMIND_HOURS_WALKING
    if near_walk(today):
        return REMIND_HOURS_NEAR
    return REMIND_HOURS_FAR


def _parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        dt = datetime.fromisoformat(stamp)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def should_emit(ledger: dict, key: str, fingerprint: str, rank: int,
                today: date, now: datetime | None = None) -> tuple[bool, str]:
    """Decide whether a condition is worth a finding, and record that we saw it.

    Returns (emit, reason). `rank` is an ordinal severity used only to detect
    escalation; `fingerprint` is what identifies a materially distinct state.
    Always mutates `ledger` so an active-but-suppressed condition stays visible.
    """
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    entry = ledger.setdefault(key, {})

    previous_fp = entry.get("fingerprint")
    peak_rank = int(entry.get("peak_rank", -1))
    last_emitted = _parse(entry.get("last_emitted"))

    entry["last_seen"] = stamp
    entry.setdefault("first_seen", stamp)
    entry["rank"] = rank
    entry["fingerprint"] = fingerprint
    entry["peak_rank"] = max(peak_rank, rank)

    if previous_fp is None:
        reason, emit = "new", True
    elif rank > peak_rank:
        # Escalation always breaks through, even mid-suppression.
        reason, emit = "escalated", True
    elif fingerprint != previous_fp:
        reason, emit = "changed", True
    elif last_emitted is None:
        reason, emit = "never-emitted", True
    elif now - last_emitted >= timedelta(hours=remind_hours(today)):
        reason, emit = "reminder", True
    else:
        reason, emit = "suppressed", False

    if emit:
        entry["last_emitted"] = stamp
        entry["emit_count"] = int(entry.get("emit_count", 0)) + 1
    entry["last_reason"] = reason
    return emit, reason


def clear(ledger: dict, key: str, today: date,
          now: datetime | None = None) -> bool:
    """Mark a condition as no longer active.

    Returns True if the clearing itself is worth reporting — while walking, "the
    closure lifted" is more useful than the closure was.
    """
    entry = ledger.get(key)
    if not entry or entry.get("fingerprint") is None:
        return False
    now = now or datetime.now(timezone.utc)
    was_rank = int(entry.get("rank", 0))
    entry["fingerprint"] = None
    entry["rank"] = 0
    entry["cleared_at"] = now.isoformat(timespec="seconds")
    entry["last_reason"] = "cleared"
    return near_walk(today) and was_rank > 0


def active(ledger: dict) -> list[dict]:
    """Conditions currently in force, for the report's standing panel."""
    out = []
    for key, entry in sorted(ledger.items()):
        if entry.get("fingerprint") is not None:
            out.append({"key": key, **entry})
    return out


def cap_alerts(items: list[dict], limit: int = 6) -> list[dict]:
    """Circuit breaker: never let one run push more than `limit` alert items.

    Insurance against any future logic bug — including one written under time
    pressure in March 2027. Excess alerts collapse into a single digest item so
    the information survives but the phone does not melt.
    """
    alerts = [i for i in items if i.get("notify") == "alert"]
    if len(alerts) <= limit:
        return items
    quiet = [i for i in items if i.get("notify") != "alert"]
    keep = alerts[:limit]
    rest = alerts[limit:]
    digest = {
        **rest[0],
        "kind": "digest",
        "weight": 1.0,
        "text": (f"{len(rest)} further alert-tier condition(s) this run, collapsed "
                 f"to keep the digest readable: "
                 + " | ".join(i.get("text", "")[:110] for i in rest[:8])),
    }
    return quiet + keep + [digest]
