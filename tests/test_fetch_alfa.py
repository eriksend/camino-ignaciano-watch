"""fetch_alfa: the route-blocking path.

The rule that must not blur: level 3 bans flame and machinery but NOT walking;
only level 4 can close a footpath.
"""
import datetime as dt

import condition_ledger as cl
import fetch_alfa as fa

FAR = dt.date(2026, 8, 1)
WALKING = dt.date(2027, 4, 25)


def test_fold_strips_accents_and_punctuation():
    assert fa.fold("Sant Llorenç del Munt") == "sant llorenc del munt"
    assert fa.fold("Segrià") == "segria"
    assert fa.fold(None) == ""


def test_levels_for_route_aggregates_max_per_comarca():
    rows = [{"NOMCOMAR": "Segarra", "NOMMUNI": "A", "PERIL_M": 2},
            {"NOMCOMAR": "Segarra", "NOMMUNI": "B", "PERIL_M": 4},
            {"NOMCOMAR": "Osona", "NOMMUNI": "Z", "PERIL_M": 4}]
    out = fa.levels_for_route(rows)
    assert out["by_comarca"] == {"Segarra": 4}      # Osona is not on the route
    assert ("B", "Segarra", 4) in out["hot"]
    assert ("A", "Segarra", 2) not in out["hot"]    # below LEVEL_REPORT_FROM


def test_levels_for_route_tolerates_messy_values():
    rows = [{"NOMCOMAR": "Anoia", "NOMMUNI": "A", "PERIL_M": None},
            {"NOMCOMAR": "Anoia", "NOMMUNI": "B", "PERIL_M": ""},
            {"NOMCOMAR": "Anoia", "NOMMUNI": "C", "PERIL_M": "3"},
            {"NOMCOMAR": "Anoia", "NOMMUNI": "D", "PERIL_M": "junk"}]
    out = fa.levels_for_route(rows)
    assert out["by_comarca"]["Anoia"] == 3


def test_levels_for_route_on_empty_input_does_not_raise():
    assert fa.levels_for_route([])["by_comarca"] == {}


def test_route_closures_matches_either_field_casing():
    assert fa.route_closures([{"Espai_prot": "Muntanya de Montserrat"}])[0]["stage"] == 26
    assert fa.route_closures([{"ESPAI_PROT": "Montserrat"}])[0]["stage"] == 26
    assert fa.route_closures([{"Espai_prot": "Cap de Creus"}]) == []


def _levels(peak, comarca="Segarra"):
    return {"by_comarca": {comarca: peak}, "hot": [("M", comarca, peak)]}


def test_level_three_is_not_dressed_up_as_a_blockage():
    items = fa.build_items(FAR, _levels(3), [], {}, [], [], {})
    assert items and items[0]["kind"] == "fire_weather"
    assert "does NOT restrict access on foot" in items[0]["text"]


def test_level_four_is_route_blocking_but_defers_to_the_closures_list():
    items = fa.build_items(FAR, _levels(4), [], {}, [], [], {})
    assert items and items[0]["kind"] == "route_block"
    assert "Check the closures list" in items[0]["text"]


def test_a_montserrat_closure_is_route_block_and_names_the_stage():
    closure = [{"space": "Muntanya de Montserrat", "stage": 26,
                "label": "Montserrat"}]
    items = fa.build_items(WALKING, _levels(0), closure, {}, [], [], {})
    block = [i for i in items if i["kind"] == "route_block"]
    assert block and block[0]["stage"] == 26
    assert block[0]["notify"] == "alert"        # inside the walk window


def test_the_same_closure_is_not_re_emitted_on_the_next_run():
    closure = [{"space": "Montserrat", "stage": 26, "label": "Montserrat"}]
    ledger = {}
    first = fa.build_items(FAR, _levels(0), closure, {}, [], [], ledger)
    second = fa.build_items(FAR, _levels(0), closure, {}, [], [], ledger)
    assert [i for i in first if i["kind"] == "route_block"]
    assert not [i for i in second if i["kind"] == "route_block"]


def test_a_closure_announced_for_tomorrow_does_not_repeat_as_today():
    """`when` must stay out of the condition identity, or the single most
    important item type duplicates itself every time."""
    closure = [{"space": "Montserrat", "stage": 26, "label": "Montserrat"}]
    ledger = {}
    fa.build_items(FAR, _levels(0), [], {}, closure, [], ledger)   # tomorrow
    again = fa.build_items(FAR, _levels(0), closure, {}, [], [], ledger)  # now today
    assert not [i for i in again if i["kind"] == "route_block"]


def test_level_below_three_produces_no_level_item():
    items = fa.build_items(FAR, _levels(2), [], {}, [], [], {})
    assert not [i for i in items if i["kind"] in ("route_block", "fire_weather")
                and "Pla ALFA reaches" in i["text"]]
