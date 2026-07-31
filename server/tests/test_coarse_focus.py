"""Coarse focus — the way out of the loop that cost a night.

Autofocus needs stars to start; a badly-defocused rig has none; the only tool
offered for getting to rough focus WAS autofocus. This walks the travel and
hands off as soon as a position has enough stars.
"""
import asyncio

import pytest

from astrodeck.focus import coarse
from astrodeck.focus.coarse import ENOUGH_STARS, plan_positions, run_coarse_focus


# --------------------------------------------------------------- the plan

def test_nearest_first_so_the_common_case_costs_two_frames():
    """Focus is usually near where you are, and every stop costs an exposure."""
    got = plan_positions(current=20000, max_position=40000, stops=5)
    assert got[0] == 20000, f"must start where the focuser already is: {got}"
    dist = [abs(p - 20000) for p in got]
    assert dist == sorted(dist), f"not ordered by distance: {got}"


def test_the_default_span_is_the_whole_travel():
    """This runs when the user has no idea where focus is; a narrow default
    would fail exactly when it is needed."""
    got = plan_positions(current=5000, max_position=40000, stops=5)
    assert min(got) == 0 and max(got) == 40000


def test_a_span_narrows_the_search_around_the_current_position():
    got = plan_positions(current=20000, max_position=40000, span=4000, stops=5)
    assert min(got) >= 18000 and max(got) <= 22000


def test_the_search_never_leaves_the_usable_travel():
    """Clamped at both ends: a plan containing a negative position or one past
    max would be rejected by the focuser mid-search, aborting the run."""
    got = plan_positions(current=100, max_position=1000, span=100000, stops=7)
    assert all(0 <= p <= 1000 for p in got), got


def test_duplicate_stops_collapse():
    """A short span over many stops rounds several onto the same integer; each
    duplicate would cost a pointless exposure."""
    got = plan_positions(current=500, max_position=1000, span=4, stops=9)
    assert len(got) == len(set(got))


def test_a_focuser_with_no_reported_travel_still_returns_something():
    assert plan_positions(current=1234, max_position=0) == [1234]


# ------------------------------------------------------------- the search

class _Focuser:
    def __init__(self, max_position=40000, start=20000):
        self.max_position = max_position
        self._pos = start
        self.visited: list[int] = []

    async def get_position(self): return self._pos

    async def move_to(self, p):
        self._pos = int(p)
        self.visited.append(self._pos)


class _Frame:
    data = None


class _Camera:
    async def expose(self, *a, **k): return _Frame()


def _patch_detector(monkeypatch, focuser, counts_by_position):
    """Star count as a function of focuser position.

    Patches ATTRIBUTES on the real astrodeck.focus.native module rather than
    swapping the module in sys.modules: the swap leaked across xdist workers and
    made these tests pass alone and fail in the full suite — a test-isolation
    bug of exactly the kind that teaches people to ignore red.
    """
    from astrodeck.focus import native as real

    class _FakeNative:
        @staticmethod
        def detect_and_measure(_data, _params):
            return [], {"star_count": counts_by_position(focuser._pos)}

    monkeypatch.setattr(real, "NATIVE_AVAILABLE", True, raising=False)
    monkeypatch.setattr(real, "_native", _FakeNative(), raising=False)


def _run(counts, monkeypatch, **kw):
    foc = _Focuser(**{k: kw.pop(k) for k in ("max_position", "start") if k in kw})
    _patch_detector(monkeypatch, foc, counts)
    return asyncio.run(run_coarse_focus(_Camera(), foc, **kw)), foc


def test_it_stops_the_moment_a_position_has_enough_stars(monkeypatch):
    """It hands off; it does not optimise. Continuing past the first usable
    position spends the user's night finding a marginally better one."""
    res, foc = _run(lambda p: 12 if p == 0 else 0, monkeypatch, stops=5)
    assert res.success is True
    assert res.best_position == 0
    assert "autofocus" in res.message.lower()
    # 20000 (start), then outward; it must NOT keep going after the hit.
    assert foc.visited[-1] == 0


def test_the_bar_is_the_one_autofocus_itself_refuses_below(monkeypatch):
    """Three stars is not enough for a curve, so it must not be enough here."""
    res, _ = _run(lambda p: ENOUGH_STARS - 1, monkeypatch, stops=3)
    assert res.success is False


def test_a_starless_search_parks_on_the_richest_position_found(monkeypatch):
    """Not the start, and not wherever it happened to stop: the best place a
    human would continue from."""
    counts = {0: 1, 20000: 0, 40000: 3}
    res, foc = _run(lambda p: counts.get(p, 0), monkeypatch, stops=3)
    assert res.success is False
    assert res.best_position == 40000
    assert foc.visited[-1] == 40000, "must MOVE there, not just report it"


def test_the_failure_message_carries_every_count(monkeypatch):
    """"It didn't work" is unactionable at 2am; the tally shows whether the
    field was empty everywhere or promising somewhere."""
    counts = {0: 1, 20000: 0, 40000: 3}
    res, _ = _run(lambda p: counts.get(p, 0), monkeypatch, stops=3)
    for pos, n in counts.items():
        assert f"{pos}:{n}" in res.message, f"missing {pos}:{n} in {res.message!r}"


def test_a_cancelled_search_returns_the_focuser_to_where_it_started(monkeypatch):
    """A stranded focuser means the rig keeps shooting from a random position."""
    foc = _Focuser()
    _patch_detector(monkeypatch, foc, lambda p: 0)

    async def _cancel_after_one():
        task = asyncio.ensure_future(
            run_coarse_focus(_Camera(), foc, stops=9))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_cancel_after_one())
    assert foc._pos == 20000, "must restore the starting position"


def test_it_refuses_clearly_without_the_native_engine(monkeypatch):
    from astrodeck.devices.base import DeviceError
    from astrodeck.focus import native as real

    monkeypatch.setattr(real, "NATIVE_AVAILABLE", False, raising=False)
    monkeypatch.setattr(real, "_native", None, raising=False)
    with pytest.raises(DeviceError, match="native engine"):
        asyncio.run(run_coarse_focus(_Camera(), _Focuser()))
