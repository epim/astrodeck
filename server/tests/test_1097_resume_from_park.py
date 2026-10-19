# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Auto-resume slews from park without solving the park view (#1097).

2026-10-18/19: the engine's cloud park left an armed session dormant, and the
resume ladder's blind solve at the park position failed on every retry until
the window closed. The park view is the one view the run never shoots, and at
this site it is always obstructed. The owner's ruling (2026-10-19): "From a
home or park position the scope should always be able to slew to target
without being gated on plate solving home first. When in doubt or lost
otherwise when slewing from one target to another the scope should just slew
home then slew to target."

So, with the driver vouching for the position (``position_known``):

* PARKED: no in-place solve; step 3 slews to the target and solves there.
* NOT PARKED and the in-place solve failed under a sky that showed light:
  park, then slew to the target and solve there.
* A hold at the target in an attempt that left the park parks the mount
  again, so the next try starts from home.
* The no-light verdict (``_ladder_light``, ``_ladder_dark_kind``) that the
  in-place solve used to set comes from the solve at the target instead.

A mount whose position is NOT known (an AM5 that lost power and read its home
pole, #867, #1075) keeps today's behaviour exactly: the in-place solve, no
slew, today's hold words.

THE HARNESS is the real ``ResumeArm`` ladder, the real ``SequenceEngine`` (for
its fenced park) and the real ``Hub.goto_and_center`` on the sim rig. Only the
solve is scripted, by where the mount is in the story: before the goto it is
the park view (starless, lit), after it it is the target's view.

MUTANTS, each applied to a byte copy of ``resume_arm.py`` and each RED (the
test that went red is named):

- M1 "unconditional in-place solve": the parked skip removed ->
  test_a_parked_mount_slews_without_solving_the_park_view.
- M2 "the skip ignores position_known": ``self._mount_position_known()``
  dropped from the skip's condition -> test_an_unknown_position_still_solves_
  in_place_and_does_not_slew.
- M3 "no park leg in B": the ``_park_for_resume`` call in the generic arm
  removed -> test_a_failed_in_place_solve_under_light_parks_then_slews.
- M3b "lit-only gate": B parks only when the verdict is CLOUD ->
  test_any_failed_in_place_solve_parks_then_slews (both parameters).
- M4 "no re-park": ``_recentre_hold`` and ``_hold_unsolved_recentre`` no
  longer park -> test_a_failed_solve_at_the_target_leaves_the_mount_parked.
- M5 "no dark handling at step 3": ``_hold_unsolved_recentre`` no longer sets
  ``_ladder_light`` / ``_ladder_dark_kind`` -> test_no_light_at_the_target_
  is_the_dark_hold_with_its_backoff.
"""
from __future__ import annotations

import re

import numpy as np
import pytest

from _simhub import SUN_PROOF_TARGET, sim_hub  # noqa: F401 (fixture import)
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.resume_arm import (NO_LIGHT_RETRY_S, RETRY_INTERVAL_S,
                                           ResumeArm)
from astrodeck.sequence.session import Session, session_store
from astrodeck.solve import light

T0 = 1_700_000_000.0
RA, DEC = SUN_PROOF_TARGET


def _plan() -> SequencePlan:
    return SequencePlan(name="park", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[Target(
                            name="NEP", ra_hours=RA, dec_deg=DEC,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=3)])])


def _dark_error() -> light.NoLightError:
    rng = np.random.default_rng(251)
    frame = np.clip(np.rint(rng.normal(251.0, 9.0, (200, 300))), 0,
                    65535).astype(np.uint16)
    v = light.classify(frame, light.Reference(
        level=250.0, source="the dark master for these settings",
        kind=light.DARK_MASTER))
    assert v.kind == light.NO_LIGHT, v
    e = light.error_for(v, "Not enough stars.", "plate solve failed")
    return light.NoLightError(f"{e} [median {v.median:.0f} ADU]", v)


def _lit_error() -> light.FailedSolveError:
    """A starless frame that shows light: the park view under an
    obstruction or a lit cloud deck."""
    rng = np.random.default_rng(252)
    frame = np.clip(np.rint(rng.normal(290.0, 11.0, (200, 300))), 0,
                    65535).astype(np.uint16)
    v = light.classify(frame, light.Reference(level=250.0))
    assert v.kind == light.CLOUD, v
    return light.error_for(v, "Not enough stars.", "plate solve failed")


def _unknown_error() -> light.FailedSolveError:
    """A failed solve nothing could judge: no reference level to compare the
    frame with, as when no dark master exists for the temperature."""
    rng = np.random.default_rng(253)
    frame = np.clip(np.rint(rng.normal(251.0, 11.0, (200, 300))), 0,
                    65535).astype(np.uint16)
    v = light.classify(frame, None)
    assert v.kind == light.UNKNOWN, v
    return light.error_for(v, "Not enough stars.", "plate solve failed")


class _Rig:
    """``park_view`` is what a solve returns before the goto to the target,
    ``target_view`` what it returns after it: ``"solved"``, ``"lit"``,
    ``"dark"``, ``"generic"``, or ``"fail"`` for a plain RuntimeError."""

    def __init__(self, hub, monkeypatch, park_view, target_view):
        from astrodeck.devices import fingerprint as _fp
        self.hub = hub
        self.tel = hub.devices["telescope"]
        self.events: list[str] = []
        self.starts: list[dict] = []
        self.views = {"park": park_view, "target": target_view}
        self.went = False
        monkeypatch.setattr(_fp, "verdict",
                            lambda **kw: _fp.Verdict(focus_trusted=True))
        self.engine = SequenceEngine(hub)

        async def limits(target, *, cfg=None, plan=None, projected=True):
            return None
        monkeypatch.setattr(self.engine, "check_slew_limits", limits)
        monkeypatch.setattr(self.engine, "start",
                            lambda plan, **kw: self.starts.append(kw))

        real_goto = hub.goto_and_center

        async def goto(ra, dec, *a, **kw):
            self.events.append("goto")
            self.went = True
            return await real_goto(ra, dec, *a, **kw)
        monkeypatch.setattr(hub, "goto_and_center", goto)

        real_park = self.tel.park

        async def park():
            self.events.append("park")
            return await real_park()
        monkeypatch.setattr(self.tel, "park", park)

        async def solve(*a, **kw):
            where = "target" if self.went else "park"
            self.events.append(f"solve@{where}")
            view = self.views[where]
            if view == "solved":
                return {"ra_hours": RA, "dec_deg": DEC}
            if view == "lit":
                raise _lit_error()
            if view == "unknown":
                raise _unknown_error()
            if view == "dark":
                raise _dark_error()
            raise RuntimeError("the test's solver failed")
        monkeypatch.setattr(hub, "solve_and_sync", solve)

        self.now = {"t": T0}
        monkeypatch.setattr(ResumeArm, "_window_open", lambda arm, s, t: True)
        monkeypatch.setattr(ResumeArm, "_can_solve", lambda arm: True)
        self.arm = ResumeArm(self.engine, hub, clock=lambda: self.now["t"])

    async def tick_at(self, t: float) -> None:
        self.now["t"] = t
        await self.arm.tick()

    async def parked(self) -> bool:
        return await self.tel.is_parked()


def _arm() -> Session:
    s = Session(name="park", status="dormant", plan=_plan(), auto_resume=True)
    session_store.save(s)
    return s


def _alerts(lines) -> list[str]:
    return [m for lv, m, _s in lines if lv == "error" and "no light" in m]


async def test_a_parked_mount_slews_without_solving_the_park_view(
        sim_hub, monkeypatch, bus_lines):
    """Parked, position known, the park view is starless (a solve there would
    fail) and the target's view solves: the first tick resumes the run, no
    solve was taken at the park position, and the first solve came after the
    goto to the target.

    RED under M1 (the unconditional in-place solve restored).
    """
    s = _arm()
    rig = _Rig(sim_hub, monkeypatch, "lit", "solved")
    await rig.tel.park()
    rig.events.clear()
    assert await rig.parked(), "premise: the mount stands parked"
    assert rig.tel.position_known, "premise: the driver vouches for it"

    await rig.tick_at(T0)

    assert rig.events == ["goto", "solve@target"], (
        "a solve was taken at the park position", rig.events)
    assert len(rig.starts) == 1 and rig.starts[0]["session"].id == s.id, (
        rig.arm.hold)
    assert rig.arm.hold is None
    assert rig.arm._ladder_light == "lit"
    assert not await rig.parked(), "the goto unparked the mount"
    said = [m for lv, m, src in bus_lines if "#1097" in m]
    assert len(said) == 1 and "parked" in said[0], bus_lines
    assert re.search(r"\d", said[0].replace("#1097", "")) is None, said


async def test_an_unknown_position_still_solves_in_place_and_does_not_slew(
        sim_hub, monkeypatch, bus_lines):
    """Parked but the driver does not vouch for the position (an AM5 that
    read its home pole on a reconnect): today's behaviour exactly. The solve
    is taken in place, it fails, nothing slews and nothing parks, and the
    hold is today's words.

    RED under M2 (``position_known`` dropped from the skip).
    """
    _arm()
    rig = _Rig(sim_hub, monkeypatch, "fail", "solved")
    await rig.tel.park()
    rig.events.clear()
    rig.tel.position_known = False

    await rig.tick_at(T0)

    assert rig.events == ["solve@park"], rig.events
    assert not rig.starts
    reason = rig.arm.hold["reason"]
    assert reason.startswith("blind plate solve failed after restart"), reason
    assert reason.endswith(
        "— refusing to slew a mount whose true position is unknown"), reason
    assert rig.arm._retry_at == T0 + RETRY_INTERVAL_S
    assert await rig.parked()


async def test_a_failed_in_place_solve_under_light_parks_then_slews(
        sim_hub, monkeypatch, bus_lines):
    """Not parked, position known, the solve where the mount stands fails
    with a lit verdict: the mount parks, then slews to the target, solves
    there and the run resumes. The park comes before the goto.

    RED under M3 (the park leg removed).
    """
    s = _arm()
    rig = _Rig(sim_hub, monkeypatch, "lit", "solved")
    assert not await rig.parked(), "premise: the mount is not parked"

    await rig.tick_at(T0)

    assert rig.events == ["solve@park", "park", "goto", "solve@target"], (
        rig.events)
    assert len(rig.starts) == 1 and rig.starts[0]["session"].id == s.id, (
        rig.arm.hold)
    assert rig.arm._ladder_light == "lit"
    assert any("#1097" in m for _lv, m, _src in bus_lines), bus_lines


async def test_a_failed_solve_at_the_target_leaves_the_mount_parked(
        sim_hub, monkeypatch, bus_lines):
    """Parked, position known, the solve at the target fails lit: a hold
    with the usual ten-minute retry, in words, and the mount ends parked,
    not tracking at the target. (Before this, a step-3 hold left the mount
    wherever the centring stopped it, tracking, until the next retry.)

    RED under M4 (no re-park).
    """
    _arm()
    rig = _Rig(sim_hub, monkeypatch, "lit", "lit")
    await rig.tel.park()
    rig.events.clear()

    await rig.tick_at(T0)

    assert rig.events == ["goto", "solve@target", "park"], rig.events
    assert not rig.starts
    assert rig.arm._retry_at == T0 + RETRY_INTERVAL_S
    reason = rig.arm.hold["reason"]
    assert re.search(r"\d", reason) is None, reason
    assert "after restart" not in reason and "unknown" not in reason, reason
    assert rig.arm._ladder_light == "lit"
    assert await rig.parked(), "the mount was left off the park position"
    assert not await rig.tel.get_tracking()

    # The retry starts from home again: parked, so no solve at the park view.
    await rig.tick_at(T0 + RETRY_INTERVAL_S)
    assert rig.events[3:] == ["goto", "solve@target", "park"], rig.events


async def test_no_light_at_the_target_is_the_dark_hold_with_its_backoff(
        sim_hub, monkeypatch, bus_lines):
    """Parked, position known, the solve at the target raises
    ``NoLightError``: the "dark" hold of the old step-2 path. One alert,
    ``_ladder_light`` and ``_ladder_dark_kind`` set, the first retry after
    ten minutes and then the hourly one a dark master earns (#308), the hold
    in words with no number, and the mount parked.

    RED under M5 (the dark handling at step 3 removed).
    """
    _arm()
    rig = _Rig(sim_hub, monkeypatch, "lit", "dark")
    await rig.tel.park()
    rig.events.clear()

    await rig.tick_at(T0)

    assert rig.events == ["goto", "solve@target", "park"], rig.events
    assert rig.arm._ladder_light == "dark"
    assert rig.arm._ladder_dark_kind == light.DARK_MASTER
    assert rig.arm._retry_at == T0 + RETRY_INTERVAL_S
    reason = rig.arm.hold["reason"]
    assert light.NO_LIGHT_WORDS in reason, reason
    assert re.search(r"\d", reason) is None, reason
    assert len(_alerts(bus_lines)) == 1, bus_lines
    assert await rig.parked()

    t1 = T0 + RETRY_INTERVAL_S
    await rig.tick_at(t1)
    assert len(_alerts(bus_lines)) == 1, "a second alert in the same spell"
    assert rig.arm._retry_at == t1 + NO_LIGHT_RETRY_S, (
        rig.arm._retry_at - t1)


@pytest.mark.parametrize("failure", ["unknown", "fail"])
async def test_any_failed_in_place_solve_parks_then_slews(
        sim_hub, monkeypatch, bus_lines, failure):
    """Test 3b. Most failed solves on this rig carry an UNKNOWN light
    verdict (no dark master for the temperature), and a plain error carries
    none. Not parked, position known: either way the mount parks, then
    slews to the target and solves there, and the run resumes. The failure
    is no evidence of light, so it must not end a no-light spell.

    RED under M3b (the lit-only gate restored), both parameters.
    """
    s = _arm()
    rig = _Rig(sim_hub, monkeypatch, failure, "solved")
    assert not await rig.parked(), "premise: the mount is not parked"

    await rig.tick_at(T0)

    assert rig.events == ["solve@park", "park", "goto", "solve@target"], (
        rig.events)
    assert len(rig.starts) == 1 and rig.starts[0]["session"].id == s.id, (
        rig.arm.hold)
    assert rig.arm._ladder_light == "lit", "the target's solve worked"


async def test_an_unknown_verdict_at_the_target_holds_and_parks(
        sim_hub, monkeypatch, bus_lines):
    """The same failure at the target: a hold in words that claim neither a
    restart nor an unknown position, the mount parked, and no verdict about
    light (``_ladder_light`` stays None)."""
    _arm()
    rig = _Rig(sim_hub, monkeypatch, "unknown", "unknown")

    await rig.tick_at(T0)

    assert rig.events == ["solve@park", "park", "goto", "solve@target",
                          "park"], rig.events
    reason = rig.arm.hold["reason"]
    assert "restart" not in reason and "unknown" not in reason, reason
    assert rig.arm._ladder_light is None
    assert await rig.parked()


async def test_a_sync_refused_at_the_target_parks_again(
        sim_hub, monkeypatch, bus_lines):
    """A refusal at the target (the mount would not take the re-centre's
    sync) is a step-3 hold too: the ladder took the mount off park, so it
    goes back."""
    from astrodeck.devices.base import SyncRefused
    _arm()
    rig = _Rig(sim_hub, monkeypatch, "lit", "solved")
    await rig.tel.park()
    rig.events.clear()

    async def solve(*a, **kw):
        rig.events.append(f"solve@{'target' if rig.went else 'park'}")
        raise SyncRefused("refused", code="e11", solved=(RA, DEC),
                          residual_deg=0.0)
    monkeypatch.setattr(sim_hub, "solve_and_sync", solve)

    await rig.tick_at(T0)

    assert not rig.starts
    assert rig.events[-1] == "park", rig.events
    assert await rig.parked()


async def test_a_parked_mount_with_nothing_to_slew_to_needs_no_solve(
        sim_hub, monkeypatch, bus_lines):
    """Calibration-only work has no candidate: nothing slews and the park
    view is not solved; the run starts."""
    s = Session(name="cal", status="dormant", auto_resume=True,
                plan=SequencePlan(name="cal", targets=[Target(
                    name="darks", ra_hours=0.0, dec_deg=0.0, calibration=True,
                    steps=[ExposureStep(frame_type="Dark", exposure_s=0.05,
                                        count=2)])]))
    session_store.save(s)
    rig = _Rig(sim_hub, monkeypatch, "lit", "solved")
    await rig.tel.park()
    rig.events.clear()

    await rig.tick_at(T0)

    assert rig.events == [], rig.events
    assert len(rig.starts) == 1, rig.arm.hold
