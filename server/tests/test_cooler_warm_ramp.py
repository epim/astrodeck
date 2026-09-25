"""Cooler warm-down ramp (2026-08-04).

THE BUG THESE TESTS PIN. "Warm" was one ``set_cooler(False)`` in three places —
the Capture button (api/app.py), the unattended ``abort_park_warm`` wind-down
(sequence/engine.py) and the NINA bridge (devices/nina.py, sending NINA's
``warm?minutes=0`` = warm IMMEDIATELY). The owner heard the TEC stop and measured
the sensor straight afterwards: 8.3 → 9.4 → 11.0 → 11.8 °C in about 40 s. That
is ~5 °C/min of uncontrolled equalisation with the room — thermal shock across
the sensor/cold-finger, and condensation inside the chamber as a cold sensor
meets un-cooled air.

So the assertions below are mostly about ORDER and ABSENCE, not just outcome:

  * the cooler must NOT be switched off until the setpoint has climbed,
  * a ramp that cannot run must SAY it did not run (a silent fallback to the bug
    leaves SafetyLimitsPanel promising a safe ramp the code no longer does),
  * a wind-down must not WAIT for the ramp (it fires when something is already
    wrong; the park and the roof close come first),
  * two warms must not race, and Cool must win cleanly.
"""
from __future__ import annotations

import asyncio

import pytest

from astrodeck import cooling
from astrodeck.config import AppConfig, CoolingConfig
from astrodeck.devices.base import Camera, CameraFrame, DeviceError
from astrodeck.hub import Hub


# --------------------------------------------------------------- pure schedule

def test_warm_minutes_never_returns_ninas_immediate_sentinel():
    """0 is NINA's warm-IMMEDIATELY value — the exact thing that caused the bug.
    A short ramp must round UP into a real minute, never down into 0."""
    assert cooling.warm_minutes(11.9, 12.0, 2.0) == 1     # 3 s of ramp -> 1 min
    assert cooling.warm_minutes(-10.0, 15.0, 2.0) == 13   # 25 °C at 2 °C/min
    assert cooling.warm_minutes(20.0, 12.0, 2.0) == 1     # already warm -> still >= 1


def test_warm_duration_and_next_setpoint():
    # 25 °C at 2 °C/min is 12.5 min. NINA's own 10-minute warm default over the
    # same delta implies 2.5 °C/min, so we are deliberately a little slower.
    assert cooling.warm_duration_s(-10.0, 15.0, 2.0) == pytest.approx(750.0)
    # one 15 s step at 2 °C/min = 0.5 °C
    assert cooling.warm_next_setpoint_c(-10.0, 15.0, 2.0, 15.0) == pytest.approx(-9.5)
    # never overshoots ambient, however big the step
    assert cooling.warm_next_setpoint_c(14.9, 15.0, 2.0, 600.0) == pytest.approx(15.0)


def test_warm_ambient_priority_and_provenance():
    """"to 20 °C (assumed)" and "to 11 °C (measured)" are different promises and
    the caller has to be able to tell them apart."""
    cfg = AppConfig()
    assert cooling.warm_ambient_c(cfg, -10.0) == (cooling.WARM_FALLBACK_AMBIENT_C,
                                                  "assumed")
    assert cooling.warm_ambient_c(cfg, -10.0, 11.0) == (11.0, "measured")
    cfg.cooling = CoolingConfig(warm_ambient_c=8.0)
    assert cooling.warm_ambient_c(cfg, -10.0, 11.0) == (8.0, "configured")
    # A ramp that runs DOWNHILL is not a warm-up: ambient can never be below the
    # sensor we are starting from, whatever the config says.
    assert cooling.warm_ambient_c(cfg, 15.0)[0] == 15.0


def test_warm_rate_is_clamped_not_trusted():
    cfg = AppConfig()
    assert cooling.warm_rate_c_per_min(cfg) == 2.0
    assert cooling.warm_rate_c_per_min(None) == 2.0        # missing block => default
    cfg.cooling = CoolingConfig(warm_rate_c_per_min=20.0)
    # 20 is the ceiling: above ~5 °C/min (the measured free-running rate) a
    # "ramp" is not controlling anything, so we refuse to pretend beyond 20.
    assert cooling.warm_rate_c_per_min(cfg) == 20.0
    cfg.cooling = CoolingConfig.model_construct(warm_rate_c_per_min=0.0)
    assert cooling.warm_rate_c_per_min(cfg) == 0.1         # never divide by zero


def test_warm_is_pointless_at_ambient():
    assert cooling.warm_is_pointless(19.5, 20.0)
    assert not cooling.warm_is_pointless(-10.0, 20.0)


# ------------------------------------------------------------------ test camera

class FakeCamera(Camera):
    """A coolable camera that records every cooler command IN ORDER.

    The order is the evidence: the defect was not "the cooler ends up off", it
    was "the cooler goes off FIRST"."""

    def __init__(self, temp_c: float = -10.0, *, ambient_c: float | None = None,
                 reports_temp: bool = True, follows: bool = True,
                 follow_fraction: float | None = None):
        super().__init__("Fake Cooled Cam")
        self.connected = True
        self.can_cool = True
        self.calls: list[tuple[bool, float | None]] = []
        self._temp = temp_c
        self._ambient = ambient_c
        self._reports = reports_temp
        # follows=False models a sensor that does NOT track the setpoint (a TEC
        # at its limit, or a setpoint already above the real ambient).
        self._follows = follows
        # THE MIDDLE OF THE RANGE, which neither of the two settings above can
        # express (issue #17). `follows=True` is a TEC that reaches every
        # setpoint the instant it is commanded and `follows=False` is one that
        # has stopped responding entirely; real hardware is in between, and the
        # lead check exists precisely to tell those two apart. Without a model
        # that lags PARTIALLY, no test in this file could say whether ordinary
        # thermal inertia trips it.
        #
        # `follow_fraction` is how much of the remaining gap the sensor closes
        # per step: 1.0 is the obedient TEC above, 0.0 is the dead one, 0.5
        # halves the distance each time.
        self._follow_fraction = follow_fraction
        self.refuse_setpoint = False

    async def expose(self, seconds, gain, offset, binning=1, light=True,
                     save=False, target="") -> CameraFrame:      # pragma: no cover
        raise DeviceError("not used")

    async def abort_exposure(self) -> None:                      # pragma: no cover
        return None

    async def connect(self) -> None:                             # pragma: no cover
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def set_cooler(self, on: bool, target_c: float | None = None) -> None:
        if on and self.refuse_setpoint:
            raise DeviceError("driver rejected the setpoint")
        self.calls.append((on, target_c))
        if on and target_c is not None and self._follows:
            if self._follow_fraction is None:
                self._temp = target_c    # a perfectly obedient TEC
            else:
                self._temp += (target_c - self._temp) * self._follow_fraction

    async def get_temperature(self) -> float | None:
        return self._temp if self._reports else None

    async def get_ambient_temperature(self) -> float | None:
        return self._ambient


@pytest.fixture(autouse=True)
def _a_store_of_its_own(monkeypatch, tmp_path):
    """Every test here gets its own config store: the singleton repointed at a
    file under tmp_path with its cache dropped, both through monkeypatch.

    A cool saves the standing setpoint (`Hub._remember_cooling`), and three
    tests here cool a camera, so the file used to leave -10 or -12 C on the
    process-wide config for every later test on the worker (#227). Autouse
    rather than on those three, so the next test written here that cools is
    covered without anyone remembering this.

    Without the two lines, the conftest guard errors at teardown
    (2 in this file alone, observed): "test_cool_during_a_warm_wins_cleanly
    left the process-wide config changed: cooling.setpoint_c."
    """
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)


@pytest.fixture
def hub_with_camera(monkeypatch):
    """A bare Hub with a fake camera and a ~instant ramp step, so a 12-minute
    schedule runs in milliseconds without faking the LOGIC (every setpoint the
    real ramp would command is still commanded, in the same order)."""
    monkeypatch.setattr(cooling, "WARM_STEP_S", 0.0)
    h = Hub()
    cam = FakeCamera()
    h.devices["camera"] = cam
    return h, cam


async def _drain(hub: Hub, timeout: float = 5.0) -> None:
    """Wait for the ramp task to finish (or fail the test loudly)."""
    task = hub._warm_task
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), timeout)


# ------------------------------------------------------------------- the ramp

async def test_warm_ramps_the_setpoint_and_switches_off_only_at_the_end(
        hub_with_camera, bus_lines):
    hub, cam = hub_with_camera
    state = await hub.warm_camera()
    # NON-BLOCKING: the call returns with the ramp still running. This is what
    # lets the safety wind-down start a warm and get on with the park.
    assert state["active"] is True and state["ramped"] is True
    await _drain(hub)

    ons = [c for c in cam.calls if c[0]]
    offs = [i for i, c in enumerate(cam.calls) if not c[0]]
    assert ons, "the ramp never commanded a setpoint"
    # THE regression assertion: nothing switches the cooler off until the very
    # end. Before the fix, cam.calls was exactly [(False, None)].
    assert offs == [len(cam.calls) - 1], f"cooler switched off mid-ramp: {cam.calls}"
    setpoints = [c[1] for c in ons]
    assert setpoints == sorted(setpoints), "setpoints must climb monotonically"
    assert setpoints[0] == pytest.approx(-9.5)          # first 15 s step at 2 °C/min
    # UPDATED 2026-08-16, deliberately. This used to assert the ramp stops at
    # the FALLBACK ambient - which is the pre-#154 behaviour, and asserting it
    # meant asserting the bug: an ASSUMED ambient the sensor is still
    # following is simply wrong and must be walked past. `_warm_ramp` could
    # never do that (it raised NameError on `ambient_from`, fixed with this
    # change), so this assertion held for the wrong reason.
    #
    # This fake TEC follows every setpoint perfectly, so it has no real
    # ambient to stop at and the ramp climbs until the SAFETY CEILING bounds
    # it. On real hardware the sensor stops following at the real air
    # temperature and the lead check ends the ramp there - which is what
    # `test_ramp_finishes_when_the_sensor_stops_following` covers.
    assert setpoints[-1] <= cooling.WARM_AMBIENT_CEILING_C + 1e-6, (
        f"the ramp climbed past its safety ceiling: {setpoints[-1]}")
    assert setpoints[-1] > cooling.WARM_FALLBACK_AMBIENT_C, (
        f"the ramp stopped at the assumed {cooling.WARM_FALLBACK_AMBIENT_C} C "
        f"ambient while the sensor was still following it - #154 unreachable")
    # …and the whole climb, not a token step or two: -10 -> 20 in 0.5 °C steps.
    assert len(setpoints) >= 55, f"only {len(setpoints)} steps — ramp cut short"
    assert any("warming camera" in m for _l, m, _s in bus_lines)


async def test_a_lagging_tec_started_from_setpoint_still_walks_all_the_way_up(
        hub_with_camera):
    """Issue #17: the ramp is reported to exit almost immediately when the
    camera is ALREADY AT SETPOINT as it begins, leaving the sensor cold.

    At-AMBIENT is a different case and is already handled - `warm_is_pointless`
    ends the ramp before it starts and says so. At-SETPOINT means the TEC is
    working hard with a real gradient still to climb, and the mechanism that
    could plausibly end the ramp early is the lead check: when the setpoint
    steps up, the sensor lags while the TEC unwinds, and two consecutive
    breaches of `WARM_MAX_LEAD_C` finish the ramp.

    Neither existing camera model can ask that question. `follows=True` reaches
    every setpoint instantly, so the lead is always zero; `follows=False` never
    moves, so the lead grows without bound. The lead check has therefore never
    been graded against anything in between, which is the only regime real
    hardware is in.

    With the sensor closing HALF the remaining gap per step - far laggier than
    the 1.72 C/min this rig was measured at against a 0.5 C step - the lead
    settles at half a step, 0.25 C, against a 3.0 C tolerance. The ramp
    completes. So ordinary inertia from an at-setpoint start does not trip it,
    and #17's mechanism is not this.

    MUTATION: `WARM_MAX_LEAD_C = 0.2`. Observed: the ramp stops after a couple
    of steps and this fails on the step count - which is what the reported
    behaviour would look like, and is how this case would have caught it.
    """
    hub, cam = hub_with_camera
    cam._follow_fraction = 0.5
    cam._temp = -10.0               # AT the setpoint the run was holding
    await hub.warm_camera()
    await _drain(hub)
    setpoints = [c[1] for c in cam.calls if c[0]]
    assert len(setpoints) >= 55, (
        f"a lagging TEC started from its setpoint climbed only {len(setpoints)} "
        f"steps before the ramp ended: {setpoints[:5]}...{setpoints[-3:]}")
    assert setpoints[-1] > cooling.WARM_FALLBACK_AMBIENT_C, (
        f"the ramp stopped at {setpoints[-1]} C, short of ambient")
    assert "already at ambient" not in (hub._warm_state or {}).get("note", ""), (
        "the lead check ended a ramp whose sensor was still following")


async def test_the_lead_check_still_fires_on_a_tec_that_has_actually_stopped(
        hub_with_camera):
    """The other side of the same model, and the reason the one above is not
    just a looser tolerance: a sensor that closes only a TENTH of the gap per
    step falls behind faster than the schedule and the ramp must still end.

    Between this and the case above, the lead check is graded on both sides of
    its boundary with the same camera, rather than only at the two impossible
    extremes.

    STARTED WARM, and that is the change issue #17 made to this case. It used to
    start at -10 °C, where a sensor that stops rising is not at ambient at all -
    it is a TEC that never took control, and ending the ramp there switched the
    cooler off onto a sensor thirty degrees below the room. This case is about
    the lead check doing its legitimate job, which is finding the REAL ambient
    under an assumed one, so it starts where that is what is actually happening:
    13.5 °C under an assumed 20 °C, which is the 2026-09-12 log's own good case.

    MUTATION: `WARM_MAX_LEAD_C = 30.0`. Observed: this ramp runs to the safety
    ceiling instead of stopping, and the note never says "already at ambient".
    """
    hub, cam = hub_with_camera
    cam._follow_fraction = 0.1
    cam._temp = 13.5
    await hub.warm_camera()
    await _drain(hub)
    setpoints = [c[1] for c in cam.calls if c[0]]
    assert setpoints[-1] < cooling.WARM_FALLBACK_AMBIENT_C, (
        f"a sensor closing a tenth of the gap per step reached {setpoints[-1]} C; "
        f"the lead check never fired")
    assert cam.calls[-1] == (False, None), "the cooler was left on"
    assert "already at ambient" in (hub._warm_state or {}).get("note", "")


async def test_ramp_finishes_when_the_sensor_stops_following(hub_with_camera):
    """A sensor that stops rising with the setpoint means the TEC has nothing
    left to do — i.e. we are AT the real ambient, which the assumed 20 °C ceiling
    would otherwise sail past. Switching off there is thermally a no-op.

    PINNED AT 13.5 °C, not at -10. This case used to pin the sensor at -10 and
    assert that the ramp called that "already at ambient" and switched the
    cooler off - which is issue #17's defect, asserted as correct behaviour.
    That is why the bug shipped and why the 2026-09-12 log shows it happening
    for real: a test was holding it in place. The inference in the docstring is
    sound only when the sensor is somewhere a room could be, and this case now
    tests it there.
    """
    hub, cam = hub_with_camera
    cam._follows = False            # sensor pinned wherever it starts
    cam._temp = 13.5                # a plausible room, under the assumed 20
    await hub.warm_camera()
    await _drain(hub)
    setpoints = [c[1] for c in cam.calls if c[0]]
    # WARM_MAX_LEAD_C is 3.0 and we require two consecutive breaches, so the ramp
    # stops a little past a 3 °C lead — nowhere near the 20 °C ceiling.
    assert setpoints[-1] < cooling.WARM_FALLBACK_AMBIENT_C, setpoints
    assert cam.calls[-1] == (False, None)
    assert "already at ambient" in (hub._warm_state or {}).get("note", "")


async def test_a_tec_that_never_warms_the_sensor_is_not_at_ambient(
        hub_with_camera, bus_lines):
    """ISSUE #17, from the 2026-09-12 night log (minutes:seconds from the
    ramp's start, W):

        W+0:00  ramping the setpoint -10.3 -> 20.0 C at 2 C/min, about 15 min
        W+2:03  warm finished - sensor stopped following the setpoint at
                -10.5 C - already at ambient; cooler off

    Two minutes into a planned fifteen, with the sensor thirty degrees below the
    target, the ramp declared ambient and switched the TEC off. That is the
    5 °C/min plunge this whole routine exists to prevent, performed by the
    routine and logged as a success.

    The sensor is pinned, so it never rises: a dead TEC, which is what the log
    shows. The ramp must not claim ambient and must not switch the cooler off.

    MUTATION: drop the `ambient_c - temp > cooling.WARM_NOT_AMBIENT_C` test.
    Observed: the note reads "already at ambient" and the last cooler call is
    `(False, None)` - the exact log line above, reproduced.
    """
    hub, cam = hub_with_camera
    cam._follows = False
    cam._temp = -10.3               # the log's own starting temperature
    await hub.warm_camera()
    await _drain(hub)
    note = (hub._warm_state or {}).get("note", "")
    assert "already at ambient" not in note, (
        f"a sensor at {cam._temp} C was called ambient: {note!r}")
    assert "could not warm the sensor" in note, note
    assert cam.calls[-1][0] is True, (
        f"the cooler was switched off onto a sensor at {cam._temp} C: "
        f"{cam.calls[-3:]}")
    assert any("leaving the cooler ENGAGED" in m for _l, m, _s in bus_lines), (
        "a TEC that will not follow a ramp is a hardware fault and has to be "
        f"loud: {[(l, m[:60]) for l, m, _ in bus_lines if 'warm' in m.lower()][-3:]}")


async def test_measured_ambient_is_used_and_labelled(monkeypatch):
    monkeypatch.setattr(cooling, "WARM_STEP_S", 0.0)
    hub = Hub()
    cam = FakeCamera(ambient_c=5.0)
    hub.devices["camera"] = cam
    state = await hub.warm_camera()
    assert state["ambient_c"] == 5.0 and state["ambient_from"] == "measured"
    await _drain(hub)
    assert max(c[1] for c in cam.calls if c[0]) == pytest.approx(5.0)


# ----------------------------------------------------------------- fallbacks

async def test_no_temperature_readout_falls_back_and_says_so(monkeypatch, bus_lines):
    """The measurement IS the ramp. Without it we still switch off — but the log
    must carry the admission, because the product goes on claiming a safe ramp."""
    monkeypatch.setattr(cooling, "WARM_STEP_S", 0.0)
    hub = Hub()
    cam = FakeCamera(reports_temp=False)
    hub.devices["camera"] = cam
    state = await hub.warm_camera()
    assert state["active"] is False and state["ramped"] is False
    assert cam.calls == [(False, None)]
    warnings = [m for lvl, m, _s in bus_lines if lvl == "warning"]
    assert any("WITHOUT a warm ramp" in m for m in warnings), warnings
    assert any("cannot report its sensor temperature" in m for m in warnings)


async def test_config_can_disable_the_ramp_but_it_is_logged(monkeypatch, bus_lines):
    monkeypatch.setattr(cooling, "WARM_STEP_S", 0.0)
    import astrodeck.config as config_mod
    cfg = config_mod.config_store.cfg()
    monkeypatch.setattr(cfg, "cooling", CoolingConfig(warm_ramp=False))
    hub = Hub()
    cam = FakeCamera()
    hub.devices["camera"] = cam
    state = await hub.warm_camera()
    assert state["ramped"] is False and cam.calls == [(False, None)]
    assert any("warm_ramp is turned off" in m for lvl, m, _s in bus_lines
               if lvl == "warning")


async def test_explicit_immediate_warm_is_still_reachable(hub_with_camera):
    """The Capture screen's "Stop ramp" — and any scripted caller — can still ask
    for the old behaviour. Deliberately, and logged."""
    hub, cam = hub_with_camera
    await hub.warm_camera(ramp=False)
    assert cam.calls == [(False, None)]


async def test_camera_already_at_ambient_does_not_theatre_ramp(monkeypatch):
    monkeypatch.setattr(cooling, "WARM_STEP_S", 0.0)
    hub = Hub()
    cam = FakeCamera(temp_c=21.0)
    hub.devices["camera"] = cam
    state = await hub.warm_camera()
    assert state["ramped"] is False
    assert "at or above ambient" in state["note"]


async def test_a_driver_that_refuses_setpoints_finishes_the_warm(hub_with_camera):
    hub, cam = hub_with_camera
    cam.refuse_setpoint = True
    await hub.warm_camera()
    await _drain(hub)
    # It cannot be ramped, so it must not be left cold and holding: the warm ends.
    assert cam.calls[-1] == (False, None)
    assert "refused a setpoint" in (hub._warm_state or {}).get("note", "")


# --------------------------------------------------------------- concurrency

async def test_two_warms_do_not_race(hub_with_camera, monkeypatch):
    monkeypatch.setattr(cooling, "WARM_STEP_S", 0.05)   # slow enough to overlap
    hub, cam = hub_with_camera
    first = await hub.warm_camera()
    task = hub._warm_task
    second = await hub.warm_camera()
    assert hub._warm_task is task, "a second stepper started on the same setpoint"
    assert second["start_c"] == first["start_c"]
    await hub.cancel_warm("test teardown", finalize=True)


async def test_cool_during_a_warm_wins_cleanly(hub_with_camera, monkeypatch):
    """Pressing Cool mid-ramp must end with the camera holding the USER's
    setpoint. Without the cancel, the ramp's next step (≤15 s later) would
    silently overwrite it and the panel would show a target the camera was not
    holding."""
    monkeypatch.setattr(cooling, "WARM_STEP_S", 0.05)
    hub, cam = hub_with_camera
    await hub.warm_camera()
    await asyncio.sleep(0.12)                     # let a step or two land
    await hub.cool_camera(-12.0)
    assert hub._warm_task is None
    assert cam.calls[-1] == (True, -12.0)
    await asyncio.sleep(0.2)                      # a live ramp would step again here
    assert cam.calls[-1] == (True, -12.0), f"the ramp kept stepping: {cam.calls}"
    assert hub._warm_state is not None and hub._warm_state["active"] is False


async def test_cancelling_leaves_a_defined_state_not_a_stale_setpoint(
        hub_with_camera, monkeypatch):
    monkeypatch.setattr(cooling, "WARM_STEP_S", 0.05)
    hub, cam = hub_with_camera
    await hub.warm_camera()
    await asyncio.sleep(0.12)
    assert await hub.cancel_warm("the user stopped it", finalize=True) is True
    assert cam.calls[-1] == (False, None), "cancelled mid-ramp with the TEC still on"
    # And the record must not claim it completed — that is the one sentence a
    # cancel can never be allowed to produce.
    assert hub._warm_state["note"].startswith("stopped:")
    assert await hub.cancel_warm("nothing to stop", finalize=True) is False


async def test_teardown_finalizes_a_running_ramp(monkeypatch):
    """A rig disconnect mid-ramp must not leave the camera on a mid-ramp setpoint
    nobody chose, with nothing left talking to it."""
    monkeypatch.setattr(cooling, "WARM_STEP_S", 0.05)
    hub = Hub()
    cam = FakeCamera()
    hub.devices["camera"] = cam
    await hub.warm_camera()
    await asyncio.sleep(0.12)
    await hub.disconnect_all()
    assert (False, None) in cam.calls
    assert hub._warm_task is None


# ------------------------------------------------------------------- status

async def test_status_reports_progress_then_expires(hub_with_camera, monkeypatch):
    monkeypatch.setattr(cooling, "WARM_STEP_S", 0.05)
    hub, cam = hub_with_camera
    await hub.warm_camera()
    await asyncio.sleep(0.12)
    state = hub.warm_state()
    assert state["active"] is True
    assert state["setpoint_c"] > state["start_c"]
    assert state["eta_s"] > 0
    assert not any(k.startswith("_") for k in state), "private bookkeeping leaked"
    await hub.cancel_warm("test", finalize=True)
    # A finished warm lingers so the panel can say "warm complete" instead of
    # snapping back to a bare "Off" — which is what the bug also looked like.
    assert hub.warm_state() is not None
    monkeypatch.setattr(cooling, "WARM_STATE_RETAIN_S", -1.0)
    assert hub.warm_state() is None


async def test_camera_without_a_cooler_is_a_no_op_not_an_error(monkeypatch):
    monkeypatch.setattr(cooling, "WARM_STEP_S", 0.0)
    hub = Hub()
    cam = FakeCamera()
    cam.can_cool = False
    hub.devices["camera"] = cam
    state = await hub.warm_camera()
    assert state["active"] is False and cam.calls == []


async def test_no_camera_raises_so_the_route_can_400():
    hub = Hub()
    with pytest.raises(DeviceError):
        await hub.warm_camera()


# ------------------------------------------------------- backend-owned ramps

class SelfWarmingCamera(FakeCamera):
    """Stands in for NINA, which owns a duration-based ramp of its own."""
    self_warms = True

    def __init__(self, **kw):
        super().__init__(**kw)
        self.warm_minutes_seen: list[int] = []

    async def warm(self, minutes: int | None = None) -> None:
        self.warm_minutes_seen.append(minutes)


async def test_backend_that_warms_itself_is_handed_a_duration(monkeypatch):
    monkeypatch.setattr(cooling, "WARM_STEP_S", 0.0)
    hub = Hub()
    cam = SelfWarmingCamera()
    hub.devices["camera"] = cam
    state = await hub.warm_camera()
    assert state["delegated"] is True
    # 30 °C (-10 -> 20) at 2 °C/min = 15 min. NEVER 0 — that is NINA's
    # warm-IMMEDIATELY sentinel and the original bug.
    assert cam.warm_minutes_seen == [15]
    assert cam.calls == [], "the hub also stepped a setpoint the backend owns"
    # No _drain here: a delegated warm is tracked on the WALL CLOCK (the
    # backend owns the ramp, so there are no setpoint steps to compress) and
    # its 15 minutes are 15 real minutes. The cancel IS the rest of the
    # assertion: the hub must be holding a tracker it can stop, not a stepper
    # fighting NINA for the setpoint.
    assert await hub.cancel_warm("test teardown", finalize=True) is True


async def test_immediate_warm_on_a_self_warming_backend_asks_for_zero(monkeypatch):
    """The one place 0 is correct: the caller explicitly asked for no ramp."""
    monkeypatch.setattr(cooling, "WARM_STEP_S", 0.0)
    hub = Hub()
    cam = SelfWarmingCamera()
    hub.devices["camera"] = cam
    await hub.warm_camera(ramp=False)
    assert cam.warm_minutes_seen == [0]


async def test_nina_camera_no_longer_sends_minutes_zero(monkeypatch):
    """devices/nina.py:326 was ``warm?minutes=0``. Pinned at the wire level,
    because this is the one call site where the bug was a single character."""
    from astrodeck.devices import nina as nina_mod

    sent: list[dict] = []

    class FakeClient:
        async def get(self, path, **params):
            sent.append({"path": path, **params})
            return {}

    cam = nina_mod.NinaCamera.__new__(nina_mod.NinaCamera)
    cam.client = FakeClient()
    cam.can_cool = True

    async def temp():
        return -10.0
    cam.get_temperature = temp                    # type: ignore[method-assign]

    await cam.set_cooler(False)
    assert sent[-1]["path"] == "/equipment/camera/warm"
    assert sent[-1]["minutes"] >= 1, sent[-1]
    # …and the explicit escape hatch still reaches NINA's own sentinel.
    await cam.warm(0)
    assert sent[-1]["minutes"] == 0


# ------------------------------------------------------- the safety wind-down

async def test_wind_down_starts_the_ramp_and_does_not_wait_for_it(monkeypatch):
    """``abort_park_warm`` is the path this whole change exists for: it runs
    unattended, and it used to cut the TEC dead. It must now start a ramp — and
    must NOT block on it, because the park and the roof close are the urgent
    parts of a wind-down."""
    monkeypatch.setattr(cooling, "WARM_STEP_S", 0.05)
    from astrodeck.sequence.engine import SequenceEngine

    hub = Hub()
    cam = FakeCamera()
    hub.devices["camera"] = cam
    engine = SequenceEngine(hub)

    started = asyncio.get_running_loop().time()
    await engine._wind_down(park=False, warm=True)
    elapsed = asyncio.get_running_loop().time() - started

    # The ramp is ~15 min of scheduled work; the wind-down returned immediately.
    assert elapsed < 1.0, f"wind-down blocked for {elapsed:.2f}s"
    assert hub._warm_task is not None and not hub._warm_task.done()
    # The first setpoint has NOT been sent yet — the ramp's first step is a
    # whole interval away — which is itself the proof the wind-down did not
    # sit and wait for a ten-minute ramp before returning.
    assert cam.calls == []
    await asyncio.sleep(0.12)
    assert cam.calls and cam.calls[0][0] is True, \
        f"wind-down cut the cooler instead of ramping: {cam.calls}"
    await hub.cancel_warm("test teardown", finalize=True)


async def test_cooling_a_plan_cancels_a_ramp_still_in_flight(monkeypatch):
    """A ramp outlives the run that started it (it is hub-owned on purpose), so
    the NEXT run's cooling wait can collide with it. Without the cancel, the
    ramp's next step would raise the setpoint the plan just asked for and the
    cooling wait would watch the sensor climb away from its target."""
    monkeypatch.setattr(cooling, "WARM_STEP_S", 0.05)
    from astrodeck.sequence.engine import SequenceEngine

    hub = Hub()
    cam = FakeCamera()
    hub.devices["camera"] = cam
    await hub.warm_camera()
    await asyncio.sleep(0.12)

    engine = SequenceEngine(hub)
    assert await engine._cool_and_wait(-10.0, timeout_s=5) is True
    assert hub._warm_task is None, "the ramp survived a cool request"
    assert cam.calls[-1] == (True, -10.0)
    await asyncio.sleep(0.2)
    assert cam.calls[-1] == (True, -10.0), f"the ramp kept stepping: {cam.calls}"


def test_the_warm_route_starts_a_ramp_and_reports_it(tmp_path, monkeypatch):
    """Route wiring only (the semantics are pinned above): POST /api/camera/cooler
    {on:false} must hand off to the hub's ramp and RETURN it, so the Capture
    screen has something to render. Before the fix the route called
    set_cooler(False) and returned {"ok": true} — there was nothing to show,
    because there was nothing happening."""
    monkeypatch.setattr(cooling, "WARM_STEP_S", 0.0)
    import astrodeck.hub as hub_module
    from fastapi.testclient import TestClient
    from astrodeck.api.app import create_app

    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    client = TestClient(create_app())
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(hub_module.hub.connect_sim())
        # Cool first: a camera already at ambient has nothing to ramp, and the
        # sim's uncooled sensor reads ambient exactly.
        assert client.post("/api/camera/cooler",
                           json={"on": True, "target_c": -10}).status_code == 200
        r = client.post("/api/camera/cooler", json={"on": False})
        assert r.status_code == 200, r.text
        warm = r.json()["warm"]
        assert warm["ramped"] is True and warm["active"] is True
        assert warm["rate_c_per_min"] == 2.0
        # …and the explicit escape hatch still reports itself as un-ramped.
        r2 = client.post("/api/camera/cooler", json={"on": False, "ramp": False})
        assert r2.json()["warm"]["ramped"] is False
    finally:
        loop.run_until_complete(hub_module.hub.disconnect_all())
        loop.close()
        client.close()


async def test_wind_down_survives_a_hub_without_the_routine(monkeypatch, bus_lines):
    """Defensive: an embedder/test double with no warm_camera still warms — but
    the log admits there was no ramp rather than printing "warming camera"."""
    from astrodeck.sequence.engine import SequenceEngine

    hub = Hub()
    cam = FakeCamera()
    hub.devices["camera"] = cam
    monkeypatch.delattr(Hub, "warm_camera")
    engine = SequenceEngine(hub)
    await engine._wind_down(park=False, warm=True)
    assert cam.calls == [(False, None)]
    assert any("no warm-ramp routine" in m for _l, m, _s in bus_lines)


# ---------------------------------------------- the wind-down park (#211)
#
# 2026-08-08.jsonl, the night the mount's link died:
#
#   [info/sequence] sequence 'NGC 7023 Iris - north, unguided'
#                   complete: 150 frames
#   [info/guide]    native guider stopped
#
# and nothing else. That run had park_when_done off, so the mount was left
# tracking and NOTHING said so; it then tracked for two more hours until the
# dawn net tried, and failed, to catch it. These pin the three ways that
# silence happened.

class _ParkTel:
    """A telescope whose link can be dropped, like the real one's can."""

    def __init__(self, *, connected=True, park_error=None, reconnects_ok=True):
        self.connected = connected
        self.park_calls = 0
        self.connect_calls = 0
        self.parked = False
        self.park_error = park_error
        self.reconnects_ok = reconnects_ok

    async def connect(self):
        self.connect_calls += 1
        if not self.reconnects_ok:
            raise OSError("cannot open COM3: the device is not present")
        self.connected = True

    async def park(self):
        self.park_calls += 1
        if self.park_error is not None:
            raise self.park_error
        self.parked = True


async def test_the_wind_down_park_is_logged_after_it_happens(bus_lines):
    """"parking mount" is written BEFORE the command, so on its own it proves
    intent, not outcome. /api/mount/park documents this rule at length — "PARK
    IS THE ONE EVENT AN UNATTENDED NIGHT MUST BE ABLE TO PROVE" — and the park
    that actually runs at the end of every unattended night was not following
    it."""
    from astrodeck.sequence.engine import SequenceEngine

    hub = Hub()
    tel = _ParkTel()
    hub.devices["telescope"] = tel
    await SequenceEngine(hub)._wind_down(park=True, warm=False)

    assert tel.parked is True, "precondition: the park really happened"
    said = [m for _l, m, _s in bus_lines]
    assert any("mount parked" in m for m in said), (
        f"a completed park must leave proof, not just an intention: {said}")


async def test_a_failed_park_leaves_no_parked_claim(bus_lines):
    """The other half: the proof line must not appear when the park FAILED."""
    from astrodeck.sequence.engine import SequenceEngine

    hub = Hub()
    tel = _ParkTel(park_error=RuntimeError("Gps read failed: link not open"))
    hub.devices["telescope"] = tel
    await SequenceEngine(hub)._wind_down(park=True, warm=False)

    said = [m for _l, m, _s in bus_lines]
    assert any("park failed during wind-down" in m for m in said), said
    assert not any("mount parked" in m for m in said), (
        f"the mount did NOT park; nothing may say it did: {said}")


async def test_the_wind_down_reopens_a_dropped_mount_link_to_park(bus_lines):
    """Since ``connected`` became a measurement (#208) this branch is reachable
    for the first time — and skipping it quietly would have turned that fix into
    a regression, because the wind-down park is the last thing standing between
    a finished run and hours of unattended tracking."""
    from astrodeck.sequence.engine import SequenceEngine

    hub = Hub()
    tel = _ParkTel(connected=False)          # the link-abandoned state
    hub.devices["telescope"] = tel
    await SequenceEngine(hub)._wind_down(park=True, warm=False)

    assert tel.connect_calls == 1, "the link must be reopened, not stepped over"
    assert tel.parked is True, "and the park must then actually run"


async def test_a_mount_that_cannot_be_reopened_says_so_loudly(bus_lines):
    """A wind-down step must never raise (it would strand the cooler and the
    roof behind it) — but it must not go quiet either."""
    from astrodeck.sequence.engine import SequenceEngine

    hub = Hub()
    tel = _ParkTel(connected=False, reconnects_ok=False)
    hub.devices["telescope"] = tel
    await SequenceEngine(hub)._wind_down(park=True, warm=False)   # must not raise

    errs = [m for lvl, m, _s in bus_lines if lvl == "error"]
    assert any("could not reopen the mount's link" in m for m in errs), errs


async def test_a_run_that_does_not_park_says_the_mount_is_still_tracking(bus_lines):
    """The actual 03:48 gap. "complete: 150 frames" and silence is indis-
    tinguishable from a run that parked."""
    from astrodeck.sequence.engine import SequenceEngine

    hub = Hub()
    hub.devices["telescope"] = _ParkTel()
    engine = SequenceEngine(hub)
    engine._frames_done = 150
    await engine._wind_down(park=False, warm=False)

    said = [m for _l, m, _s in bus_lines]
    assert any("still tracking" in m for m in said), (
        f"a run that deliberately leaves the mount live must SAY so: {said}")


async def test_that_line_is_a_warning_when_no_dawn_net_will_catch_it(
        bus_lines, monkeypatch):
    """Level follows the actual exposure. Dawn park is the net under this
    choice; when that net cannot act, nothing at all is watching."""
    from astrodeck import dawn_park as dp
    from astrodeck.sequence.engine import SequenceEngine

    monkeypatch.setenv(dp.NO_DAWN_PARK_ENV_VAR, "1")
    hub = Hub()
    hub.devices["telescope"] = _ParkTel()
    engine = SequenceEngine(hub)
    engine._frames_done = 150
    await engine._wind_down(park=False, warm=False)

    hit = [(lvl, m) for lvl, m, _s in bus_lines if "still tracking" in m]
    assert hit and hit[0][0] == "warning", hit
    assert "nothing will stop it" in hit[0][1].lower(), hit
