# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 4.4 (#1126 C-20): the observatory envelope. Open the roof and the dust
cover before the first light, bind the dome to the mount, and close the roof
only over a mount confirmed parked, whatever the state of the mount's link.

Members: #601 (open before the first slew, at run start and in ResumeArm's
recovery), #602 (bind the dome), #893 (ruling D-B: no close without a
confirmed park), #657 (the unsafe close and the flow graph), #192 (the copy).

Four groups:

* ``close_observatory`` against a mount whose link is down (#893, D-B);
* ``open_for_the_night`` and ``bind_dome`` on their own (#601, #602);
* the engine's run start, on a sim rig with and without actuators;
* ResumeArm's recovery ladder, and the unsafe close of a flow with no DOME
  CONTROL node over a mount whose link is down.

NAMED MUTANT (the plan row's): the run start skips the open
(``SequenceEngine._open_the_observatory`` made to return at once). The sim
night's first slew then happens under a closed cover, and
``test_a_sim_night_opens_the_cover_before_the_first_slew`` fails:

    AssertionError: the cover was closed when the mount first slewed

Further mutants, each run from a byte backup of the file it changes and
restored byte for byte, are named in the docstring of the test that turns red.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.config as config_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.config import ConfigStore, SafetyConfig, Site
from astrodeck.devices.base import (
    CoverState, DeviceError, DomeShutterState, DomePolicy, SafetyReading)
from astrodeck.devices.sim import SimRotatingDome, build_sim_rig
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.hub import Hub
from astrodeck.sequence import (
    ExposureStep, SequenceEngine, SequencePlan, Target)
from astrodeck.sequence import roof as roof_mod
from astrodeck.sequence.roof import (
    bind_dome, close_observatory, open_for_the_night)
from astrodeck.sequence.session import Session

# Fixtures and helpers the resume ladder's own tests already keep. The fixture
# is a real sim hub with a made-up configured site.
from test_resume_arm import (  # noqa: F401  (sim_hub is a fixture)
    NGC604_DEC, NGC604_RA, _plan_with_floor, _spy_slew_limits,
    _trust_focus, _when_altitude_between, sim_hub as resume_hub)


def _spy():
    logs: list[tuple] = []
    return logs, (lambda level, msg, source: logs.append((level, msg, source)))


def _paged(logs) -> bool:
    return any(lvl == "error" and "REFUSED" in msg for lvl, msg, _ in logs)


# ============================================================ #893, ruling D-B

class _CountingRoof:
    """A roll-off roof that records every command it is given. It needs a
    parked tube (the fail-safe default), starts open, and would crush the
    mount if told to close over it, as the sim roof does."""

    requires_park_before_close = True
    name = "counting roof"

    def __init__(self, rig):
        self.rig = rig
        self.state = DomeShutterState.OPEN
        self.closes = 0

    async def is_closed(self):
        return self.state is DomeShutterState.CLOSED

    async def shutter_state(self):
        return self.state

    async def close_shutter(self):
        self.closes += 1
        assert self.rig.parked, "the roof was commanded to close over the tube"
        self.state = DomeShutterState.CLOSED


def _rig_with_link_down(*, parked: bool):
    """The sim mount with its link down (not connected, and not in doubt: the
    state a serial mount is left in), and a roof that records its commands."""
    rig = build_sim_rig()
    tel = rig["telescope"]
    rig["_rig"].parked = parked
    tel.connected = False
    tel.link_lost = False
    reopened: list[int] = []
    real_connect = tel.connect

    async def counting_connect():
        reopened.append(1)
        await real_connect()

    tel.connect = counting_connect
    return rig, tel, _CountingRoof(rig["_rig"]), reopened


async def test_a_roof_is_not_closed_over_a_mount_the_link_cannot_confirm():
    """THE RULING, whole. The link is down, the reopen fails: the roof is not
    commanded to close, the refusal is logged at error level (the page), and
    the answer is False.

    MUTANT D1 "the connected short-circuit" (the guard in
    ``close_observatory`` made ``if requires_park and telescope is not None
    and is_present(telescope):`` again, with no reopen): the roof closes,
    ``assert roof.closes == 0`` RED with 1.
    """
    rig, tel, roof, reopened = _rig_with_link_down(parked=True)

    async def dead_link():
        reopened.append(1)
        raise DeviceError("the serial port is gone")

    tel.connect = dead_link
    logs, log = _spy()

    assert await close_observatory(roof, tel, log=log) is False

    assert roof.closes == 0, "the roof closed over a mount nobody could ask"
    assert roof.state is DomeShutterState.OPEN
    assert _paged(logs), logs
    assert any("link" in msg and "park" in msg for _l, msg, _s in logs), logs
    assert reopened == [1], "the link is reopened once, not retried"


async def test_a_dropped_link_is_reopened_once_and_the_park_is_read():
    """The link is down, the reopen works and the mount reads parked: the roof
    closes. The only way the roof closes with the link down is by asking.

    MUTANT D2 "no reopen" (the ``telescope.connect()`` call removed): the link
    stays down and the guard refuses, so the roof is never closed:
    ``assert await close_observatory(...) is True`` RED.
    """
    rig, tel, roof, reopened = _rig_with_link_down(parked=True)
    logs, log = _spy()

    assert await close_observatory(roof, tel, log=log) is True

    assert reopened == [1]
    assert roof.closes == 1
    assert not _paged(logs), logs


async def test_a_reopened_link_over_an_unparked_mount_still_refuses():
    """The link comes back and says the tube is not parked: the ordinary
    refusal, the roof is not commanded to close."""
    rig, tel, roof, reopened = _rig_with_link_down(parked=False)
    logs, log = _spy()

    assert await close_observatory(roof, tel, log=log) is False

    assert reopened == [1]
    assert roof.closes == 0
    assert _paged(logs), logs


async def test_a_reopen_that_never_returns_is_bounded_and_refuses():
    """A reopen that hangs is bounded: the guard gives up on it, refuses and
    pages, and does not hold the wind-down for ever."""
    rig, tel, roof, reopened = _rig_with_link_down(parked=True)

    async def hung():
        reopened.append(1)
        await asyncio.sleep(3600)

    tel.connect = hung
    logs, log = _spy()

    ok = await asyncio.wait_for(
        close_observatory(roof, tel, log=log, reopen_timeout_s=0.05), 5.0)

    assert ok is False
    assert roof.closes == 0
    assert _paged(logs), logs


async def test_a_mount_that_reopens_not_knowing_where_it_points_refuses():
    """An AM5 that lost power reads its home pole on reconnect, and a mount
    that reads "parked" there is no proof the tube is. The reopened link does
    not confirm a park while the driver says its position is unknown."""
    rig, tel, roof, reopened = _rig_with_link_down(parked=True)
    real_connect = tel.connect

    async def reopen_and_forget():
        await real_connect()
        tel.mark_position_unknown("test: reconnected at the home pole")

    tel.connect = reopen_and_forget
    logs, log = _spy()

    assert await close_observatory(roof, tel, log=log) is False

    assert roof.closes == 0
    assert _paged(logs), logs


async def test_a_roof_that_needs_no_parked_tube_closes_without_asking_the_link():
    """A rotating dome whose shutter clears the tube at any position opts out:
    the link is neither reopened nor asked, as before."""
    rig, tel, roof, reopened = _rig_with_link_down(parked=False)
    roof.requires_park_before_close = False
    logs, log = _spy()

    class _Clear(_CountingRoof):
        async def close_shutter(self):
            self.closes += 1
            self.state = DomeShutterState.CLOSED

    clear = _Clear(rig["_rig"])
    clear.requires_park_before_close = False

    assert await close_observatory(clear, tel, log=log) is True
    assert reopened == []
    assert clear.closes == 1


async def test_a_roof_that_is_already_closed_does_not_touch_the_link():
    rig, tel, roof, reopened = _rig_with_link_down(parked=False)
    roof.state = DomeShutterState.CLOSED
    logs, log = _spy()

    assert await close_observatory(roof, tel, log=log) is True
    assert reopened == []
    assert roof.closes == 0


async def test_a_rig_with_no_mount_closes_its_roof():
    """No telescope in the rig is nothing to crush: unchanged."""
    rig = build_sim_rig()
    roof = _CountingRoof(rig["_rig"])
    rig["_rig"].parked = True
    logs, log = _spy()

    assert await close_observatory(roof, None, log=log) is True
    assert roof.closes == 1


# ============================================ #601 and #602, the devices alone

async def _closed_envelope():
    """A connected sim rig whose roof and cover are both shut."""
    rig = build_sim_rig()
    dome, cover = rig["dome"], rig["covercalibrator"]
    await dome.connect()
    await cover.connect()
    dome._state = DomeShutterState.CLOSED
    cover._cover = CoverState.CLOSED
    return rig, dome, cover


async def test_the_roof_and_the_cover_are_opened_and_confirmed():
    rig, dome, cover = await _closed_envelope()
    logs, log = _spy()

    reason = await open_for_the_night(dome, cover, log=log)

    assert reason is None, reason
    assert await dome.shutter_state() is DomeShutterState.OPEN
    assert await cover.get_cover_state() is CoverState.OPEN


async def test_an_open_envelope_is_not_commanded_again():
    """Re-commanding an open roof sends some drivers through another full
    travel, and a cover already open needs no command: the second call of a
    night (a resume after a run) costs nothing."""
    rig, dome, cover = await _closed_envelope()
    await open_for_the_night(dome, cover, log=_spy()[1])
    commands: list[str] = []
    real_roof, real_cover = dome.open_shutter, cover.open_cover

    async def roof_spy():
        commands.append("roof")
        await real_roof()

    async def cover_spy():
        commands.append("cover")
        await real_cover()

    dome.open_shutter, cover.open_cover = roof_spy, cover_spy

    assert await open_for_the_night(dome, cover, log=_spy()[1]) is None
    assert commands == []


async def test_a_rig_without_actuators_is_left_alone():
    """No roof and no cover, or a panel with no cover: nothing is asked and
    nothing is refused."""
    rig = build_sim_rig()
    cover = rig["covercalibrator"]
    await cover.connect()
    cover.has_cover = False
    asked: list[str] = []

    async def spy():
        asked.append("cover")

    cover.open_cover = spy
    assert await open_for_the_night(None, cover, log=_spy()[1]) is None
    assert await open_for_the_night(None, None, log=_spy()[1]) is None
    assert asked == []


async def test_a_roof_that_will_not_open_is_refused_by_name():
    rig, dome, cover = await _closed_envelope()

    async def jammed():
        dome._state = DomeShutterState.ERROR

    dome.open_shutter = jammed
    logs, log = _spy()

    reason = await open_for_the_night(dome, cover, log=log)

    assert reason is not None and dome.name in reason, reason
    assert "roof" in reason
    # The cover is not touched once the roof has refused: the night is not
    # going to start, and a half-opened observatory is not a state to make.
    assert await cover.get_cover_state() is CoverState.CLOSED


async def test_a_cover_that_will_not_open_is_refused_by_name():
    rig, dome, cover = await _closed_envelope()

    async def stuck():
        return None

    cover.open_cover = stuck
    logs, log = _spy()

    reason = await open_for_the_night(
        dome, cover, log=log, cover_timeout_s=0.1)

    assert reason is not None and cover.name in reason, reason
    assert "cover" in reason


async def test_a_cover_that_raises_is_refused_by_name():
    rig, dome, cover = await _closed_envelope()

    async def broken():
        raise DeviceError("the cover motor is not answering")

    cover.open_cover = broken
    reason = await open_for_the_night(dome, cover, log=_spy()[1])

    assert reason is not None and cover.name in reason, reason
    assert "not answering" in reason


async def test_the_safety_check_runs_only_when_the_roof_must_move():
    """The caller's weather check is asked before the roof travels and not
    for a roof that is already open (the cover opening is no weather risk)."""
    rig, dome, cover = await _closed_envelope()
    asked: list[str] = []

    async def check():
        asked.append("weather")

    assert await open_for_the_night(
        dome, cover, log=_spy()[1], before_roof=check) is None
    assert asked == ["weather"]

    asked.clear()
    cover._cover = CoverState.CLOSED
    assert await open_for_the_night(
        dome, cover, log=_spy()[1], before_roof=check) is None
    assert asked == [], "an open roof was weather-checked for nothing"


async def test_an_unsafe_verdict_from_the_check_is_not_swallowed():
    """The check raises the engine's own abort for rain: that is the run's
    ending, not a refusal this function words."""
    rig, dome, cover = await _closed_envelope()

    class Rain(Exception):
        pass

    async def check():
        raise Rain()

    with pytest.raises(Rain):
        await open_for_the_night(
            dome, cover, log=_spy()[1], before_roof=check)
    assert await dome.shutter_state() is DomeShutterState.CLOSED


async def test_a_rotating_dome_is_bound_to_the_mount():
    rig = build_sim_rig()
    dome = SimRotatingDome(rig["_rig"])
    await dome.connect()
    logs, log = _spy()

    assert await bind_dome(dome, log=log) is None

    assert await dome.get_bound() is True
    assert any("bound" in msg for _l, msg, _s in logs), logs


async def test_a_roll_off_roof_has_nothing_to_bind_and_says_so():
    rig = build_sim_rig()
    await rig["dome"].connect()
    logs, log = _spy()

    assert await bind_dome(rig["dome"], log=log) is None
    assert any("no bindable azimuth" in msg for _l, msg, _s in logs), logs


async def test_a_dome_that_claims_to_bind_and_refuses_is_a_refusal():
    """An OTA that spends the night photographing the dome wall while every
    readout says bound: ``set_bound`` raising is the run's refusal, named."""
    rig = build_sim_rig()
    dome = SimRotatingDome(rig["_rig"])
    await dome.connect()

    async def refuse(on):
        raise DeviceError("the dome controller refused")

    dome.set_bound = refuse
    reason = await bind_dome(dome, log=_spy()[1])

    assert reason is not None and dome.name in reason, reason


async def test_a_manual_policy_leaves_the_azimuth_alone():
    rig = build_sim_rig()
    dome = SimRotatingDome(rig["_rig"])
    await dome.connect()
    policy = DomePolicy(bind_to_mount=False)

    assert await bind_dome(dome, log=_spy()[1], policy=policy) is None
    assert await dome.get_bound() is False


# ================================================== the engine's run start

@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Test", latitude=40.0, longitude=-74.0,
                        is_default=False))
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(engine_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(engine_mod, "SAFETY_PAUSE_POLL_S", 0.05)
    monkeypatch.setattr(engine_mod, "SAFETY_SEED_WAIT_S", 0.5)
    return store


@pytest.fixture
async def sim_hub(temp_store, monkeypatch):
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _safety(store, **kw) -> None:
    store.set_safety(SafetyConfig(**kw))


def _light_plan(**overrides) -> SequencePlan:
    defaults = dict(
        name="envelope",
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter="L", exposure_s=0.05, gain=100,
                                count=2)])],
        guide=False, dither_every=0, autofocus_every=0, meridian_flip=False)
    return SequencePlan(**(defaults | overrides))


def _dark_plan() -> SequencePlan:
    return SequencePlan(
        name="darks", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(
            name="Darks", ra_hours=0.0, dec_deg=0.0, calibration=True,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter="L", exposure_s=0.05, gain=100,
                                count=2, frame_type="Dark")])])


async def _wait_for(predicate, timeout=40.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def _record_first_slew(hub) -> dict:
    """What the roof and the cover read the moment the mount first slews."""
    seen: dict = {}
    tel = hub.devices["telescope"]
    real = tel.slew

    async def slew(ra, dec):
        if "cover" not in seen:
            cc = hub.devices.get("covercalibrator")
            dome = hub.devices.get("dome")
            seen["cover"] = (await cc.get_cover_state()) if cc else None
            seen["roof"] = (await dome.shutter_state()) if dome else None
        await real(ra, dec)

    tel.slew = slew
    return seen


async def test_a_sim_night_opens_the_cover_before_the_first_slew(
        sim_hub, temp_store):
    """THE PLAN ROW'S MUTANT. The sim cover starts shut and every wind-down
    shuts it again; nothing opened it for light frames.

    MUTANT E1 "skip the open" (``SequenceEngine._open_the_observatory``
    returns at once): the first slew reads CLOSED, RED -
        AssertionError: the cover was closed when the mount first slewed
    """
    _safety(temp_store, enabled=False, min_alt_deg=0.0)
    cover = sim_hub.devices["covercalibrator"]
    assert await cover.get_cover_state() is CoverState.CLOSED, "premise"
    seen = _record_first_slew(sim_hub)

    engine = SequenceEngine(sim_hub)
    engine.start(_light_plan())
    assert await _wait_for(lambda: not engine.running), engine.state

    assert engine.state.get("state") == "complete", engine.state
    assert "cover" in seen, "the mount never slewed"
    assert seen["cover"] is CoverState.OPEN, (
        "the cover was closed when the mount first slewed")


async def test_a_closed_roof_is_opened_before_the_first_slew(
        sim_hub, temp_store):
    _safety(temp_store, enabled=False, min_alt_deg=0.0)
    dome = sim_hub.devices["dome"]
    dome._state = DomeShutterState.CLOSED
    seen = _record_first_slew(sim_hub)

    engine = SequenceEngine(sim_hub)
    engine.start(_light_plan())
    assert await _wait_for(lambda: not engine.running), engine.state

    assert engine.state.get("state") == "complete", engine.state
    assert seen["roof"] is DomeShutterState.OPEN, (
        "the roof was not open when the mount first slewed")


async def test_a_closed_roof_waits_for_a_safe_sky_before_it_opens(
        sim_hub, temp_store):
    """Rain at the start of the night: the roof is not opened into it. The
    unsafe verdict is the run's ordinary one (here an abort), reached before
    the roof is commanded."""
    _safety(temp_store, enabled=True, on_unsafe="abort_park_warm",
            unsafe_consecutive=1, max_pause_min=0, min_alt_deg=0.0)
    dome = sim_hub.devices["dome"]
    dome._state = DomeShutterState.CLOSED
    opened: list[int] = []
    real = dome.open_shutter

    async def counting():
        opened.append(1)
        await real()

    dome.open_shutter = counting
    mon = sim_hub.devices["safety"]
    mon.force_unsafe("rain")
    sim_hub._safety_reading = SafetyReading(
        is_safe=False, reason="rain", source="Sim Safety Monitor")

    engine = SequenceEngine(sim_hub)
    engine.start(_light_plan())
    assert await _wait_for(lambda: not engine.running), engine.state

    assert engine.state.get("end_reason") == "unsafe", engine.state
    assert opened == [], "the roof was opened into rain"


async def test_a_rig_without_actuators_runs_as_it_always_did(
        sim_hub, temp_store):
    """No roof, and a panel with no cover: the run asks neither of anything
    and its ending is the same."""
    _safety(temp_store, enabled=False, min_alt_deg=0.0)
    sim_hub.devices.pop("dome")
    cover = sim_hub.devices["covercalibrator"]
    cover.has_cover = False
    asked: list[str] = []

    async def spy():
        asked.append("cover")

    cover.open_cover = spy

    engine = SequenceEngine(sim_hub)
    engine.start(_light_plan())
    assert await _wait_for(lambda: not engine.running), engine.state

    assert engine.state.get("state") == "complete", engine.state
    assert asked == []


async def test_a_rig_with_no_panel_and_no_roof_runs_as_it_always_did(
        sim_hub, temp_store):
    _safety(temp_store, enabled=False, min_alt_deg=0.0)
    sim_hub.devices.pop("dome")
    sim_hub.devices.pop("covercalibrator")

    engine = SequenceEngine(sim_hub)
    engine.start(_light_plan())
    assert await _wait_for(lambda: not engine.running), engine.state

    assert engine.state.get("state") == "complete", engine.state


async def test_a_night_of_darks_does_not_open_the_cover(sim_hub, temp_store):
    """A dark is shot with the cover shut: a plan with no light frames in it
    has no use for an open sky and does not open one."""
    _safety(temp_store, enabled=False, min_alt_deg=0.0)
    cover = sim_hub.devices["covercalibrator"]
    dome = sim_hub.devices["dome"]
    asked: list[str] = []
    real_cover, real_roof = cover.open_cover, dome.open_shutter

    async def cover_spy():
        asked.append("cover")
        await real_cover()

    async def roof_spy():
        asked.append("roof")
        await real_roof()

    cover.open_cover, dome.open_shutter = cover_spy, roof_spy
    dome._state = DomeShutterState.CLOSED

    engine = SequenceEngine(sim_hub)
    engine.start(_dark_plan())
    assert await _wait_for(lambda: not engine.running), engine.state

    assert asked == [], asked


async def test_a_cover_that_will_not_open_stops_the_run_before_it_slews(
        sim_hub, temp_store, monkeypatch):
    """Refuse with a reason naming the device. The mount is never slewed to
    shoot through a cover that is not open."""
    _safety(temp_store, enabled=False, min_alt_deg=0.0)
    cover = sim_hub.devices["covercalibrator"]

    async def stuck():
        return None

    cover.open_cover = stuck
    monkeypatch.setattr(roof_mod, "COVER_OPEN_TIMEOUT_S", 0.2)
    seen = _record_first_slew(sim_hub)
    engine = SequenceEngine(sim_hub)
    engine.start(_light_plan())
    assert await _wait_for(lambda: not engine.running), engine.state

    assert engine.state.get("state") == "error", engine.state
    assert cover.name in str(engine.state.get("detail")), engine.state
    assert "cover" in str(engine.state.get("detail"))
    assert seen == {}, "the mount slewed anyway"


async def test_a_roof_that_will_not_open_stops_the_run_before_it_slews(
        sim_hub, temp_store):
    _safety(temp_store, enabled=False, min_alt_deg=0.0)
    dome = sim_hub.devices["dome"]
    dome._state = DomeShutterState.CLOSED

    async def jammed():
        dome._state = DomeShutterState.ERROR

    dome.open_shutter = jammed
    seen = _record_first_slew(sim_hub)

    engine = SequenceEngine(sim_hub)
    engine.start(_light_plan())
    assert await _wait_for(lambda: not engine.running), engine.state

    assert engine.state.get("state") == "error", engine.state
    assert dome.name in str(engine.state.get("detail")), engine.state
    assert seen == {}, "the mount slewed anyway"


async def test_the_dome_is_bound_to_the_mount_when_the_run_starts(
        sim_hub, temp_store):
    """#602: a rotating dome in the rig is bound after connect, before the
    first slew, so its azimuth follows the mount through the night."""
    _safety(temp_store, enabled=False, min_alt_deg=0.0)
    rotating = SimRotatingDome(sim_hub.sim_rig)
    await rotating.connect()
    sim_hub.devices["dome"] = rotating
    bound_at_first_slew: list[bool] = []
    tel = sim_hub.devices["telescope"]
    real = tel.slew

    async def slew(ra, dec):
        bound_at_first_slew.append(await rotating.get_bound())
        await real(ra, dec)

    tel.slew = slew

    engine = SequenceEngine(sim_hub)
    engine.start(_light_plan())
    assert await _wait_for(lambda: not engine.running), engine.state

    assert engine.state.get("state") == "complete", engine.state
    assert bound_at_first_slew and bound_at_first_slew[0] is True, (
        "the dome was not bound when the mount first slewed")


async def test_a_dome_that_refuses_to_bind_stops_the_run_before_it_slews(
        sim_hub, temp_store):
    _safety(temp_store, enabled=False, min_alt_deg=0.0)
    rotating = SimRotatingDome(sim_hub.sim_rig)
    await rotating.connect()

    async def refuse(on):
        raise DeviceError("the dome controller refused")

    rotating.set_bound = refuse
    sim_hub.devices["dome"] = rotating
    seen = _record_first_slew(sim_hub)

    engine = SequenceEngine(sim_hub)
    engine.start(_light_plan())
    assert await _wait_for(lambda: not engine.running), engine.state

    assert engine.state.get("state") == "error", engine.state
    assert rotating.name in str(engine.state.get("detail")), engine.state
    assert seen == {}, "the mount slewed anyway"


# ===================================================== ResumeArm's recovery

def _record_cover_at_motion(hub, monkeypatch) -> list[tuple]:
    """The recovery ladder's motion calls, each with the cover and the roof as
    they read when it was made. Stubs, as ``test_resume_arm._record_motion``
    does, so the call is recorded instead of failing inside the ladder."""
    calls: list[tuple] = []

    def stub(label):
        async def _f(*a, **kw):
            cc = hub.devices.get("covercalibrator")
            dome = hub.devices.get("dome")
            calls.append((
                label,
                (await cc.get_cover_state()) if cc else None,
                (await dome.shutter_state()) if dome else None))
            return {}
        return _f

    monkeypatch.setattr(hub, "goto_and_center", stub("goto"))
    monkeypatch.setattr(hub, "solve_and_sync", stub("solve"))
    return calls


async def test_a_resumed_night_opens_the_roof_and_cover_in_recovery(
        resume_hub, monkeypatch):
    """#601, the red test of #192: night one ended with the cover (and here the
    roof) shut. At the next dusk the recovery ladder solves and slews; it must
    do so under an open sky.

    MUTANT R1 "skip the open in recovery" (the ``open_for_the_night`` call
    removed from ``ResumeArm._recover``): the first motion call reads CLOSED,
    RED - AssertionError: the recovery's solve ran under a shut cover or roof
    """
    hub = resume_hub
    _trust_focus(monkeypatch)
    hub.devices["dome"]._state = DomeShutterState.CLOSED
    assert await hub.devices["covercalibrator"].get_cover_state() \
        is CoverState.CLOSED, "premise"
    calls = _record_cover_at_motion(hub, monkeypatch)
    engine = SequenceEngine(hub)
    _spy_slew_limits(engine, monkeypatch)
    from astrodeck.sequence.resume_arm import ResumeArm

    t = _when_altitude_between(hub.site, NGC604_RA, NGC604_DEC, 40.0, 60.0)
    arm = ResumeArm(engine, hub, clock=lambda: t)
    refusal = await arm._recover(
        Session(name="resume", plan=_plan_with_floor(30.0)))

    assert refusal is None, refusal
    assert calls, "the ladder made no motion call"
    for label, cover, roof in calls:
        assert (cover, roof) == (CoverState.OPEN, DomeShutterState.OPEN), (
            f"the recovery's {label} ran under a shut cover or roof: "
            f"cover {cover}, roof {roof}")


async def test_recovery_refuses_and_does_not_move_when_the_cover_will_not_open(
        resume_hub, monkeypatch):
    hub = resume_hub
    _trust_focus(monkeypatch)
    cover = hub.devices["covercalibrator"]

    async def stuck():
        return None

    cover.open_cover = stuck
    monkeypatch.setattr(roof_mod, "COVER_OPEN_TIMEOUT_S", 0.2)
    calls = _record_cover_at_motion(hub, monkeypatch)
    engine = SequenceEngine(hub)
    _spy_slew_limits(engine, monkeypatch)
    from astrodeck.sequence.resume_arm import ResumeArm

    t = _when_altitude_between(hub.site, NGC604_RA, NGC604_DEC, 40.0, 60.0)
    arm = ResumeArm(engine, hub, clock=lambda: t)
    refusal = await arm._recover(
        Session(name="resume", plan=_plan_with_floor(30.0)))

    assert refusal is not None and cover.name in refusal, refusal
    assert calls == [], f"the ladder moved anyway: {calls}"


async def test_recovery_of_a_session_with_no_light_frames_opens_nothing(
        resume_hub, monkeypatch):
    """A session of darks has no use for an open sky, in recovery as at run
    start: nothing is commanded to open."""
    hub = resume_hub
    _trust_focus(monkeypatch)
    hub.devices["dome"]._state = DomeShutterState.CLOSED
    opened: list[str] = []
    for role, name in (("dome", "open_shutter"),
                       ("covercalibrator", "open_cover")):
        async def spy(role=role):
            opened.append(role)
        monkeypatch.setattr(hub.devices[role], name, spy)
    engine = SequenceEngine(hub)
    _spy_slew_limits(engine, monkeypatch)
    from astrodeck.sequence.resume_arm import ResumeArm

    arm = ResumeArm(engine, hub, clock=lambda: 1_700_000_000.0)
    await arm._recover(Session(name="darks", plan=_dark_plan()))

    assert opened == [], opened


async def test_recovery_on_a_rig_without_actuators_is_unchanged(
        resume_hub, monkeypatch):
    hub = resume_hub
    _trust_focus(monkeypatch)
    hub.devices.pop("dome")
    hub.devices.pop("covercalibrator")
    calls = _record_cover_at_motion(hub, monkeypatch)
    engine = SequenceEngine(hub)
    _spy_slew_limits(engine, monkeypatch)
    from astrodeck.sequence.resume_arm import ResumeArm

    t = _when_altitude_between(hub.site, NGC604_RA, NGC604_DEC, 40.0, 60.0)
    arm = ResumeArm(engine, hub, clock=lambda: t)
    refusal = await arm._recover(
        Session(name="resume", plan=_plan_with_floor(30.0)))

    assert refusal is None, refusal
    assert [c[0] for c in calls] == ["solve", "goto"], calls


# ============================================ #657 and #893 through the engine

def _flow_with_no_dome_control_node() -> FlowGraph:
    def node(nid, ntype, x=0.0, **params):
        return FlowNode(id=nid, type=ntype, x=x, y=0.0, params=params)

    return FlowGraph(
        nodes=[node("t", "target", name="w4.4 dome test",
                    ra="05h 35m 17s", dec="-05 23 28"),
               node("c", "capture", x=100, exposure=0.05, count=1,
                    filter="L", gain=100)],
        edges=[FlowEdge(**{"from": "t", "fromPort": "target",
                           "to": "c", "toPort": "run"})])


def _compiled_plan() -> SequencePlan:
    graph = _flow_with_no_dome_control_node()
    compiled = compile_plan(graph, "w4.4 dome independence")
    assert "dome" not in (compiled.get("automation") or {}), "premise"
    plan, _unmapped = to_sequence_plan(compiled, graph)
    return plan.model_copy(update={
        "targets": [t.model_copy(update={"center": False,
                                         "autofocus_first": False})
                    for t in plan.targets]})


async def _unsafe_trip(hub, store, *, mount_link: str):
    """An unsafe verdict on a flow with no DOME CONTROL node, with the flag
    that closes the roof on, and the mount's link in the given state at the
    moment the wind-down parks: "up", "reopens" (down until reopened) or
    "dead" (down and the reopen fails)."""
    _safety(store, enabled=True, on_unsafe="pause", unsafe_consecutive=1,
            resume_safe_consecutive=1, max_pause_min=0, min_alt_deg=0.0,
            close_dome_on_unsafe=True)
    hub.devices["safety"].force_unsafe("cloud sensor")
    hub._safety_reading = SafetyReading(
        is_safe=False, reason="cloud sensor", source="Sim Safety Monitor")
    tel = hub.devices["telescope"]
    dome = hub.devices["dome"]
    closes: list[int] = []
    real_close = dome.close_shutter

    async def counting_close():
        closes.append(1)
        await real_close()

    dome.close_shutter = counting_close
    real_connect = tel.connect
    real_park = tel.park

    async def park_with_the_link_down():
        # The link drops as the park runs, after the safety verdict: the
        # wind-down sees a mount that reads not connected.
        await real_park()
        tel.connected = False
        tel.link_lost = False

    async def reopen():
        if mount_link == "dead":
            raise DeviceError("the serial port is gone")
        await real_connect()

    if mount_link != "up":
        tel.park = park_with_the_link_down
        tel.connect = reopen
    engine = SequenceEngine(hub)
    engine.start(_compiled_plan())
    assert await _wait_for(lambda: not engine.running), engine.state
    return engine, dome, closes


async def test_an_unsafe_trip_closes_the_roof_of_a_flow_with_no_dome_node(
        sim_hub, temp_store):
    """#657's question, answered at flow level through the real compile: with
    ``close_dome_on_unsafe`` on, a connected roof is closed after the park
    though the flow has no DOME CONTROL node. The close rides the rig, not the
    graph. (Whether the flag should default on is the owner's open question
    (b) on #657 and is not decided here.)"""
    engine, dome, closes = await _unsafe_trip(
        sim_hub, temp_store, mount_link="up")

    assert engine.state.get("end_reason") == "unsafe", engine.state
    assert await dome.shutter_state() is DomeShutterState.CLOSED
    assert closes == [1]


async def test_an_unsafe_trip_closes_after_the_link_was_reopened(
        sim_hub, temp_store):
    engine, dome, closes = await _unsafe_trip(
        sim_hub, temp_store, mount_link="reopens")

    assert await sim_hub.require("telescope").is_parked()
    assert await dome.shutter_state() is DomeShutterState.CLOSED
    assert closes == [1]


async def test_an_unsafe_trip_leaves_the_roof_open_when_the_link_is_dead(
        sim_hub, temp_store, bus_lines):
    """D-B through the whole teardown: the link is down at the wind-down and
    does not come back. The roof is never commanded to close, and the refusal
    pages at error level.

    MUTANT D1 (see above) turns this red as well: the roof closes."""
    engine, dome, closes = await _unsafe_trip(
        sim_hub, temp_store, mount_link="dead")

    assert engine.state.get("end_reason") == "unsafe", engine.state
    assert closes == [], "the roof was commanded to close over an unread mount"
    assert await dome.shutter_state() is DomeShutterState.OPEN
    assert any(lvl == "error" and ("REFUSED" in msg
                                   or "CLOSE FAILED" in msg)
               for lvl, msg, _s in bus_lines), bus_lines


# ================================ #602, the DOME CONTROL node reaches the run

def _compiled_plan_with_a_dome(**params) -> SequencePlan:
    """TARGET -> CAPTURE plus a DOME CONTROL node, through the real compile."""
    graph = _flow_with_no_dome_control_node()
    graph.nodes.append(FlowNode(id="d", type="dome", x=0.0, y=100.0,
                                params=params))
    compiled = compile_plan(graph, "w4.4 dome node")
    assert "dome" in (compiled.get("automation") or {}), "premise"
    plan, _unmapped = to_sequence_plan(compiled, graph)
    return plan.model_copy(update={
        "targets": [t.model_copy(update={"center": False,
                                         "autofocus_first": False})
                    for t in plan.targets]})


def test_a_dome_node_reaches_the_plan():
    """The compiled block, the azimuth choice and the shutter timeout, is on
    the plan the engine runs and the session stores.

    MUTANT P1 "drop the field from the compile" (the ``fields["dome"] = ...``
    line of ``to_sequence_plan`` removed): ``plan.dome`` is None, RED -
        AssertionError: assert None == {'bind': False, ...}
    """
    plan = _compiled_plan_with_a_dome(timeout=45, bind="Manual")

    assert plan.dome == {"bind": False, "on_unsafe": "close",
                         "shutter_timeout_s": 45.0}
    assert DomePolicy.from_plan(plan.dome) == DomePolicy(
        bind_to_mount=False, shutter_timeout_s=45.0)


def test_a_plan_with_no_dome_node_dumps_as_it_always_did():
    """None is left out of the dump, so every plan without the node, every
    stored session and every plan golden is byte for byte what it was."""
    plan = _compiled_plan()

    assert plan.dome is None
    assert "dome" not in plan.model_dump()
    assert "dome" not in plan.model_dump(mode="json")
    assert '"dome"' not in plan.model_dump_json()
    with_one = _compiled_plan_with_a_dome(timeout=45)
    assert with_one.model_dump()["dome"]["shutter_timeout_s"] == 45.0


def test_the_session_a_resume_builds_from_carries_the_dome_block():
    """A resumed run starts from the session's stored plan: the block survives
    the round trip through the session's own JSON."""
    plan = _compiled_plan_with_a_dome(timeout=45, bind="Manual")
    session = Session(name="dome", plan=plan)

    stored = Session.model_validate_json(session.model_dump_json())

    assert stored.plan.dome == plan.dome


async def test_the_nodes_manual_choice_reaches_the_run(sim_hub, temp_store):
    """A DOME CONTROL node set to Manual leaves a rotating dome unbound; the
    default binds it (the test above).

    MUTANT P2 "the engine ignores the plan's block" (``DomePolicy.from_plan(
    plan.dome or {})`` made ``DomePolicy()`` in ``_open_the_observatory``):
    the dome is bound, RED -
        AssertionError: the dome was bound though the node says Manual
    """
    _safety(temp_store, enabled=False, min_alt_deg=0.0)
    rotating = SimRotatingDome(sim_hub.sim_rig)
    await rotating.connect()
    sim_hub.devices["dome"] = rotating

    engine = SequenceEngine(sim_hub)
    engine.start(_compiled_plan_with_a_dome(bind="Manual"))
    assert await _wait_for(lambda: not engine.running), engine.state

    assert engine.state.get("state") == "complete", engine.state
    assert await rotating.get_bound() is False, (
        "the dome was bound though the node says Manual")


async def test_the_nodes_shutter_timeout_reaches_the_run(
        sim_hub, temp_store):
    """A roof that never confirms open is given the node's timeout, not the
    120 s default: the run is refused at the node's 0.3 s, naming the roof.

    MUTANT P2 (see above) turns this red as well: the run waits the default
    and the test's bound ends it first."""
    _safety(temp_store, enabled=False, min_alt_deg=0.0)
    dome = sim_hub.devices["dome"]
    dome._state = DomeShutterState.CLOSED

    async def never_moves():
        return None

    dome.open_shutter = never_moves

    engine = SequenceEngine(sim_hub)
    engine.start(_compiled_plan_with_a_dome(timeout=0.3))
    assert await _wait_for(lambda: not engine.running, timeout=20.0), \
        engine.state

    assert engine.state.get("state") == "error", engine.state
    assert "within 0.3s" in str(engine.state.get("detail")), engine.state


async def test_the_nodes_shutter_timeout_reaches_a_resumes_recovery(
        resume_hub, monkeypatch):
    """The same block, read from the session plan by ResumeArm's recovery."""
    hub = resume_hub
    _trust_focus(monkeypatch)
    dome = hub.devices["dome"]
    dome._state = DomeShutterState.CLOSED

    async def never_moves():
        return None

    dome.open_shutter = never_moves
    calls = _record_cover_at_motion(hub, monkeypatch)
    engine = SequenceEngine(hub)
    _spy_slew_limits(engine, monkeypatch)
    from astrodeck.sequence.resume_arm import ResumeArm

    plan = _plan_with_floor(30.0).model_copy(update={
        "dome": {"bind": True, "on_unsafe": "close",
                 "shutter_timeout_s": 0.3}})
    t = _when_altitude_between(hub.site, NGC604_RA, NGC604_DEC, 40.0, 60.0)
    arm = ResumeArm(engine, hub, clock=lambda: t)
    refusal = await asyncio.wait_for(
        arm._recover(Session(name="resume", plan=plan)), 20.0)

    assert refusal is not None and "within 0.3s" in refusal, refusal
    assert calls == [], f"the ladder moved anyway: {calls}"


# ================== the cover through a cloud hold's darks (WP 4.4, item 3)

async def test_a_cloud_holds_darks_leave_the_cover_open_for_the_next_light(
        sim_hub, temp_store):
    """The question was whether a mid-night calibration leaves the cover shut
    for the lights that follow it: ``_run_calibration``'s ``_panel_off_safe``
    shuts it, and a cloud hold takes darks through ``_run_calibration``.

    GREEN, and why: ``_panel_off_safe`` runs there only when the step lit the
    panel (``if panel_lit:``, the flat metering path). A dark is shot through
    the filter wheel's blackout slot, not through the cover, and never lights
    the panel, so the hold's darks do not touch the cover. The cover the run
    start opened is still open when the hold releases. This holds that: one
    dark is shot by the real ``_hold_darks``, and the cover reads OPEN
    before it and after it.
    """
    _safety(temp_store, enabled=False, min_alt_deg=0.0)
    engine = SequenceEngine(sim_hub)
    engine.plan = _light_plan(cloud_hold_darks=1)
    cover = sim_hub.devices["covercalibrator"]
    await cover.open_cover()
    assert await cover.get_cover_state() is CoverState.OPEN, "premise"
    engine._hold_step = engine.plan.targets[0].steps[0]
    engine._hold_darks_want = None
    engine._hold_darks_taken = 0
    shot: list[str] = []
    real = sim_hub.capture

    async def capture(*a, **kw):
        shot.append(kw.get("frame_type", "Light"))
        return await real(*a, **kw)

    sim_hub.capture = capture

    assert await engine._hold_darks(None) is True

    assert shot == ["Dark"], shot
    assert await cover.get_cover_state() is CoverState.OPEN, (
        "the hold's dark shut the cover the run opened")


# ===================== what ResumeArm does after a refused open (item 4)

async def test_resume_arm_retries_at_its_interval_after_a_run_ended_for_a_cover(
        resume_hub, monkeypatch):
    """A run that ends in "error" because the cover would not open: what does
    auto-resume do about the session?

    Observed, and held here: the run's error ending leaves the session
    dormant and armed (``_finalize_report("error")`` is not a disarm), so the
    next tick takes it up again. With the window open, the recovery ladder's
    own open (step 0b) finds the cover still stuck and REFUSES, naming the
    cover: the tick holds on that reason and waits ``RETRY_INTERVAL_S`` before
    the next attempt, as for any refusal. No tight loop, and the policy is
    ResumeArm's own, unchanged.
    """
    import astrodeck.sequence.resume_arm as arm_mod
    from astrodeck.sequence.resume_arm import RETRY_INTERVAL_S, ResumeArm
    from astrodeck.sequence.session import session_store
    hub = resume_hub
    _trust_focus(monkeypatch)
    cover = hub.devices["covercalibrator"]

    async def stuck():
        return None

    cover.open_cover = stuck
    monkeypatch.setattr(roof_mod, "COVER_OPEN_TIMEOUT_S", 0.2)
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)

    # night one: the run ends in "error" at its start, with the cover stuck
    engine = SequenceEngine(hub)
    plan = _plan_with_floor(0.0).model_copy(update={"name": "stuck cover"})
    engine.start(plan)
    sid = engine._session.id
    assert await _wait_for(lambda: not engine.running), engine.state
    assert engine.state.get("state") == "error", engine.state
    stored = session_store.load(sid)
    assert (stored.status, stored.auto_resume) == ("dormant", True), (
        stored.status, stored.auto_resume)

    # the next tick: refused by the recovery's own open, named, backed off
    started: list[int] = []
    monkeypatch.setattr(engine, "start",
                        lambda *a, **kw: started.append(1) or None)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, hub, clock=lambda: now["t"])
    await arm.tick()
    assert started == [], "the arm started a run through a stuck cover"
    assert arm._retry_at == now["t"] + RETRY_INTERVAL_S, arm._retry_at
    hold = arm.hold
    assert hold is not None and cover.name in hold["reason"], hold

    # inside the interval it does not try again; after it, it does (and is
    # refused again, for the same named reason)
    asked: list[int] = []
    real_recover = arm._recover

    async def counting(session):
        asked.append(1)
        return await real_recover(session)

    monkeypatch.setattr(arm, "_recover", counting)
    now["t"] += RETRY_INTERVAL_S - 1
    await arm.tick()
    assert asked == [], "the arm tried again inside its retry interval"
    now["t"] += 2
    await arm.tick()
    assert asked == [1], "the arm did not retry after its interval"
    assert started == []
