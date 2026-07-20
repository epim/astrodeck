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


_VENDORED = [p for p in (zwo_sdk._VENDOR_DIR / n for n in zwo_sdk._DLL_SPECS)
             if p.is_file()]


@pytest.mark.skipif(len(_VENDORED) < 2,
                    reason="vendored ZWO DLLs absent (CI without binaries)")
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
