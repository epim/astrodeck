"""Tonight ranking (NOV-3) — pure, no astropy."""
from astrodeck.catalog.tonight import tonight_score, rank_picks


def _night(**kw):
    base = {"never_rises_above_limit": False, "transit_alt": 50.0, "best_window": None}
    base.update(kw)
    return base


def test_best_window_mean_alt_is_the_score():
    assert tonight_score(_night(best_window={"mean_alt": 65.0})) == 65.0


def test_no_window_uses_transit_alt():
    assert tonight_score(_night(best_window=None, transit_alt=42.0)) == 42.0


def test_never_rises_sinks_below_all_risers_but_stays_orderable():
    low = tonight_score(_night(never_rises_above_limit=True, transit_alt=12.0))
    lower = tonight_score(_night(never_rises_above_limit=True, transit_alt=5.0))
    assert low < 0 and lower < low            # higher transit ranks above lower
    assert low < tonight_score(_night(transit_alt=1.0))   # always below any riser


def test_rank_picks_orders_best_first_then_brighter():
    picks = [
        {"id": "A", "score": 40.0, "mag": 6.0},
        {"id": "B", "score": 70.0, "mag": 9.0},
        {"id": "C", "score": 70.0, "mag": 5.0},   # ties B on score, brighter -> first
    ]
    order = [p["id"] for p in rank_picks(picks)]
    assert order == ["C", "B", "A"]
