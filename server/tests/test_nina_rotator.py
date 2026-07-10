"""NinaRotator against a scripted fake NinaClient."""
import asyncio

import pytest

from astrodeck.devices.nina import _ROLE_CLASSES, NinaRotator


class FakeClient:
    def __init__(self):
        self.host, self.port = "obs-pc", 1888
        self.calls = []
        self.info = {"Connected": True, "Name": "ZWO CAA",
                     "MechanicalPosition": 77.0, "Position": 77.0,
                     "CanReverse": False, "IsMoving": False}
        self.moving_seq: list[bool] = []

    async def get(self, path, **params):
        self.calls.append((path, params))
        if path == "/equipment/rotator/info":
            info = dict(self.info)
            if self.moving_seq:
                info["IsMoving"] = self.moving_seq.pop(0)
            return info
        return {}


@pytest.fixture()
def rot():
    return NinaRotator(FakeClient(), "rotator")


def test_registered_in_role_classes():
    cls, path = _ROLE_CLASSES["rotator"]
    assert cls is NinaRotator
    assert path == "/equipment/rotator/info"


@pytest.mark.asyncio
async def test_connect_reads_info(rot):
    await rot.connect()
    assert rot.connected is True
    assert rot.name == "ZWO CAA"
    assert rot.can_reverse is False


@pytest.mark.asyncio
async def test_mechanical_position_prefers_mechanical_field(rot):
    rot.client.info["MechanicalPosition"] = 200.5
    rot.client.info["Position"] = 10.0
    assert await rot.get_mechanical_position() == pytest.approx(200.5)


@pytest.mark.asyncio
async def test_move_mechanical_calls_endpoint_and_polls(rot):
    rot.client.moving_seq = [True, False]
    await rot.move_mechanical(150.0)
    paths = [c[0] for c in rot.client.calls]
    assert "/equipment/rotator/move-mechanical" in paths
    call = next(c for c in rot.client.calls
                if c[0] == "/equipment/rotator/move-mechanical")
    assert call[1] == {"position": 150.0}


@pytest.mark.asyncio
async def test_nina_backend_advertises_rotator():
    from astrodeck.devices.backends.nina_backend import NinaBackend
    assert "rotator" in NinaBackend.roles
