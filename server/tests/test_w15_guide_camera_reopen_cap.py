# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A guide camera that never comes back is reopened a bounded number of times,
and the night is told once that the gate has stopped (#703).

WP-89 (#16) made the frame-boundary reconnect gate soft-fail a guide camera:
a camera that will not reopen warns, names ``escalation.guiding_action`` and
carries on, so an imaging night never ends over its guide camera. It then
held the camera off for a 60 s cool-off and tried again, for as long as the
night lasted. That is bounded in rate and not in count: a camera that is
gone for good was reopened and warned about once a minute until dawn, and
each attempt publishes a ``reconnect`` event that alerting never dedupes
(``_NEVER_DEDUPE``), so it paged about as often.

THE CAP. ``GUIDE_CAMERA_REOPEN_CAP`` failed reopen attempts in a row per run,
the night's. The attempt that reaches it is the last: the gate says so ONCE,
at warning, naming ``escalation.guiding_action`` and the policy it is set to,
which is what decides what guiding does without the camera, and never asks
the guide camera to reopen again that run. A pass is never allowed past the
cap: with ``reconnect_retries`` of 3 the passes are 3 attempts and then 2, so
the bound is on attempts and not on passes. The count is the run's (it starts
again with a new run's ``_started_at``), and a camera that comes back starts
it again, whether the gate reopened it or something else did (a profile
activate): the cap is for a camera that does not come back, and a camera that
has proved it can is never abandoned for the failures before it.

THE NIGHT is the clocked simulator (tests/_group_harness.py): FRAMES frames
of 30 s, the guide camera disconnected, every reopen refused. The gate runs at
each frame boundary for real, on the fake clock, and the cool-off is the
engine's own 60 s on it.

Named mutants, each applied from a byte backup, run, and restored
byte-identically (sha256 compared, the mutant text grepped gone).
"""
from __future__ import annotations

import pytest

import astrodeck.sequence.engine as engine_mod
from _group_harness import (T0, Night, group_hub, group_store,  # noqa: F401
                            single)
from astrodeck.sequence import SequencePlan

RETRIES = 2
CAP = engine_mod.GUIDE_CAMERA_REOPEN_CAP
#: Twelve minutes of fake time: enough passes, a minute apart, for the cap to
#: be reached with room to spare, and for a comeback and a second outage.
FRAMES = 24


def _plan(frames: int = FRAMES) -> SequencePlan:
    return SequencePlan(name="guide camera gone", guide=True, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        park_when_done=False, warm_cooler_when_done=False,
                        recover_guiding=False,
                        targets=[single("A", count=frames)])


def _gate_on(monkeypatch, *, retries: int = RETRIES, action: str = "warn"):
    """The healing gate on, with ``retries`` attempts to a pass and
    ``action`` as the rig's guiding policy; the config is the test's own
    store's, which the engine snapshots at the run's start."""
    esc = engine_mod.config_store.cfg().escalation
    monkeypatch.setattr(esc, "reconnect_resume", True)
    monkeypatch.setattr(esc, "reconnect_retries", retries)
    monkeypatch.setattr(esc, "guiding_action", action)


async def _night_with_a_dead_guide_camera(group_hub, monkeypatch, plan):
    """Run ``plan`` with the guide camera disconnected and every reopen of
    it refused. Returns ``(night, attempts)``: the fake time of each reopen
    attempt of the guide camera, in order. Other roles are healthy and are
    never asked."""
    await group_hub.devices["guide_camera"].disconnect()
    attempts: list[float] = []
    night = Night(group_hub, monkeypatch, t0=T0)

    async def reconnect_role(role):
        assert role == "guide_camera", f"premise: only it dropped out: {role}"
        attempts.append(night.clock.t)
        return False

    monkeypatch.setattr(group_hub, "reconnect_role", reconnect_role)
    try:
        night.done = await night.run(plan, wall_s=120.0)
    finally:
        await night.close()
    assert night.done, f"premise: the night ended: {night.trace[-3:]}"
    return night, attempts


async def test_a_guide_camera_that_never_comes_back_is_reopened_at_most_the_cap(
        group_hub, monkeypatch):
    """(#703) A night with a guide camera that refuses every reopen. The gate
    tries ``GUIDE_CAMERA_REOPEN_CAP`` times and no more; the night still ends
    complete (a soft-fail never ends an imaging night).

    MUTANT "no cap" (the cap check and the trim of the pass removed from the
    gate): RED (observed):
        AssertionError: the guide camera was reopened 24 times in a night of
        24 frames; the cap is 5
    """
    _gate_on(monkeypatch)
    night, attempts = await _night_with_a_dead_guide_camera(
        group_hub, monkeypatch, _plan())

    assert len(attempts) >= 1, "premise: the gate tried at all"
    assert len(attempts) == CAP, (
        f"the guide camera was reopened {len(attempts)} times in a night of "
        f"{FRAMES} frames; the cap is {CAP}")
    assert night.engine.state.get("state") == "complete", (
        f"the night ended {night.engine.state.get('state')}, not complete: "
        f"{night.engine.state}")


async def test_the_gate_says_once_that_it_has_stopped_and_names_the_policy(
        group_hub, monkeypatch):
    """(#703) One line, at warning, when the cap is reached: the gate will not
    reopen the guide camera again this night, and guiding falls to
    ``escalation.guiding_action``, named with the setting it has. Not said
    again at any later boundary, and nothing about the guide camera is said
    after it. The earlier give-up lines are #16's and stay.

    MUTANT "the final line said at every later boundary" (the cap's
    `continue` replaced by a fall-through to the line): RED (observed):
        AssertionError: the stop was said 10 times; it is said once
    MUTANT "the final line unnamed" (``escalation.guiding_action`` and the
    policy cut from it): RED (observed):
        AssertionError: the final line does not name the policy:
        guide_camera dropped out and did not come back after 5 reopen
        attempts this night; it will not be reopened again, imaging
        continues
    """
    _gate_on(monkeypatch, action="skip")
    night, attempts = await _night_with_a_dead_guide_camera(
        group_hub, monkeypatch, _plan())

    stops = [(t, lvl, m) for t, lvl, m in night.lines
             if "will not be reopened again" in m]
    assert len(stops) == 1, f"the stop was said {len(stops)} times; it is said once"
    t, level, message = stops[0]
    assert level == "warning"
    assert "guide_camera" in message
    assert "escalation.guiding_action" in message and "skip" in message, (
        f"the final line does not name the policy: {message}")
    assert t >= attempts[-1], "the stop was said before the last attempt"
    after = [m for t2, _lvl, m in night.lines
             if t2 > t and "guide_camera" in m]
    assert after == [], f"the guide camera was spoken of after the stop: {after}"


async def test_the_cap_trims_a_pass_and_the_spacing_between_passes_stays_a_minute(
        group_hub, monkeypatch):
    """(#703) With 3 attempts to a pass and a cap of 5 the passes are 3 and 2:
    the second is trimmed so the bound is on attempts. Passes are still a
    cool-off apart (the 60 s of #16), so the cap bounds the count and the
    cool-off still bounds the rate.

    MUTANT "the pass is not trimmed" (``tries`` left at ``reconnect_retries``
    on the guide camera's pass): RED (observed):
        AssertionError: the guide camera was reopened 6 times in a night of
        24 frames; the cap is 5
    MUTANT "the cool-off dropped" is test_w14_stranded_guide_camera_is_
    reconnected's m6 and stays red there.
    """
    _gate_on(monkeypatch, retries=3)
    night, attempts = await _night_with_a_dead_guide_camera(
        group_hub, monkeypatch, _plan())

    assert len(attempts) == CAP, (
        f"the guide camera was reopened {len(attempts)} times in a night of "
        f"{FRAMES} frames; the cap is {CAP}")
    gaps = [round(b - a, 1) for a, b in zip(attempts, attempts[1:])]
    passes = [g for g in gaps if g >= engine_mod.RECONNECT_BACKOFF_S * 2]
    assert len(passes) == 1 and passes[0] >= 60.0, (
        f"the two passes are not a cool-off apart: gaps {gaps}")


async def test_a_new_run_starts_the_count_again(group_hub, monkeypatch):
    """(#703) The count is the run's: ``(the run's start, count)``, so a run on
    an engine whose last run spent the cap begins with the whole of it, and no
    run-start block has to know the counter exists. The gate is driven
    directly, one attempt to a pass so that no backoff sleep is taken, with
    the cool-off reset between passes (it is #16's, and not under test here).

    Added by the WP-107 verifier: the run half of the counter was in the code
    and in this file's prose, and no case had a second run.

    MUTANT "the count is the engine's" (the ``counted_for != self._started_at``
    reset removed from the gate): RED (observed):
        AssertionError: a new run's guide camera was not reopened: the cap of
        5 carried over (5 attempts over two runs)
    """
    _gate_on(monkeypatch, retries=1)
    await group_hub.devices["guide_camera"].disconnect()
    attempts: list[str] = []

    async def reconnect_role(role):
        assert role == "guide_camera", f"premise: only it dropped out: {role}"
        attempts.append(role)
        return False

    monkeypatch.setattr(group_hub, "reconnect_role", reconnect_role)
    eng = engine_mod.SequenceEngine(group_hub)
    eng._cfg = engine_mod.config_store.cfg()
    eng.plan = _plan()
    eng._started_at = T0
    for _ in range(CAP + 3):
        eng._guide_camera_retry_at = 0.0
        await eng._reconnect_gate()
    assert len(attempts) == CAP, (
        f"premise: the first run stopped at the cap: {len(attempts)}")

    eng._started_at = T0 + 3600.0             # a new run on the same engine
    eng._guide_camera_retry_at = 0.0
    await eng._reconnect_gate()
    assert len(attempts) == CAP + 1, (
        f"a new run's guide camera was not reopened: the cap of {CAP} "
        f"carried over ({len(attempts)} attempts over two runs)")


#: How the camera comes back, and how many frames after it does it drop again:
#: ``(who brings it back, frames until it is gone again)``. A camera that drops
#: at once is gone before the gate has seen it healthy, so only the reopen's
#: own reset can have cleared the count; one that drops three frames later has
#: been seen healthy, so the gate's healthy-branch reset is enough; and one
#: that something else brought back is only ever seen healthy.
COMEBACKS = [
    pytest.param("gate", 1, id="the gate reopens it and it drops at once"),
    pytest.param("gate", 3, id="the gate reopens it and it drops later"),
    pytest.param("else", 3, id="something else reconnects it"),
]


@pytest.mark.parametrize("who, drops_after", COMEBACKS)
async def test_a_camera_that_comes_back_starts_its_count_again(
        who, drops_after, group_hub, monkeypatch):
    """(#703) The first outage fails ``CAP - 1`` attempts, one short of the cap,
    and the camera comes back: its own reopen succeeds on the next attempt, or
    something else (a profile activate) reconnects it. A few frames later it
    drops again, and every reopen is refused. The cap is for a camera that
    does not come back, so the second outage has the whole of it again:
    ``CAP`` more attempts (not the one the first outage left), then the one
    line. A count that survived the comeback would abandon a camera that had
    proved it could reconnect, after a single attempt.

    The drop is made from the engine's own capture, ``drops_after`` frames
    after the camera is back.

    MUTANT "a reopen that worked does not clear the count" (the reset after
    the gate's own successful reopen removed): RED (observed), for the case
    where the camera drops at once, the only one in which the gate has not
    seen it healthy first:
        AssertionError: after it came back the camera was reopened 1 more
        time(s); a fresh outage is allowed 5
    MUTANT "the healthy branch does not clear the count" (the reset beside
    the cool-off's removed): RED (observed), for the case where something
    else reconnects it:
        AssertionError: after it came back the camera was reopened 1 more
        time(s); a fresh outage is allowed 5
    """
    _gate_on(monkeypatch)
    gcam = group_hub.devices["guide_camera"]
    await gcam.disconnect()
    attempts: list[float] = []
    stage = {"back_at": None, "dropped": False}
    night = Night(group_hub, monkeypatch, t0=T0)

    async def reconnect_role(role):
        attempts.append(night.clock.t)
        if who == "gate" and stage["back_at"] is None and len(attempts) == CAP:
            await gcam.connect()
            stage["back_at"] = len(night.captures)
            return True
        return False

    def on_capture(rec):
        n = len(night.captures)
        if (who == "else" and stage["back_at"] is None
                and len(attempts) >= CAP - 1):
            gcam.connected = True               # a profile activate, say
            stage["back_at"] = n
        elif (stage["back_at"] is not None and not stage["dropped"]
                and n >= stage["back_at"] + drops_after):
            gcam.connected = False              # and gone again
            stage["dropped"] = True

    night.on_capture = on_capture
    monkeypatch.setattr(group_hub, "reconnect_role", reconnect_role)
    try:
        night.done = await night.run(_plan(), wall_s=120.0)
    finally:
        await night.close()
    assert night.done
    assert stage["back_at"] is not None and stage["dropped"], (
        f"premise: the camera came back and dropped again: {stage}")
    first = CAP if who == "gate" else CAP - 1
    later = len(attempts) - first
    assert later == CAP, (
        f"after it came back the camera was reopened {later} more time(s); "
        f"a fresh outage is allowed {CAP}")
    stops = night.said("will not be reopened again")
    assert len(stops) == 1, f"the stop was said {len(stops)} times: {stops}"
