# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-114 (#697, #709): a FAILED rotator self-test can be re-run by the
operator, and the hub's rotator verdict has an observing night.

#697. ``Hub.ensure_rotator_ready`` ran the follow test only while the verdict
was None, so a FAIL stood until the whole rig reconnected: the owner re-seats
the CAA coupling (#594) and the only way to try again was ``POST
/api/profiles/<id>/activate``. Backlog ruling for WP-114: an explicit operator
TEST ROTATOR (``retest_failed=True``, which the preflight route passes) re-runs
the self-test when the last one FAILED, keeping the learned sign; the
automatic callers (``goto_and_center``'s preflight, the engine's nightly
self-test) keep the once-per-connect rule, because re-testing a coupling that
already failed would let the next hop quietly clear a verdict the operator has
not acted on.

#709. ``_rotation_trusted`` was cleared only by ``_teardown``, so "rotation is
off for the night" really meant "off until reconnect" and a PASS from night 1
stood as night 2's in a process that stayed up. The verdict is stamped with
``events.night_key`` when it is set, and a verdict from another night reads as
None, so the first rotating group of each night asks again. The learned sign is
NOT stamped: it is which way the camera turns against the motor, a fact about
the train's geometry that a re-seated coupling does not change, and the
simulator's declared +1 would otherwise be forgotten at every noon.

The night is pinned by replacing ``astrodeck.events.night_key``, the one
definition the hub reads at call time; the engine imported it by name, so the
engine case pins ``astrodeck.sequence.engine.night_key`` to the same function.
Both clocks are pinned (#682): no case here reads the wall clock's night
between a set and a get except through that function.

NAMED MUTANTS, each run from a byte backup inside this worktree and restored
byte for byte (sha256 compared), the mutant text grepped out afterwards; the
failing assertion each produced is quoted on the test that caught it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import astrodeck.events as events_mod
import astrodeck.sequence.engine as engine_mod
from _group_harness import (Night, grid_plan, group_hub,  # noqa: F401
                            group_store)
from _simhub import sim_hub  # noqa: F401 (fixture import)
from astrodeck.devices.base import DeviceError
from astrodeck.devices.sim import SimRotator
from astrodeck.hub import Hub
from astrodeck.solve.simsolver import SimSolver

#: The goto target the rotation cases use (the field test_goto_rotation.py and
#: test_w14_rotator_preflight.py use).
RA, DEC = 5.0, 10.0

#: A solve frame's name since #532: ``<kind>-<12 hex digits>.fits``.
_UNIQUE = re.compile(r"^(?P<kind>.+)-[0-9a-f]{12}\.fits$")


def _kind_of(fits_path) -> str:
    name = Path(fits_path).name
    m = _UNIQUE.match(name)
    return f"{m.group('kind')}.fits" if m else name


@pytest.fixture
def solves(monkeypatch):
    """The kind of file every solver call was asked to solve, in order.
    Delegates to the real ``SimSolver.solve``."""
    seen: list[str] = []
    real = SimSolver.solve

    async def spy(self, fits_path, **kw):
        seen.append(_kind_of(fits_path))
        return await real(self, fits_path, **kw)

    monkeypatch.setattr(SimSolver, "solve", spy)
    return seen


def _count(seen: list[str], kind: str) -> int:
    return sum(1 for k in seen if k == kind)


def _slip(hub) -> None:
    """50 degrees of play with the motor at the bottom of it: the +20 degree
    step is absorbed whole and the camera does not turn (#594's shape)."""
    rig = hub.sim_rig
    rig.rotator_backlash_deg = 50.0
    rig.rotator_slack_deg = -25.0


def _reseat(hub) -> None:
    """The owner re-seats the coupling: no play, no slack."""
    rig = hub.sim_rig
    rig.rotator_backlash_deg = 0.0
    rig.rotator_slack_deg = 0.0


@pytest.fixture
def at_target(sim_hub):
    sim_hub.sim_rig.ra_hours = RA
    sim_hub.sim_rig.dec_deg = DEC
    return sim_hub


@pytest.fixture
def night_is(monkeypatch):
    """Pin ``night_key`` for the hub (and the engine) to a mutable value."""
    key = {"night": "2026-09-01"}

    def pinned(ts=None):
        return key["night"]

    monkeypatch.setattr(events_mod, "night_key", pinned)
    monkeypatch.setattr(engine_mod, "night_key", pinned)

    def set_night(night: str) -> None:
        key["night"] = night

    return set_night


# ------------------------------------------------ #697: the operator retest


async def test_the_operator_retest_clears_a_failed_self_test_and_keeps_the_sign(
        at_target, solves):
    """The defect, end to end: a slipping coupling fails the self-test, the
    owner re-seats it, TEST ROTATOR runs again and a passing rotator is
    trusted. Only the follow test is measured (two solves): the sign the first
    call learned is kept, not re-learned (no ``rotsign`` frame).

    Mutation 'retest keeps the cached False' (the ``retest_failed and trusted
    is False`` arm of ``ensure_rotator_ready`` made ``False``) went red here
    (and on the next three cases, 4 failed in this file and the route file):

        E       AssertionError: {'ran': [], 'sign': 1, 'trusted': False}
        E       assert {'ran': [], '...usted': False} == {'ran': ['sel...rusted': True}
        E         Differing items:
        E         {'trusted': False} != {'trusted': True}
        E         {'ran': []} != {'ran': ['self_test']}
    """
    hub = at_target
    hub._rotator_sky_sign = None
    await hub.learn_rotator_sign()       # measured, on a healthy train
    assert hub._rotator_sky_sign == 1
    _slip(hub)
    first = await hub.ensure_rotator_ready()
    assert first == {"sign": 1, "trusted": False, "ran": ["self_test"]}, first
    solves.clear()
    _reseat(hub)

    second = await hub.ensure_rotator_ready(retest_failed=True)

    assert second == {"sign": 1, "trusted": True, "ran": ["self_test"]}, second
    assert hub._rotation_trusted is True
    assert _count(solves, "rotselftest.fits") == 2, solves
    assert _count(solves, "rotsign.fits") == 0, (
        f"the learned sign was measured again: {solves}")


async def test_a_retest_that_fails_again_is_an_answer_not_an_error(
        at_target, solves):
    """The coupling is still loose: the retest runs (two solves), returns
    normally with ``trusted`` False, and rotation stays off. Not an error: the
    button's lane would log it as a crash otherwise.

    Mutation 'retest keeps the cached False' went red here:

        E       AssertionError: {'ran': [], 'sign': 1, 'trusted': False}
        E       assert (False is False and [] == ['self_test']
        E         Right contains one more item: 'self_test'
    """
    hub = at_target
    _slip(hub)
    await hub.ensure_rotator_ready()
    assert hub._rotation_trusted is False
    solves.clear()

    again = await hub.ensure_rotator_ready(retest_failed=True)

    assert again["trusted"] is False and again["ran"] == ["self_test"], again
    assert _count(solves, "rotselftest.fits") == 2, solves


async def test_a_retest_of_a_passing_rotator_exposes_nothing(at_target, solves):
    """``retest_failed`` is for a FAILED verdict only: with the rotator
    trusted, the button measures nothing, as before."""
    hub = at_target
    await hub.ensure_rotator_ready()
    assert hub._rotation_trusted is True
    solves.clear()

    again = await hub.ensure_rotator_ready(retest_failed=True)

    assert again["ran"] == [] and again["trusted"] is True, again
    assert solves == [], solves


async def test_a_retest_that_cannot_run_leaves_the_failure_standing(
        at_target, monkeypatch):
    """A self-test that could not run said nothing about the coupling, so it
    must not flip a FAIL to trusted. The DeviceError propagates; ``False``
    stays.

    Mutation 'retest keeps the cached False' went red here, the retest never
    having run:

        E       Failed: DID NOT RAISE <class 'astrodeck.devices.base.DeviceError'>
    """
    hub = at_target
    _slip(hub)
    await hub.ensure_rotator_ready()
    assert hub._rotation_trusted is False

    async def frozen_move(self, mech_deg):
        pass

    monkeypatch.setattr(SimRotator, "move_mechanical", frozen_move)

    with pytest.raises(DeviceError, match="too little"):
        await hub.ensure_rotator_ready(retest_failed=True)

    assert hub._rotation_trusted is False


async def test_a_goto_does_not_retest_a_failed_rotator(at_target, solves):
    """The AUTOMATIC path keeps the once-per-connect rule: after a FAIL the
    next rotating hop degrades to ``rotation_skipped`` through the D-05
    refusal and exposes no follow-test frame, however the coupling has been
    handled since. Only the operator's button re-tests.

    Mutation 'the automatic default retests a failure' (``retest_failed:
    bool = False`` made ``True`` on ``ensure_rotator_ready``) went red here:

        E       AssertionError: {'attempts': 2, 'centered': True,
        'error_arcmin': 0.176842248926498, 'rotation': {'adjusted_to': None,
        'attempts': 2, 'error_deg': 0.0, 'pa_deg': 45.0, ...}}
        E       assert None is True
        E        +  where None = <built-in method get of dict object ...>('rotation_skipped')
    """
    hub = at_target
    _slip(hub)
    await hub.ensure_rotator_ready()
    assert hub._rotation_trusted is False
    _reseat(hub)
    solves.clear()

    result = await hub.goto_and_center(RA, DEC, rotation_deg=45.0)

    assert result.get("rotation_skipped") is True, result
    assert _count(solves, "rotselftest.fits") == 0, solves
    assert hub._rotation_trusted is False


async def test_the_refusal_and_the_docstring_name_the_button(at_target):
    """The D-05 refusal told the operator to wait for 'rotator_self_test' (a
    function name nobody can press) and the self-test's docstring said 'until
    the next self-test passes'; both now name TEST ROTATOR, the thing that
    can run it. ``D-05`` stays in the refusal: the tests that grade it, and
    the log readers, match on it.

    Mutation 'the old wording' (the refusal restored to 'until
    rotator_self_test passes again') went red here:

        E       AssertionError: rotator: the nightly self-test found the camera
        does not reliably follow the rotator (D-05, #594), so rotation is
        refused for the rest of the night; panels should be shot at a fixed
        angle until rotator_self_test passes again
        E       assert 'TEST ROTATOR' in 'rotator: the nightly self-test found ...'

    Mutation 'docstring does not name the button' (``the operator's TEST
    ROTATOR`` in ``rotator_self_test``'s docstring made ``the operator's
    re-test``) went red on the next assertion of the same case:

        E       AssertionError: the docstring does not name the button
    """
    hub = at_target
    hub._rotation_trusted = False
    with pytest.raises(DeviceError, match="D-05") as exc:
        await hub.rotate_to_pa(30.0)
    assert "TEST ROTATOR" in str(exc.value), str(exc.value)
    assert "until rotator_self_test passes again" not in str(exc.value)
    doc = Hub.rotator_self_test.__doc__
    assert "TEST ROTATOR" in doc, "the docstring does not name the button"
    assert "until the next self-test passes" not in doc, doc


# ------------------------------------------------ #709: the verdict's night


async def test_a_verdict_from_another_night_reads_as_none(at_target, night_is):
    """A PASS and a FAIL both read as set on the night they were measured and
    as None on any other: the getter, which every reader (the engine, the
    status block, ``rotate_to_pa``, the resume arm) goes through.

    Mutation 'no night stamp' (the getter returning the stored verdict
    whatever the night) went red here, and on the five cases below, 6 failed
    in all:

        E       assert False is None
        E        +  where False = <astrodeck.hub.Hub object at ...>._rotation_trusted
    """
    hub = at_target
    night_is("2026-09-01")
    hub._rotation_trusted = True
    assert hub._rotation_trusted is True
    hub._rotation_trusted = False
    assert hub._rotation_trusted is False

    night_is("2026-09-02")
    assert hub._rotation_trusted is None

    hub._rotation_trusted = True
    assert hub._rotation_trusted is True, "a verdict set tonight must read tonight"
    night_is("2026-09-03")
    assert hub._rotation_trusted is None


async def test_the_status_block_reads_a_stale_verdict_as_not_measured(
        at_target, night_is):
    """The panel's TEST ROTATOR state comes from this block: a FAIL from
    last night must read 'not measured' so the button, and the preflight
    behind it, are live again."""
    hub = at_target
    night_is("2026-09-01")
    hub._rotation_trusted = False
    # 'no night stamp' (see above) went red on the next-night read: ``assert
    # False is None``.
    assert (await hub.poll_status())["rotator"]["trusted"] is False
    night_is("2026-09-02")
    assert (await hub.poll_status())["rotator"]["trusted"] is None


async def test_a_failure_from_last_night_no_longer_refuses_rotation(
        at_target, night_is):
    """``rotate_to_pa`` refuses only on a verdict that reads False: last
    night's FAIL no longer does, so the first hop of the new night is
    allowed to ask for a self-test instead of being refused for a night it is
    not.

    Mutation 'no night stamp' went red here, last night's refusal standing:

        E       astrodeck.devices.base.DeviceError: rotator: the nightly
        self-test found the camera does not reliably follow the rotator (D-05,
        #594), so rotation is refused for the rest of the night; ...
    """
    hub = at_target
    night_is("2026-09-01")
    hub._rotation_trusted = False
    with pytest.raises(DeviceError, match="D-05"):
        await hub.rotate_to_pa(30.0)
    night_is("2026-09-02")
    result = await hub.rotate_to_pa(30.0)
    assert "rotated" in result, result


async def test_the_second_night_re_tests_but_keeps_the_sign(at_target, solves,
                                                            night_is):
    """Two nights, one connect: the second night's preflight runs the follow
    test (two solves) and not the sign (the sign is geometry, not a verdict,
    and is not night-stamped).

    Mutation 'no night stamp' went red here, night 2 reading night 1's PASS:

        E       AssertionError: {'ran': [], 'sign': 1, 'trusted': True}
        E       assert {'ran': [], '...rusted': True} == {'ran': ['sel...rusted': True}
        E         Differing items:
        E         {'ran': []} != {'ran': ['self_test']}
    """
    hub = at_target
    hub._rotator_sky_sign = None
    night_is("2026-09-01")
    first = await hub.ensure_rotator_ready()
    assert first["ran"] == ["sign", "self_test"], first
    again = await hub.ensure_rotator_ready()
    assert again["ran"] == [], "the same night measured again"
    solves.clear()

    night_is("2026-09-02")
    second = await hub.ensure_rotator_ready()

    assert second == {"sign": 1, "trusted": True, "ran": ["self_test"]}, second
    assert _count(solves, "rotselftest.fits") == 2, solves
    assert _count(solves, "rotsign.fits") == 0, solves


async def test_a_coupling_re_seated_between_nights_is_trusted_again(
        at_target, night_is):
    """A failure on night 1, the owner re-seats the coupling in a process
    that stayed up, and night 2's first preflight measures and trusts it: no
    reconnect and no button press.

    Mutation 'no night stamp' went red here, night 1's FAIL standing:

        E       AssertionError: {'ran': [], 'sign': 1, 'trusted': False}
        E       assert (False is True)
    """
    hub = at_target
    night_is("2026-09-01")
    _slip(hub)
    await hub.ensure_rotator_ready()
    assert hub._rotation_trusted is False
    _reseat(hub)

    night_is("2026-09-02")
    result = await hub.ensure_rotator_ready()

    assert result["trusted"] is True and result["ran"] == ["self_test"], result


async def test_a_clocked_engine_asks_for_a_self_test_on_each_night(
        group_hub, monkeypatch, night_is):
    """The engine's own path, across two observing nights on one connect
    (the case #709 names): the first rotating group of night 1 asks the hub
    for a self-test, a second hop of that night does not, and the first of
    night 2 asks again, because the hub's PASS from night 1 reads as None.
    Before the stamp the engine read night 1's True as night 2's and asked
    nothing, for as long as the process lived.

    Mutation 'no night stamp' (the getter returning the stored verdict
    whatever the night) went red here: the engine read night 1's PASS as
    night 2's and asked nothing:

        E       AssertionError: [{'night': '2026-09-01'}]
        E       assert 1 == 2
    """
    hub = group_hub
    hub._rotation_trusted = None
    night = Night(hub, monkeypatch, sky=lambda who, n: 30.0)
    eng = night.engine
    plan = grid_plan(group_kw={"rotate": True, "pa_deg": 30.0,
                               "angle_tolerance_deg": 6.0})
    eng.plan = plan
    eng._groups = {g.id: g for g in plan.groups}
    target = plan.targets[0]
    calls: list[dict] = []
    real = Hub.rotator_self_test

    async def counted(self, *a, **kw):
        from astrodeck.events import night_key
        calls.append({"night": night_key()})
        return await real(self, *a, **kw)

    monkeypatch.setattr(Hub, "rotator_self_test", counted)
    night_is("2026-09-01")
    try:
        await eng._ensure_rotator_self_test(target)
        await eng._ensure_rotator_self_test(target)
        assert len(calls) == 1, calls
        assert hub._rotation_trusted is True

        night.advance(24 * 3600.0)
        night_is("2026-09-02")
        await eng._ensure_rotator_self_test(target)
        assert len(calls) == 2, calls
        assert [c["night"] for c in calls] == ["2026-09-01", "2026-09-02"], calls
        assert hub._rotation_trusted is True
    finally:
        await night.close()
