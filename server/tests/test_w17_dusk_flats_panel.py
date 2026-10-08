# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The DUSK FLATS stage with a flat panel (#603 job B; backlog wave 17, WP-134).

THE CLASS: a claim nothing keeps. A flow with a DUSK FLATS node compiled, and
``to_plan`` warned "this run will not take flats", because nothing in the
engine ran the block. ``SequenceEngine._dusk_flats`` is that stage, for the
flat-panel method only: once per observing night, after the cooling wait and
before the first light, never after a light frame has been taken tonight, and
only with a connected flat source (without one it says so once and the night
goes on). It reuses ``_run_calibration``: cover closed, lamp on, metered to the
ADU target, lamp off and cover shut in a ``finally``.

What these tests drive, through a REAL ``engine.start`` on the simulator (the
scheduler is stubbed to a marker, so the first light is a point in the event
order and not a time-of-day lottery, #682):

* the flats come first, in filter order, saved, and the marker follows them;
* the lamp is lit with the cover shut, and off and shut after each filter;
* a cover that was OPEN before the stage is open again when the first light
  comes (the stage restores what it found: #601 is the general open-for-the-
  night step and has not landed, and a flat set must not turn a night of lights
  into frames through a shut cover);
* a second engine the same night (a restart) finds the library holding fresh
  flats and shoots none;
* no flat source: one warning, no flats, the scheduler still runs;
* a SafetyAbort mid-flats ends the night, with the lamp off; any other failure
  does not;
* the other two methods (cap, sky) are carried and reported not run yet;
* the flats are shot at the LIGHTS' gain and binning, because a flat is
  matched on both.

NAMED MUTANTS (2026-10-07), each run from a byte backup inside this worktree,
sha256 compared after the restore, the mutant text grepped out. The failing
assertion of each, verbatim (first failure, ``-x``):

* "stage call skipped" (engine.py ``_run``: ``await self._dusk_flats()``
  removed from before ``_run_scheduled``): ``test_flats_come_first_in_filter_
  order``:

      assert [] == ['L', 'L', 'L', 'R', 'R', 'R']

* "stage after the lights" (the call moved to after ``_run_scheduled``):
  ``test_flats_come_first_in_filter_order``:

      AssertionError: a flat was taken after the first light

* "library skip dropped" (``_dusk_flats_fresh`` made to answer 'not fresh'):
  ``test_a_restart_finds_fresh_flats_and_shoots_none``:

      assert ['L', 'L', 'L', 'R', 'R', 'R'] == []

* "SafetyAbort swallowed" (``except SafetyAbort: raise`` removed from the
  stage): ``test_a_safety_abort_mid_flats_ends_the_night``:

      AssertionError: the night went on after an unsafe trip

* "cover not restored" (``if cover_was_open:`` made ``if False:``):
  ``test_a_cover_that_was_open_is_open_for_the_first_light``:

      AssertionError: the first light would be taken through a shut cover:
      ('scheduled', 'closed', 'off')

* "lights guard dropped" (``_lights_taken_tonight`` made ``return False``):
  ``test_it_never_runs_once_a_light_has_been_taken_tonight``:

      assert False is True

* "flats at the engine defaults" (the recipe made ``(filt, 100, 30, 1)``):
  ``test_flats_are_shot_at_the_lights_gain_and_binning``:

      AssertionError: the flats were not shot at the lights' gain and
      binning: [(100, 1)]

* "per-night latch dropped" (the ``_dusk_flats_night == night`` early return
  removed): ``test_the_same_engine_runs_the_stage_once_a_night``:

      assert ['L', 'L', 'L', 'R', 'R', 'R'] == []

* "cap and sky run as panel" (``if df.method != "panel":`` made ``if
  False:``): ``test_the_other_methods_are_carried_and_reported_not_run[cap]``:

      assert (['L', 'L', 'L', 'R', 'R', 'R'] == []

* "failures stop the night" (the stage's ``except Exception as exc:`` made
  ``except ZeroDivisionError as exc:``): ``test_any_other_failure_does_not_stop_
  the_night`` (and ``test_a_failure_before_the_first_frame_does_not_stop_the_
  night``):

      ValueError: not enough values to unpack (expected 1, got 0)

  (the scheduler marker the test unpacks is never recorded: the failure ended
  the run).

* "cover opened before lighting the lamp" (``_run_calibration``'s close_cover
  made open_cover, the #194 order), caught by the existing guard
  ``test_w5_flat_panel_cover_order.py``:

      AssertionError: the cover was not closed while the panel was lit for the
      flat's first trial exposure: {'state': 'ready', 'cover_state': 'open'}
"""
from __future__ import annotations

import time

import pytest

import astrodeck.hub as hub_module
from astrodeck.calibration.library import CalibrationLibrary
from astrodeck.hub import Hub
from astrodeck.sequence import engine as engine_module
from astrodeck.sequence.engine import SafetyAbort, SequenceEngine
from astrodeck.sequence.models import (DuskFlatsPlan, ExposureStep,
                                       SequencePlan, Target)
from astrodeck.sequence.session import SessionFrame

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _lights(filters=("L", "R"), *, gain=100, binning=1) -> Target:
    return Target(name="M31", ra_hours=0.7, dec_deg=41.0, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter=f, exposure_s=1.0, gain=gain,
                                      offset=30, binning=binning, count=2)
                         for f in filters])


def _flats(**kw) -> DuskFlatsPlan:
    base = dict(method="panel", filters=["L", "R"], adu_target=8000, count=3)
    base.update(kw)
    return DuskFlatsPlan(**base)


def _plan(flats=None, **kw) -> SequencePlan:
    return SequencePlan(name="Flats night", guide=False, dither_every=0,
                        targets=[kw.pop("lights", None) or _lights()],
                        dusk_flats=flats, **kw)


class Watch:
    """Records, in order, what the engine does to the camera, the lamp and the
    cover, and what the scheduler finds when it is reached."""

    def __init__(self, hub, eng, monkeypatch):
        self.events: list[tuple] = []
        real_capture, real_on, real_off = (hub.capture, hub.calibrator_on,
                                           hub.calibrator_off)
        real_open, real_close = hub.open_cover, hub.close_cover

        async def capture(exposure_s, gain, offset, binning, **kw):
            fw = hub.devices["filterwheel"]
            name = fw.filter_names[await fw.get_position()]
            self.events.append(("capture", kw.get("frame_type", "Light"),
                                bool(kw.get("save", True)), name, gain,
                                binning))
            return await real_capture(exposure_s, gain, offset, binning, **kw)

        async def lamp_on(level):
            self.events.append(("lamp_on", level, await self.cover(hub)))
            return await real_on(level)

        async def lamp_off():
            self.events.append(("lamp_off",))
            return await real_off()

        async def open_cover():
            self.events.append(("cover", "open"))
            return await real_open()

        async def close_cover():
            self.events.append(("cover", "closed"))
            return await real_close()

        async def scheduled(plan):
            self.events.append(("scheduled", await self.cover(hub),
                                (await hub.calibrator_status())["state"]))

        for name, fn in (("capture", capture), ("calibrator_on", lamp_on),
                         ("calibrator_off", lamp_off),
                         ("open_cover", open_cover),
                         ("close_cover", close_cover)):
            monkeypatch.setattr(hub, name, fn)
        monkeypatch.setattr(eng, "_run_scheduled", scheduled)

    @staticmethod
    async def cover(hub) -> str:
        return (await hub.calibrator_status())["cover_state"]

    def saved_flats(self) -> list[str]:
        return [e[3] for e in self.events
                if e[0] == "capture" and e[1] == "Flat" and e[2]]

    def at(self, kind) -> list[int]:
        return [i for i, e in enumerate(self.events) if e[0] == kind]


def _log_spy(monkeypatch) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    real = engine_module.bus.log

    def log(level, message, source="hub", **kw):
        lines.append((level, message))
        return real(level, message, source, **kw)

    monkeypatch.setattr(engine_module.bus, "log", log)
    return lines


async def _run(hub, plan, monkeypatch, *, engine=None):
    eng = engine or SequenceEngine(hub)
    watch = Watch(hub, eng, monkeypatch)
    eng.start(plan)
    await eng._task
    return eng, watch


# ------------------------------------------------------------- the headline

async def test_flats_come_first_in_filter_order(sim_hub, monkeypatch):
    """Six saved Flat frames (3 per filter), L then R, every one before the
    first light, and a metering exposure that saved nothing before each set.

    RED under "stage call skipped" (``await self._dusk_flats()`` taken out of
    ``_run``): no Flat frame is taken, so the list is empty. RED under "stage
    after the lights" (the call moved to after ``_run_scheduled``): the
    scheduler marker precedes the flats."""
    _, w = await _run(sim_hub, _plan(_flats()), monkeypatch)
    assert w.saved_flats() == ["L", "L", "L", "R", "R", "R"], w.events
    (marker,) = w.at("scheduled")
    last_flat = max(i for i, e in enumerate(w.events)
                    if e[0] == "capture" and e[1] == "Flat")
    assert last_flat < marker, "a flat was taken after the first light"
    metering = [e for e in w.events
                if e[0] == "capture" and e[1] == "Flat" and not e[2]]
    assert metering, "the exposure was not metered to the ADU target"


async def test_lamp_lit_with_the_cover_shut_and_off_after_each_filter(
        sim_hub, monkeypatch):
    """The panel goes on at the connected panel's middle level (the plan names
    none), only once the cover is shut (#194), and is off with the cover shut
    again before the next filter starts."""
    _, w = await _run(sim_hub, _plan(_flats()), monkeypatch)
    ons = [e for e in w.events if e[0] == "lamp_on"]
    assert len(ons) == 2, w.events
    mid = sim_hub.calibrator.max_brightness // 2
    assert all(e[1] == mid for e in ons), ons
    assert all(e[2] == "closed" for e in ons), (
        f"the lamp was lit with the cover not shut: {ons}")
    # per filter: lamp on ... lamp off, cover closed, and only then the next
    # (up to the first light: the wind-down turns the lamp off once more)
    before = w.events[:w.at("scheduled")[0]]
    seq = [e[0] if e[0] != "cover" else f"cover:{e[1]}"
           for e in before if e[0] in ("lamp_on", "lamp_off", "cover")]
    assert seq[:2] == ["cover:closed", "lamp_on"], seq
    first_off = seq.index("lamp_off")
    assert seq[first_off + 1] == "cover:closed", seq
    assert seq.count("lamp_off") == 2 and seq[-1] == "cover:closed", seq


async def test_a_cover_that_was_open_is_open_for_the_first_light(
        sim_hub, monkeypatch):
    """The stage restores what it found. The flats leave the cover shut
    (``_panel_off_safe``); a cover that was open before them is re-opened, so
    the first light is not taken through a shut cover.

    RED under "cover not restored" (the stage's re-open removed): the marker
    finds the cover 'closed'."""
    await sim_hub.open_cover()
    _, w = await _run(sim_hub, _plan(_flats()), monkeypatch)
    (marker,) = [e for e in w.events if e[0] == "scheduled"]
    assert marker[1] == "open", (
        f"the first light would be taken through a shut cover: {marker}")
    assert marker[2] == "off", f"the lamp is lit at the first light: {marker}"


async def test_a_cover_that_was_shut_is_left_shut(sim_hub, monkeypatch):
    """Control: the stage does not open a cover it found shut. Opening it for
    the night is #601's, with the roof."""
    assert await Watch.cover(sim_hub) == "closed"
    _, w = await _run(sim_hub, _plan(_flats()), monkeypatch)
    (marker,) = [e for e in w.events if e[0] == "scheduled"]
    assert marker[1] == "closed", marker


# ---------------------------------------------------------- once per night

async def test_a_restart_finds_fresh_flats_and_shoots_none(
        sim_hub, tmp_path, monkeypatch):
    """The calibration queue's if-stale rule, asked of the same health matrix
    the Calibration Matrix panel renders. A second ENGINE the same night (a
    restart; the per-night latch is the first engine's memory and is gone)
    finds a full, fresh set in the library for each filter and shoots nothing.

    RED under "library skip dropped" (``_dusk_flats_fresh`` answering 'not fresh'): the second run takes its six flats again."""
    monkeypatch.setattr(sim_hub, "master_library",
                        CalibrationLibrary(lambda: tmp_path), raising=False)
    _, first = await _run(sim_hub, _plan(_flats()), monkeypatch)
    assert first.saved_flats() == ["L", "L", "L", "R", "R", "R"]
    _, second = await _run(sim_hub, _plan(_flats()), monkeypatch,
                           engine=SequenceEngine(sim_hub))
    assert second.saved_flats() == [], (
        f"the library held fresh flats and they were shot again: "
        f"{second.events}")


async def test_the_same_engine_runs_the_stage_once_a_night(
        sim_hub, monkeypatch):
    """A resume the same night on the same engine does not reshoot, even with
    no library to ask."""
    eng = SequenceEngine(sim_hub)
    _, first = await _run(sim_hub, _plan(_flats()), monkeypatch, engine=eng)
    assert len(first.saved_flats()) == 6
    _, again = await _run(sim_hub, _plan(_flats()), monkeypatch, engine=eng)
    assert again.saved_flats() == [], again.events


# ----------------------------------------------------------- no flat source

async def test_without_a_flat_panel_it_says_so_once_and_the_night_goes_on(
        sim_hub, monkeypatch):
    lines = _log_spy(monkeypatch)
    del sim_hub.devices["covercalibrator"]
    eng = SequenceEngine(sim_hub)
    reached: list[int] = []

    async def scheduled(plan):
        reached.append(1)

    monkeypatch.setattr(eng, "_run_scheduled", scheduled)
    for n in (1, 2):
        eng.start(_plan(_flats()))
        await eng._task
        assert len(reached) == n, (
            "the scheduler did not run: a missing panel stopped the night")
    said = [m for lv, m in lines
            if "flat panel" in m and "DUSK FLATS" in m]
    assert len(said) == 1 and "goes on" in said[0], (
        f"it should say once that no flat panel is connected: {said}")


# ------------------------------------------------------------ failure modes

async def test_a_safety_abort_mid_flats_ends_the_night(sim_hub, monkeypatch):
    """An unsafe trip during the flats is the night's end, not a flats hiccup:
    the run is aborted 'unsafe', the scheduler is never reached, and the lamp
    is off with the cover shut.

    RED under "SafetyAbort swallowed" (``except SafetyAbort: raise`` removed
    from the stage, so its generic handler eats it): the scheduler runs."""
    eng = SequenceEngine(sim_hub)
    calls = {"n": 0}
    real_gate = eng._safety_gate

    async def gate(*, context, target=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise SafetyAbort("test: rain on the second flat")
        return await real_gate(context=context, target=target)

    monkeypatch.setattr(eng, "_safety_gate", gate)
    _, w = await _run(sim_hub, _plan(_flats()), monkeypatch, engine=eng)
    assert not w.at("scheduled"), "the night went on after an unsafe trip"
    assert eng.state.get("end_reason") == "unsafe", eng.state
    assert (await sim_hub.calibrator_status())["state"] == "off"
    assert await Watch.cover(sim_hub) == "closed"


async def test_any_other_failure_does_not_stop_the_night(sim_hub, monkeypatch):
    """A flats hiccup (a camera error on one frame) is logged and the night
    goes on to its lights, with the cover back as it was and the lamp off."""
    lines = _log_spy(monkeypatch)
    await sim_hub.open_cover()
    eng = SequenceEngine(sim_hub)

    async def boom(ti, target):
        raise RuntimeError("camera said no")

    monkeypatch.setattr(eng, "_run_calibration", boom)
    _, w = await _run(sim_hub, _plan(_flats()), monkeypatch, engine=eng)
    (marker,) = [e for e in w.events if e[0] == "scheduled"]
    assert marker[1] == "open" and marker[2] == "off", marker
    assert any(lv == "warning" and "DUSK FLATS" in m and "camera said no" in m
               for lv, m in lines), lines


async def test_a_failure_before_the_first_frame_does_not_stop_the_night(
        sim_hub, monkeypatch):
    """The same promise for the stage's planning: a wheel that cannot say its
    filters is a flats problem, not a reason to lose the night.

    RED under "failures stop the night" (the stage's ``except Exception``
    made ``except ZeroDivisionError``): the KeyError ends the run."""
    eng = SequenceEngine(sim_hub)

    def boom(df):
        raise KeyError("wheel")

    monkeypatch.setattr(eng, "_dusk_flat_recipes", boom)
    _, w = await _run(sim_hub, _plan(_flats()), monkeypatch, engine=eng)
    assert len(w.at("scheduled")) == 1 and w.saved_flats() == [], w.events


# --------------------------------------------------- the methods not run yet

@pytest.mark.parametrize("method", ["cap", "sky"])
async def test_the_other_methods_are_carried_and_reported_not_run(
        sim_hub, monkeypatch, method):
    lines = _log_spy(monkeypatch)
    _, w = await _run(sim_hub, _plan(_flats(method=method)), monkeypatch)
    assert w.saved_flats() == [] and not w.at("lamp_on"), w.events
    assert len(w.at("scheduled")) == 1
    said = [m for lv, m in lines if "DUSK FLATS" in m and "not run yet" in m]
    assert len(said) == 1, lines


# ------------------------------------------------------ never after a light

async def test_it_never_runs_once_a_light_has_been_taken_tonight(
        sim_hub, monkeypatch):
    """A restart mid-night, after lights, is astronomical darkness with a
    sky to image: no flats then. The ledger knows (a session frame of a
    LIGHT step stamped tonight); a light stamped on another night, or a frame
    of a calibration step, does not count."""
    plan = _plan(_flats())
    light_step = plan.targets[0].steps[0]
    eng = SequenceEngine(sim_hub)
    eng.plan = plan

    class _Session:
        frames: list = []

    eng._session = _Session()
    assert eng._lights_taken_tonight() is False
    _Session.frames = [SessionFrame(ts=time.time() - 3 * 86400,
                                    step_id=light_step.id)]
    assert eng._lights_taken_tonight() is False, "another night's light"
    _Session.frames = [SessionFrame(ts=time.time(), step_id="a-flat-step")]
    assert eng._lights_taken_tonight() is False, "not a light step"
    _Session.frames = [SessionFrame(ts=time.time(), step_id=light_step.id)]
    assert eng._lights_taken_tonight() is True

    # and the stage honours it: nothing is shot
    w = Watch(sim_hub, eng, monkeypatch)
    await eng._dusk_flats()
    assert w.saved_flats() == [], w.events


# ------------------------------------------------------ what the flats match

async def test_flats_are_shot_at_the_lights_gain_and_binning(
        sim_hub, monkeypatch):
    """A flat is matched on gain and binning (``flat_matches``), so a set shot
    at the engine's defaults would calibrate nothing."""
    lights = _lights(("L", "R"), gain=125, binning=2)
    _, w = await _run(sim_hub, _plan(_flats(), lights=lights), monkeypatch)
    flats = [e for e in w.events if e[0] == "capture" and e[1] == "Flat"]
    assert flats, w.events
    assert {(e[4], e[5]) for e in flats} == {(125, 2)}, (
        f"the flats were not shot at the lights' gain and binning: "
        f"{sorted({(e[4], e[5]) for e in flats})}")


async def test_all_in_wheel_shoots_every_light_path_filter(
        sim_hub, monkeypatch):
    """'All in wheel' (filters None): the wheel's own names, in wheel order,
    the blackout slot left out."""
    _, w = await _run(sim_hub, _plan(_flats(filters=None, count=1)),
                      monkeypatch)
    fw = sim_hub.devices["filterwheel"]
    expected = [n for i, n in enumerate(fw.filter_names)
                if not fw.is_opaque(i)]
    assert w.saved_flats() == expected, w.saved_flats()


async def test_a_filter_the_wheel_lacks_is_skipped_with_a_warning(
        sim_hub, monkeypatch):
    lines = _log_spy(monkeypatch)
    _, w = await _run(sim_hub,
                      _plan(_flats(filters=["L", "NoSuchFilter"], count=1)),
                      monkeypatch)
    assert w.saved_flats() == ["L"], w.events
    assert any("NoSuchFilter" in m and "wheel" in m for lv, m in lines), lines
