"""Armed safety with no monitor: the frames are still evidence.

Observed on the rig 2026-08-17 at 00:22:59, on a run recovering 58 frames:

    safety is armed but no monitor is assigned - nothing is watching the
    weather for this run.

That was literally true. The weather veto guards the START of a run, not the
running night; the self-releasing cloud hold is driven only by a
`hold_for_clear` INSTRUCTION, and that night was a Plan-built session with zero
instructions. So a rig that takes a cloud verdict off every single frame - it
logged "sky verdict: clear (200 bright stars, 17x noise)" fourteen minutes
later - had nothing at all standing between it and a sky that closed in.

NOT the forecast, and the same night proves why: Open-Meteo said 100% cloud
while the frames showed 200 bright stars at 17x noise. A prediction that wrong
must never be allowed to park a mount. This reads the sky.
"""
import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _engine(hub, *, enabled=True, fallback=True, monitor=False):
    # These are about a REAL rig with no monitor assigned. The fixture is a
    # simulator, and a simulated frame is explicitly not a sky (see the last
    # test), so the mode has to say what is being modelled.
    hub.mode = "native"
    eng = SequenceEngine(hub)
    cfg = hub_module.config_store.cfg()
    cfg.safety.enabled = enabled
    cfg.safety.sky_fallback_hold = fallback
    cfg.escalation.require_safety_monitor = False
    eng._cfg = cfg
    eng.plan = SequencePlan(
        name="p", safety_check=True, guide=False,
        targets=[Target(name="A", ra_hours=5.5, dec_deg=-5.0, center=False,
                        autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=1)])])
    if not monitor:
        hub.devices.pop("safety", None)
    return eng


def _sky(eng, cloudy: bool | None):
    """Force the frame-derived verdict without faking the detector's maths."""
    eng._clouds.cloudy = lambda _now: cloudy       # type: ignore[assignment]
    eng._clouds.describe = lambda _now: (          # type: ignore[assignment]
        "cloudy" if cloudy else "clear")


async def test_a_closed_sky_holds_instead_of_shooting_through_it(sim_hub):
    eng = _engine(sim_hub)
    _sky(eng, True)
    held = {"n": 0}

    async def _hold(reason, target):
        held["n"] += 1
        held["reason"] = reason
    eng._hold_for_clear = _hold                    # type: ignore[assignment]

    await eng._no_safety_source(None)
    assert held["n"] == 1
    assert "no safety monitor" in held["reason"]


async def test_a_clear_sky_does_not_hold(sim_hub):
    eng = _engine(sim_hub)
    _sky(eng, False)
    held = {"n": 0}

    async def _hold(reason, target):
        held["n"] += 1
    eng._hold_for_clear = _hold                    # type: ignore[assignment]

    await eng._no_safety_source(None)
    assert held["n"] == 0


async def test_an_unknown_sky_does_not_hold(sim_hub):
    """`None` is "nobody can say" - a blind probe must not hold the night. The
    tri-state matters: reading unknown as cloudy would stop every run that has
    not taken a probe frame yet."""
    eng = _engine(sim_hub)
    _sky(eng, None)
    held = {"n": 0}

    async def _hold(reason, target):
        held["n"] += 1
    eng._hold_for_clear = _hold                    # type: ignore[assignment]

    await eng._no_safety_source(None)
    assert held["n"] == 0


async def test_turning_it_off_restores_the_old_behaviour(sim_hub, bus_lines):
    eng = _engine(sim_hub, fallback=False)
    _sky(eng, True)
    held = {"n": 0}

    async def _hold(reason, target):
        held["n"] += 1
    eng._hold_for_clear = _hold                    # type: ignore[assignment]

    await eng._no_safety_source(None)
    assert held["n"] == 0, "opting out must really opt out"
    msgs = [m for (_lvl, m, _src) in bus_lines]
    assert any("nothing is watching" in m for m in msgs)


async def test_the_warning_stops_claiming_nothing_is_watching(sim_hub, bus_lines):
    """The old sentence was true before the fallback existed. Leaving it would
    be the opposite kind of lie - telling an operator they are unguarded while
    the run holds itself on their behalf."""
    eng = _engine(sim_hub)
    _sky(eng, False)
    await eng._no_safety_source(None)
    msgs = [m for (_lvl, m, _src) in bus_lines]
    assert any("standing in for one" in m for m in msgs)
    assert not any("nothing is watching" in m for m in msgs)
    # and it must not overclaim: this is weather, not rain or wind or a roof
    assert any("assign a monitor for rain, wind or a roof" in m for m in msgs)


async def test_it_is_inert_when_a_real_monitor_exists(sim_hub):
    """_no_safety_source is only reached when `mon is None`; this pins the
    contract so a future refactor cannot start second-guessing a real device."""
    eng = _engine(sim_hub)
    assert sim_hub.devices.get("safety") is None


def test_the_default_is_on_and_that_is_the_whole_point():
    """Caught by sabotage: flipping the default to False left every test above
    green, because each one sets the flag explicitly. The default IS the fix -
    a rig that never opens Settings is exactly the rig running armed with no
    monitor, which is the shipped default and the state this one was in.

    This is also the one place to argue with if the trade turns out wrong: the
    hold is self-releasing and bounded, and the alternative default is a gate
    that reads as protection and permits everything.
    """
    from astrodeck.config import AppConfig
    assert AppConfig().safety.sky_fallback_hold is True


async def test_a_simulated_frame_is_not_a_sky(sim_hub):
    """The sim's frames have no stars, so the detector calls every one of them
    "cloudy: 1 bright stars, low contrast". The first version of this fallback
    therefore held every simulated run - the suite caught it as a whole-run
    regression, not as a unit failure.

    Trusting a sim frame as weather evidence is the same mistake as trusting
    the forecast, pointed the other way.
    """
    eng = _engine(sim_hub)
    sim_hub.mode = "sim"
    _sky(eng, True)
    held = {"n": 0}

    async def _hold(reason, target):
        held["n"] += 1
    eng._hold_for_clear = _hold                    # type: ignore[assignment]

    await eng._no_safety_source(None)
    assert held["n"] == 0, "a simulated sky must never hold a run"
