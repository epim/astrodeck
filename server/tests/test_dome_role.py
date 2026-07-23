"""PRO-4 Task 1 — the ``Dome`` role ABC + ``DomeShutterState`` (base.py).

Mirrors the ``SafetyMonitor``/``CoverCalibrator`` role pattern: a minimal
in-file fake implements the three abstracts and we pin the capability-flag +
raise-by-default idiom and the derived ``is_open``/``is_closed`` accessors.
"""
import pytest

from astrodeck.devices.base import DeviceError, Dome, DomeShutterState


class FakeDome(Dome):
    def __init__(self, name: str = "Fake Roof") -> None:
        super().__init__(name)
        self._state = DomeShutterState.OPEN

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def shutter_state(self) -> DomeShutterState:
        return self._state

    async def open_shutter(self) -> None:
        self._state = DomeShutterState.OPEN

    async def close_shutter(self) -> None:
        self._state = DomeShutterState.CLOSED


def test_kind_and_capability_defaults():
    d = FakeDome()
    assert d.kind == "dome"
    assert Dome.kind == "dome"
    # FAIL-SAFE default: assume the roof can collide with the mount.
    assert d.requires_park_before_close is True
    assert d.can_slave is False


@pytest.mark.asyncio
async def test_is_closed_is_open_derive_from_shutter_state():
    d = FakeDome()
    assert await d.is_open() is True
    assert await d.is_closed() is False
    await d.close_shutter()
    assert await d.is_closed() is True
    assert await d.is_open() is False


@pytest.mark.asyncio
async def test_slaved_defaults_raise_and_false():
    d = FakeDome()
    assert await d.get_slaved() is False
    with pytest.raises(DeviceError):
        await d.set_slaved(True)
    # abort is a safe no-op by default.
    assert await d.abort() is None


def test_describe_carries_kind_and_capabilities():
    d = FakeDome()
    desc = d.describe()
    assert desc["kind"] == "dome"
    assert desc["can_slave"] is False
    assert desc["requires_park_before_close"] is True
