"""Per-run item ceiling.

The failure mode to guard against is deferral quietly becoming deletion: an item
that is dropped rather than postponed is invisible, because a smaller run looks
exactly like a quieter day.
"""
import fetch_sources as fs


def cand(name, notify="quiet", weight=1.0, deferred=0, changed="2026-01-01",
         kind="html"):
    entry = {"deferred_count": deferred}
    return {"item": {"source_name": name}, "entry": entry, "name": name,
            "weight": weight, "notify": notify, "kind": kind,
            "commit": ("digest", "trafilatura", "text"), "eid": name + "-id",
            "deferred_count": deferred, "last_change_seen": changed}


def test_under_the_ceiling_everything_passes():
    cs = [cand(f"s{i}") for i in range(5)]
    take, defer = fs.triage(cs, 25)
    assert len(take) == 5 and defer == []


def test_alert_tier_is_never_deferred():
    cs = [cand(f"quiet{i}", weight=1.5) for i in range(10)]
    cs.append(cand("the-alert", notify="alert", weight=0.1))
    take, defer = fs.triage(cs, 3)
    assert "the-alert" in [c["name"] for c in take]
    assert "the-alert" not in [c["name"] for c in defer]


def test_repeatedly_deferred_source_outranks_heavier_neighbours():
    """Anti-starvation: without this a churny high-weight source crowds out a
    quieter one forever."""
    cs = [cand(f"heavy{i}", weight=1.5) for i in range(5)]
    cs.append(cand("starved", weight=0.5, deferred=3))
    take, defer = fs.triage(cs, 2)
    assert "starved" in [c["name"] for c in take]


def test_weight_breaks_ties_when_deferral_counts_match():
    cs = [cand("light", weight=0.5), cand("heavy", weight=1.5)]
    take, _ = fs.triage(cs, 1)
    assert take[0]["name"] == "heavy"


def test_commit_writes_the_baseline_so_the_change_is_not_re_reported():
    c = cand("s", kind="html")
    fs.commit_candidate(c)
    assert c["entry"]["last_hash"] == "digest"
    assert c["entry"]["extracted"] == "text"
    assert c["entry"]["deferred_count"] == 0


def test_defer_withholds_the_baseline_so_the_change_IS_re_detected():
    """This is the whole mechanism: no last_hash written => next run recomputes
    the same digest, sees it still differs, and re-emits."""
    c = cand("s", kind="html")
    fs.defer_candidate(c)
    assert "last_hash" not in c["entry"]
    assert "extracted" not in c["entry"]
    assert c["entry"]["deferred_count"] == 1


def test_defer_then_commit_resets_the_counter():
    c = cand("s", deferred=2, kind="html")
    fs.defer_candidate(c)
    assert c["entry"]["deferred_count"] == 3
    c2 = cand("s", deferred=3, kind="html")
    fs.commit_candidate(c2)
    assert c2["entry"]["deferred_count"] == 0


def test_rss_commit_marks_only_that_id_seen():
    c = cand("feed", kind="rss")
    c["entry"]["seen_ids"] = ["old"]
    fs.commit_candidate(c)
    assert c["entry"]["seen_ids"] == ["old", "feed-id"]


def test_rss_defer_leaves_the_id_unseen():
    c = cand("feed", kind="rss")
    c["entry"]["seen_ids"] = ["old"]
    fs.defer_candidate(c)
    assert c["entry"]["seen_ids"] == ["old"]


def test_nothing_is_lost_across_the_split():
    cs = [cand(f"s{i}", weight=i / 10) for i in range(30)]
    take, defer = fs.triage(cs, 25)
    assert len(take) + len(defer) == 30
    assert {c["name"] for c in take} | {c["name"] for c in defer} == \
        {f"s{i}" for i in range(30)}
