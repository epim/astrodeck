# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-14: ResumeArm messages, hold, rotation, dead branch (backlog plan
2026-09-30, D-nn: none needed -- this WP's fix shapes were owner-approved
directly, not through a numbered ruling).

(a) #139 -- the "auto-resume is NOT armed" note names the stalled session by
    id and origin as well as by name, so a reader can tell it apart from a
    same-named session the store holds under another id.
(b) #261 -- starting the recovery ladder stops REPORTING the previous
    attempt's hold: ``ResumeArm.hold`` reads None while ``recovering`` is
    true, so a screen cannot say "holding: <reason> ... starts when that
    clears" about a wait that a new attempt has already superseded. The
    backing field is untouched, so a repeat of the same refusal still keeps
    its first ``since`` once the ladder returns (``test_resume_arm.py::
    test_a_floor_hold_keeps_its_since_while_the_altitude_changes`` is the
    existing pin on that, and it is not touched here).
(c) #295 -- ``commanded_rotation`` commands a locked angle only to a
    CONNECTED rotator, as ``SequenceEngine._commanded_rotation`` already
    does, so a fixed-camera rig stops seeing a false "rotation ... asked for
    but no rotator is in the rig" warning on every auto-resume of an
    unframed target.
(d) #543 -- ``ResumeArm._walk``'s ``except (KeyError, TypeError,
    ValueError)`` fallback is unreachable since H4-SCHED (#527) moved
    ``schedule.resolve_window`` onto ``site_gate.site_lat_lon``, which
    answers None instead of raising. Removed in resume_arm.py; this file
    adds no test for the removal itself (deleting dead code changes no
    behaviour), and the existing suite (in particular
    ``test_resume_arm_picks_what_runs.py::
    test_targets_come_in_the_order_the_run_walks_them``, whose second part
    exercised exactly this fallback) is the regression check -- it is listed
    in ``blocked_on``/run separately, since this file may not edit it.
"""
from __future__ import annotations

import asyncio

from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.resume_arm import ResumeArm, commanded_rotation
from astrodeck.sequence.session import Session, session_store

from _simhub import sim_hub  # noqa: F401 (fixture import, THE HARNESS below)

# THE HARNESS. ``_simhub.sim_hub``: the real ``Hub`` on the simulator rig, an
# isolated config and captures dir, a real site (not the real rig's -- a
# made-up 40 N 74 W, per this worktree's rule), and a connected ``SimRotator``
# in the "rotator" role (asserted by the fixture itself). Reused rather than
# hand-rolled so these cases see the same rig test_resume_arm_picks_what_runs.py
# and test_resume_arm.py grade their own cases against.


def _plan(target: Target) -> SequencePlan:
    return SequencePlan(name="w2", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        targets=[target])


def _target(name: str = "w2-target", ra: float = 1.572,
           dec: float = 30.7853) -> Target:
    """An unfloored, unframed light target: no ``min_altitude_deg`` and no
    ``rotation_deg``, so none of the floor, limit or planned-angle branches
    these cases are not about ever fires."""
    return Target(name=name, ra_hours=ra, dec_deg=dec, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])


# --------------------------------------------------------------- (a) #139


async def test_the_not_armed_note_names_the_session_by_id_and_origin(
        sim_hub, bus_lines):
    """One dormant, unarmed session (so ``session_store.armed()`` is None and
    the tick reaches the "nobody will restart it" note), from a flow. The
    line must let a reader find the exact file it means -- the name alone is
    not a key: the store can (and on 2026-09-23 did, #139) hold many dormant
    unarmed sessions sharing one display name.

    RED under mutant "drop id and origin from the stalled note" (revert to
    ``f"auto-resume is NOT armed: '{newest.name}' is dormant with ..."``),
    observed verbatim:

        E   AssertionError: the note does not name the session's id: auto-resume is NOT armed: 'NGC 7331 - LRGB+SHO with SN 2026aaiv' is dormant with auto-resume off, so nothing will restart it. Arm it from the session list to resume tonight.
        E   assert '9f5eda60f2a94e2c8b6e1a7c3d0f1234' in "auto-resume is NOT armed: 'NGC 7331 - LRGB+SHO with SN 2026aaiv' is dormant with auto-resume off, so nothing will restart it. Arm it from the session list to resume tonight."
    """
    stale = Session(name="NGC 7331 - LRGB+SHO with SN 2026aaiv",
                    status="dormant", auto_resume=False, origin="flow",
                    origin_id="flow-xyz", plan=_plan(_target()))
    session_store.save(stale)

    engine = SequenceEngine(sim_hub)
    arm = ResumeArm(engine, sim_hub, clock=lambda: 1_700_000_000.0)
    await arm.tick()

    notes = [m for lv, m, _s in bus_lines if "is NOT armed" in m]
    assert len(notes) == 1, bus_lines
    note = notes[0]
    assert stale.id in note, (
        f"the note does not name the session's id: {note}")
    assert "flow" in note, f"the note does not name its origin: {note}"
    assert stale.name in note, note


async def test_the_not_armed_note_says_so_when_the_origin_is_unknown(
        sim_hub, bus_lines):
    """A session that predates ``origin`` (the field is "" for one, never
    backfilled) still gets a note that says it does not know, rather than an
    empty parenthetical -- the id is read from the field whatever it holds,
    not hardcoded."""
    stale = Session(name="old plan", status="dormant", auto_resume=False,
                    origin="", plan=_plan(_target()))
    session_store.save(stale)

    engine = SequenceEngine(sim_hub)
    arm = ResumeArm(engine, sim_hub, clock=lambda: 1_700_000_000.0)
    await arm.tick()

    notes = [m for lv, m, _s in bus_lines if "is NOT armed" in m]
    assert len(notes) == 1, bus_lines
    assert stale.id in notes[0], notes[0]
    assert "unknown" in notes[0], notes[0]


async def test_resume_note_ignores_a_superseded_dormant_session(
        sim_hub, bus_lines, monkeypatch):
    """The 2026-09-23 incident, reproduced (#139's CORE fix, which (a) above
    did not build -- it only made the note tell sessions apart, not stop
    naming the wrong one). S_old: dormant, auto-resume off. S_new: COMPLETE
    (not dormant -- the newer session need not itself be a resume candidate
    to supersede the old one), same ``origin_id`` and name. A session with a
    DIFFERENT origin_id (``other``, also dormant and unarmed, no newer
    sibling) is the control: the note still owes the operator word of it.

    ``session_store.save`` stamps ``updated_ts = time.time()`` on every save
    (session.py), overwriting whatever the constructor was given, so the
    ORDER the three are saved in, under a monkeypatched clock, is what fixes
    "newest" -- not the constructor arguments. Saved other, old, new: by
    real save order other < old < new, so without the fix `max(...,
    key=updated_ts)` would already pick `old` over `other` on save order
    alone (masking the bug this case exists to catch); WITH it old drops out
    as superseded by new and `other` -- the control -- is reported, same as
    today.

    RED under mutant "drop the supersession filter" (`_unsuperseded_stalled`
    returns ``stalled`` unfiltered, the code before this fix), observed:

        AssertionError: arming S_old would re-shoot a plan S_new already
        finished: auto-resume is NOT armed: 'NGC 7331 - LRGB+SHO with SN
        2026aaiv' (<S_old id>, flow) is dormant with auto-resume off, so
        nothing will restart it. Arm it from the session list to resume
        tonight.
    """
    import astrodeck.sequence.session as session_mod

    clock = {"t": 500.0}
    monkeypatch.setattr(session_mod.time, "time", lambda: clock["t"])

    name = "NGC 7331 - LRGB+SHO with SN 2026aaiv"
    other = Session(name="unrelated plan", status="dormant",
                    auto_resume=False, origin="flow", origin_id="flow-xyz",
                    plan=_plan(_target("other-target")))
    session_store.save(other)
    clock["t"] = 1_000.0
    old = Session(name=name, status="dormant", auto_resume=False,
                 origin="flow", origin_id="flow-abc",
                 plan=_plan(_target("old-target")))
    session_store.save(old)
    clock["t"] = 2_000.0
    new = Session(name=name, status="complete", auto_resume=True,
                 origin="flow", origin_id="flow-abc",
                 plan=_plan(_target("new-target")))
    session_store.save(new)
    # No unpatch needed: nothing below saves again (`tick` only reads), and
    # `monkeypatch` reverts this fixture-wide at teardown like every other
    # patch here (including `bus_lines`' own, which an `undo()` here would
    # also have reverted mid-test).

    engine = SequenceEngine(sim_hub)
    arm = ResumeArm(engine, sim_hub, clock=lambda: 1_700_000_000.0)
    await arm.tick()

    notes = [m for lv, m, _s in bus_lines if "is NOT armed" in m]
    assert old.id not in "".join(notes), (
        f"arming S_old would re-shoot a plan S_new already finished: "
        f"{notes}")
    assert len(notes) == 1 and other.id in notes[0], (
        f"the control (a dormant session with no newer sibling) still "
        f"needs its note: {notes}")


# --------------------------------------------------------------- (b) #261


async def test_starting_the_ladder_stops_reporting_the_previous_hold(
        sim_hub, monkeypatch):
    """Tick 1: the window is closed, which holds with no ladder at all (the
    simplest real refusal to seed a stale hold with). Tick 2: the window is
    open and the ladder starts, but is paused mid-flight (a monkeypatched
    ``_recover`` that blocks on an ``asyncio.Event``) so the hold can be read
    WHILE ``recovering`` is true, not after ``tick`` has already cleared or
    replaced it on the ladder's own outcome -- which is the one place the
    bug lived and the one place a post-``await arm.tick()`` assertion could
    never see it.

    RED under mutant "the ladder keeps reporting the old hold" (the ``hold``
    property returns ``self._hold`` unconditionally, without the
    ``recovering`` check), observed verbatim:

        E   AssertionError: the previous attempt's hold outlived the refusal it reported, beside recovering=True: {'reason': 'it is not dark enough yet, and this session still owes 1 frame', 'since': 1700000000.0, 'retry_at': None, 'session_id': '8b2bdfd6c275420aa54a9c3320677ff3', 'session_name': 'w2-hold', 'owed': 1}
        E   assert {'owed': 1, 'reason': 'it is not dark enough yet, and this session still owes 1 frame', ...} is None
    """
    session_store.save(Session(name="w2-hold", status="dormant",
                               auto_resume=True, plan=_plan(_target())))
    now = {"t": 1_700_000_000.0}
    engine = SequenceEngine(sim_hub)
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])

    # Tick 1: window closed -> a hold, no ladder (`recovering` never rises).
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: False)
    await arm.tick()
    assert arm.hold is not None, "premise: tick 1 left a hold"
    assert not arm.recovering, "premise: tick 1 ran no ladder"

    # Tick 2: window open, and the ladder is held open on an event so the
    # state mid-ladder can be read, exactly as #261's own suggested test
    # describes ("a tick whose ladder is held open on an event").
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    started = asyncio.Event()
    release = asyncio.Event()

    async def paused_recover(self, session):
        started.set()
        await release.wait()
        return None  # the attempt succeeds once released

    monkeypatch.setattr(ResumeArm, "_recover", paused_recover)
    task = asyncio.ensure_future(arm.tick())
    try:
        await asyncio.wait_for(started.wait(), timeout=5.0)

        assert arm.recovering, "premise: the ladder is running"
        assert arm.hold is None, (
            f"the previous attempt's hold outlived the refusal it reported, "
            f"beside recovering=True: {arm.hold}")
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=5.0)

    # And the backing state was not thrown away for it: a session dormant and
    # armed after a start is not this test's concern, but the hold must not
    # have been corrupted into something `_set_hold`'s `since`-preserving
    # compare can no longer read (dict or None, never left half-written).
    assert arm.hold is None or isinstance(arm.hold, dict)


async def test_a_repeated_refusal_through_the_ladder_still_keeps_its_since(
        sim_hub, monkeypatch):
    """Companion premise to the test above, stated directly rather than left
    to be taken on faith: two ticks whose ladder BOTH refuse with the same
    words keep the FIRST refusal's ``since`` once each ladder has returned.
    This is what rules out "clear ``_hold`` when the ladder starts" as the
    fix -- that shape passes the test above too, but restamps ``since`` on
    every retry of an identical hold, which
    ``test_resume_arm.py::test_a_floor_hold_keeps_its_since_while_the_altitude_changes``
    already pins against a real floor refusal. Here the same invariant is
    checked directly against ``_set_hold``/``hold`` without needing a real
    sky, so a regression shows up without the floor-altitude machinery.
    """
    session_store.save(Session(name="w2-since", status="dormant",
                               auto_resume=True, plan=_plan(_target())))
    now = {"t": 1_700_000_000.0}
    engine = SequenceEngine(sim_hub)
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)

    async def refuse(self, session):
        return "a made-up refusal for this test"

    monkeypatch.setattr(ResumeArm, "_recover", refuse)

    await arm.tick()
    first = dict(arm.hold or {})
    assert first.get("reason") == "a made-up refusal for this test", first

    now["t"] += 700.0  # past RETRY_INTERVAL_S
    await arm.tick()
    second = dict(arm.hold or {})

    assert second.get("reason") == first.get("reason"), (first, second)
    assert second.get("since") == first.get("since"), (
        f"a repeated refusal through the ladder restamped since: "
        f"{first} -> {second}")


# --------------------------------------------------------------- (c) #295


async def test_commanded_rotation_checks_a_connected_rotator(sim_hub):
    """A locked angle is commanded to a connected rotator (the fixture's
    default) and withheld once it is disconnected, mirroring
    ``SequenceEngine._commanded_rotation``'s own check. A planned angle
    (``rotation_deg``) is unaffected either way -- it is commanded whatever
    the rig, as the docstring says.

    RED under mutant "lock commanded to an absent rotator" (the ``hub is not
    None`` branch removed from ``commanded_rotation``), observed verbatim:

        E   AssertionError: a lock must not be commanded with no rotator connected
        E   assert 42.0 is None
    """
    target = _target()
    s = Session(name="w2-lock-direct", plan=_plan(target))
    s.lock_angle(target.id, 42.0, solved_at=1_700_000_000.0 - 86400.0,
                exposed_at=None, source="first centring solve")

    assert sim_hub.devices["rotator"].connected, "premise: fixture default"
    assert commanded_rotation(s, target, sim_hub) == 42.0

    sim_hub.devices["rotator"].connected = False
    assert commanded_rotation(s, target, sim_hub) is None, (
        "a lock must not be commanded with no rotator connected")

    # A planned angle is commanded whatever the rig (unchanged by #295).
    planned = Target(name="w2-planned", ra_hours=1.572, dec_deg=30.7853,
                     center=False, autofocus_first=False, rotation_deg=99.0,
                     steps=[ExposureStep(filter="L", exposure_s=0.05,
                                         count=1)])
    assert commanded_rotation(s, planned, sim_hub) == 99.0


async def test_recover_commands_no_rotation_with_no_rotator_connected(
        sim_hub, monkeypatch):
    """End to end through ``_recover``'s own re-centre call (#295's own
    suggested test shape): a locked, unframed target's re-centre, on a rig
    whose rotator is disconnected, passes ``goto_and_center`` no
    ``rotation_deg`` at all -- the same "nothing new" call a rig with no
    rotator and no angle always got -- instead of the hub logging a spurious
    "rotation ... asked for but no rotator is in the rig" warning about an
    angle the operator never set.

    RED under the same mutant, "lock commanded to an absent rotator",
    observed verbatim:

        E   AssertionError: [((1.572, 30.7853), {'rotation_deg': 42.0})] != [((1.572, 30.7853), {})]
    """
    from astrodeck.devices import fingerprint as _fp
    monkeypatch.setattr(_fp, "verdict",
                        lambda **kw: _fp.Verdict(focus_trusted=True))

    gotos: list[tuple[tuple, dict]] = []

    async def goto(*args, **kwargs):
        gotos.append((args, kwargs))
        return {"centered": True, "error_arcmin": 0.2, "attempts": 1,
                "rotation": None}

    async def solve(*args, **kwargs):
        return {}

    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    monkeypatch.setattr(sim_hub, "solve_and_sync", solve)

    engine = SequenceEngine(sim_hub)

    async def limits_pass(target, *, cfg=None, plan=None, projected=True):
        return None

    monkeypatch.setattr(engine, "check_slew_limits", limits_pass)

    sim_hub.devices["rotator"].connected = False

    target = _target(name="w2-no-rotator")
    s = Session(name="w2-no-rotator", plan=_plan(target))
    s.lock_angle(target.id, 42.0, solved_at=1_700_000_000.0 - 86400.0,
                exposed_at=None, source="first centring solve")

    arm = ResumeArm(engine, sim_hub, clock=lambda: 1_700_000_000.0)
    refusal = await arm._recover(s)

    assert refusal is None, refusal
    assert gotos == [((target.ra_hours, target.dec_deg), {})], gotos
