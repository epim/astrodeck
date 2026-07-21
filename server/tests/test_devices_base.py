"""Base ``Telescope`` contract for multi-rate mount tracking (2026-07-21):
the ``TRACKING_RATES`` vocabulary + the ``can_set_tracking_rate`` capability
flag + the two default methods, exercised against a minimal concrete
subclass (a bare test double implementing only the pre-existing abstract
methods)."""
from __future__ import annotations

import pytest

from astrodeck.devices.base import DeviceError, Telescope, TRACKING_RATES


class _BareTelescope(Telescope):
    """The minimal concrete Telescope: implements only the abstract methods,
    exercising every non-abstract default (incl. tracking-rate) unmodified."""

    def __init__(self) -> None:
        super().__init__("Bare Test Mount")

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_position(self) -> tuple[float, float]:
        return 0.0, 0.0

    async def slew(self, ra_hours: float, dec_deg: float) -> None:
        pass

    async def sync(self, ra_hours: float, dec_deg: float) -> None:
        pass

    async def set_tracking(self, on: bool) -> None:
        pass

    async def get_tracking(self) -> bool:
        return True

    async def park(self) -> None:
        pass

    async def unpark(self) -> None:
        pass

    async def is_parked(self) -> bool:
        return False

    async def move_axis(self, axis: str, rate_deg_s: float) -> None:
        pass

    async def is_slewing(self) -> bool:
        return False


def test_tracking_rates_vocabulary():
    assert TRACKING_RATES == ("sidereal", "lunar", "solar")


def test_bare_telescope_tracking_rate_defaults():
    tel = _BareTelescope()
    assert tel.can_set_tracking_rate is False


async def test_bare_telescope_get_tracking_rate_default():
    tel = _BareTelescope()
    assert await tel.get_tracking_rate() == "sidereal"


async def test_bare_telescope_set_tracking_rate_raises():
    tel = _BareTelescope()
    with pytest.raises(DeviceError):
        await tel.set_tracking_rate("lunar")
