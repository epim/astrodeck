"""The safety pause's re-asks of an unconfirmed stop keep their clock while
its cooler gate waits (#453; spec 5.8 and 6.17).

The open-sky pause (`_park_hold_pause`) reads its own stop of tracking back,
and while the mount has not confirmed it, asks again at most once per
``IDLE_STOP_RETRY_S`` (#345). The ask was made at the top of the pause
loop's pass. Once the weather has read safe long enough, the pass awaits
the cooler gate (`_cooler_gate(..., weather=True)`), which waits up to
``cool_timeout_s`` for a drifted sensor and ``COOLER_SETTLE_MAX_S`` more:
25 minutes at the defaults in which the loop did not come round, so a stop
the flaky link had dropped was asked for by nobody, the mount perhaps still
tracking. The "safety rides value paths" class: the re-ask rode the pause
loop's turn, which the cooler wait held, while the hazard went on.

NOW the asking is one function (``ask_if_due``) that the pause's loop calls
on each pass and the gate's weather watch (`_cool_watching_the_weather`)
calls on each of its turns, on the pause's own task, so the due time is
kept whichever of the two is waiting. An ask that hangs on a dead link
holds the watch's reads, as it holds the pause's own; the read it held is
taken as soon as it returns, before the watch looks at the sensor again, so
rain that came during the ask is not missed by a gate that settled in it.

THE HARNESS is test_refused_close_keeps_idle_stop's `_Pause` (the clocked
simulator with no run, a plain pause opened by rain at ``RAIN_AT`` from a
task of the test's that the driver clocks, ``RAIN``, the weather safe from
``SAFE_AT``, on a link that drops every stop until ``accept_at``), with a
plan that asks for ``COOL_TO`` and a sensor that reads warm until
``COOL_AT``: the gate the pause runs when the weather clears waits minutes.
The gate's sensor wait runs on a task of its own, which the driver clocks
(test_safe_again_after_cooler_gate's `_clock_the_cooling`). Every stop and
tracking read is recorded with its fake time and task, every safety read
with its fake time, every cooler gate with its span. The site is a fixture,
never the real one.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/ (S7-ENG-SAFE-r2-mut, in the session scratchpad), never in the
shared tree (#254), and every quote is from that copy's run.
"""
from __future__ import annotations

import astrodeck.sequence.engine as engine_mod
from astrodeck.devices.base import SafetyReading

from test_idle_park_hold import (_a_mount_that_will_not_stop, _plan,  # noqa: F401
                                 _target, sim_hub, temp_store)
from test_refused_close_keeps_idle_stop import (RAIN, RETRY, SAFE_AT,
                                                _Pause)
from test_safe_again_after_cooler_gate import _clock_the_cooling

COOL_TO = -5.0
#: The pause's gate, by its reason.
PAUSE_WHY = "resumed after a safety pause"
#: The watch's poll, the tolerance on "at its due time".
STEP = engine_mod.IDLE_STOP_FINISH_POLL_S
HANG = engine_mod.MOUNT_QUERY_TIMEOUT_S


def _gated_pause(p: _Pause, monkeypatch, *, cool_at: float,
                 rain_again: tuple[float, float] | None = None) -> dict:
    """Give ``p``'s engine a plan that cools to ``COOL_TO``, a sensor that
    reads warm until ``cool_at`` fake seconds after ``p.t0`` and in its
    band from then on, and the gate's sensor wait on the driver's clock.
    ``rain_again``: a second spell of rain, (from, until) in the same fake
    seconds, on top of `_Pause`'s own until ``SAFE_AT``. Returns the
    records: ``gates`` [why, began, ended, result] of every cooler gate,
    ``reads`` (fake time, safe) of every safety read."""
    run, engine = p.run, p.engine
    plan = _plan(_target("Alpha", 0.0, 40.0))
    plan.cool_to = COOL_TO
    plan.cool_timeout_s = 900
    engine.plan = plan
    rec: dict = {"gates": [], "reads": []}
    cam = p.run.hub.devices["camera"]
    assert getattr(cam, "can_cool", False), "premise: the sim cools"

    async def get_temperature():
        return COOL_TO if run.clock.t - p.t0 >= cool_at else 15.0

    monkeypatch.setattr(cam, "get_temperature", get_temperature)

    async def safety_reading():
        t = run.clock.t - p.t0
        wet = t < SAFE_AT or (rain_again is not None
                              and rain_again[0] <= t < rain_again[1])
        rec["reads"].append((run.clock.t, not wet))
        if wet:
            return SafetyReading(is_safe=False, reason="rain sensor",
                                 source="script", ts=run.clock.t)
        return SafetyReading(is_safe=True, source="script", ts=run.clock.t)

    monkeypatch.setattr(run.hub, "safety_reading", safety_reading)
    real_gate = engine._cooler_gate

    async def cooler_gate(why, **kw):
        g = [why, run.clock.t, None, None]
        rec["gates"].append(g)
        try:
            g[3] = await real_gate(why, **kw)
            return g[3]
        finally:
            g[2] = run.clock.t

    monkeypatch.setattr(engine, "_cooler_gate", cooler_gate)
    _clock_the_cooling(p, monkeypatch)
    return rec


async def test_the_pause_asks_its_unconfirmed_stop_through_its_cooler_gate(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """A plain pause, no idle stop decided, on a link that never takes the
    stop: nothing but the pause asks. The weather is safe from ``SAFE_AT``
    and the gate begins at the pause's first safe read, on a sensor that
    reads warm until 300 s later and then settles for ``COOLER_STABLE_S``:
    a gate of about seven minutes. The pause's asks go on through it, once
    a minute on their own clock, as they did before it: no gap between two
    of the pause's asks, from its own stop to the gate's end, is longer
    than ``IDLE_STOP_RETRY_S`` and one watch poll, none is shorter than
    ``IDLE_STOP_RETRY_S``, and each ask inside the gate is read back.

    Mutant "asks ride the pause loop" (the pause's `_cooler_gate` call
    without ``asks=ask_if_due``, so only the pause loop asks, as before
    #453): RED (observed) -
        AssertionError: the pause asked nothing while its cooler gate
        waited: the gate ran from 400.0 s to 820.0 s, the pause's asks at
        [130.0, 190.0, 250.0, 310.0, 370.0] s, the longest gap 450.0 s
    (It turns the dead-link case below red too, at its premise: with no
    ask inside the gate, none holds the watch across the rain.)
    """
    p = _Pause(sim_hub, monkeypatch, roof=False, accept_at=10 * SAFE_AT)
    rec = _gated_pause(p, monkeypatch, cool_at=SAFE_AT + 300.0)
    try:
        await p.rain()
        assert p.paused and p.released, "premise: the pause ran"
        start, end = p.paused[0], p.released[0]
        gates = [g for g in rec["gates"] if g[0] == PAUSE_WHY]
        assert len(gates) == 1 and gates[0][3] is True, (
            f"premise: the pause ran one cooler gate, which passed: {gates}")
        _why, began, ended, _ok = gates[0]
        assert ended - began > 5 * RETRY, (
            f"premise: the gate waited minutes: {p.rel([began, ended])} s")
        asks = p.asks_by(RAIN, lo=start, hi=end)
        upto = [t for t in asks if t <= ended]
        gaps = [round(b - a, 2) for a, b in zip(upto + [ended],
                                                upto[1:] + [ended])]
        inside = [t for t in asks if began < t <= ended]
        assert inside and max(gaps) <= RETRY + STEP + 1e-6, (
            f"the pause asked nothing while its cooler gate waited: the "
            f"gate ran from {p.rel([began])[0]} s to {p.rel([ended])[0]} s, "
            f"the pause's asks at {p.rel(asks)} s, the longest gap "
            f"{max(gaps)} s")
        between = [round(b - a, 2) for a, b in zip(asks, asks[1:])]
        assert min(between) >= RETRY - 1e-6, (
            f"the pause asked more often than once a minute: its asks at "
            f"{p.rel(asks)} s")
        for t in inside:
            assert [r for r in p.reads_by(RAIN) if t <= r < t + STEP], (
                f"the ask at {round(t - p.t0, 2)} s, inside the gate, was "
                f"not read back")
        assert p.asks_by("retry") == [], "premise: no idle stop was decided"
    finally:
        await p.run.close()


async def test_a_dead_link_s_ask_in_the_gate_does_not_hide_rain(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The link is dead: a stop hangs for ``MOUNT_QUERY_TIMEOUT_S`` and
    times out, and so does a tracking read, so each of the pause's asks
    holds its task for a minute. The gate begins at the pause's first safe
    read after ``SAFE_AT``; its first ask falls a minute later and holds
    the watch through the sensor's settle (``COOLER_STABLE_S`` cut to 30 s
    here) and the start of a second spell of rain. When the ask returns,
    the read it held is taken before the watch looks at the sensor's wait
    again: the gate ends on the rain, the pause says the weather turned,
    and nothing says the conditions are safe again until the rain has
    passed.

    Mutant "the ask after the read" (the watch's ``await asks()`` moved
    from the top of its turn to the end of it, after the turn's sleep, so
    the loop tests whether the sensor's wait is done straight after an ask,
    and a wait that settled during the ask ends on the read taken before
    it): RED (observed) -
        AssertionError: the pause's gate passed on a read taken before the
        rain at 520 s: gate [('resumed after a safety pause', 430.0, 550.0,
        True)], 'conditions safe again' 1 time(s), the safety reads inside
        the gate at [435.0, 440.0, 445.0, 450.0, 455.0, 460.0, 465.0, 470.0,
        475.0, 480.0, 485.0] s
    """
    monkeypatch.setattr(engine_mod, "COOLER_STABLE_S", 30.0)
    p = _Pause(sim_hub, monkeypatch, roof=False, accept_at=10 * SAFE_AT)
    _a_mount_that_will_not_stop(p.run, readback="unreadable")
    rain_at, clear_at = 520.0, 900.0
    rec = _gated_pause(p, monkeypatch, cool_at=480.0,
                       rain_again=(rain_at, clear_at))
    try:
        await p.rain()
        assert p.paused and p.released, "premise: the pause ran"
        gates = [(g[0], p.rel([g[1]])[0], p.rel([g[2]])[0], g[3])
                 for g in rec["gates"] if g[0] == PAUSE_WHY]
        first = gates[0] if gates else None
        asks = p.rel(p.asks_by(RAIN))
        held = [t for t in asks if first and first[1] < t < rain_at < t
                + 2 * HANG]
        assert first and held, (
            f"premise: an ask inside the first gate held the watch across "
            f"the rain at {rain_at:g} s: gates {gates}, the pause's asks at "
            f"{asks} s")
        safe = [m for _l, m, _s in bus_lines
                if m == "conditions safe again — resuming"]
        reads_in = [p.rel([t])[0] for t, _ok in rec["reads"]
                    if first[1] < t - p.t0 <= first[2]]
        assert first[3] is False and first[2] >= held[0] + 2 * HANG - STEP, (
            f"the pause's gate passed on a read taken before the rain at "
            f"{rain_at:g} s: gate {gates[:1]}, 'conditions safe again' "
            f"{len(safe)} time(s), the safety reads inside the gate at "
            f"{reads_in} s")
        turned = [m for _l, m, s in bus_lines
                  if "weather turned" in m and s == "safety"]
        assert len(turned) >= 1, (
            f"the pause did not say the weather turned: {turned}")
        passed = [g for g in gates if g[3] is True]
        assert passed and all(g[1] >= clear_at for g in passed), (
            f"a gate passed before the rain cleared at {clear_at:g} s: "
            f"{gates}")
    finally:
        await p.run.close()


async def test_control_a_confirmed_stop_is_not_asked_in_the_gate(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL. The same gated pause on a link that takes the stop: the
    pause's own stop is read back confirmed as the pause opens, and nothing
    asks it again, outside the gate or in it. It passes under "asks ride
    the pause loop" above, so that case's asks are the unconfirmed stop's
    and nothing else's.

    Mutant "ask on a confirmed stop" (both of ``ask_if_due``'s guards on
    ``unconfirmed`` taken out: ``not unconfirmed or`` dropped from its
    first test, and the ask's own ``if unconfirmed:`` made ``if True:``, so
    it asks at every due time): RED (observed) -
        AssertionError: the pause asked a stop it had read back as
        confirmed: its asks at [130.0, 194.0, 258.0, 322.0, 386.0, 450.0,
        514.0, 578.0, 642.0, 706.0, 770.0] s
    (Dropping only the first test was run too, and this case passes under
    it: the ask is guarded again by its own ``if unconfirmed:``.)
    """
    p = _Pause(sim_hub, monkeypatch, roof=False, accept_at=0.0)
    rec = _gated_pause(p, monkeypatch, cool_at=SAFE_AT + 300.0)
    try:
        await p.rain()
        assert p.paused and p.released, "premise: the pause ran"
        start, end = p.paused[0], p.released[0]
        gates = [g for g in rec["gates"] if g[0] == PAUSE_WHY]
        assert len(gates) == 1 and gates[0][2] - gates[0][1] > 5 * RETRY, (
            f"premise: the gate waited minutes: {gates}")
        asks = p.asks_by(RAIN, lo=start, hi=end)
        assert asks == [start], (
            f"the pause asked a stop it had read back as confirmed: its "
            f"asks at {p.rel(asks)} s")
        assert p.tel.rig.tracking is False
    finally:
        await p.run.close()
