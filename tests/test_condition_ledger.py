"""condition_ledger gates every alert. A bug here is silent in production:
either the phone floods, or an escalation is swallowed and never mentioned."""
import datetime as dt

import condition_ledger as cl

FAR = dt.date(2026, 8, 1)
NEAR = dt.date(2027, 4, 5)
WALKING = dt.date(2027, 4, 25)


def T(d, h=12):
    return dt.datetime(d.year, d.month, d.day, h, tzinfo=dt.timezone.utc)


def test_first_sight_emits():
    L = {}
    emit, why = cl.should_emit(L, "k", "3", 3, FAR, T(FAR))
    assert emit and why == "new"


def test_unchanged_suppressed_but_still_recorded():
    L = {}
    cl.should_emit(L, "k", "3", 3, FAR, T(FAR, 9))
    emit, why = cl.should_emit(L, "k", "3", 3, FAR, T(FAR, 10))
    assert not emit and why == "suppressed"
    # suppression must stay VISIBLE or a live condition looks like nothing
    assert L["k"]["last_seen"] == T(FAR, 10).isoformat(timespec="seconds")


def test_escalation_breaks_through_suppression():
    """The anti-silent-drop case: a worse day must never be swallowed."""
    L = {}
    cl.should_emit(L, "k", "3", 3, FAR, T(FAR, 9))
    emit, why = cl.should_emit(L, "k", "4", 4, FAR, T(FAR, 10))
    assert emit and why == "escalated"


def test_fingerprint_change_emits():
    L = {}
    cl.should_emit(L, "k", "3", 3, FAR, T(FAR, 9))
    emit, why = cl.should_emit(L, "k", "2", 2, FAR, T(FAR, 10))
    assert emit and why == "changed"


def test_reminder_after_interval_far_off():
    L = {}
    cl.should_emit(L, "k", "3", 3, FAR, T(FAR, 9))
    assert not cl.should_emit(L, "k", "3", 3, FAR, T(FAR + dt.timedelta(days=2)))[0]
    emit, why = cl.should_emit(L, "k", "3", 3, FAR, T(FAR + dt.timedelta(days=8)))
    assert emit and why == "reminder"


def test_reminder_interval_tightens_toward_the_walk():
    assert cl.remind_hours(FAR) > cl.remind_hours(NEAR) > cl.remind_hours(WALKING)
    assert cl.remind_hours(WALKING) == 24.0


def test_sustained_condition_emits_rarely_far_off_and_daily_while_walking():
    far = {}
    n_far = sum(cl.should_emit(far, "s", "4", 4, FAR + dt.timedelta(days=i),
                               T(FAR + dt.timedelta(days=i)))[0]
                for i in range(30))
    walk = {}
    n_walk = sum(cl.should_emit(walk, "s", "4", 4, WALKING + dt.timedelta(days=i),
                                T(WALKING + dt.timedelta(days=i)))[0]
                 for i in range(30) if i < 26)
    assert n_far <= 6, "a sustained condition should not emit daily when far off"
    assert n_walk >= 20, "it must still speak up daily while walking"


def test_recurrence_after_clearing_emits_again():
    """A closure that lifts and returns is news both times."""
    L = {}
    cl.should_emit(L, "c", "closed", 4, WALKING, T(WALKING, 9))
    cl.clear(L, "c", WALKING, T(WALKING, 10))
    emit, _ = cl.should_emit(L, "c", "closed", 4, WALKING, T(WALKING, 11))
    assert emit


def test_clear_is_newsworthy_only_near_the_walk():
    L = {}
    cl.should_emit(L, "c", "closed", 4, WALKING, T(WALKING, 9))
    assert cl.clear(L, "c", WALKING, T(WALKING, 10)) is True
    L2 = {}
    cl.should_emit(L2, "c", "closed", 4, FAR, T(FAR, 9))
    assert cl.clear(L2, "c", FAR, T(FAR, 10)) is False


def test_clear_on_unknown_key_is_safe():
    assert cl.clear({}, "nope", WALKING) is False


def test_active_lists_only_conditions_in_force():
    L = {}
    cl.should_emit(L, "a", "closed", 4, FAR, T(FAR))
    cl.should_emit(L, "b", "closed", 4, FAR, T(FAR))
    cl.clear(L, "b", FAR, T(FAR))
    assert [c["key"] for c in cl.active(L)] == ["a"]


def test_cap_alerts_collapses_excess_and_keeps_quiet_items():
    items = ([{"notify": "alert", "text": f"a{i}"} for i in range(11)]
             + [{"notify": "quiet", "text": "q"}])
    out = cl.cap_alerts(items, limit=6)
    assert sum(1 for i in out if i.get("kind") == "digest") == 1
    assert sum(1 for i in out if i["notify"] == "quiet") == 1
    assert len(out) < len(items)


def test_cap_alerts_passes_small_batches_through_untouched():
    items = [{"notify": "alert", "text": "a"}, {"notify": "quiet", "text": "q"}]
    assert cl.cap_alerts(items, limit=6) == items
