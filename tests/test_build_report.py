"""build_report: escaping, the alert/block badges, and the health panel.

The badge paths have still never executed in production — no finding in the
committed log has ever carried notify: "alert" — so these are the only thing
standing between a rendering bug and discovering it during the walk.
"""
import datetime as dt

import build_report as br

NOW = dt.datetime(2026, 8, 1, 12, tzinfo=dt.timezone.utc)


def ago(hours):
    return (NOW - dt.timedelta(hours=hours)).isoformat()


def finding(**kw):
    base = dict(id="a" * 16, detected_at="2026-08-01T00:00:00Z", source_name="S",
                url="https://example.org", region="catalonia", tier="official",
                lang="ca", title="T", summary_en="s", relevance=50, is_new=True)
    base.update(kw)
    return base


# ---------- escaping ----------

def test_esc_neutralises_markup():
    out = br.esc('</script><img onerror=x>')
    assert "<script" not in out and "<img" not in out
    assert "&lt;" in out


def test_card_escapes_a_hostile_title():
    html = br.card(finding(title='</script><b>x</b>'))
    assert "<b>x</b>" not in html


def test_translate_url_only_wraps_non_english():
    assert br.translate_url("https://e.org", "en") == "https://e.org"
    assert "translate.google.com" in br.translate_url("https://e.org", "ca")


# ---------- badges ----------

def test_alert_tier_renders_the_alert_badge():
    html = br.card(finding(notify="alert"))
    assert 'data-notify="alert"' in html and "alerttag" in html


def test_quiet_tier_renders_no_alert_badge():
    assert "alerttag" not in br.card(finding(notify="quiet"))


def test_route_block_gets_its_own_badge_and_rail():
    html = br.card(finding(notify="alert", kind="route_block"))
    assert "blocktag" in html and "is-block" in html


def test_fire_weather_is_badged_distinctly_from_route_block():
    html = br.card(finding(kind="fire_weather"))
    assert "wxtag" in html and "blocktag" not in html


def test_stage_chip_appears_when_a_stage_is_known():
    assert "stage 26" in br.card(finding(stage=26, stage_end="Montserrat"))


# ---------- source status ----------

def test_status_failing_only_when_the_latest_attempt_failed():
    assert br.source_status({"last_checked": ago(1), "last_error": ago(1),
                             "last_ok": ago(25)}, NOW)[0] == "FAILING"
    # recovered since: not failing any more
    assert br.source_status({"last_checked": ago(1), "last_error": ago(25),
                             "last_ok": ago(1), "last_change_seen": ago(2)},
                            NOW)[0] == "OK"


def test_status_stale_when_not_checked_at_all():
    assert br.source_status({}, NOW)[0] == "STALE"
    assert br.source_status({"last_checked": ago(200), "last_ok": ago(200)},
                            NOW)[0] == "STALE"


def test_status_silent_is_not_an_error():
    """A static page that never changes is healthy, not broken."""
    status, _ = br.source_status(
        {"last_checked": ago(1), "last_ok": ago(1), "last_change_seen": ago(24 * 200)},
        NOW)
    assert status == "SILENT"


def test_status_ok_when_recently_checked_and_changed():
    assert br.source_status({"last_checked": ago(1), "last_ok": ago(1),
                             "last_change_seen": ago(5)}, NOW)[0] == "OK"


# ---------- panel ----------

def test_panel_counts_failures_in_the_collapsed_summary():
    ledger = {
        "a": {"name": "Dead", "last_checked": ago(1), "last_error": ago(1),
              "last_error_msg": "503"},
        "b": {"name": "Fine", "last_checked": ago(1), "last_ok": ago(1),
              "last_change_seen": ago(3)},
    }
    html = br.health_panel(ledger, {}, {}, NOW)
    assert "1 failing" in html and "2 sources" in html
    assert "503" in html
    assert "<details" in html and " open" in html   # opens itself when bad


def test_panel_stays_closed_when_everything_is_healthy():
    ledger = {"b": {"name": "Fine", "last_checked": ago(1), "last_ok": ago(1),
                    "last_change_seen": ago(3)}}
    html = br.health_panel(ledger, {}, {}, NOW)
    assert "all reachable" in html and "<details class=\"health\">" in html


def test_panel_lists_conditions_in_force_and_hides_cleared_ones():
    conditions = {"alfa:closure:montserrat": {"fingerprint": "closed", "rank": 4,
                                              "peak_rank": 4, "first_seen": ago(48),
                                              "last_emitted": ago(2), "emit_count": 3},
                  "fwi:corridor": {"fingerprint": None, "rank": 0}}
    html = br.health_panel({}, conditions, {}, NOW)
    assert "alfa:closure:montserrat" in html
    assert "fwi:corridor" not in html


def test_panel_reports_an_unreachable_api():
    html = br.health_panel({}, {}, {"run_at": ago(1), "fwi_ok": False,
                                    "alfa_ok": True, "failed": ["X"]}, NOW)
    assert "UNREACHABLE" in html and "unreachable: X" in html


def test_panel_is_empty_when_there_is_no_state_at_all():
    assert br.health_panel(None, None, None, NOW) == ""


# ---------- page ----------

def test_build_is_deterministic_when_generated_is_supplied():
    a = br.build([finding()], generated=NOW)
    b = br.build([finding()], generated=NOW)
    assert a == b
    assert "2026-08-01 12:00" in a


def test_build_floats_this_runs_alerts_to_the_top():
    quiet = finding(id="q" * 16, title="Quiet one", notify="quiet")
    alert = finding(id="a" * 16, title="Alert one", notify="alert")
    html = br.build([quiet, alert], generated=NOW)
    assert html.index("Alert one") < html.index("Quiet one")


def test_build_survives_findings_with_missing_fields():
    html = br.build([{"id": "x", "title": "bare"}], generated=NOW)
    assert "bare" in html


def test_freshly_baselined_source_is_ok_not_silent():
    """On the first run after migration nothing has a last_change_seen. Reporting
    all 47 as SILENT would make the panel useless on the day it ships."""
    status, why = br.source_status({"last_checked": ago(1), "last_ok": ago(1)}, NOW)
    assert status == "OK" and "baseline" in why


def test_aggregator_links_are_never_translate_wrapped():
    """Wrapping a Google News JS interstitial in translate.goog produced a
    guaranteed dead end for 61 findings."""
    gn = "https://news.google.com/rss/articles/CBMiopaque"
    assert br.translate_url(gn, "es") == gn
    assert br.translate_url("https://caminoignaciano.org/x", "es").startswith(
        "https://translate.google.com")


def test_publisher_chip_links_out_when_known():
    html = br.card(finding(url="https://news.google.com/rss/articles/CBMiopaque",
                           publisher="ESMTB.com",
                           publisher_url="https://esmtb.com"))
    assert 'class="chip via"' in html and "https://esmtb.com" in html
    assert "via ESMTB.com" in html


def test_no_publisher_chip_when_unknown():
    assert 'chip via' not in br.card(finding())
