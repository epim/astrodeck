# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1257: the whole-catalogue night equals ``compute_night``, row for row.

``visibility.compute_nights`` answers every catalogue row in one vectorised
pass (0.3 s) where the route used to call ``compute_night`` 13,370 times (87 s).
It is only worth having if it is the SAME answer, so this file holds the two
together at two levels:

  * A, the best-window selection rule, in isolation. ``_best_runs`` is
    ``_best_window``'s rule written over arrays; it is graded against
    ``_best_window`` itself on synthetic nights (two- and three-run rows, rows
    whose runs tie, a Moon that is up and down, positive and negative altitude
    limits). Only here is the tie branch certain to be reached.
  * B to E, the whole function against ``compute_night`` on real astropy, at
    five made-up (site, date) pairs that give every twilight grade, both
    hemispheres and a low altitude limit; the branch where no sample falls in
    the dark window; chunk boundaries; and "tonight" under a pinned clock.

Equality is EXACT, floats included. The astropy broadcast is bit-identical to
the per-row transform, the roundings are the same calls and the sums are the
same sequential sums, so there is no tolerance to choose and a difference is a
bug. A one-off comparison of all 13,370 rows at three (site, date) pairs found
0 differences.

The cache (``nights_for_catalogue``) is graded here too: what a key is made of,
that a hit needs the same coordinates, the bound, one build under concurrent
callers, read-only entries, and a key that cannot print the site.

Every site below is made up.

MUTATIONS RUN, and what each printed:
  M1, ``_best_runs`` without the tie rule (``if score > best_score:`` alone):
  the selection test failed on its tie rows, "row 1200: (4.0, 4.0, 44.2) !=
  {'start_unix': 10.0, 'end_unix': 12.0, 'mean_alt': 44.2}", at limit 30.0 with
  the Moon down and with it up.
  M2, the cache key without the site (latitude, longitude, elevation): the
  latitude and elevation cases of ``test_every_input_of_the_answer_is_in_the_key``
  failed ("an input of the night was left out of the cache key") and so did the
  site-change case in ``test_tonight_route.py``. The longitude case still
  passes, because the night's anchor carries the longitude.
"""
from __future__ import annotations

import threading
import time
import types

import numpy as np
import pytest

from astrodeck.catalog import visibility as vis
from astrodeck.catalog.objects import CATALOG


def _site(lat: float, lon: float, elevation_m: float = 12.0) -> dict:
    return {"name": "made-up", "latitude": lat, "longitude": lon,
            "elevation_m": elevation_m, "is_default": False,
            "horizon_min_deg": 20.0}


RA = np.array([o.ra_hours for o in CATALOG])
DEC = np.array([o.dec_deg for o in CATALOG])


@pytest.fixture(autouse=True)
def _cold_nights():
    with vis._nights_lock:
        vis._nights_cache.clear()
    yield
    with vis._nights_lock:
        vis._nights_cache.clear()


# ------------------------------------------------ A: the selection rule alone

_N = 40
_IN_DARK = np.zeros(_N, dtype=bool)
_IN_DARK[4:36] = True


def _reference(alt_r, sep_r, moon_alt_r, illum, alt_limit):
    """``_best_window`` on sample dicts built from the same numbers."""
    first = np.flatnonzero(_IN_DARK)[0]
    last = np.flatnonzero(_IN_DARK)[-1]
    out = []
    for r in range(alt_r.shape[0]):
        samples = [{"t_unix": float(i), "alt": float(alt_r[r, i]),
                    "moon_alt": float(moon_alt_r[i]),
                    "moon_sep_deg": float(sep_r[r, i])} for i in range(_N)]
        out.append(vis._best_window(
            samples, float(first), float(last), alt_limit, float(illum[r])))
    return out


def _tie_rows(rng, count: int) -> np.ndarray:
    """Rows of two or three runs at one constant altitude, so the runs' scores
    tie to within float noise and only the length rule can choose."""
    rows = np.full((count, _N), -10.0)
    for r in range(count):
        level = round(float(rng.uniform(35.0, 80.0)), 2)
        at = 4
        for _ in range(int(rng.integers(2, 4))):
            length = int(rng.integers(1, 7))
            if at + length > 35:
                break
            rows[r, at:at + length] = level
            at += length + int(rng.integers(1, 4))
    return rows


def _synthetic(rng):
    t = np.arange(_N)
    rows = 1200
    phase = rng.uniform(0, 2 * np.pi, (rows, 1))
    freq = rng.uniform(0.05, 0.6, (rows, 1))
    amp = rng.uniform(5.0, 60.0, (rows, 1))
    base = rng.uniform(-20.0, 60.0, (rows, 1))
    smooth = base + amp * np.sin(phase + freq * t[None, :])
    alt = np.round(np.vstack([smooth, _tie_rows(rng, 240)]), 2)
    sep = np.round(rng.uniform(0.0, 180.0, alt.shape), 2)
    illum = np.round(rng.uniform(0.0, 1.0, len(alt)), 3)
    return alt, sep, illum


@pytest.mark.parametrize("moon", ["down", "up-and-down"])
@pytest.mark.parametrize("alt_limit", [30.0, 0.0, -20.0, 55.5])
def test_the_selection_rule_equals_best_window(moon, alt_limit):
    rng = np.random.default_rng(1257)
    alt_r, sep_r, illum = _synthetic(rng)
    if moon == "down":
        moon_alt_r = np.full(_N, -10.0)
    else:
        # includes a sample at 0.0, which the rounded rule reads as NOT up
        moon_alt_r = np.round(25.0 * np.sin(np.arange(_N) / 4.0), 2)
        moon_alt_r[7] = 0.0
    moon_up_cum = np.concatenate(([0], np.cumsum(moon_alt_r > 0.0)))

    first, last, mean = vis._best_runs(
        alt_r, sep_r, _IN_DARK, moon_up_cum, illum, alt_limit)

    want = _reference(alt_r, sep_r, moon_alt_r, illum, alt_limit)
    windows = 0
    for r, w in enumerate(want):
        if w is None:
            assert first[r] == -1, f"row {r}: a window where there is none"
            continue
        windows += 1
        got = (float(first[r]), float(last[r]), float(mean[r]))
        assert got == (w["start_unix"], w["end_unix"], w["mean_alt"]), (
            f"row {r}: {got} != {w}")
    assert windows, "premise: some rows have a window at this limit"


def test_a_tie_between_runs_goes_to_the_longer_one():
    """The premise of A's tie rows, shown directly: two runs at one altitude, the
    Moon down. The shorter comes first and must lose."""
    alt_r = np.full((1, _N), -10.0)
    alt_r[0, 5:7] = 50.0      # two samples
    alt_r[0, 12:17] = 50.0    # five samples
    sep_r = np.full((1, _N), 90.0)
    illum = np.array([0.5])
    cum = np.zeros(_N + 1, dtype=int)
    first, last, _ = vis._best_runs(alt_r, sep_r, _IN_DARK, cum, illum, 30.0)
    assert (int(first[0]), int(last[0])) == (12, 16)
    ref = _reference(alt_r, sep_r, np.full(_N, -10.0), illum, 30.0)[0]
    assert (ref["start_unix"], ref["end_unix"]) == (12.0, 16.0)


# ------------------------------------------------ B: the whole function

# (label, latitude, longitude, elevation, date, darkness grade, altitude limit)
CASES = [
    ("mid-latitude winter", 40.25, -74.5, 12.0, "2026-01-15", "astronomical", 30.0),
    ("nautical summer", 52.0, 10.5, 40.0, "2026-06-21", "nautical", 30.0),
    ("high latitude summer", 70.0, 20.0, 5.0, "2026-06-21", "none", 30.0),
    ("southern winter", -35.0, 150.0, 300.0, "2026-06-21", "astronomical", 30.0),
    ("tropics, low limit", 5.0, -60.0, 0.0, "2026-03-20", "astronomical", 10.0),
]


def _rows_to_grade(cols: "vis.NightColumns", seed: int) -> list[int]:
    """A seeded random sample plus one row of each kind the vectorised code
    branches on, so a sample cannot miss a branch by luck."""
    rng = np.random.default_rng(seed)
    rows = {int(i) for i in rng.choice(len(CATALOG), 45, replace=False)}
    unix = cols.scaffold["unix"]
    dark = np.flatnonzero((unix >= cols.scaffold["dark_start"])
                          & (unix <= cols.scaffold["dark_end"]))
    windowed = cols.win_start >= 0
    circumpolar = (cols.win_start == dark[0]) & (cols.win_end == dark[-1])
    for kind in (cols.never_rises, cols.in_daylight, circumpolar,
                 windowed & ~circumpolar):
        hits = np.flatnonzero(kind)
        if len(hits):
            rows.add(int(hits[0]))
    rows.update((int(DEC.argmax()), int(DEC.argmin())))
    return sorted(rows)


def _same_as_compute_night(cols, rows, *, date, alt_limit, site):
    for i in rows:
        o = CATALOG[i]
        want = vis.compute_night(o.ra_hours, o.dec_deg, date=date,
                                 step_min=20, alt_limit=alt_limit, site=site)
        want.pop("samples")
        got = cols.row(i)
        differing = [k for k in want if got[k] != want[k]]
        assert not differing, f"row {i} ({o.id}): {differing} differ"


@pytest.mark.parametrize("label,lat,lon,elev,date,grade,alt_limit", CASES,
                         ids=[c[0] for c in CASES])
def test_every_field_equals_compute_night(label, lat, lon, elev, date, grade,
                                          alt_limit):
    site = _site(lat, lon, elev)
    cols = vis.compute_nights(RA, DEC, date=date, step_min=20,
                              alt_limit=alt_limit, site=site)
    assert cols.scaffold["darkness_kind"] == grade, "premise: the twilight grade"
    assert cols.never_rises.any() and (cols.win_start >= 0).any(), "premise"
    _same_as_compute_night(cols, _rows_to_grade(cols, 1257), date=date,
                           alt_limit=alt_limit, site=site)


def test_tonight_equals_compute_night_under_a_pinned_clock(monkeypatch):
    """``date=None`` is the clock's night. Pinned, both read the same anchor."""
    noon_utc = 1768478400.0     # 2026-01-15 12:00 UTC
    monkeypatch.setattr(vis, "time", types.SimpleNamespace(time=lambda: noon_utc))
    site = _site(40.25, -74.5)
    cols = vis.compute_nights(RA, DEC, date=None, step_min=20,
                              alt_limit=30.0, site=site)
    _same_as_compute_night(cols, _rows_to_grade(cols, 7)[:12], date=None,
                           alt_limit=30.0, site=site)


def test_a_dark_window_between_two_samples_is_handled_as_compute_night_does(
        monkeypatch):
    """No sample inside the dark window: compute_night reports the peak
    outside the dark, no window, and the unrounded maximum for the limit test.
    The grid steps 20 min from 150 min before the window, so a one-minute
    window starting on the window start holds no sample."""
    real = vis._find_dark_window

    def narrow(loc, anchor, lon):
        start, _end, kind = real(loc, anchor, lon)
        return start, start + 60.0, kind

    monkeypatch.setattr(vis, "_find_dark_window", narrow)
    # a (site, date) of its own: the scaffold cache must not keep this window
    with vis._scaffold_lock:
        vis._scaffold_cache.clear()
    site = _site(31.0, -91.0, 7.0)
    try:
        cols = vis.compute_nights(RA[:300], DEC[:300], date="2026-02-03",
                                  step_min=20, alt_limit=30.0, site=site)
        unix = cols.scaffold["unix"]
        inside = (unix >= cols.scaffold["dark_start"]) & (
            unix <= cols.scaffold["dark_end"])
        assert not inside.any(), "premise: no sample falls in the window"
        assert (cols.win_start == -1).all() and cols.in_daylight.all()
        _same_as_compute_night(cols, range(0, 300, 15), date="2026-02-03",
                               alt_limit=30.0, site=site)
    finally:
        with vis._scaffold_lock:
            vis._scaffold_cache.clear()


def test_chunk_boundaries_change_nothing(monkeypatch):
    site = _site(40.25, -74.5)
    whole = vis.compute_nights(RA[:40], DEC[:40], date="2026-01-15",
                               step_min=20, alt_limit=30.0, site=site)
    monkeypatch.setattr(vis, "_ROW_CHUNK", 7)
    chunked = vis.compute_nights(RA[:40], DEC[:40], date="2026-01-15",
                                 step_min=20, alt_limit=30.0, site=site)
    assert [chunked.row(i) for i in range(40)] == [whole.row(i) for i in range(40)]


def test_an_empty_catalogue_is_an_empty_answer():
    cols = vis.nights_for_catalogue([], date="2026-01-15", step_min=20,
                                    alt_limit=30.0, site=_site(40.25, -74.5))
    assert len(cols) == 0


def test_no_site_is_refused_as_compute_night_refuses_it():
    default = {"name": "", "latitude": 0.0, "longitude": 0.0,
               "elevation_m": 0.0, "is_default": True}
    with pytest.raises(vis.NoSite):
        vis.compute_nights(RA[:3], DEC[:3], date="2026-01-15", site=default)
    with pytest.raises(vis.NoSite):
        vis.nights_for_catalogue(CATALOG[:3], date="2026-01-15", site=default)


# ------------------------------------------------ the cache

SITE_A = _site(40.25, -74.5, 12.0)


class _Spy:
    """Counts ``_nights_at`` calls, then runs the real one."""

    def __init__(self, monkeypatch, delay: float = 0.0):
        self.calls = 0
        self._real = vis._nights_at
        self._delay = delay
        monkeypatch.setattr(vis, "_nights_at", self)

    def __call__(self, *a, **k):
        self.calls += 1
        time.sleep(self._delay)
        return self._real(*a, **k)


def _ask(rows=CATALOG[:30], **over):
    args = dict(date="2026-01-15", step_min=20, alt_limit=30.0, site=SITE_A)
    args.update(over)
    return vis.nights_for_catalogue(rows, **args)


def test_the_same_question_is_answered_from_memory(monkeypatch):
    spy = _Spy(monkeypatch)
    first = _ask()
    assert _ask() is first and spy.calls == 1


@pytest.mark.parametrize("change", [
    {"alt_limit": 15.0},
    {"date": "2026-01-16"},
    {"step_min": 10},
    {"site": _site(40.25, -74.5, 13.0)},
    {"site": _site(40.26, -74.5)},
    {"site": _site(40.25, -74.4)},
], ids=["alt_limit", "date", "step", "elevation", "latitude", "longitude"])
def test_every_input_of_the_answer_is_in_the_key(monkeypatch, change):
    spy = _Spy(monkeypatch)
    _ask()
    _ask(**change)
    assert spy.calls == 2, "an input of the night was left out of the cache key"


def test_other_coordinates_of_the_same_length_are_not_served_stale(monkeypatch):
    """CATALOG can be replaced (a test does); a count alone would answer for
    the wrong rows."""
    spy = _Spy(monkeypatch)
    _ask(CATALOG[:30])
    shifted = _ask(CATALOG[1:31])
    assert spy.calls == 2
    assert np.array_equal(shifted.ra_hours, RA[1:31])


def test_the_cache_is_bounded_and_drops_the_oldest(monkeypatch):
    monkeypatch.setattr(vis, "_NIGHTS_MAX", 2)
    spy = _Spy(monkeypatch)
    for limit in (10.0, 20.0, 30.0):
        _ask(CATALOG[:5], alt_limit=limit)
    assert len(vis._nights_cache) == 2
    _ask(CATALOG[:5], alt_limit=10.0)      # was evicted: a rebuild
    assert spy.calls == 4


def test_concurrent_askers_share_one_build(monkeypatch):
    spy = _Spy(monkeypatch, delay=0.3)
    answers: list = []
    gate = threading.Barrier(4)

    def ask():
        gate.wait()
        answers.append(_ask())

    threads = [threading.Thread(target=ask) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert spy.calls == 1 and len({id(a) for a in answers}) == 1


def test_an_entry_cannot_be_written_to():
    cols = _ask()
    with pytest.raises(ValueError):
        cols.transit_alt[0] = 99.0
    with pytest.raises(ValueError):
        cols.win_start[0] = 0


def test_the_key_cannot_print_the_site():
    """A repr, a str or the KeyError of a bare ``cache[key]`` all print the
    key, and the key is the site. Nothing here may carry a digit of it."""
    _ask(site=_site(47.6789, -88.2468, 123.456))
    (key,) = vis._nights_cache.keys()
    shown = " ".join((repr(key), str(key), repr(vis._nights_cache),
                      repr(next(iter(vis._nights_cache.values())))))
    for digits in ("47.6789", "88.2468", "123.456"):
        assert digits not in shown, "the cache printed a coordinate"
    with pytest.raises(KeyError) as missing:
        vis._nights_cache[vis._NightsKey((1.5, 2.5))]
    assert "1.5" not in str(missing.value)
