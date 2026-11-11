"""fetch_fires: the geofence and the raster parser.

A latitude sign flip or a nodata sentinel here is invisible — it produces a
plausible-looking number, not an error.
"""
import datetime as dt
import math

import pytest
from conftest import make_tiff

import fetch_fires as ff


@pytest.mark.parametrize("value,expected", [
    (0.0, "low"), (11.19, "low"), (11.2, "moderate"),
    (21.29, "moderate"), (21.3, "high"), (37.9, "high"), (38.0, "very high"),
    (49.9, "very high"), (50.0, "extreme"), (69.9, "extreme"),
    (70.0, "very extreme"), (70.1, "very extreme"),
])
def test_fwi_class_boundaries(value, expected):
    assert ff.fwi_class(value) == expected


BBOX = {"west": 0.0, "south": 40.0, "east": 4.0, "north": 42.0}


def grid(values, w, h):
    return (values, w, h)


@pytest.mark.parametrize("bad", [1e20, 9999.0, 200.0, 150.0, -9999.0, 0.0,
                                 float("nan"), float("inf")])
def test_grid_value_rejects_nodata_and_zero(bad):
    """A nodata sentinel classifies as 'very extreme' and would push an alert
    for an empty pixel. This is the guard that stops it."""
    assert ff.grid_value(grid([bad] * 4, 2, 2), BBOX, 41.0, 2.0) is None


def test_grid_value_accepts_real_readings():
    assert ff.grid_value(grid([34.5] * 4, 2, 2), BBOX, 41.0, 2.0) == pytest.approx(34.5)


def test_grid_value_corner_mapping_is_north_up():
    """Rows are top-down. If this inverts, every stage samples the wrong end of a
    650 km corridor, forever, with no error.

    Values are 1-based because grid_value treats 0.0 as nodata.
    """
    w = h = 4
    values = [float(y * w + x + 1) for y in range(h) for x in range(w)]
    g = grid(values, w, h)
    # north-west corner -> row 0, col 0
    assert ff.grid_value(g, BBOX, 41.99, 0.01) == 1.0
    # north-east -> row 0, last col
    assert ff.grid_value(g, BBOX, 41.99, 3.99) == float(w)
    # south-west -> last row, col 0
    assert ff.grid_value(g, BBOX, 40.01, 0.01) == float((h - 1) * w + 1)


def test_grid_value_clamps_outside_the_box():
    w = h = 4
    values = [float(y * w + x + 1) for y in range(h) for x in range(w)]
    g = grid(values, w, h)
    assert ff.grid_value(g, BBOX, 41.0, 99.0) is not None   # clamped, not crashed
    assert ff.grid_value(g, BBOX, -99.0, 2.0) is not None


def test_tiff_roundtrip_single_and_multi_strip():
    vals = [float(i) for i in range(12)]
    for strips in (1, 3):
        got, w, h = ff._tiff_floats(make_tiff(vals, 4, 3, strips=strips))
        assert (got, w, h) == (vals, 4, 3)


def test_tiff_big_endian():
    vals = [1.5, 2.5, 3.5, 4.5]
    assert ff._tiff_floats(make_tiff(vals, 2, 2, endian=">"))[0] == vals


def test_tiff_rejects_unexpected_encoding():
    with pytest.raises(ValueError):
        ff._tiff_floats(make_tiff([1.0, 2.0, 3.0, 4.0], 2, 2, bits=16))
    with pytest.raises(ValueError):
        ff._tiff_floats(make_tiff([1.0, 2.0, 3.0, 4.0], 2, 2, fmt=1))


def test_haversine_identity_and_known_distance():
    assert ff.haversine_km(41.6, 1.2, 41.6, 1.2) == 0.0
    # Zumárraga -> Manresa, the two ends of the route
    d = ff.haversine_km(43.083, -2.313, 41.728, 1.827)
    assert 350 < d < 400


def rows_at(lat, lon, n=1, date="2025-07-01"):
    return [{"latitude": str(lat), "longitude": str(lon), "acq_date": date,
             "frp": "10.0", "confidence": "n"} for _ in range(n)]


def test_annotate_respects_the_20km_boundary():
    stages = [{"stage": 24, "end": "Cervera", "lat": 41.670, "lon": 1.272,
               "region": "catalonia"}]
    # ~19.5 km north of Cervera: inside
    near = ff.annotate(rows_at(41.845, 1.272), stages)
    assert len(near) == 1 and near[0]["stage"] == 24
    # ~24 km north: outside
    assert ff.annotate(rows_at(41.888, 1.272), stages) == []


def test_annotate_skips_unparseable_coordinates():
    stages = [{"stage": 1, "end": "X", "lat": 41.6, "lon": 1.2, "region": "whole"}]
    rows = [{"latitude": "n/a", "longitude": "1.2", "acq_date": "2025-07-01"},
            {"longitude": "1.2", "acq_date": "2025-07-01"}]
    assert ff.annotate(rows, stages) == []


def test_cluster_splits_on_a_gap_over_two_days():
    stages = [{"stage": 24, "end": "Cervera", "lat": 41.670, "lon": 1.272,
               "region": "catalonia"}]
    def hits(dates):
        out = []
        for d in dates:
            out += ff.annotate(rows_at(41.72, 1.272, date=d), stages)
        return out
    assert len(ff.cluster(hits(["2025-07-01", "2025-07-03"]))) == 1
    assert len(ff.cluster(hits(["2025-07-01", "2025-07-05"]))) == 2


def test_cluster_reports_nearest_distance_not_the_mean():
    stages = [{"stage": 24, "end": "Cervera", "lat": 41.670, "lon": 1.272,
               "region": "catalonia"}]
    hits = (ff.annotate(rows_at(41.68, 1.272), stages)
            + ff.annotate(rows_at(41.84, 1.272), stages))
    c = ff.cluster(hits)[0]
    assert c["detections"] == 2
    assert c["min_distance_km"] < 2.0


# ---------- fingerprint churn (the 42-emissions-in-102-days regression) ----------

def _sweep_with(monkeypatch, values_by_call, ledger, day):
    """Run fwi_sweep against synthetic grids; values_by_call feeds every stage."""
    import fetch_fires as ff
    calls = iter(values_by_call)

    def fake_grid(bbox, d, layer, **kw):
        if layer != ff.FWI_LAYER:
            return None
        v = next(calls, values_by_call[-1])
        return ([v] * 8, 4, 2)
    monkeypatch.setattr(ff, "fetch_fwi_grid", fake_grid)
    stages = ff.load_stages()
    return ff.fwi_sweep(ff.load_bbox(), stages, day, ledger)


def test_breadth_change_alone_does_not_re_emit(monkeypatch):
    """13 of 23 recorded emissions changed nothing but the stage-count bucket.
    Same danger class => same fingerprint => suppressed."""
    import datetime as dt
    import fetch_fires as ff
    day = dt.date(2026, 11, 11)
    ledger = {}
    first = _sweep_with(monkeypatch, [45.0], ledger, day)           # very high, broad
    assert len(first) == 1
    assert ledger["fwi:corridor"]["fingerprint"] == "very high"     # class only

    # next day: same class, utterly different breadth — must stay quiet
    def narrow_grid(bbox, d, layer, **kw):
        if layer != ff.FWI_LAYER:
            return None
        vals = [45.0] + [5.0] * 7                                   # 1 stage high vs 8
        return (vals * 1, 4, 2)
    monkeypatch.setattr(ff, "fetch_fwi_grid", narrow_grid)
    again = ff.fwi_sweep(ff.load_bbox(), ff.load_stages(),
                         day + dt.timedelta(days=1), ledger)
    assert again == []


def test_class_change_still_emits(monkeypatch):
    import datetime as dt
    day = dt.date(2026, 11, 11)
    ledger = {}
    assert len(_sweep_with(monkeypatch, [30.0], ledger, day)) == 1          # high
    assert len(_sweep_with(monkeypatch, [55.0], ledger,
                           day + dt.timedelta(days=1))) == 1                 # extreme
    assert ledger["fwi:corridor"]["fingerprint"] == "extreme"


def test_old_colon_fingerprints_still_parse_for_hysteresis(monkeypatch):
    """The ledger on disk still holds 'high:1-2'-shaped values from before."""
    import datetime as dt
    L = {"fwi:corridor": {"fingerprint": "high:1-2", "rank": 2,
                          "peak_rank": 5, "emit_count": 42,
                          "first_seen": "2026-07-30T19:08:00+00:00",
                          "last_emitted": "2026-11-09T14:36:00+00:00",
                          "last_seen": "2026-11-09T14:36:00+00:00"}}
    # 22.0 reads as 'high' but sits within FWI_HYSTERESIS of the 21.3 boundary;
    # the previous class must parse out of the old colon-shaped value for the
    # hysteresis comparison to work at all.
    _sweep_with(monkeypatch, [22.0], L, dt.date(2026, 11, 11))
    assert L["fwi:corridor"]["fingerprint"] == "high"
