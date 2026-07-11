"""AlpacaRotator verb mapping against a recording fake connection."""
import asyncio

import pytest

from astrodeck.devices import alpaca
from astrodeck.devices.alpaca import AlpacaRotator, DEVICE_CLASSES
from astrodeck.devices.base import DeviceError


class RecConn:
    """Records every Alpaca verb; scripted responses per GET method name.
    ``ismoving_seq`` pops one value per ismoving poll (then False)."""

    def __init__(self):
        self.host, self.port = "10.0.0.9", 11111
        self.calls = []
        self.responses = {"canreverse": True, "mechanicalposition": 123.4,
                          "ismoving": False, "reverse": False}
        self.ismoving_seq: list[bool] = []

    async def get(self, dev_type, dev_num, method, **params):
        self.calls.append(("get", method, params))
        if method == "ismoving" and self.ismoving_seq:
            return self.ismoving_seq.pop(0)
        return self.responses.get(method)

    async def put(self, dev_type, dev_num, method, **params):
        self.calls.append(("put", method, params))
        return None


@pytest.fixture()
def rot():
    return AlpacaRotator(RecConn(), 0, "ZWO CAA")


def test_registered_in_device_classes():
    assert DEVICE_CLASSES["rotator"] is AlpacaRotator
    assert AlpacaRotator.dev_type == "rotator"


@pytest.mark.asyncio
async def test_connect_probes_canreverse(rot):
    await rot.connect()
    assert rot.connected is True
    assert rot.can_reverse is True
    assert ("get", "canreverse", {}) in rot.conn.calls


@pytest.mark.asyncio
async def test_connect_survives_missing_canreverse(rot):
    async def boom(dev_type, dev_num, method, **params):
        if method == "canreverse":
            raise DeviceError("not implemented")
        return None
    rot.conn.get = boom
    await rot.connect()
    assert rot.can_reverse is False


@pytest.mark.asyncio
async def test_mechanical_position_reads_driver(rot):
    assert await rot.get_mechanical_position() == pytest.approx(123.4)


@pytest.mark.asyncio
async def test_move_mechanical_puts_and_polls(rot):
    rot.conn.ismoving_seq = [True, True, False]
    await rot.move_mechanical(200.0)
    puts = [c for c in rot.conn.calls if c[0] == "put"]
    assert ("put", "movemechanical", {"Position": 200.0}) in puts
    polls = [c for c in rot.conn.calls if c[1] == "ismoving"]
    assert len(polls) == 3


@pytest.mark.asyncio
async def test_move_halts_on_cancel(rot):
    rot.conn.ismoving_seq = [True] * 100
    task = asyncio.create_task(rot.move_mechanical(90.0))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ("put", "halt", {}) in rot.conn.calls


@pytest.mark.asyncio
async def test_move_times_out(rot, monkeypatch):
    monkeypatch.setattr(AlpacaRotator, "MOVE_TIMEOUT_S", 0.5)
    rot.conn.ismoving_seq = [True] * 100
    with pytest.raises(DeviceError):
        await rot.move_mechanical(90.0)
    assert ("put", "halt", {}) in rot.conn.calls


@pytest.mark.asyncio
async def test_reverse_gated_on_capability(rot):
    rot.can_reverse = False
    with pytest.raises(DeviceError):
        await rot.set_reverse(True)
    rot.can_reverse = True
    await rot.set_reverse(True)
    assert ("put", "reverse", {"Reverse": True}) in rot.conn.calls


@pytest.mark.asyncio
async def test_get_reverse_falls_back_to_false_on_driver_error(rot):
    """can_reverse=True (the device DECLARES support) but the ``reverse`` GET
    itself raises (a flaky/incomplete driver) -- get_reverse() degrades to
    False rather than propagating, matching the missing-canreverse contract."""
    rot.can_reverse = True

    async def boom(dev_type, dev_num, method, **params):
        if method == "reverse":
            raise DeviceError("reverse not implemented")
        return rot.conn.responses.get(method)
    rot.conn.get = boom
    assert await rot.get_reverse() is False
