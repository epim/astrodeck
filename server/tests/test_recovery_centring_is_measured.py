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
    st = {"limit": True, "events": []}
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


def _engine(sim_hub) -> tuple[SequenceEngine, Target]:
    t = Target(name="Alpha", ra_hours=22.6, dec_deg=34.4, center=True,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=1.0, count=1)])
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
