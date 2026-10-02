# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""ResumeArm says "targets share a name" ONCE per resume (#156, spec 3.5;
mosaic S1 carry-over 5a).

A repeated target name that no rule names is not refused - the classic Plan
appends the same object twice routinely - so every start path lets it through
and logs ``duplicate_name_warning`` instead. ResumeArm logs it too, and WHERE it
logs it is the whole point of this file. ``tick`` runs every 60 s, and between
the identity refusal and ``engine.start`` it has early returns that come back on
the next tick with no backoff: ``_devices_ready`` while the boot is still
connecting the camera and mount, and the dusk-preparation hold while the camera
cools. A warning placed ahead of those returns repeats once a minute for as long
as they last, the same line every tick until the run starts. So
``resume_arm.py`` logs it after ``_recover`` and right before ``engine.start``,
and its comment there says why. Nothing held it to that comment until now: the
reachability test in ``test_plan_identity.py`` runs one tick with every early
return disabled, so the warning could move anywhere above ``engine.start`` and
stay green there.

The night here is the one that comment describes: an armed dormant session,
three ticks with the camera and mount not yet connected, one tick under a dusk
hold, then the tick that starts the run. ``_devices_ready`` is the REAL method,
reading a hub whose devices are not yet ``connected``, wrapped only to record
what it answered. ``_recover`` is replaced by a spy (it is where the mount
moves), and ``_window_open`` is pinned open (it reads the sky). A second night
covers the early return after the ladder itself: the spy refuses once, the
ten-minute backoff runs, and the retry starts the run.

Each test that guards a branch names the mutants it kills and quotes the
failure each produced. Every mutant was run from a byte-for-byte backup of the
file it changes (``resume_arm.py``, and ``models.py`` for the control's) in a
copy of ``server/``, so no other suite saw it, and the file was restored
byte-identical (SHA-256 compared) afterwards.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import astrodeck.hub as hub_module
from astrodeck.sequence.models import (ExposureStep, SequencePlan, Target,
                                       duplicate_name_warning,
                                       plan_identity_errors)
from astrodeck.sequence.resume_arm import (CHECK_INTERVAL_S, RETRY_INTERVAL_S,
                                           ResumeArm)
from astrodeck.sequence.session import Session, session_store

#: Ticks spent waiting for the boot to connect the camera and mount. Three, the
#: acceptance's floor: enough that "once" and "once per tick" cannot coincide.
NOT_READY_TICKS = 3

DUSK_REASON = "Dusk preparation: cooling the camera to -10 C"

LADDER_REFUSAL = "the sky could not be verified"

#: Ticks inside the ladder's ten-minute backoff, between its refusal and the
#: retry that starts the run.
BACKOFF_TICKS = 2

WARNING_PREFIX = "targets share a name"


# ------------------------------------------------------------------ builders

def _target(name: str, *, ra: float = 5.5, dec: float = -5.0) -> Target:
    """A light target with fresh ids, so only its NAME can repeat."""
    return Target(name=name, ra_hours=ra, dec_deg=dec, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=1.0, count=2)])


def _plan(*targets: Target) -> SequencePlan:
    """No instructions at all, so no rule can name a repeated target and turn
    the warning into a refusal."""
    return SequencePlan(name="once", guide=False, dither_every=0,
                        meridian_flip=False, targets=list(targets))


class _Device:
    """What ``_devices_ready`` reads of a device: ``connected``."""

    def __init__(self) -> None:
        self.connected = False


class _Dusk:
    """Dusk preparation, reduced to the one call ``tick`` makes. ``reason`` is
    the hold it reports (None = no hold); ``calls`` counts the ticks that got
    far enough to ask."""

    def __init__(self) -> None:
        self.reason: str | None = None
        self.calls = 0

    def resume_veto(self) -> str | None:
        self.calls += 1
        return self.reason


class _Hub:
    site: dict = {}

    def __init__(self) -> None:
        self.devices = {"camera": _Device(), "telescope": _Device()}
        self.dusk_arm = _Dusk()

    def connect_devices(self) -> None:
        for dev in self.devices.values():
            dev.connected = True

    def require(self, role):
        return object()


class _Engine:
    """What ``tick`` reads of the engine: ``running``, and ``start`` itself."""
    running = False

    def __init__(self) -> None:
        self.starts: list[SequencePlan] = []

    def start(self, plan, **kw) -> None:
        self.starts.append(plan)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A real ``ResumeArm.tick`` on a rig whose boot has not finished."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    recovers: list[str] = []
    readies: list[bool] = []
    #: What the ladder answers, one entry per call; empty = it lets the run go.
    refusals: list[str] = []

    async def spy_recover(self, session):
        recovers.append(session.id)
        return refusals.pop(0) if refusals else None

    real_ready = ResumeArm._devices_ready

    def spy_ready(self) -> bool:
        ok = real_ready(self)
        readies.append(ok)
        return ok

    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    monkeypatch.setattr(ResumeArm, "_recover", spy_recover)
    monkeypatch.setattr(ResumeArm, "_devices_ready", spy_ready)
    engine, hub = _Engine(), _Hub()
    now = {"t": 1_700_000_000.0}
    return SimpleNamespace(arm=ResumeArm(engine, hub, clock=lambda: now["t"]),
                           engine=engine, hub=hub, now=now,
                           recovers=recovers, readies=readies,
                           refusals=refusals)


def _armed(plan: SequencePlan) -> Session:
    s = Session(name="armed", status="dormant", plan=plan, auto_resume=True)
    session_store.save(s)
    return s


def _ticker(rig, bus_lines, per_tick: list[list[str]]):
    """One real ``tick``, recording the name-warning lines it logged, then the
    clock moves on to the next 60 s tick."""
    async def tick() -> None:
        before = len(bus_lines)
        await rig.arm.tick()
        per_tick.append([m for lvl, m, _src in bus_lines[before:]
                         if lvl == "warning" and m.startswith(WARNING_PREFIX)])
        rig.now["t"] += CHECK_INTERVAL_S
    return tick


async def _resume_night(rig, bus_lines, session: Session) -> list[list[str]]:
    """Drive the night and return the name-warning lines logged on each tick,
    one list entry per tick.

    Every stage asserts the branch it meant to reach BEFORE the caller counts
    anything, so a count of one cannot come from ticks that never got as far
    as the early return they are supposed to exercise.
    """
    per_tick: list[list[str]] = []
    tick = _ticker(rig, bus_lines, per_tick)

    # 1. The boot is still connecting the camera and mount.
    for _ in range(NOT_READY_TICKS):
        await tick()
    assert rig.readies == [False] * NOT_READY_TICKS, (
        "the not-ready ticks never reached _devices_ready")
    assert rig.hub.dusk_arm.calls == NOT_READY_TICKS
    assert rig.recovers == [] and rig.engine.starts == []
    assert rig.arm._retry_at == 0.0, "booting armed the ten-minute backoff"

    # 2. Devices up, but dusk preparation is still cooling the camera.
    rig.hub.connect_devices()
    rig.hub.dusk_arm.reason = DUSK_REASON
    await tick()
    assert rig.arm.hold is not None and rig.arm.hold["reason"] == DUSK_REASON, (
        "the dusk tick did not stop at the dusk hold")
    assert len(rig.readies) == NOT_READY_TICKS, (
        "the dusk hold returned after _devices_ready, not before it")
    assert rig.recovers == [] and rig.engine.starts == []

    # 3. The hold lifts and the run starts.
    rig.hub.dusk_arm.reason = None
    await tick()
    assert rig.readies[-1] is True
    assert rig.recovers == [session.id]
    assert len(rig.engine.starts) == 1, "the resume never reached engine.start"
    assert rig.arm.hold is None
    return per_tick


async def _held_night(rig, bus_lines, session: Session) -> list[list[str]]:
    """The other early return between the identity gate and ``engine.start``:
    the recovery ladder refuses, the ten-minute backoff runs, and the retry
    starts the run. Devices are up and dusk is clear from the first tick.

    Staged like ``_resume_night``: each stage asserts the branch it reached
    before anything is counted.
    """
    per_tick: list[list[str]] = []
    tick = _ticker(rig, bus_lines, per_tick)
    rig.hub.connect_devices()
    rig.refusals.append(LADDER_REFUSAL)

    # 1. The ladder refuses and arms the backoff.
    refused_at = rig.now["t"]
    await tick()
    assert rig.recovers == [session.id], "the refusal tick never reached _recover"
    assert rig.engine.starts == []
    assert rig.arm.hold is not None and rig.arm.hold["reason"] == LADDER_REFUSAL
    assert rig.arm._retry_at == refused_at + RETRY_INTERVAL_S

    # 2. Inside the backoff nothing is retried.
    for _ in range(BACKOFF_TICKS):
        await tick()
    assert rig.recovers == [session.id], "a backoff tick re-ran the ladder"
    assert rig.engine.starts == []

    # 3. The retry: the ladder lets it go and the run starts.
    rig.now["t"] = rig.arm._retry_at
    await tick()
    assert rig.recovers == [session.id, session.id]
    assert len(rig.engine.starts) == 1, "the retry never reached engine.start"
    assert rig.arm.hold is None
    return per_tick


async def test_the_name_warning_is_logged_once_per_resume(rig, bus_lines):
    """Five ticks, one resume, one line - on the tick that starts the run.

    RED under mutant "warning moved ahead of resume_veto" (the three-line
    ``name_warning`` block cut from above ``engine.start`` and pasted directly
    above ``veto = self.resume_veto()``). It logs on every tick that passes the
    identity and quota gates, which is all five: N+1 lines on the ticks that
    started nothing (N = NOT_READY_TICKS not-ready ticks plus the dusk hold)
    and one more on the tick that did, N+2 in all:

        E   AssertionError: the duplicate-name warning was logged 5 times
            across one resume, per tick [1, 1, 1, 1, 1]
        E   assert 5 == 1
        E    +  where 5 = sum([1, 1, 1, 1, 1])

    RED under mutant "warning moved below the dusk hold" (the same block
    pasted directly above ``if not self._devices_ready():``). The dusk tick
    is quiet and the not-ready ticks are not, so the not-ready ticks catch a
    misplacement on their own:

        E   AssertionError: the duplicate-name warning was logged 4 times
            across one resume, per tick [1, 1, 1, 0, 1]
        E   assert 4 == 1
        E    +  where 4 = sum([1, 1, 1, 0, 1])

    RED under mutant "drop the ResumeArm warning" (its ``bus.log`` made
    ``pass``), so "exactly once" is not satisfied by "never":

        E   AssertionError: the duplicate-name warning was logged 0 times
            across one resume, per tick [0, 0, 0, 0, 0]
        E   assert 0 == 1
        E    +  where 0 = sum([0, 0, 0, 0, 0])

    RED under mutant "latched ahead of resume_veto" (the block moved above
    ``veto = self.resume_veto()`` inside ``if getattr(self, '_named_for',
    None) != armed.id: self._named_for = armed.id``). Once, but on the first
    tick past the gates, which started nothing; the count passes and the
    second assertion is the one that fails:

        E   AssertionError: the one warning was not logged on the tick that
            started, per tick [1, 0, 0, 0, 0]
        E   assert [] == ['targets sha... one of them']
        E
        E     Right contains one more item: "targets share a name ('M42' x2);
              they run and count separately, but an instruction could not
              name just one of them"
    """
    plan = _plan(_target("M42"), _target("M42"))
    # The setup's own premise: the plan starts (nothing refuses it) and it has
    # a warning to give. Without both, "logged once" could be "logged by some
    # other path" or "refused before anything was logged".
    assert plan_identity_errors(plan) == []
    expected = duplicate_name_warning(plan)
    assert expected == ("targets share a name ('M42' x2); they run and count "
                        "separately, but an instruction could not name just "
                        "one of them")
    s = _armed(plan)

    per_tick = await _resume_night(rig, bus_lines, s)

    counts = [len(lines) for lines in per_tick]
    assert sum(counts) == 1, (
        f"the duplicate-name warning was logged {sum(counts)} times across one "
        f"resume, per tick {counts}")
    assert per_tick[-1] == [expected], (
        f"the one warning was not logged on the tick that started, per tick "
        f"{counts}")


async def test_a_held_ladder_does_not_repeat_the_name_warning(rig, bus_lines):
    """The ladder refuses once and the retry starts the run: one line, on the
    retry. The booting night above cannot see this placement, because its
    spy ladder never refuses, so a warning pasted directly above ``_recover``
    still logs once there, on the tick that started. Here the refusal tick
    reaches that point too.

    RED under mutant "warning moved above _recover" (the three-line block
    pasted directly above ``refusal = await self._recover(armed)``; the
    booting night stays green under it):

        E   AssertionError: the duplicate-name warning was logged 2 times
            across one resume, per tick [1, 0, 0, 1]
        E   assert 2 == 1
        E    +  where 2 = sum([1, 0, 0, 1])

    Mutant "warning moved ahead of resume_veto" turns this red too, with the
    same per-tick [1, 0, 0, 1].
    """
    plan = _plan(_target("M42"), _target("M42"))
    assert plan_identity_errors(plan) == []
    expected = duplicate_name_warning(plan)
    assert expected is not None
    s = _armed(plan)

    per_tick = await _held_night(rig, bus_lines, s)

    counts = [len(lines) for lines in per_tick]
    assert sum(counts) == 1, (
        f"the duplicate-name warning was logged {sum(counts)} times across one "
        f"resume, per tick {counts}")
    assert per_tick[-1] == [expected], (
        f"the one warning was not logged on the tick that started, per tick "
        f"{counts}")


async def test_control_held_ladder_distinct_names_log_no_warning(rig,
                                                                 bus_lines):
    """CONTROL for the held night: no repeated name, nothing about names on any
    tick, and the retry still starts (``_held_night`` asserts it). Green under
    every ``resume_arm.py`` mutant named in this file.

    RED under mutant "warn with no repeats" (``models.py``, as for the control
    below):

        E   AssertionError: [[], [], [], ['targets share a name (); they run
            and count separately, but an instruction could not name just one
            of them']]
        E   assert [[], [], [], ...one of them']] == [[], [], [], []]
        E
        E     At index 3 diff: ['targets share a name (); they run and count
              separately, but an instruction could not name just one of
              them'] != []
    """
    plan = _plan(_target("M42"), _target("M31", ra=0.7, dec=41.3))
    s = _armed(plan)

    per_tick = await _held_night(rig, bus_lines, s)

    assert per_tick == [[]] * (BACKOFF_TICKS + 2), per_tick


async def test_control_distinct_names_log_no_warning(rig, bus_lines):
    """CONTROL: the same night with no repeated name says nothing about names,
    and still starts (``_resume_night`` asserts ``engine.start``), so the
    silence is from a tick that reached the logging point, not one that
    returned early.

    Green under every ``resume_arm.py`` mutant named in this file, as a control
    should be: ``duplicate_name_warning`` returns None for this plan wherever
    it is called from.

    RED under mutant "warn with no repeats" (in ``models.py``, the ``if not
    repeats: return None`` in ``duplicate_name_warning`` deleted; the test
    above stays green). The timeline is asserted BEFORE the pure function's
    premise, so this is the tick-level assertion failing, not the premise:

        E   AssertionError: [[], [], [], [], ['targets share a name (); they
            run and count separately, but an instruction could not name just
            one of them']]
        E   assert [[], [], [], ...one of them']] == [[], [], [], [], []]
        E
        E     At index 4 diff: ['targets share a name (); they run and count
              separately, but an instruction could not name just one of
              them'] != []
    """
    plan = _plan(_target("M42"), _target("M31", ra=0.7, dec=41.3))
    assert plan_identity_errors(plan) == []
    s = _armed(plan)

    per_tick = await _resume_night(rig, bus_lines, s)

    assert per_tick == [[]] * (NOT_READY_TICKS + 2), per_tick
    # The premise, checked after the timeline so a warning from a clean plan
    # is caught where it would be logged rather than here.
    assert duplicate_name_warning(plan) is None
