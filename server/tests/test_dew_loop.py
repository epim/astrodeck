"""D-RIG-3: the dew-margin heater loop (``astrodeck/dew.py``).

The loop turns one number - air temperature minus dew point - into a heater
level, on two kinds of hardware whose units do not agree, without ever
stampeding over a level a human set by hand. These tests pin the four things
that can each silently ruin a night:

1. THE RAMP. Two thresholds with a linear ramp between them, clamped at both
   ends. An inverted or unclamped ramp heats hardest on the driest night, and
   nothing about the panel would look wrong.
2. NEVER A DEFAULT POWER. With no weather reading the loop commands NOTHING.
   The tempting bug is a ``0`` fallback, which switches off a heater somebody
   set by hand on the one night the network was down.
3. UNITS. A percentage is not a device value: a UPB dew port is an 8-bit PWM
   register, so 50% is 128, not 50. Writing the percentage straight through
   gives a fifth of the requested heat and no error anywhere.
4. THE MANUAL OVERRIDE. A hand-set level suppresses following for
   ``manual_override_s``, and following resumes ON ITS OWN afterwards - it must
   re-command even when the target has not moved, because the heater is no
   longer where the loop last left it.

The devices are fakes that REMEMBER. ``FakeCamera.writes`` and
``FakeSwitch.calls`` are the whole point: "the heater is at 50" and "the loop
commanded 50" are different claims, and only the second one is what this module
is responsible for.
"""
from __future__ import annotations

import math

import pytest

from astrodeck import dew as dew_mod
from astrodeck import power_guard
from astrodeck.config import DewConfig
from astrodeck.devices.base import DeviceError, SwitchPort
from astrodeck.dew import DewController, ramp_power, scale_to_port


# --------------------------------------------------------------------- fakes

class Clock:
    """A wall clock somebody else winds. ``DewController`` takes ``clock`` for
    exactly this reason (and defaults to ``None``, never to ``time.time``, so
    the injection cannot be silently bypassed)."""

    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeCamera:
    """``writes`` is the record of what was COMMANDED, which is the only thing
    the loop is answerable for.

    ``attempts`` is recorded BEFORE the refusal, and it is the difference
    between "the loop skipped a camera that has no heater" and "the loop tried
    every camera and swallowed the exception". Both leave ``writes`` empty, so a
    test that only looked at ``writes`` would pass with the capability check
    deleted - which is exactly what the first run of this file did.
    """

    def __init__(self, *, has_dew_heater: bool = True, connected: bool = True,
                 fail: bool = False) -> None:
        self.connected = connected
        self.has_dew_heater = has_dew_heater
        self.name = "FakeCamera"
        self.attempts: list[int] = []
        self.writes: list[int] = []
        self._fail = fail

    async def set_dew_heater(self, power: int) -> None:
        # Mirrors ``devices/base.py:213``: a camera without one RAISES, which is
        # why the loop has to check the capability rather than try and catch.
        self.attempts.append(int(power))
        if self._fail or not self.has_dew_heater:
            raise DeviceError(f"{self.name} has no dew heater")
        self.writes.append(int(power))


class FakeSwitch:
    def __init__(self, ports: list[SwitchPort]) -> None:
        self.connected = True
        self.ports = ports
        self.calls: list[tuple[int, float]] = []

    async def get_ports(self) -> list[SwitchPort]:
        return self.ports

    async def set_port(self, port_id: int, value: float) -> None:
        self.calls.append((port_id, value))
        for p in self.ports:
            if p.id == port_id:
                p.value = value


class FakeHub:
    """Only what the loop touches: a role->device mapping and an attribute slot
    for ``dew_controller``."""

    def __init__(self, camera=None, switch=None) -> None:
        self.devices: dict[str, object] = {}
        if camera is not None:
            self.devices["camera"] = camera
        if switch is not None:
            self.devices["switch"] = switch


class FakeWeather:
    """``reads`` counts calls, so "the disabled loop reads no weather" is an
    assertion and not a hope."""

    def __init__(self, reading: dict | None = None) -> None:
        self.reading = reading
        self.reads = 0

    def surface_now(self, ts: float) -> dict | None:
        self.reads += 1
        return self.reading


def _reading(temp_c, dewpoint_c) -> dict:
    """The shape ``weather.surface_now`` returns - every key, because a loop
    that happened to work on a three-key dict would break on the real one."""
    return {"ts": "2026-09-10T04:00:00Z", "temp_c": temp_c,
            "dewpoint_c": dewpoint_c, "humidity_pct": 90.0, "wind_kmh": 3.0,
            "wind_dir_deg": 180.0, "gust_kmh": 5.0, "cloud_base_m": 900.0}


def _port(port_id: int, name: str, *, lo: float = 0.0, hi: float = 1.0,
          value: float = 0.0, boolean: bool = True) -> SwitchPort:
    return SwitchPort(id=port_id, name=name, can_write=True, is_boolean=boolean,
                      value=value, min=lo, max=hi)


# ------------------------------------------------------------------ fixtures

@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """A per-TEST config dir and config store.

    ``tests/conftest.py::_never_touch_the_real_config`` is session-scoped: it
    guarantees nothing writes the developer's real ``server/config/``, but every
    test in the run shares ONE throwaway directory, so a ``follow_dew`` flag set
    by one test would be read by the next.

    ``dew_mod.config_store`` is patched TOO, and that is not belt-and-braces:
    ``dew.py`` does ``from .config import config_store`` at module scope (as the
    task specifies), which binds the object, not the module attribute. Patching
    only ``config_mod.config_store`` - which is what ``power_guard`` needs,
    because it imports lazily inside its functions - would leave the loop
    reading the shared store while the port settings came from the isolated one.
    """
    import astrodeck.config as config_mod
    from astrodeck.config import ConfigStore
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(dew_mod, "config_store", store)
    power_guard._corrupt_warned.clear()
    yield
    power_guard._corrupt_warned.clear()


def _set_dew(**kwargs) -> DewConfig:
    """Install a dew policy on the isolated store and hand it back."""
    cfg = DewConfig(**kwargs)
    dew_mod.config_store.cfg().dew = cfg
    return cfg


def _controller(*, camera=None, switch=None, weather=None, clock=None,
                **dew_kwargs):
    _set_dew(**dew_kwargs)
    hub = FakeHub(camera=camera, switch=switch)
    ctl = DewController(hub, clock=clock or Clock(), weather=weather)
    return ctl, hub


# ---------------------------------------------------------------- the ramp

@pytest.mark.parametrize("margin, expected", [
    (1.0, 100),      # at margin_full_c: full power
    (5.0, 0),        # at margin_off_c: the floor
    (3.0, 50),       # halfway along the ramp
    (-2.0, 100),     # below the bottom threshold - CLAMPED, not extrapolated
    (20.0, 0),       # far above the top threshold - CLAMPED
])
async def test_the_ramp_maps_margin_to_power(margin, expected):
    """The whole policy, graded THROUGH THE TICK.

    SABOTAGE: invert the ramp (``max_power`` at the dry end, ``min_power`` at
    the wet end). It heats hardest on the driest night and not at all when the
    glass is about to fog, and nothing about the panel would look wrong.

    Graded through the tick and not through ``ramp_power`` alone: a correct ramp
    function that the tick never calls (or calls with the humidity) is exactly
    the shape of bug this file exists to catch. The camera write is the
    evidence.
    """
    cam = FakeCamera()
    weather = FakeWeather(_reading(temp_c=10.0, dewpoint_c=10.0 - margin))
    ctl, _hub = _controller(camera=cam, weather=weather, enabled=True,
                            margin_full_c=1.0, margin_off_c=5.0,
                            min_power=0, max_power=100)
    # Precondition: the reading really does carry the margin under test, so an
    # assertion that passes because the loop saw nothing proves nothing.
    assert weather.reading["temp_c"] - weather.reading["dewpoint_c"] == pytest.approx(margin)

    await ctl.tick()

    assert cam.writes == [expected]
    assert ctl.snapshot()["power_pct"] == expected
    assert ctl.snapshot()["margin_c"] == pytest.approx(margin)


async def test_the_floor_is_a_floor_not_a_zero():
    """With ``min_power=20`` the driest sky still leaves the heater at 20%.

    SABOTAGE: drop ``min_power`` from the ramp entirely (``lo = 0``). It has to
    be the whole floor and not one of its two enforcements: the branch and the
    trailing clamp in ``ramp_power`` each hold the bound on their own, so
    breaking either alone leaves the answers correct - which is the point of
    having both, and was measured rather than assumed.

    An operator who says "never below 20" is describing hardware that dews at
    the drop of a hat, or a strip that takes ten minutes to come back from cold;
    a ramp that honours the ceiling and silently drops the floor is off every
    time the sky is briefly dry.
    """
    cam = FakeCamera()
    weather = FakeWeather(_reading(temp_c=25.0, dewpoint_c=5.0))    # margin 20
    ctl, _hub = _controller(camera=cam, weather=weather, enabled=True,
                            margin_full_c=1.0, margin_off_c=5.0,
                            min_power=20, max_power=100)
    await ctl.tick()
    assert cam.writes == [20]
    # And the pure function agrees, so the constant is not coming from somewhere
    # else that happens to be 20.
    assert ramp_power(DewConfig(min_power=20), 20.0) == 20


# ------------------------------------------------------- no weather reading

async def test_no_weather_reading_commands_nothing():
    """SABOTAGE: an ``else: power = 0`` fallback on the missing-reading path.

    The heater keeps the level it already had. A fallback of 0 would switch off
    a heater somebody set by hand, on the one night the forecast fetch failed -
    which is, by construction, a night with weather in it.
    """
    cam = FakeCamera()
    weather = FakeWeather(_reading(temp_c=10.0, dewpoint_c=7.0))    # margin 3
    ctl, _hub = _controller(camera=cam, weather=weather, enabled=True)
    await ctl.tick()
    assert cam.writes == [50], "precondition: the loop drove the heater first"

    weather.reading = None
    await ctl.tick()
    assert cam.writes == [50], "a missing reading must command NOTHING"
    snap = ctl.snapshot()
    assert snap["reason"] == "no dew-point reading"
    assert snap["margin_c"] is None and snap["temp_c"] is None
    # The last level is still reported, because that is where the heater is.
    assert snap["power_pct"] == 50


async def test_a_half_reading_is_no_reading():
    """A sample with a temperature but no dew point is not half a decision."""
    cam = FakeCamera()
    weather = FakeWeather(_reading(temp_c=10.0, dewpoint_c=None))
    ctl, _hub = _controller(camera=cam, weather=weather, enabled=True)
    await ctl.tick()
    assert cam.writes == []
    assert ctl.snapshot()["reason"] == "no dew-point reading"


# ------------------------------------------------------------- the camera

async def test_a_camera_with_no_dew_heater_is_skipped():
    """SABOTAGE: drop the ``has_dew_heater`` check and let the DeviceError fly.

    Most cameras have no window heater and ``set_dew_heater`` RAISES on them
    (devices/base.py:213). If that reaches the tick, the switch ports below it
    never get their write - so one un-heatable camera would silently disable
    every dew strip on the rig.
    """
    cam = FakeCamera(has_dew_heater=False)
    sw = FakeSwitch([_port(1, "Dew A", lo=0.0, hi=255.0, boolean=False)])
    power_guard.set_port_settings(1, follow_dew=True)
    weather = FakeWeather(_reading(temp_c=10.0, dewpoint_c=7.0))    # margin 3
    ctl, _hub = _controller(camera=cam, switch=sw, weather=weather, enabled=True)

    await ctl.tick()        # must not raise

    assert cam.attempts == [], "the loop must SKIP it, not try it and catch"
    assert cam.writes == []
    assert sw.calls == [(1, 128.0)], "the port still got its tick"
    assert ctl.snapshot()["power_pct"] == 50


async def test_a_camera_write_that_fails_never_reaches_the_tick():
    """A heater that refuses one write must not stop the loop or the ports."""
    cam = FakeCamera(fail=True)
    sw = FakeSwitch([_port(1, "Dew A", lo=0.0, hi=255.0, boolean=False)])
    power_guard.set_port_settings(1, follow_dew=True)
    weather = FakeWeather(_reading(temp_c=10.0, dewpoint_c=7.0))
    ctl, _hub = _controller(camera=cam, switch=sw, weather=weather, enabled=True)

    await ctl.tick()

    assert cam.attempts == [50], "this one IS tried - it claims a heater"
    assert cam.writes == []
    assert sw.calls == [(1, 128.0)]


async def test_the_camera_window_can_be_turned_off_on_its_own():
    """``camera_window=False`` is a rig whose corrector is already heated by a
    strip; the ports still follow."""
    cam = FakeCamera()
    sw = FakeSwitch([_port(1, "Dew A", lo=0.0, hi=255.0, boolean=False)])
    power_guard.set_port_settings(1, follow_dew=True)
    weather = FakeWeather(_reading(temp_c=10.0, dewpoint_c=7.0))
    ctl, _hub = _controller(camera=cam, switch=sw, weather=weather,
                            enabled=True, camera_window=False)
    await ctl.tick()
    assert cam.writes == []
    assert sw.calls == [(1, 128.0)]


# -------------------------------------------------------------- the ports

async def test_a_port_without_follow_dew_is_never_written():
    """SABOTAGE: drop the ``power_guard.port_settings`` check and drive every
    port.

    The dew loop shares the power box with the mount, the camera and the USB
    hub. A loop that wrote every port would put the mount's relay to 128 at
    dusk, which on a boolean port means "on" and on a mount that was off means
    a rig that powers itself up unattended.
    """
    sw = FakeSwitch([_port(1, "Mount"), _port(2, "USB hub"),
                     _port(3, "Dew A", lo=0.0, hi=255.0, boolean=False)])
    power_guard.set_port_settings(3, follow_dew=True)
    # Precondition: the other two really are unset, not merely absent.
    assert power_guard.port_settings(1)["follow_dew"] is False
    assert power_guard.port_settings(3)["follow_dew"] is True

    weather = FakeWeather(_reading(temp_c=10.0, dewpoint_c=7.0))
    ctl, _hub = _controller(switch=sw, weather=weather, enabled=True)
    await ctl.tick()

    assert [c[0] for c in sw.calls] == [3]
    assert [row["id"] for row in ctl.snapshot()["ports"]] == [3]


async def test_port_scaling_uses_the_ports_own_range():
    """SABOTAGE: write the percentage straight through (``set_port(id, power)``).

    A Pegasus UPB dew port is an 8-bit PWM register. 50% of it is 128. Writing
    50 to it is 20% heat - enough to look like it is working and not enough to
    hold the dew off, all night, with the panel reporting 50%.
    """
    upb = _port(1, "Dew A", lo=0.0, hi=255.0, boolean=False)
    sw = FakeSwitch([upb])
    power_guard.set_port_settings(1, follow_dew=True)
    weather = FakeWeather(_reading(temp_c=10.0, dewpoint_c=7.0))    # margin 3
    ctl, _hub = _controller(switch=sw, weather=weather, enabled=True)

    await ctl.tick()

    assert ctl.snapshot()["power_pct"] == 50, "precondition: the ramp asked for 50%"
    assert sw.calls == [(1, 128.0)]
    assert sw.calls[0][1] != 50, "the percentage must not go through raw"
    # And the pure mapping is honest at both ends.
    assert scale_to_port(upb, 0) == 0.0
    assert scale_to_port(upb, 100) == 255.0


# ---------------------------------------------------------- manual override

async def test_a_manual_write_pauses_following_and_it_resumes_by_itself():
    """SABOTAGE (two): (a) make ``note_manual`` stamp nothing, so the next tick
    stampedes over the hand-set level; (b) keep the last-commanded memory across
    ``note_manual``, so the resumed loop declines to re-command.

    (b) is the subtle one. The loop last commanded 50; the operator sets 90 by
    hand; two hours later the ramp still wants 50, which is within the 5-point
    threshold of the loop's OWN last write - so a loop that kept its memory
    would send nothing and leave the heater at 90% for the rest of the night
    while the panel said 50%.
    """
    clock = Clock()
    cam = FakeCamera()
    weather = FakeWeather(_reading(temp_c=10.0, dewpoint_c=7.0))    # margin 3
    ctl, _hub = _controller(camera=cam, weather=weather, clock=clock,
                            enabled=True, manual_override_s=7200)
    await ctl.tick()
    assert cam.writes == [50], "precondition: the loop was following"

    ctl.note_manual("camera")           # the operator drags the slider to 90%
    assert ctl.snapshot() is not None

    clock.advance(3600.0)               # halfway through the override
    await ctl.tick()
    assert cam.writes == [50], "a paused tick commands nothing"
    snap = ctl.snapshot()
    assert snap["following"] is False
    assert snap["override_until_ts"] == pytest.approx(1_000_000.0 + 7200.0)
    assert "paused" in snap["reason"]
    assert snap["power_pct"] == 50, "the target is still reported while paused"

    clock.advance(3700.0)               # past the override
    await ctl.tick()
    assert cam.writes == [50, 50], "following resumes and RE-COMMANDS"
    snap = ctl.snapshot()
    assert snap["following"] is True
    assert snap["override_until_ts"] is None


async def test_a_manual_port_write_pauses_only_that_ports_memory():
    """A hand write to one port must not make the loop forget the others."""
    clock = Clock()
    sw = FakeSwitch([_port(1, "Dew A", lo=0.0, hi=255.0, boolean=False),
                     _port(2, "Dew B", lo=0.0, hi=255.0, boolean=False)])
    power_guard.set_port_settings(1, follow_dew=True)
    power_guard.set_port_settings(2, follow_dew=True)
    weather = FakeWeather(_reading(temp_c=10.0, dewpoint_c=7.0))
    ctl, _hub = _controller(switch=sw, weather=weather, clock=clock,
                            enabled=True, manual_override_s=60)
    await ctl.tick()
    assert sw.calls == [(1, 128.0), (2, 128.0)]

    ctl.note_manual("switch", 1)
    clock.advance(120.0)                # the override has expired
    await ctl.tick()
    # Port 1's memory was dropped, so it is re-commanded; port 2's was not, and
    # the target has not moved, so it is left alone.
    assert sw.calls == [(1, 128.0), (2, 128.0), (1, 128.0)]


async def test_a_manual_write_to_a_port_the_loop_does_not_drive_is_no_override():
    """SABOTAGE: drop the ``_drives`` filter and let every port write pause the
    loop.

    ``POST /api/switch/set`` calls ``note_manual`` for EVERY port - the route has
    no business knowing which ports are dew strips - so somebody power-cycling
    the USB hub at 22:00 would switch the heaters off the weather until
    midnight, with nothing to see but a ``reason`` string.
    """
    clock = Clock()
    cam = FakeCamera()
    sw = FakeSwitch([_port(1, "Mount"), _port(2, "Dew A", lo=0.0, hi=255.0,
                                              boolean=False)])
    power_guard.set_port_settings(2, follow_dew=True)
    weather = FakeWeather(_reading(temp_c=10.0, dewpoint_c=7.0))
    ctl, _hub = _controller(camera=cam, switch=sw, weather=weather, clock=clock,
                            enabled=True, manual_override_s=7200)

    ctl.note_manual("switch", 1)        # the mount relay - not a dew instruction
    await ctl.tick()
    assert ctl.snapshot()["following"] is True
    assert cam.writes == [50] and sw.calls == [(2, 128.0)]

    ctl.note_manual("switch", 2)        # the dew port - this one IS
    clock.advance(60.0)
    await ctl.tick()
    assert ctl.snapshot()["following"] is False


async def test_note_manual_never_raises():
    """SABOTAGE: drop the outer guard in ``note_manual``.

    It is called from inside a request that has ALREADY changed the hardware.
    An exception there would 500 a write that succeeded, telling the operator
    their heater did not move when it did - the loop's bookkeeping is not
    allowed to be that expensive.
    """
    def boom():
        raise RuntimeError("no clock")

    ctl, _hub = _controller(clock=boom, enabled=True)
    ctl.note_manual("camera")           # must not raise
    ctl.note_manual("switch", 1)
    assert ctl.snapshot() is None


async def test_an_override_of_zero_never_expires_on_its_own():
    """``manual_override_s = 0`` means "until I say otherwise" (config.DewConfig),
    which is infinity - the opposite reading would make the safest-looking
    setting the one that ignores the operator on the very next tick."""
    clock = Clock()
    cam = FakeCamera()
    weather = FakeWeather(_reading(temp_c=10.0, dewpoint_c=7.0))
    ctl, _hub = _controller(camera=cam, weather=weather, clock=clock,
                            enabled=True, manual_override_s=0)
    ctl.note_manual("camera")
    clock.advance(365 * 24 * 3600.0)
    await ctl.tick()
    assert cam.writes == []
    snap = ctl.snapshot()
    assert snap["following"] is False
    # Infinity is not JSON; the pair (following=False, override_until_ts=None)
    # plus the reason is how a never-expiring override is reported.
    assert snap["override_until_ts"] is None
    assert "does not expire" in snap["reason"]
    assert ctl._override_until == math.inf


# ------------------------------------------------------- the change threshold

async def test_an_unchanged_level_is_not_recommanded():
    """SABOTAGE: drop the 5-point threshold and write every tick.

    A dew strip has a thermal time constant of minutes; a 2-point correction
    changes nothing measurable and costs a device round trip on a bus that is
    also carrying the guide camera. Sample noise alone would otherwise write
    both heaters every two minutes for ten hours.
    """
    cam = FakeCamera()
    weather = FakeWeather(_reading(temp_c=10.0, dewpoint_c=7.0))    # margin 3 -> 50
    ctl, _hub = _controller(camera=cam, weather=weather, enabled=True)
    await ctl.tick()
    assert cam.writes == [50]

    weather.reading = _reading(temp_c=10.0, dewpoint_c=6.92)        # 3.08 -> 48
    await ctl.tick()
    assert ctl.snapshot()["power_pct"] == 48, "precondition: the target DID move"
    assert cam.writes == [50], "2 points is not worth a write"

    weather.reading = _reading(temp_c=10.0, dewpoint_c=8.0)         # margin 2 -> 75
    await ctl.tick()
    assert cam.writes == [50, 75], "25 points is"


# ------------------------------------------------------------------ snapshot

async def test_snapshot_is_none_before_the_first_tick():
    """None is a real state. "The loop has not run yet" and "the loop ran and
    found nothing to do" are different things to show, and a status node that
    cannot tell them apart reports an idle heater for the first two minutes of
    every boot."""
    cam = FakeCamera()
    weather = FakeWeather(_reading(temp_c=10.0, dewpoint_c=7.0))
    ctl, _hub = _controller(camera=cam, weather=weather, enabled=True)
    assert ctl.snapshot() is None

    await ctl.tick()
    snap = ctl.snapshot()
    assert set(snap) == {"enabled", "following", "override_until_ts", "margin_c",
                         "temp_c", "dewpoint_c", "power_pct", "reason", "ports"}
    assert snap["enabled"] is True and snap["following"] is True
    assert snap["temp_c"] == 10.0 and snap["dewpoint_c"] == 7.0
    assert snap["margin_c"] == pytest.approx(3.0)
    assert snap["power_pct"] == 50
    assert isinstance(snap["reason"], str) and snap["reason"]


async def test_the_reason_carries_no_weather_number():
    """SABOTAGE: put the margin back in the reason
    (``f"margin {margin:.1f} C - heaters at {power}%"``).

    S7a's ``_strip_dew`` removes ``margin_c``/``temp_c``/``dewpoint_c`` and nulls
    ``power_pct`` for a principal without ``view.weather``. A KEY FILTER CANNOT
    WITHHOLD A NUMBER SPELLED OUT IN A SENTENCE - the same shape as the
    2026-08 finding where a viewer geolocated the rig to 2.9 km through a
    derived value. So the following-path reason is digit-free at the source, and
    the numbers stay in the structured fields, which are strippable.
    """
    cam = FakeCamera()
    weather = FakeWeather(_reading(temp_c=17.3, dewpoint_c=13.3))   # margin 4.0
    ctl, _hub = _controller(camera=cam, weather=weather, enabled=True)
    await ctl.tick()

    snap = ctl.snapshot()
    assert snap["power_pct"] == 25, "precondition: there ARE numbers to leak"
    assert snap["margin_c"] == pytest.approx(4.0)
    assert not any(ch.isdigit() for ch in snap["reason"]), snap["reason"]

    # And the same on the tick that finds nothing worth commanding.
    await ctl.tick()
    assert not any(ch.isdigit() for ch in ctl.snapshot()["reason"])


async def test_the_snapshot_is_a_copy():
    """The caller redacts (``_strip_dew``) and serialises it; neither should be
    able to reach into the loop's own state."""
    sw = FakeSwitch([_port(1, "Dew A", lo=0.0, hi=255.0, boolean=False)])
    power_guard.set_port_settings(1, follow_dew=True)
    weather = FakeWeather(_reading(temp_c=10.0, dewpoint_c=7.0))
    ctl, _hub = _controller(switch=sw, weather=weather, enabled=True)
    await ctl.tick()

    snap = ctl.snapshot()
    assert snap["ports"] == [{"id": 1, "name": "Dew A", "follow_dew": True,
                              "value": 128.0}]
    snap["power_pct"] = 999
    snap["ports"][0]["value"] = 999
    again = ctl.snapshot()
    assert again["power_pct"] == 50
    assert again["ports"][0]["value"] == 128.0


async def test_the_controller_attaches_itself_to_the_hub():
    """S7c's status node reads ``getattr(hub, "dew_controller", None)`` so
    ``hub.py`` keeps no import of a service it does not own."""
    ctl, hub = _controller(enabled=True)
    assert hub.dew_controller is ctl


# ------------------------------------------------------------------ disabled

async def test_disabled_touches_nothing():
    """SABOTAGE: read the weather before the ``enabled`` check.

    The loop is started UNCONDITIONALLY (a config flag that only took effect on
    the next restart would take effect on the next NIGHT), so the disabled tick
    is the common case on most rigs and has to cost one attribute read. A
    weather read here would also make a disabled feature responsible for an
    outbound fetch.
    """
    cam = FakeCamera()
    sw = FakeSwitch([_port(1, "Dew A", lo=0.0, hi=255.0, boolean=False)])
    power_guard.set_port_settings(1, follow_dew=True)
    weather = FakeWeather(_reading(temp_c=10.0, dewpoint_c=7.0))
    ctl, _hub = _controller(camera=cam, switch=sw, weather=weather,
                            enabled=False)

    await ctl.tick()

    assert weather.reads == 0, "a disabled loop reads no weather"
    assert cam.writes == [] and sw.calls == []
    snap = ctl.snapshot()
    assert snap["enabled"] is False and snap["following"] is False
    assert snap["power_pct"] is None and snap["ports"] == []
