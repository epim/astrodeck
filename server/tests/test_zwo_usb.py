"""ZWO native accessories: SDK loader/bindings, EafFocuser, CaaRotator,
zwo-usb backend + framework integration. Hardware-free (fake SDK doubles);
the vendored-DLL export test runs only where the DLLs exist."""
from __future__ import annotations

import pytest

from astrodeck.devices import zwo_sdk
from astrodeck.devices.zwo_sdk import ZwoSdkError


def test_sdk_error_carries_code_and_fn():
    e = ZwoSdkError(7, "CAAMoveToMechanical")
    assert e.code == 7 and e.fn == "CAAMoveToMechanical"
    assert "CAAMoveToMechanical" in str(e) and "7" in str(e)


import sys  # noqa: E402

_VENDORED = [p for p in (zwo_sdk._VENDOR_DIR / n for n in zwo_sdk._DLL_SPECS)
             if p.is_file()]


@pytest.mark.skipif(sys.platform != "win32" or len(_VENDORED) < 2,
                    reason="win-x64 vendored DLLs only (Linux .so deferred)")
def test_vendored_dlls_export_api():
    """Filenames lie; exports don't. The vendored DLLs must carry the full API
    (the ASIStudio CAA_SRC.dll rejection story — see vendor/zwo/README.md)."""
    for basename, (_alts, exports) in zwo_sdk._DLL_SPECS.items():
        dll = zwo_sdk._loads_with_exports(zwo_sdk._VENDOR_DIR / basename, exports)
        assert dll is not None, f"{basename} failed load/export verification"


# ------------------------------------------------------- transport="local"

def test_driverentry_local_needs_no_addressing():
    from astrodeck.config import DriverEntry
    e = DriverEntry(id="zwo-usb-ab12", type="zwo-usb", transport="local")
    assert e.transport == "local" and e.host == "" and e.port_path == ""


def test_add_driver_local(tmp_path, monkeypatch):
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    e = config_store.add_driver("zwo-usb", transport="local")
    assert e.id.startswith("zwo-usb-") and e.transport == "local"
    assert e.label == "ZWO-USB (USB)"


# --------------------------------------------------------------- fake SDKs

import asyncio  # noqa: E402

from astrodeck.devices.base import DeviceError  # noqa: E402
import astrodeck.devices.backends.zwo_usb as zu  # noqa: E402


class FakeEafSdk:
    """Scripted EAF SDK double: positions list, moving-poll sequence, per-method
    raisables, and a call log."""

    def __init__(self, *, count=1, name="EAF", max_step=60000, position=18128,
                 moving_seq=None, temp=11.5, firmware_="3.3.8", raise_on=None):
        self._count = count
        self._name, self._max = name, max_step
        self.position = position
        self.moving_seq = list(moving_seq or [])
        self.temp = temp
        self._fw = firmware_
        self.raise_on = dict(raise_on or {})   # method -> ZwoSdkError
        self.calls: list[str] = []

    def _log(self, m):
        self.calls.append(m)
        if m in self.raise_on:
            raise self.raise_on[m]

    def count(self):
        self._log("count"); return self._count
    def get_id(self, i):
        self._log("get_id"); return 10 + i
    def open(self, d):
        self._log("open")
    def close(self, d):
        self._log("close")
    def get_property(self, d):
        self._log("get_property"); return self._name, self._max
    def move(self, d, step):
        self._log("move"); self.target = step
    def stop(self, d):
        self._log("stop"); self.moving_seq = []
    def is_moving(self, d):
        self._log("is_moving")
        if self.moving_seq:
            m = self.moving_seq.pop(0)
            if not m:
                self.position = getattr(self, "target", self.position)
            return m, False
        self.position = getattr(self, "target", self.position)
        return False, False
    def get_position(self, d):
        self._log("get_position"); return self.position
    def get_temp(self, d):
        self._log("get_temp"); return self.temp
    def firmware(self, d):
        self._log("firmware"); return self._fw


@pytest.fixture(autouse=True)
def _fast_polls(monkeypatch):
    monkeypatch.setattr(zu, "POLL_S", 0.01)


@pytest.fixture(autouse=True)
def _focuser_state(tmp_path, monkeypatch):
    """Isolate the remembered-position store — AUTOUSE, because every connect
    reads it and every completed move writes it. Opt-in isolation leaks: one
    test's final position becomes the next test's "the focuser lost its count"
    warning, which is a real defect this module must not manufacture."""
    from astrodeck import config as cfg
    monkeypatch.setattr(cfg, "FOCUSER_STATE_FILE",
                        tmp_path / "focuser_state.json")
    return tmp_path / "focuser_state.json"


# ---------------------------------------------------------------- EafFocuser

async def test_eaf_connect_and_props():
    sdk = FakeEafSdk()
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    assert f.connected and f.max_position == 60000
    assert f.describe()["firmware"] == "3.3.8"
    assert await f.get_position() == 18128
    assert await f.get_temperature() == 11.5


async def test_eaf_move_polls_to_completion():
    sdk = FakeEafSdk(moving_seq=[True, True, False])
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    await f.move_to(20000)
    assert await f.get_position() == 20000
    assert "move" in sdk.calls and sdk.calls.count("is_moving") >= 3


async def test_eaf_move_bounds_checked():
    f = zu.EafFocuser(FakeEafSdk(max_step=1000), 10)
    await f.connect()
    with pytest.raises(DeviceError, match="range"):
        await f.move_to(5000)


async def test_eaf_cancel_mid_move_halts():
    sdk = FakeEafSdk(moving_seq=[True] * 10_000)
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    task = asyncio.create_task(f.move_to(30000))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "stop" in sdk.calls


async def test_eaf_move_timeout_halts(monkeypatch):
    monkeypatch.setattr(zu, "EAF_MOVE_TIMEOUT_S", 0.05)
    sdk = FakeEafSdk(moving_seq=[True] * 10_000)
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    with pytest.raises(DeviceError, match="settle|timed out|did not happen"):
        await f.move_to(30000)
    assert "stop" in sdk.calls


async def test_eaf_sdk_error_surfaces_named():
    sdk = FakeEafSdk(raise_on={"move": ZwoSdkError(12, "EAFMove")})
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    with pytest.raises(DeviceError, match="EAFMove"):
        await f.move_to(20000)


async def test_eaf_temp_none_on_error():
    sdk = FakeEafSdk(raise_on={"get_temp": ZwoSdkError(2, "EAFGetTemp")})
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    assert await f.get_temperature() is None


async def test_eaf_reports_motion_for_the_status_readout():
    """The status poll needs the SAME EAFIsMoving truth ``move_to`` waits on, so
    the UI can tell a move under way from a move that was silently refused."""
    sdk = FakeEafSdk(moving_seq=[True, False])
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    assert await f.is_moving() is True
    assert await f.is_moving() is False


async def test_eaf_motion_read_surfaces_sdk_failure():
    sdk = FakeEafSdk(raise_on={"is_moving": ZwoSdkError(3, "EAFIsMoving")})
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    with pytest.raises(DeviceError, match="EAFIsMoving"):
        await f.is_moving()


# ---------------------------------------------------------------- CaaRotator

class FakeCaaSdk:
    """Scripted CAA SDK double, mirroring FakeEafSdk. ``CAACurDegree`` is
    deliberately present so the contract test can prove we never call it."""

    #: SDK error codes (CAA_API.h enum order): STALL=12 per the header family.
    STALL = 12

    def __init__(self, *, count=1, degree=290.77, moving_seq=None, temp=9.0,
                 reverse=False, type_="CAA-M54", firmware_="1.2.0",
                 hand_control=False, raise_on=None):
        self._count = count
        self.degree = degree
        self.moving_seq = list(moving_seq or [])
        self.temp = temp
        self.reverse = reverse
        self._type, self._fw = type_, firmware_
        self.hand = hand_control
        self.raise_on = dict(raise_on or {})
        self.calls: list[str] = []

    def _log(self, m):
        self.calls.append(m)
        if m in self.raise_on:
            raise self.raise_on[m]

    def count(self):
        self._log("count"); return self._count
    def get_id(self, i):
        self._log("get_id"); return 20 + i
    def open(self, d):
        self._log("open")
    def close(self, d):
        self._log("close")
    def get_property(self, d):
        self._log("get_property"); return "CAA", 360
    def move_to_mechanical(self, d, deg):
        self._log("move_to_mechanical"); self.target = deg
    def stop(self, d):
        self._log("stop"); self.moving_seq = []
    def is_moving(self, d):
        self._log("is_moving")
        if self.moving_seq:
            m = self.moving_seq.pop(0)
            if not m:
                self.degree = getattr(self, "target", self.degree)
            return m, self.hand
        self.degree = getattr(self, "target", self.degree)
        return False, self.hand
    def get_degree(self, d):
        self._log("get_degree"); return self.degree
    def get_temp(self, d):
        self._log("get_temp"); return self.temp
    def get_reverse(self, d):
        self._log("get_reverse"); return self.reverse
    def set_reverse(self, d, v):
        self._log("set_reverse"); self.reverse = v
    def get_type(self, d):
        self._log("get_type"); return self._type
    def firmware(self, d):
        self._log("firmware"); return self._fw
    def cur_degree(self, d, v):                      # the FORBIDDEN sync
        self._log("CAACurDegree")


async def test_caa_connect_and_mechanical_reads():
    sdk = FakeCaaSdk()
    r = zu.CaaRotator(sdk, 20)
    await r.connect()
    assert r.connected and r.can_reverse is True
    assert r.describe()["firmware"] == "1.2.0"
    assert abs(await r.get_mechanical_position() - 290.77) < 1e-6


async def test_caa_move_and_sky_sync_never_touch_sdk_sync():
    """The base Rotator owns sync client-side; the SDK's CAACurDegree must
    NEVER be called — through connect, mechanical move, sync(), and a sky
    move_to() through the offset."""
    sdk = FakeCaaSdk(moving_seq=[True, False])
    r = zu.CaaRotator(sdk, 20)
    await r.connect()
    await r.move_mechanical(300.0)
    assert abs(await r.get_mechanical_position() - 300.0) < 1e-6
    await r.sync(10.0)                       # declare mech 300 == sky 10
    assert abs(await r.get_position() - 10.0) < 1e-6
    sdk.moving_seq = [True, False]
    await r.move_to(20.0)                    # sky 20 -> mech 310 via offset
    assert abs(await r.get_mechanical_position() - 310.0) < 1e-6
    assert "CAACurDegree" not in sdk.calls   # the contract


async def test_caa_cancel_mid_move_halts():
    sdk = FakeCaaSdk(moving_seq=[True] * 10_000)
    r = zu.CaaRotator(sdk, 20)
    await r.connect()
    task = asyncio.create_task(r.move_mechanical(10.0))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "stop" in sdk.calls


async def test_caa_stall_surfaces_named():
    sdk = FakeCaaSdk(raise_on={"move_to_mechanical":
                               ZwoSdkError(FakeCaaSdk.STALL, "CAAMoveToMechanical")})
    r = zu.CaaRotator(sdk, 20)
    await r.connect()
    with pytest.raises(DeviceError, match="CAAMoveToMechanical"):
        await r.move_mechanical(10.0)


async def test_caa_hand_control_reported():
    sdk = FakeCaaSdk(moving_seq=[True] * 10_000, hand_control=True)
    r = zu.CaaRotator(sdk, 20)
    await r.connect()
    with pytest.raises(DeviceError, match="hand controller"):
        await r.move_mechanical(10.0)
    assert "stop" in sdk.calls


async def test_caa_reverse_roundtrip():
    sdk = FakeCaaSdk()
    r = zu.CaaRotator(sdk, 20)
    await r.connect()
    assert await r.get_reverse() is False
    await r.set_reverse(True)
    assert await r.get_reverse() is True


# ------------------------------------------- backend + framework integration

from astrodeck.devices import backends as _backends  # noqa: E402,F401
from astrodeck.devices import zwo_sdk as zsdk  # noqa: E402
from astrodeck.devices.backend import BACKENDS  # noqa: E402


@pytest.fixture
def registered():
    prior = BACKENDS.get("zwo-usb")
    zu.register_all()
    try:
        yield BACKENDS["zwo-usb"]
    finally:
        if prior is not None:
            BACKENDS["zwo-usb"] = prior
        else:
            BACKENDS.pop("zwo-usb", None)


def test_zwo_usb_manifest(registered):
    from astrodeck.devices.backend import list_backends
    row = next(r for r in list_backends() if r["name"] == "zwo-usb")
    assert row["transport"] == "local" and row["hardware"] is True
    assert row["driver_type"] == "zwo-usb"
    assert row["roles"] == ("rotator", "focuser")


async def test_no_device_attached_is_honest(registered, monkeypatch):
    monkeypatch.setattr(zsdk, "make_eaf", lambda: FakeEafSdk(count=0))
    s = await registered.open(None)
    with pytest.raises(DeviceError, match="no EAF attached"):
        await s.get_device("focuser", None)


async def test_connect_profile_e2e_both_roles(registered, monkeypatch):
    """rotator+focuser on zwo-usb through the orchestrator: ONE session
    (hostless coalescing), both devices connected, hardware=True stamped."""
    from astrodeck.devices.orchestrator import connect_profile
    from astrodeck.profiles import Profile, ProfileDevice
    monkeypatch.setattr(zsdk, "make_eaf", lambda: FakeEafSdk())
    monkeypatch.setattr(zsdk, "make_caa", lambda: FakeCaaSdk())
    p = Profile(name="accessories", primary_backend="none", devices=[
        ProfileDevice(role="rotator", backend="zwo-usb", transport="local"),
        ProfileDevice(role="focuser", backend="zwo-usb", transport="local")])
    res = await connect_profile(p.to_rigspec())
    assert len(res.sessions) == 1                      # hostless: one session
    rot, foc = res.rig.get("rotator"), res.rig.get("focuser")
    assert rot is not None and rot.connected and rot.hardware is True
    assert foc is not None and foc.connected and foc.hardware is True
    for s in res.sessions.values():
        await s.close()


def test_entry_point_discovery_loads_zwo_usb(monkeypatch):
    import astrodeck.devices.backends._discovery as disc

    class _EP:
        name = "zwo_usb"
        dist = type("D", (), {"name": "astrodeck"})()
        def load(self):
            return zu.register_all
    monkeypatch.setattr(disc.md, "entry_points", lambda group=None: [_EP()])
    prior = BACKENDS.pop("zwo-usb", None)
    try:
        disc.discover_plugin_backends(app_version="99.0")
        assert "zwo-usb" in BACKENDS
    finally:
        if prior is not None:
            BACKENDS["zwo-usb"] = prior
        else:
            BACKENDS.pop("zwo-usb", None)


# ---------------------------------------------- a move that does not move
# 2026-07-31: the EAF refused every move above position 360 (against a stop) and
# move_to returned SUCCESS every time, because it waited for is_moving() to read
# false twice — instantly true when the motor never engages. The user typed
# 22000, pressed Go, and nothing happened, with no error in the UI, the log or
# the API response.

class _StuckEafSdk(FakeEafSdk):
    """Accepts EAFMove, reports not-moving, and never changes position — a
    focuser against a mechanical limit."""

    def move(self, d, step):
        self._log("move")          # accepted, and then ignored by the hardware

    def is_moving(self, d):
        self._log("is_moving")
        return False, False


async def test_a_refused_move_raises_instead_of_reporting_success():
    zu.EAF_MOVE_TIMEOUT_S = 30.0
    sdk = _StuckEafSdk(position=360)
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    with pytest.raises(DeviceError) as e:
        await f.move_to(22000)
    msg = str(e.value)
    assert "22000" in msg, f"must name the target the user asked for: {msg}"
    assert "360" in msg, f"must say where it actually stopped: {msg}"
    assert "limit" in msg or "jam" in msg, f"must suggest the cause: {msg}"


async def test_a_refused_move_does_not_wait_for_the_full_timeout():
    """It knows within a couple of polls; making the user wait 120s for silence
    is its own defect."""
    zu.EAF_MOVE_TIMEOUT_S = 120.0
    sdk = _StuckEafSdk(position=360)
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    t0 = asyncio.get_running_loop().time()
    with pytest.raises(DeviceError):
        await f.move_to(22000)
    assert asyncio.get_running_loop().time() - t0 < 10.0


async def test_a_slow_but_progressing_move_is_not_called_stalled():
    """Position creeping toward the target must never be mistaken for a stall,
    or every slow focuser reports a false failure."""
    zu.EAF_MOVE_TIMEOUT_S = 60.0

    class _Creeper(FakeEafSdk):
        def move(self, d, step):
            self._log("move"); self.target = step
        def is_moving(self, d):
            self._log("is_moving")
            t = getattr(self, "target", self.position)
            if self.position != t:
                # one step per poll: slow, but real progress
                self.position += 1 if t > self.position else -1
                return True, False
            return False, False

    sdk = _Creeper(position=100)
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    await f.move_to(105)              # must NOT raise
    assert abs(sdk.position - 105) <= zu.ARRIVAL_TOLERANCE_STEPS


async def test_arrival_is_what_counts_not_silence():
    """The normal case still works: the move completes and returns cleanly."""
    sdk = FakeEafSdk(position=1000, moving_seq=[True, True, False])
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    await f.move_to(2000)
    assert sdk.position == 2000


# ------------------------------------------- the enforced travel limit
# 2026-07-31: EAF_INFO.MaxStep read 600000 while the firmware's stored
# EAFGetMaxStep was 360. Only the former was bound, so the driver believed it
# had the full range and every move above 360 was silently clamped BY THE
# DEVICE. That is the whole reason "type 22000, press Go, nothing happens".

class _ClampedEafSdk(FakeEafSdk):
    """Hardware ceiling 600000, firmware limit 360 — the real rig.

    ``set_max_step`` is writable and read back by ``get_max_step``, mirroring
    the device: the limit is settable, it just does not SURVIVE a reconnect."""

    def __init__(self, enforced=360, settable=True, **kw):
        super().__init__(max_step=600000, **kw)
        self._enforced = enforced
        self._settable = settable

    def get_max_step(self, d):
        self._log("get_max_step"); return self._enforced

    def set_max_step(self, d, value):
        self._log("set_max_step")
        if not self._settable:
            raise ZwoSdkError(8, "EAFSetMaxStep")
        self._enforced = int(value)


class _TalkativeEafSdk(_ClampedEafSdk):
    """An EAF whose firmware DOES answer the diagnostic calls."""

    def __init__(self, motor="E2", battery="", reason=0, **kw):
        super().__init__(**kw)
        self._codes = (motor, battery)
        self._reason = reason

    def error_codes(self, d):
        self._log("error_codes"); return self._codes

    def power_off_reason(self, d):
        self._log("power_off_reason"); return self._reason

    def reset_position(self, d, value):
        self._log("reset_position"); self.position = int(value)


async def test_max_position_is_the_ENFORCED_limit_not_the_hardware_ceiling():
    sdk = _ClampedEafSdk(position=360)
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    assert f.max_position == 360, (
        f"must use the firmware limit, got {f.max_position}")
    assert f.hardware_max_position == 600000


async def test_a_move_past_the_enforced_limit_is_refused_before_the_hardware():
    """Catching it in range-checking is better than discovering it as a stall:
    the error names the limit instead of describing a symptom."""
    sdk = _ClampedEafSdk(position=360)
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    with pytest.raises(DeviceError, match="out of range"):
        await f.move_to(22000)
    assert "move" not in sdk.calls, "must not command hardware it cannot reach"


async def test_an_sdk_without_the_export_falls_back_rather_than_failing():
    """An older or macOS SDK missing EAFGetMaxStep must keep the focuser
    working — losing it entirely would be worse than the bug this fixes."""
    sdk = FakeEafSdk(max_step=60000, position=100)   # no get_max_step attribute
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    assert f.max_position == 60000


def _logs_since(n: int) -> list[dict]:
    """Focuser log payloads published after mark ``n`` (bus history wraps each
    event as {"type", "data", "ts"})."""
    from astrodeck.events import bus
    return [e["data"] for e in list(bus.log_history)[n:]
            if e.get("type") == "log" and e.get("data", {}).get("source") == "focuser"]


async def test_falling_back_to_the_hardware_max_is_said_out_loud():
    """The silent version of this fallback is indistinguishable from a healthy
    read: on the real rig `max` reported 600000 and nothing anywhere said
    whether that was the enforced limit or a failed attempt to read it. The UI
    clamps Go-to targets against this number, so an unread limit means offering
    positions the firmware will refuse without a word."""
    from astrodeck.events import bus
    before = len(bus.log_history)
    sdk = FakeEafSdk(max_step=60000, position=100)   # no get_max_step export
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    said = _logs_since(before)
    assert said, "a fallback to the hardware max must be announced"
    assert any(e.get("level") == "warning" for e in said), \
        f"must be a warning, not an aside: {said}"
    assert any("60000" in str(e.get("message", "")) for e in said), \
        f"must name the number it fell back to: {said}"


# The EAF FORGETS its travel limit. 2026-07-31: a device deliberately set to
# 40000 came back reporting 360 after a server restart (position reset to 0
# too), silently re-arming "type 22000, press Go, nothing happens". Setting it
# once by hand fixes nothing — it has to be restored on every connect.

async def test_the_configured_travel_limit_is_restored_on_connect():
    sdk = _ClampedEafSdk(enforced=360, position=0)      # woke up reset
    f = zu.EafFocuser(sdk, 10, max_step=40000)
    await f.connect()
    assert "set_max_step" in sdk.calls, "must push the limit back, not just read it"
    assert f.max_position == 40000, (
        f"must end up with the configured travel, got {f.max_position}")
    assert f.hardware_max_position == 600000


async def test_a_move_that_was_refused_before_works_after_the_restore():
    """The user's exact command. With the limit restored, 22000 is in range."""
    sdk = _ClampedEafSdk(enforced=360, position=0, moving_seq=[True, False])
    f = zu.EafFocuser(sdk, 10, max_step=40000)
    await f.connect()
    await f.move_to(22000)                    # would have raised "out of range"
    assert await f.get_position() == 22000


async def test_the_configured_limit_never_exceeds_the_hardware_ceiling():
    sdk = _ClampedEafSdk(enforced=360, position=0)
    f = zu.EafFocuser(sdk, 10, max_step=10_000_000)
    await f.connect()
    assert f.max_position == 600000, "must clamp to what the hardware can do"


async def test_no_configured_limit_leaves_the_device_alone():
    """Unconfigured must stay read-only: writing a guessed travel limit to
    someone's focuser is not ours to do."""
    sdk = _ClampedEafSdk(enforced=360, position=0)
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    assert "set_max_step" not in sdk.calls
    assert f.max_position == 360


async def test_an_absurd_unconfigured_limit_is_called_out_with_the_fix():
    """360 steps out of 600000 is 0.06% of the travel — nobody sets that on
    purpose, and the symptom (Go does nothing) does not point at it."""
    from astrodeck.events import bus
    before = len(bus.log_history)
    f = zu.EafFocuser(_ClampedEafSdk(enforced=360, position=0), 10)
    await f.connect()
    said = _logs_since(before)
    warns = [e for e in said if e.get("level") == "warning"]
    assert warns, f"an implausible limit must be a warning: {said}"
    blob = " ".join(str(e.get("message", "")) for e in warns)
    assert "360" in blob and "600000" in blob, f"must give both numbers: {blob}"
    assert "max_step" in blob, f"must name the setting that fixes it: {blob}"


async def test_a_normal_unconfigured_limit_is_not_cried_wolf_over():
    from astrodeck.events import bus
    before = len(bus.log_history)
    f = zu.EafFocuser(_ClampedEafSdk(enforced=45000, position=0), 10)
    await f.connect()
    assert not [e for e in _logs_since(before) if e.get("level") == "warning"]


async def test_a_device_that_refuses_the_limit_says_so_rather_than_pretending():
    from astrodeck.events import bus
    before = len(bus.log_history)
    sdk = _ClampedEafSdk(enforced=360, settable=False, position=0)
    f = zu.EafFocuser(sdk, 10, max_step=40000)
    await f.connect()
    assert f.connected, "a refused limit must not cost the focuser entirely"
    assert f.max_position == 360, "must report what the DEVICE will honour"
    blob = " ".join(str(e.get("message", "")) for e in _logs_since(before)
                    if e.get("level") == "warning")
    assert "40000" in blob and "360" in blob, f"must name both numbers: {blob}"


async def test_the_session_passes_the_configured_limit_to_the_focuser(monkeypatch):
    """Plumbing: the value lives on the driver entry's `extra`, and the whole
    fix is inert if it never reaches the device."""
    from astrodeck.devices import zwo_sdk as zsdk
    sdk = _ClampedEafSdk(enforced=360, position=0)
    monkeypatch.setattr(zsdk, "make_eaf", lambda: sdk)

    from astrodeck.devices.backend import ConnSpec
    conn = ConnSpec(backend="zwo-usb", role="focuser",
                    driver_id="zwo-usb-ad03", extra={"max_step": 40000})
    dev = await zu.ZwoUsbSession().get_device("focuser", conn)
    assert dev.max_position == 40000
    # ConnSpec names it driver_id, NOT id — reading the wrong field silently
    # collapses every focuser onto one shared remembered-position slot.
    assert dev._state_key == "zwo-usb-ad03"


async def test_a_healthy_limit_read_is_also_stated():
    """Not only the mismatch case — a log that speaks up only on disagreement
    cannot distinguish 'agrees' from 'never asked'."""
    from astrodeck.events import bus
    before = len(bus.log_history)
    sdk = _ClampedEafSdk(enforced=600000, position=100)
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    said = _logs_since(before)
    assert any("600000" in str(e.get("message", "")) for e in said), \
        f"must state the limit it is using: {said}"
    assert not any(e.get("level") == "warning" for e in said), \
        f"a successful read is not a warning: {said}"


# ---------------------------------------------- diagnostics + re-anchoring
# An open-loop stepper cannot be trusted to notice a mechanical stop, so the
# repair for a lost position count is a human anchoring it — never a sweep into
# the ends. These cover the three pieces of that: ask the device what it thinks
# went wrong, let a human declare where the tube is, and NOTICE when the count
# is lost instead of letting stored positions quietly change meaning.



class _StuckTalkativeEafSdk(_TalkativeEafSdk):
    """Against a stop AND willing to say so: accepts EAFMove, never moves."""

    def move(self, d, step):
        self._log("move")          # accepted, then ignored by the hardware

    def is_moving(self, d):
        self._log("is_moving"); return False, False


async def test_a_refused_move_reports_what_the_device_says_is_wrong():
    sdk = _StuckTalkativeEafSdk(motor="E2", enforced=600000, position=100)
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    with pytest.raises(DeviceError) as ei:
        await f.move_to(50000)
    assert "motor error E2" in str(ei.value), str(ei.value)


async def test_a_firmware_that_will_not_answer_costs_nothing():
    """Most EAF firmwares return NOT_SUPPORTED here. Silence is not a fault,
    and it must not replace the real error with a diagnostic one."""
    sdk = _StuckEafSdk(max_step=600000, position=100)     # no error_codes at all
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    with pytest.raises(DeviceError, match="did not happen"):
        await f.move_to(50000)


async def test_setting_the_position_reference_moves_nothing():
    sdk = _TalkativeEafSdk(enforced=60000, position=347)
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    await f.set_position_reference(22000)
    assert await f.get_position() == 22000
    assert "reset_position" in sdk.calls
    assert "move" not in sdk.calls, "re-anchoring must never command motion"


async def test_the_reference_cannot_be_set_mid_move():
    """A number written while the tube is travelling is stale on arrival."""
    sdk = _TalkativeEafSdk(enforced=60000, position=100, moving_seq=[True] * 50)
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    with pytest.raises(DeviceError, match="moving"):
        await f.set_position_reference(22000)
    assert "reset_position" not in sdk.calls


async def test_the_reference_must_be_inside_the_travel():
    sdk = _TalkativeEafSdk(enforced=40000, position=100)
    f = zu.EafFocuser(sdk, 10)
    await f.connect()
    with pytest.raises(DeviceError, match="outside"):
        await f.set_position_reference(99999)


async def test_a_lost_position_count_is_reported_not_silently_accepted():
    """The 2026-07-31 failure: 30000 before a reconnect, 0 after, tube unmoved,
    and every stored focus position silently 30000 steps out."""
    from astrodeck.events import bus
    sdk = _TalkativeEafSdk(enforced=40000, position=30000, moving_seq=[True, False])
    f = zu.EafFocuser(sdk, 10, state_key="zwo-usb-test")
    await f.connect()
    await f.move_to(30000)                     # records 30000

    before = len(bus.log_history)
    reset = _TalkativeEafSdk(enforced=40000, position=0)     # came back at zero
    f2 = zu.EafFocuser(reset, 10, state_key="zwo-usb-test")
    await f2.connect()
    warns = [e for e in _logs_since(before) if e.get("level") == "warning"]
    blob = " ".join(str(e.get("message", "")) for e in warns)
    assert warns, "a lost position count must not be silent"
    assert "30000" in blob and "0" in blob, f"must give both numbers: {blob}"
    assert "not moved" in blob.lower() or "did not move" in blob.lower(), \
        f"must say the drawtube itself did not move: {blob}"


async def test_an_unchanged_position_says_nothing():
    from astrodeck.events import bus
    sdk = _TalkativeEafSdk(enforced=40000, position=12345, moving_seq=[True, False])
    f = zu.EafFocuser(sdk, 10, state_key="k")
    await f.connect()
    await f.move_to(12345)

    before = len(bus.log_history)
    again = _TalkativeEafSdk(enforced=40000, position=12345)
    f2 = zu.EafFocuser(again, 10, state_key="k")
    await f2.connect()
    assert not [e for e in _logs_since(before) if e.get("level") == "warning"]


async def test_two_focusers_do_not_overwrite_each_others_reference():
    from astrodeck.config import load_focuser_position
    a = _TalkativeEafSdk(enforced=40000, position=1000, moving_seq=[True, False])
    fa = zu.EafFocuser(a, 10, state_key="driver-a")
    await fa.connect(); await fa.move_to(1000)
    b = _TalkativeEafSdk(enforced=40000, position=2000, moving_seq=[True, False])
    fb = zu.EafFocuser(b, 11, state_key="driver-b")
    await fb.connect(); await fb.move_to(2000)
    assert load_focuser_position("driver-a") == 1000
    assert load_focuser_position("driver-b") == 2000


async def test_the_first_ever_connect_is_not_a_lost_count():
    from astrodeck.events import bus
    before = len(bus.log_history)
    f = zu.EafFocuser(_TalkativeEafSdk(enforced=40000, position=5000), 10,
                      state_key="fresh")
    await f.connect()
    assert not [e for e in _logs_since(before) if e.get("level") == "warning"]


async def test_describe_says_whether_the_firmware_answers_diagnostics():
    """Whether EAFGetErrorCode answers is firmware-dependent, and it decides
    whether a refused move can ever explain itself. That has to be visible
    without waiting for a failure to find out."""
    talkative = zu.EafFocuser(_TalkativeEafSdk(motor="E2", enforced=40000,
                                               position=100, reason=1), 10)
    await talkative.connect()
    d = talkative.describe()
    assert d["reports_diagnostics"] is True
    assert d["motor_error_code"] == "E2"
    assert d["power_off_reason"] == 1

    quiet = zu.EafFocuser(_ClampedEafSdk(enforced=40000, position=100), 10)
    await quiet.connect()
    dq = quiet.describe()
    assert dq["reports_diagnostics"] is False, \
        "a firmware that will not answer must not look like one that did"
    assert dq["motor_error_code"] is None
