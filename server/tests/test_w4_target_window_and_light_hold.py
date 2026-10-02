# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#596 (backlog shape b): a flow run spent its one-time setup (initial
autofocus, guider calibration) on a field with no light -- NGC 7331 slewed,
centred (281 ADU, "Not enough stars") and ran both at 20:03, twenty minutes
before its OWN window (20:23) and behind a local roofline, two nights
running (2026-09-28/29). The flow's compiled schedule carries one shared
autorun clock for every target; the per-target answer lives in
``flows.tonight``'s own computation (the Tonight card's per-target window).

Two defences, tested separately and together:

1. ``SequenceEngine._await_target_window`` -- holds a SINGLE (non-group,
   non-calibration) target's setup before its own window has opened, read
   from ``flows.tonight.target_own_window``.
2. ``SequenceEngine._hold_for_light`` (with ``_centre_once`` factored out of
   the inline centring call it retries) -- holds and retries centring when
   the astronomical window says light should be there but the solver finds
   nothing (an obstruction geometry cannot predict: a tree, a roofline).

Real sim hub (test_resume_arm.py's precedent); the astronomy itself
(``target_own_window``) is stubbed per test so these are deterministic and
owe nothing to the real calendar or to where the fixture's RA/Dec actually
sit tonight.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.events import bus
from astrodeck.flows.tonight import target_own_window
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _target(**kw) -> Target:
    base = dict(name="NGC-test", ra_hours=5.0, dec_deg=10.0, center=True,
               autofocus_first=True,
               steps=[ExposureStep(filter="L", exposure_s=1.0, count=1)])
    base.update(kw)
    return Target(**base)


def _warnings(*fragments: str) -> list[dict]:
    """Every warning log line carrying all of ``fragments``, read from the
    WHOLE ring (never a captured "before" length -- the ring is a bounded
    deque shared by the whole test session and a slice from a full one
    silently sees nothing)."""
    out = []
    for e in bus.log_history:
        if e["data"].get("level") != "warning":
            continue
        msg = e["data"].get("message", "")
        if all(f in msg for f in fragments):
            out.append(e)
    return out


# ------------------------------------------------------- target_own_window

def test_target_own_window_pure_function():
    """No engine at all: ``flows.tonight.target_own_window`` is a pure
    reader over ``catalog.visibility.compute_night``.

    NAMED MUTANT (run from a byte backup, restored and sha256-verified):
    in ``target_own_window`` (flows/tonight.py), change ``if not bw:`` to
    ``if False:``. This test then fails with:

        TypeError: 'NoneType' object is not subscriptable

    -- the never-rises case no longer answers None; it crashes building the
    window dict from nothing."""
    site = {"latitude": 35.0, "longitude": -110.0, "elevation_m": 1200.0,
           "is_default": False}
    now = time.time()

    window = target_own_window(5.5, 20.0, site=site, min_altitude_deg=30.0,
                               now=now)
    assert window is not None
    assert window["start_unix"] <= window["end_unix"]
    assert window["mean_alt"] >= 30.0

    # A floor this target can never clear in the dark -> None, never a crash.
    assert target_own_window(5.5, -85.0, site=site, min_altitude_deg=60.0,
                             now=now) is None

    # An unset site (the "is_default" marker the whole app reads this by,
    # site_gate.site_is_set) -> None, the same "cannot judge" answer
    # schedule.gating_status gives for "no site".
    assert target_own_window(5.5, 20.0, site={"is_default": True},
                             min_altitude_deg=30.0, now=now) is None


# --------------------------------------------------- _await_target_window

async def test_await_target_window_holds_until_it_opens(sim_hub, monkeypatch):
    """NAMED MUTANT (run from a byte backup, restored and sha256-verified):
    in ``_await_target_window`` (engine.py), change
    ``while time.time() < window["start_unix"]:`` to ``while False:``. This
    test then fails with:

        assert 0.0 >= 0.9

    -- the hold returns at once instead of waiting for the window.

    REAL DWELL, OPTED IN. RE-PINNED FOR WP-31's own follow-up (backlog wave
    4, owner-approved 2026-09-30): this wait used to collapse under
    ``ASTRODECK_FAST_TEST`` directly, the same flag a simulated device's
    pacing reads, and this test opted out with ``monkeypatch.delenv(
    "ASTRODECK_FAST_TEST")``. Production code must not branch on a test
    flag (test_w4_no_engine_fast_test_read.py), so the hold now reads its
    own module switch (``engine_mod._SKIP_TARGET_HOLDS_FOR_TEST``,
    conftest's ``_skip_target_holds`` sets it for the whole suite), and
    proving the hold itself needs that switch flipped back to ``False``
    for this one test instead."""
    monkeypatch.setattr(engine_mod, "_SKIP_TARGET_HOLDS_FOR_TEST", False)
    engine = SequenceEngine(sim_hub)
    target = _target()
    opens_at = time.time() + 0.6
    window = {"start_unix": opens_at, "end_unix": opens_at + 3600.0,
             "mean_alt": 50.0}
    monkeypatch.setattr(
        "astrodeck.flows.tonight.target_own_window",
        lambda *a, **kw: dict(window))

    t0 = time.monotonic()
    await engine._await_target_window(target)
    elapsed = time.monotonic() - t0

    # The floor on each sleep (`max(1.0, ...)`, so a near-open window does
    # not spin) means at least one full second passes even for a window
    # 0.6s away.
    assert elapsed >= 0.9
    assert time.time() >= window["start_unix"]
    hits = _warnings(target.name, "holding before its own window")
    assert len(hits) == 1, bus.log_history[-5:]


async def test_await_target_window_returns_at_once_when_already_open(
        sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    target = _target(name="already-up")
    window = {"start_unix": time.time() - 600.0, "end_unix": time.time() + 600.0,
             "mean_alt": 50.0}
    monkeypatch.setattr(
        "astrodeck.flows.tonight.target_own_window",
        lambda *a, **kw: dict(window))

    t0 = time.monotonic()
    await engine._await_target_window(target)
    elapsed = time.monotonic() - t0

    assert elapsed < 0.5
    assert _warnings("already-up", "holding") == []


async def test_await_target_window_never_holds_a_group_member(
        sim_hub, monkeypatch):
    """A mosaic panel's timing is the group's own question
    (`_group_gate`/`_group_hop_checks`), asked every pass; this gate would
    be a second, disagreeing answer for the same panel."""
    from astrodeck.sequence.models import TargetGroup
    engine = SequenceEngine(sim_hub)
    group = TargetGroup(id="g1", name="mosaic")
    engine._groups = {"g1": group}      # the lookup `_group_of` reads
    target = _target(name="panel-1-1", mosaic_group="g1")

    opens_at = time.time() + 3600.0     # would hold for an hour if asked
    monkeypatch.setattr(
        "astrodeck.flows.tonight.target_own_window",
        lambda *a, **kw: {"start_unix": opens_at, "end_unix": opens_at + 1,
                          "mean_alt": 50.0})

    t0 = time.monotonic()
    await engine._await_target_window(target)
    elapsed = time.monotonic() - t0

    assert elapsed < 0.5


async def test_await_target_window_never_holds_calibration(sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    target = _target(name="darks", calibration=True, ra_hours=0.0, dec_deg=0.0)
    opens_at = time.time() + 3600.0
    monkeypatch.setattr(
        "astrodeck.flows.tonight.target_own_window",
        lambda *a, **kw: {"start_unix": opens_at, "end_unix": opens_at + 1,
                          "mean_alt": 50.0})

    t0 = time.monotonic()
    await engine._await_target_window(target)
    elapsed = time.monotonic() - t0

    assert elapsed < 0.5


# ------------------------------------------------------------ _hold_for_light

async def test_hold_for_light_retries_until_centring_finds_light(
        sim_hub, monkeypatch):
    """A single target's centring solve that finds nothing (no
    ``error_arcmin`` at all -- GENERIC_SOLVE_FAILURE, #596) holds and
    retries the SAME centring (`_centre_once`) rather than handing a dark
    field straight to autofocus.

    NAMED MUTANT (run from a byte backup, restored and sha256-verified):
    in ``_hold_for_light`` (engine.py), change
    ``attempts < self._NO_LIGHT_MAX_RETRIES`` to ``attempts < 0``. This
    test, ``test_hold_for_light_gives_up_after_the_attempt_cap`` and the
    end-to-end ``test_setup_target_holds_for_light_before_autofocus_runs``
    then all fail; this one with:

        AssertionError: assert {'centered': ...'arcmin': None} == \
{'centered': ...'arcmin': 0.05}
        Differing items:
        {'error_arcmin': None} != {'error_arcmin': 0.05}
        {'centered': False} != {'centered': True}

    -- the hold never retries at all, so the first (dark) result is handed
    straight back."""
    monkeypatch.setattr(engine_mod, "CENTRING_HOLD_RETRY_S", 0.01)
    engine = SequenceEngine(sim_hub)
    target = _target(name="obstructed")

    calls = {"n": 0}
    async def scripted_goto(ra_hours, dec_deg, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"centered": False, "error_arcmin": None}
        return {"centered": True, "error_arcmin": 0.05}
    monkeypatch.setattr(sim_hub, "goto_and_center", scripted_goto)

    first_miss = {"centered": False, "error_arcmin": None}
    result = await engine._hold_for_light(target, None, first_miss)

    assert result == {"centered": True, "error_arcmin": 0.05}
    assert calls["n"] == 3        # the two retries `_hold_for_light` made
    hits = _warnings("obstructed", "holding for light")
    assert len(hits) == 1, bus.log_history[-5:]


async def test_hold_for_light_gives_up_after_the_attempt_cap(sim_hub, monkeypatch):
    """Still dark after every retry: `_hold_for_light` gives up (bounded by
    `_NO_LIGHT_MAX_RETRIES`) and hands back the last miss -- the caller is
    left to do what it always did (log and continue unguided/unfocused),
    never to spin forever on a target that stays obstructed all night."""
    monkeypatch.setattr(engine_mod, "CENTRING_HOLD_RETRY_S", 0.01)
    engine = SequenceEngine(sim_hub)
    target = _target(name="always-dark")

    calls = {"n": 0}
    async def always_miss(ra_hours, dec_deg, **kw):
        calls["n"] += 1
        return {"centered": False, "error_arcmin": None}
    monkeypatch.setattr(sim_hub, "goto_and_center", always_miss)

    first_miss = {"centered": False, "error_arcmin": None}
    result = await engine._hold_for_light(target, None, first_miss)

    assert result == {"centered": False, "error_arcmin": None}
    assert calls["n"] == engine._NO_LIGHT_MAX_RETRIES


async def test_hold_for_light_does_not_retry_a_measured_miss(sim_hub, monkeypatch):
    """A centring that DID solve, just not within tolerance (`error_arcmin`
    is a real number), is not "no light" -- `_hold_for_light` is never
    asked, and today's "continuing" behaviour for that case is unchanged.
    This is `_hold_for_light`'s OWN precondition, so the test calls it
    directly with a result that should never reach it in production and
    confirms the loop condition itself (``hop_centring.get("error_arcmin")
    is None``) is what gates the retry, not merely ``not centered``."""
    monkeypatch.setattr(engine_mod, "CENTRING_HOLD_RETRY_S", 0.01)
    engine = SequenceEngine(sim_hub)
    target = _target(name="slightly-off")

    calls = {"n": 0}
    async def never_called(ra_hours, dec_deg, **kw):
        calls["n"] += 1
        return {"centered": True, "error_arcmin": 0.01}
    monkeypatch.setattr(sim_hub, "goto_and_center", never_called)

    measured_miss = {"centered": False, "error_arcmin": 2.3}
    result = await engine._hold_for_light(target, None, measured_miss)

    assert result == measured_miss
    assert calls["n"] == 0


# ------------------------------------------- end-to-end: _setup_target wiring

async def test_setup_target_holds_for_light_before_autofocus_runs(
        sim_hub, monkeypatch):
    """END TO END through the real ``engine.start()`` -> ``_setup_target``
    path (not calling ``_hold_for_light`` directly): proves the DISPATCH
    itself -- the ``elif member is None and err is None and not
    via_recovery:`` branch added beside the existing centring-miss handling
    -- actually reaches `_hold_for_light` before autofocus, not just that
    the helper works in isolation. The target's own window is stubbed open
    (Step 1 is not this test's concern); the sim's centring is scripted to
    miss twice with no light, then find it.

    NAMED MUTANT (run from a byte backup, restored and sha256-verified):
    in ``_setup_target`` (engine.py), change that whole ``elif`` to ``elif
    False:``. This test then fails with:

        AssertionError: centring did not retry through the no-light hold
        assert 1 >= 3

    -- the dispatch never reaches `_hold_for_light` at all, so autofocus
    runs straight off the first (dark) centring miss, the original bug."""
    monkeypatch.setattr(engine_mod, "CENTRING_HOLD_RETRY_S", 0.01)
    monkeypatch.setattr(
        "astrodeck.flows.tonight.target_own_window",
        lambda *a, **kw: {"start_unix": time.time() - 600.0,
                          "end_unix": time.time() + 3600.0, "mean_alt": 50.0})

    engine = SequenceEngine(sim_hub)
    real_goto = sim_hub.goto_and_center
    calls = {"n": 0}
    async def scripted_goto(ra_hours, dec_deg, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"centered": False, "error_arcmin": None}
        return await real_goto(ra_hours, dec_deg, **kw)
    monkeypatch.setattr(sim_hub, "goto_and_center", scripted_goto)

    # A STUB, not a spy that still runs the real sweep: this test asks only
    # WHEN `_setup_target` calls autofocus, never what a real sweep then
    # does to the focuser or to the process-wide temp-comp config (#227's
    # guard) -- a concern test_resume_arm.py and every other engine test
    # here sidesteps the same way, by keeping autofocus off the target
    # entirely when it is not what the test is about.
    af_labels: list[str] = []
    async def stub_autofocus(label, **kw):
        af_labels.append(label)
        return True
    monkeypatch.setattr(engine, "_autofocus", stub_autofocus)

    plan = SequencePlan(name="w4-setup-wiring", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[
                            Target(name="roofline", ra_hours=5.0, dec_deg=10.0,
                                  center=True, autofocus_first=True,
                                  steps=[ExposureStep(filter="L",
                                                      exposure_s=0.05,
                                                      count=1)])])
    engine.start(plan)

    deadline = asyncio.get_event_loop().time() + 10.0
    while asyncio.get_event_loop().time() < deadline:
        if engine._frames_done >= 1:
            break
        await asyncio.sleep(0.02)
    await engine.abort()

    assert engine._frames_done >= 1, "the run never reached its first frame"
    assert calls["n"] >= 3, "centring did not retry through the no-light hold"
    # Exactly once, and only the real centring call that found light ran it
    # -- not once per miss, which would have spent the sweep on a field with
    # nothing in it, the original bug.
    assert af_labels == ["initial autofocus"]


async def test_setup_target_does_not_hold_after_a_recovery_misses(
        sim_hub, monkeypatch):
    """#171's contract (test_recovery_centring_is_measured.py,
    ``test_a_recovery_that_did_not_converge_leaves_centered_false``) must
    survive #596 shape b: when the tracking-refusal recovery's OWN
    re-centre finds nothing to solve, setup reports that miss ONCE and
    continues, exactly as before -- it must not ALSO route it through
    `_hold_for_light`, which would be a second, later report of the same
    miss (through a different message) and would retry a centring the
    recovery already made. ``via_recovery`` is what keeps the two paths
    apart; this is `_setup_target`'s own minimal harness
    (test_recovery_centring_is_measured.py's ``_engine``), not the full
    sim run, so it reaches exactly the branch under test.

    NAMED MUTANT (run from a byte backup, restored and sha256-verified):
    in ``_setup_target`` (engine.py), delete ``and not via_recovery`` from
    the no-light ``elif``. This test then fails with:

        AssertionError: assert 1 == 0

    -- the recovery's own miss is held and retried a second time, on top
    of (not instead of) the report #171 already made."""
    t = Target(name="Alpha", ra_hours=22.6, dec_deg=34.4, center=True,
              autofocus_first=False,
              steps=[ExposureStep(filter="L", exposure_s=1.0, count=1)])
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(name="recover", guide=False,
                               meridian_flip=False, safety_check=False,
                               targets=[t])
    engine._cfg = None

    async def refused(ra_hours, dec_deg, **kw):
        raise RuntimeError("tracking on rejected (reply '0')")
    monkeypatch.setattr(sim_hub, "goto_and_center", refused)

    async def tracking_now():
        return False
    monkeypatch.setattr(engine, "_tracking_now", tracking_now)

    async def recovered(target, *, centring=None):
        if centring is not None:
            centring.update({"centered": False, "error_arcmin": None})
        return True
    monkeypatch.setattr(engine, "_recover_from_tracking_refusal", recovered)

    real_hold = engine._hold_for_light
    hold_calls = {"n": 0}
    async def spy_hold(*a, **kw):
        hold_calls["n"] += 1
        return await real_hold(*a, **kw)
    monkeypatch.setattr(engine, "_hold_for_light", spy_hold)

    await engine._setup_target(0, t)

    assert hold_calls["n"] == 0
    hits = _warnings("Alpha", "centering", "continuing")
    assert len(hits) == 1, bus.log_history[-5:]
