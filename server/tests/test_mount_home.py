"""Mount Home — the reference position you START a session from.

Home is NOT Park, and the difference is the whole reason the control exists: a
mount that ends PARKED after Home looks like it worked and then refuses the next
slew. The ZWO AM5 makes that trap easy to fall into, because one wire command
(``:hP#``) does both.
"""
import asyncio

import pytest

from astrodeck.devices.base import DeviceError, Telescope
from astrodeck.devices.backends import zwo_am5


class _BareMount(Telescope):
    """A mount that implements nothing optional — the default contract."""

    async def connect(self): self.connected = True
    async def disconnect(self): self.connected = False
    async def get_position(self): return (0.0, 0.0)
    async def slew(self, ra_hours, dec_deg): pass
    async def sync(self, ra_hours, dec_deg): pass
    async def set_tracking(self, on): pass
    async def get_tracking(self): return False
    async def park(self): pass
    async def unpark(self): pass
    async def is_parked(self): return False
    async def move_axis(self, axis, rate_deg_s): pass
    async def is_slewing(self): return False


def test_a_mount_without_a_home_refuses_rather_than_no_ops():
    """Silence would be the worst answer: the user presses Home, nothing moves,
    and nothing says why."""
    m = _BareMount("Bare")
    assert m.can_find_home is False
    with pytest.raises(DeviceError, match="cannot find home"):
        asyncio.run(m.find_home())


# ------------------------------------------------------------------ ZWO AM5

class _FakeLink:
    """Records the LX200 commands in ORDER — the ordering is the contract."""

    def __init__(self, parked=False, tracking=False):
        self.sent: list[str] = []
        self._parked = parked
        self._tracking = tracking
        # SerialLink's health surface. This double answers everything, so it is
        # permanently open — but it must ANSWER the question, because the driver
        # asks it on every transport error to decide whether to reopen.
        self.is_open = True
        self.needs_reopen = False

    async def request(self, cmd: str, reply: str = "hash", timeout: float = 1.5):
        self.sent.append(cmd)
        if cmd == "Gps":
            return "2" if self._parked else "1"
        if cmd == "GU":
            return "T" if self._tracking else "N"
        if cmd == "hP":
            self._parked = True          # the mount homes AND parks
            return ""
        if cmd == "Spu":
            self._parked = False
            return "1"
        if cmd in ("Td", "Te"):
            self._tracking = (cmd == "Te")
            return "1"
        return "1"


def _mount(**kw):
    t = zwo_am5.ZwoAm5Telescope(_FakeLink(**kw), name="AM5")
    t.connected = True
    return t


def test_am5_declares_it_can_home():
    assert zwo_am5.ZwoAm5Telescope.can_find_home is True


def test_am5_home_leaves_the_mount_UNPARKED():
    """THE regression. :hP# parks as a side effect of homing, so find_home must
    unpark afterwards or Home silently disables the mount."""
    t = _mount(parked=False, tracking=False)
    asyncio.run(t.find_home())
    assert asyncio.run(t.is_parked()) is False, "Home must leave the mount usable"
    assert "hP" in t._link.sent
    assert "Spu" in t._link.sent, "must unpark after the homing park"


def test_am5_home_unparks_AFTER_homing_not_before():
    """Order matters: unparking first and homing second would leave it parked."""
    t = _mount(parked=False, tracking=False)
    asyncio.run(t.find_home())
    sent = t._link.sent
    assert sent.index("hP") < sent.index("Spu"), f"wrong order: {sent}"


def test_am5_home_stops_tracking_before_the_home_command():
    """Inherited from the park fix, and it must not be lost by going through a
    different entry point: with tracking ON the AM5 accepts :hP# and silently
    does nothing (verified on hardware 2026-07-30)."""
    t = _mount(parked=False, tracking=True)
    asyncio.run(t.find_home())
    sent = t._link.sent
    assert "Td" in sent, "must stop tracking"
    assert sent.index("Td") < sent.index("hP"), (
        f"tracking must stop BEFORE the home command, got {sent}")


def test_am5_home_from_an_already_parked_mount_still_ends_usable():
    """park() short-circuits when already parked; the unpark must still run,
    otherwise Home on a parked mount is a no-op that reports success."""
    t = _mount(parked=True)
    asyncio.run(t.find_home())
    assert asyncio.run(t.is_parked()) is False


# --------------------------------------------------------------------- sim

def test_sim_home_parks_nothing():
    from astrodeck.devices.sim import SimRig, SimTelescope
    rig = SimRig()
    m = SimTelescope(rig)
    m.connected = True
    asyncio.run(m.find_home())
    assert rig.parked is False, "Home must not park the sim either"
    assert rig.tracking is False, "Home stops tracking"
    assert m.can_find_home is True
