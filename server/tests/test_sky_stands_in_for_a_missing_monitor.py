# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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

A HOLD NEEDS A TARGET (#221, H2 orchestrator ruling 2, spec "Still waiting
on the owner" item 5; the orchestrator's ruling, not the owner's). The gate
this fallback sits behind is also asked with no target: by every tick of a
scheduler wait, and between day darks after the park. A hold opened there had
nothing to watch and nothing to point at, and on a stopped mount every check it
took was a streak read as cloud, so it ran to its 45 minute bound under a sky
that had cleared. So: a closed sky with a target holds; with none, nothing
holds, and a wait says it once for the spell and publishes it in
``sky.hold_deferred``. Day darks, which run after the run has ended, say
nothing. The whole-night cases are test_cloud_hold_follows_the_mount.py's.
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


def _engine(hub, monkeypatch, *, enabled=True, fallback=True, monitor=False):
    # These are about a REAL rig with no monitor assigned. The fixture is a
    # simulator, and a simulated frame is explicitly not a sky (see the last
    # test), so the mode has to say what is being modelled.
    hub.mode = "native"
    eng = SequenceEngine(hub)
    cfg = hub_module.config_store.cfg()
    # Through monkeypatch: this is the process-wide config, and a plain write
    # left `sky_fallback_hold = False` on it for every later test on the
    # worker (#227).
    # With a plain write in place of the sky_fallback_hold patch, the
    # conftest guard errors at teardown (2 in this file alone, observed):
    # "test_turning_it_off_restores_the_old_behaviour left the
    # process-wide config changed: safety.sky_fallback_hold."
    monkeypatch.setattr(cfg.safety, "enabled", enabled)
    monkeypatch.setattr(cfg.safety, "sky_fallback_hold", fallback)
    monkeypatch.setattr(cfg.escalation, "require_safety_monitor", False)
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


async def test_a_closed_sky_holds_instead_of_shooting_through_it(
        sim_hub, monkeypatch):
    """With a target: the frame loop's gate, or a setup's pre-slew gate."""
    eng = _engine(sim_hub, monkeypatch)
    _sky(eng, True)
    held = {"n": 0}

    async def _hold(reason, target):
        held["n"] += 1
        held["reason"] = reason
        held["target"] = target
    eng._hold_for_clear = _hold                    # type: ignore[assignment]

    a = eng.plan.targets[0]
    await eng._no_safety_source(a)
    assert held["n"] == 1
    assert held["target"] is a
    assert "no safety monitor" in held["reason"]


async def test_a_closed_sky_with_no_target_holds_nothing_and_says_so_once(
        sim_hub, monkeypatch, bus_lines):
    """#221. With no target, as every tick of a scheduler wait asks it: no
    hold, one line for the spell however many ticks read the closed sky, and
    the state publishes why nothing is holding. When the sky no longer reads
    cloudy, what was published goes with it.

    Mutant "target-less hold from the wait" (the ``target is None`` branch
    in `_no_safety_source` deleted): RED (observed) -
        AssertionError: a closed sky with no target opened 3 holds
    Mutant "said on every look" (the once-per-spell latch in
    `_note_hold_deferred` removed): RED (observed) -
        AssertionError: the closed sky was said 3 times in one spell
    Mutant "the note outlives the cloud" (the clearing of
    ``_hold_deferred`` on a sky that no longer reads cloudy deleted): RED
    (observed) -
        AssertionError: still published after the sky cleared: "the frames
        say the sky has closed in, with no target set up to judge it from,
        so no cloud hold is open; the next target's setup opens one if the
        sky is still closed"
    """
    eng = _engine(sim_hub, monkeypatch)
    _sky(eng, True)
    held = {"n": 0}

    async def _hold(reason, target):
        held["n"] += 1
    eng._hold_for_clear = _hold                    # type: ignore[assignment]
    # `eng.state` changes only through `_set_state`, which publishes it: what
    # it holds is what was last published.
    for _ in range(3):
        await eng._no_safety_source(None)
    assert held["n"] == 0, (
        f"a closed sky with no target opened {held['n']} holds")
    said = [m for _l, m, _s in bus_lines
            if "no target set up to judge it from" in m]
    assert len(said) == 1, (
        f"the closed sky was said {len(said)} times in one spell")
    assert (eng.state.get("sky") or {}).get("hold_deferred"), (
        "it was never published")
    _sky(eng, False)
    await eng._no_safety_source(None)
    assert eng.state["sky"]["hold_deferred"] is None, (
        f"still published after the sky cleared: "
        f"{eng.state['sky']['hold_deferred']!r}")


async def test_a_clear_sky_does_not_hold(sim_hub, monkeypatch):
    """With a target, since #221: a target-less gate holds nothing whatever
    the sky says, so asked that way this could not fail.

    Mutant "any verdict holds" (``if verdict is True:`` made ``if True:``):
    RED (observed) -
        AssertionError: a clear sky held the run
    """
    eng = _engine(sim_hub, monkeypatch)
    _sky(eng, False)
    held = {"n": 0}

    async def _hold(reason, target):
        held["n"] += 1
    eng._hold_for_clear = _hold                    # type: ignore[assignment]

    await eng._no_safety_source(eng.plan.targets[0])
    assert held["n"] == 0, "a clear sky held the run"


async def test_an_unknown_sky_does_not_hold(sim_hub, monkeypatch):
    """`None` is "nobody can say" - a blind probe must not hold the night. The
    tri-state matters: reading unknown as cloudy would stop every run that has
    not taken a probe frame yet.

    With a target, since #221 (see the clear-sky case). Mutant "unknown read
    as cloudy" (``verdict is True`` made ``verdict is not False``): RED
    (observed) -
        AssertionError: a sky nobody can read held the run
    """
    eng = _engine(sim_hub, monkeypatch)
    _sky(eng, None)
    held = {"n": 0}

    async def _hold(reason, target):
        held["n"] += 1
    eng._hold_for_clear = _hold                    # type: ignore[assignment]

    await eng._no_safety_source(eng.plan.targets[0])
    assert held["n"] == 0, "a sky nobody can read held the run"


async def test_turning_it_off_restores_the_old_behaviour(
        sim_hub, monkeypatch, bus_lines):
    """With a target, since #221 (see the clear-sky case).

    Mutant "the fallback ignores its switch" (the ``sky_fallback_hold`` test
    before the verdict deleted): RED (observed) -
        AssertionError: opting out must really opt out
    """
    eng = _engine(sim_hub, monkeypatch, fallback=False)
    _sky(eng, True)
    held = {"n": 0}

    async def _hold(reason, target):
        held["n"] += 1
    eng._hold_for_clear = _hold                    # type: ignore[assignment]

    await eng._no_safety_source(eng.plan.targets[0])
    assert held["n"] == 0, "opting out must really opt out"
    msgs = [m for (_lvl, m, _src) in bus_lines]
    assert any("nothing is watching" in m for m in msgs)


async def test_the_warning_stops_claiming_nothing_is_watching(
        sim_hub, monkeypatch, bus_lines):
    """The old sentence was true before the fallback existed. Leaving it would
    be the opposite kind of lie - telling an operator they are unguarded while
    the run holds itself on their behalf."""
    eng = _engine(sim_hub, monkeypatch)
    _sky(eng, False)
    await eng._no_safety_source(None)
    msgs = [m for (_lvl, m, _src) in bus_lines]
    assert any("standing in for one" in m for m in msgs)
    assert not any("nothing is watching" in m for m in msgs)
    # and it must not overclaim: this is weather, not rain or wind or a roof
    assert any("assign a monitor for rain, wind or a roof" in m for m in msgs)


async def test_it_is_inert_when_a_real_monitor_exists(sim_hub, monkeypatch):
    """_no_safety_source is only reached when `mon is None`; this pins the
    contract so a future refactor cannot start second-guessing a real device."""
    eng = _engine(sim_hub, monkeypatch)
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


async def test_a_simulated_frame_is_not_a_sky(sim_hub, monkeypatch):
    """The sim's frames have no stars, so the detector calls every one of them
    "cloudy: 1 bright stars, low contrast". The first version of this fallback
    therefore held every simulated run - the suite caught it as a whole-run
    regression, not as a unit failure.

    Trusting a sim frame as weather evidence is the same mistake as trusting
    the forecast, pointed the other way.

    With a target, since #221 (see the clear-sky case). Mutant "a simulated
    frame is a sky" (``not simulated`` dropped from the fallback's test):
    RED (observed) -
        AssertionError: a simulated sky must never hold a run
    """
    eng = _engine(sim_hub, monkeypatch)
    sim_hub.mode = "sim"
    _sky(eng, True)
    held = {"n": 0}

    async def _hold(reason, target):
        held["n"] += 1
    eng._hold_for_clear = _hold                    # type: ignore[assignment]

    await eng._no_safety_source(eng.plan.targets[0])
    assert held["n"] == 0, "a simulated sky must never hold a run"


def _darks_under_a_closed_sky(eng, monkeypatch) -> tuple[dict, list[str]]:
    """Two day darks to shoot, the frames' verdict cloudy, a hold and the
    darks themselves recorded rather than run."""
    eng.plan.day_darks = 2
    _sky(eng, True)
    held = {"n": 0}

    async def _hold(reason, target):
        held["n"] += 1
    eng._hold_for_clear = _hold                    # type: ignore[assignment]
    monkeypatch.setattr(eng, "_hold_darks_shortfall",
                        lambda step, quota: (quota, 0))
    shot: list[str] = []

    async def run_calibration(ti, target):
        shot.append(target.name)

    monkeypatch.setattr(eng, "_run_calibration", run_calibration)
    return held, shot


def _noted(bus_lines) -> list[str]:
    return [m for _l, m, _s in bus_lines
            if "no target set up to judge it from" in m]


def _published(eng) -> str | None:
    """The ``sky.hold_deferred`` last published, None when none was."""
    return (eng.state.get("sky") or {}).get("hold_deferred")


async def test_day_darks_under_a_closed_sky_are_shot_and_hold_nothing(
        sim_hub, monkeypatch, bus_lines):
    """WHAT DAY DARKS DO NOW (#221). They run after the park, with the cover
    shut, and ask the gate with no target between frames. A closed sky from
    the night's last frames used to open a target-less hold there: its checks
    were taken at the last science step on a parked mount, read as cloud,
    and the hold ran to its 45 minute bound and aborted inside the wind-down.
    Now nothing holds and every dark is shot: a dark needs no sky.

    AND NOTHING IS NOTED (changed in the H2 integration). H2 T1 had the
    gate say and publish a wait's note here: "the next target's setup opens
    one if the sky is still closed". Day darks run after the run has ended,
    so no setup is coming and nothing would ever end the note; this case
    asserted it was said once. It now asserts nothing is said or published.
    The control after the darks shows a wait's gate still notes the same
    sky, so the flag the darks raise is down again.

    Mutant "target-less hold from the wait" (the ``target is None`` branch
    in `_no_safety_source` deleted): RED (observed) -
        AssertionError: day darks under a closed sky opened 2 holds and shot
        2 darks
    Mutant "day darks note a deferred hold" (``if not
    self._day_darks_gate:`` in `_no_safety_source` made ``if True:``): RED
    (observed) -
        AssertionError: day darks noted a wait's deferred hold: said 1
        time(s), published "the frames say the sky has closed in, with no
        target set up to judge it from, so no cloud hold is open; the next
        target's setup opens one if the sky is still closed"
    Mutant "the day-darks flag is never lowered" (the ``finally`` after the
    darks' gate made ``pass``): RED at the control (observed) -
        AssertionError: a wait after the day darks noted nothing: said 0
        time(s), published None
    """
    eng = _engine(sim_hub, monkeypatch)
    held, shot = _darks_under_a_closed_sky(eng, monkeypatch)
    await eng._day_darks()
    assert held["n"] == 0 and len(shot) == 2, (
        f"day darks under a closed sky opened {held['n']} holds and shot "
        f"{len(shot)} darks")
    assert not _noted(bus_lines) and _published(eng) is None, (
        f"day darks noted a wait's deferred hold: said "
        f"{len(_noted(bus_lines))} time(s), published {_published(eng)!r}")
    # CONTROL: a wait's gate on the same engine and the same sky notes it.
    await eng._no_safety_source(None)
    assert len(_noted(bus_lines)) == 1 and _published(eng), (
        f"a wait after the day darks noted nothing: said "
        f"{len(_noted(bus_lines))} time(s), published {_published(eng)!r}")


async def test_day_darks_stopped_by_the_gate_leave_a_wait_its_note(
        sim_hub, monkeypatch, bus_lines):
    """The flag day darks raise around their gate comes down when the gate
    raises too. A SafetyAbort there (a real monitor's unsafe verdict, a
    watchdog trip) ends the run, and the engine lives on to the next run: a
    flag left up would silence every later wait's note under a closed sky.

    Mutant "the day-darks flag is lowered only on success" (the ``try`` /
    ``finally`` around the darks' gate made two plain assignments): RED
    (observed) -
        AssertionError: a wait after day darks the gate stopped noted
        nothing: said 0 time(s), published None
    Also RED under "the day-darks flag is never lowered", with the same
    words.
    """
    from astrodeck.sequence.engine import SafetyAbort
    eng = _engine(sim_hub, monkeypatch)
    _held, shot = _darks_under_a_closed_sky(eng, monkeypatch)

    async def unsafe(*_a, **_kw):
        raise SafetyAbort("rain")

    monkeypatch.setattr(eng, "_safety_gate", unsafe)
    with pytest.raises(SafetyAbort):
        await eng._day_darks()
    assert not shot, f"premise: the gate stopped the darks first: {shot}"
    await eng._no_safety_source(None)
    assert len(_noted(bus_lines)) == 1 and _published(eng), (
        f"a wait after day darks the gate stopped noted nothing: said "
        f"{len(_noted(bus_lines))} time(s), published {_published(eng)!r}")
