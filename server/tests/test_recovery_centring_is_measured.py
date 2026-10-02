# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""After a tracking-refusal recovery, "centred" is a measurement (#171).

`_setup_target`'s GoTo can die on a mount pinned at its meridian limit
("tracking on rejected"). The park/unpark recovery then re-acquires the target
with a `goto_and_center` of its own, and it may not converge - it logs as much.
Setup used to throw that answer away and carry on with a fabricated
``{"centered": True, "error_arcmin": None}``, so everything after it (today the
centring report, from S1 the mosaic's centring and angle checks) was told a
target was centred that nobody had measured as centred.

The harness drives the REAL recovery to its re-centre: the site is a configured
fixture and `schedule.dark_enough` is pinned True, on purpose, so the
recovery's daylight and default-site refusals pass; the mount refuses tracking
until a park clears it, as the AM5 did on 2026-08-21 and 08-22. Every case
asserts the park and the unpark happened, so none can pass by never reaching
the branch.
"""
from __future__ import annotations

import pytest

import astrodeck.config as config_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.config import ConfigStore, SafetyConfig, Site
from astrodeck.devices.sim import SimTelescope
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    # A fixture site, configured (set_site clears is_default): the recovery
    # refuses to unpark on a rig that cannot tell day from night.
    store.set_site(Site(name="Fixture", latitude=40.0, longitude=-74.0))
    store.set_safety(SafetyConfig(enabled=False))
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(engine_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    return store


@pytest.fixture
async def sim_hub(temp_store, monkeypatch):
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    monkeypatch.setattr(SimTelescope, "SLEW_RATE_DEG_S", 1.0e6)
    # Night, by decree: the daylight refusal is not what these tests are about.
    monkeypatch.setattr(engine_mod.schedule, "dark_enough",
                        lambda *a, **kw: True)
    # One confirmed "not tracking" costs 4 x 1 s of real sleep otherwise.
    monkeypatch.setattr(engine_mod, "TRACKING_CONFIRM_S", 0.0)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _pinned_mount(sim_hub, monkeypatch, gotos: list) -> dict:
    """A mount at its meridian limit: it reads not-tracking until a park clears
    it. ``gotos`` scripts `goto_and_center`, one entry per call: an exception
    is raised, a dict is returned (copied)."""
    tel = sim_hub.devices["telescope"]
    # ``kwargs`` holds every call's keywords, in call order (#170).
    st = {"limit": True, "events": [], "kwargs": []}
    real_get, real_park, real_unpark = tel.get_tracking, tel.park, tel.unpark

    async def get_tracking():
        return bool(await real_get()) and not st["limit"]

    async def park():
        st["events"].append("park")
        st["limit"] = False
        await real_park()

    async def unpark():
        st["events"].append("unpark")
        await real_unpark()

    async def goto(ra_hours, dec_deg, **kw):
        st["kwargs"].append(dict(kw))
        answer = gotos[len([e for e in st["events"] if e.startswith("goto")])]
        if isinstance(answer, Exception):
            st["events"].append("goto refused")
            raise answer
        st["events"].append("goto")
        return dict(answer)

    monkeypatch.setattr(tel, "get_tracking", get_tracking)
    monkeypatch.setattr(tel, "park", park)
    monkeypatch.setattr(tel, "unpark", unpark)
    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    return st


def _engine(sim_hub, **target_kw) -> tuple[SequenceEngine, Target]:
    t = Target(name="Alpha", ra_hours=22.6, dec_deg=34.4, center=True,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=1.0, count=1)],
               **target_kw)
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(name="recover", guide=False, meridian_flip=False,
                          safety_check=False, targets=[t])
    e._cfg = None
    return e, t


REFUSED = RuntimeError("tracking on rejected (reply '0')")


def _misses(lines) -> list[str]:
    """Every line that reports a centring which did not converge."""
    return [m for _lvl, m, _src in lines
            if ("centering" in m or "re-centring after the recovery" in m)
            and "continuing" in m]


def _recovered_line(lines) -> str:
    """The single `recovered in Ns` summary line, or "" if none was logged."""
    matches = [m for _lvl, m, _src in lines if "recovered in" in m]
    assert len(matches) <= 1, f"expected at most one recovered-in line: {matches}"
    return matches[0] if matches else ""


def _assert_recovered(st: dict) -> None:
    ev = st["events"]
    assert ev[:1] == ["goto refused"], f"premise: setup's GoTo must fail: {ev}"
    assert "park" in ev and "unpark" in ev, (
        f"premise: the recovery never ran, so this proves nothing: {ev}")
    assert ev.index("goto", ev.index("unpark")) > ev.index("unpark"), (
        f"premise: the recovery must end in its own re-centre: {ev}")


@pytest.mark.parametrize("error_arcmin, said", [
    (None, "plate solve failed"),
    (3.2, "converged to 3.2'"),
])
async def test_a_recovery_that_did_not_converge_leaves_centered_false(
        sim_hub, monkeypatch, bus_lines, error_arcmin, said):
    """The recovery's re-centre answers ``centered: False``. Setup acts on THAT,
    reporting the miss with the recovery's own number, and the miss is reported
    once: the recovery leaves the line to setup.

    Mutant "fabricated True" (setup sets ``result = {"centered": True,
    "error_arcmin": None}`` after the recovery, as it used to): RED, both
    cases -
        AssertionError: setup took a recovery that never converged as a
        centred target: nothing reports the miss: []
    Mutant "the recovery reports too" (``report_centring=True`` whatever the
    caller passed): RED, both cases -
        AssertionError: one miss, reported 2 times: ['Alpha: re-centring
        after the recovery could not plate solve — continuing', 'Alpha:
        centering plate solve failed — used raw GoTo — continuing']
        AssertionError: one miss, reported 2 times: ["Alpha: re-centring
        after the recovery converged to 3.2' — continuing", "Alpha:
        centering converged to 3.2' — continuing"]
    """
    st = _pinned_mount(sim_hub, monkeypatch, [
        REFUSED, {"centered": False, "error_arcmin": error_arcmin}])
    e, t = _engine(sim_hub)
    await e._setup_target(0, t)
    _assert_recovered(st)
    misses = _misses(bus_lines)
    assert misses, (
        f"setup took a recovery that never converged as a centred target: "
        f"nothing reports the miss: {misses}")
    assert len(misses) == 1, f"one miss, reported {len(misses)} times: {misses}"
    assert said in misses[0], (
        f"the report is not the recovery's own measurement: {misses[0]!r}")


async def test_control_a_recovery_that_converged_leaves_centered_true(
        sim_hub, monkeypatch, bus_lines):
    """CONTROL. The recovery converges: nothing reports a miss.

    Mutant "fabricated False" (setup sets ``result = {"centered": False,
    "error_arcmin": None}`` after the recovery): RED -
        AssertionError: a recovery that converged was reported as a miss:
        ['Alpha: centering plate solve failed — used raw GoTo — continuing']
    """
    st = _pinned_mount(sim_hub, monkeypatch, [
        REFUSED, {"centered": True, "error_arcmin": 0.4}])
    e, t = _engine(sim_hub)
    await e._setup_target(0, t)
    _assert_recovered(st)
    assert _misses(bus_lines) == [], (
        f"a recovery that converged was reported as a miss: "
        f"{_misses(bus_lines)}")


async def test_the_other_callers_still_hear_the_recovery_report_its_miss(
        sim_hub, monkeypatch, bus_lines):
    """The frame loop's `_enforce_tracking` (like the flip) does not act on the
    centring, so for it the recovery still reports its own miss, exactly as it
    did before #171, and still recovers.

    Mutant "the recovery stops reporting for everyone"
    (`_recover_from_tracking_refusal` passes ``report_centring=False`` to
    `_do_tracking_recovery` whatever its caller asked): RED -
        AssertionError: the recovery no longer reports its own miss to the
        callers that do not: []
    """
    st = _pinned_mount(sim_hub, monkeypatch,
                       [{"centered": False, "error_arcmin": None}])
    e, t = _engine(sim_hub)
    await e._enforce_tracking(t.steps[0], t)       # recovers, so no StopTarget
    assert "park" in st["events"] and "goto" in st["events"], (
        f"premise: the recovery must run: {st['events']}")
    misses = _misses(bus_lines)
    assert len(misses) == 1 and "re-centring after the recovery" in misses[0], (
        f"the recovery no longer reports its own miss to the callers that do "
        f"not: {misses}")


# ------------------------------------------------ the target's centring (#170)


async def test_the_recovery_re_centres_with_the_targets_own_settings(
        sim_hub, monkeypatch):
    """The recovery's re-centre is a centring of THIS target, so it takes the
    target's tolerance and attempts exactly as setup's does: both calls build
    their keywords through one helper.

    Mutant "setup only" (the recovery's `goto_and_center` passes only
    ``rotation_deg``, as it did before #170): RED -
        AssertionError: the recovery re-centred to the hub's defaults, not
        the target's: setup [{'rotation_deg': None, 'tolerance_deg':
        0.008333333333333333, 'max_attempts': 5}], recovery
        [{'rotation_deg': None}]
    """
    st = _pinned_mount(sim_hub, monkeypatch, [
        REFUSED, {"centered": True, "error_arcmin": 0.3}])
    e, t = _engine(sim_hub, center_tolerance_arcmin=0.5, center_attempts=5)
    await e._setup_target(0, t)
    _assert_recovered(st)
    want = {"rotation_deg": None, "tolerance_deg": pytest.approx(0.5 / 60),
            "max_attempts": 5}
    setup, recovery = st["kwargs"][:1], st["kwargs"][1:]
    assert setup == [want], f"premise: setup passed the settings: {setup}"
    assert recovery == [want], (
        f"the recovery re-centred to the hub's defaults, not the target's: "
        f"setup {setup}, recovery {recovery}")


# ----------------------------------- the summary line itself (#200) --------


async def test_recovered_line_says_recentred_only_when_it_converged(
        sim_hub, monkeypatch, bus_lines):
    """#200: the "recovered in Ns" summary line said "the target is
    re-centred" from the final tracking readback alone, never from the
    re-centre's own `goto_and_center` result. A recovery whose re-centre did
    not converge used to log two contradictory lines in a row: this one
    claiming "re-centred", immediately followed by `_do_tracking_recovery`'s
    own "re-centring after the recovery ... could not plate solve". Build the
    clause from `found["centered"]` instead.

    Mutant "fixed wording" (the clause stays "and the target is re-centred"
    whatever `found` says): RED -
        AssertionError: a recovery that did not converge still claims
        "re-centred": 'Alpha: recovered in 0s - the mount is tracking again
        and the target is re-centred'
    """
    st = _pinned_mount(sim_hub, monkeypatch,
                       [{"centered": False, "error_arcmin": None}])
    e, t = _engine(sim_hub)
    await e._enforce_tracking(t.steps[0], t)       # recovers, so no StopTarget
    assert "park" in st["events"] and "goto" in st["events"], (
        f"premise: the recovery must run: {st['events']}")
    line = _recovered_line(bus_lines)
    assert line, f"premise: the recovered-in line must be logged: {bus_lines}"
    assert "re-centred" not in line, (
        f'a recovery that did not converge still claims "re-centred": '
        f"{line!r}")
    assert "did not converge" in line, (
        f"the line does not say the re-centre missed: {line!r}")


async def test_control_recovered_line_says_recentred_when_it_converged(
        sim_hub, monkeypatch, bus_lines):
    """CONTROL. A re-centre that converges keeps the "re-centred" wording.

    Mutant "always say it missed" (the clause is always "but the re-centre
    did not converge"): RED -
        AssertionError: a recovery that converged does not say "re-centred":
        'Alpha: recovered in 0s - the mount is tracking again but the
        re-centre did not converge'
    """
    st = _pinned_mount(sim_hub, monkeypatch,
                       [{"centered": True, "error_arcmin": 0.4}])
    e, t = _engine(sim_hub)
    await e._enforce_tracking(t.steps[0], t)       # recovers, so no StopTarget
    assert "park" in st["events"] and "goto" in st["events"], (
        f"premise: the recovery must run: {st['events']}")
    line = _recovered_line(bus_lines)
    assert line, f"premise: the recovered-in line must be logged: {bus_lines}"
    assert "re-centred" in line, (
        f'a recovery that converged does not say "re-centred": {line!r}')


async def test_control_an_unset_target_recovers_with_todays_call(
        sim_hub, monkeypatch):
    """CONTROL. Unset, the recovery's re-centre is today's exact call:
    ``rotation_deg`` alone.

    Mutant "always pass the hub defaults" (the helper always returns
    ``tolerance_deg=0.02, max_attempts=3``): RED -
        AssertionError: an unset target's recovery changed its call:
        [{'rotation_deg': None, 'tolerance_deg': 0.02, 'max_attempts': 3},
        {'rotation_deg': None, 'tolerance_deg': 0.02, 'max_attempts': 3}]
    """
    st = _pinned_mount(sim_hub, monkeypatch, [
        REFUSED, {"centered": True, "error_arcmin": 0.3}])
    e, t = _engine(sim_hub)
    await e._setup_target(0, t)
    _assert_recovered(st)
    assert st["kwargs"] == [{"rotation_deg": None}] * 2, (
        f"an unset target's recovery changed its call: {st['kwargs']}")
