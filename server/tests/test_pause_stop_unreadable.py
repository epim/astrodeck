# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A safety pause whose stop of tracking cannot be READ BACK keeps asking for
it (#345's read-back, spec 6.17 and 5.8): unknown is not stopped.

`_park_hold_pause` stops tracking, reads the stop back through
`_pause_stop_unconfirmed`, and while the mount has not confirmed it asks
again at most once per ``IDLE_STOP_RETRY_S``. The read is tri-state
(`_tracking_now`): True, False, or None when nobody can say, which on the
flaky link #345 is about is the LIKELY answer: the same dropped link that
lost the stop loses the read. `_pause_stop_unconfirmed` treats only a False
as a confirmed stop, and words the None case "its tracking state cannot be
read".

THE GAP THIS FILE CLOSES. test_refused_close_keeps_idle_stop.py grades the
read-back on a link that drops the STOP and answers the READ ("it still
reports tracking"), and its no-mount control grades the telescope guard.
Nothing graded the None arm: mutant "unreadable read as stopped"
(``if tracking is False: return False`` made ``if not tracking: return
False`` in `_pause_stop_unconfirmed`) passed every test in the S4 set
(1862 passed), and under it a pause on a link that drops both the stop and
the read says nothing and never asks again, so a mount still tracking is
left unwatched for the whole pause: the hazard #345 exists for, on the link
it is most likely on. Found by the S4 tests-that-cannot-fail review
(#414).

THE HARNESS is test_refused_close_keeps_idle_stop.py's `_Pause`: the clocked
simulator with no run, a plain pause (no roof, no idle stop decided), rain
from ``RAIN_AT`` until ``SAFE_AT``. On top of its flaky link, every tracking
read raises until the link recovers, as a dropped serial link does, and is
still recorded. The site is a fixture, never the real one.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/ (scratchpad s4-tcf-review-mut for the S4 set, s4-tcf-review-mut2 for
this file), from a byte backup restored and compared by SHA-256 after each
run, never in the shared tree (#254).
"""
from __future__ import annotations

from astrodeck.devices.base import DeviceError

from test_idle_park_hold import sim_hub, temp_store  # noqa: F401
from test_refused_close_keeps_idle_stop import (POLL_S, RAIN, RAIN_AT, RETRY,
                                                _Pause)

#: The line the pause says once when its stop is not confirmed, with the
#: detail for a read that did not answer (`_pause_stop_unconfirmed`).
UNREADABLE = ("the mount did not confirm the safety pause's stop of tracking "
              "(its tracking state cannot be read) — asking again about once "
              "a minute while the pause lasts")
#: Fake seconds after the idle clock's start when the link comes back: 90 s
#: into the pause, between its first ask again (RAIN_AT + 60) and its second.
BACK_AT = RAIN_AT + 90.0


def _reads_drop_until(p: _Pause, monkeypatch, back: float) -> list[float]:
    """Every tracking read raises until fake time ``back``, and is recorded
    in ``p.reads`` all the same; from ``back`` the harness's own read (which
    records itself) answers. Returns the fake times of the reads that
    raised."""
    run = p.run
    answering = p.tel.get_tracking
    dropped: list[float] = []

    async def get_tracking():
        if run.clock.t < back:
            dropped.append(run.clock.t)
            p.reads.append((run.clock.t, run.who()))
            raise DeviceError("the mount's link dropped the tracking read")
        return await answering()

    monkeypatch.setattr(p.tel, "get_tracking", get_tracking)
    return dropped


async def test_a_stop_that_cannot_be_read_back_is_asked_again(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The link drops the pause's stop AND its read-back until 90 s into the
    pause. The pause cannot tell whether the mount stopped, so it says once
    that the stop is unconfirmed because the state cannot be read, and asks
    again once a minute, each ask read back: the ask at 60 s is dropped too,
    the one at 120 s takes and reads back stopped, and nothing asks after
    it. Three asks, a minute or more apart; the mount is not tracking when
    the pause ends.

    Mutant "unreadable read as stopped" (``if tracking is False`` made ``if
    not tracking`` in `_pause_stop_unconfirmed`): RED (observed) -
        AssertionError: the pause asked its stop at [130.0] s and the mount
        is still tracking at the end of the pause: a read-back that could
        not be read was taken for a stop
    Mutant "unreadable worded as still tracking" (the detail's ``if
    tracking`` made ``if tracking is not False``, so a None reads "it still
    reports tracking"): RED (observed) -
        AssertionError: the pause's unconfirmed-stop lines: ["the mount did
        not confirm the safety pause's stop of tracking (it still reports
        tracking) — asking again about once a minute while the pause
        lasts"]
    The control below passed under both (observed, 1 failed, 1 passed each
    time).
    """
    p = _Pause(sim_hub, monkeypatch, roof=False, accept_at=BACK_AT)
    dropped = _reads_drop_until(p, monkeypatch, p.t0 + BACK_AT)
    try:
        await p.rain()
        assert p.paused and p.released, "premise: the pause ran"
        start, end = p.paused[0], p.released[0]
        assert [t for t in dropped if start <= t < start + POLL_S], (
            f"premise: the pause's first read-back was one the link dropped: "
            f"dropped reads at {p.rel(dropped)}")
        asked = p.asks_by(RAIN, lo=start, hi=end)
        assert len(asked) > 1 or p.tel.rig.tracking is False, (
            f"the pause asked its stop at {p.rel(asked)} s and the mount is "
            f"still tracking at the end of the pause: a read-back that could "
            f"not be read was taken for a stop")
        gaps = [b - a for a, b in zip(asked, asked[1:])]
        assert len(asked) == 3 and all(g >= RETRY for g in gaps), (
            f"the pause asked its stop at {p.rel(asked)} s: not three asks "
            f"at least a minute apart")
        for t in asked:
            assert [r for r in p.reads_by(RAIN) if t <= r < t + POLL_S], (
                f"the ask at {round(t - p.t0, 2)} s was not read back")
        assert p.tel.rig.tracking is False and asked[-1] < end
        said = [m for _l, m, _s in bus_lines
                if "did not confirm the safety pause's stop" in m]
        assert said == [UNREADABLE], (
            f"the pause's unconfirmed-stop lines: {said}")
        assert p.asks_by("retry") == [], "premise: no idle stop was decided"
    finally:
        await p.run.close()


async def test_control_a_read_that_answers_stopped_asks_nothing_more(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL. The same pause on a link that takes the stop and answers the
    read from the start: the read-back says stopped, nothing is said about
    an unconfirmed stop, and the pause asks nothing after its own stop. It
    passes under both mutants above, so the case above is a test of the
    unreadable read and not of the pause in general."""
    p = _Pause(sim_hub, monkeypatch, roof=False, accept_at=0.0)
    try:
        await p.rain()
        assert p.paused and p.released, "premise: the pause ran"
        start, end = p.paused[0], p.released[0]
        asked = p.asks_by(RAIN, lo=start, hi=end)
        assert asked == [start], (
            f"the pause asked again for a stop it had read back: "
            f"{p.rel(asked)}")
        assert [r for r in p.reads_by(RAIN) if start <= r < start + POLL_S], (
            "premise: the pause read its stop back")
        said = [m for _l, m, _s in bus_lines
                if "did not confirm the safety pause's stop" in m]
        assert said == [], said
        assert p.tel.rig.tracking is False
    finally:
        await p.run.close()
