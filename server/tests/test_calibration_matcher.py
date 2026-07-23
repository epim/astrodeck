from astrodeck.calibration.matcher import (
    LightNeed, MasterRecord, MatchTolerance, Gap,
    dark_matches, flat_matches, best_master, coverage_for)

TOL = MatchTolerance(exposure_tol_pct=5.0, temp_tol_c=2.0)


def dark(exp, g=100, o=30, t=-10.0, b=1, n=20, id="d"):
    return MasterRecord(id, "DARK", exp, g, o, t, b, "", n, "/m/" + id, 0.0)


def flat(filt, g=100, o=30, b=1, n=20, id="f"):
    return MasterRecord(id, "FLAT", 2.0, g, o, -10.0, b, filt, n, "/m/" + id, 0.0)


def need(exp=300, g=100, o=30, t=-10.0, b=1, filt="Ha"):
    return LightNeed(exp, g, o, t, b, filt)


def test_dark_matches_within_exposure_tolerance():
    assert dark_matches(need(300), dark(307), TOL)      # +2.3% within 5%
    assert not dark_matches(need(300), dark(400), TOL)  # +33% out


def test_dark_temp_out_of_tolerance_fails():
    assert not dark_matches(need(300, t=-10.0), dark(300, t=-15.0), TOL)


def test_dark_temp_unknown_is_not_a_constraint():
    assert dark_matches(need(300, t=None), dark(300, t=-40.0), TOL)


def test_gain_offset_binning_are_exact():
    assert not dark_matches(need(300, g=200), dark(300, g=100), TOL)
    assert not dark_matches(need(300, b=2), dark(300, b=1), TOL)


def test_flat_matches_filter_and_binning():
    assert flat_matches(need(filt="Ha"), flat("Ha"), TOL)
    assert not flat_matches(need(filt="OIII"), flat("Ha"), TOL)


def test_best_master_prefers_closest_exposure_then_most_frames():
    best = best_master(need(300), [dark(310, n=5, id="far"),
                                   dark(302, n=5, id="near"),
                                   dark(302, n=40, id="deep")], TOL, "DARK")
    assert best.id == "deep"                # equal Δexp, more frames wins


def test_coverage_reports_missing_dark_and_flat():
    gaps = coverage_for([need(300, filt="Ha")], [flat("OIII")], TOL)
    assert gaps == [Gap("Ha", 300.0, 100, 1, ("dark", "flat"))]


def test_coverage_empty_when_all_covered():
    assert coverage_for([need(300, filt="Ha")], [dark(300), flat("Ha")], TOL) == []


def test_coverage_empty_library_never_nags():
    assert coverage_for([need(300)], [], TOL) == []
