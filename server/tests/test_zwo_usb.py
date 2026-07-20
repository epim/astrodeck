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
    with pytest.raises(DeviceError, match="settle|timeout"):
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
