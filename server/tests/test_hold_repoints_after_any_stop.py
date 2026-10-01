"""A mount the engine stopped is re-pointed, never resumed in place (#248,
H3 orchestrator ruling 6).

H2 (#224) decided whether a cloud hold's mount was "elsewhere" from
``_tracked_target`` alone: the target the mount was last pointed at. That
does not say the mount is still on it. The idle park-hold stops tracking
without clearing it, and so do the safety pause and the roof close. So a hold
opened for X by X's own pre-slew gate, after the idle park-hold had stopped
the mount on X during a wait, was not "elsewhere": it published "Checking at
the science exposure" over the stopped mount, and its first check resumed
tracking in place, from where X was when the mount stopped. That patch is
east of X by the length of the stop, so X's looks do not describe it (#225's
shape, reached through the same target). And the stopped-tracking branch's
"RESTORED, OR THIS LINE IS NOT REACHED" was not kept for a hold whose
``_hold_step`` was not a light step: `_enforce_tracking` returned at once for
a dark, and the opening detail was published again over a stopped mount.

THE RULING. An explicit "mount stopped since" state
(``_mount_stopped_since``), set by every stop the engine makes (the idle
park-hold's decided stop, the safety pause's `_park_hold`, the roof-close
park, `_hold_park`), and cleared only by a fresh pointing (a setup's slew, a
hold re-point, a flip's goto, the tracking-refusal recovery's re-slew). An
in-place resume never clears it. A hold treats a stopped mount as elsewhere
and re-points, at its open and in its stopped-tracking branch. A mount that
stopped on its own still takes the frame loop's resume-then-recover in place
(test_cloud_hold_watch's
`test_a_mount_that_stops_tracking_mid_hold_is_restored_not_judged`, which now
also holds that no slew is made).

THE HARNESS is test_cloud_hold_watch's `_Watched` (the clocked simulator,
the sky a script, no safety monitor) for the two nights, and the simulator's
mount on its own for the state. The site is a fixture.
"""
from __future__ import annotations

import time

import astrodeck.sequence.engine as engine_mod
from astrodeck.config import AppConfig, SafetyConfig
from astrodeck.devices.base import SafetyReading
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan
from astrodeck.sequence import schedule

from test_cloud_hold_watch import EXP, _Watched, _plan, _target
from test_idle_park_hold import (  # noqa: F401 (fixtures and harness)
    _constraint_waiter, _ra_at, sim_hub, temp_store)

CHECKING = "Checking at the science exposure"


async def test_a_hold_on_the_target_the_idle_stop_stopped_repoints_it(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """#248 (1). Auto-resume hands the run X, which it has just re-centred
    (``start(tracking=X)``). X waits for its hour-angle window under a sky
    that is already shut, and the idle park-hold stops tracking X on the idle
    clock. X's window opens; its own setup's pre-slew gate opens a hold for X,
    with ``_tracked_target`` still X. The hold treats the stopped mount as
    elsewhere: it points the mount at X, through the gates, before anything
    is judged or published as judged, and its first check is taken on X,
    tracking.

    RED before the ruling (observed) -
        AssertionError: the hold on X, whose mount the idle park-hold had
        stopped, did not point it at X at its open: slews after the open at
        [] s, and 'Checking at the science exposure' published at [0.0,
        128.0] s
    Mutant "elsewhere decided by `_tracked_target` identity alone" (the
    open's ``or self._mount_stopped_since is not None`` deleted): RED, the
    stopped-tracking branch re-points at the first check instead (observed) -
        AssertionError: the hold on X, whose mount the idle park-hold had
        stopped, did not point it at X at its open: slews after the open at
        [124.0] s, and 'Checking at the science exposure' published at [0.0]
        s
    The idle park-hold's decided stop is also recorded by its task's own
    stop, which here lands long before the hold opens, so this night stays
    green without the set at the decision; the state test below holds that.
    """
    ready_s = 300.0
    w = _Watched(sim_hub, temp_store, monkeypatch,
                 horizon_s=ready_s + 400.0, closes_at_s=-1.0)
    x = _constraint_waiter("X", w.t0, ready_after_s=ready_s)
    x.steps[0].count = 40
    # As auto-resume leaves it: re-centred on X and tracking. The check
    # frames borrow the last step the engine shot, which `start` does not
    # reset (the engine outlives its runs).
    w.tel.rig.ra_hours, w.tel.rig.dec_deg = x.ra_hours, x.dec_deg
    w.tel.rig.tracking = True
    w.engine._hold_step = x.steps[0]
    opened: list[bool] = []
    real_hold = w.engine._hold_for_clear

    async def hold(reason, target):
        opened.append(w.engine._tracked_target is target)
        return await real_hold(reason, target)

    monkeypatch.setattr(w.engine, "_hold_for_clear", hold)
    try:
        await w.night(_plan(x), tracking=x)
        t_h = w.hold_started()
        assert abs(t_h - (w.t0 + ready_s)) < engine_mod.SCHEDULE_WAIT_STEP_S, (
            f"premise: X's own setup opened the hold when its window opened, "
            f"at {t_h - w.t0:.1f} s")
        assert opened[:1] == [True], (
            "premise: the hold opened with X as the tracked target")
        idle = [t for t in w.stops(w.t0) if t < t_h]
        assert idle, "premise: the idle park-hold stopped the mount on X"
        slews = [t for t in w.run.slews if t >= t_h]
        checking = [t for t, st, _h, d in w.states
                    if t >= t_h and st == "holding" and CHECKING in d]
        assert slews and slews[0] - t_h < 0.5 and \
            all(t >= slews[0] for t in checking), (
                f"the hold on X, whose mount the idle park-hold had stopped, "
                f"did not point it at X at its open: slews after the open at "
                f"{w.rel(slews, t_h)} s, and {CHECKING!r} published at "
                f"{w.rel(checking[:2], t_h)} s")
        checks = [(round(t - t_h, 1), tr) for t, tr in w.probes if t >= t_h]
        assert checks and all(tr for _t, tr in checks), (
            f"a check was taken on the stopped mount: {checks}")
        assert w.engine._mount_stopped_since is None, (
            "the hold's re-point left the stopped state standing")
    finally:
        await w.close()


async def test_a_hold_whose_step_is_a_dark_restores_tracking_before_it_says_so(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """#248 (2). The hold's ``_hold_step`` is a dark (scripted, as the engine
    keeps the last step it shot from run to run, and a run that ended on a
    dark leaves one). The sky is already shut at the first target's setup, so
    Alpha's hold opens there and points the mount at Alpha. The mount then
    stops on its own. The next check is not judged; the hold restores
    tracking in place (the frame loop's resume, because the mount stopped on
    its own), and only then says the sky is being checked again. Never over
    a stopped mount, and the stop is said once.

    Mutant "today's early return for a non-light step" (the stopped-tracking
    branch passing ``self._hold_step`` to `_enforce_tracking` again, whose
    first line returns for a dark): RED (observed; the same failure before
    the ruling) -
        AssertionError: the hold never restored tracking after the mount
        stopped at 130.0 s: tracking turned on at [] s after it, and
        'Checking at the science exposure' published at [274.0, 398.0] s over
        the stopped mount; the stop said 3 times
    Mutant "the stopped branch re-points whatever stopped the mount" (its
    ``self._mount_stopped_since is not None`` test made True): RED
    (observed), the re-point arm taken for a mount that stopped on its own -
        AssertionError: the hold never restored tracking after the mount
        stopped at 130.0 s: tracking turned on at [274.0] s after it, and
        'Checking at the science exposure' published at [] s over the
        stopped mount; the stop said 0 times
    """
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=600.0,
                 closes_at_s=-1.0, stops_tracking_at_s=130.0)
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0)
    w.engine._hold_step = ExposureStep(filter="L", exposure_s=EXP, gain=100,
                                       count=1, frame_type="Dark")
    try:
        await w.night(_plan(a))
        t_h = w.hold_started()
        assert abs(t_h - w.t0) < 1.0, "premise: the first setup's hold"
        assert w.stopped_at is not None, "premise: the mount stopped"
        on = [t for t, tracking, _w in w.run.tracking_calls
              if tracking and t >= w.stopped_at]
        back = on[0] if on else None
        over = [t for t, st, _h, d in w.states
                if st == "holding" and CHECKING in d and t >= w.stopped_at
                and (back is None or t < back)]
        said = sum("has stopped tracking during the cloud hold" in m
                   for _l, m, _s in bus_lines)
        assert back is not None and not over and said == 1, (
            f"the hold never restored tracking after the mount stopped at "
            f"{w.stopped_at - t_h:.1f} s: tracking turned on at "
            f"{w.rel(on[:2], t_h)} s after it, and {CHECKING!r} published at "
            f"{w.rel(over[:2], t_h)} s over the stopped mount; the stop said "
            f"{said} times")
        assert not [t for t in w.run.slews if t >= w.stopped_at], (
            "a mount that stopped on its own was re-slewed, not resumed")
        assert all(tr for t, tr in w.probes if t >= t_h), (
            f"a check was judged on a stopped mount: {w.probes}")
    finally:
        await w.close()


async def test_a_mount_the_engine_stops_mid_hold_is_repointed_not_resumed(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """#248 (3), the stopped-tracking branch's own arm. Bravo's setup's
    pre-slew gate opens a hold for Bravo, which points the mount at it, so a
    setup waits behind that gate for the whole hold (#241). On the hold
    loop's second pass its frame gate opens a safety pause (the real
    `_park_hold_pause`, reading safe at once): the pause stops tracking, and
    its release, with the setup waiting behind, runs no setup of its own. The
    mount is left stopped where Bravo was when the pause began, with
    ``_hold_parked`` unset. The hold's next tracking read finds it stopped,
    and because the ENGINE stopped it the hold re-points (a slew to Bravo
    before tracking comes back), never a resume in place from the patch of
    sky the stop left it on. The mount that stops on its own is the control
    (test_cloud_hold_watch's
    `test_a_mount_that_stops_tracking_mid_hold_is_restored_not_judged`).

    Added by the T7 verifier: with the arm made dead the whole owned suite
    stayed green (68 passed), because every other night reaches a stopped
    mount the engine stopped at the hold's open, whose check it is.
    Mutant "the stopped branch never re-points" (its
    ``self._mount_stopped_since is not None`` test made False): RED
    (observed) -
        AssertionError: the mount the pause stopped at 150.0 s was not
        re-pointed before tracking came back: slews to Bravo after it at []
        s, tracking on at [293.0] s
    """
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=EXP + 600.0)
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0, count=1)
    b = _target("Bravo", _ra_at(-2.0, w.t0), 60.0)
    passes: list[float] = []
    paused: list[float] = []
    ahead = w.engine._safety_gate

    async def gate(*args, **kw):
        await ahead(*args, **kw)
        if (kw.get("context") == "frame" and w.engine._holding_for_clear
                and not w.run.frozen.is_set()):
            passes.append(w.run.clock.t)
            if len(passes) == 2 and not paused:
                paused.append(w.run.clock.t)

                async def safe():
                    return SafetyReading(is_safe=True, source="script",
                                         ts=w.run.clock.t)

                monkeypatch.setattr(w.engine, "_read_safety", safe)
                await w.engine._park_hold_pause("rain sensor",
                                                kw.get("target"))

    monkeypatch.setattr(w.engine, "_safety_gate", gate)
    try:
        await w.night(_plan(a, b, flip=False))
        t_h = w.hold_started()
        assert abs(t_h - (w.t0 + EXP)) < 1.0, (
            "premise: Bravo's setup opened the hold as Alpha's frame ended")
        assert paused, f"premise: the hold's frame gate paused: {passes}"
        t_p = paused[0]
        msgs = [m for _l, m, _s in bus_lines]
        assert any("through the acquisition the pause interrupted" in m
                   for m in msgs), (
            "premise: the pause released with Bravo's setup waiting behind "
            "it, so it ran no setup of its own")
        assert [t for t in w.stops(t_p) if t - t_p < 0.5], (
            "premise: the pause stopped tracking")
        slews = [t for t in w.run.slews if t >= t_p]
        on = [t for t, tracking, _w in w.run.tracking_calls
              if tracking and t >= t_p]
        assert slews and on and slews[0] <= on[0], (
            f"the mount the pause stopped at {t_p - t_h:.1f} s was not "
            f"re-pointed before tracking came back: slews to Bravo after it "
            f"at {w.rel(slews, t_h)} s, tracking on at {w.rel(on[:2], t_h)} s")
        assert sum("stopped by this run and has not been pointed since" in m
                   for m in msgs) == 1, "the re-point was not said once"
        assert not [m for m in msgs
                    if "has stopped tracking during the cloud hold" in m], (
            "the engine's own stop was reported as the mount stopping on its "
            "own")
        assert all(tr for t, tr in w.probes if t >= t_h), (
            f"a check was judged on a stopped mount: {w.probes}")
        assert w.engine._mount_stopped_since is None, (
            "the re-point left the stopped state standing")
    finally:
        await w.close()


# ------------------------------------------------------------------ the state

async def test_every_engine_stop_sets_the_state_and_only_a_pointing_clears_it(
        sim_hub, temp_store, monkeypatch):
    """The state itself, on the simulator's mount. Each stop the engine
    makes sets it: the idle park-hold at its decision (its stop is on a task
    of its own, which may not have run when a hold opens), the safety
    pause's `_park_hold`, `_hold_park`, and the roof-close park. An in-place
    resume (`_enforce_tracking`) turns tracking back on and leaves it set,
    because the mount resumes from where it stopped, not from where the
    target is. A hold re-point clears it.

    Mutant "the idle stop sets it only when its task stops the mount" (the
    set in `_idle_park_hold` deleted): RED (observed) -
        AssertionError: the idle park-hold decided a stop and the state does
        not say so
    Mutant "an in-place resume clears it" (``self._mount_stopped_since =
    None`` after `_enforce_tracking`'s resume): RED (observed) -
        AssertionError: an in-place resume cleared the stopped state
    Mutant "the stop primitive records nothing" (the `_note_mount_stopped`
    at the top of `_stop_tracking_quietly` deleted, which the safety pause's
    `_park_hold` and `_hold_park` both stop through): RED (observed) -
        AssertionError: _park_hold stopped the mount and the state does not
        say so
    Mutant "the roof-close park is not recorded" (the `_note_mount_stopped`
    in `_fenced_park` deleted): RED (observed) -
        AssertionError: _fenced_park stopped the mount and the state does not
        say so
    Mutant "the re-point leaves the state" (the
    ``self._mount_stopped_since = None`` after `_hold_repoint`'s successful
    slew deleted): RED (observed) -
        AssertionError: a hold re-point did not clear the stopped state
    and the #248 night above goes RED on it too, at its last line
    (observed) -
        AssertionError: the hold's re-point left the stopped state standing
    """
    monkeypatch.setattr(engine_mod, "TRACKING_CONFIRM_S", 0.0)
    e = SequenceEngine(sim_hub)
    alpha = _target("Alpha", _ra_at(-2.0, time.time()), 60.0)
    e.plan = SequencePlan(name="s", meridian_flip=False, targets=[alpha])
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False))
    tel = sim_hub.devices["telescope"]
    e._tracked_target = alpha
    assert e._mount_stopped_since is None, "premise: nothing stopped yet"
    try:
        await e._idle_park_hold("a long wait")
        assert e._mount_stopped_since is not None, (
            "the idle park-hold decided a stop and the state does not say so")
        await e._cancel_idle_stop_retry()
        stops = (("_park_hold", e._park_hold),
                 ("_hold_park", lambda: e._hold_park("ceiling", "Alpha")),
                 ("_fenced_park", e._fenced_park))
        for name, stop in stops:
            e._mount_stopped_since = None
            e._hold_parked = None
            await tel.unpark()
            await tel.set_tracking(True)
            await stop()
            assert e._mount_stopped_since is not None, (
                f"{name} stopped the mount and the state does not say so")
        await tel.unpark()
        await tel.set_tracking(False)
        await e._enforce_tracking(None, alpha)
        assert tel.rig.tracking, "premise: the resume turned tracking on"
        assert e._mount_stopped_since is not None, (
            "an in-place resume cleared the stopped state")
        e._hold_parked = None
        await e._hold_repoint(alpha, elsewhere=True)
        assert e._mount_stopped_since is None, (
            "a hold re-point did not clear the stopped state")
    finally:
        await e._cancel_idle_stop_retry()


async def test_a_flips_goto_is_a_fresh_pointing(sim_hub, temp_store,
                                                monkeypatch):
    """A flip's goto clears the stopped state, whichever side it lands on:
    it has pointed the mount at the target and turned tracking on. Asked of
    `_maybe_meridian_flip` on the simulator's mount, a minute past the flip
    point (so there is no wait), the hub's flip a goto that tracks.

    Mutant "the flip's goto leaves the state" (the
    ``self._mount_stopped_since = None`` after the flip's goto deleted): RED
    (observed) -
        AssertionError: a flip's goto left the stopped state standing
    """
    sim_hub.devices.pop("focuser", None)        # no post-flip sweep
    e = SequenceEngine(sim_hub)
    tel = sim_hub.devices["telescope"]

    async def no_countdown():
        raise RuntimeError("no countdown")

    monkeypatch.setattr(tel, "time_to_meridian_flip", no_countdown)

    async def meridian_flip(ra, dec):
        await tel.set_tracking(True)            # a goto turns tracking on
        return {"flipped": False}

    monkeypatch.setattr(sim_hub, "meridian_flip", meridian_flip)
    lead_s = 60.0 * schedule.MERIDIAN_FLIP_LEAD_MIN
    alpha = _target("Alpha", _ra_at(-(lead_s - 60.0) / 3600.0, time.time()),
                    20.0)
    e.plan = SequencePlan(name="flip", guide=False, dither_every=0,
                          autofocus_every=0, meridian_flip=True,
                          targets=[alpha])
    e._flip_armed = True
    await tel.set_tracking(False)
    e._note_mount_stopped()
    await e._maybe_meridian_flip(alpha, 0.0)
    assert tel.rig.tracking, "premise: the flip's goto was taken"
    assert e._mount_stopped_since is None, (
        "a flip's goto left the stopped state standing")


async def test_the_recoverys_park_sets_the_state_and_its_reslew_clears_it(
        sim_hub, temp_store, monkeypatch):
    """The tracking-refusal recovery parks the mount (a stop the engine
    makes) and then re-slews and re-centres the target (a fresh pointing).
    The state is set as the park goes out, so a failure between the two
    leaves the mount read as elsewhere, and cleared once the re-slew has
    landed. The hub's centring is a double that unparks, tracks and slews.

    Mutant "the recovery's park is not recorded" (the
    `_note_mount_stopped` before the recovery's park deleted): RED (observed)
    -
        AssertionError: the recovery parked the mount with the stopped state
        unset
    Mutant "the recovery's re-slew leaves the state" (the
    ``self._mount_stopped_since = None`` after its re-centre deleted): RED
    (observed) -
        AssertionError: the recovery's re-slew left the stopped state
        standing
    """
    e = SequenceEngine(sim_hub)
    tel = sim_hub.devices["telescope"]
    alpha = _target("Alpha", _ra_at(-2.0, time.time()), 60.0)
    e.plan = SequencePlan(name="r", guide=False, meridian_flip=False,
                          targets=[alpha])
    at_park: list[bool] = []
    real_park = tel.park

    async def park():
        at_park.append(e._mount_stopped_since is not None)
        return await real_park()

    monkeypatch.setattr(tel, "park", park)

    async def goto_and_center(ra_hours, dec_deg, **_kw):
        if await tel.is_parked():
            await tel.unpark()
        await tel.set_tracking(True)
        await tel.slew(ra_hours, dec_deg)
        return {"centered": True, "error_arcmin": 0.1}

    monkeypatch.setattr(sim_hub, "goto_and_center", goto_and_center)
    found = await e._do_tracking_recovery(tel, alpha, report_centring=False)
    assert found is not None, "premise: the recovery succeeded"
    assert at_park == [True], (
        "the recovery parked the mount with the stopped state unset")
    assert e._mount_stopped_since is None, (
        "the recovery's re-slew left the stopped state standing")


async def test_a_setups_slew_is_a_fresh_pointing(sim_hub, temp_store,
                                                 monkeypatch):
    """A setup's slew clears the stopped state: the mount the idle park-hold
    stopped on the last target is on this one now, tracking. Asked of
    `_setup_target` itself, uncentred, on the simulator's mount.

    Mutant "the setup's slew leaves the state" (the
    ``self._mount_stopped_since = None`` beside `_setup_target`'s
    ``_tracked_target = target`` deleted): RED (observed) -
        AssertionError: a setup's slew left the stopped state standing
    """
    e = SequenceEngine(sim_hub)
    now = time.time()
    alpha = _target("Alpha", _ra_at(-2.0, now), 60.0)
    bravo = _target("Bravo", _ra_at(-1.0, now), 50.0)
    e.plan = SequencePlan(name="s", guide=False, meridian_flip=False,
                          targets=[alpha, bravo])
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False))
    e._tracked_target = alpha
    try:
        await e._idle_park_hold("a long wait")
        assert e._mount_stopped_since is not None, "premise: stopped"
        await e._setup_target(1, bravo)
        assert e._tracked_target is bravo, "premise: the setup slewed"
        assert e._mount_stopped_since is None, (
            "a setup's slew left the stopped state standing")
    finally:
        await e._cancel_idle_stop_retry()
