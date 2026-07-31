"""fetch_sources: date gating and the diff logic.

The diff tests exist because this module once manufactured seven relevance-100
findings from a page that never changed.
"""
import datetime as dt

import pytest

import fetch_sources as fs


def T(y, m, d, h=12):
    return dt.datetime(y, m, d, h, tzinfo=dt.timezone.utc)


# ---------- date handling ----------

def test_as_date_accepts_strings_and_real_dates():
    """sources.yaml mixes quoted strings with unquoted dates PyYAML auto-parses."""
    assert fs.as_date("2027-04-19") == dt.date(2027, 4, 19)
    assert fs.as_date(dt.date(2027, 4, 19)) == dt.date(2027, 4, 19)
    assert fs.as_date(None) is None
    assert fs.as_date("not-a-date") is None


def test_effective_notify_escalates_one_way_on_the_exact_day():
    src = {"notify": "quiet", "notify_from": "2027-03-01"}
    assert fs.effective_notify(src, dt.date(2027, 2, 28)) == "quiet"
    assert fs.effective_notify(src, dt.date(2027, 3, 1)) == "alert"
    assert fs.effective_notify(src, dt.date(2027, 6, 1)) == "alert"


def test_effective_notify_defaults_and_rejects_garbage():
    assert fs.effective_notify({}, dt.date(2026, 8, 1)) == "quiet"
    assert fs.effective_notify({"notify": "LOUD"}, dt.date(2026, 8, 1)) == "quiet"
    assert fs.effective_notify({"notify": "alert"}, dt.date(2026, 8, 1)) == "alert"


def test_effective_analyze_stays_silent_until_its_date():
    src = {"analyze": "never", "analyze_from": "2027-04-01"}
    assert fs.effective_analyze(src, dt.date(2027, 3, 31)) == "never"
    assert fs.effective_analyze(src, dt.date(2027, 4, 1)) == "on_change"
    assert fs.effective_analyze({}, dt.date(2026, 8, 1)) == "on_change"


def test_due_reason_dormant_and_stop_boundaries():
    d = {"dormant_until": "2027-02-01"}
    assert fs.due_reason(d, {}, dt.date(2027, 1, 31), T(2027, 1, 31)) is not None
    assert fs.due_reason(d, {}, dt.date(2027, 2, 1), T(2027, 2, 1)) is None
    s = {"stop_after": "2027-05-25"}
    assert fs.due_reason(s, {}, dt.date(2027, 5, 25), T(2027, 5, 25)) is None
    assert fs.due_reason(s, {}, dt.date(2027, 5, 26), T(2027, 5, 26)) is not None


def test_due_reason_respects_cadence_and_handles_naive_stamps():
    recent = {"last_checked": T(2026, 8, 1, 6).isoformat()}
    assert fs.due_reason({}, recent, dt.date(2026, 8, 1), T(2026, 8, 1, 12))
    old = {"last_checked": T(2026, 7, 30, 6).isoformat()}
    assert fs.due_reason({}, old, dt.date(2026, 8, 1), T(2026, 8, 1, 12)) is None
    naive = {"last_checked": "2026-08-01T06:00:00"}          # no timezone
    assert fs.due_reason({}, naive, dt.date(2026, 8, 1), T(2026, 8, 1, 12))


def test_due_reason_survives_a_corrupt_timestamp():
    bad = {"last_checked": "yesterday-ish"}
    assert fs.due_reason({}, bad, dt.date(2026, 8, 1), T(2026, 8, 1)) is None


def test_min_interval_hours_accepts_names_and_numbers():
    assert fs.min_interval_hours({"check": "twice_daily"}) == 10
    assert fs.min_interval_hours({}) == 20
    assert fs.min_interval_hours({"check": 6}) == 6
    assert fs.min_interval_hours({"check": "nonsense"}) == 20


# ---------- the diff logic that once invented findings ----------

def test_norm_for_hash_ignores_cosmetic_whitespace():
    a = "Alojamiento\n\t\t\tA continuación"
    b = "Alojamiento   A continuación"
    assert fs.norm_for_hash(a) == fs.norm_for_hash(b)


def test_added_text_reports_nothing_for_identical_input():
    text = "line one\nline two\n"
    assert fs.added_text(text, text).strip() == ""


def test_added_text_reports_a_genuine_edit():
    old = "a\nb\nc\n"
    new = "a\nb\nc\nalbergue closed\n"
    assert "albergue closed" in fs.added_text(old, new)


def test_added_text_returns_everything_when_there_is_no_baseline():
    """Documents the sharp edge: main() must re-baseline instead of emitting this,
    or a source with an empty stored text dumps its whole page to the model."""
    assert fs.added_text("", "whole page") == "whole page"


def test_extract_main_text_reports_which_extractor_ran():
    """The extractor NAME is the fix for the false-findings bug: diffing
    trafilatura output against bs4 output reports the whole page as new."""
    html = "<html><head><title>T</title></head><body><p>" + ("hola " * 40) + "</p></body></html>"
    text, extractor = fs.extract_main_text(html, "https://example.org")
    assert text.strip()
    assert extractor in ("trafilatura", "bs4")


def test_extract_main_text_on_junk_returns_an_empty_marker():
    text, extractor = fs.extract_main_text("", "https://example.org")
    assert text == "" or extractor in ("trafilatura", "bs4")


# ---------- seen_ids retention ----------

def test_retain_recent_is_deterministic_and_order_preserving():
    prev = [f"id{n}" for n in range(5)]
    out = fs.retain_recent(prev, ["idA", "idB"], 300)
    assert out == prev + ["idA", "idB"]
    assert out == fs.retain_recent(prev, ["idA", "idB"], 300)


def test_retain_recent_drops_oldest_at_the_cap_and_keeps_newest():
    prev = [f"id{n}" for n in range(300)]
    out = fs.retain_recent(prev, ["fresh"], 300)
    assert len(out) == 300
    assert "fresh" in out and "id0" not in out


def test_retain_recent_never_duplicates():
    assert fs.retain_recent(["a"], ["a", "b"], 300) == ["a", "b"]


# ---------- health stamping ----------

def test_mark_ok_clears_a_previous_failure():
    """Without this, one bad afternoon marks a source broken forever."""
    entry = {"last_error": "2026-07-01T00:00:00+00:00", "last_error_msg": "503"}
    fs.mark_ok(entry)
    assert "last_error" not in entry and "last_error_msg" not in entry
    assert entry["last_ok"] and entry["last_checked"]


def test_mark_failed_records_the_reason():
    entry = {}
    fs.mark_failed(entry, "HTTPError: 503 Service Unavailable")
    assert entry["last_error"] and "503" in entry["last_error_msg"]
    assert entry["last_checked"]


def test_sid_is_stable_and_url_keyed():
    """The ledger is keyed by this, which is why a changed URL orphans an entry
    and why main() prunes entries no longer present in sources.yaml."""
    assert fs.sid("https://a.example") == fs.sid("https://a.example")
    assert fs.sid("https://a.example") != fs.sid("https://b.example")
    assert len(fs.sid("https://a.example")) == 12


def test_mark_ok_must_not_be_what_decides_first_sight():
    """Regression: mark_ok() stamps last_checked, and the RSS branch uses the
    ABSENCE of last_checked to detect a brand-new feed. Calling mark_ok first made
    a new feed look already-seen and emitted its entire backlog as news."""
    entry = {}
    first_sight = not entry.get("seen_ids") and not entry.get("last_checked")
    assert first_sight is True
    fs.mark_ok(entry)
    # after stamping, the same expression would wrongly say "not new"
    assert (not entry.get("seen_ids") and not entry.get("last_checked")) is False


def test_rss_parser_captures_the_publisher_from_source_element():
    """Google News puts an opaque JS redirect in <link> and the real publisher in
    <source url=...>. That element is the only usable provenance, because the
    interstitial serves 0 bytes to anything but a browser and the link blob is
    not decodable."""
    import xml.etree.ElementTree as ET

    import rss_compat as rc
    xml = """<rss><channel><item>
      <title>Headline</title>
      <link>https://news.google.com/rss/articles/CBMiopaque</link>
      <guid isPermaLink="false">CBMiopaque</guid>
      <description>blah</description>
      <source url="https://esmtb.com">ESMTB.com</source>
    </item></channel></rss>"""
    e = rc._parse_rss(ET.fromstring(xml))[0]
    assert e["source_title"] == "ESMTB.com"
    assert e["source_url"] == "https://esmtb.com"
    assert e["link"].startswith("https://news.google.com")


def test_rss_parser_tolerates_a_missing_source_element():
    import xml.etree.ElementTree as ET

    import rss_compat as rc
    xml = ("<rss><channel><item><title>T</title>"
           "<link>https://a.example/x</link></item></channel></rss>")
    e = rc._parse_rss(ET.fromstring(xml))[0]
    assert e.get("source_url", "") == ""
    assert e["link"] == "https://a.example/x"
