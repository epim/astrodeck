# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""ResumeArm hands the target it re-centred to the engine's idle clock (#202,
the resume half).

The recovery ladder ends in ``goto_and_center`` on the session's first real
target, which leaves the mount TRACKING it, and ``tick`` then calls
``engine.start``. The engine's idle park-hold watches only what it acquired
itself (``_tracked_target``, reset by every start), on the premise that before
the first ``_setup_target`` the mount is wherever the operator left it. After
an auto-resume that is false: our own code pointed it seconds earlier. So a
resumed run that opened on short scheduler waits had nothing watching the
mount's idle clock, floor or flip point until some target was set up. T2 gave
``engine.start`` a ``tracking=`` keyword that counts as an acquisition made
just now; this is ResumeArm passing it.

WHICH OBJECT. The one ``tick`` starts from is the session RE-READ under the
store lock after the ladder (#211), so ``tracking`` is taken from that copy by
the re-centred target's id: the target the engine is handed belongs to the plan
it runs. When that copy no longer has the target where the ladder pointed the
mount (its plan was replaced during the ladder, which PATCH allows for a
dormant session), ``tracking`` is the target as re-centred, because the idle
clock's floor and flip checks must read where the mount IS.

THE HARNESS. A real ``tick`` and a real ``_recover`` on a recording hub and a
recording engine: no safety monitor, no focuser (so step 1 has nothing to
read), a plate solver that succeeds, and the sky pinned open.

Each mutant was applied to a byte-for-byte backup of ``resume_arm.py`` and
the file was restored byte-identical (SHA-256 compared) afterwards. The
failures are quoted as observed.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.resume_arm import ResumeArm
from astrodeck.sequence.session import Session, session_store


class _Engine:
    """The engine surface ``tick`` and the ladder use, recording every
    ``engine.start`` call's keywords."""

    def __init__(self) -> None:
        self.running = False
        self.starts: list[dict] = []

    def start(self, plan, **kw) -> None:
        self.starts.append(kw)

    async def check_slew_limits(self, target, *, cfg=None, plan=None,
                                projected=True) -> None:
        return None


class _Connected:
    connected = True


class _Hub:
    """Records the ladder's re-centring slews. No focuser and no safety
    monitor, so the ladder goes straight to the solve and the slew."""
    site: dict = {}
    dusk_arm = None

    def __init__(self) -> None:
        self.devices = {"camera": _Connected(), "telescope": _Connected()}
        self.gotos: list[tuple[float, float]] = []
        self.on_goto = None

    def require(self, role):
        if role == "focuser":
            raise RuntimeError("no focuser on this rig")
        return object()

    async def solve_and_sync(self, exposure_s: float = 3.0, *,
                             blind: bool = False):
        return {"ok": True}

    async def goto_and_center(self, ra, dec, *a, **k) -> None:
        self.gotos.append((ra, dec))
        if self.on_goto is not None:
            self.on_goto()


@pytest.fixture
def ladder(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    engine, hub = _Engine(), _Hub()
    arm = ResumeArm(engine, hub)
    monkeypatch.setattr(arm, "_window_open", lambda s, t: True)
    monkeypatch.setattr(arm, "_can_solve", lambda: True)
    return arm, engine, hub


def _darks() -> Target:
    return Target(name="darks", ra_hours=0.0, dec_deg=0.0, calibration=True,
                  steps=[ExposureStep(frame_type="Dark", exposure_s=1.0,
                                      count=2)])


def _light(name: str, ra: float, dec: float) -> Target:
    return Target(name=name, ra_hours=ra, dec_deg=dec,
                  steps=[ExposureStep(filter="L", exposure_s=1.0, count=2)])


def _armed(name: str, targets: list[Target]) -> Session:
    s = Session(name=name, status="dormant", auto_resume=True,
                plan=SequencePlan(name=name, targets=targets))
    session_store.save(s)
    return s


def _in_plan(target, session: Session) -> bool:
    return any(target is t for t in session.plan.targets)


async def test_the_resumed_run_tracks_the_target_the_ladder_recentred(ladder):
    """A calibration target first, then two lights. The ladder re-centres the
    first light (M42), and ``engine.start`` receives it as ``tracking``: the
    same id, and the very object in the plan of the session it starts, which
    is the copy re-read after the ladder.

    RED under mutant "tracking omitted" (``tracking=`` dropped from the
    ``engine.start`` call in ``tick``), and the same under mutant "never
    recorded" (``self._recentred = tgt`` removed from ``_recover``), observed
    verbatim (``--tb=short``):

        _________ test_the_resumed_run_tracks_the_target_the_ladder_recentred _________
        E   AssertionError: the re-centred target was not handed to the idle clock
        E   assert None is not None

    RED under mutant "tracking from the copy read before the ladder"
    (``_tracking_for`` returns ``self._recentred`` without looking in the
    re-read session), observed verbatim:

        _________ test_the_resumed_run_tracks_the_target_the_ladder_recentred _________
        E   AssertionError: tracking is not a target of the session the engine was handed
        E   assert False
    """
    arm, engine, hub = ladder
    m42 = _light("M42", 5.5881, -5.3911)
    s = _armed("lights", [_darks(), m42, _light("M31", 0.7123, 41.2689)])

    await arm.tick()

    assert hub.gotos == [(m42.ra_hours, m42.dec_deg)], "premise: M42 re-centred"
    assert len(engine.starts) == 1, "premise: the resume started"
    kw = engine.starts[0]
    assert kw["session"].id == s.id
    tracking = kw.get("tracking")
    assert tracking is not None, (
        "the re-centred target was not handed to the idle clock")
    assert tracking.id == m42.id
    assert _in_plan(tracking, kw["session"]), (
        "tracking is not a target of the session the engine was handed")


async def test_control_a_calibration_only_resume_tracks_nothing(ladder):
    """CONTROL: a calibration-only session never slews, so there is no
    target to hand over and ``tracking`` is None. Run on the SAME service
    right after a light session's resume, so what the earlier ladder
    re-centred cannot leak into a later start.

    RED under mutant "the re-centred target is not cleared before the
    ladder" (the reset in ``tick`` removed), observed verbatim:

        ____________ test_control_a_calibration_only_resume_tracks_nothing ____________
        E   AssertionError: a calibration-only resume was handed a target it never slewed to: 'M42' is None
        E   assert Target(id='5b03dff0e91440109e1a3b3bcf2cfa41', name='M42', ra_hours=5.5881, dec_deg=-5.3911, center=True, autofocus_fir...
    """
    arm, engine, hub = ladder
    m42 = _light("M42", 5.5881, -5.3911)
    lights = _armed("lights", [m42])
    await arm.tick()
    assert [kw["tracking"].id for kw in engine.starts] == [m42.id], (
        "premise: the light session resumed tracking M42")
    done = session_store.load(lights.id)
    done.status = "complete"                  # its run ended, all banked
    session_store.save(done)
    cal = _armed("darks", [_darks()])

    await arm.tick()

    assert hub.gotos == [(m42.ra_hours, m42.dec_deg)], (
        "premise: the calibration-only session never slewed")
    assert len(engine.starts) == 2 and engine.starts[1]["session"].id == cal.id
    tracking = engine.starts[1].get("tracking")
    assert tracking is None, (
        "a calibration-only resume was handed a target it never slewed to: "
        f"{getattr(tracking, 'name', tracking)!r} is None")


@pytest.mark.parametrize("edit", ["moved", "removed"])
async def test_a_plan_replaced_during_the_ladder_tracks_where_the_mount_points(
        ladder, edit):
    """The session's plan is replaced while the ladder slews, as PATCH may do
    to a dormant session. ``moved``: M42 keeps its id at other coordinates.
    ``removed``: the plan no longer has it. Either way the mount is tracking
    the coordinates the ladder slewed to, and those are what the idle clock
    must watch, so ``tracking`` is the target as re-centred.

    RED under mutant "by id only" (``_tracking_for`` returns the re-read
    copy's target with that id, or None), both cases, observed verbatim:

        [moved]
        E   AssertionError: the idle clock would watch a place the mount is not
        E   assert (6.5881, -5.3911) == (5.5881, -5.3911)
        E     At index 0 diff: 6.5881 != 5.5881
        [removed]
        E   AssertionError: the re-centred target was not handed to the idle clock
        E   assert None is not None
    """
    arm, engine, hub = ladder
    m42 = _light("M42", 5.5881, -5.3911)
    m31 = _light("M31", 0.7123, 41.2689)
    s = _armed("lights", [m42, m31])

    def replace_the_plan() -> None:
        stored = session_store.load(s.id)
        if edit == "moved":
            moved = stored.plan.targets[0].model_copy(
                update={"ra_hours": m42.ra_hours + 1.0})
            targets = [moved, stored.plan.targets[1]]
        else:
            targets = [stored.plan.targets[1]]
        stored.plan = stored.plan.model_copy(update={"targets": targets})
        session_store.save(stored)

    hub.on_goto = replace_the_plan

    await arm.tick()

    assert hub.gotos == [(m42.ra_hours, m42.dec_deg)], "premise: M42 re-centred"
    assert len(engine.starts) == 1, "premise: the resume started"
    tracking = engine.starts[0].get("tracking")
    assert tracking is not None, (
        "the re-centred target was not handed to the idle clock")
    assert tracking.id == m42.id
    assert (tracking.ra_hours, tracking.dec_deg) == (
        m42.ra_hours, m42.dec_deg), (
        "the idle clock would watch a place the mount is not")

