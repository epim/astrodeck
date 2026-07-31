"""Is the focuser actually turning? The status payload must be able to say.

Background: POST /api/focuser/move returns ``{"started": "focuser"}`` the moment
the task is spawned, so the UI learns nothing about the move itself from the
response. On 2026-07-31 that meant a firmware-refused move -- commanded 22000,
clamped to 360, motor never engaged -- was visually indistinguishable from a
move under way, for two nights. ``focuser.moving`` is the missing signal.

The load-bearing rule tested here: a backend that CANNOT report motion must
report False, never a hopeful True, and must never cost the position/max/temp
readouts if asking throws.
"""
import asyncio

import pytest

from astrodeck.devices.base import DeviceError, Focuser
from astrodeck.hub import Hub

pytestmark = pytest.mark.anyio


class _MinimalFocuser(Focuser):
    """A backend that implements only the abstract contract -- i.e. one written
    before ``is_moving`` existed."""

    def __init__(self, name="minimal"):
        super().__init__(name)
        self.connected = True
        self.pos = 500

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_position(self) -> int:
        return self.pos

    async def move_to(self, position: int) -> None:
        self.pos = int(position)

    async def halt(self) -> None:
        pass


class _AngryFocuser(_MinimalFocuser):
    async def is_moving(self) -> bool:
        raise DeviceError("the link is down")


class _MovingFocuser(_MinimalFocuser):
    moving = True

    async def is_moving(self) -> bool:
        return self.moving


async def test_a_backend_that_cannot_say_reports_not_moving():
    # NOT True. The UI reads this to decide whether a commanded move is
    # happening; defaulting to "yes it's moving" would turn every silently
    # refused move back into the invisible failure this exists to catch.
    assert await _MinimalFocuser().is_moving() is False


async def test_the_sim_focuser_reports_motion_only_while_it_moves():
    from astrodeck.devices.sim import SimFocuser, SimRig

    foc = SimFocuser(SimRig())
    await foc.connect()
    assert await foc.is_moving() is False

    task = asyncio.create_task(foc.move_to(30_000))
    await asyncio.sleep(0.05)
    assert await foc.is_moving() is True, "must report motion during the move"
    await task
    assert await foc.is_moving() is False, "must clear when the move lands"


async def test_a_halted_move_does_not_leave_the_sim_claiming_motion():
    from astrodeck.devices.sim import SimFocuser, SimRig

    foc = SimFocuser(SimRig())
    await foc.connect()
    task = asyncio.create_task(foc.move_to(59_000))
    await asyncio.sleep(0.05)
    await foc.halt()
    await task
    assert await foc.is_moving() is False


async def test_a_cancelled_move_does_not_leave_the_sim_claiming_motion():
    from astrodeck.devices.sim import SimFocuser, SimRig

    foc = SimFocuser(SimRig())
    await foc.connect()
    task = asyncio.create_task(foc.move_to(59_000))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await foc.is_moving() is False, "a cancelled move must still clear"


async def test_poll_status_publishes_the_moving_flag():
    hub = Hub()
    foc = _MovingFocuser()
    hub.devices["focuser"] = foc
    out = await hub.poll_status()
    assert out["focuser"]["moving"] is True
    foc.moving = False
    assert (await hub.poll_status())["focuser"]["moving"] is False


async def test_a_backend_that_raises_still_gets_its_readouts_published():
    # position/max/temperature are what the user is looking at; a newer, less
    # universally-supported field must not be able to blank them.
    hub = Hub()
    hub.devices["focuser"] = _AngryFocuser()
    out = await hub.poll_status()
    assert out["focuser"]["position"] == 500
    assert "max" in out["focuser"] and "temperature" in out["focuser"]
    # Absent, not False: "cannot say" is a different claim from "not moving",
    # and the UI's stall detector treats the two differently.
    assert "moving" not in out["focuser"]
