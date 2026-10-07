# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""D-05, the engine half (backlog ruling D-05, owner-approved 2026-09-30;
#648): the nightly rotator self-test before the first rotating group, and a
fixed angle when rotation is untrusted.

WP-32b built the primitives in hub.py: ``Hub.rotator_self_test`` (a 20 degree
step, two solves, 90% followed) and the ``_rotation_trusted`` gate that makes
``rotate_to_pa`` refuse after a failed self-test. Nothing in the engine ran
the self-test, and a failed trust made every ``rotate_to_pa`` raise, which
``_group_hop_checks`` turned into a deferral of every panel of a rotating
mosaic for the rest of the night: the opposite of D-05's "panels are shot at a
fixed angle". This file pins the three halves the engine owes:

* the self-test runs at most once per observing night, before the first
  rotating group's first hop, with at most two attempts (a self-test that
  could not run says nothing, so it is asked again once);
* with trust False the engine requests no ``rotation_deg`` and judges the
  group by the fixed-camera angle rules: a grid laid at ``pa_deg`` is shot
  only where the camera already sits within tolerance, otherwise the group is
  set aside once with the turn-the-camera wording, never a per-panel
  "rotation" deferral;
* one night-log line and one report entry say that rotation is off.

The cases run the REAL `_run_scheduled`, `_setup_target` and `_run_step` on
the clocked simulator (tests/_group_harness.py), with the REAL
``Hub.rotator_self_test`` against the simulator's rotator, whose play knob
(``rotator_backlash_deg``) makes the camera not follow the motor, as
test_w5_rotator_trust.py does. The harness replaces ``goto_and_center`` with a
script that never turns the rotator, so a case that needs the hub's refusal
of a rotation request after a failed self-test installs it in the goto script
(`_hub_refuses_rotation`), word for word what ``Hub.rotate_to_pa`` and
``goto_and_center`` do: ``rotation_skipped``.

WHAT THE SELF-TEST COSTS A HARNESS NIGHT. The engine now runs the hub's
self-test at the first rotating hop, and the simulator's rotator is
connected with its coupling unmeasured, so a rotating-group night on the
shared harness makes one more goto (the self-test's, onto the panel's
field, which `Night.visits` counts as a visit) and one real solve pair whose
records are stamped on the real clock, which the night's fake clock reads as
fresh. Cases that are not about the self-test want it off: a harness that
presets the simulator rotator's verdict (``_rotation_trusted = True`` in
`_group_harness.night_hub`, as ``connect_sim`` already declares the sign)
keeps them as they were. THESE cases state their own premise instead: the
``hub`` fixture below resets the verdict to unmeasured, so they hold with
or without that preset.

NAMED MUTANTS, each run from a byte backup of engine.py (or resume_arm.py)
in this worktree and restored byte for byte (sha256 compared), and the text
of the mutant gone from the tree afterwards; the failure each produced is
quoted on the test that caught it.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.sequence.engine as engine_mod
from _group_harness import (GROUP_NAME, Night, grid_plan, group_hub,  # noqa: F401
                            group_store, single)
from astrodeck.api.redact import _redact_log_rows_for
from astrodeck.auth import principal_for_role
from astrodeck.devices.base import DeviceError, Rotator
from astrodeck.hub import Hub, ROTATOR_SELF_TEST_STEP_DEG
from astrodeck.rotation import ROTATOR_BACKLASH_DEG
from astrodeck.sequence import group_rules
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.session import session_store
from test_group_meridian import _night as _meridian_night
from test_group_meridian import meridian_plan

PA, TOL = 30.0, 6.0
ROTATING = {"rotate": True, "pa_deg": PA, "angle_tolerance_deg": TOL}
FIXED = {"pa_deg": PA, "angle_tolerance_deg": TOL}
OFF_LINE = "rotation is off for the night"
PANELS = ("1-1", "1-2", "2-2", "2-1")


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


@pytest.fixture
async def hub(group_hub):
    """The harness's simulated rig, with its rotator's coupling UNMEASURED:
    ``_rotation_trusted`` None, a fresh connect's state, which is the one
    the engine's self-test exists for. Said here and not assumed, so these
    cases hold whether or not the shared harness presets a verdict on the
    simulator's rotator (whose physics are ideal unless a case dials play
    into them)."""
    group_hub._rotation_trusted = None
    return group_hub


def _slip(hub) -> None:
    """A coupling that slips: 50 degrees of play with the motor already at
    the bottom of it, so the self-test's +20 degree step is absorbed whole
    and the camera does not turn at all (test_w5_rotator_trust.py's shape,
    and what #594 measured on the real rig)."""
    rig = hub.sim_rig
    rig.rotator_backlash_deg = 50.0
    rig.rotator_slack_deg = -25.0


def _count_self_tests(monkeypatch, *, box: dict | None = None) -> list:
    """Wrap ``Hub.rotator_self_test`` so every call is counted, and the real
    one still runs. ``box["night"]``, when given, lets a call note how many
    gotos the engine had made when it asked."""
    calls: list[dict] = []
    real = Hub.rotator_self_test

    async def counted(self, *a, **kw):
        night = (box or {}).get("night")
        calls.append({"gotos_before": len(night.goto_calls) if night else None,
                      "a": a, "kw": kw})
        return await real(self, *a, **kw)

    monkeypatch.setattr(Hub, "rotator_self_test", counted)
    return calls


def _spy_deferral_kinds(monkeypatch) -> list[str]:
    """Every ``PanelDeferred`` the night raises, by kind."""
    kinds: list[str] = []
    real = group_rules.PanelDeferred.__init__

    def init(self, reason, *, kind, last_error=""):
        kinds.append(kind)
        real(self, reason, kind=kind, last_error=last_error)

    monkeypatch.setattr(group_rules.PanelDeferred, "__init__", init)
    return kinds


def _hub_refuses_rotation(night: Night, hub) -> None:
    """What the REAL hub does with a rotation request once the self-test has
    failed: ``rotate_to_pa`` raises, ``goto_and_center`` degrades it to
    ``rotation_skipped`` (test_w5_rotator_trust.py's
    test_goto_and_center_degrades_a_failed_self_test_too). The harness's goto
    never turns the rotator, so this is where the refusal lives."""
    def goto(who, n, result):
        asked = night.goto_calls[-1]["rotation_deg"]
        if asked is not None and hub._rotation_trusted is False:
            return {**result, "rotation": None, "rotation_skipped": True}
        return result

    night.goto_script = goto


async def _run(hub, monkeypatch, plan, *, sky=lambda who, n: PA,
               refuse: bool = True, **kw) -> Night:
    night = Night(hub, monkeypatch, sky=sky, **kw)
    if refuse:
        _hub_refuses_rotation(night, hub)
    try:
        night.done = await night.run(plan)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _rotation_asked(night: Night) -> list[float | None]:
    return [c["rotation_deg"] for c in night.goto_calls]


def _off_lines(night: Night) -> list[str]:
    return night.said(OFF_LINE)


def _report_entries(night: Night) -> list[dict]:
    return [e for e in night.engine.reporter.build().safety_events
            if e["action"] == "rotation_off"]


# ------------------------------------------------- (a) once, before the hop

async def test_the_first_rotating_hop_runs_the_self_test_once_for_the_night(
        hub, monkeypatch):
    """A rotating 2x2 with a healthy coupling: the engine asks the hub's
    self-test ONCE, before the first panel's rotating goto, however many
    panels and passes follow. It first put the mount on the panel's field
    without rotating (one attempt, no angle), so the self-test solves a
    field, and the hop's own goto, which does ask for the group's angle,
    comes after it.

    RED under mutant "self-test call removed" (the
    ``await self._ensure_rotator_self_test(target)`` line in `_setup_target`
    deleted), observed:

        AssertionError: the engine never asked the self-test: []
    """
    box: dict = {}
    calls = _count_self_tests(monkeypatch, box=box)
    night = Night(hub, monkeypatch, sky=lambda who, n: PA)
    box["night"] = night
    try:
        night.done = await night.run(grid_plan(group_kw=ROTATING))
    finally:
        await night.close()
    assert night.done, night.trace[-3:]
    assert len(calls) >= 1, f"the engine never asked the self-test: {calls}"
    assert len(calls) == 1, (
        f"the self-test must run once for the night, not once per hop: "
        f"{len(calls)} calls over {len(night.gotos)} gotos")
    assert hub._rotation_trusted is True
    # Before the first rotating goto, after the goto that put the mount on
    # the field: two gotos exist by the time the hop asks for its angle.
    assert calls[0]["gotos_before"] == 1, calls
    first, hop = night.goto_calls[0], night.goto_calls[1]
    assert first["who"] == _name("1-1") and first["rotation_deg"] is None, first
    assert first["kw"].get("max_attempts") == 1, first
    assert hop["who"] == _name("1-1") and hop["rotation_deg"] == PA, hop
    assert len(night.shots()) == 24, night.shots()


async def test_a_healthy_coupling_rotates_as_before(hub, monkeypatch):
    """The pass path (d): the self-test passes, the group's angle is
    requested on every hop that follows, and nothing says rotation is off.

    RED under mutant "the fallback fires on a pass" (`_rotation_off_tonight`
    reading ``is not None`` where it reads ``is False``), observed:

        AssertionError: a trusted rotator is asked for the group's angle on
        every hop: [None, None, None, None, None, None, None, None, None,
        None, None, None, None]
    """
    night = await _run(hub, monkeypatch, grid_plan(group_kw=ROTATING))
    assert night.done, night.trace[-3:]
    assert hub._rotation_trusted is True
    asked = _rotation_asked(night)
    assert asked[0] is None and all(a == PA for a in asked[1:]), (
        f"a trusted rotator is asked for the group's angle on every hop: "
        f"{asked}")
    assert not _off_lines(night), night.lines
    assert not _report_entries(night)
    assert len(night.shots()) == 24


async def test_a_measured_trust_is_not_measured_again(hub, monkeypatch):
    """Trust already known (WP-88's goto measured it on first use, or the
    operator ran the test): the engine does not spend four exposures and a
    20 degree move on it again, whichever way it came out.

    RED under mutant "trust is not asked" (the ``_rotation_trusted is not
    None`` return deleted from `_ensure_rotator_self_test`), observed:

        AssertionError: trust was already True; the engine asked again: 1
    """
    for known in (True, False):
        calls = _count_self_tests(monkeypatch)
        hub._rotation_trusted = known
        night = await _run(hub, monkeypatch,
                           grid_plan(group_kw=ROTATING))
        assert night.done, night.trace[-3:]
        assert not calls, (f"trust was already {known}; the engine asked "
                           f"again: {len(calls)}")
        if known is False:
            assert all(a is None for a in _rotation_asked(night)), \
                _rotation_asked(night)
        hub._rotation_trusted = None
        calls.clear()


async def test_a_trust_reset_mid_night_is_not_measured_a_second_time(
        hub, monkeypatch):
    """At most once per OBSERVING NIGHT, not once per time the hub's trust
    reads None: a reconnect mid-night (``Hub._teardown`` clears it) must not
    cost a second self-test, which is what D-05's "once per night" means.
    The wrapper clears the hub's verdict right after the first answer, as a
    reconnect would; a night that asked again would count two.

    RED under mutant "the night is not stamped" (``self._rotator_tested_night
    = night`` deleted from `_ensure_rotator_self_test`), observed:

        AssertionError: the second hop of the same night asked again: 2
        assert 2 == 1
    """
    calls = _count_self_tests(monkeypatch)
    real = Hub.rotator_self_test

    async def then_forgotten(self, *a, **kw):
        out = await real(self, *a, **kw)
        self._rotation_trusted = None
        return out

    monkeypatch.setattr(Hub, "rotator_self_test", then_forgotten)
    night = await _run(hub, monkeypatch, grid_plan(group_kw=ROTATING))
    assert night.done, night.trace[-3:]
    assert len(calls) == 1, (
        f"the second hop of the same night asked again: {len(calls)}")


async def test_a_new_night_asks_again_and_a_spent_night_does_not(
        hub, monkeypatch):
    """The night is the engine's own ``night_key``: the same key answers
    nothing more (the stamp, and the attempts a night has spent), a new key
    asks afresh with its own two attempts.

    RED under mutant "the attempts never reset" (the tries kept whatever the
    night), observed:

        AssertionError: a new night gets its own two attempts:
        ['2026-09-01', '2026-09-01']
        assert ['2026-09-01', '2026-09-01'] == ['2026-09-01',
        '2026-09-01', '2026-09-02', '2026-09-02']
    """
    night = Night(hub, monkeypatch, sky=lambda who, n: PA)
    eng = night.engine
    plan = grid_plan(group_kw=ROTATING)
    eng.plan = plan
    eng._groups = {g.id: g for g in plan.groups}
    target = plan.targets[0]
    key = {"night": "2026-09-01"}
    monkeypatch.setattr(engine_mod, "night_key", lambda ts=None: key["night"])
    calls: list[str] = []

    async def cannot_run(self, *a, **kw):
        calls.append(key["night"])
        raise DeviceError("rotator self-test: plate solve failed")

    monkeypatch.setattr(Hub, "rotator_self_test", cannot_run)
    try:
        for _ in range(5):
            await eng._ensure_rotator_self_test(target)
        assert calls == ["2026-09-01"] * 2, (
            f"a night gets two attempts and no more: {calls}")
        key["night"] = "2026-09-02"
        for _ in range(5):
            await eng._ensure_rotator_self_test(target)
        assert calls == ["2026-09-01"] * 2 + ["2026-09-02"] * 2, (
            f"a new night gets its own two attempts: {calls}")
    finally:
        await night.close()


def test_an_engine_with_no_hub_is_not_off():
    """The angle methods are graded on bare engines built with ``__new__``
    (test_goto_rotation.py, test_mosaic_spec_claims.py), which have no hub:
    asking whether rotation is off must answer no, not raise.

    RED under mutant "bare engine breaks" (``hub = getattr(self, "hub",
    None)`` made ``self.hub``), observed:

        AttributeError: 'SequenceEngine' object has no attribute 'hub'
    """
    eng = engine_mod.SequenceEngine.__new__(engine_mod.SequenceEngine)
    assert eng._rotation_off_tonight() is False


# ---------------------------------------------- (b) a slipping coupling

async def test_a_failed_self_test_shoots_the_panels_at_a_fixed_angle(
        hub, monkeypatch):
    """A slipping coupling: the self-test FAILS, rotation is off, and the
    group is judged as a fixed camera. No goto asks for an angle, so the
    hub's refusal is never provoked; every panel measured inside the
    tolerance (31 against a layout of 30, 6 deg) is SHOT, nothing is deferred
    for rotation, and the night log says once that rotation is off, in words
    that carry the limit: a grid laid at an angle is shot only where the
    camera already sits within its tolerance, else the group is set aside
    once. The report holds exactly one entry for it.

    RED under mutant "the fixed-angle fallback removed"
    (`_rotation_off_tonight` returning False, so `_setup_target` and
    `_group_angle_check` ignore the trust), observed: the panels asked for
    their angle on every hop and the hub refused each (``rotation_skipped``;
    the measured 31 is inside the tolerance, so `_group_hop_checks` waives
    the deferral here and the spiral shows in the beyond-tolerance case
    below):

        AssertionError: no angle is requested once rotation is off: [None,
        30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0,
        30.0]
    WAVE 14 INTEGRATION (this note replaces WP-90's "setup filter removed"
    mutant): the angle is withheld in `_commanded_rotation`, the one choke
    point every acquisition and every re-centre reads. The ``if rotation is
    not None and self._rotation_off_tonight():`` filter WP-90 first put in
    `_setup_target` became dead code there and was removed: the mutant that
    made it ``if False:`` survived all 67 tests of this file, the preflight
    file and the two rotation-trust files, which proved it equivalent. The
    mutant that turns this case red now is "the choke point does not honour
    rotation off" (the ``if planned is not None and
    self._rotation_off_tonight():`` block of `_commanded_rotation` removed),
    which shows the same line, observed:

        AssertionError: no angle is requested once rotation is off: [None,
        30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0,
        30.0]

    test_w14_recentre_rotation_off.py grades the same choke point on the
    three recovery re-centres.
    """
    _slip(hub)
    calls = _count_self_tests(monkeypatch)
    kinds = _spy_deferral_kinds(monkeypatch)
    night = await _run(hub, monkeypatch, grid_plan(group_kw=ROTATING),
                       sky=lambda who, n: PA + 1.0)
    assert night.done, night.trace[-3:]
    assert len(calls) == 1, calls
    assert hub._rotation_trusted is False
    asked = _rotation_asked(night)
    assert all(a is None for a in asked), (
        f"no angle is requested once rotation is off: {asked}")
    assert len(night.shots()) == 24, (
        f"every panel is shot at the fixed angle: {night.shots()}")
    assert "rotation" not in kinds and not kinds, (
        f"a failed self-test must not defer panels for rotation: {kinds}")
    said = _off_lines(night)
    assert len(said) == 1, (
        f"said {len(said)} times, wanted exactly once and not per panel; "
        f"the first: {said[:1]}")
    line = said[0]
    assert "fixed angle" in line, line
    assert "within" in line and "tolerance" in line, line
    assert "set aside" in line and "turn the camera" in line, line
    entries = _report_entries(night)
    assert len(entries) == 1, (
        f"the report holds {len(entries)} rotation_off entries, wanted "
        f"exactly one: {entries}")
    assert entries[0]["reason"] == line, (
        f"the report says something other than the night log: "
        f"{entries[0]['reason']!r}")


async def test_the_report_and_the_log_say_it_once_per_run(
        hub, monkeypatch):
    """The say-once is per run, tied to its report: a second run on the same
    engine (a resume, a restart in the same process) has a report of its own
    and says it into that one, once. Driven on the helper directly, since a
    second `Night.run` would re-seed everything else.

    RED under mutant "said every time" (the ``_rotation_off_said_in`` guard
    removed from `_say_rotation_off`), observed here and, in the whole
    night, in the case above ("said 12 times"):

        AssertionError: one run's report holds 3 rotation_off entries,
        wanted exactly one
        assert 3 == 1
    Under "no report entry" (the ``self._record_safety(msg, "rotation_off")``
    call made ``pass``) the same line reads "holds 0 rotation_off entries",
    and the whole-night case above reads "the report holds 0 rotation_off
    entries, wanted exactly one: []".
    """
    night = Night(hub, monkeypatch, sky=lambda who, n: PA)
    eng = night.engine
    hub._rotation_trusted = False
    from astrodeck.sequence.report import SessionReporter
    plan = grid_plan(group_kw=ROTATING)
    try:
        eng.reporter = SessionReporter(plan, report_id="w14-a",
                                       started_at=1.0)
        for _ in range(3):
            eng._say_rotation_off()
        mine = [e for e in eng.reporter.build().safety_events
                if e["action"] == "rotation_off"]
        assert len(mine) == 1, (
            f"one run's report holds {len(mine)} rotation_off entries, "
            f"wanted exactly one")
        await eng.reporter.flush()
        eng.reporter = SessionReporter(plan, report_id="w14-b",
                                       started_at=2.0)
        for _ in range(3):
            eng._say_rotation_off()
        again = [e for e in eng.reporter.build().safety_events
                 if e["action"] == "rotation_off"]
        assert len(again) == 1, (
            f"a new run's report holds {len(again)} rotation_off entries, "
            f"wanted exactly one")
    finally:
        await eng.reporter.flush()
        await night.close()


# ------------------------------------------- (c) beyond the tolerance

async def test_a_failed_self_test_beyond_tolerance_sets_the_group_aside_once(
        hub, monkeypatch):
    """The semantic limit D-05's fixed angle has: the camera sits 15 degrees
    off the layout (45 against 30, 6 deg tolerance) and nothing can turn it,
    so the FIRST hop sets the whole group aside, once, with the
    turn-the-camera wording. Not a deferral per panel, not a rotation
    deferral, no frame.

    RED under mutant "rotate left unmodified in the angle check"
    (`_group_angle_check`'s ``rotate=rotates`` made ``rotate=group.rotate``,
    so it judges a rotator that is off as a rotator), observed: the verdict
    ``off`` defers the panel ("the rotator did not bring the camera to the
    mosaic's angle") pass after pass instead of setting the group aside:

        AssertionError: a fixed camera beyond tolerance defers nothing:
        ['angle', 'angle', 'angle', 'angle', 'angle', 'angle', 'angle',
        'angle', 'angle', 'angle', 'angle', 'angle']
    Under "the fixed-angle fallback removed" (`_rotation_off_tonight`
    returning False) the same case shows the rotation spiral this WP exists
    to end, the hub's refusal turned into a deferral at every hop:

        AssertionError: a fixed camera beyond tolerance defers nothing:
        ['rotation', 'rotation', 'rotation', 'rotation', 'rotation',
        'rotation', 'rotation', 'rotation', 'rotation', 'rotation',
        'rotation', 'rotation']
    """
    _slip(hub)
    kinds = _spy_deferral_kinds(monkeypatch)
    night = await _run(hub, monkeypatch, grid_plan(group_kw=ROTATING),
                       sky=lambda who, n: 45.0)
    assert night.done, night.trace[-3:]
    assert night.shots() == [], night.shots()
    assert not kinds, f"a fixed camera beyond tolerance defers nothing: {kinds}"
    asked = _rotation_asked(night)
    assert all(a is None for a in asked), asked
    assert len(night.gotos) == 2, (
        f"the self-test's goto and ONE hop, then the group is set aside: "
        f"{night.gotos}")
    alerts = [m for _t, lvl, m in night.lines
              if lvl == "warning" and "set aside for tonight" in m]
    assert len(alerts) == 1, night.lines
    assert "turn the camera or re-frame at the measured angle" in alerts[0], \
        alerts[0]
    stored = night.stored.set_aside
    assert sorted(r["target_id"] for r in stored) == ["p00", "p01", "p10",
                                                      "p11"], stored
    assert len(_off_lines(night)) == 1


# ------------------------------------------------- a self-test that could not run

async def test_a_self_test_that_cannot_run_is_tried_twice_and_the_night_goes_on(
        hub, monkeypatch):
    """A solve that could not run (a DeviceError) says nothing about the
    coupling: trust stays unknown, the attempt is spent with one warning, and
    the night carries on exactly as it did before D-05, asking for the angle
    (the per-move follow check is the backstop). It is asked once more at the
    next hop, and then no more that night.

    RED under mutant "the attempt cap removed" (``tries >=
    ROTATOR_SELF_TEST_ATTEMPTS`` made ``tries >= 99``), observed:

        AssertionError: two attempts a night, then no more: 12
    """
    calls: list[int] = []

    async def cannot_run(self, *a, **kw):
        calls.append(len(calls) + 1)
        raise DeviceError("rotator self-test: plate solve failed")

    monkeypatch.setattr(Hub, "rotator_self_test", cannot_run)
    night = await _run(hub, monkeypatch, grid_plan(group_kw=ROTATING))
    assert night.done, night.trace[-3:]
    assert len(calls) == 2, f"two attempts a night, then no more: {len(calls)}"
    assert hub._rotation_trusted is None
    warned = [m for _t, lvl, m in night.lines
              if lvl == "warning" and "rotator self-test" in m]
    assert len(warned) == 2, night.lines
    assert "not asked again" not in warned[0] and "not asked again" in warned[1], (
        f"only the last attempt says it will not be asked again: {warned}")
    assert not _off_lines(night)
    asked = _rotation_asked(night)
    assert PA in asked and len(night.shots()) == 24, (asked, night.shots())


async def test_a_hub_with_no_self_test_is_left_alone(hub, monkeypatch):
    """A hub double that has no ``rotator_self_test`` has nothing to ask:
    the engine neither raises, nor spends an attempt, nor warns, and the
    night is the one it always was.

    RED under mutant "the self-test not looked for" (the ``self_test is
    None`` return deleted), observed:

        AssertionError: nothing to ask, so nothing to say: ['M31: the rotator
        self-test could not run ('NoneType' object is not callable); ...']
    """
    monkeypatch.delattr(Hub, "rotator_self_test")
    night = await _run(hub, monkeypatch, grid_plan(group_kw=ROTATING))
    assert night.done, night.trace[-3:]
    said = [m for _t, _lvl, m in night.lines if "rotator self-test" in m]
    assert not said, f"nothing to ask, so nothing to say: {said}"
    assert len(night.gotos) == 12, night.gotos


async def test_the_targets_own_centring_settings_do_not_collide(
        hub, monkeypatch):
    """The panel's own ``center_attempts`` rides `_centring_kwargs` into
    every centring, and the self-test's goto pins ``max_attempts`` to one;
    passing both is a TypeError, which the helper would take for a self-test
    that could not run. The tolerance still rides along.

    RED under mutant "max_attempts passed twice" (``**{**self.
    _centring_kwargs(target), "max_attempts": 1}`` made
    ``**self._centring_kwargs(target), max_attempts=1``), observed:

        AssertionError: the self-test never ran: []
        assert 0 == 1
        (the call raised a TypeError, taken for a self-test that could
        not run)
    """
    calls = _count_self_tests(monkeypatch)
    plan = grid_plan(group_kw=ROTATING)
    for t in plan.targets:
        t.center_attempts = 5
        t.center_tolerance_arcmin = 2.0
    night = await _run(hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert len(calls) == 1, f"the self-test never ran: {calls}"
    first, hop = night.goto_calls[0], night.goto_calls[1]
    assert first["kw"] == {"max_attempts": 1,
                           "tolerance_deg": pytest.approx(2.0 / 60.0)}, first
    assert hop["kw"].get("max_attempts") == 5, hop


async def test_a_self_test_that_hangs_ends_the_run_and_is_not_swallowed(
        hub, monkeypatch):
    """The self-test is a device await like every other, so it is bounded,
    and a bound that fires is a ``SafetyAbort`` through the normal abort and
    park path, never a warning the night shrugs off: nothing may sit on a
    wedged rotator unbounded, and nothing may carry on past a timeout as if
    the coupling had been measured.

    RED under mutant "the safety abort swallowed" (the ``except SafetyAbort:
    raise`` removed in `_ensure_rotator_self_test`, so the broad handler
    below it turns the timeout into one more warning), observed:

        AssertionError: a hung self-test must end the run, not be carried on
        from: 'complete'
    """
    async def hangs(self, *a, **kw):
        await asyncio.Event().wait()

    monkeypatch.setattr(Hub, "rotator_self_test", hangs)
    monkeypatch.setattr(engine_mod, "ROTATOR_SELF_TEST_TIMEOUT_S", 0.05)
    night = await _run(hub, monkeypatch, grid_plan(group_kw=ROTATING))
    assert night.done, night.trace[-3:]
    state = night.engine.state.get("state")
    assert state in ("aborted", "error"), (
        f"a hung self-test must end the run, not be carried on from: "
        f"{state!r}")
    assert night.shots() == [], night.shots()
    assert any("rotator self-test timed out" in m
               for _t, _lvl, m in night.lines), night.lines


# --------------------------------------------- (e) when it must not run

async def test_no_rotator_no_self_test(hub, monkeypatch):
    """A rig with no rotator (or one not connected) skips everything: the
    engine does not call the hub's self-test, which would only raise.

    RED under mutant "the rotator check removed" (the ``not
    self._rotator_connected()`` condition deleted), observed:

        AssertionError: no rotator, so nothing to test: 2
        (the next case, a rotator present but not connected, fails the
        same way under the same mutant: "assert not [...]", 2 calls)
    """
    hub.devices.pop("rotator")
    calls = _count_self_tests(monkeypatch)
    night = await _run(hub, monkeypatch, grid_plan(group_kw=ROTATING),
                       refuse=False)
    assert night.done, night.trace[-3:]
    assert not calls, f"no rotator, so nothing to test: {len(calls)}"
    assert len(night.gotos) == 12, night.gotos


async def test_a_disconnected_rotator_is_no_rotator(hub, monkeypatch):
    """Present in the rig but not connected: the same."""
    hub.devices["rotator"].connected = False
    calls = _count_self_tests(monkeypatch)
    night = await _run(hub, monkeypatch, grid_plan(group_kw=ROTATING),
                       refuse=False)
    assert night.done, night.trace[-3:]
    assert not calls, len(calls)


async def test_a_fixed_group_never_runs_the_self_test(hub, monkeypatch):
    """A group whose camera is fixed (``rotate`` off) is not a rotating
    mosaic, even when its panels carry a planned angle (a classic plan can
    send one): the group's own flag decides, so nothing is tested.

    RED under mutant "any group runs it" (the ``not member.rotate`` condition
    deleted), observed:

        AssertionError: a group that does not rotate has nothing to test: 1
        assert not [{'a': (), 'gotos_before': None, 'kw': {}}]
    """
    calls = _count_self_tests(monkeypatch)
    night = await _run(hub, monkeypatch,
                       grid_plan(group_kw=FIXED,
                                 panel_kw={"rotation_deg": PA}))
    assert night.done, night.trace[-3:]
    assert not calls, (f"a group that does not rotate has nothing to test: "
                       f"{len(calls)}")
    assert len(night.gotos) == 12, night.gotos


async def test_a_rotating_group_with_no_planned_angle_has_nothing_to_test(
        hub, monkeypatch):
    """The test is for an angle that will be COMMANDED. A group that rotates
    in name whose panels carry no ``rotation_deg`` commands nothing.

    RED under mutant "no angle needed" (the ``rotation_deg is None``
    condition deleted), observed:

        AssertionError: no angle will be commanded, so no coupling needs
        trusting: 1
    """
    calls = _count_self_tests(monkeypatch)
    night = await _run(hub, monkeypatch,
                       grid_plan(group_kw=ROTATING,
                                 panel_kw={"rotation_deg": None}))
    assert night.done, night.trace[-3:]
    assert not calls, (f"no angle will be commanded, so no coupling needs "
                       f"trusting: {len(calls)}")


async def test_an_uncentred_rotating_group_has_nothing_to_test(
        hub, monkeypatch):
    """Only a centred slew turns the rotator (#160), so a group whose panels
    are not centred never reaches a rotate that could slip, and the
    self-test's own centring on the panel's field would be the one centring
    the plan did not ask for.

    RED under mutant "centring not needed" (the ``center`` condition
    deleted), observed:

        AssertionError: panels that are never centred are never rotated: 1
    """
    calls = _count_self_tests(monkeypatch)
    plan = grid_plan(group_kw=ROTATING)
    for t in plan.targets:
        t.center = False
    night = await _run(hub, monkeypatch, plan, refuse=False)
    assert night.done, night.trace[-3:]
    assert not calls, (f"panels that are never centred are never rotated: "
                       f"{len(calls)}")


async def test_a_single_framed_target_is_not_a_mosaic(hub, monkeypatch):
    """A single TARGET with a planned angle is not a mosaic: no group, no
    self-test, and the call it makes is today's.

    RED under mutant "any target runs it" (the ``member is None`` return
    deleted), observed:

        assert [] == [45.0]
        (the run died on an AttributeError, 'NoneType' object has no
        attribute 'rotate', before its first goto)
    """
    calls = _count_self_tests(monkeypatch)
    plan = SequencePlan(
        name="solo", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False, park_when_done=False,
        warm_cooler_when_done=False, recover_guiding=False,
        targets=[single("Solo", count=2, rotation_deg=45.0)])
    night = await _run(hub, monkeypatch, plan, refuse=False)
    assert night.done, night.trace[-3:]
    assert not calls, len(calls)
    assert _rotation_asked(night) == [45.0], _rotation_asked(night)


async def test_no_mount_no_self_test(hub, monkeypatch):
    """The self-test puts the mount on the panel's field first, and a hop on
    a rig with no mount never slews or commands an angle (`_setup_target`'s
    whole acquisition sits behind the telescope), so there is nothing to
    test and no attempt to spend. Driven on the helper directly: the night
    harness needs the mount to exist.

    RED under mutant "the mount check removed" (the ``"telescope" not in
    self.hub.devices`` condition deleted), observed:

        AssertionError: no mount, so no attempt is spent: 1
    """
    night = Night(hub, monkeypatch, sky=lambda who, n: PA)
    eng = night.engine
    plan = grid_plan(group_kw=ROTATING)
    eng.plan = plan
    eng._groups = {g.id: g for g in plan.groups}
    hub.devices.pop("telescope")
    calls = _count_self_tests(monkeypatch)
    try:
        await eng._ensure_rotator_self_test(plan.targets[0])
        assert not calls, f"no mount, so no attempt is spent: {len(calls)}"
        assert eng._rotator_test_tries == ("", 0), eng._rotator_test_tries
    finally:
        await night.close()


async def test_the_guider_stands_down_before_the_self_test_moves_the_mount(
        hub, monkeypatch):
    """The self-test slews the mount ahead of the hop's own stand-down
    (#148: nothing may pulse a guide loop through a slew), so it stands the
    guider down itself, before its goto.

    RED under mutant "no stand-down" (the ``_stand_down_guider`` call
    deleted from `_ensure_rotator_self_test`), observed:

        AssertionError: the guider must stop before the self-test's goto:
        trace kinds [('state',), ('log',), ('log',), ('state',), ('log',),
        ('log',), ('goto',)]
    """
    night = Night(hub, monkeypatch, sky=lambda who, n: PA)
    plan = grid_plan(group_kw=ROTATING, guide=True)
    try:
        night.done = await night.run(plan)
    finally:
        await night.close()
    assert night.done, night.trace[-3:]
    kinds = [(e[1], e[2]) if e[1] == "guider" else (e[1],)
             for e in night.trace]
    first_goto = next((i for i, e in enumerate(night.trace)
                       if e[1] == "goto" and e[3] is None), None)
    assert first_goto is not None, (
        f"premise: a goto with no angle (the self-test's) opens the night: "
        f"{kinds[:8]}")
    assert ("guider", "stop") in kinds[:first_goto], (
        f"the guider must stop before the self-test's goto: "
        f"trace kinds {kinds[:first_goto + 1]}")


async def test_the_last_spells_stop_retry_ends_before_the_self_test_moves_the_mount(
        hub, monkeypatch):
    """A stop retry left running from the last spell can land a
    ``set_tracking(False)`` after the self-test's goto turned tracking on,
    and the self-test would solve on a mount that is not tracking. The hop
    ends it before ITS slew; this runs ahead of the hop's, so it ends it
    itself, and waits until it has. Driven on the helper directly, with a
    stand-in for the retry's task.

    RED under mutant "the stop retry left running" (the
    ``await self._cancel_idle_stop_retry()`` line deleted from
    `_ensure_rotator_self_test`), observed:

        AssertionError: the stop retry was still running when the self-test
        moved the mount
    """
    night = Night(hub, monkeypatch, sky=lambda who, n: PA)
    eng = night.engine
    plan = grid_plan(group_kw=ROTATING)
    eng.plan = plan
    eng._groups = {g.id: g for g in plan.groups}
    retry = asyncio.create_task(asyncio.sleep(3600))
    eng._idle_stop_task = retry
    seen: dict = {}
    real = Hub.rotator_self_test

    async def watch(self, *a, **kw):
        seen["retry_done"] = retry.done()
        return await real(self, *a, **kw)

    monkeypatch.setattr(Hub, "rotator_self_test", watch)
    try:
        await eng._ensure_rotator_self_test(plan.targets[0])
        assert seen.get("retry_done") is True, (
            "the stop retry was still running when the self-test moved the "
            "mount")
    finally:
        retry.cancel()
        await asyncio.gather(retry, return_exceptions=True)
        await night.close()


# ------------------------------------- the moment of the line can be the site's

ASKED_LINE = "testing that the camera follows the rotator"


def _viewer_reads(night) -> list[str]:
    """Every log line a viewer's ring read serves from this night
    (`api.redact._redact_log_rows_for`, what ``/api/logs`` serves)."""
    rows = [e for e in night.events if e["type"] == "log"]
    return [(r["data"] or {}).get("message", "")
            for r in _redact_log_rows_for(rows, principal_for_role("viewer"))]


async def test_the_self_test_line_is_site_derived_when_the_hop_ends_a_meridian_wait(
        hub, monkeypatch):
    """The first hop of a night can be the hop that ENDS a mosaic's meridian
    wait (test_group_meridian.py's panel 8 minutes before transit, on a mount
    that does not flip early, waits for its crossing and is acquired at it).
    The engine's line saying the self-test is about to run is said at the
    start of that hop, at the same moment as the flagged "target N/M" line, so
    unflagged its ``ts`` is the transit of a known RA: the longitude, to any
    viewer who reads ``/api/logs`` (spec 6.9, #166, the #19 class). It is
    flagged ``site_derived`` while `_site_timed` says the run's publishes are,
    and a viewer is not served it.

    RED under mutant "the flag removed" (the ``site_derived=self.
    _site_timed()`` of that `bus.log` dropped), observed:

        AssertionError: said but not flagged site_derived: ['M31: testing
        that the camera follows the rotator before the first rotated panel
        (D-05; attempt 1 of 2)']
    """
    def setup(night):
        tel = hub.devices["telescope"]
        night.engine._flips_early_by_mount[tel.name] = False

    plan = meridian_plan(cols=1, ha_h=-8 / 60.0, ha_step_h=0.0, count=3,
                         group_kw=ROTATING)
    night = await _meridian_night(hub, monkeypatch, plan, before_run=setup)
    assert night.done, night.trace[-3:]
    assert night.said("1-1 waits for the meridian"), (
        f"premise: the first hop ends a meridian wait: {night.lines[:6]}")
    said = night.said(ASKED_LINE)
    assert len(said) == 1, f"premise: the self-test was asked once: {said}"
    flagged = [m for _t, _lvl, m in night.flagged if ASKED_LINE in m]
    assert flagged == said, f"said but not flagged site_derived: {said}"
    assert not [m for m in _viewer_reads(night) if ASKED_LINE in m], (
        "a viewer is served the line that is timed by the crossing")


async def test_the_self_test_line_is_an_ordinary_line_when_no_site_timed_it(
        hub, monkeypatch):
    """The control for the case above: a night with no meridian wait says the
    same line unflagged, as it would have before the flag existed, and a
    viewer reads it.

    RED under mutant "flagged always" (the ``site_derived=self._site_timed()``
    made ``site_derived=True``), observed:

        AssertionError: nothing site-derived timed this line, so it is not
        flagged: ['M31: testing that the camera follows the rotator before
        the first rotated panel (D-05; attempt 1 of 2)']
    """
    night = await _run(hub, monkeypatch, grid_plan(group_kw=ROTATING))
    assert night.done, night.trace[-3:]
    said = night.said(ASKED_LINE)
    assert len(said) == 1, said
    flagged = [m for _t, _lvl, m in night.flagged if ASKED_LINE in m]
    assert not flagged, (
        f"nothing site-derived timed this line, so it is not flagged: "
        f"{flagged}")
    assert [m for m in _viewer_reads(night) if ASKED_LINE in m] == said


# ------------------------------------------------ the bound on the preflight

def test_the_rotation_allowance_covers_the_first_hops_preflight():
    """RULING 4: ``ROTATION_ALLOWANCE_S`` covers what the first rotating hop
    of a night asks of its goto's bound beyond a goto that does not turn the
    rotator. The self-test has just left the rotator 20 degrees from where
    the rotate shortcut would have found it, so that hop runs the whole
    rotate loop: four solves, and about 22 degrees of net travel (the step
    undone and a residual) plus the approach's overshoot and return, 5
    degrees each way. The solve is the self-test's own 3 s exposure and
    ASTAP's 60 s cap (solve/astap.py, a literal there, so it is repeated
    here), plus 20 s for the wheel borrow, the download and the frame write;
    travel is priced at the device layer's own bound, a full revolution
    inside ``Rotator.MOVE_TIMEOUT_S``. The engine's comment beside the
    constant carries the same arithmetic.

    RED under mutant "allowance left at 300" (``ROTATION_ALLOWANCE_S =
    300.0``, the number it had), observed:

        AssertionError: 300.0 s does not cover the first hop's preflight:
        four solves of 83 s and 32 deg of travel need 348 s
        assert 300.0 >= 348.0
    """
    solve_s = 3.0 + 60.0 + 20.0
    travel_deg = ((ROTATOR_SELF_TEST_STEP_DEG + 2.0)
                  + 2 * ROTATOR_BACKLASH_DEG)
    need = 4 * solve_s + travel_deg * (Rotator.MOVE_TIMEOUT_S / 360.0)
    assert engine_mod.ROTATION_ALLOWANCE_S >= need, (
        f"{engine_mod.ROTATION_ALLOWANCE_S:g} s does not cover the first "
        f"hop's preflight: four solves of {solve_s:g} s and {travel_deg:g} "
        f"deg of travel need {need:g} s")


def test_the_self_test_bound_covers_its_own_two_solves_and_its_step():
    """The self-test's own bound is two solves and the 20 degree step, priced
    the same way: a bound smaller than the work it wraps is a guillotine
    (the bound that fires is a safety abort).

    RED under mutant "bound too small" (``ROTATOR_SELF_TEST_TIMEOUT_S =
    60.0``), observed:

        AssertionError: 60 s cannot cover two 83 s solves and a 20 deg step
        assert 60.0 >= 176.0
    """
    solve_s = 3.0 + 60.0 + 20.0
    need = 2 * solve_s + ROTATOR_SELF_TEST_STEP_DEG * (
        Rotator.MOVE_TIMEOUT_S / 360.0)
    assert engine_mod.ROTATOR_SELF_TEST_TIMEOUT_S >= need, (
        f"{engine_mod.ROTATOR_SELF_TEST_TIMEOUT_S:g} s cannot cover two "
        f"{solve_s:g} s solves and a {ROTATOR_SELF_TEST_STEP_DEG:g} deg step")
